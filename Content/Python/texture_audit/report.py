"""JSON/CSV reports only. This module never opens or modifies UE assets."""
import csv
import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .rules import AuditPolicy, AuditResult

SCHEMA_VERSION = "0.1.0"


def build_report(results: Iterable[AuditResult], policy: AuditPolicy,
                 source_label: str, synthetic: bool = True) -> dict:
    if not isinstance(source_label, str) or not source_label.strip():
        raise ValueError("source_label must describe the actual input provenance.")
    if type(synthetic) is not bool:
        raise ValueError("synthetic must be bool.")
    rows = [item.to_dict() for item in results]
    paths = [row["asset_path"] for row in rows]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate asset paths in report.")
    counts = Counter(issue["severity"] for row in rows for issue in row["issues"])
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "offline_snapshot_audit",
        "source_label": source_label.strip(),
        "synthetic_input": synthetic,
        "ue_adapter_verified": False,
        "asset_modification_supported": False,
        "policy": asdict(policy),
        "summary": {
            "assets_total": len(rows),
            "assets_needing_review": sum(bool(row["issues"]) for row in rows),
            "assets_compliant_with_selected_policy": sum(not row["issues"] for row in rows),
            "issues_total": sum(counts.values()),
            "issues_by_severity": dict(counts),
            "proposed_property_changes": sum(len(row["suggestions"]) for row in rows),
            "modified_assets": 0,
        },
        "assets": rows,
    }


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, bool):
        value = "true" if value else "false"
    else:
        value = str(value)
    # Asset names and descriptions may be untrusted when this is connected to UE.
    # Prevent spreadsheet programs from treating cells as formulas.
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        value = "'" + value
    return value


def _write_csv(path, columns, rows):
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in columns})


def write_reports(report: dict, output_dir) -> dict:
    """Create a NEW output directory; never overwrite an existing run.

    On a filesystem error, stop and leave partial output for diagnosis.
    The caller should choose another new directory for a retry.
    """
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported report schema.")
    if report.get("asset_modification_supported") is not False:
        raise ValueError("This exporter only accepts read-only audit reports.")
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    json_path = output / "audit_report.json"
    with json_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    issue_rows = []
    suggestion_rows = []
    for asset in report["assets"]:
        base = {
            "asset_path": asset["asset_path"],
            "asset_name": asset["asset_name"],
            "kind": asset["kind"],
        }
        for issue in asset["issues"]:
            issue_rows.append(dict(base, **issue))
        for suggestion in asset["suggestions"]:
            suggestion_rows.append(dict(base, **suggestion))

    issues_path = output / "issues.csv"
    suggestions_path = output / "suggestions.csv"
    _write_csv(
        issues_path,
        ("asset_path", "asset_name", "kind", "code", "severity",
         "property_name", "actual", "expected", "message", "suggestion_id"),
        issue_rows,
    )
    _write_csv(
        suggestions_path,
        ("suggestion_id", "asset_path", "asset_name", "kind", "property_name",
         "before", "proposed_after", "reason", "requires_user_confirmation", "applied"),
        suggestion_rows,
    )
    return {
        "json": str(json_path),
        "issues_csv": str(issues_path),
        "suggestions_csv": str(suggestions_path),
    }
