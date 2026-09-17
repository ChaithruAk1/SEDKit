"""Synthetic delivery data: four fictional projects with their register, three weekly plan versions, a RAID log, Jira
epics and stories, and a Confluence space each (requirements, ADRs, status pages).

Planted patterns (ground_truth/delivery/patterns.json):
* DP1 double slip: PRJ-101's "UAT sign-off" milestone moves later in two consecutive plan versions (35 days past its
  baseline in the latest plan).
* DP2 overdue high risk: PRJ-103 keeps a high-severity risk open two weeks past its due date.
* DP3 scope growth: PRJ-102 adds about 45% more story points in the last four weeks.
* DP4 forecast late: at PRJ-102's recent velocity the remaining points finish well after its target go-live.
* Control: PRJ-104 is on plan (no slips, no overdue high items, stable scope, forecast before target), and PRJ-103
  closes a high issue on time.
"""

from __future__ import annotations

import json
import random
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sed.errors import PreconditionFailed
from sed.ingest import manifest as inbox_manifest
from sed.ingest.loader import file_sha256
from sed.modules.contract import SynthRequest
from sed.paths import Paths
from sed.synth import writers as W

GENERATOR_VERSION = 1
KEY = "delivery"
JIRA_FMT = "%d/%b/%y %I:%M %p"
PLAN_FMT = "%d/%m/%Y"
MANAGERS = ["Iris Okafor", "Tomasz Lindqvist", "Mei Hartmann", "Rafael Dumont"]
SPONSORS = ["Clara Weiss", "Jonas Petrov", "Amara Silva", "Henrik Moreau"]
DEVELOPERS = ["Lena Novak", "Samir Haddad", "Julia Brandt", "Oskar Ferreira", "Nadia Kowal", "Pieter Janssen"]


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    app: str
    jira: str
    phase: str
    rag: str
    start: date
    target: date
    budget: int


PROJECTS = [
    Project("PRJ-101", "Orion ERP e-invoicing rollout", "Orion ERP", "EINV", "Build", "Amber", date(2026, 3, 2),
            date(2026, 11, 30), 420_000),
    Project("PRJ-102", "Northstar CRM partner portal", "Northstar CRM", "PORT", "Build", "Green", date(2026, 4, 6),
            date(2026, 10, 15), 310_000),
    Project("PRJ-103", "Nimbus WMS voice picking", "Nimbus WMS", "VOICE", "Test", "Amber", date(2026, 2, 2),
            date(2026, 10, 30), 265_000),
    Project("PRJ-104", "Lumen BI self-service analytics", "Lumen BI", "SELF", "Build", "Green", date(2026, 5, 4),
            date(2027, 1, 29), 180_000),
]  # fmt: skip

MILESTONES = [
    ("M1", "Requirements signed off", 0.15),
    ("M2", "Design approved", 0.30),
    ("M3", "Build complete", 0.65),
    ("M4", "UAT sign-off", 0.85),
    ("M5", "Go-live", 1.00),
]
TASKS = [("T1", "Solution design", 0.30), ("T2", "Integration build", 0.65), ("T3", "Data migration", 0.80)]
FEATURES = {
    "EINV": ["supplier invoice intake", "tax validation", "e-archive", "exception handling", "payment matching"],
    "PORT": ["partner onboarding", "deal registration", "lead sharing", "partner dashboards", "co-marketing funds"],
    "VOICE": ["headset pairing", "pick confirmation", "exception prompts", "multilingual prompts", "shift reports"],
    "SELF": ["data catalogue", "governed datasets", "report sharing", "usage analytics", "certified dashboards"],
}


def _date_at(project: Project, fraction: float) -> date:
    return project.start + timedelta(days=round((project.target - project.start).days * fraction))


