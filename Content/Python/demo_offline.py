"""Synthetic snapshot demonstration; no Unreal import and no real asset changes.

Run explicitly only after approving an offline validation step.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from texture_audit.rules import AssetSnapshot, AuditPolicy, audit_assets
from texture_audit.report import build_report, write_reports


def synthetic_assets():
    # Purposes are explicitly declared for these fabricated test records.
    # Do not infer real material purpose solely from a filename.
    rows = [
        dict(name="T_Stone_BC", kind="color", width=1024, height=1024,
             srgb=True, compression="default"),
        dict(name="T_Stone_N", kind="normal", width=1024, height=1024,
             srgb=True, compression="default"),
        dict(name="T_Stone_M", kind="mask", width=3000, height=1500,
             srgb=True, compression="default"),
        dict(name="Stone_BC", kind="color", width=1024, height=1024,
             srgb=True, compression="default"),
        dict(name="T_Detail_BC", kind="normal", width=512, height=512,
             srgb=False, compression="normalmap"),
        dict(name="T_Unclassified", kind="unknown", width=1024, height=1024,
             srgb=True, compression="default"),
        dict(name="T_Unreadable_N", kind="normal", width=None, height=None,
             srgb=None, compression=None),
        dict(name="T_Good_N", kind="normal", width=512, height=512,
             srgb=False, compression="normalmap"),
    ]
    return tuple(
        AssetSnapshot.from_mapping(
            dict(row, asset_path="/Game/TextureAuditDemo/" + row["name"])
        )
        for row in rows
    )


def main():
    parser = argparse.ArgumentParser(
        description="Audit eight fabricated texture records. Does not connect to UE."
    )
    parser.add_argument(
        "--output-dir", type=Path,
        help="New report directory. Defaults to a unique reports/ subdirectory under the project Saved directory."
    )
    args = parser.parse_args()
    policy = AuditPolicy()
    results = audit_assets(synthetic_assets(), policy)
    report = build_report(results, policy, "fabricated_texture_snapshots_v1", synthetic=True)
    name = datetime.now(timezone.utc).strftime("offline_%Y%m%dT%H%M%SZ_") + uuid4().hex[:8]
    output = args.output_dir if args.output_dir is not None else (
        Path(__file__).resolve().parents[2] / "Saved" / "TextureAudit" / "Offline" / name
    )
    paths = write_reports(report, output)
    print(json.dumps({
        "notice": "Synthetic records only; no UE asset was read or modified.",
        "summary": report["summary"],
        "reports": paths,
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
