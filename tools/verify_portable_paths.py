"""Verify the release output boundary without launching Unreal."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "Content" / "Python"))
from texture_audit import paths
import widget_repair_bridge

assert paths.PROJECT_FILE == root / "TextureAuditTool.uproject"
widget_repair_bridge._verify_sources()
(root / "Saved").mkdir(exist_ok=True)

with tempfile.TemporaryDirectory(dir=root / "Saved") as temporary:
    project = Path(temporary).resolve()
    with patch.object(paths, "PROJECT_ROOT", project):
        report = paths.new_output_directory(" demo-report ", "Reports")
        repair = paths.new_output_directory("demo-repair", "RepairRuns")
        assert report == project / "Saved" / "TextureAudit" / "Reports" / "demo-report"
        assert repair == project / "Saved" / "TextureAudit" / "RepairRuns" / "demo-repair"
        assert paths.new_output_directory(str(repair), "RepairRuns") == repair
        for invalid in ("../escape", r"\temp", "C:relative", "CON", "space name", report):
            try:
                paths.new_output_directory(invalid, "RepairRuns")
            except ValueError:
                pass
            else:
                raise AssertionError(f"Accepted disallowed output {invalid!r}")
        report.mkdir(parents=True)
        try:
            paths.new_output_directory("demo-report", "Reports")
        except FileExistsError:
            pass
        else:
            raise AssertionError("Accepted a pre-existing report directory")

print("Release path boundaries and reviewed source fingerprints passed.")