def plan_rows(project: Project, status_date: date, version: int) -> list[list[Any]]:
    rows = []
    for task_id, name, fraction in MILESTONES + TASKS:
        baseline = _date_at(project, fraction)
        finish = baseline
        if project.project_id == "PRJ-101" and task_id in ("M4", "M5"):
            finish = baseline + timedelta(days=[0, 14, 35][version])  # DP1: two replans push UAT and go-live
        if project.project_id == "PRJ-103" and task_id == "M5":
            finish = baseline + timedelta(days=5)  # small slip below the threshold
        done = finish <= status_date
        percent = (
            100
            if done
            else max(
                0, min(95, round(100 * (status_date - project.start).days / max(1, (finish - project.start).days)))
            )
        )
        rows.append(
            [
                project.project_id,
                task_id,
                status_date.isoformat(),
                name,
                "Yes" if task_id.startswith("M") else "No",
                project.start.strftime(PLAN_FMT),
                finish.strftime(PLAN_FMT),
                baseline.strftime(PLAN_FMT),
                finish.strftime(PLAN_FMT) if done else "",
                percent,
            ]
        )
    return rows


def raid_rows(as_of: date) -> list[list[Any]]:
    d = as_of
    rows = [
        ["R-101-01", "PRJ-101", "Risk", "Tax authority format change before go-live",
         "Late format change forces rework", "Iris Okafor", "High", "Open", d - timedelta(days=40),
         d + timedelta(days=20), ""],
        ["R-101-02", "PRJ-101", "Dependency", "Supplier master data cleansing", "Needs finance data stewards",
         "Clara Weiss", "Medium", "Open", d - timedelta(days=30), d + timedelta(days=10), ""],
        ["R-102-01", "PRJ-102", "Assumption", "Partners use single sign-on", "Confirmed with identity team",
         "Tomasz Lindqvist", "Low", "Closed", d - timedelta(days=90), d - timedelta(days=60), d - timedelta(days=62)],
        ["R-102-02", "PRJ-102", "Risk", "Portal load at campaign launch", "Performance test planned",
         "Tomasz Lindqvist", "Medium", "Open", d - timedelta(days=20), d + timedelta(days=30), ""],
        ["R-103-01", "PRJ-103", "Issue", "Headset firmware incompatibility", "Vendor patch delivered and verified",
         "Mei Hartmann", "High", "Closed", d - timedelta(days=45), d - timedelta(days=10), d - timedelta(days=12)],
        ["R-103-02", "PRJ-103", "Risk", "Warehouse Wi-Fi coverage in cold store", "Survey not scheduled yet",
         "Mei Hartmann", "High", "Open", d - timedelta(days=50), d - timedelta(days=17), ""],
        ["R-103-03", "PRJ-103", "Decision", "Pilot in one warehouse first", "Agreed at steering committee",
         "Jonas Petrov", "Medium", "Closed", d - timedelta(days=70), d - timedelta(days=60), d - timedelta(days=61)],
        ["R-104-01", "PRJ-104", "Risk", "Adoption by business analysts", "Champions network in place",
         "Rafael Dumont", "High", "Open", d - timedelta(days=15), d + timedelta(days=45), ""],
        ["R-104-02", "PRJ-104", "Dependency", "Governed dataset owners named", "Owners named for finance and sales",
         "Henrik Moreau", "Medium", "Closed", d - timedelta(days=40), d - timedelta(days=20), d - timedelta(days=21)],
    ]  # fmt: skip
    out = []
    for row in rows:
        out.append([v.isoformat() if isinstance(v, date) else v for v in row])
    return out


