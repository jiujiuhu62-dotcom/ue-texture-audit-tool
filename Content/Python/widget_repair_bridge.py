"""Draft widget bridge for explicitly reviewed sRGB repairs in TextureAuditTool.

Python 3.9+, standard library only. Offline tests of the previous draft exposed
a result-comparison bug. This revision awaits offline retesting and live UE
validation. It is not connected to the existing Widget automatically.

Import/runpy loading does not import unreal, read selections, open a dialog,
create records, change assets or register callbacks. Call functions separately.

Proposed Blueprint integration, to implement only after separate approval:
* Preview node calls preview_selected_status(texture_kind, max_assets=16).
  Store BOTH returned plan_json and candidate_ids_json in Widget String
  variables on EVERY result, including failures (which return empty strings).
* Confirmation node receives those strings, the CURRENT Combo_Purpose option
  and an explicit NEW repair run directory. It calls confirm_and_apply_status.
  candidate_ids_json is an explicit pending range, not execution permission.
  A subset may be supplied as selected_ids_json. There is no implicit "all".
* The confirmation function presents ALL requested targets/values, the plan ID,
  backup size and full run directory in a native UE Yes/No dialog, default No.
  Only an explicit Yes permits the existing core's apply(..., confirmed=True).
  Do not add a caller-controlled confirmed flag or bypass the native dialog.
* After ANY confirmation attempt, clear both stored plan/ID strings using the
  returned empty strings. Do not retain an old plan when a new preview fails.
* Python-node Success? describes Python execution, not business success.
  Display status_text on both refusal and success. modified_assets=None means
  execution was attempted but its final result is not verified; never show 0.

Scope remains the existing saved-Texture2D pilot under /Game/TextureAuditDemo/,
sRGB only, up to 16 textures; T_Stone_BC remains protected. The preview adapter requires the project containing these scripts.
Run directories are restricted to Saved/TextureAudit/RepairRuns.
No download, compression edit, rename, pixel edit, save-all, automatic reload,
automatic rollback or retry is implemented.

Existing preview/core sources are pinned to reviewed file hashes. Updating
them requires reviewing this bridge and its tests, not disabling the checks.
There is no Python-global retained plan or hidden approved state.
"""
from importlib import import_module
import hashlib
import json
from pathlib import Path
import runpy
import shutil

from texture_audit.paths import new_output_directory


