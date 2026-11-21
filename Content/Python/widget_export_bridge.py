"""Explicit report-export bridge for the TextureAuditTool editor widget.

Python 3.9+, standard library only. Loading this file does not import unreal,
scan assets, create directories or export reports. Approve and invoke
export_selected_status(output_dir=...) separately inside Unreal Editor.

The caller must supply a new run name under Saved/TextureAudit/Reports. There is no default output directory, automatic retry,
overwrite, cleanup, alternate-drive fallback or background callback.

Export performs a fresh read-only scan of the CURRENT Content Browser
selection. It does not reuse a previous widget scan or repair textures.
An optional texture_kind declares the same purpose for all selected textures.
Omitting it retains the existing per-asset mapping/default-unknown API.

Later, after separately approving Blueprint wiring:
    import runpy
    bridge = runpy.run_path(
        "widget_export_bridge.py"
    )
    result = bridge["export_selected_status"](
        output_dir=approved_new_directory, texture_kind=texture_kind)
    status_text = result["status_text"]

Expose texture_kind as a String input and status_text as a String output.
The Execute Python Script node's
Success? means the Python code executed, while result["ok"] means all three
report files were written and their contents verified. Handle both levels.
Normal UE log/cache writes and Python import caches may occur at runtime.
"""
import csv
from importlib import import_module
import hashlib
import io
import json
from pathlib import Path
import runpy

from texture_audit.paths import new_output_directory


BRIDGE_REVISION = "0.1.2-declared-purpose-widget-export"
_SCAN_BRIDGE_REVISION = "0.1.1-declared-purpose-widget-scan"
_KIND_LABELS = {"color": "颜色（color）", "normal": "法线（normal）",
                "mask": "遮罩（mask）", "unknown": "未声明（unknown）"}
_SOURCE_ROOT = Path(__file__).resolve().parent
_SCAN_BRIDGE_FILE = _SOURCE_ROOT / "widget_scan_bridge.py"
_MAX_FILE_BYTES = 16 * 1024 * 1024

_FILES = {
    "json": "audit_report.json",
    "issues_csv": "issues.csv",
    "suggestions_csv": "suggestions.csv",
}
_COLUMNS = {
    "issues_csv": (
        "asset_path", "asset_name", "kind", "code", "severity",
        "property_name", "actual", "expected", "message", "suggestion_id",
    ),
    "suggestions_csv": (
        "suggestion_id", "asset_path", "asset_name", "kind", "property_name",
        "before", "proposed_after", "reason", "requires_user_confirmation", "applied",
    ),
}
_MESSAGES = {
    "INVALID_BATCH_LIMIT": "导出未执行：批量上限必须是 1 至 256 的整数。",
    "INVALID_PURPOSE": "导出未执行：用途只支持 unknown、color、normal 或 mask。",
    "INVALID_DECLARATION": "导出未执行：批次用途与逐资产用途不能同时提供。",
    "INVALID_OUTPUT_DIR": "导出未执行：请输入新的报告目录名（字母、数字、连字符或下划线）。",
    "OUTPUT_EXISTS": "导出未执行：目标目录已存在，请选择新的目录；已有文件保持不变。",
    "SCAN_BRIDGE_UNAVAILABLE": "导出未执行：扫描桥接文件、版本或接口不符合要求。",
    "SCAN_NOT_COMPLETED": "导出未执行：当前选择的只读扫描未完成，未创建报告目录。",
    "INVALID_REPORT": "导出未执行：扫描结果未通过导出前检查，未创建报告目录。",
    "EXPORTER_UNAVAILABLE": "导出未执行：报告写入模块的路径、版本或接口不符合要求。",
    "FILES_NOT_VERIFIED": "报告输出核对未通过。",
    "EXPORT_FAILED": "报告导出未完成。",
}


class _ExportStop(RuntimeError):
    """A fixed internal error code, with no external exception details."""


def _failure(code, output=None, write_attempted=False, scan_status=None):
    message = _MESSAGES[code]
    if code == "SCAN_NOT_COMPLETED" and isinstance(scan_status, str):
        message += "\n" + scan_status
    if write_attempted:
        message += (
            "\n目标目录可能已有全部或部分输出，但本次未确认导出成功。"
            "\n请保留目录检查，不要重复点击同目录导出；程序未自动清理或覆盖。"
        )
    if output is not None:
        message += "\n目标目录：" + str(output)
    return {
        "bridge_revision": BRIDGE_REVISION,
        "ok": False,
        "error_code": code,
        "status_text": message,
        "output_dir": str(output) if output is not None else None,
        "write_attempted": write_attempted,
        "partial_output_possible": write_attempted,
        "report": None,
        "reports": None,
        "verified_files": None,
    }


def _new_output_dir(output_dir):
    try:
        return new_output_directory(output_dir, "Reports")
    except FileExistsError as exc:
        raise _ExportStop("OUTPUT_EXISTS") from exc
    except (ValueError, OSError, RuntimeError) as exc:
        raise _ExportStop("INVALID_OUTPUT_DIR") from exc


