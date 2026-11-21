"""Read-only UI bridge for the TextureAuditTool Editor Utility Widget.

Python 3.9+, standard library only. Loading this file with runpy is inert:
it neither imports unreal nor scans assets. Invoke scan_selected_status()
only inside UE, after separately approving the scan execution step.

Execute Python Script node, after separately approving Blueprint wiring:
    import runpy
    bridge = runpy.run_path(
        "widget_scan_bridge.py"
    )
    result = bridge["scan_selected_status"](texture_kind=texture_kind)
    status_text = result["status_text"]
    scan_ok = result["ok"]

Create a String input named texture_kind from Combo_Purpose's selected option,
and output pins named status_text (String) and scan_ok (Boolean).
The node's built-in Success? means the Python script executed; scan_ok means
the read-only capture and report checks succeeded. They are not equivalent.
No property setters, asset saves, repairs, exports or downloads are provided.
Normal UE log/cache writes and Python import caches may occur at runtime.
"""
from importlib import import_module
from pathlib import Path
import runpy
from texture_audit.paths import PROJECT_FILE


BRIDGE_REVISION = "0.1.1-declared-purpose-widget-scan"
_ADAPTER_REVISION = "0.2.0-source-dimensions"
_PROJECT_FILE = PROJECT_FILE
_SCAN_FILE = Path(__file__).resolve().with_name("scan_selected_textures.py")
_KINDS = ("color", "normal", "mask", "unknown")
_KIND_LABELS = {"color": "颜色（color）", "normal": "法线（normal）",
                "mask": "遮罩（mask）", "unknown": "未声明（unknown）"}


class _BridgeStop(RuntimeError):
    """A safe, fixed error code; never includes external exception text."""


_ERROR_TEXT = {
    "INVALID_BATCH_LIMIT": "扫描未执行：批量上限必须是 1 至 256 的整数。",
    "UNREAL_UNAVAILABLE": "扫描未执行：请在 UE 编辑器自带的 Python 环境中运行。",
    "WRONG_ENGINE": "扫描未执行：此桥接脚本仅适用于已验证的 UE 5.3.x。",
    "WRONG_PROJECT": "扫描未执行：当前打开的工程不是指定的 TextureAuditTool 工程。",
    "SCANNER_UNAVAILABLE": "扫描未执行：扫描入口文件或 preview 接口不可用。",
    "EDITOR_CONTEXT_ERROR": "扫描未完成：请先在内容浏览器选择项目 Texture2D，并确认选择数量和编辑器状态。",
    "INVALID_DECLARATION": "扫描未完成：请核对用途声明、所选资产范围与调用参数。",
    "INVALID_PURPOSE": "扫描未执行：用途只支持 unknown、color、normal 或 mask。",
    "SELECTION_CHANGED": "扫描未完成：读取期间的选择或用途数据不一致，请重新选择并扫描。",
    "SCANNER_STATE_ERROR": "扫描未完成：扫描器状态或已加载模块不符合要求，请查看并核对扫描器配置。",
    "REPORT_CONTRACT_ERROR": "扫描结果未通过一致性检查，不展示成功状态。请检查报告来源与字段。",
    "SCAN_FAILED": "扫描未完成：发生未预期错误。请停止操作，检查扫描入口与编辑器状态。",
}


def _failure(code):
    """Return a fresh result, with no stale report or claimed change count."""
    return {
        "bridge_revision": BRIDGE_REVISION,
        "ok": False,
        "status_text": _ERROR_TEXT[code],
        "error_code": code,
        "report": None,
    }


def _editor_context():
    try:
        ue = import_module("unreal")
    except ImportError as exc:
        raise _BridgeStop("UNREAL_UNAVAILABLE") from exc
    version = ue.SystemLibrary.get_engine_version()
    if not isinstance(version, str) or not version.startswith("5.3."):
        raise _BridgeStop("WRONG_ENGINE")
    project_name = ue.Paths.get_project_file_path()
    if not isinstance(project_name, str) or not project_name:
        raise _BridgeStop("WRONG_PROJECT")
    project = Path(ue.Paths.convert_relative_path_to_full(project_name)).resolve()
    if project != Path(_PROJECT_FILE).resolve() or not project.is_file():
        raise _BridgeStop("WRONG_PROJECT")
    return ue


def _sequence(value):
    return isinstance(value, (list, tuple))


