"""`sed synth`: write a full set of realistic synthetic exports into DATA_DIR\\inbox (+ manifest + ground truth)."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sed.errors import PreconditionFailed
from sed.ingest import manifest as inbox_manifest
from sed.ingest.loader import MANIFEST, file_sha256
from sed.paths import Paths
from sed.synth import writers as W
from sed.synth.catalog import CRITICALITY, VENDOR_RUN_GROUPS, build_catalog
from sed.synth.commercial import JIRA_PROJECTS, build_commercial, build_confluence, build_jira
from sed.synth.tickets import (
    IMPACT_LABEL,
    PRIORITY_LABEL,
    RESOLUTION_TARGET_H,
    RESPONSE_TARGET_H,
    PlanContext,
    Ticket,
    TicketGenerator,
    month_start,
)

GENERATOR_VERSION = 2
INCIDENT_FIELDS = [
    "number",
    "opened_at",
    "sys_updated_on",
    "resolved_at",
    "closed_at",
    "state",
    "priority",
    "impact",
    "urgency",
    "category",
    "subcategory",
    "short_description",
    "description",
    "close_code",
    "close_notes",
    "caller_id",
    "assigned_to",
    "assignment_group",
    "business_service",
    "cmdb_ci",
    "made_sla",
    "reassignment_count",
    "reopen_count",
    "problem_id",
    "caused_by",
    "parent_incident",
]
INCIDENT_LABELS = [
    "Number",
    "Opened",
    "Updated",
    "Resolved",
    "Closed",
    "State",
    "Priority",
    "Impact",
    "Urgency",
    "Category",
    "Subcategory",
    "Short description",
    "Description",
    "Resolution code",
    "Resolution notes",
    "Caller",
    "Assigned to",
    "Assignment group",
    "Service",
    "Configuration item",
    "Made SLA",
    "Reassignment count",
    "Reopen count",
    "Problem",
    "Caused by Change",
    "Parent Incident",
]


@dataclass
class SynthOptions:
    seed: int = 42
    as_of: date = date(2026, 9, 1)
    anchor: date | None = None
    months: int = 18
    scale: float = 1.0
    clean: bool = True


def _state_row(t: Ticket, moment: datetime, missed_resolution: bool) -> dict[str, Any]:
    resolved = t.resolved
    is_resolved = resolved is not None and resolved <= moment and not missed_resolution
    target = RESOLUTION_TARGET_H[t.priority]
    row: dict[str, Any] = {"resolved_at": None, "closed_at": None}
    rnd = random.Random(t.number)
    if is_resolved:
        assert resolved is not None
        if t.kind == "incident":
            closed = resolved + timedelta(days=5)
            row["resolved_at"] = resolved
            row["closed_at"] = closed if closed <= moment else None
            row["state"] = "Closed" if row["closed_at"] else "Resolved"
        elif t.kind == "sc_req_item":
            row["closed_at"] = resolved
            row["state"] = "Closed Complete" if rnd.random() < 0.94 else "Closed Incomplete"
        elif t.kind == "change_request":
            row["closed_at"] = resolved
            row["state"] = "Closed"
        else:
            row["resolved_at"] = resolved
            closed = resolved + timedelta(days=7)
            row["closed_at"] = closed if closed <= moment else None
            row["state"] = "Closed" if row["closed_at"] else "Resolved"
        row["updated"] = row["closed_at"] or row["resolved_at"]
        row["made_sla"] = not bool(t.target_breach)
    else:
        elapsed_h = (moment - t.opened).total_seconds() / 3600
        states = {
            "incident": ["In Progress", "On Hold", "In Progress"],
            "sc_req_item": ["Work in Progress", "Open"],
            "change_request": ["Scheduled", "Implement", "Assess"],
            "problem": ["Root Cause Analysis", "Fix in Progress"],
        }
        row["state"] = "New" if elapsed_h < 4 and t.kind == "incident" else rnd.choice(states[t.kind])
        if missed_resolution and resolved is not None:
            row["updated"] = resolved - timedelta(hours=2)
        else:
            row["updated"] = min(moment - timedelta(minutes=30), t.opened + timedelta(hours=elapsed_h * 0.6))
        row["made_sla"] = elapsed_h <= target
    return row


def _sla_rows(t: Ticket, moment: datetime, display_format: bool) -> list[list[Any]]:
    rnd = random.Random(f"sla:{t.number}")
    resp_target = RESPONSE_TARGET_H[t.priority]
    resp_h = resp_target * (rnd.uniform(1.1, 2.0) if rnd.random() < 0.03 else rnd.uniform(0.05, 0.9))
    rows = []
    for name, hours, breach, done in (
        (
            f"P{t.priority} Response ({resp_target:g} hours)",
            resp_h,
            resp_h > resp_target,
            t.opened + timedelta(hours=resp_h) <= moment,
        ),
        (
            f"P{t.priority} Resolution ({RESOLUTION_TARGET_H[t.priority]} hours)",
            t.resolve_h,
            bool(t.target_breach),
            t.resolved is not None and t.resolved <= moment,
        ),
    ):
        if hours is None or not done:
            elapsed = (moment - t.opened).total_seconds() / 3600
            end = None
            stage = "In progress"
            breached = "resolution" in name.lower() and elapsed > RESOLUTION_TARGET_H[t.priority]
            dur_s = int(elapsed * 3600 * 0.4)
        else:
            end = t.opened + timedelta(hours=hours)
            stage = "Completed"
            breached = breach
            dur_s = int(hours * 3600 * 0.4)
        if display_format:
            d, rem = divmod(dur_s, 86400)
            h, rem = divmod(rem, 3600)
            duration = " ".join(
                p for p in (f"{d} Days" if d else "", f"{h} Hours" if h else "", f"{rem // 60} Minutes") if p
            )
        else:
            duration = (datetime(1970, 1, 1) + timedelta(seconds=dur_s)).strftime(W.CSV_DT)
        rows.append(
            [t.number, name, stage, "true" if breached else "false", W.fmt_dt(t.opened), W.fmt_dt(end), duration]
        )
    return rows


def generate(paths: Paths, opts: SynthOptions) -> dict[str, Any]:
    if paths.data_class != "synthetic":
        raise PreconditionFailed("`sed synth` only runs on synthetic/eval profiles, never on the real profile.")
    anchor = opts.anchor or opts.as_of
    if opts.as_of < anchor:
        raise PreconditionFailed("--as-of must be on or after the pattern anchor date")
    window_start = month_start(anchor, opts.months)
    moment = datetime(opts.as_of.year, opts.as_of.month, opts.as_of.day, 6, 0, 0)
    inbox = paths.inbox
    inbox.mkdir(parents=True, exist_ok=True)
    if opts.clean:
        inbox_manifest.clean(inbox, "ops", inbox_manifest.LEGACY)  # a pre-sections manifest was always ours

    cat = build_catalog(opts.seed)
    ctx = PlanContext(opts.seed, anchor, window_start, opts.as_of, opts.scale, cat)
    gen = TicketGenerator(ctx)
    tickets: dict[str, list[Ticket]] = defaultdict(list)
    day = window_start
    while day < opts.as_of:
        tickets["incident"] += gen.incidents_for_day(day)
        tickets["sc_req_item"] += gen.requests_for_day(day)
        tickets["change_request"] += gen.changes_for_day(day)
        tickets["problem"] += gen.problems_for_day(day)
        day += timedelta(days=1)

    written: list[str] = []
    truth_rows: list[list[Any]] = []

    # Delta exports that missed recent resolutions: store keeps them open until the active snapshot says otherwise.
    missed = {
        t.number
        for t in tickets["incident"]
        if t.resolved
        and moment - timedelta(days=3) <= t.resolved <= moment
        and random.Random(f"missed:{t.number}").random() < 0.3
    }

    by_month: dict[date, list[list[Any]]] = defaultdict(list)
    sla_by_month: dict[date, list[list[Any]]] = defaultdict(list)
    active_rows: list[list[Any]] = []
    sla_display_month = month_start(anchor, 12)
    for t in tickets["incident"]:
        state = _state_row(t, moment, t.number in missed)
        row = [
            t.number,
            W.fmt_dt(t.opened),
            W.fmt_dt(state["updated"]),
            W.fmt_dt(state["resolved_at"]),
            W.fmt_dt(state["closed_at"]),
            state["state"],
            PRIORITY_LABEL[t.priority],
            IMPACT_LABEL[min(3, max(1, (t.priority + 1) // 2))],
            IMPACT_LABEL[min(3, max(1, t.priority // 2 + 1))],
            t.category,
            t.subcategory,
            t.short,
            t.desc,
            t.close_code if state["resolved_at"] else "",
            t.close_notes if state["resolved_at"] else "",
            t.caller,
            t.assignee,
            t.group,
            t.business_service,
            t.ci,
            "true" if state["made_sla"] else "false",
            t.reassignments,
            t.reopen,
            t.problem_id or "",
            t.caused_by or "",
            t.parent_incident or "",
        ]
        m = date(t.opened.year, t.opened.month, 1)
        by_month[m].append(row)
        true_open = t.resolved is None or t.resolved > moment
        if true_open:
            active_state = _state_row(t, moment, False)
            active_rows.append([*row[:2], W.fmt_dt(active_state["updated"]), "", "", active_state["state"], *row[6:]])
        sla_by_month[m] += _sla_rows(t, moment, display_format=(m == sla_display_month))
        truth_rows.append(
            [
                t.number,
                "incident",
                t.app.name if t.app else "",
                t.truth.get("symptom_key", ""),
                t.truth.get("am_category", ""),
                t.truth.get("am_subcategory", ""),
                t.truth.get("misfiled_as", ""),
                t.truth.get("pattern", ""),
            ]
        )

    last_month = month_start(anchor, 1)
    split_month = month_start(anchor, 6)
    for m, rows in sorted(by_month.items()):
        label = m.strftime("%Y-%m")
        if m == last_month:
            labelled = [[r[0], *[_relabel_dt(v) for v in r[1:5]], *r[5:]] for r in rows]
            W.write_xlsx(inbox / f"incident_{label}.xlsx", INCIDENT_LABELS, labelled, sheet="Incidents")
            written.append(f"incident_{label}.xlsx")
        elif m == split_month:
            half = len(rows) // 2
            for part, chunk in ((1, rows[:half]), (2, rows[half:])):
                W.write_csv(inbox / f"incident_{label}_part{part}.csv", INCIDENT_FIELDS, chunk)
                written.append(f"incident_{label}_part{part}.csv")
        else:
            W.write_csv(inbox / f"incident_{label}.csv", INCIDENT_FIELDS, rows)
            written.append(f"incident_{label}.csv")
    active_name = f"incident_active_{opts.as_of.isoformat()}.csv"
    W.write_csv(inbox / active_name, INCIDENT_FIELDS, active_rows)
    written.append(active_name)
    sla_header = ["task", "sla", "stage", "has_breached", "start_time", "end_time", "business_duration"]
    for m, rows in sorted(sla_by_month.items()):
        name = f"task_sla_{m.strftime('%Y-%m')}.csv"
        W.write_csv(inbox / name, sla_header, rows)
        written.append(name)

    written += _write_requests(inbox, tickets["sc_req_item"], moment)
    written += _write_changes(inbox, tickets["change_request"], moment)
    written += _write_problems(inbox, tickets["problem"], moment)
    for kind in ("sc_req_item", "change_request", "problem"):
        for t in tickets[kind]:
            truth_rows.append([t.number, kind, t.app.name if t.app else "", "", "", "", "", t.truth.get("pattern", "")])

    commercial = build_commercial(cat, opts.seed, anchor, window_start)
    written += _write_master(inbox, cat, commercial, anchor, window_start)
    jira = build_jira(cat, opts.seed, anchor, window_start, opts.scale)
    written += _write_jira(inbox, jira, opts.as_of)
    for key, pages in build_confluence(cat, opts.seed, anchor).items():
        from sed.synth.commercial import CONFLUENCE_SPACES

        W.write_confluence_space(inbox / f"confluence_space_{key}", key, CONFLUENCE_SPACES[key][0], pages)
        written.append(f"confluence_space_{key}")

    section = {
        "generator_version": GENERATOR_VERSION,
        "seed": opts.seed,
        "as_of": opts.as_of.isoformat(),
        "anchor": anchor.isoformat(),
        "months": opts.months,
        "scale": opts.scale,
        "files": {name: file_sha256(inbox / name) for name in sorted(written)},
    }
    inbox_manifest.save_section(inbox, "ops", section)

    counts = {k: len(v) for k, v in tickets.items()}
    truth = _ground_truth(paths, ctx, gen, commercial, truth_rows, missed, counts, opts, anchor)
    return {"inbox": str(inbox), "files": len(written), "counts": counts, "ground_truth": truth, "manifest": MANIFEST}


def _relabel_dt(value: str) -> str:
    return datetime.strptime(value, W.CSV_DT).strftime(W.LABEL_DT) if value else ""


def _write_requests(inbox: Path, tickets: list[Ticket], moment: datetime) -> list[str]:
    header = [
        "Number",
        "Opened",
        "Updated",
        "Closed",
        "State",
        "Priority",
        "Item",
        "Short description",
        "Description",
        "Requested for",
        "Assigned to",
        "Assignment group",
        "Service",
        "Configuration item",
    ]
    by_month: dict[date, list[list[Any]]] = defaultdict(list)
    for t in tickets:
        s = _state_row(t, moment, False)
        by_month[date(t.opened.year, t.opened.month, 1)].append(
            [
                t.number,
                W.fmt_dt(t.opened, W.LABEL_DT),
                W.fmt_dt(s["updated"], W.LABEL_DT),
                W.fmt_dt(s["closed_at"], W.LABEL_DT),
                s["state"],
                PRIORITY_LABEL[t.priority],
                t.category,
                t.short,
                t.desc,
                t.caller,
                t.assignee,
                t.group,
                t.business_service,
                t.ci,
            ]
        )
    names = []
    for m, rows in sorted(by_month.items()):
        name = f"sc_req_item_{m.strftime('%Y-%m')}.xlsx"
        W.write_xlsx(inbox / name, header, rows, sheet="Requested Items")
        names.append(name)
    return names


def _write_changes(inbox: Path, tickets: list[Ticket], moment: datetime) -> list[str]:
    header = [
        "number",
        "opened_at",
        "sys_updated_on",
        "closed_at",
        "state",
        "type",
        "risk",
        "start_date",
        "end_date",
        "close_code",
        "close_notes",
        "short_description",
        "description",
        "assignment_group",
        "assigned_to",
        "business_service",
        "cmdb_ci",
    ]
    by_month: dict[date, list[list[Any]]] = defaultdict(list)
    for t in tickets:
        s = _state_row(t, moment, False)
        closed = s["closed_at"]
        by_month[date(t.opened.year, t.opened.month, 1)].append(
            [
                t.number,
                W.fmt_dt(t.opened),
                W.fmt_dt(s["updated"]),
                W.fmt_dt(closed),
                s["state"],
                t.change_type,
                t.risk,
                W.fmt_dt(t.planned_start),
                W.fmt_dt(t.planned_end),
                t.close_code if closed else "",
                t.close_notes if closed else "",
                t.short,
                t.desc,
                t.group,
                t.assignee,
                t.business_service,
                t.ci,
            ]
        )
    names = []
    for m, rows in sorted(by_month.items()):
        name = f"change_request_{m.strftime('%Y-%m')}.csv"
        W.write_csv(inbox / name, header, rows)
        names.append(name)
    return names


def _write_problems(inbox: Path, tickets: list[Ticket], moment: datetime) -> list[str]:
    header = [
        "number",
        "opened_at",
        "sys_updated_on",
        "resolved_at",
        "closed_at",
        "state",
        "priority",
        "category",
        "short_description",
        "description",
        "cause_notes",
        "assignment_group",
        "assigned_to",
        "business_service",
        "cmdb_ci",
    ]
    rows = []
    for t in tickets:
        s = _state_row(t, moment, False)
        rows.append(
            [
                t.number,
                W.fmt_dt(t.opened),
                W.fmt_dt(s["updated"]),
                W.fmt_dt(s["resolved_at"]),
                W.fmt_dt(s["closed_at"]),
                s["state"],
                PRIORITY_LABEL[t.priority],
                t.category,
                t.short,
                t.desc,
                t.close_notes if s["resolved_at"] else "",
                t.group,
                t.assignee,
                t.business_service,
                t.ci,
            ]
        )
    W.write_csv(inbox / "problem.csv", header, rows)
    return ["problem.csv"]


def _write_master(inbox: Path, cat, commercial, anchor: date, window_start: date) -> list[str]:
    names = []
    user_rows = []
    for person in cat.people:
        first, last = person.split(" ", 1)
        user_id = f"{first[0]}{last}".lower().replace(" ", "")
        user_rows.append(
            [person, user_id, f"{user_id}@example.com", "IT" if person in cat.agents else "Business", "true"]
        )
    W.write_csv(inbox / "sys_user.csv", ["name", "user_name", "email", "department", "active"], user_rows)
    names.append("sys_user.csv")
    W.write_xlsx(
        inbox / "cmdb_ci_business_app.xlsx",
        [
            "Number",
            "Name",
            "Application family",
            "Business criticality",
            "Life cycle stage",
            "IT application owner",
            "Cost center",
            "Vendor",
        ],
        [
            [
                a.app_id,
                a.name,
                a.family,
                CRITICALITY[a.criticality],
                a.lifecycle,
                a.owner,
                a.cost_center,
                a.vendor or "",
            ]
            for a in cat.apps
        ],
        sheet="Business Applications",
    )
    names.append("cmdb_ci_business_app.xlsx")
    rels = [[a.name, ci, "Depends on::Used by"] for a in cat.apps for ci in a.cis]
    rels += [["shared-sql-cluster-03", "shared-sql-cluster-03-node1", "Cluster of::Cluster"]]
    W.write_csv(inbox / "cmdb_rel_ci.csv", ["parent", "child", "type"], rels)
    names.append("cmdb_rel_ci.csv")
    rnd = random.Random("groups")
    group_rows = []
    for g in cat.groups.values():
        vendor_spelling = ""
        if g.vendor:
            v = cat.vendor(g.vendor)
            vendor_spelling = rnd.choice(v.aliases) if v.aliases and rnd.random() < 0.3 else v.name
        group_rows.append([g.name, f"{g.family or 'Shared'} support group", g.members[0], vendor_spelling, "true"])
    W.write_csv(inbox / "sys_user_group.csv", ["name", "description", "manager", "u_vendor", "active"], group_rows)
    names.append("sys_user_group.csv")
    W.write_xlsx(
        inbox / "Vendor_Master.xlsx",
        ["Vendor ID", "Vendor Name", "Type", "Tier", "SLA Target %"],
        [[v.vendor_id, v.name, v.vtype, v.tier, f"{v.sla_target * 100:.0f}%"] for v in cat.vendors],
        sheet="Vendors",
    )
    names.append("Vendor_Master.xlsx")
    W.write_xlsx(
        inbox / "Contracts_Register.xlsx",
        [
            "Contract No",
            "Vendor",
            "Application",
            "Product/Service",
            "Start Date",
            "End Date",
            "Notice Period (days)",
            "Auto Renew",
            "Status",
            "Annual Value",
            "Currency",
            "Contract Owner",
            "Comments",
        ],
        [
            [
                c.number,
                c.vendor_spelling,
                c.app or "",
                c.product,
                c.start,
                c.end,
                c.notice_days,
                "Y" if c.auto_renew else "N",
                c.status,
                c.annual_value,
                c.currency,
                c.owner,
                c.comments,
            ]
            for c in commercial.contracts
        ],
        sheet="Register",
        title_rows=[f"IT Contracts Register - FY{anchor.year % 100}"],
    )
    names.append("Contracts_Register.xlsx")
    rnd = random.Random("licenses")
    lic_rows = []
    for lic in commercial.licenses:
        v = cat.vendor(lic.vendor)
        spelling = rnd.choice(v.aliases) if v.aliases and rnd.random() < 0.25 else v.name
        lic_rows.append(
            [
                lic.license_id,
                lic.app,
                spelling,
                lic.contract or "",
                lic.product,
                lic.metric,
                lic.entitled,
                lic.unit_cost,
            ]
        )
    W.write_xlsx(
        inbox / "License_Inventory.xlsx",
        ["License ID", "Application", "Vendor", "Contract No", "Product", "Metric", "Entitled Qty", "Unit Cost (EUR)"],
        lic_rows,
        sheet="Inventory",
    )
    names.append("License_Inventory.xlsx")
    months = sorted({m for lic in commercial.licenses for m in lic.usage})
    for m in months:
        name = f"License_Usage_{m}.xlsx"
        W.write_xlsx(
            inbox / name,
            ["License ID", "Assigned", "Active (90d)"],
            [[lic.license_id, lic.usage[m][0], lic.usage[m][1]] for lic in commercial.licenses if m in lic.usage],
            sheet="Usage",
        )
        names.append(name)
    grouped: dict[tuple[str, str, str, str], dict[date, str]] = defaultdict(dict)
    for line in commercial.cost_actuals:
        grouped[(line["application"], line["vendor"], line["category"], line["cost_center"])][line["month"]] = (
            W.eu_amount(line["amount"])
        )
    all_months = sorted({line["month"] for line in commercial.cost_actuals})
    fy_label = f"FY{window_start.year % 100:02d}_FY{anchor.year % 100:02d}"
    W.write_wide_costs(
        inbox / f"IT_Cost_Actuals_{fy_label}.xlsx",
        ["Application", "Vendor", "Cost Category", "Cost Center", "Contract No"],
        all_months,
        [([app, vendor, category, cc, ""], amounts) for (app, vendor, category, cc), amounts in grouped.items()],
        title="IT Cost Actuals (EUR)",
    )
    names.append(f"IT_Cost_Actuals_{fy_label}.xlsx")
    budget_name = f"Budget_FY{anchor.year % 100:02d}.csv"
    W.write_csv(
        inbox / budget_name,
        ["Application", "Vendor", "Catégorie de coût", "Centre de coût", "Période", "Montant"],
        [
            [b["application"], b["vendor"], b["category"], b["cost_center"], b["period"], W.eu_amount(b["amount"])]
            for b in commercial.budget
        ],
        encoding="cp1252",
        delimiter=";",
        preamble=[f"Budget IT FY{anchor.year % 100:02d} – version 1"],
    )
    names.append(budget_name)
    return names


def _write_jira(inbox: Path, issues: list[dict[str, Any]], as_of: date) -> list[str]:
    header = [
        "Issue key",
        "Project key",
        "Project name",
        "Summary",
        "Issue Type",
        "Status",
        "Status Category",
        "Priority",
        "Assignee",
        "Created",
        "Updated",
        "Resolved",
        "Sprint",
        "Sprint",
        "Labels",
        "Labels",
        "Component/s",
        "Fix Version/s",
        "Parent",
        "Custom field (Story Points)",
    ]
    jira_fmt = "%d/%b/%y %I:%M %p"
    by_project: dict[str, list[list[Any]]] = defaultdict(list)
    for i in issues:
        sprints = (i["Sprint"] + ["", ""])[:2]
        labels = (i["Labels"] + ["", ""])[:2]
        by_project[i["Project key"]].append(
            [
                i["Issue key"],
                i["Project key"],
                i["Project name"],
                i["Summary"],
                i["Issue Type"],
                i["Status"],
                i["Status Category"],
                i["Priority"],
                i["Assignee"],
                i["Created"].strftime(jira_fmt),
                i["Updated"].strftime(jira_fmt),
                i["Resolved"].strftime(jira_fmt) if i["Resolved"] else "",
                *sprints,
                *labels,
                (i["Component/s"] or [""])[0],
                i["Fix Version/s"],
                i["Parent"],
                i["Custom field (Story Points)"],
            ]
        )
    names = []
    for key, _ in JIRA_PROJECTS:
        name = f"jira_export_{key}.csv"
        W.write_csv(inbox / name, header, by_project.get(key, []))
        names.append(name)
    return names


def _ground_truth(
    paths: Paths, ctx: PlanContext, gen: TicketGenerator, commercial, truth_rows, missed, counts, opts, anchor: date
) -> str:
    gt = paths.ground_truth
    gt.mkdir(parents=True, exist_ok=True)
    W.write_csv(
        gt / "ticket_truth.csv",
        ["number", "kind", "app", "symptom_key", "am_category", "am_subcategory", "misfiled_as", "pattern"],
        truth_rows,
    )
    patterns = {
        "generator_version": GENERATOR_VERSION,
        "seed": opts.seed,
        "anchor": anchor.isoformat(),
        "as_of": opts.as_of.isoformat(),
        "counts": counts,
        "P1": {
            "app": "Orion ERP",
            "change": ctx.p1_change,
            "problem": ctx.p1_problem,
            "incidents": 230,
            "change_day": gen.p1_change_day.isoformat(),
        },
        "P2": {
            "vendor": "Nordwind Managed Services",
            "groups": sorted(g for g, (v, _) in VENDOR_RUN_GROUPS.items() if v == "Nordwind Managed Services"),
            "made_sla_by_month": {m.strftime("%Y-%m"): r for m, r in sorted(gen.degrade_months.items())},
        },
        "P5": {"app": "Ledgerline Finance", "days": sorted(d.isoformat() for d in gen.p5_counts)},
        "P6": {"apps": ["Atlas HR Core", "Waypoint Intranet"], "incidents": 700, "misfiled_as": "request"},
        "P7": {"day": gen.p7_day.isoformat(), "parent": ctx.p7_parent, "children": 160},
        "P8": {**commercial.truth["P8"], "change": ctx.p8_change},
        "P9": {"group": "IT-HR-L2", "from": gen.p9_start.isoformat(), "weeks": 10},
        "P3": commercial.truth["P3"],
        "P4": commercial.truth["P4"],
        "P4_uplift": commercial.truth["P4_uplift"],
        "P10": commercial.truth["P10"],
        "P11": commercial.truth["P11"],
        "P12": {
            "vendor_alias_spellings": {v.name: v.aliases for v in ctx.catalog.vendors if v.aliases},
            "dirt_files": [
                "Budget_FY*.csv (cp1252, ';', EU decimals, title row)",
                "IT_Cost_Actuals_*.xlsx (merged FY header, EU decimal text, title row)",
                "Contracts_Register.xlsx (title row)",
                "incident_<last month>.xlsx (display labels, dd/mm)",
                "incident_<month>_part1/2.csv (row-capped split)",
                "jira_export_*.csv (repeated headers)",
                "task_sla_<month>.csv (display durations)",
            ],
            "unmapped_ci": "shared-sql-cluster-03",
        },
        "P13": {"injections": len(ctx.pii_injections)},
        "stale_open_expected": sorted(missed),
        "controls": {
            **commercial.truth["controls"],
            "noisy_stable_vendor": "Keel Support Services",
            "go_live_app": "Pathway Learning",
            "go_live_day": gen.golive_day.isoformat(),
            "coincidental_change_ci": "forge-mes-prd-db01",
        },
    }
    (gt / "patterns.json").write_text(json.dumps(patterns, indent=2, default=str), encoding="utf-8")
    (gt / "pii_injections.json").write_text(json.dumps(sorted(set(ctx.pii_injections)), indent=0), encoding="utf-8")
    return str(gt)
