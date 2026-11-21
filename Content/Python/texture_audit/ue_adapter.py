"""Read selected project textures using UE's embedded Python 3.9+.

Importing this module does not import unreal, inspect assets or write files.
The adapter has no property setter, save, rename, reimport or repair operation.
API contract: Unreal Python 5.3. Source-size tag access was checked against
UE 5.3.2 Texture2D.cpp and one live texture; broader validation is pending.
"""
from dataclasses import asdict, dataclass
from importlib import import_module
from pathlib import PurePosixPath
import re
from typing import Mapping, Optional, Tuple

from .rules import AssetSnapshot, KINDS

ADAPTER_REVISION = "0.2.0-source-dimensions"


class UnrealContextError(RuntimeError):
    """The requested read cannot safely run in the current editor context."""


@dataclass(frozen=True)
class ReadNotice:
    asset_path: str
    field: str
    code: str
    message: str


@dataclass(frozen=True)
class TextureRead:
    snapshot: AssetSnapshot
    raw_compression: Optional[str]
    source_dimensions_verified: bool = False
    raw_dimensions: Optional[str] = None
    dimension_source: Optional[str] = None


@dataclass(frozen=True)
class SelectionCapture:
    engine_version: str
    project_name: str
    selected_objects: int
    textures: Tuple[TextureRead, ...]
    skipped_objects: Tuple[dict, ...]
    notices: Tuple[ReadNotice, ...]

    @property
    def snapshots(self):
        return tuple(item.snapshot for item in self.textures)

    def to_dict(self):
        # Only /Game/ identifiers and the project name are recorded, not the
        # local project filename, import source filenames or environment.
        return asdict(self)


def _project_asset_path(path):
    if not isinstance(path, str) or not path.startswith("/Game/"):
        return False
    return (
        "\\" not in path
        and ":" not in path
        and not any(part in ("", ".", "..") for part in path[1:].split("/"))
    )


def _declared_kinds(kind_by_path):
    if kind_by_path is None:
        return {}
    if not isinstance(kind_by_path, Mapping):
        raise ValueError("kind_by_path must map exact /Game/ object paths to confirmed purposes.")
    result = {}
    for path, kind in kind_by_path.items():
        if not _project_asset_path(path):
            raise ValueError("Purpose declarations require exact /Game/ asset identifiers.")
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError("Purpose must be color, normal, mask or unknown.")
        result[path] = kind
    return result


def _editor_context(unreal_module=None):
    if unreal_module is None:
        try:
            unreal_module = import_module("unreal")
        except ImportError as exc:
            raise UnrealContextError(
                "Run inside Unreal Editor's Python environment; do not pip-install unreal."
            ) from exc
    try:
        version = unreal_module.SystemLibrary.get_engine_version()
        project_file = unreal_module.Paths.get_project_file_path()
        selection_api = unreal_module.EditorUtilityLibrary.get_selected_assets
        selection_data_api = unreal_module.EditorUtilityLibrary.get_selected_asset_data
        texture_class = unreal_module.Texture2D
        compression_enum = unreal_module.TextureCompressionSettings
    except AttributeError as exc:
        raise UnrealContextError("Required Unreal Editor Python APIs are unavailable.") from exc
    if not isinstance(version, str) or not version.startswith("5.3."):
        raise UnrealContextError("This adapter targets UE 5.3.x; review other versions separately.")
    if not isinstance(project_file, str) or not project_file:
        raise UnrealContextError("No open .uproject was reported by Unreal.")
    project = PurePosixPath(project_file.replace("\\", "/"))
    if project.suffix.lower() != ".uproject" or project.stem != "TextureAuditTool":
        raise UnrealContextError("Open the TextureAuditTool project before scanning.")
    return unreal_module, version, project.stem, selection_api, selection_data_api, texture_class, compression_enum


def _read_property(asset, property_name, asset_path, notices):
    try:
        return asset.get_editor_property(property_name)
    except Exception as exc:
        # Exception text may contain physical import filenames. Store the
        # exception type only; a missing field is not a successful read.
        notices.append(ReadNotice(
            asset_path, property_name, "PROPERTY_READ_FAILED",
            "Property read failed: " + type(exc).__name__,
        ))
        return None


def _read_source_dimensions(asset, asset_path, ue, notices):
    """Read freshly gathered Texture2D source logical dimensions, never mip size.

    In UE 5.3 Texture2D.cpp, Dimensions is produced by GetImportedSize(); for
    uncooked editor assets that reads Source.GetLogicalSize(). Fresh AssetData
    avoids relying on an older saved registry tag after an in-memory change.
    Only this tag is read; import source filenames are not inspected.
    """
    try:
        helpers = ue.AssetRegistryHelpers
        data = helpers.create_asset_data(asset)
        if not helpers.is_valid(data):
            notices.append(ReadNotice(
                asset_path, "dimensions", "SOURCE_ASSET_DATA_INVALID",
                "Fresh asset metadata is invalid; source dimensions remain unknown.",
            ))
            return None, None, None
        raw = helpers.get_tag_value(data, "Dimensions")
    except Exception as exc:
        notices.append(ReadNotice(
            asset_path, "dimensions", "SOURCE_DIMENSIONS_READ_FAILED",
            "Source dimension tag read failed: " + type(exc).__name__,
        ))
        return None, None, None
    if raw is None or raw == "":
        notices.append(ReadNotice(
            asset_path, "dimensions", "SOURCE_DIMENSIONS_MISSING",
            "Dimensions tag is missing; no default or resource size is substituted.",
        ))
        return None, None, None
    if not isinstance(raw, str) or len(raw) > 64:
        notices.append(ReadNotice(
            asset_path, "dimensions", "SOURCE_DIMENSIONS_INVALID",
            "Dimensions tag has an unsupported type or length.",
        ))
        return None, None, None
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", raw.strip())
    if match is not None:
        width, height = (int(value) for value in match.groups())
        if width <= 2147483647 and height <= 2147483647:
            return width, height, raw
    notices.append(ReadNotice(
        asset_path, "dimensions", "SOURCE_DIMENSIONS_INVALID",
        "Dimensions must contain two positive int32 sizes in WIDTHxHEIGHT form.",
    ))
    return None, None, raw