def jira_issues(seed: int, as_of: date) -> list[dict[str, Any]]:
    """Epics and stories per project. Story creation and resolution are planted per project (see module docstring)."""
    issues: list[dict[str, Any]] = []
    moment = datetime(as_of.year, as_of.month, as_of.day, 6, 0)
    for project in PROJECTS:
        rnd = random.Random(f"{seed}:delivery:jira:{project.jira}")
        features = FEATURES[project.jira]
        start = datetime(project.start.year, project.start.month, project.start.day, 9, 0)
        for e, feature in enumerate(features, start=1):
            issues.append(_issue(project, f"{project.jira}-{e}", f"Epic: {feature}", "Epic", start, None, None, "", ""))
        number = len(features)
        stories: list[tuple[datetime, int]] = []
        base_count = {"EINV": 48, "PORT": 40, "VOICE": 42, "SELF": 30}[project.jira]
        span = max(1, (moment - start).days - 35)
        for i in range(base_count):
            stories.append(
                (
                    start + timedelta(days=round(span * i / base_count), hours=rnd.randint(0, 6)),
                    rnd.choice([2, 3, 5, 8]),
                )
            )
        if project.jira == "PORT":  # DP3: late scope added in the last four weeks
            for i in range(18):
                stories.append((moment - timedelta(days=26 - i, hours=3), rnd.choice([5, 8])))
        for s, (created, points) in enumerate(stories):
            number += 1
            age = (moment - created).days
            if project.jira == "PORT":
                done_chance = 0.55 if age > 60 else 0.2 if age > 28 else 0.0  # DP4: slow recent throughput
            elif project.jira == "SELF":
                done_chance = 0.95 if age > 21 else 0.5
            else:
                done_chance = 0.85 if age > 30 else 0.3
            resolved = None
            if rnd.random() < done_chance:
                resolved = min(
                    moment - timedelta(hours=2), created + timedelta(days=rnd.randint(5, max(6, min(age, 40))))
                )
            feature = features[s % len(features)]
            version = f"{project.jira} {'1.0' if s < len(stories) * 0.6 else '1.1'}"
            issues.append(
                _issue(project, f"{project.jira}-{number}", f"As a user I can use {feature} ({s + 1})", "Story",
                       created, resolved, points, f"{project.jira}-{(s % len(features)) + 1}",
                       version if resolved else "")
            )  # fmt: skip
    return issues


def _issue(project, key, summary, kind, created, resolved, points, parent, version) -> dict[str, Any]:
    status = "Done" if resolved else "In Progress" if kind == "Story" and created.day % 3 == 0 else "To Do"
    return {
        "Issue key": key,
        "Project key": project.jira,
        "Project name": project.name,
        "Summary": summary,
        "Issue Type": kind,
        "Status": status,
        "Status Category": "Done" if resolved else "In Progress" if status == "In Progress" else "To Do",
        "Priority": "Medium",
        "Assignee": DEVELOPERS[zlib.crc32(key.encode()) % len(DEVELOPERS)] if kind == "Story" else "",
        "Created": created,
        "Updated": resolved or created + timedelta(days=1),
        "Resolved": resolved,
        "Parent": parent,
        "Fix Version/s": version,
        "Custom field (Story Points)": points if points is not None else "",
    }


def confluence_pages(project: Project, as_of: date) -> list[dict[str, Any]]:
    base = int(project.project_id.split("-")[1]) * 1000
    pages = []
    for i, feature in enumerate(FEATURES[project.jira], start=1):
        pages.append(
            {
                "page_id": str(base + i),
                "title": f"Requirements - {feature}",
                "labels": ["requirements", project.jira.lower()],
                "author": MANAGERS[i % len(MANAGERS)],
                "modified": datetime.combine(as_of - timedelta(days=10 * i), datetime.min.time()),
                "body": f"As a business user I need {feature} so that the {project.name} scope is covered. "
                f"Acceptance: the {feature} flow works end to end for the pilot users.",
            }
        )
    decisions = ["Use the platform integration layer", "Keep master data in the source system", "Deploy in waves"]
    for i, decision in enumerate(decisions, start=1):
        pages.append(
            {
                "page_id": str(base + 100 + i),
                "title": f"ADR-{i:03d} {decision}",
                "labels": ["adr", project.jira.lower()],
                "author": MANAGERS[(i + 1) % len(MANAGERS)],
                "modified": datetime.combine(as_of - timedelta(days=20 * i), datetime.min.time()),
                "body": f"Context: {project.name} needs a decision. Decision: {decision.lower()}. "
                "Consequences: documented in the design.",
            }
        )
    return pages


