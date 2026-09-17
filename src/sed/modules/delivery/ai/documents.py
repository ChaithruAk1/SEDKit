"""Markdown bodies of the delivery drafts, and parsers for the exports.

A draft's `body_md` is what the reviewer reads, edits and approves, so the exports read the approved `body_md` back
rather than the structured draft: an edit always reaches the exported file. Stories and test plans use a fixed Markdown
shape (rendered here); the parsers accept any edit that keeps that shape and report the line of anything they cannot
read. ADRs and release notes export their Markdown as is.
"""

from __future__ import annotations

import re
from typing import Any

from sed.errors import ValidationFailed

STORY_HEADING = re.compile(r"^###\s+(\d+)\.\s+(.+?)\s*$")
USER_STORY = re.compile(
    r"^\*\*As an?\*\*\s+(?P<as_a>.+?),\s+\*\*I want\*\*\s+(?P<i_want>.+?),\s+\*\*so that\*\*\s+(?P<so_that>.+?)\.?\s*$"
)
FIELD = re.compile(r"^-\s+(?P<name>[A-Za-z ]+):\s*(?P<value>.*?)\s*$")
BULLET = re.compile(r"^[-*]\s+(.+?)\s*$")
STEP = re.compile(r"^\d+\.\s+(.+?)\s*$")
CASE_HEADING = re.compile(r"^###\s+TC-(\d+)\s+(.+?)\s*$")
EXPECTED = re.compile(r"^\*\*Expected:\*\*\s*(.+?)\s*$")
ESTIMATE = re.compile(r"^(\d+)\s+points?$")


def _article(role: str) -> str:
    return "As an" if role[:1].lower() in "aeiou" else "As a"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


# -- stories -------------------------------------------------------------------------------------------------------


def render_stories(page_title: str, page_id: str, stories: list[dict[str, Any]], questions: list[str]) -> str:
    lines = [f"Source: {_one_line(page_title)} (page {page_id})"]
    for n, s in enumerate(stories, start=1):
        lines += [
            "",
            f"### {n}. {_one_line(s['title'])}",
            f"**{_article(s['as_a'])}** {_one_line(s['as_a'])}, **I want** {_one_line(s['i_want'])}, "
            f"**so that** {_one_line(s['so_that']).rstrip('.')}.",
            "",
            f"- Priority: {s.get('priority') or 'medium'}",
        ]
        if s.get("estimate_points") is not None:
            lines.append(f"- Estimate: {s['estimate_points']} points")
        if s.get("epic_key"):
            lines.append(f"- Epic: {s['epic_key']}")
        lines += ["", "**Acceptance criteria**"]
        lines += [f"- {_one_line(c)}" for c in s["acceptance_criteria"]]
    if not stories:
        lines += ["", "_No stories drafted for this page._"]
    if questions:
        lines += ["", "**Open questions**"]
        lines += [f"- {_one_line(q)}" for q in questions]
    return "\n".join(lines) + "\n"


def parse_stories(body_md: str, where: str) -> list[dict[str, Any]]:
    """Stories of an approved `delivery_stories` body (see render_stories). Raises ValidationFailed naming the line."""
    stories: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    section = ""

    def fail(n: int, msg: str) -> ValidationFailed:
        return ValidationFailed(f"{where}, line {n}: {msg}", {"finding": where, "line": n})

    def close(n: int) -> None:
        if current is None:
            return
        if not current.get("as_a"):
            raise fail(n, f"story '{current['title']}' has no '**As a** ..., **I want** ..., **so that** ...' line")
        if not current["acceptance_criteria"]:
            raise fail(n, f"story '{current['title']}' has no acceptance criteria")
        stories.append(current)

    for n, raw in enumerate(body_md.splitlines(), start=1):
        line = raw.strip()
        heading = STORY_HEADING.match(line)
        if heading:
            close(n)
            current = {"title": heading.group(2), "acceptance_criteria": [], "priority": "medium",
                       "estimate_points": None, "epic_key": None}  # fmt: skip
            section = "story"
            continue
        if line == "**Open questions**":
            close(n)
            current, section = None, "questions"
            continue
        if current is None or not line:
            continue
        story = USER_STORY.match(line)
        if story:
            current.update({k: v.strip() for k, v in story.groupdict().items()})
            continue
        if line == "**Acceptance criteria**":
            section = "criteria"
            continue
        field = FIELD.match(line)
        if section == "story" and field:
            name, value = field.group("name").strip().lower(), field.group("value")
            if name == "priority":
                current["priority"] = value.lower()
            elif name == "estimate":
                m = ESTIMATE.match(value)
                if not m:
                    raise fail(n, "estimate must read '<n> points'")
                current["estimate_points"] = int(m.group(1))
            elif name == "epic":
                current["epic_key"] = value or None
            else:
                raise fail(n, f"unknown story field '{field.group('name')}'")
            continue
        bullet = BULLET.match(line)
        if section == "criteria" and bullet:
            current["acceptance_criteria"].append(bullet.group(1))
            continue
        if section == "criteria" and current["acceptance_criteria"]:
            current["acceptance_criteria"][-1] += " " + line  # a wrapped criterion
            continue
        raise fail(n, f"unexpected text in story '{current['title']}': {line[:60]}")
    close(len(body_md.splitlines()))
    return stories