def _declared_selection(ue, kind, max_assets):
    """Read selection identities only, with a count guard before asset loading."""
    try:
        library = ue.EditorUtilityLibrary
        metadata = tuple(library.get_selected_asset_data())
        if not metadata or len(metadata) > max_assets:
            raise _BridgeStop("EDITOR_CONTEXT_ERROR")
        assets = tuple(library.get_selected_assets())
        if len(assets) != len(metadata):
            raise _BridgeStop("SELECTION_CHANGED")
        declarations = {}
        for asset in assets:
            if asset is None:
                raise _BridgeStop("SELECTION_CHANGED")
            path = asset.get_path_name()
            if (not isinstance(asset, ue.Texture2D)
                    or asset.get_class().get_name() != "Texture2D"
                    or not isinstance(path, str) or not path.startswith("/Game/")):
                continue
            if path in declarations:
                raise _BridgeStop("SELECTION_CHANGED")
            declarations[path] = kind
        if not declarations:
            raise _BridgeStop("EDITOR_CONTEXT_ERROR")
        return declarations, len(metadata)
    except _BridgeStop:
        raise
    except Exception as exc:
        raise _BridgeStop("EDITOR_CONTEXT_ERROR") from exc


def _check_declared_report(report, declarations, selected_count):
    """Reject a stale/mixed-purpose result before export can create any files."""
    rows = report["assets"]
    if (report["capture"]["selected_objects"] != selected_count
            or tuple(row["asset_path"] for row in rows) != tuple(declarations)
            or any(row["kind"] != declarations[row["asset_path"]] for row in rows)):
        raise _BridgeStop("SELECTION_CHANGED")


def _report_counts(report):
    """Check the known report contract and recompute displayed totals.

    These checks catch incompatible or internally inconsistent reports. They
    do not independently validate Unreal's property reads or prove that an
    asset is suitable for every material, platform or production pipeline.
    """
    if not isinstance(report, dict) or not (
        report.get("schema_version") == "0.1.0"
        and report.get("mode") == "ue_selected_texture_audit"
        and report.get("adapter_revision") == _ADAPTER_REVISION
        and report.get("synthetic_input") is False
        and report.get("asset_modification_supported") is False
    ):
        raise _BridgeStop("REPORT_CONTRACT_ERROR")
    summary = report.get("summary")
    rows = report.get("assets")
    capture = report.get("capture")
    if not isinstance(summary, dict) or not isinstance(capture, dict):
        raise _BridgeStop("REPORT_CONTRACT_ERROR")
    if not isinstance(rows, list) or not rows:
        raise _BridgeStop("REPORT_CONTRACT_ERROR")
    reads = capture.get("textures")
    skipped = capture.get("skipped_objects")
    notices = capture.get("notices")
    version = capture.get("engine_version")
    if not (
        _sequence(reads) and len(reads) == len(rows)
        and _sequence(skipped) and _sequence(notices)
        and capture.get("project_name") == "TextureAuditTool"
        and isinstance(version, str) and version.startswith("5.3.")
        and type(capture.get("selected_objects")) is int
        and capture["selected_objects"] == len(reads) + len(skipped)
    ):
        raise _BridgeStop("REPORT_CONTRACT_ERROR")
    expected = {
        "assets_total": len(rows),
        "assets_needing_review": 0,
        "assets_compliant_with_selected_policy": 0,
        "issues_total": 0,
        "proposed_property_changes": 0,
        "modified_assets": 0,
    }
    paths, read_paths = [], []
    unknown, dimensions_unknown = 0, 0
    for row, read in zip(rows, reads):
        if not isinstance(row, dict) or not isinstance(read, dict):
            raise _BridgeStop("REPORT_CONTRACT_ERROR")
        snapshot = read.get("snapshot")
        if not isinstance(snapshot, dict):
            raise _BridgeStop("REPORT_CONTRACT_ERROR")
        path, kind = row.get("asset_path"), row.get("kind")
        issues, suggestions = row.get("issues"), row.get("suggestions")
        if not (
            isinstance(path, str) and path.startswith("/Game/")
            and kind in _KINDS
            and _sequence(issues) and _sequence(suggestions)
            and snapshot.get("asset_path") == path
            and snapshot.get("kind") == kind
        ):
            raise _BridgeStop("REPORT_CONTRACT_ERROR")
        for suggestion in suggestions:
            if not isinstance(suggestion, dict) or not (
                suggestion.get("applied") is False
                and suggestion.get("requires_user_confirmation") is True
            ):
                raise _BridgeStop("REPORT_CONTRACT_ERROR")
        paths.append(path)
        read_paths.append(snapshot["asset_path"])
        unknown += kind == "unknown"
        dimensions_unknown += snapshot.get("width") is None or snapshot.get("height") is None
        expected["assets_needing_review"] += bool(issues)
        expected["assets_compliant_with_selected_policy"] += not issues
        expected["issues_total"] += len(issues)
        expected["proposed_property_changes"] += len(suggestions)
    if len(set(paths)) != len(paths) or paths != read_paths:
        raise _BridgeStop("REPORT_CONTRACT_ERROR")
    if any(type(summary.get(key)) is not int or summary[key] != value
           for key, value in expected.items()):
        raise _BridgeStop("REPORT_CONTRACT_ERROR")
    return expected, unknown, dimensions_unknown, len(skipped), len(notices)