def _scan_current_selection(kind_by_path, max_assets, texture_kind=None):
    if not _SCAN_BRIDGE_FILE.is_file():
        raise _ExportStop("SCAN_BRIDGE_UNAVAILABLE")
    bridge = runpy.run_path(str(_SCAN_BRIDGE_FILE), run_name="_widget_export_scan_bridge")
    scan = bridge.get("scan_selected_status")
    if bridge.get("BRIDGE_REVISION") != _SCAN_BRIDGE_REVISION or not callable(scan):
        raise _ExportStop("SCAN_BRIDGE_UNAVAILABLE")
    arguments = {"kind_by_path": kind_by_path, "max_assets": max_assets}
    if texture_kind is not None:
        arguments["texture_kind"] = texture_kind
    result = scan(**arguments)
    if not isinstance(result, dict) or type(result.get("ok")) is not bool:
        raise _ExportStop("INVALID_REPORT")
    return result


def _report_module():
    # A successful fresh scan has already imported and checked these modules.
    # Import from that context, without changing sys.path or reloading modules.
    module = import_module("texture_audit.report")
    module_file = getattr(module, "__file__", None)
    if not module_file or Path(module_file).resolve() != (_SOURCE_ROOT / "texture_audit/report.py").resolve():
        raise _ExportStop("EXPORTER_UNAVAILABLE")
    if getattr(module, "SCHEMA_VERSION", None) != "0.1.0" or not callable(getattr(module, "write_reports", None)):
        raise _ExportStop("EXPORTER_UNAVAILABLE")
    return module