BRIDGE_REVISION = "0.1.1-json-result-normalization-draft"
_ROOT = Path(__file__).resolve().parent
_PREVIEW_FILE = _ROOT / "widget_preview_bridge.py"
_ENTRY_FILE = _ROOT / "repair_selected_textures.py"
_ADAPTER_REVISION = "0.2.0-source-dimensions"
_REPAIR_REVISION = "0.1.0-srgb-pilot"
_PREVIEW_REVISION = "0.1.1-source-tag-whitespace"
_MAX_PLAN_BYTES = 2 * 1024 * 1024
_MAX_IDS_BYTES = 64 * 1024
_MAX_DIALOG_CHARS = 24576
_SCOPE = "/Game/TextureAuditDemo/"
_PROTECTED = "/Game/TextureAuditDemo/T_Stone_BC.T_Stone_BC"
_KINDS = {"color": "颜色", "normal": "法线", "mask": "遮罩"}
_STABLE_PLAN_KEYS = (
    "schema", "revision", "adapter_revision", "project_file", "max_assets",
    "capture", "files", "policy", "candidate_changes", "kind_by_path",
)
_REVIEWED_FILES = (
    ("widget_preview_bridge.py", "796cfb6c1cc80a805573ffc7efdc3d78b1bba3129c5ba24245ff227543cea0d7"),
    ("repair_selected_textures.py", "57b9be33b267b87ff250ca9ee42ef1153209aed7c47dd43ae9f19a5d9fb555a2"),
    ("texture_audit/repair.py", "6b8e906872bdc08e5ffafd3fe7e0a48d7cd1e69e0175232d6f663e5432aa5de3"),
    ("texture_audit/paths.py", "89c2dcacf00788e16ce089d5139cffba1052b1f8f0e58580071ad5febbdc0a9a"),
)
_ERROR_TEXT = {
    "PURPOSE_REQUIRED": "预览未执行：请先明确本批次用途（color / normal / mask）。",
    "INVALID_PURPOSE": "用途无效：只接受 color、normal 或 mask；同一批次须使用同一用途。",
    "INVALID_BATCH_LIMIT": "预览未执行：本阶段批量上限必须是 1 至 16 的整数。",
    "SOURCE_CHANGED": "脚本版本与已审核版本不一致：请停止并重新检查代码。",
    "PREVIEW_UNAVAILABLE": "本地预览入口或返回接口不符合要求，请检查代码版本。",
    "PREVIEW_FAILED": "预览未完成：请核对编辑器、选择和本地脚本状态。",
    "PLAN_REQUIRED": "修复未执行：请先在当前面板重新生成预览。",
    "INVALID_PLAN": "修复未执行：预览计划未通过校验，请重新生成预览。",
    "SELECTION_OR_PURPOSE_CHANGED": "修复未执行：当前选择或用途与预览不符，请重新预览。",
    "PLAN_STALE": "修复未执行：资产属性、文件或选择已变化，请重新预览。",
    "NO_CANDIDATES": "修复未执行：当前计划没有可执行的 sRGB 修改。",
    "IDS_REQUIRED": "修复未执行：没有明确提供待确认的候选 ID；不会自动执行全部建议。",
    "INVALID_IDS": "修复未执行：候选 ID 必须是计划中的 1 至 16 个唯一 sRGB 候选。",
    "DIR_REQUIRED": "修复未执行：请输入明确的新修复记录目录。",
    "INVALID_DIR": "修复未执行：请输入新的修复目录名（字母、数字、连字符或下划线）。",
    "DIR_EXISTS": "修复未执行：修复记录目录已存在，请选择新目录；已有文件保持不变。",
    "NO_DISK_SPACE": "修复未执行：项目所在磁盘可用空间不足以保存备份及运行记录。",
    "DIR_CHECK_FAILED": "修复未执行：无法确认修复目录或磁盘状态，请检查路径与权限。",
    "UNSAVED_CONTENT": "修复未执行：所选资产有未保存改动，请先单独核对并保存，再重新预览。",
    "STATE_CHECK_FAILED": "修复未执行：无法完整确认当前编辑器或资产状态。",
    "CONFIRMATION_UNAVAILABLE": "修复未执行：UE 原生确认对话框接口不可用；不会绕过确认。",
    "DIALOG_TOO_LONG": "修复未执行：完整确认内容过长，请减少本次候选数量。",
    "CONFIRMATION_FAILED": "修复未执行：无法取得明确的用户确认。",
    "CANCELLED": "已取消本次修复；资产未由本次操作修改。再次执行前请重新预览。",
    "CORE_REFUSED": "修复未执行：后端拒绝当前状态，请重新核对并预览。",
    "UNEXPECTED_PRECHECK": "修复未执行：检查发生异常，请核对本地状态后重新预览。",
}


class _BridgeStop(RuntimeError):
    """Fixed user-facing refusal code; never interpret arbitrary exception text."""


def _stop(code):
    raise _BridgeStop(code)


def _response(action, ok, status_text, *, error_code=None, plan_json="",
              candidate_ids_json="", plan_id="", candidate_count=0,
              modified_assets=0, write_attempted=False, approval_received=False,
              run_dir=""):
    return {
        "bridge_revision": BRIDGE_REVISION,
        "action": action,
        "ok": ok,
        "error_code": error_code,
        "status_text": status_text,
        "plan_json": plan_json,
        "candidate_ids_json": candidate_ids_json,
        "plan_id": plan_id,
        "candidate_count": candidate_count,
        "modified_assets": modified_assets,
        "write_attempted": write_attempted,
        "approval_received": approval_received,
        "invalidate_plan": action != "preview" or not ok,
        "run_dir": run_dir,
    }


def _refusal(action, code, *, plan_id="", run_dir="", approved=False):
    return _response(
        action, False, _ERROR_TEXT[code], error_code=code,
        plan_id=plan_id, run_dir=run_dir, approval_received=approved,
    )


def _safe_log(ue, text):
    """A logging error must not change a verified repair outcome."""
    try:
        logger = getattr(ue, "log", None)
        if callable(logger):
            logger(text)
    except Exception:
        pass


def _purpose(value):
    if not isinstance(value, str):
        _stop("INVALID_PURPOSE")
    kind = value.strip().lower()
    if kind in ("", "unknown"):
        _stop("PURPOSE_REQUIRED")
    if kind not in _KINDS:
        _stop("INVALID_PURPOSE")
    return kind


