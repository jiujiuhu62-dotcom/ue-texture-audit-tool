"""Explicit sRGB-only pilot repair. Importing this module performs no I/O.

build_plan is read-only. apply_plan requires a reviewed plan, explicit change
IDs, confirmed=True and a new project-local run directory. No automatic rollback,
retry, deletion, compression edit, rename, pixel edit or save-all is provided.
Actual UE execution and behavioral validation require separate approval.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from .paths import PROJECT_FILE, new_output_directory
from .rules import AuditPolicy, audit_assets
from . import ue_adapter

REPAIR_REVISION = "0.1.0-srgb-pilot"
PLAN_SCHEMA = "texture_srgb_repair_plan/0.1"
ADAPTER_REVISION = "0.2.0-source-dimensions"
SCOPE = "/Game/TextureAuditDemo/"
PROTECTED = "/Game/TextureAuditDemo/T_Stone_BC.T_Stone_BC"
MAX_FILE_BYTES = 256 * 1024 * 1024


class RepairRefused(RuntimeError):
    """Preconditions failed; do not retry by bypassing the checks."""


class RepairExecutionError(RuntimeError):
    """A run stopped; backups and journal may describe partial changes."""


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _context(unreal_module=None):
    if ue_adapter.ADAPTER_REVISION != ADAPTER_REVISION:
        raise RepairRefused("Unexpected adapter revision; reload and review modules first.")
    ue = ue_adapter._editor_context(unreal_module)[0]
    filename = ue.Paths.convert_relative_path_to_full(ue.Paths.get_project_file_path())
    project = Path(filename).resolve()
    if not project.is_file() or project != PROJECT_FILE:
        raise RepairRefused("Open the TextureAuditTool project that contains these scripts.")
    return ue, project


def _asset_file(project, identifier):
    if not ue_adapter._project_asset_path(identifier) or not identifier.startswith(SCOPE):
        raise RepairRefused("Pilot repairs only support /Game/TextureAuditDemo/ textures.")
    package, separator, name = identifier.rpartition(".")
    if not separator or package.rsplit("/", 1)[-1] != name:
        raise RepairRefused("An exact top-level asset object identifier is required.")
    content = project.parent / "Content"
    if content.resolve() != content:
        raise RepairRefused("The project Content folder must not redirect to another location.")
    path = (content / (package[len("/Game/"):] + ".uasset")).resolve()
    try:
        path.relative_to(content)
    except ValueError as exc:
        raise RepairRefused("Resolved asset file escaped the project Content directory.") from exc
    if not path.is_file():
        raise RepairRefused("A saved asset file is required.")
    for suffix in (".uexp", ".ubulk", ".uptnl", ".upayload"):
        if path.with_suffix(suffix).exists():
            raise RepairRefused("Sidecar payloads are not supported by the pilot backup routine.")
    if not 0 < path.stat().st_size <= MAX_FILE_BYTES:
        raise RepairRefused("Pilot asset files must be nonempty and at most 256 MiB.")
    return path


def build_plan(kind_by_path, policy=None, max_assets=16, unreal_module=None):
    """Read selection, current policy suggestions and saved-file fingerprints.

    This never creates a run directory or modifies an asset. Unknown purposes
    generate no changes. Only /Game/TextureAuditDemo/ is supported in this pilot.
    """
    if type(max_assets) is not int or not 1 <= max_assets <= 16:
        raise ValueError("Pilot batch size must be between 1 and 16.")
    ue, project = _context(unreal_module)
    policy = AuditPolicy() if policy is None else policy
    if not isinstance(policy, AuditPolicy):
        raise ValueError("policy must be an AuditPolicy.")
    capture = ue_adapter.collect_selected_textures(kind_by_path, max_assets, ue)
    if capture.skipped_objects:
        raise RepairRefused("Selection contains out-of-scope objects; select only pilot textures.")
    results = audit_assets(capture.snapshots, policy)
    files = {}
    for snapshot in capture.snapshots:
        path = _asset_file(project, snapshot.asset_path)
        files[snapshot.asset_path] = {
            "relative_content_file": path.relative_to(project.parent / "Content").as_posix(),
            "bytes": path.stat().st_size, "sha256": _file_hash(path),
        }
    candidates = [asdict(change) for result in results for change in result.suggestions
                  if change.property_name == "srgb" and change.asset_path != PROTECTED]
    plan = {
        "schema": PLAN_SCHEMA, "revision": REPAIR_REVISION,
        "adapter_revision": ADAPTER_REVISION, "created_at_utc": _utc(),
        "status": "preview_only", "project_file": str(project),
        "kind_by_path": {item.asset_path: item.kind for item in capture.snapshots},
        "max_assets": max_assets, "policy": asdict(policy),
        "capture": capture.to_dict(), "files": files,
        "audits": [result.to_dict() for result in results],
        "candidate_changes": candidates, "modified_assets": 0,
        "limits": ["Explicit sRGB suggestions only", "Protected original baseline",
                   "Saved single-file assets only", "No automatic rollback or retry"],
    }
    plan["plan_id"] = _digest(plan)
    return plan


def _new_run_directory(value, project):
    if project != PROJECT_FILE:
        raise RepairRefused("Repair output must be inside the open project's Saved directory.")
    try:
        return new_output_directory(value, "RepairRuns")
    except (ValueError, OSError, RuntimeError) as exc:
        raise RepairRefused("Use a new run name under Saved/TextureAudit/RepairRuns.") from exc


def _json_file(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        import os
        os.fsync(handle.fileno())


def _event(path, name, **details):
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(time_utc=_utc(), event=name, **details),
                                ensure_ascii=True, allow_nan=False) + "\n")
        handle.flush()
        import os
        os.fsync(handle.fileno())


def _dirty_names(ue):
    # Failure to query dirty state must stop the run, not assume clean assets.
    return {package.get_name() for package in
            ue.EditorLoadingAndSavingUtils.get_dirty_content_packages()}


def apply_plan(plan, selected_ids, run_dir, *, confirmed=False, unreal_module=None):
    """Apply explicit sRGB IDs only after a separate user approval.

    All backups must verify before any property edit. Any later failure stops
    the batch and retains backups/journal; memory or disk may be partly changed.
    Never retry blindly, delete assets, or restore files over an open editor.
    """
    if confirmed is not True:
        raise RepairRefused("Explicit confirmed=True is required after reviewing the plan.")
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise RepairRefused("A supported preview plan is required.")
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    if plan.get("plan_id") != _digest(body) or plan.get("revision") != REPAIR_REVISION:
        raise RepairRefused("Plan changed or belongs to another repair revision.")
    if (not isinstance(selected_ids, (list, tuple)) or not selected_ids
            or any(not isinstance(item, str) for item in selected_ids)
            or len(selected_ids) != len(set(selected_ids)) or len(selected_ids) > 16):
        raise ValueError("Supply 1-16 unique suggestion IDs explicitly.")
    ue, project = _context(unreal_module)
    if str(project) != plan.get("project_file"):
        raise RepairRefused("The current project differs from the preview project.")
    fresh = build_plan(plan["kind_by_path"], AuditPolicy(**plan["policy"]),
                       plan["max_assets"], ue)
    for key in ("capture", "files", "policy", "candidate_changes", "kind_by_path"):
        if _digest(fresh[key]) != _digest(plan[key]):
            raise RepairRefused("Selection, saved files or live values changed; create a new preview.")
    candidates = {item["suggestion_id"]: item for item in fresh["candidate_changes"]}
    if any(identifier not in candidates for identifier in selected_ids):
        raise RepairRefused("Selected ID is not a current eligible suggestion.")
    changes = [candidates[identifier] for identifier in selected_ids]
    targets = {}
    dirty = _dirty_names(ue)
    if any(item.rpartition(".")[0] in dirty for item in fresh["files"]):
        raise RepairRefused("Selected content has unsaved edits; review and save separately first.")
    subsystem = ue.get_editor_subsystem(ue.EditorAssetSubsystem)
    for change in changes:
        identifier = change["asset_path"]
        if (identifier == PROTECTED or change["property_name"] != "srgb"
                or type(change["before"]) is not bool or type(change["proposed_after"]) is not bool
                or change["before"] == change["proposed_after"]
                or change["requires_user_confirmation"] is not True or change["applied"] is not False):
            raise RepairRefused("Unsupported property, protected baseline or invalid suggestion.")
        asset = ue.load_asset(identifier)
        if (not isinstance(asset, ue.Texture2D) or asset.get_path_name() != identifier
                or asset.get_class().get_name() != "Texture2D"
                or asset.get_editor_property("srgb") is not change["before"]):
            raise RepairRefused("Live asset identity/value no longer matches the preview.")
        targets[identifier] = asset
    output = _new_run_directory(run_dir, project)
    ancestor = output.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    required = sum(fresh["files"][identifier]["bytes"] for identifier in targets) + 1024 * 1024
    if shutil.disk_usage(ancestor).free < required:
        raise RepairRefused("Insufficient space for verified backups and records.")

    attempted, saved, backups = [], [], []
    run_created = False
    stage = "create_records"
    journal = output / "events.jsonl"
    try:
        output.mkdir(parents=True, exist_ok=False)
        run_created = True
        _json_file(output / "plan.json", plan)
        _json_file(output / "approval.json", {"selected_ids": list(selected_ids),
                                             "confirmed": True, "time_utc": _utc()})
        with journal.open("x", encoding="utf-8"):
            pass
        _event(journal, "run_prepared", plan_id=plan["plan_id"])
        backup_dir = output / "backups"
        backup_dir.mkdir(exist_ok=False)
        stage = "backup"
        for index, identifier in enumerate(targets, 1):
            source = _asset_file(project, identifier)
            destination = backup_dir / (str(index).zfill(3) + "_" + source.name)
            with source.open("rb") as src, destination.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                import os
                os.fsync(dst.fileno())
            expected = fresh["files"][identifier]
            if (_file_hash(destination) != expected["sha256"]
                    or _file_hash(source) != expected["sha256"]
                    or destination.stat().st_size != expected["bytes"]):
                raise RepairRefused("Backup did not match the preview file; no changes permitted.")
            backups.append({"asset_path": identifier, "backup_file": destination.name,
                            "sha256": expected["sha256"], "bytes": expected["bytes"]})
        _json_file(output / "backup_manifest.json", {"backups": backups})
        _event(journal, "all_backups_verified", count=len(backups))
        stage = "apply"
        for change in changes:
            identifier = change["asset_path"]
            asset = targets[identifier]
            source = _asset_file(project, identifier)
            if (identifier.rpartition(".")[0] in _dirty_names(ue)
                    or _file_hash(source) != fresh["files"][identifier]["sha256"]
                    or asset.get_editor_property("srgb") is not change["before"]):
                raise RepairRefused("Target changed after backup; stop the run.")
            _event(journal, "mutation_requested", suggestion=change)
            attempted.append(change["suggestion_id"])
            asset.set_editor_property("srgb", change["proposed_after"])
            if asset.get_editor_property("srgb") is not change["proposed_after"]:
                raise RepairRefused("Property readback did not match the requested value.")
            stage = "save"
            _event(journal, "save_requested", asset_path=identifier)
            if not subsystem.save_loaded_asset(asset, only_if_is_dirty=False):
                raise RepairRefused("UE reported an asset save failure.")
            if not source.is_file() or identifier.rpartition(".")[0] in _dirty_names(ue):
                raise RepairRefused("Saved file is missing or the package remains dirty.")
            saved.append(change["suggestion_id"])
            _event(journal, "saved", asset_path=identifier, after_sha256=_file_hash(source))
            stage = "apply"
        stage = "recheck"
        after = ue_adapter.collect_selected_textures(plan["kind_by_path"], plan["max_assets"], ue)
        expected_snapshots = {item["snapshot"]["asset_path"]: dict(item["snapshot"])
                              for item in fresh["capture"]["textures"]}
        for change in changes:
            expected_snapshots[change["asset_path"]]["srgb"] = change["proposed_after"]
        actual_snapshots = {item.asset_path: asdict(item) for item in after.snapshots}
        if actual_snapshots != expected_snapshots:
            raise RepairRefused("Post-repair snapshot differs beyond the selected sRGB edits.")
        for identifier in set(fresh["files"]) - set(targets):
            if _file_hash(_asset_file(project, identifier)) != fresh["files"][identifier]["sha256"]:
                raise RepairRefused("An unmodified selected asset's saved file changed.")
        results = audit_assets(after.snapshots, AuditPolicy(**plan["policy"]))
        remaining = {s.suggestion_id for result in results for s in result.suggestions}
        if set(selected_ids) & remaining:
            raise RepairRefused("A selected suggestion remains after repair.")
        result = {
            "schema": "texture_srgb_repair_result/0.1", "revision": REPAIR_REVISION,
            "status": "completed", "completed_at_utc": _utc(), "plan_id": plan["plan_id"],
            "applied_ids": saved, "modified_assets": len(targets),
            "backups": backups, "after_capture": after.to_dict(),
            "after_audits": [item.to_dict() for item in results],
            "limits": ["Live property and UE save-result checks; cold reload verification is separate",
                       "Not an automatic rollback facility"],
        }
        _json_file(output / "result.json", result)
        _event(journal, "completed", modified_assets=len(targets))
        return result
    except BaseException as exc:
        failure = {"status": "stopped_partial_or_unverified", "stage": stage,
                   "exception_type": type(exc).__name__, "attempted_ids": attempted,
                   "saved_ids": saved, "backups": backups, "automatic_retry": False,
                   "automatic_rollback": False}
        if run_created and output.is_dir():
            try:
                _json_file(output / "failure.json", failure)
            except Exception:
                pass
        raise RepairExecutionError(
            "Repair stopped at " + stage + ". Memory/disk may be partly changed. "
            "Keep records at " + str(output) + "; do not retry or restore over an open editor."
        ) from exc