def _csv_cell(value):
    """Expected CSV representation, including this project's formula escaping."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def _expected_tables(report):
    tables = {"issues_csv": [], "suggestions_csv": []}
    for asset in report["assets"]:
        if not isinstance(asset, dict):
            raise _ExportStop("INVALID_REPORT")
        base = {name: asset[name] for name in ("asset_path", "asset_name", "kind")}
        for key, field in (("issues_csv", "issues"), ("suggestions_csv", "suggestions")):
            if not isinstance(asset[field], list):
                raise _ExportStop("INVALID_REPORT")
            for item in asset[field]:
                if not isinstance(item, dict):
                    raise _ExportStop("INVALID_REPORT")
                # Suggestion records legitimately repeat asset_path. Allow
                # matching identity fields, while rejecting values that would
                # replace the parent asset's identity in the exported row.
                if any(key in item and item[key] != value for key, value in base.items()):
                    raise _ExportStop("INVALID_REPORT")
                if field == "suggestions" and not (
                    item.get("applied") is False and item.get("requires_user_confirmation") is True
                ):
                    raise _ExportStop("INVALID_REPORT")
                row = dict(base, **item)
                tables[key].append({column: _csv_cell(row.get(column)) for column in _COLUMNS[key]})
    summary = report["summary"]
    if len(tables["issues_csv"]) != summary["issues_total"]:
        raise _ExportStop("INVALID_REPORT")
    if len(tables["suggestions_csv"]) != summary["proposed_property_changes"]:
        raise _ExportStop("INVALID_REPORT")
    return tables


def _frozen_report(report):
    """Make a JSON-compatible snapshot before any report-writing operation."""
    try:
        frozen = json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise _ExportStop("INVALID_REPORT") from exc
    if not isinstance(frozen, dict) or not (
        frozen.get("schema_version") == "0.1.0"
        and frozen.get("mode") == "ue_selected_texture_audit"
        and frozen.get("synthetic_input") is False
        and frozen.get("asset_modification_supported") is False
        and frozen.get("adapter_revision") == "0.2.0-source-dimensions"
    ):
        raise _ExportStop("INVALID_REPORT")
    summary, assets = frozen.get("summary"), frozen.get("assets")
    if not isinstance(summary, dict) or not isinstance(assets, list) or not assets:
        raise _ExportStop("INVALID_REPORT")
    for name in ("assets_total", "assets_needing_review", "issues_total", "proposed_property_changes", "modified_assets"):
        if type(summary.get(name)) is not int or summary[name] < 0:
            raise _ExportStop("INVALID_REPORT")
    if summary["modified_assets"] != 0 or summary["assets_total"] != len(assets):
        raise _ExportStop("INVALID_REPORT")
    return frozen


def _verify_outputs(report, tables, returned_paths, output):
    """Read back all outputs. File existence alone does not establish success."""
    if not isinstance(returned_paths, dict) or set(returned_paths) != set(_FILES):
        raise _ExportStop("FILES_NOT_VERIFIED")
    if output.is_symlink() or output.resolve() != output or not output.is_dir():
        raise _ExportStop("FILES_NOT_VERIFIED")
    if {item.name for item in output.iterdir()} != set(_FILES.values()):
        raise _ExportStop("FILES_NOT_VERIFIED")
    paths, verified = {}, {}
    for key, filename in _FILES.items():
        path = output / filename
        returned = returned_paths[key]
        if not isinstance(returned, (str, Path)):
            raise _ExportStop("FILES_NOT_VERIFIED")
        if Path(returned).resolve() != path or path.is_symlink() or path.resolve() != path:
            raise _ExportStop("FILES_NOT_VERIFIED")
        if not path.is_file() or not 0 < path.stat().st_size <= _MAX_FILE_BYTES:
            raise _ExportStop("FILES_NOT_VERIFIED")
        payload = path.read_bytes()
        if not 0 < len(payload) <= _MAX_FILE_BYTES:
            raise _ExportStop("FILES_NOT_VERIFIED")
        if key == "json":
            if json.loads(payload.decode("utf-8")) != report:
                raise _ExportStop("FILES_NOT_VERIFIED")
        else:
            if not payload.startswith(b"\xef\xbb\xbf"):
                raise _ExportStop("FILES_NOT_VERIFIED")
            reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
            if reader.fieldnames != list(_COLUMNS[key]) or list(reader) != tables[key]:
                raise _ExportStop("FILES_NOT_VERIFIED")
        paths[key] = str(path)
        verified[key] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    return paths, verified


def _success_status(report, output):
    summary = report["summary"]
    unknown = sum(asset["kind"] == "unknown" for asset in report["assets"])
    lines = [
        "导出完成：纹理 {assets_total} 项；需复核 {assets_needing_review} 项；问题 {issues_total} 条。".format(**summary),
        "待确认属性建议 {proposed_property_changes} 项；修改资产 {modified_assets} 项。".format(**summary),
    ]
    if unknown:
        lines.append("用途未声明 {} 项：这些纹理未检查用途对应的后缀、sRGB 和压缩匹配。".format(unknown))
    lines.extend([
        "已核对 JSON 内容及两份 CSV 的表头与内容。",
        "报告目录：" + str(output),
        "文件：audit_report.json、issues.csv、suggestions.csv。",
        "本次导出重新读取了当前选择；结果仅覆盖当前项目已启用的规则。",
    ])
    return "\n".join(lines)


def export_selected_status(output_dir, *, kind_by_path=None, max_assets=256, texture_kind=None):
    """Write and verify a fresh selected-texture report after execution approval.

    output_dir is a new name under Saved/TextureAudit/Reports. No outputs are written for a failed
    scan. A writing or verification failure retains all files for diagnosis;
    no deletion, rollback, retry, overwrite, asset mutation or repair occurs.

    This is a synchronous, explicit call. Its returned hashes describe the
    bytes verified during the call, not protection against later file edits.
    """
    if type(max_assets) is not int or not 1 <= max_assets <= 256:
        return _failure("INVALID_BATCH_LIMIT")
    if texture_kind is not None:
        if not isinstance(texture_kind, str) or texture_kind not in _KIND_LABELS:
            return _failure("INVALID_PURPOSE")
        if kind_by_path is not None:
            return _failure("INVALID_DECLARATION")
    output = None
    write_attempted = False
    try:
        output = _new_output_dir(output_dir)
        if texture_kind is None:
            scanned = _scan_current_selection(kind_by_path, max_assets)
        else:
            scanned = _scan_current_selection(kind_by_path, max_assets, texture_kind=texture_kind)
        if scanned["ok"] is False:
            return _failure("SCAN_NOT_COMPLETED", output, scan_status=scanned.get("status_text"))
        report = _frozen_report(scanned.get("report"))
        if texture_kind is not None and any(row.get("kind") != texture_kind for row in report["assets"]):
            raise _ExportStop("INVALID_REPORT")
        tables = _expected_tables(report)
        status = _success_status(report, output)
        if texture_kind is not None:
            status = "本批次用途：" + _KIND_LABELS[texture_kind] + "（适用于本次全部所选纹理）。\n" + status
        exporter = _report_module()
        # Recheck after the scan so a directory created in the meantime is
        # rejected. The existing writer also uses mkdir(exist_ok=False) and
        # exclusive file creation; it never silently replaces another run.
        if _new_output_dir(output_dir) != output:
            raise _ExportStop("INVALID_OUTPUT_DIR")
        write_attempted = True
        returned_paths = exporter.write_reports(report, output)
        paths, verified = _verify_outputs(report, tables, returned_paths, output)
        return {
            "bridge_revision": BRIDGE_REVISION,
            "ok": True,
            "error_code": None,
            "status_text": status,
            "output_dir": str(output),
            "write_attempted": True,
            "partial_output_possible": False,
            "report": report,
            "reports": paths,
            "verified_files": verified,
        }
    except _ExportStop as exc:
        return _failure(str(exc), output, write_attempted)
    except FileExistsError:
        code = "FILES_NOT_VERIFIED" if write_attempted else "OUTPUT_EXISTS"
        return _failure(code, output, write_attempted)
    except (ValueError, TypeError, KeyError):
        code = "FILES_NOT_VERIFIED" if write_attempted else "INVALID_REPORT"
        return _failure(code, output, write_attempted)
    except Exception:
        # Do not copy external exception text, which can contain local paths,
        # account details or other unrelated information, into the UI result.
        return _failure("EXPORT_FAILED", output, write_attempted)


# Intentionally no __main__ execution, UE callbacks or automatic directory creation.