def _status_text(counts, unknown, dimensions_unknown, skipped, notices):
    lines = [
        "只读扫描完成：纹理 {assets_total} 项；需复核 {assets_needing_review} 项；问题 {issues_total} 条。".format(**counts),
        "待确认属性建议 {proposed_property_changes} 项；修改资产 {modified_assets} 项。".format(**counts),
    ]
    if unknown:
        lines.append(
            "用途未声明 {} 项：这些纹理未检查用途对应的后缀、sRGB 和压缩匹配。".format(unknown)
        )
    if dimensions_unknown:
        lines.append("源尺寸未知 {} 项：对应尺寸规则尚未完整检查。".format(dimensions_unknown))
    if skipped or notices:
        lines.append("跳过范围外对象 {} 项；字段读取提示 {} 条。".format(skipped, notices))
    lines.append("结果仅覆盖当前项目已启用的规则；属性建议尚未应用，报告未导出。")
    return "\n".join(lines)


def scan_selected_status(kind_by_path=None, max_assets=256, *, texture_kind=None):
    """Capture selected textures and return UI-ready data; no asset mutation.

    texture_kind explicitly applies unknown/color/normal/mask to this entire
    selection. Do not supply it together with kind_by_path. Omit both for
    backwards-compatible unknown purposes. Explicit per-asset declarations
    use the scanner's exact /Game/ object-path mapping; declarations for
    unselected assets are rejected. No inference, reload, retry or fallback
    is performed. Errors return ok=False and report=None, never old results.
    """
    if type(max_assets) is not int or not 1 <= max_assets <= 256:
        return _failure("INVALID_BATCH_LIMIT")
    if texture_kind is not None:
        if not isinstance(texture_kind, str) or texture_kind not in _KINDS:
            return _failure("INVALID_PURPOSE")
        if kind_by_path is not None:
            return _failure("INVALID_DECLARATION")
    try:
        ue = _editor_context()
        if not _SCAN_FILE.is_file():
            raise _BridgeStop("SCANNER_UNAVAILABLE")
        entry = runpy.run_path(str(_SCAN_FILE), run_name="_widget_scanner_entry")
        preview = entry.get("preview")
        if not callable(preview):
            raise _BridgeStop("SCANNER_UNAVAILABLE")
        declarations, selected_count = None, None
        if texture_kind is not None:
            declarations, selected_count = _declared_selection(ue, texture_kind, max_assets)
            kind_by_path = declarations
        report = preview(kind_by_path=kind_by_path, max_assets=max_assets)
        counts, unknown, dimensions_unknown, skipped, notices = _report_counts(report)
        if declarations is not None:
            _check_declared_report(report, declarations, selected_count)
        status = _status_text(counts, unknown, dimensions_unknown, skipped, notices)
        if texture_kind is not None:
            status = "本批次用途：" + _KIND_LABELS[texture_kind] + "（适用于本次全部所选纹理）。\n" + status
        ue.log("TA_WIDGET_SCAN_COMPLETED: textures={}; issues={}; unknown_purposes={}; modified_assets=0".format(
            counts["assets_total"], counts["issues_total"], unknown,
        ))
        return {
            "bridge_revision": BRIDGE_REVISION,
            "ok": True,
            "status_text": status,
            "error_code": None,
            "report": report,
        }
    except _BridgeStop as exc:
        return _failure(str(exc))
    except Exception as exc:
        # The scanner's exception class is deliberately not imported at load
        # time. Match its name, not exception text that could contain paths.
        if type(exc).__name__ == "UnrealContextError":
            return _failure("EDITOR_CONTEXT_ERROR")
        if isinstance(exc, ValueError):
            return _failure("INVALID_DECLARATION")
        if isinstance(exc, RuntimeError):
            return _failure("SCANNER_STATE_ERROR")
        return _failure("SCAN_FAILED")


# Intentionally no automatic __main__ execution or UE callback registration.