# -- ADRs ----------------------------------------------------------------------------------------------------------


def render_adr(project_name: str, adr: dict[str, Any], pages: dict[str, str]) -> str:
    lines = [
        f"# {_one_line(adr['title'])}",
        "",
        "- Status: proposed",
        f"- Project: {_one_line(project_name)}",
        "",
        "## Context",
        "",
        adr["context"].strip(),
        "",
        "## Decision drivers",
        "",
        *[f"- {_one_line(d)}" for d in adr["drivers"]],
        "",
        "## Considered options",
    ]
    for option in adr["options"]:
        lines += ["", f"### {_one_line(option['name'])}"]
        lines += [f"- Pro: {_one_line(p)}" for p in option.get("pros") or []]
        lines += [f"- Con: {_one_line(c)}" for c in option.get("cons") or []]
    lines += [
        "",
        "## Decision",
        "",
        f"Chosen option: **{_one_line(adr['chosen_option'])}**. {adr['rationale'].strip()}",
        "",
        "## Consequences",
        "",
        *[f"- {_one_line(c)}" for c in adr["consequences"]],
    ]
    related = [f"- {pages[p]} (page {p})" for p in adr.get("related_page_ids") or [] if p in pages]
    if related:
        lines += ["", "## Related pages", "", *related]
    return "\n".join(lines) + "\n"


# -- test plans ----------------------------------------------------------------------------------------------------


def render_test_plan(page_title: str, cases: list[dict[str, Any]], not_covered: list[str]) -> str:
    lines = [f"Stories: {_one_line(page_title)}"]
    for n, case in enumerate(cases, start=1):
        lines += [
            "",
            f"### TC-{n:02d} {_one_line(case['title'])}",
            f"- Story: {_one_line(case['story'])}",
            f"- Type: {case['type']}",
        ]
        if case.get("preconditions"):
            lines.append("- Preconditions: " + "; ".join(_one_line(p) for p in case["preconditions"]))
        lines.append("")
        lines += [f"{i}. {_one_line(step)}" for i, step in enumerate(case["steps"], start=1)]
        lines += ["", f"**Expected:** {_one_line(case['expected'])}"]
    if not_covered:
        lines += ["", "**Not covered**"]
        lines += [f"- {_one_line(x)}" for x in not_covered]
    return "\n".join(lines) + "\n"


def parse_test_plan(body_md: str, where: str) -> list[dict[str, Any]]:
    """Test cases of an approved `delivery_test_plan` body (see render_test_plan)."""
    cases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def fail(n: int, msg: str) -> ValidationFailed:
        return ValidationFailed(f"{where}, line {n}: {msg}", {"finding": where, "line": n})

    def close(n: int) -> None:
        if current is None:
            return
        for key in ("story", "type", "expected"):
            if not current.get(key):
                raise fail(n, f"test case '{current['title']}' has no {key}")
        if not current["steps"]:
            raise fail(n, f"test case '{current['title']}' has no numbered steps")
        cases.append(current)

    for n, raw in enumerate(body_md.splitlines(), start=1):
        line = raw.strip()
        heading = CASE_HEADING.match(line)
        if heading:
            close(n)
            current = {"id": f"TC-{int(heading.group(1)):02d}", "title": heading.group(2), "story": "", "type": "",
                       "preconditions": [], "steps": [], "expected": ""}  # fmt: skip
            continue
        if line == "**Not covered**":
            close(n)
            current = None
            continue
        if current is None or not line:
            continue
        field = FIELD.match(line)
        if field:
            name, value = field.group("name").strip().lower(), field.group("value")
            if name in ("story", "type"):
                current[name] = value
            elif name == "preconditions":
                current["preconditions"] = [p.strip() for p in value.split(";") if p.strip()]
            else:
                raise fail(n, f"unknown test case field '{field.group('name')}'")
            continue
        step = STEP.match(line)
        if step:
            current["steps"].append(step.group(1))
            continue
        expected = EXPECTED.match(line)
        if expected:
            current["expected"] = expected.group(1)
            continue
        raise fail(n, f"unexpected text in test case '{current['title']}': {line[:60]}")
    close(len(body_md.splitlines()))
    return cases


# -- release notes -------------------------------------------------------------------------------------------------


def render_release_notes(notes: dict[str, Any]) -> str:
    lines = [f"# {_one_line(notes['title'])}", "", notes["summary_md"].strip()]
    for section in notes["sections"]:
        lines += ["", f"## {_one_line(section['heading'])}", ""]
        lines += [f"- {_one_line(e['text'])} ({', '.join(e['issue_keys'])})" for e in section["entries"]]
    if notes.get("known_issues"):
        lines += ["", "## Known issues", ""]
        lines += [f"- {_one_line(x)}" for x in notes["known_issues"]]
    return "\n".join(lines) + "\n"