def _digest(value):
    data = json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_sources():
    try:
        for name, expected in _REVIEWED_FILES:
            path = _ROOT / name
            if not path.is_file() or not 0 < path.stat().st_size <= 1024 * 1024:
                _stop("SOURCE_CHANGED")
            if _file_hash(path) != expected:
                _stop("SOURCE_CHANGED")
    except _BridgeStop:
        raise
    except Exception as exc:
        raise _BridgeStop("SOURCE_CHANGED") from exc


def _load_preview():
    _verify_sources()
    try:
        module = runpy.run_path(str(_PREVIEW_FILE),
                                run_name="_texture_audit_widget_read_only_preview")
        required = ("preview_selected_status", "_editor_context",
                    "_declared_selection", "_validate_plan")
        if (module.get("BRIDGE_REVISION") != _PREVIEW_REVISION
                or Path(module.get("_ROOT", "")).resolve() != _ROOT
                or any(not callable(module.get(name)) for name in required)):
            _stop("PREVIEW_UNAVAILABLE")
        return module
    except _BridgeStop:
        raise
    except Exception as exc:
        raise _BridgeStop("PREVIEW_UNAVAILABLE") from exc


def _load_entry():
    _verify_sources()
    try:
        entry = runpy.run_path(str(_ENTRY_FILE),
                               run_name="_texture_audit_widget_explicit_repair")
        if (entry.get("_ADAPTER_REVISION") != _ADAPTER_REVISION
                or entry.get("_REPAIR_REVISION") != _REPAIR_REVISION
                or Path(entry.get("_ROOT", "")).resolve() != _ROOT
                or any(not callable(entry.get(name))
                       for name in ("preview", "apply", "_module"))):
            _stop("SOURCE_CHANGED")
        core = entry["_module"]()
        if (core.REPAIR_REVISION != _REPAIR_REVISION
                or Path(core.__file__).resolve() != (_ROOT / "texture_audit/repair.py").resolve()
                or core.SCOPE != _SCOPE or core.PROTECTED != _PROTECTED):
            _stop("SOURCE_CHANGED")
        return entry, core
    except _BridgeStop:
        raise
    except Exception as exc:
        raise _BridgeStop("SOURCE_CHANGED") from exc


def _decode_json(text, max_bytes, missing_code, invalid_code):
    if not isinstance(text, str) or not text.strip():
        _stop(missing_code)
    try:
        if len(text.encode("utf-8")) > max_bytes:
            _stop(invalid_code)

        def invalid_constant(value):
            raise ValueError("Non-finite JSON is unsupported.")

        return json.loads(text, parse_constant=invalid_constant)
    except _BridgeStop:
        raise
    except Exception as exc:
        raise _BridgeStop(invalid_code) from exc


def _review_plan(preview, raw_plan, kind):
    if not isinstance(raw_plan, dict):
        _stop("INVALID_PLAN")
    limit = raw_plan.get("max_assets")
    if type(limit) is not int or not 1 <= limit <= 16:
        _stop("INVALID_PLAN")
    try:
        ue, project, version = preview["_editor_context"]()
        declarations = preview["_declared_selection"](ue, kind, limit)
    except Exception as exc:
        raise _BridgeStop("SELECTION_OR_PURPOSE_CHANGED") from exc
    try:
        plan, counts = preview["_validate_plan"](
            raw_plan, declarations, limit, project, version,
        )
    except Exception as exc:
        raise _BridgeStop("INVALID_PLAN") from exc
    return ue, plan, counts


