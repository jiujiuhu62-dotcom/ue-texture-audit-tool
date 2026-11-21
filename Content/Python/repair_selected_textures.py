"""UE 5.3 sRGB pilot repair entry. No actions on import/runpy loading.

First obtain a preview plan; then obtain explicit approval for selected IDs,
the new project-local run folder, backups and asset writes before calling apply().
This entry never applies all suggestions automatically. Preview performs no
file writes. Apply retains plan, approval, verified backups, journal and result.
"""
import importlib
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent
_ADAPTER_REVISION = "0.2.0-source-dimensions"
_REPAIR_REVISION = "0.1.0-srgb-pilot"


def _module():
    root = str(_ROOT)
    added = root not in sys.path
    if added:
        sys.path.insert(0, root)
    try:
        modules = tuple(importlib.import_module(name) for name in (
            "texture_audit.rules", "texture_audit.ue_adapter", "texture_audit.repair",
        ))
        for module in modules:
            expected = _ROOT / "texture_audit" / (module.__name__.rsplit(".", 1)[-1] + ".py")
            if Path(module.__file__).resolve() != expected.resolve():
                raise RuntimeError("A different texture_audit package is loaded; stop and review.")
        if getattr(modules[1], "ADAPTER_REVISION", None) != _ADAPTER_REVISION:
            raise RuntimeError("An older adapter is cached; explicitly reload it before preview.")
        if getattr(modules[2], "REPAIR_REVISION", None) != _REPAIR_REVISION:
            raise RuntimeError("An older repair module is cached; explicitly reload it first.")
        return modules[2]
    finally:
        if added:
            sys.path.remove(root)


def preview(kind_by_path, policy=None, max_assets=16):
    """Read selection and backup fingerprints. Does not write files or assets."""
    plan = _module().build_plan(kind_by_path, policy, max_assets)
    ue = importlib.import_module("unreal")
    ue.log("TEXTURE_REPAIR_PREVIEW " + json.dumps(plan, ensure_ascii=True))
    return plan


def apply(plan, selected_ids, run_dir, *, confirmed=False):
    """Explicitly apply reviewed sRGB IDs; this WRITES backups and UE assets.

    Use only after the user confirms the exact targets, changes and output
    directory. No auto-confirmation, automatic restoration or retry is offered.
    """
    result = _module().apply_plan(plan, selected_ids, run_dir, confirmed=confirmed)
    ue = importlib.import_module("unreal")
    ue.log("TEXTURE_REPAIR_COMPLETED " + json.dumps({
        "status": result["status"], "plan_id": result["plan_id"],
        "applied_ids": result["applied_ids"], "modified_assets": result["modified_assets"],
        "run_dir": str(Path(run_dir).resolve()),
    }, ensure_ascii=True))
    return result
