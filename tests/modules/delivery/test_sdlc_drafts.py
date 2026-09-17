"""Delivery drafting skills (M7 D2) on the delivery profile, driven by a fake agent: user stories from requirements
pages, ADRs, test plans from approved stories and release notes from resolved Jira issues. Covers packet contents,
ingest validation, review gates (a test plan needs its stories approved), carry-forward and the file exports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from sed.ai.contract import StartParams
from sed.ai.ingest import ingest_file
from sed.ai.review import review_findings
from sed.ai.runs import finish_run, start_run
from sed.errors import PreconditionFailed, ValidationFailed
from sed.modules.delivery.ai.documents import parse_stories, parse_test_plan, render_stories, render_test_plan
from sed.modules.delivery.ai.export import export
from tests.fake_agent.flow import query
from tests.fake_agent.triage import write_output


def _start(paths: Any, skill: str, **params: Any) -> tuple[Any, list[dict]]:
    plan = start_run(paths, skill, StartParams(**params))
    lines = []
    for batch in plan.inputs:
        lines += [json.loads(x) for x in Path(batch.packet).read_text(encoding="utf-8").splitlines() if x.strip()]
    return plan, lines


def _ingest_all(paths: Any, plan: Any, items_for) -> None:
    for batch in plan.inputs:
        lines = [json.loads(x) for x in Path(batch.packet).read_text(encoding="utf-8").splitlines() if x.strip()]
        write_output(batch.out, {"meta": {"model": "fake"}, "items": [items_for(line) for line in lines]})
        ingest_file(paths, plan.run_id, batch.out)


def _story(title: str, **extra: Any) -> dict[str, Any]:
    return {
        "title": title,
        "as_a": "accounts payable clerk",
        "i_want": f"to {title.lower()}",
        "so_that": "invoices post without manual work",
        "acceptance_criteria": [f"Given a supplier invoice, when I {title.lower()}, then the result is recorded"],
        "priority": "high",
        "estimate_points": 3,
        "epic_key": "EINV-1",
        **extra,
    }


def _story_set(line: dict) -> dict[str, Any]:
    page = line["title"].removeprefix("Requirements - ")
    return {"ref": line["ref"], "stories": [_story(f"Capture {page}"), _story(f"Review {page}", epic_key=None)],
            "open_questions": [], "confidence": 0.8}  # fmt: skip


def _ids(paths: Any, kind: str, run_id: str) -> list[str]:
    return [r[0] for r in query(paths, "SELECT finding_id FROM finding WHERE kind = ? AND run_id = ?", kind, run_id)]


def _approved_stories(paths: Any) -> list[str]:
    plan, _ = _start(paths, "sed-draft-stories", subject="PRJ-101")
    _ingest_all(paths, plan, _story_set)
    finish_run(paths, plan.run_id)
    ids = _ids(paths, "delivery_stories", plan.run_id)
    review_findings(paths, ids, "approve", "tester")
    return ids


def test_documents_round_trip():
    stories = [_story("Capture invoices"), _story("Validate tax identifier", estimate_points=None, epic_key=None)]
    body = render_stories("Requirements - intake", "1001", stories, ["Which formats?"])
    parsed = parse_stories(body, "f1")
    assert [{k: s[k] for k in parsed[0]} for s in stories] == parsed
    edited = body.replace("- Priority: high", "- Priority: low", 1) + ""
    assert parse_stories(edited, "f1")[0]["priority"] == "low"
    with pytest.raises(ValidationFailed) as exc:
        parse_stories(body.replace("**Acceptance criteria**", "Criteria"), "f1")
    assert "line" in exc.value.details

    cases = [{"title": "Happy path", "story": "Capture invoices", "type": "functional", "preconditions": ["a", "b"],
              "steps": ["Open intake", "Submit"], "expected": "Recorded"}]  # fmt: skip
    parsed_cases = parse_test_plan(render_test_plan("Requirements - intake", cases, ["Load"]), "f2")
    assert parsed_cases == [{"id": "TC-01", **cases[0]}]


def test_stories_packet_validation_review_and_jira_export(delivery_profile_rw):
    paths = delivery_profile_rw.paths
    with pytest.raises(ValidationFailed):
        start_run(paths, "sed-draft-stories", StartParams())
    with pytest.raises(PreconditionFailed):
        start_run(paths, "sed-draft-stories", StartParams(subject="PRJ-999"))

    plan, lines = _start(paths, "sed-draft-stories", subject="PRJ-101")
    assert len(lines) == 5 and all(line["type"] == "requirements_page" and line["body"] for line in lines)
    context = Path(plan.context[0]).read_text(encoding="utf-8")
    assert "EINV-1" in context and "Stories already in Jira" in context

    batch = plan.inputs[0]
    first = _story_set(lines[0])
    for change, fragment in [
        ({"stories": [_story("Capture", epic_key="PORT-1")]}, "is not an epic of this project"),
        ({"stories": [_story("Same"), _story("same")]}, "duplicate title"),
        ({"stories": [], "open_questions": []}, "empty stories need open_questions"),
    ]:
        items = [_story_set(line) for line in lines[: batch.items]]
        items[0] = {**first, **change}
        write_output(batch.out, {"meta": {"model": "fake"}, "items": items})
        with pytest.raises(ValidationFailed) as exc:
            ingest_file(paths, plan.run_id, batch.out)
        assert any(fragment in d["msg"] for d in exc.value.details), exc.value.details

    _ingest_all(paths, plan, _story_set)
    assert finish_run(paths, plan.run_id).counts["findings_new"] == 5
    rows = query(paths, "SELECT * FROM finding WHERE run_id = ? ORDER BY stable_key", plan.run_id)
    assert {r["kind"] for r in rows} == {"delivery_stories"} and {r["status"] for r in rows} == {"draft"}
    assert rows[0]["stable_key"].startswith("delivery_stories:PRJ-101:") and rows[0]["subject_id"] == "PRJ-101"
    with pytest.raises(PreconditionFailed):
        export(paths, "stories", "PRJ-101")  # nothing approved yet

    ids = [r["finding_id"] for r in rows]
    review_findings(paths, ids[1:], "approve", "tester")
    edited = rows[0]["body_md"].replace("### 1. Capture", "### 1. =Capture", 1)
    review_findings(paths, ids[:1], "edit", "tester", body_md=edited)
    result = export(paths, "stories", "PRJ-101")
    assert result["drafts"] == 5 and result["items"] == 10
    out = Path(result["files"][0])
    assert out.name == "stories_PRJ-101_jira_import_SYNTHETIC.csv"
    records = list(csv.DictReader(out.read_text(encoding="utf-8-sig").splitlines()))
    assert len(records) == 10 and {r["Issue Type"] for r in records} == {"Story"}
    assert any(r["Summary"].startswith("'=Capture") for r in records)  # formula-safe, and the edit reached the file
    assert {r["Parent"] for r in records} == {"EINV-1", ""} and all("Acceptance criteria" in r["Description"]
                                                                   for r in records)  # fmt: skip

    again, _ = _start(paths, "sed-draft-stories", subject="PRJ-101")
    _ingest_all(paths, again, _story_set)
    counts = finish_run(paths, again.run_id).counts
    assert counts["findings_unchanged_approved"] == 4 and counts["findings_carried_forward"] == 1


def test_test_plan_needs_approved_stories_and_covers_each_story(delivery_profile_rw):
    paths = delivery_profile_rw.paths
    empty = start_run(paths, "sed-draft-test-plan", StartParams(subject="PRJ-101"))
    assert empty.run_id is None and empty.plan["items"] == 0

    story_ids = _approved_stories(paths)
    plan, lines = _start(paths, "sed-draft-test-plan", subject="PRJ-101")
    assert len(lines) == 5 and all(len(line["stories"]) == 2 for line in lines)

    def plan_for(line: dict, *, skip_last: bool = False) -> dict[str, Any]:
        stories = line["stories"][:-1] if skip_last else line["stories"]
        cases = [{"title": f"Check {s['title']}", "story": s["title"], "type": "functional", "preconditions": [],
                  "steps": ["Open the intake queue", "Submit a test invoice"], "expected": "The invoice is recorded"}
                 for s in stories]  # fmt: skip
        return {"ref": line["ref"], "cases": cases, "not_covered": [], "confidence": 0.8}

    batch = plan.inputs[0]
    batch_lines = [json.loads(x) for x in Path(batch.packet).read_text(encoding="utf-8").splitlines()]
    write_output(batch.out, {"items": [plan_for(line, skip_last=True) for line in batch_lines]})
    with pytest.raises(ValidationFailed) as exc:
        ingest_file(paths, plan.run_id, batch.out)
    assert any("has no test case" in d["msg"] for d in exc.value.details)

    _ingest_all(paths, plan, plan_for)
    finish_run(paths, plan.run_id)
    plans = query(paths, "SELECT * FROM finding WHERE kind = 'delivery_test_plan' AND run_id = ?", plan.run_id)
    assert len(plans) == 5
    cited = {json.loads(p["payload_json"])["cited_finding_ids"][0]: p["finding_id"] for p in plans}
    assert set(cited) == set(story_ids)

    review_findings(paths, [story_ids[0]], "reject", "tester", note="page is out of scope")
    with pytest.raises(PreconditionFailed):
        review_findings(paths, [cited[story_ids[0]]], "approve", "tester")
    review_findings(paths, [cited[s] for s in story_ids[1:]], "approve", "tester")
    result = export(paths, "test-plan", "PRJ-101")
    assert result["drafts"] == 4 and result["items"] == 8
    cases = list(csv.DictReader(Path(result["files"][1]).read_text(encoding="utf-8-sig").splitlines()))
    assert len(cases) == 8 and cases[0]["ID"] == "TC-01" and "1. Open the intake queue" in cases[0]["Steps"]


def test_adr_validation_and_export(delivery_profile_rw):
    paths = delivery_profile_rw.paths
    plan, lines = _start(paths, "sed-draft-adr", subject="PRJ-101")
    assert len(lines) == 1 and len(lines[0]["requirements"]) == 5 and len(lines[0]["recorded_adrs"]) == 3
    page_id = lines[0]["requirements"][0]["page_id"]

    def adr(**change: Any) -> dict[str, Any]:
        return {
            "title": "Validate tax identifiers in the integration layer",
            "context": "Invoices arrive from several channels.",
            "drivers": ["One validation for every channel"],
            "options": [{"name": "Integration layer", "pros": ["Reuse"], "cons": ["Extra hop"]},
                        {"name": "ERP custom code", "pros": ["No new part"], "cons": ["Upgrades"]}],
            "chosen_option": "Integration layer",
            "rationale": "It meets the driver.",
            "consequences": ["The integration team owns the rules"],
            "related_page_ids": [page_id],
            **change,
        }  # fmt: skip

    out = plan.inputs[0].out
    for change, fragment in [
        ({"chosen_option": "Something else"}, "must be one of the option names"),
        ({"related_page_ids": ["999999"]}, "is not on the packet"),
        ({"title": "Use the platform integration layer"}, "already recorded"),
    ]:
        write_output(out, {"items": [{"ref": "T001", "adrs": [adr(**change)], "confidence": 0.7}]})
        with pytest.raises(ValidationFailed) as exc:
            ingest_file(paths, plan.run_id, out)
        assert any(fragment in d["msg"] for d in exc.value.details), exc.value.details

    write_output(out, {"items": [{"ref": "T001", "adrs": [adr()], "confidence": 0.7}]})
    ingest_file(paths, plan.run_id, out)
    finish_run(paths, plan.run_id)
    ids = _ids(paths, "delivery_adr", plan.run_id)
    assert len(ids) == 1
    review_findings(paths, ids, "approve", "tester")
    result = export(paths, "adr", "PRJ-101")
    text = Path(result["files"][0]).read_text(encoding="utf-8")
    assert text.startswith("# Validate tax identifiers in the integration layer")
    assert "Chosen option: **Integration layer**" in text and "## Related pages" in text and ids[0] in text


def test_release_notes_tokens_issue_keys_and_export(delivery_profile_rw):
    paths = delivery_profile_rw.paths
    plan, lines = _start(paths, "sed-draft-release-notes", subject="PRJ-104", scope="period:2026-08")
    line = lines[0]
    fact = "delivery.release.PRJ-104.stories_resolved"
    assert line["scope"] == "period:2026-08" and line["facts"][fact] == len(line["issues"]) > 0
    keys = [i["key"] for i in line["issues"]]

    def notes(**change: Any) -> dict[str, Any]:
        return {
            "ref": "T001", "title": "Self-service analytics – August", "confidence": 0.8,
            "summary_md": f"This release delivers {{{{f:{fact}}}}} stories.",
            "sections": [{"heading": "New features", "entries": [{"text": "Analysts can share reports.",
                                                                  "issue_keys": keys}]}],
            "known_issues": [], "facts_cited": [fact], **change,
        }  # fmt: skip

    out = plan.inputs[0].out
    for change, fragment in [
        ({"facts_cited": []}, "is not in facts_cited"),
        ({"facts_cited": [fact, "delivery.release.PRJ-104.nope"]}, "is not a packet fact"),
        ({"sections": [{"heading": "Fixes", "entries": [{"text": "x", "issue_keys": ["SELF-99999"]}]}]},
         "is not a resolved issue"),
    ]:  # fmt: skip
        write_output(out, {"items": [notes(**change)]})
        with pytest.raises(ValidationFailed) as exc:
            ingest_file(paths, plan.run_id, out)
        assert any(fragment in d["msg"] for d in exc.value.details), exc.value.details

    write_output(out, {"items": [notes()]})
    ingest_file(paths, plan.run_id, out)
    finish_run(paths, plan.run_id)
    ids = _ids(paths, "delivery_release_notes", plan.run_id)
    row = query(paths, "SELECT period, stable_key FROM finding WHERE finding_id = ?", ids[0])[0]
    assert row["period"] == "2026-08" and row["stable_key"] == "delivery_release_notes:PRJ-104:period:2026-08"
    review_findings(paths, ids, "approve", "tester")
    result = export(paths, "release-notes", "PRJ-104", period="2026-08")
    text = Path(result["files"][0]).read_text(encoding="utf-8")
    assert f"This release delivers {len(keys)} stories." in text and "{{" not in text
    assert Path(result["files"][0]).name == "release_notes_PRJ-104_period-2026-08_SYNTHETIC.md"
