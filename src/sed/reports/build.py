"""`sed report build`: snapshot -> spec -> artifacts (XLSX, Markdown; PPTX in M2) recorded in report_artifact."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sed import db
from sed.errors import ValidationFailed
from sed.paths import Paths
from sed.reports.md_builder import build_md
from sed.reports.snapshot import Snapshot, create_snapshot
from sed.reports.specs import load_report_spec
from sed.reports.xlsx_builder import build_xlsx

SUPPORTED_FORMATS = {"xlsx", "md"}
AI_MODES = {"approved", "none", "draft"}


def artifact_name(snapshot: Snapshot, ext: str, ai_mode: str) -> str:
    parts = [snapshot.report_key, snapshot.period]
    if snapshot.vendor_id:
        parts.append(snapshot.vendor_id)
    if snapshot.data_class == "synthetic":
        parts.append("SYNTHETIC")
    if ai_mode == "draft":
        parts.append("DRAFT")
    return "_".join(parts) + f".{ext}"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report(
    paths: Paths,
    report_key: str,
    period: str,
    formats: list[str],
    ai_mode: str = "approved",
    vendor_id: str | None = None,
) -> dict[str, Any]:
    unknown = set(formats) - SUPPORTED_FORMATS
    if unknown:
        raise ValidationFailed(f"Unsupported format(s) {sorted(unknown)}; available now: {sorted(SUPPORTED_FORMATS)}")
    if ai_mode not in AI_MODES:
        raise ValidationFailed(f"--ai must be one of {sorted(AI_MODES)}")
    spec = load_report_spec(report_key, paths)
    conn = db.connect(paths.db)
    try:
        snapshot = create_snapshot(conn, paths, report_key, period, vendor_id)
        out_dir = paths.out / snapshot.period
        generated = db.utc_now()
        artifacts = []
        for fmt in formats:
            target = out_dir / artifact_name(snapshot, fmt, ai_mode)
            if fmt == "xlsx":
                build_xlsx(snapshot, spec, target, ai_mode=ai_mode, generated_at=generated)
            else:
                build_md(snapshot, spec, target, ai_mode=ai_mode)
            sha = _sha(target)
            artifact_id = f"art-{snapshot.snapshot_id}-{fmt}-{sha[:10]}"
            with db.write_tx(conn):
                conn.execute(
                    "INSERT OR REPLACE INTO report_artifact (artifact_id, snapshot_id, format, path, sha256, "
                    "template_map_sha, "
                    "ai_mode, ai_run_ids_json, unapproved_omitted_json, built_at) VALUES (?, ?, ?, ?, ?, NULL, ?, "
                    "'[]', ?, ?)",
                    (
                        artifact_id,
                        snapshot.snapshot_id,
                        fmt,
                        str(target),
                        sha,
                        ai_mode,
                        json.dumps([]),
                        generated,
                    ),
                )
            artifacts.append({"format": fmt, "path": str(target), "sha256": sha})
    finally:
        conn.close()
    return {
        "report": report_key,
        "period": snapshot.period,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_sha256": snapshot.sha256,
        "data_class": snapshot.data_class,
        "ai_mode": ai_mode,
        "readiness": {
            "ai_sections_required": 0,
            "ai_sections_approved": 0,
            "note": "AI narrative sections arrive in M5; deterministic content only.",
        },
        "artifacts": artifacts,
    }
