"""`sed report build`: snapshot -> spec -> artifacts (XLSX, Markdown, PPTX) recorded in report_artifact."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sed import db
from sed.errors import NotImplementedByWorkstream, ValidationFailed
from sed.paths import Paths
from sed.reports.md_builder import build_md
from sed.reports.snapshot import Snapshot, create_snapshot, render_view
from sed.reports.specs import load_report_spec
from sed.reports.xlsx_builder import build_xlsx

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
    formats: list[str] | None,
    ai_mode: str = "approved",
    vendor_id: str | None = None,
    *,
    template_map: str | None = None,
) -> dict[str, Any]:
    """Build artifacts for one report period. `formats=None` builds every format the report declares."""
    from sed.modules import report

    _, rdef = report(report_key)
    wanted = formats if formats else list(rdef.formats)
    unknown = [f for f in wanted if f not in rdef.formats]
    if unknown:
        raise ValidationFailed(f"Unsupported format(s) {unknown} for {report_key}; available: {list(rdef.formats)}")
    if ai_mode not in AI_MODES:
        raise ValidationFailed(f"--ai must be one of {sorted(AI_MODES)}")
    spec = load_report_spec(report_key, paths)
    conn = db.connect(paths.db)
    try:
        snapshot = create_snapshot(conn, paths, report_key, period, vendor_id)
        view = render_view(snapshot, ai_mode)
        run_ids = [run["run_id"] for run in view.ai_runs]
        out_dir = paths.out / snapshot.period
        generated = db.utc_now()
        artifacts = []
        for fmt in wanted:
            target = out_dir / artifact_name(snapshot, fmt, ai_mode)
            template_info: dict[str, str] | None = None
            if fmt == "xlsx":
                build_xlsx(snapshot, spec, target, ai_mode=ai_mode, generated_at=generated)
            elif fmt == "md":
                build_md(snapshot, spec, target, ai_mode=ai_mode)
            else:
                raise NotImplementedByWorkstream("ws2-pptx")
            sha = _sha(target)
            artifact_id = f"art-{snapshot.snapshot_id}-{fmt}-{sha[:10]}"
            with db.write_tx(conn):
                conn.execute(
                    "INSERT OR REPLACE INTO report_artifact (artifact_id, snapshot_id, format, path, sha256, "
                    "template_map_sha, ai_mode, ai_run_ids_json, unapproved_omitted_json, built_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)",
                    (
                        artifact_id,
                        snapshot.snapshot_id,
                        fmt,
                        str(target),
                        sha,
                        template_info["sha256"] if template_info else None,
                        ai_mode,
                        json.dumps(run_ids),
                        generated,
                    ),
                )
            artifacts.append({"format": fmt, "path": str(target), "sha256": sha, "template_map": template_info})
    finally:
        conn.close()
    return {
        "report": report_key,
        "period": snapshot.period,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_sha256": snapshot.sha256,
        "data_class": snapshot.data_class,
        "ai_mode": ai_mode,
        "ai_runs": run_ids,
        "readiness": {
            "ai_sections_required": 0,
            "ai_sections_approved": 0,
            "note": "AI narrative sections arrive in M5; deterministic content only.",
        },
        "artifacts": artifacts,
    }