def _requested_changes(plan, selected_ids_json):
    identifiers = _decode_json(
        selected_ids_json, _MAX_IDS_BYTES, "IDS_REQUIRED", "INVALID_IDS",
    )
    if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 16
            or any(not isinstance(item, str) for item in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        _stop("INVALID_IDS")
    candidates = {change["suggestion_id"]: change
                  for change in plan["candidate_changes"]}
    if not candidates:
        _stop("NO_CANDIDATES")
    if any(identifier not in candidates for identifier in identifiers):
        _stop("INVALID_IDS")
    changes = [candidates[identifier] for identifier in identifiers]
    for change in changes:
        if (change["asset_path"] == _PROTECTED
                or not change["asset_path"].startswith(_SCOPE)
                or change["property_name"] != "srgb"
                or type(change["before"]) is not bool
                or type(change["proposed_after"]) is not bool
                or change["before"] == change["proposed_after"]
                or change["applied"] is not False
                or change["requires_user_confirmation"] is not True):
            _stop("INVALID_IDS")
    return identifiers, changes


def _directory_preflight(value, plan, changes):
    if not isinstance(value, str) or not value.strip():
        _stop("DIR_REQUIRED")
    try:
        path = new_output_directory(value, "RepairRuns")
    except FileExistsError as exc:
        raise _BridgeStop("DIR_EXISTS") from exc
    except ValueError as exc:
        raise _BridgeStop("INVALID_DIR") from exc
    except (OSError, RuntimeError) as exc:
        raise _BridgeStop("DIR_CHECK_FAILED") from exc
    try:
        ancestor = path.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        if not ancestor.is_dir():
            _stop("INVALID_DIR")
        targets = {change["asset_path"] for change in changes}
        backup_bytes = sum(plan["files"][identifier]["bytes"] for identifier in targets)
        if shutil.disk_usage(ancestor).free < backup_bytes + 1024 * 1024:
            _stop("NO_DISK_SPACE")
        return path, backup_bytes
    except _BridgeStop:
        raise
    except Exception as exc:
        raise _BridgeStop("DIR_CHECK_FAILED") from exc


def _state_preflight(preview, entry, core, original, kind):
    ue, plan, counts = _review_plan(preview, original, kind)
    try:
        fresh = entry["preview"](
            kind_by_path=plan["kind_by_path"], max_assets=plan["max_assets"],
        )
        _, fresh, _ = _review_plan(preview, fresh, kind)
        if any(_digest(fresh[key]) != _digest(plan[key]) for key in _STABLE_PLAN_KEYS):
            _stop("PLAN_STALE")
        dirty = core._dirty_names(ue)
        if any(identifier.rpartition(".")[0] in dirty for identifier in plan["files"]):
            _stop("UNSAVED_CONTENT")
    except _BridgeStop:
        raise
    except Exception as exc:
        raise _BridgeStop("STATE_CHECK_FAILED") from exc
    return ue, plan, counts


def _confirmation_message(plan, changes, counts, run_dir, backup_bytes, kind):
    lines = [
        "请核对本次修复范围。选择“是”将执行以下全部已列出的修改；默认选择“否”。",
        "",
        "工程：" + plan["project_file"],
        "本批次用途：" + _KINDS[kind] + " (" + kind + ")，由用户明确声明。",
        "本次待确认 sRGB 修改：" + str(len(changes)) + " 项。",
        "当前计划问题共 " + str(counts["issues_total"]) + " 条；本次仅处理以下 sRGB 修改。",
        "",
    ]
    for index, change in enumerate(changes, 1):
        lines.extend([
            str(index) + ". " + change["asset_path"],
            "   sRGB: " + str(change["before"]) + " -> " + str(change["proposed_after"]),
        ])
    lines.extend([
        "",
        "计划编号：" + plan["plan_id"],
        "新备份及记录目录：" + str(run_dir),
        "修复前资产备份合计：" + str(backup_bytes) + " 字节；另有运行记录及 UE 缓存开销。",
        "",
        "程序先校验全部备份，再修改并保存已列出的资产。",
        "不修改原始 T_Stone_BC；不修改压缩、尺寸、名称或像素。",
        "如发生中断，可能已有部分修改；保留记录，不自动重试或回滚。",
        "本次不自动重新加载资产包，保存后的重新加载验证需另行执行。",
        "",
        "是否确认执行以上 " + str(len(changes)) + " 项修改并创建上述备份和记录？",
    ])
    message = "\n".join(lines)
    if len(message) > _MAX_DIALOG_CHARS:
        _stop("DIALOG_TOO_LONG")
    return message


def _native_confirmation(ue, message):
    dialog = getattr(ue, "EditorDialog", None)
    show_message = getattr(dialog, "show_message", None)
    types = getattr(ue, "AppMsgType", None)
    replies = getattr(ue, "AppReturnType", None)
    message_type = getattr(types, "YES_NO", None)
    yes = getattr(replies, "YES", None)
    no = getattr(replies, "NO", None)
    if (not callable(show_message) or message_type is None
            or yes is None or no is None or yes == no):
        _stop("CONFIRMATION_UNAVAILABLE")
    try:
        # Official UE API: title, message, message_type, default_value.
        # Availability and runtime compatibility still require live UE testing.
        answer = show_message("确认纹理 sRGB 修复", message, message_type, no)
    except Exception as exc:
        raise _BridgeStop("CONFIRMATION_FAILED") from exc
    if isinstance(answer, bool):
        _stop("CONFIRMATION_FAILED")
    # No/Cancel/closed dialogs never grant permission. Do not test truthiness.
    return answer == yes


def _read_record(path):
    if not path.is_file() or not 0 < path.stat().st_size <= _MAX_PLAN_BYTES:
        raise ValueError("Repair record is missing or outside the size bound.")
    return _decode_json(path.read_text(encoding="utf-8"), _MAX_PLAN_BYTES,
                        "INVALID_PLAN", "INVALID_PLAN")


def _verify_completed_result(result, plan, identifiers, changes, run_dir):
    """Read-only checks after apply; failure must be reported as unverified."""
    if (not isinstance(result, dict)
            or result.get("schema") != "texture_srgb_repair_result/0.1"
            or result.get("revision") != _REPAIR_REVISION
            or result.get("status") != "completed"
            or result.get("plan_id") != plan["plan_id"]
            or result.get("applied_ids") != identifiers
            or type(result.get("modified_assets")) is not int
            or result["modified_assets"] != len(changes)):
        raise ValueError("Unexpected repair result contract.")
    # asdict retains tuple sequences; JSON records use lists.
    # Freeze every field once for record comparison and later checks.
    encoded_result = json.dumps(
        result, ensure_ascii=True, allow_nan=False, separators=(",", ":"),
    )
    if len(encoded_result.encode("utf-8")) > _MAX_PLAN_BYTES:
        raise ValueError("Repair result exceeds the record size bound.")
    result = json.loads(encoded_result)
    if _read_record(run_dir / "result.json") != result:
        raise ValueError("Result record does not match the returned result.")
    if _read_record(run_dir / "plan.json") != plan:
        raise ValueError("Saved plan differs from the approved plan.")
    approval = _read_record(run_dir / "approval.json")
    if approval.get("confirmed") is not True or approval.get("selected_ids") != identifiers:
        raise ValueError("Saved approval differs from the approved range.")
    manifest = _read_record(run_dir / "backup_manifest.json")
    backups = result.get("backups")
    if not isinstance(backups, list) or len(backups) != len(changes):
        raise ValueError("Unexpected backup count.")
    if manifest.get("backups") != backups:
        raise ValueError("Backup manifest does not match the result.")

    content = Path(plan["project_file"]).parent / "Content"
    expected_paths = [change["asset_path"] for change in changes]
    for index, backup in enumerate(backups, 1):
        identifier = expected_paths[index - 1]
        before = plan["files"][identifier]
        source = (content / before["relative_content_file"]).resolve()
        source.relative_to(content.resolve())
        expected_name = str(index).zfill(3) + "_" + source.name
        if (backup.get("asset_path") != identifier
                or backup.get("backup_file") != expected_name
                or backup.get("sha256") != before["sha256"]
                or backup.get("bytes") != before["bytes"]):
            raise ValueError("Unexpected backup entry.")
        backup_path = run_dir / "backups" / expected_name
        if (not backup_path.is_file()
                or backup_path.stat().st_size != before["bytes"]
                or _file_hash(backup_path) != before["sha256"]):
            raise ValueError("Actual backup does not match the reviewed fingerprint.")

    journal = run_dir / "events.jsonl"
    if not journal.is_file() or not 0 < journal.stat().st_size <= _MAX_PLAN_BYTES:
        raise ValueError("Repair journal is unavailable.")
    events = [_decode_json(line, _MAX_PLAN_BYTES, "INVALID_PLAN", "INVALID_PLAN")
              for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    expected_events = ["run_prepared", "all_backups_verified"]
    for _ in changes:
        expected_events.extend(["mutation_requested", "save_requested", "saved"])
    expected_events.append("completed")
    if [event.get("event") for event in events] != expected_events:
        raise ValueError("Unexpected repair journal ordering.")
    if (events[0].get("plan_id") != plan["plan_id"]
            or events[1].get("count") != len(changes)
            or events[-1].get("modified_assets") != len(changes)):
        raise ValueError("Repair journal summary does not match approval.")
    for index, change in enumerate(changes):
        mutation, requested, saved = events[2 + index * 3:5 + index * 3]
        if (mutation.get("suggestion") != change
                or requested.get("asset_path") != change["asset_path"]
                or saved.get("asset_path") != change["asset_path"]):
            raise ValueError("Repair journal target differs from approval.")
        source = content / plan["files"][change["asset_path"]]["relative_content_file"]
        if not source.is_file() or _file_hash(source) != saved.get("after_sha256"):
            raise ValueError("Saved asset file differs from its journal fingerprint.")

    before_reads = plan["capture"]["textures"]
    after_capture = result.get("after_capture")
    if (not isinstance(after_capture, dict)
            or after_capture.get("engine_version") != plan["capture"]["engine_version"]
            or after_capture.get("project_name") != plan["capture"]["project_name"]
            or after_capture.get("selected_objects") != plan["capture"]["selected_objects"]
            or after_capture.get("skipped_objects") != []):
        raise ValueError("Unexpected post-repair capture context.")
    expected = {read["snapshot"]["asset_path"]: dict(read["snapshot"]) for read in before_reads}
    for change in changes:
        expected[change["asset_path"]]["srgb"] = change["proposed_after"]
    after_reads = after_capture.get("textures")
    if not isinstance(after_reads, list) or len(after_reads) != len(before_reads):
        raise ValueError("Unexpected post-repair capture count.")
    actual = {read["snapshot"]["asset_path"]: read["snapshot"] for read in after_reads}
    if actual != expected:
        raise ValueError("Unexpected post-repair attribute change.")
    audits = result.get("after_audits")
    if (not isinstance(audits, list) or len(audits) != len(expected)
            or {audit.get("asset_path") for audit in audits} != set(expected)):
        raise ValueError("Unexpected post-repair audit range.")
    remaining_ids = {
        suggestion["suggestion_id"]
        for audit in audits for suggestion in audit["suggestions"]
    }
    if set(identifiers) & remaining_ids:
        raise ValueError("An approved sRGB suggestion remains after repair.")
    return sum(len(audit["issues"]) for audit in audits)


def preview_selected_status(texture_kind="unknown", max_assets=16):
    """Read-only preview; return serialized review data for Widget variables."""
    try:
        kind = _purpose(texture_kind)
        if type(max_assets) is not int or not 1 <= max_assets <= 16:
            _stop("INVALID_BATCH_LIMIT")
        preview = _load_preview()
        result = preview["preview_selected_status"](texture_kind=kind, max_assets=max_assets)
        if not isinstance(result, dict):
            _stop("PREVIEW_UNAVAILABLE")
        if result.get("ok") is not True:
            code = result.get("error_code")
            if (isinstance(code, str) and code in preview.get("_ERROR_TEXT", {})
                    and isinstance(result.get("status_text"), str)):
                return _response("preview", False, result["status_text"], error_code=code)
            _stop("PREVIEW_FAILED")
        ue, plan, counts = _review_plan(preview, result.get("plan"), kind)
        plan_json = json.dumps(plan, ensure_ascii=True, allow_nan=False,
                               sort_keys=True, separators=(",", ":"))
        if len(plan_json.encode("utf-8")) > _MAX_PLAN_BYTES:
            _stop("INVALID_PLAN")
        identifiers = [change["suggestion_id"] for change in plan["candidate_changes"]]
        identifiers_json = json.dumps(identifiers, ensure_ascii=True, separators=(",", ":"))
        if len(identifiers_json.encode("utf-8")) > _MAX_IDS_BYTES:
            _stop("INVALID_IDS")
        status = preview["_status_text"](plan, counts, kind)
        status += "\n确认修复前仍须核对候选范围、当前用途和新备份目录，并在 UE 对话框明确选择“是”。"
        _safe_log(ue, "TA_WIDGET_REPAIR_PREVIEW_READY: candidates={}; modified_assets=0".format(
            len(identifiers),
        ))
        return _response(
            "preview", True, status, plan_json=plan_json,
            candidate_ids_json=identifiers_json, plan_id=plan["plan_id"],
            candidate_count=len(identifiers),
        )
    except _BridgeStop as exc:
        return _refusal("preview", str(exc))
    except Exception:
        return _refusal("preview", "PREVIEW_FAILED")


def confirm_and_apply_status(plan_json, texture_kind, run_dir, selected_ids_json):
    """Explicit native confirmation, then apply the requested reviewed IDs only.

    There is intentionally no confirmed=True argument exposed to the Widget.
    No file is written by this bridge before a native dialog returns YES.
    The existing core independently rechecks state and verifies all backups
    before any property mutation. A returned failure always consumes the plan.
    Callers must clear stored Widget plan/ID strings from this function's result.
    """
    ue = None
    core = None
    plan_id = ""
    output = ""
    approved = False
    apply_invoked = False
    try:
        kind = _purpose(texture_kind)
        raw_plan = _decode_json(plan_json, _MAX_PLAN_BYTES, "PLAN_REQUIRED", "INVALID_PLAN")
        preview = _load_preview()
        ue, plan, counts = _review_plan(preview, raw_plan, kind)
        plan_id = plan["plan_id"]
        identifiers, changes = _requested_changes(plan, selected_ids_json)
        entry, core = _load_entry()
        ue, plan, counts = _state_preflight(preview, entry, core, plan, kind)
        directory, backup_bytes = _directory_preflight(run_dir, plan, changes)
        output = str(directory)
        message = _confirmation_message(plan, changes, counts, directory, backup_bytes, kind)
        if not _native_confirmation(ue, message):
            return _refusal("apply", "CANCELLED", plan_id=plan_id, run_dir=output)
        approved = True

        # Do not use a new plan or new target set after the user approves.
        # Recheck the EXACT reviewed plan, explicit IDs, directory and sources.
        _verify_sources()
        ue, plan, counts = _state_preflight(preview, entry, core, plan, kind)
        identifiers_again, changes_again = _requested_changes(plan, selected_ids_json)
        if identifiers_again != identifiers or changes_again != changes:
            _stop("PLAN_STALE")
        directory_again, _ = _directory_preflight(run_dir, plan, changes)
        if directory_again != directory:
            _stop("INVALID_DIR")
        apply_invoked = True
        result = entry["apply"](plan, identifiers, str(directory), confirmed=True)
        remaining_issues = _verify_completed_result(
            result, plan, identifiers, changes, directory,
        )
        status = (
            "修复及保存后核对完成：sRGB 修改 {} 项；备份 {} 份。\n"
            "当前规则复查仍需复核的问题 {} 条；其他属性未由本次修复处理。\n"
            "已核对备份、保存文件及运行记录。尚未执行资产包重新加载验证。\n"
            "运行目录：{}\n计划编号：{}\n"
            "本次预览已消费；后续操作须重新预览。"
        ).format(len(changes), len(result["backups"]), remaining_issues, output, plan_id)
        _safe_log(ue, "TA_WIDGET_REPAIR_COMPLETED: modified_assets={}; backups={}".format(
            result["modified_assets"], len(result["backups"]),
        ))
        return _response(
            "apply", True, status, plan_id=plan_id, candidate_count=len(changes),
            modified_assets=result["modified_assets"], write_attempted=True,
            approval_received=True, run_dir=output,
        )
    except Exception as exc:
        # The core wraps EVERY error after run creation as RepairExecutionError.
        # Only its exact pre-write refusal class may be reported as zero writes.
        core_refusal = (
            core is not None
            and isinstance(exc, getattr(core, "RepairRefused", ()))
        )
        if apply_invoked and not core_refusal:
            _safe_log(ue, "TA_WIDGET_REPAIR_STOPPED_UNVERIFIED: exception_type=" + type(exc).__name__)
            status = (
                "修复已停止，最终结果尚未验证，可能已有部分资产或文件修改。\n"
                "请保留备份及运行记录，不要重复点击修复或覆盖恢复文件。\n"
                "运行目录：{}\n计划编号：{}\n本次预览已失效。"
            ).format(output or "<未确认>", plan_id or "<未确认>")
            return _response(
                "apply", False, status, error_code="PARTIAL_OR_UNVERIFIED",
                plan_id=plan_id, modified_assets=None, write_attempted=True,
                approval_received=approved, run_dir=output,
            )
        if isinstance(exc, _BridgeStop):
            code = str(exc)
        elif core_refusal:
            code = "CORE_REFUSED"
        else:
            code = "UNEXPECTED_PRECHECK"
        if code not in _ERROR_TEXT:
            code = "UNEXPECTED_PRECHECK"
        _safe_log(ue, "TA_WIDGET_REPAIR_REFUSED: code=" + code)
        return _refusal("apply", code, plan_id=plan_id, run_dir=output, approved=approved)


# No automatic __main__ entry, global retained plan, callback or UE execution.
