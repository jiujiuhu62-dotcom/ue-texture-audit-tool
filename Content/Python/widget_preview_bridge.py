"""Read-only sRGB plan preview bridge for the TextureAuditTool widget.

Python 3.9+, standard library only. Import/runpy loading does not import unreal,
read a selection, create a plan, write reports or register editor callbacks.
Call preview_selected_status() separately inside UE after execution approval.

texture_kind is an explicit user declaration for ALL textures in this batch:
"color", "normal" or "mask". The default "unknown" refuses to create a plan.
This interface is intended for same-purpose selections, never inferred names.
Scope: saved Texture2D assets under /Game/TextureAuditDemo/, at most 16.
The original T_Stone_BC is excluded from eligible changes by the existing core.

Proposed Execute Python Script pins (Blueprint wiring is a separate step):
    input  texture_kind : String
    output status_text  : String

    import runpy
    status_text = "预览未完成，请查看输出日志。"
    bridge = runpy.run_path(
        "widget_preview_bridge.py"
    )
    result = bridge["preview_selected_status"](texture_kind=texture_kind)
    status_text = result["status_text"]

The node's Success? only means Python execution succeeded. Domain refusals
return ok=False and a fresh status_text, with plan=None. This bridge calls
only the existing preview entry, never an asset mutation or repair entry.
Normal editor loads, module imports and UE logging can still update caches.
"""
from dataclasses import asdict
from importlib import import_module
import hashlib
import json
from pathlib import Path
import re
import runpy
from texture_audit.paths import PROJECT_FILE
import sys


BRIDGE_REVISION = "0.1.1-source-tag-whitespace"
_ROOT = Path(__file__).resolve().parent
_ENTRY_FILE = _ROOT / "repair_selected_textures.py"
_PROJECT_FILE = PROJECT_FILE
_ADAPTER_REVISION = "0.2.0-source-dimensions"
_REPAIR_REVISION = "0.1.0-srgb-pilot"
_PLAN_SCHEMA = "texture_srgb_repair_plan/0.1"
_SCOPE = "/Game/TextureAuditDemo/"
_PROTECTED = "/Game/TextureAuditDemo/T_Stone_BC.T_Stone_BC"
_MAX_PLAN_BYTES = 2 * 1024 * 1024
_KIND_LABELS = {"color": "颜色（color）", "normal": "法线（normal）", "mask": "遮罩（mask）"}

_ERROR_TEXT = {
    "PURPOSE_REQUIRED": "预览未执行：请先明确本批次纹理用途（color / normal / mask）；未声明用途不会生成修复计划。",
    "INVALID_PURPOSE": "预览未执行：用途只接受 color、normal 或 mask。请按实际材质用途选择，同一批次应使用同一用途。",
    "INVALID_BATCH_LIMIT": "预览未执行：本阶段批量上限必须是 1 至 16 的整数。",
    "UNREAL_UNAVAILABLE": "预览未执行：请在 UE 编辑器自带的 Python 环境中运行。",
    "WRONG_ENGINE": "预览未执行：此脚本目前仅适用于已验证的 UE 5.3.x。",
    "WRONG_PROJECT": "预览未执行：当前工程不是指定的 D 盘 TextureAuditTool 工程。",
    "EDITOR_API_UNAVAILABLE": "预览未执行：所需的编辑器选择 API 不可用。",
    "EMPTY_SELECTION": "预览未执行：请先在内容浏览器选中测试目录内的 Texture2D。",
    "SELECTION_LIMIT": "预览未执行：选择数量超过本次上限，请减少所选纹理。",
    "UNSUPPORTED_SELECTION": "预览未执行：请只选择 /Game/TextureAuditDemo/ 内的 Texture2D，不要混选关卡、控件或其他资产。",
    "SELECTION_CHANGED": "预览未完成：本次读取过程中选择发生变化或对象未能完整加载，请重新核对选择。",
    "PREVIEW_ENTRY_UNAVAILABLE": "预览未执行：本地预览入口或其版本不符合要求，请检查脚本。",
    "PREVIEW_STATE_ERROR": "预览未完成：现有模块、资产范围或文件状态不符合预览要求，请停止并核对。",
    "PREVIEW_CONTRACT_ERROR": "预览结果未通过一致性检查，不展示候选修改；请检查脚本版本与返回字段。",
    "PREVIEW_FAILED": "预览未完成：发生未预期错误，请查看本地编辑器状态与脚本配置。",
}


class _PreviewStop(RuntimeError):
    """Fixed internal error code. External exception text is never displayed."""


