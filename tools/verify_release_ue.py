"""UE commandlet smoke test: real assets, injected selection, no asset save."""
from pathlib import Path
from types import SimpleNamespace
import json

import unreal
import widget_scan_bridge
import widget_export_bridge
import widget_repair_bridge
from texture_audit.paths import PROJECT_FILE

version = unreal.SystemLibrary.get_engine_version()
assert version.startswith("5.3.2")
assert Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.get_project_file_path())).resolve() == PROJECT_FILE

bp_path = "/Game/EditorTools/EUW_TextureAuditTool"
blueprint = unreal.load_asset(bp_path)
generated_class = unreal.load_blueprint_class(bp_path)
assert blueprint is not None and generated_class is not None
blueprint_library = getattr(unreal, "BlueprintEditorLibrary", None)
if hasattr(blueprint_library, "compile_blueprint"):
    blueprint_library.compile_blueprint(blueprint)
    assert unreal.load_blueprint_class(bp_path) is not None

sample_dir = "/Game/TextureAuditDemo/"
names = (
    "T_Stone_BC", "T_Stone_BadSRGB_BC", "T_Stone_PreviewBadSRGB_BC",
    "T_Stone_UIBatchA_BC", "T_Stone_UIBatchB_BC",
    "T_Stone_UIRegression_20261121_BC", "T_Stone_UIRepairTest_BC",
)
assets = {name: unreal.load_asset(sample_dir + name) for name in names}
assert all(isinstance(asset, unreal.Texture2D) for asset in assets.values())
for asset in assets.values():
    assert unreal.EditorAssetLibrary.find_asset_data(asset.get_path_name()).is_valid()

# A commandlet has no Content Browser selection. Substitute its two selection
# getters only; registry reads, property queries and bridge logic remain live.
selection = []
native_selection = unreal.EditorUtilityLibrary
unreal.EditorUtilityLibrary = SimpleNamespace(
    get_selected_assets=lambda: list(selection),
    get_selected_asset_data=lambda: [unreal.EditorAssetLibrary.find_asset_data(obj.get_path_name()) for obj in selection],
)
try:
    selection[:] = [assets["T_Stone_BC"]]
    scan = widget_scan_bridge.scan_selected_status(texture_kind="color")
    assert scan["ok"], scan["error_code"]
    normal = widget_scan_bridge.scan_selected_status(texture_kind="normal")
    assert normal["ok"], normal["error_code"]
    protected = widget_repair_bridge.preview_selected_status("color")
    assert protected["ok"] and protected["candidate_count"] == 0, protected["error_code"]

    selection[:] = [assets[name] for name in ("T_Stone_UIBatchA_BC", "T_Stone_UIBatchB_BC")]
    from uuid import uuid4
    report_name = "smoke-" + uuid4().hex[:12]
    export = widget_export_bridge.export_selected_status(report_name, texture_kind="normal")
    assert export["ok"], export["error_code"]
    folder = PROJECT_FILE.parent / "Saved" / "TextureAudit" / "Reports" / report_name
    report = json.loads((folder / "audit_report.json").read_text(encoding="utf-8"))
    summary = report["summary"]
    assert (summary["assets_total"], summary["issues_total"],
            summary["proposed_property_changes"], summary["modified_assets"]) == (2, 6, 4, 0), summary
    repeated = widget_export_bridge.export_selected_status(report_name, texture_kind="normal")
    assert not repeated["ok"] and repeated["error_code"] == "OUTPUT_EXISTS", repeated["error_code"]

    selection[:] = [assets["T_Stone_BadSRGB_BC"]]
    assert selection[0].get_editor_property("srgb") is False
    preview = widget_repair_bridge.preview_selected_status("color")
    assert preview["ok"] and preview["candidate_count"] == 1, preview["error_code"]
    refused = widget_repair_bridge.confirm_and_apply_status(
        preview["plan_json"], "color", "smoke-repair", "[]")
    assert not refused["ok"] and not refused["write_attempted"], refused["error_code"]
finally:
    unreal.EditorUtilityLibrary = native_selection

unreal.log("TA_RELEASE_SMOKE_OK " + json.dumps({
    "engine": version, "editor_widget_loaded": True, "textures_loaded": len(assets),
    "batch_report": summary, "duplicate_report_refused": True,
    "protected_original_candidates": 0, "repair_sample_candidates": 1,
    "invalid_candidate_ids_refused": True, "repair_writes": 0,
    "selection": "injected real saved assets; Content Browser UI and native dialog not exercised",
}, ensure_ascii=True))
