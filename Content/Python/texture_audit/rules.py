"""Engine-independent texture policy checks. No Unreal imports or asset writes.

All policies are project conventions, not universal Unreal requirements.
A suggestion is a review item, never permission to change an asset.
"""
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Optional, Tuple

KINDS = ("color", "normal", "mask", "unknown")


@dataclass(frozen=True)
class AssetSnapshot:
    asset_path: str
    name: str
    kind: str
    width: Optional[int] = None
    height: Optional[int] = None
    srgb: Optional[bool] = None
    compression: Optional[str] = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "AssetSnapshot":
        if not isinstance(data, Mapping):
            raise ValueError("Each snapshot must be a mapping.")
        allowed = {"asset_path", "name", "kind", "width", "height", "srgb", "compression"}
        if set(data) - allowed:
            raise ValueError("Unknown snapshot field(s): " + ", ".join(sorted(set(data) - allowed)))
        for key in ("asset_path", "name"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError(key + " must be a non-empty string.")
        path = data["asset_path"].strip()
        if not path.startswith("/Game/") or any(part in (".", "..") for part in path.split("/")):
            raise ValueError("asset_path must be a /Game/ asset identifier, not a disk path.")
        kind = data.get("kind", "unknown")
        if kind not in KINDS:
            raise ValueError("kind must be color, normal, mask or unknown.")
        for key in ("width", "height"):
            value = data.get(key)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(key + " must be a positive integer or None.")
        srgb = data.get("srgb")
        if srgb is not None and type(srgb) is not bool:
            raise ValueError("srgb must be bool or None.")
        compression = data.get("compression")
        if compression is not None and (
            not isinstance(compression, str) or not compression.strip()
        ):
            raise ValueError("compression must be a non-empty string or None.")
        return cls(
            asset_path=path,
            name=data["name"].strip(),
            kind=kind,
            width=data.get("width"),
            height=data.get("height"),
            srgb=srgb,
            compression=compression.strip().lower() if compression is not None else None,
        )


@dataclass(frozen=True)
class AuditPolicy:
    name_prefix: str = "T_"
    max_source_edge: int = 2048
    check_power_of_two: bool = True
    suffixes: Tuple[Tuple[str, str], ...] = (
        ("color", "_BC"), ("normal", "_N"), ("mask", "_M")
    )
    expected_srgb: Tuple[Tuple[str, bool], ...] = (
        ("color", True), ("normal", False), ("mask", False)
    )
    expected_compression: Tuple[Tuple[str, str], ...] = (
        ("color", "default"), ("normal", "normalmap"), ("mask", "masks")
    )

    def __post_init__(self) -> None:
        if not isinstance(self.name_prefix, str) or not self.name_prefix:
            raise ValueError("name_prefix must be a non-empty string.")
        if type(self.max_source_edge) is not int or self.max_source_edge <= 0:
            raise ValueError("max_source_edge must be a positive integer.")
        if type(self.check_power_of_two) is not bool:
            raise ValueError("check_power_of_two must be bool.")
        for field, pairs in (
            ("suffixes", self.suffixes),
            ("expected_srgb", self.expected_srgb),
            ("expected_compression", self.expected_compression),
        ):
            keys = []
            for kind, value in pairs:
                if kind not in KINDS[:-1]:
                    raise ValueError(field + " contains an unsupported kind.")
                keys.append(kind)
                if field == "expected_srgb":
                    if type(value) is not bool:
                        raise ValueError("expected_srgb values must be bool.")
                elif not isinstance(value, str) or not value.strip():
                    raise ValueError(field + " values must be non-empty strings.")
                elif field == "expected_compression" and value != value.strip().lower():
                    raise ValueError("compression policy values must use canonical lowercase labels.")
            if len(keys) != len(set(keys)):
                raise ValueError(field + " contains duplicate kinds.")


@dataclass(frozen=True)
class Suggestion:
    suggestion_id: str
    asset_path: str
    property_name: str
    before: Any
    proposed_after: Any
    reason: str
    requires_user_confirmation: bool = True
    applied: bool = False


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    property_name: str
    actual: Any
    expected: Any
    message: str
    suggestion_id: Optional[str] = None


@dataclass(frozen=True)
class AuditResult:
    asset_path: str
    asset_name: str
    kind: str
    issues: Tuple[Issue, ...]
    suggestions: Tuple[Suggestion, ...]

    @property
    def status(self) -> str:
        return "needs_review" if self.issues else "compliant_with_selected_policy"

    def to_dict(self) -> dict:
        result = asdict(self)
        result["status"] = self.status
        return result


def _power_of_two(value: int) -> bool:
    return value > 0 and (value & (value - 1)) == 0


def audit_asset(snapshot: AssetSnapshot, policy: AuditPolicy) -> AuditResult:
    """Validate public dataclass construction too, not only JSON parsing."""
    asset = AssetSnapshot.from_mapping(asdict(snapshot))
    issues = []
    suggestions = []

    def note(code, prop, actual, expected, message, severity="warning"):
        issues.append(Issue(code, severity, prop, actual, expected, message))

    def propose(code, prop, actual, expected, reason):
        identifier = asset.asset_path + "::" + prop
        suggestions.append(Suggestion(identifier, asset.asset_path, prop, actual, expected, reason))
        issues.append(Issue(code, "warning", prop, actual, expected, reason, identifier))

    if not asset.name.startswith(policy.name_prefix):
        note("NAME_PREFIX", "name", asset.name, policy.name_prefix,
             "Name does not follow this project's prefix convention. Rename is manual.")
    suffix = dict(policy.suffixes).get(asset.kind)
    if suffix and not asset.name.endswith(suffix):
        note("NAME_SUFFIX", "name", asset.name, suffix,
             "Name does not match the declared kind's project suffix. No automatic rename.")

    if asset.width is None or asset.height is None:
        note("DIMENSIONS_UNAVAILABLE", "dimensions", [asset.width, asset.height], None,
             "Source dimensions are unavailable. They must be read by a verified UE adapter.",
             "info")
    else:
        if max(asset.width, asset.height) > policy.max_source_edge:
            note("SOURCE_SIZE_BUDGET", "dimensions", [asset.width, asset.height],
                 policy.max_source_edge,
                 "Source size exceeds the selected project budget. No pixels are resized.")
        if policy.check_power_of_two and not (
            _power_of_two(asset.width) and _power_of_two(asset.height)
        ):
            note("NON_POWER_OF_TWO", "dimensions", [asset.width, asset.height],
                 "power-of-two dimensions",
                 "Review this project's size convention. Non-power-of-two is not a universal UE error.")

    if asset.kind == "unknown":
        note("KIND_REVIEW_REQUIRED", "kind", asset.kind, "human-confirmed texture purpose",
             "Purpose has not been confirmed. No color-space or compression changes are proposed.",
             "info")
    else:
        for prop, expectations, code in (
            ("srgb", dict(policy.expected_srgb), "SRGB_POLICY"),
            ("compression", dict(policy.expected_compression), "COMPRESSION_POLICY"),
        ):
            if asset.kind not in expectations:
                continue
            actual = getattr(asset, prop)
            expected = expectations[asset.kind]
            if actual is None:
                note(code + "_UNAVAILABLE", prop, None, expected,
                     "Property is unavailable. No change can be proposed.", "info")
            elif actual != expected:
                propose(code, prop, actual, expected,
                        "Declared texture purpose disagrees with the selected project policy. "
                        "Review material usage before accepting any future modification.")

    return AuditResult(
        asset.asset_path, asset.name, asset.kind, tuple(issues), tuple(suggestions)
    )


def audit_assets(assets: Iterable[AssetSnapshot], policy: Optional[AuditPolicy] = None):
    """Audit snapshots in input order. Reject duplicates to keep review IDs unambiguous."""
    policy = policy if policy is not None else AuditPolicy()
    snapshots = tuple(AssetSnapshot.from_mapping(asdict(item)) for item in assets)
    paths = [item.asset_path for item in snapshots]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate asset_path values are not allowed in one audit.")
    return tuple(audit_asset(item, policy) for item in snapshots)