def generate(paths: Paths, req: SynthRequest) -> dict[str, Any]:
    if paths.data_class != "synthetic":
        raise PreconditionFailed("`sed synth` only runs on synthetic/eval profiles, never on the real profile.")
    inbox = paths.inbox
    inbox.mkdir(parents=True, exist_ok=True)
    if req.clean:
        inbox_manifest.clean(inbox, KEY)
    as_of = req.as_of
    written: list[str] = []

    W.write_csv(
        inbox / "delivery_projects.csv",
        ["Project ID", "Project Name", "Application", "Phase", "RAG Status", "Sponsor", "Project Manager",
         "Jira Projects", "Confluence Space", "Start Date", "Target Go-Live", "Budget", "Currency"],
        [[p.project_id, p.name, p.app, p.phase, p.rag, SPONSORS[i], MANAGERS[i], p.jira, p.jira,
          p.start.isoformat(), p.target.isoformat(), p.budget, "EUR"] for i, p in enumerate(PROJECTS)],
    )  # fmt: skip
    written.append("delivery_projects.csv")

    status_dates = [as_of - timedelta(days=15), as_of - timedelta(days=8), as_of - timedelta(days=1)]
    plan_header = ["Project ID", "ID", "Status Date", "Name", "Milestone", "Start", "Finish", "Baseline Finish",
                   "Actual Finish", "% Complete"]  # fmt: skip
    for version, status_date in enumerate(status_dates):
        name = f"delivery_plan_{status_date.isoformat()}.csv"
        W.write_csv(inbox / name, plan_header, [r for p in PROJECTS for r in plan_rows(p, status_date, version)])
        written.append(name)

    W.write_csv(
        inbox / "delivery_raid_log.csv",
        ["ID", "Project ID", "Type", "Title", "Description", "Owner", "Severity", "Status", "Raised On", "Due Date",
         "Closed On"],
        raid_rows(as_of),
    )  # fmt: skip
    written.append("delivery_raid_log.csv")

    header = ["Issue key", "Project key", "Project name", "Summary", "Issue Type", "Status", "Status Category",
              "Priority", "Assignee", "Created", "Updated", "Resolved", "Parent", "Fix Version/s",
              "Custom field (Story Points)"]  # fmt: skip
    issues = jira_issues(req.seed, as_of)
    W.write_csv(
        inbox / "jira_export_delivery.csv",
        header,
        [
            [
                *(i[h] for h in header[:9]),
                i["Created"].strftime(JIRA_FMT),
                i["Updated"].strftime(JIRA_FMT),
                i["Resolved"].strftime(JIRA_FMT) if i["Resolved"] else "",
                i["Parent"],
                i["Fix Version/s"],
                i["Custom field (Story Points)"],
            ]
            for i in issues
        ],
    )
    written.append("jira_export_delivery.csv")

    for project in PROJECTS:
        folder = f"confluence_space_{project.jira}"
        W.write_confluence_space(inbox / folder, project.jira, project.name, confluence_pages(project, as_of))
        written.append(folder)

    files = {name: file_sha256(inbox / name) for name in sorted(written)}
    inbox_manifest.save_section(
        inbox,
        KEY,
        {"generator_version": GENERATOR_VERSION, "seed": req.seed, "as_of": as_of.isoformat(), "files": files},
    )
    truth = _ground_truth(paths.ground_truth / KEY, as_of, status_dates)
    counts = {
        "project": len(PROJECTS),
        "plan_versions": len(status_dates),
        "raid": len(raid_rows(as_of)),
        "jira_issue": len(issues),
    }
    return {"inbox": str(inbox), "files": len(written), "counts": counts, "ground_truth": truth}


def _ground_truth(folder: Path, as_of: date, status_dates: list[date]) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    patterns = {
        "generator_version": GENERATOR_VERSION,
        "as_of": as_of.isoformat(),
        "plan_status_dates": [d.isoformat() for d in status_dates],
        "DP1": {"project_id": "PRJ-101", "task_id": "M4", "slip_days": 35, "replans": 2},
        "DP2": {"project_id": "PRJ-103", "raid_id": "R-103-02", "days_overdue": 17},
        "DP3": {"project_id": "PRJ-102", "window_weeks": 4},
        "DP4": {"project_id": "PRJ-102"},
        "control": {"project_id": "PRJ-104", "closed_on_time": "R-103-01"},
    }
    (folder / "patterns.json").write_text(json.dumps(patterns, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(folder)