def _failure(code):
    return {
        "bridge_revision": BRIDGE_REVISION,
        "ok": False,
        "error_code": code,
        "status_text": _ERROR_TEXT[code],
        "preview_only": True,
        "modified_assets": 0,
        "counts": None,
        "plan": None,
    }


def _editor_context():
    try:
        ue = import_module("unreal")
    except ImportError as exc:
        raise _PreviewStop("UNREAL_UNAVAILABLE") from exc
    version = ue.SystemLibrary.get_engine_version()
    if not isinstance(version, str) or not version.startswith("5.3."):
        raise _PreviewStop("WRONG_ENGINE")
    filename = ue.Paths.get_project_file_path()
    if not isinstance(filename, str) or not filename:
        raise _PreviewStop("WRONG_PROJECT")
    project = Path(ue.Paths.convert_relative_path_to_full(filename)).resolve()
    if project != _PROJECT_FILE.resolve() or not project.is_file():
        raise _PreviewStop("WRONG_PROJECT")
    library = getattr(ue, "EditorUtilityLibrary", None)
    if not (
        callable(getattr(library, "get_selected_asset_data", None))
        and callable(getattr(library, "get_selected_assets", None))
        and isinstance(getattr(ue, "Texture2D", None), type)
    ):
        raise _PreviewStop("EDITOR_API_UNAVAILABLE")
    return ue, project, version


def _pilot_path(identifier):
    if not isinstance(identifier, str) or not identifier.startswith(_SCOPE):
        return False
    if "\\" in identifier or ":" in identifier or any(ord(c) < 32 for c in identifier):
        return False
    package, separator, name = identifier.rpartition(".")
    return bool(
        separator and name and "." not in package
        and package.rsplit("/", 1)[-1] == name
        and not any(part in ("", ".", "..") for part in package[1:].split("/"))
    )


def _declared_selection(ue, texture_kind, max_assets):
    """Limit selection metadata before loading objects; do not read properties."""
    metadata = tuple(ue.EditorUtilityLibrary.get_selected_asset_data())
    if not metadata:
        raise _PreviewStop("EMPTY_SELECTION")
    if len(metadata) > max_assets:
        raise _PreviewStop("SELECTION_LIMIT")
    selected = tuple(ue.EditorUtilityLibrary.get_selected_assets())
    if len(selected) != len(metadata):
        raise _PreviewStop("SELECTION_CHANGED")
    paths = []
    for asset in selected:
        if asset is None:
            raise _PreviewStop("SELECTION_CHANGED")
        if not isinstance(asset, ue.Texture2D) or asset.get_class().get_name() != "Texture2D":
            raise _PreviewStop("UNSUPPORTED_SELECTION")
        identifier = asset.get_path_name()
        if not _pilot_path(identifier):
            raise _PreviewStop("UNSUPPORTED_SELECTION")
        paths.append(identifier)
    if len(paths) != len(set(paths)):
        raise _PreviewStop("SELECTION_CHANGED")
    return {identifier: texture_kind for identifier in paths}


def _preview_entry():
    if not _ENTRY_FILE.is_file():
        raise _PreviewStop("PREVIEW_ENTRY_UNAVAILABLE")
    entry = runpy.run_path(str(_ENTRY_FILE), run_name="_widget_repair_preview_entry")
    if not (
        callable(entry.get("preview"))
        and entry.get("_ADAPTER_REVISION") == _ADAPTER_REVISION
        and entry.get("_REPAIR_REVISION") == _REPAIR_REVISION
        and Path(entry.get("_ROOT", "")).resolve() == _ROOT
    ):
        raise _PreviewStop("PREVIEW_ENTRY_UNAVAILABLE")
    return entry["preview"]


def _loaded_rules():
    """Review the modules loaded by the existing entry; never reload or replace."""
    for name, revision_name, revision in (
        ("texture_audit.rules", None, None),
        ("texture_audit.ue_adapter", "ADAPTER_REVISION", _ADAPTER_REVISION),
        ("texture_audit.repair", "REPAIR_REVISION", _REPAIR_REVISION),
    ):
        module = sys.modules.get(name)
        filename = getattr(module, "__file__", None)
        expected = _ROOT / "texture_audit" / (name.rsplit(".", 1)[-1] + ".py")
        if not isinstance(filename, str) or Path(filename).resolve() != expected.resolve():
            raise _PreviewStop("PREVIEW_STATE_ERROR")
        if revision_name and getattr(module, revision_name, None) != revision:
            raise _PreviewStop("PREVIEW_STATE_ERROR")
    core = sys.modules["texture_audit.repair"]
    if getattr(core, "SCOPE", None) != _SCOPE or getattr(core, "PROTECTED", None) != _PROTECTED:
        raise _PreviewStop("PREVIEW_STATE_ERROR")
    return sys.modules["texture_audit.rules"]