def _compression_label(value, enum_class):
    if value is None:
        return None
    if not isinstance(value, enum_class):
        return None
    # Compare actual enum members rather than fragile display strings.
    for member, label in (
        ("TC_DEFAULT", "default"), ("TC_NORMALMAP", "normalmap"),
        ("TC_MASKS", "masks"), ("TC_GRAYSCALE", "grayscale"),
        ("TC_DISPLACEMENTMAP", "displacementmap"), ("TC_BC7", "bc7"),
        ("TC_HDR", "hdr"), ("TC_HDR_COMPRESSED", "hdr_compressed"),
        ("TC_ALPHA", "alpha"), ("TC_EDITOR_ICON", "editor_icon"),
        ("TC_HALF_FLOAT", "half_float"), ("TC_SINGLE_FLOAT", "single_float"),
        ("TC_HDR_F32", "hdr_f32"),
        ("TC_VECTOR_DISPLACEMENTMAP", "vector_displacementmap"),
        ("TC_DISTANCE_FIELD_FONT", "distance_field_font"),
    ):
        candidate = getattr(enum_class, member, None)
        if candidate is not None and value == candidate:
            return label
    return None


def collect_selected_textures(kind_by_path=None, max_assets=256, unreal_module=None):
    """Read only currently selected, exact Texture2D assets under /Game/.

    Purposes must be provided explicitly after material-usage review. Names
    and compression settings are never used to infer them. Source dimensions
    come from freshly gathered UE 5.3 Texture2D Dimensions tags. Failed or
    malformed reads remain None; no runtime-size or default-size fallback.
    """
    declared = _declared_kinds(kind_by_path)
    if type(max_assets) is not int or not 1 <= max_assets <= 256:
        raise ValueError("max_assets must be an integer from 1 to 256.")
    (ue, version, project_name, get_selected, get_selected_data,
     texture_class, compression_enum) = _editor_context(unreal_module)
    # Check selection metadata before the selected-assets API loads objects.
    selected_data = tuple(get_selected_data())
    if not selected_data:
        raise UnrealContextError("Select Texture2D assets in the Content Browser first.")
    if len(selected_data) > max_assets:
        raise UnrealContextError("Selection exceeds the approved batch limit; select fewer assets.")
    selected = tuple(get_selected())
    if len(selected) != len(selected_data):
        raise UnrealContextError("Selection changed or an asset failed to load; reselect and retry.")
    notices, skipped, reads = [], [], []
    for asset in selected:
        if asset is None:
            raise UnrealContextError("A selected asset could not be loaded; scan stopped.")
        try:
            is_texture = isinstance(asset, texture_class)
            class_name = asset.get_class().get_name()
            path = asset.get_path_name()
        except Exception as exc:
            raise UnrealContextError("Selected asset identity read failed.") from exc
        # Engine and plugin objects are deliberately outside the first scope.
        if not is_texture or class_name != "Texture2D" or not _project_asset_path(path):
            skipped.append({
                "asset_path": path if _project_asset_path(path) else None,
                "class_name": class_name,
                "reason": "Only exact Texture2D assets under /Game/ are in scope.",
            })
            continue
        srgb = _read_property(asset, "srgb", path, notices)
        if type(srgb) is not bool:
            notices.append(ReadNotice(path, "srgb", "SRGB_UNAVAILABLE", "No boolean sRGB value was read."))
            srgb = None
        raw = _read_property(asset, "compression_settings", path, notices)
        compression = _compression_label(raw, compression_enum)
        if compression is None:
            notices.append(ReadNotice(
                path, "compression", "COMPRESSION_UNAVAILABLE",
                "Compression enum was unavailable or has no verified semantic mapping.",
            ))
        width, height, dimensions_tag = _read_source_dimensions(asset, path, ue, notices)
        snapshot = AssetSnapshot.from_mapping({
            "asset_path": path, "name": asset.get_name(),
            "kind": declared.get(path, "unknown"),
            "width": width, "height": height, "srgb": srgb,
            "compression": compression,
        })
        dimensions_read = width is not None and height is not None
        reads.append(TextureRead(
            snapshot, str(raw) if raw is not None else None,
            # This flag means a validated source-size tag was read, not that
            # every asset has separately passed manual editor UI review.
            source_dimensions_verified=dimensions_read,
            raw_dimensions=dimensions_tag,
            dimension_source="ue53_texture2d_dimensions_tag" if dimensions_read else None,
        ))
    if not reads:
        raise UnrealContextError("No supported project Texture2D assets were selected.")
    paths = [item.snapshot.asset_path for item in reads]
    if len(paths) != len(set(paths)):
        raise UnrealContextError("Duplicate selected asset identifiers; scan stopped.")
    unused = set(declared) - set(paths)
    if unused:
        raise ValueError("Purpose declarations include assets outside the captured selection.")
    return SelectionCapture(version, project_name, len(selected), tuple(reads), tuple(skipped), tuple(notices))
