"""UE 5.3 selected-texture audit entry. Python 3.9+; standard library only.

Run only after a separate execution approval. preview() prints JSON to the UE
log without exporting files. run(output_dir=...) additionally exports reports
to a new run directory under Saved/TextureAudit/Reports. Neither repairs assets.

UE console, Python mode, after execution approval:
    import scan_selected_textures
    result = scan_selected_textures.preview()

Importing or runpy-loading this file does not scan assets. No unreal pip package
is required or supported. Do not run it with PyCharm's standalone interpreter.
"""
import importlib
import json
from pathlib import Path
import sys

from texture_audit.paths import new_output_directory


_SOURCE_ROOT = Path(__file__).resolve().parent
_EXPECTED_ADAPTER_REVISION = "0.2.0-source-dimensions"


def _components():
    """Use this prototype's modules without a persistent sys.path change."""
    root = str(_SOURCE_ROOT)
    added = root not in sys.path
    if added:
        sys.path.insert(0, root)
    try:
        modules = tuple(importlib.import_module(name) for name in (
            "texture_audit.rules", "texture_audit.report", "texture_audit.ue_adapter",
        ))
        for module in modules:
            module_file = Path(module.__file__).resolve()
            expected = _SOURCE_ROOT / "texture_audit" / (module.__name__.rsplit(".", 1)[-1] + ".py")
            if module_file != expected.resolve():
                raise RuntimeError("Another texture_audit package is loaded; stop and review module paths.")
        if getattr(modules[2], "ADAPTER_REVISION", None) != _EXPECTED_ADAPTER_REVISION:
            raise RuntimeError(
                "An earlier UE adapter is cached. Reload texture_audit.ue_adapter "
                "with importlib.reload before retrying; no asset scan was performed."
            )
        return modules
    finally:
        if added:
            sys.path.remove(root)


def _new_report_directory(output_dir):
    return new_output_directory(output_dir, "Reports")


def _capture_report(kind_by_path=None, policy=None, max_assets=256):
    rules, exporter, adapter = _components()
    chosen_policy = rules.AuditPolicy() if policy is None else policy
    if not isinstance(chosen_policy, rules.AuditPolicy):
        raise ValueError("policy must be an AuditPolicy from this prototype.")
    capture = adapter.collect_selected_textures(kind_by_path, max_assets=max_assets)
    results = rules.audit_assets(capture.snapshots, chosen_policy)
    report = exporter.build_report(
        results, chosen_policy,
        "UE Content Browser selection; live property reads; material purpose requires explicit declaration",
        synthetic=False,
    )
    # Keep the original exporter schema, but correct its offline provenance.
    # A completed capture is not independent validation of adapter correctness.
    report.update({
        "mode": "ue_selected_texture_audit",
        "ue_adapter_verified": False,
        "adapter_validation": "pending_independent_editor_property_review",
        "adapter_revision": adapter.ADAPTER_REVISION,
        "asset_modification_supported": False,
        "capture": capture.to_dict(),
        "limitations": [
            "Only currently selected exact Texture2D assets under /Game/ are captured.",
            "Source dimensions use the UE 5.3 Texture2D Dimensions tag; missing or invalid values remain unknown.",
            "The source-size read method has one live-asset check; broader asset coverage remains pending.",
            "Unknown purposes do not produce sRGB or compression-change suggestions.",
            "Suggestions are review data. No property writes, renames, resizes or saves are implemented.",
            "Loading selected assets may cause normal editor cache and log writes.",
        ],
    })
    return report, exporter


def _log_result(payload):
    ue = importlib.import_module("unreal")
    ue.log("TEXTURE_AUDIT_READ_ONLY " + json.dumps(payload, ensure_ascii=True))


def preview(kind_by_path=None, policy=None, max_assets=256):
    """Capture the current selection; return and log results, with no export.

    kind_by_path maps exact /Game/Folder/T_Name.T_Name object identifiers to
    human-confirmed color/normal/mask/unknown purposes. Omitted purposes remain
    unknown. No purpose is inferred from an asset's name or current properties.
    """
    report, _ = _capture_report(kind_by_path, policy, max_assets)
    _log_result(report)
    return report


def run(output_dir, kind_by_path=None, policy=None, max_assets=256):
    """Explicitly export a live read-only report to a new project-local directory.

    A filesystem error stops the export. Partial files are retained for review;
    no retry, overwrite, cleanup or alternate-drive fallback is performed.
    Approve this export step separately before calling it.
    """
    output = _new_report_directory(output_dir)
    report, exporter = _capture_report(kind_by_path, policy, max_assets)
    paths = exporter.write_reports(report, output)
    _log_result({"mode": report["mode"], "summary": report["summary"], "reports": paths})
    return {"report": report, "reports": paths}


if __name__ == "__main__":
    # Explicit direct execution previews only; no automatic filesystem export.
    preview()
