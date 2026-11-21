"""Project-local output paths shared by the editor and repair core."""
from pathlib import Path, PureWindowsPath
import re

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_FILE = PROJECT_ROOT / "TextureAuditTool.uproject"


def new_output_directory(value, category):
    """Resolve a run name (or exact child path) without allowing overwrite."""
    if not isinstance(value, (str, Path)) or category not in {"Reports", "RepairRuns"}:
        raise ValueError("Provide a new run name.")
    name = str(value).strip()
    root = PROJECT_ROOT / "Saved" / "TextureAudit" / category
    candidate = Path(name)
    if candidate.is_absolute():
        if candidate.parent != root:
            raise ValueError("The directory must be an immediate child of the output root.")
        name = candidate.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("Run names must have 1 to 64 ASCII letters, digits, hyphens or underscores.")
    if PureWindowsPath(name).is_reserved():
        raise ValueError("Reserved Windows names are not supported.")
    output = root / name
    if output.resolve() != output:
        raise ValueError("Output paths cannot redirect to another location.")
    if output.exists() or output.is_symlink():
        raise FileExistsError("The run directory already exists.")
    return output
