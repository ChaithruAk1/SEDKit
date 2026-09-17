"""`sed delivery export`: approved delivery drafts as files a person uploads or pastes. SED never writes to Jira,
Confluence or a test tool.

* `stories`: a Jira CSV import file (Summary, Issue Type, Description, Priority, Story Points, Parent, Labels).
* `adr`: one Markdown file per ADR, ready to paste into Confluence.
* `test-plan`: one Markdown file with every plan and a CSV of the test cases.
* `release-notes`: one Markdown file per approved release, with the fact tokens filled.

Only published drafts are exported (approved, or update_pending showing the approved text), read from the approved
`body_md`, so reviewer edits reach the file. Files go to `DATA_DIR\\out\\delivery\\<project>\\`, with `_SYNTHETIC`
in the name on synthetic profiles. CSV cells that a spreadsheet would read as a formula are prefixed with a quote.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from sed import db
from sed.errors import PreconditionFailed, ValidationFailed
from sed.modules.delivery.ai import common as C
from sed.modules.delivery.ai.documents import parse_stories, parse_test_plan
from sed.paths import Paths
from sed.reports.sections import fill_tokens

KINDS = {
    "stories": "delivery_stories",
    "adr": "delivery_adr",
    "test-plan": "delivery_test_plan",
    "release-notes": "delivery_release_notes",
}
JIRA_PRIORITY = {"highest": "Highest", "high": "High", "medium": "Medium", "low": "Low", "lowest": "Lowest"}
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def _csv(header: list[str], rows: list[list[Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows([[_cell(v) for v in row] for row in rows])
    return buf.getvalue()


def _provenance(row: Any) -> str:
    who = row["reviewed_by"] or "unknown reviewer"
    return f"SED draft {row['finding_id']}, run {row['run_id']}, approved by {who} on {(row['reviewed_at'] or '')[:10]}"


def _write(path: Path, text: str, *, bom: bool = False) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8", newline="\n")
    return str(path)


def export(paths: Paths, what: str, project_id: str, *, period: str | None = None) -> dict[str, Any]:
    kind = KINDS.get(what)
    if kind is None:
        raise ValidationFailed(f"Unknown export '{what}'", {"exports": sorted(KINDS)})
    conn = db.connect(paths.db, readonly=True)
    try:
        project = conn.execute("SELECT * FROM delivery_project WHERE project_id = ?", (project_id,)).fetchone()
        if project is None:
            raise PreconditionFailed(f"Unknown delivery project '{project_id}'")
        rows = C.published(conn, kind, project_id)
    finally:
        conn.close()
    if period:
        rows = [r for r in rows if r["period"] == period]
    if not rows:
        raise PreconditionFailed(
            f"No approved {what} drafts for {project_id}" + (f" in {period}" if period else "")
            + "; draft them with the sed-draft-* skill and approve them in #/review first"
        )  # fmt: skip
    suffix = "_SYNTHETIC" if paths.data_class == "synthetic" else ""
    out = paths.out / "delivery" / project_id
    files: list[str] = []
    if what == "stories":
        records = []
        for row in rows:
            source = C.payload_of(row).get("page_title") or row["title"]
            for s in parse_stories(row["body_md"] or "", row["finding_id"]):
                criteria = "\n".join(f"* {c}" for c in s["acceptance_criteria"])
                description = (
                    f"As a {s['as_a']}, I want {s['i_want']}, so that {s['so_that']}.\n\n"
                    f"h4. Acceptance criteria\n{criteria}\n\nSource: {source} ({_provenance(row)})"
                )
                records.append(
                    [s["title"], "Story", description, JIRA_PRIORITY.get(s["priority"], "Medium"),
                     s["estimate_points"] if s["estimate_points"] is not None else "", s["epic_key"] or "", "sed-draft"]
                )  # fmt: skip
        header = ["Summary", "Issue Type", "Description", "Priority", "Story Points", "Parent", "Labels"]
        name = f"stories_{project_id}_jira_import{suffix}.csv"
        files.append(_write(out / name, _csv(header, records), bom=True))
        count = len(records)
    elif what == "adr":
        for row in rows:
            slug = row["stable_key"].rsplit(":", 1)[-1]
            text = (row["body_md"] or "").rstrip() + f"\n\n---\n_{_provenance(row)}._\n"
            files.append(_write(out / f"ADR-draft_{slug}{suffix}.md", text))
        count = len(rows)
    elif what == "test-plan":
        parts = [f"# Test plan – {project['name']}", ""]
        cases_rows = []
        for row in rows:
            page = C.payload_of(row).get("page_title") or row["title"]
            parts += [f"## {page}", "", (row["body_md"] or "").strip(), "", f"_{_provenance(row)}._", ""]
            for case in parse_test_plan(row["body_md"] or "", row["finding_id"]):
                cases_rows.append(
                    [case["id"], case["title"], case["story"], case["type"], "; ".join(case["preconditions"]),
                     "\n".join(f"{i}. {s}" for i, s in enumerate(case["steps"], start=1)), case["expected"], page]
                )  # fmt: skip
        files.append(_write(out / f"test_plan_{project_id}{suffix}.md", "\n".join(parts)))
        header = ["ID", "Title", "Story", "Type", "Preconditions", "Steps", "Expected", "Requirements page"]
        files.append(_write(out / f"test_cases_{project_id}{suffix}.csv", _csv(header, cases_rows), bom=True))
        count = len(cases_rows)
    else:
        for row in rows:
            data = C.payload_of(row)
            facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
            text = fill_tokens(row["body_md"] or "", facts, "EUR").rstrip() + f"\n\n_{_provenance(row)}._\n"
            label = (data.get("scope") or row["period"] or "release").replace(":", "-")
            files.append(_write(out / f"release_notes_{project_id}_{label}{suffix}.md", text))
        count = len(rows)
    return {"export": what, "project_id": project_id, "drafts": len(rows), "items": count, "files": files}