def _frozen(value):
    data = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    if len(data.encode("utf-8")) > _MAX_PLAN_BYTES:
        raise _PreviewStop("PREVIEW_CONTRACT_ERROR")
    return json.loads(data)


def _digest(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _require(condition):
    if not condition:
        raise _PreviewStop("PREVIEW_CONTRACT_ERROR")


def _validate_plan(value, declarations, max_assets, project, version):
    """Recompute rule results and eligible candidates from this plan's capture.

    This checks the UI/core boundary, not the correctness of every UE property
    read. Execution must later refresh selection, files and live values again.
    """
    _require(isinstance(value, dict))
    plan = _frozen(value)
    _require(
        plan.get("schema") == _PLAN_SCHEMA
        and plan.get("revision") == _REPAIR_REVISION
        and plan.get("adapter_revision") == _ADAPTER_REVISION
        and plan.get("status") == "preview_only"
        and type(plan.get("modified_assets")) is int and plan["modified_assets"] == 0
        and type(plan.get("max_assets")) is int and plan["max_assets"] == max_assets
        and plan.get("kind_by_path") == declarations
    )
    project_name = plan.get("project_file")
    _require(isinstance(project_name, str) and Path(project_name).resolve() == project)
    identifier = plan.get("plan_id")
    _require(isinstance(identifier, str) and re.fullmatch(r"[0-9a-f]{64}", identifier) is not None)
    _require(identifier == _digest({key: item for key, item in plan.items() if key != "plan_id"}))

    capture = plan.get("capture")
    _require(isinstance(capture, dict))
    reads = capture.get("textures")
    notices = capture.get("notices")
    _require(
        capture.get("engine_version") == version
        and capture.get("project_name") == "TextureAuditTool"
        and type(capture.get("selected_objects")) is int
        and capture["selected_objects"] == len(declarations)
        and isinstance(reads, list) and len(reads) == len(declarations)
        and capture.get("skipped_objects") == []
        and isinstance(notices, list)
    )
    rules = _loaded_rules()
    policy = rules.AuditPolicy()
    _require(plan.get("policy") == _frozen(asdict(policy)))
    snapshots = []
    dimensions_unknown = 0
    properties_unknown = 0
    for read in reads:
        _require(isinstance(read, dict) and isinstance(read.get("snapshot"), dict))
        snapshot = rules.AssetSnapshot.from_mapping(read["snapshot"])
        _require(
            _pilot_path(snapshot.asset_path)
            and snapshot.kind == declarations.get(snapshot.asset_path)
            and snapshot.name == snapshot.asset_path.rsplit(".", 1)[-1]
        )
        known_size = snapshot.width is not None and snapshot.height is not None
        _require(read.get("source_dimensions_verified") is known_size)
        if known_size:
            _require(
                read.get("dimension_source") == "ue53_texture2d_dimensions_tag"
                and isinstance(read.get("raw_dimensions"), str)
                and read["raw_dimensions"].strip() == "{}x{}".format(snapshot.width, snapshot.height)
            )
        else:
            _require(read.get("dimension_source") is None)
            dimensions_unknown += 1
        properties_unknown += snapshot.srgb is None or snapshot.compression is None
        snapshots.append(snapshot)
    _require([snapshot.asset_path for snapshot in snapshots] == list(declarations))

    files = plan.get("files")
    _require(isinstance(files, dict) and set(files) == set(declarations))
    for path, fingerprint in files.items():
        _require(isinstance(fingerprint, dict))
        expected_file = path.rpartition(".")[0][len("/Game/"):] + ".uasset"
        _require(
            fingerprint.get("relative_content_file") == expected_file
            and type(fingerprint.get("bytes")) is int
            and 0 < fingerprint["bytes"] <= 256 * 1024 * 1024
            and isinstance(fingerprint.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", fingerprint["sha256"]) is not None
        )

    results = rules.audit_assets(tuple(snapshots), policy)
    expected_audits = _frozen([result.to_dict() for result in results])
    _require(plan.get("audits") == expected_audits)
    eligible = [asdict(change) for result in results for change in result.suggestions
                if change.property_name == "srgb" and change.asset_path != _PROTECTED]
    _require(plan.get("candidate_changes") == _frozen(eligible))
    for change in eligible:
        _require(
            type(change["before"]) is bool and type(change["proposed_after"]) is bool
            and change["before"] != change["proposed_after"]
            and change["applied"] is False and change["requires_user_confirmation"] is True
        )
    counts = {
        "assets_total": len(snapshots),
        "assets_needing_review": sum(bool(result.issues) for result in results),
        "issues_total": sum(len(result.issues) for result in results),
        "eligible_srgb_changes": len(eligible),
        "other_property_suggestions": sum(
            change.property_name != "srgb" for result in results for change in result.suggestions
        ),
        "protected_assets": sum(snapshot.asset_path == _PROTECTED for snapshot in snapshots),
        "dimensions_unknown": dimensions_unknown,
        "properties_unknown": properties_unknown,
        "read_notices": len(notices),
        "modified_assets": 0,
    }
    return plan, counts


def _status_text(plan, counts, texture_kind):
    lines = [
        "只读预览完成：纹理 {assets_total} 项；需复核 {assets_needing_review} 项；问题 {issues_total} 条。".format(**counts),
        "本批次用途声明：{}（由用户指定，适用于本次全部选择）。".format(_KIND_LABELS[texture_kind]),
        "可预览 sRGB 修改 {} 项；修改资产 0 项。".format(counts["eligible_srgb_changes"]),
    ]
    for change in plan["candidate_changes"]:
        lines.append("{}：sRGB {} → {}；尚未应用。".format(
            change["asset_path"].rsplit(".", 1)[-1], change["before"], change["proposed_after"],
        ))
    if not counts["eligible_srgb_changes"]:
        lines.append("当前选择没有可预览的 sRGB 修改；这不代表全部规则或材质效果均已验证。")
    if counts["protected_assets"]:
        lines.append("原始 T_Stone_BC 受保护，未列入修改候选。")
    if counts["other_property_suggestions"]:
        lines.append("其他属性建议 {} 项：当前修复预览仅支持 sRGB，不纳入执行候选。".format(
            counts["other_property_suggestions"],
        ))
    if counts["dimensions_unknown"] or counts["properties_unknown"] or counts["read_notices"]:
        lines.append("读取范围提示：源尺寸未知 {} 项；属性不完整 {} 项；读取提示 {} 条。".format(
            counts["dimensions_unknown"], counts["properties_unknown"], counts["read_notices"],
        ))
    lines.extend([
        "计划编号：" + plan["plan_id"],
        "计划仅在内存中生成，未导出、未执行修复。实际修复前需单独确认并重新核对状态。",
    ])
    return "\n".join(lines)


def preview_selected_status(texture_kind="unknown", max_assets=16):
    """Return a new read-only preview for an explicitly declared same-purpose batch.

    No report/run folders, property setters, saves, downloads, callbacks, retries,
    source edits or automatic repair are provided. Failure never returns an old
    plan. A returned plan is review data, not approval to execute its changes.
    """
    if type(max_assets) is not int or not 1 <= max_assets <= 16:
        return _failure("INVALID_BATCH_LIMIT")
    if not isinstance(texture_kind, str):
        return _failure("INVALID_PURPOSE")
    texture_kind = texture_kind.strip().lower()
    if texture_kind in ("", "unknown"):
        return _failure("PURPOSE_REQUIRED")
    if texture_kind not in _KIND_LABELS:
        return _failure("INVALID_PURPOSE")
    try:
        ue, project, version = _editor_context()
        declarations = _declared_selection(ue, texture_kind, max_assets)
        preview = _preview_entry()
        raw_plan = preview(kind_by_path=declarations, max_assets=max_assets)
        plan, counts = _validate_plan(raw_plan, declarations, max_assets, project, version)
        status = _status_text(plan, counts, texture_kind)
        ue.log("TA_WIDGET_PREVIEW_COMPLETED: textures={}; eligible_srgb={}; modified_assets=0".format(
            counts["assets_total"], counts["eligible_srgb_changes"],
        ))
        return {
            "bridge_revision": BRIDGE_REVISION,
            "ok": True,
            "error_code": None,
            "status_text": status,
            "preview_only": True,
            "modified_assets": 0,
            "counts": counts,
            "plan": plan,
        }
    except _PreviewStop as exc:
        return _failure(str(exc))
    except Exception as exc:
        if type(exc).__name__ == "UnrealContextError":
            return _failure("SELECTION_CHANGED")
        if isinstance(exc, (ValueError, TypeError)):
            return _failure("PREVIEW_CONTRACT_ERROR")
        if isinstance(exc, RuntimeError):
            return _failure("PREVIEW_STATE_ERROR")
        return _failure("PREVIEW_FAILED")


# Intentionally no automatic __main__ execution and no retained plan/UI state.
