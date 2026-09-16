"""Synthetic SAP data (fictional; generic SAP terms only): SAP business applications, SAP L3 incidents with planted
patterns, and the "all open SAP incidents" snapshot. Files go through the SAP mappings (sap_business_apps,
sap_incidents, sap_incidents_active); ground truth goes to ground_truth/sap/.

Determinism: one RNG per (seed, stream, day). Planted patterns are anchored to the anchor date and generated at
absolute counts whatever --scale is; a later --as-of with the same anchor gives a superset of tickets.

Planted patterns (ground_truth/sap/patterns.json):
* SP1 EWM backlog growth: 3 EWM incidents per business day in the 8 complete weeks before the anchor week, 40% open.
* SP2 FI/CO month-end close: 6 "period-end close job failed" incidents on business days 1 and 2 of the last 12 months.
* SN1 SD go-live surge (negative control): 8 SD incidents per business day for 3 weeks, all resolved within 2 days.
* SC1 service desk tickets with the SAP category (area "unassigned"); SC2 tickets marked only by the u_sap_component
  custom field.
"""

from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sed.errors import PreconditionFailed
from sed.ingest import manifest as inbox_manifest
from sed.ingest.loader import file_sha256
from sed.modules.contract import SynthRequest
from sed.paths import Paths
from sed.synth import writers as W
from sed.synth.catalog import build_catalog
from sed.synth.tickets import (
    IMPACT_LABEL,
    PRIORITY_LABEL,
    RESOLUTION_TARGET_H,
    business_days_of_month,
    month_start,
    number,
)

GENERATOR_VERSION = 1
KEY = "sap"
ECC_APP = ("APM0990001", "SAP ECC")
S4_APP = ("APM0990002", "SAP S/4HANA")
SERVICE_DESK = "IT-SERVICE-DESK-L1"
MONTH_INCIDENTS = 150
WEEKDAY_WEIGHT = [1.0, 1.0, 1.0, 1.0, 1.0, 0.1, 0.1]
WEIGHTED_DAYS_PER_MONTH = 21.7 + 8.7 * 0.1
MEDIAN_H = {1: 3.0, 2: 6.0, 3: 30.0, 4: 80.0, 5: 120.0}
BLOCK = {"background": 6000, "SP1": 7000, "SP2": 7200, "SN1": 7400, "SC1": 7600, "SC2": 7700}
INCIDENT_FIELDS = [
    "number", "opened_at", "sys_updated_on", "resolved_at", "closed_at", "state", "priority", "impact", "urgency",
    "category", "subcategory", "short_description", "description", "close_code", "close_notes", "caller_id",
    "assigned_to", "assignment_group", "business_service", "cmdb_ci", "made_sla", "reassignment_count", "reopen_count",
    "problem_id", "caused_by", "parent_incident", "u_sap_component",
]  # fmt: skip

# area -> (group, share of background volume, S/4 share, short description templates, SAP subcategory for AI truth)
AREAS: dict[str, tuple[str, float, float, list[tuple[str, str]]]] = {
    "fi_co": ("SAP-FICO-L3", 0.20, 0.45, [
        ("Posting error in company code {cc} for document {doc}", "sap_posting_error"),
        ("GL account determination failed for billing document {doc}", "sap_master_data"),
        ("Cost center report shows wrong totals for {period}", "sap_reporting"),
    ]),
    "sd": ("SAP-SD-L3", 0.16, 0.45, [
        ("Sales order {doc} blocked for delivery", "sap_process_error"),
        ("Pricing condition missing for customer {cust}", "sap_master_data"),
        ("Invoice output not sent for billing document {doc}", "sap_output"),
    ]),
    "mm": ("SAP-MM-L3", 0.14, 0.45, [
        ("Purchase order {doc} cannot be released", "sap_workflow"),
        ("Goods receipt posting error for material {mat}", "sap_posting_error"),
        ("Invoice verification blocked for supplier {cust}", "sap_process_error"),
    ]),
    "pp_qm": ("SAP-PPQM-L3", 0.10, 0.45, [
        ("Production order {doc} confirmation fails in plant {plant}", "sap_posting_error"),
        ("MRP run created no planned orders for plant {plant}", "sap_job_failure"),
    ]),
    "ewm": ("SAP-EWM-L3", 0.10, 0.90, [
        ("Warehouse task not confirmed in warehouse {wh}", "sap_process_error"),
        ("Handling unit stuck in staging area of warehouse {wh}", "sap_process_error"),
        ("Outbound delivery {doc} not distributed to the warehouse", "sap_interface"),
    ]),
    "basis": ("SAP-BASIS-L3", 0.10, 0.45, [
        ("Background job {job} cancelled", "sap_job_failure"),
        ("Slow response in transaction {tcode}", "sap_performance"),
        ("RFC connection to {dest} failing", "sap_interface"),
    ]),
    "security": ("SAP-SEC-L3", 0.08, 0.45, [
        ("Missing authorisation for transaction {tcode}", "sap_authorisation"),
        ("User locked after password reset in production client", "sap_authorisation"),
    ]),
    "integration": ("SAP-INT-L3", 0.08, 0.45, [
        ("IDoc {mtype} in error status 51 for partner {partner}", "sap_idoc_error"),
        ("Interface message stuck in the middleware queue for {mtype}", "sap_interface"),
    ]),
    "abap": ("SAP-ABAP-L3", 0.04, 0.45, [
        ("Short dump in custom program {prog}", "sap_custom_code_dump"),
        ("Custom report {prog} times out", "sap_performance"),
    ]),
}  # fmt: skip
MONTH_END = ("Period-end close job failed for company code {cc}", "sap_month_end_close")
COMPONENTS = ["FI-GL", "SD-BIL", "MM-PUR", "PP-SFC", "EWM-OUT"]
MESSAGE_TYPES = ["ORDERS", "INVOIC", "DESADV", "MATMAS", "DEBMAS"]
TCODES = ["FB60", "VA02", "ME21N", "CO11N", "MIGO", "VL02N"]


@dataclass
class SapTicket:
    number: str
    opened: datetime
    area: str | None
    group: str
    app: tuple[str, str]
    priority: int
    short: str
    desc: str
    caller: str
    assignee: str | None
    resolve_h: float | None
    category: str = "Software"
    component: str = ""
    pattern: str = ""
    subcategory: str = ""
    reassignments: int = 0
    truth: dict[str, Any] = field(default_factory=dict)

    @property
    def resolved(self) -> datetime | None:
        return None if self.resolve_h is None else self.opened + timedelta(hours=self.resolve_h)


class SapGenerator:
    def __init__(self, seed: int, anchor: date, window_start: date, as_of: date, scale: float) -> None:
        self.seed, self.anchor, self.window_start, self.as_of, self.scale = seed, anchor, window_start, as_of, scale
        people = build_catalog(seed).people
        self.callers = people
        self.team = people[-40:]
        self.pii: list[str] = []
        anchor_monday = anchor - timedelta(days=anchor.weekday())
        self.sp1_start = anchor_monday - timedelta(weeks=8)
        self.sp1_end = anchor_monday
        self.sn1_start = anchor_monday - timedelta(weeks=6)
        self.sn1_end = self.sn1_start + timedelta(weeks=3)
        self.sp2_days: set[date] = set()
        for back in range(1, 13):
            ms = month_start(anchor, back)
            self.sp2_days.update(business_days_of_month(ms.year, ms.month)[:2])

    def _rnd(self, stream: str, day: date) -> random.Random:
        return random.Random(f"{self.seed}:sap:{stream}:{day.isoformat()}")

    def _text(self, rnd: random.Random, template: str) -> str:
        return template.format(
            cc=rnd.choice(["1000", "1100", "2000", "3100"]),
            doc=str(rnd.randint(4_000_000, 4_999_999)),
            period=f"period {rnd.randint(1, 12):02d}",
            cust=str(rnd.randint(100_000, 199_999)),
            mat=f"MAT-{rnd.randint(10_000, 99_999)}",
            plant=rnd.choice(["P100", "P200", "P310"]),
            wh=rnd.choice(["W001", "W002"]),
            job=f"Z_{rnd.choice(['BILLING', 'MRP', 'SETTLEMENT', 'ARCHIVE'])}_{rnd.randint(1, 9)}",
            tcode=rnd.choice(TCODES),
            dest=f"RFC_{rnd.choice(['CRM', 'BW', 'MES'])}",
            mtype=rnd.choice(MESSAGE_TYPES),
            partner=f"PARTNER_{rnd.randint(1, 40):04d}",
            prog=f"Z_{rnd.choice(['SD', 'FI', 'MM'])}_REPORT_{rnd.randint(10, 99)}",
        )

    def _signature(self, rnd: random.Random, name: str) -> str:
        first, last = name.split(" ", 1)
        phone = "+33 6 " + " ".join(f"{rnd.randint(10, 99)}" for _ in range(4))
        email = f"{first.lower()}.{last.lower().replace(' ', '')}@example.com"
        self.pii += [name, phone, email]
        return f"\n\nBest regards,\n{name}\nTel {phone}\n{email}"

    def _ticket(
        self,
        rnd: random.Random,
        day: date,
        i: int,
        area: str | None,
        template: tuple[str, str],
        *,
        pattern: str = "",
        priority: int | None = None,
        resolve_h: float | str | None = "draw",
        group: str | None = None,
        s4_share: float | None = None,
        category: str = "Software",
        component: str = "",
    ) -> SapTicket:
        opened = datetime(day.year, day.month, day.day, rnd.choice([8, 9, 10, 11, 14, 15]), rnd.randrange(60))
        x = rnd.random()
        pr = priority or (2 if x < 0.06 else 3 if x < 0.66 else 4)
        grp = group or AREAS[area][0]  # type: ignore[index]
        share = s4_share if s4_share is not None else (AREAS[area][2] if area else 0.45)
        app = S4_APP if rnd.random() < share else ECC_APP
        caller = rnd.choice(self.callers)
        short = self._text(rnd, template[0])
        desc = short + ". Reported by the business; please check the logs and advise."
        if rnd.random() < 0.15:
            desc += self._signature(rnd, caller)
        hours = (
            math.exp(rnd.gauss(math.log(MEDIAN_H[pr]), 0.7)) if resolve_h == "draw" else resolve_h  # type: ignore[arg-type]
        )
        return SapTicket(
            number=number("INC", day, i),
            opened=opened,
            area=area,
            group=grp,
            app=app,
            priority=pr,
            short=short[:160],
            desc=desc,
            caller=caller,
            assignee=rnd.choice(self.team) if grp != SERVICE_DESK else None,
            resolve_h=hours,
            category=category,
            component=component,
            pattern=pattern,
            subcategory=template[1],
            reassignments=1 if rnd.random() < 0.2 else 0,
        )

    def tickets_for_day(self, day: date) -> list[SapTicket]:
        out: list[SapTicket] = []
        rnd = self._rnd("bg", day)
        rate = MONTH_INCIDENTS / WEIGHTED_DAYS_PER_MONTH * WEEKDAY_WEIGHT[day.weekday()] * self.scale
        count = int(rate) + (1 if rnd.random() < rate - int(rate) else 0)
        codes = list(AREAS)
        weights = [AREAS[c][1] for c in codes]
        for i in range(count):
            area = rnd.choices(codes, weights=weights, k=1)[0]
            out.append(self._ticket(rnd, day, BLOCK["background"] + i, area, rnd.choice(AREAS[area][3])))
        if day.weekday() >= 5:
            return out
        if self.sp1_start <= day < self.sp1_end:
            r1 = self._rnd("sp1", day)
            for k in range(3):
                hours = None if r1.random() < 0.40 else r1.uniform(24, 96)
                out.append(
                    self._ticket(
                        r1, day, BLOCK["SP1"] + k, "ewm", r1.choice(AREAS["ewm"][3]), pattern="SP1", resolve_h=hours
                    )
                )
        if day in self.sp2_days:
            r2 = self._rnd("sp2", day)
            for k in range(6):
                out.append(
                    self._ticket(
                        r2,
                        day,
                        BLOCK["SP2"] + k,
                        "fi_co",
                        MONTH_END,
                        pattern="SP2",
                        priority=2,
                        resolve_h=r2.uniform(4, 20),
                    )
                )
        if self.sn1_start <= day < self.sn1_end:
            r3 = self._rnd("sn1", day)
            for k in range(8):
                out.append(
                    self._ticket(
                        r3,
                        day,
                        BLOCK["SN1"] + k,
                        "sd",
                        r3.choice(AREAS["sd"][3]),
                        pattern="SN1",
                        resolve_h=r3.uniform(6, 48),
                    )
                )
        recent = self.anchor - timedelta(days=90) <= day < self.anchor
        if recent and (day - self.window_start).days % 3 == 0:
            r4 = self._rnd("sc", day)
            area = r4.choice(list(AREAS))
            template = r4.choice(AREAS[area][3])
            hours = None if r4.random() < 0.3 else r4.uniform(2, 30)
            out.append(
                self._ticket(
                    r4,
                    day,
                    BLOCK["SC1"],
                    None,
                    template,
                    pattern="SC1",
                    resolve_h=hours,
                    group=SERVICE_DESK,
                    category="SAP",
                )
            )
            if (day - self.window_start).days % 6 == 0:
                out.append(
                    self._ticket(
                        r4,
                        day,
                        BLOCK["SC2"],
                        None,
                        template,
                        pattern="SC2",
                        resolve_h=r4.uniform(2, 30),
                        group=SERVICE_DESK,
                        component=r4.choice(COMPONENTS),
                    )
                )
        return out


def _state(t: SapTicket, moment: datetime) -> dict[str, Any]:
    resolved = t.resolved
    target = RESOLUTION_TARGET_H[t.priority]
    if resolved is not None and resolved <= moment:
        closed = resolved + timedelta(days=5)
        closed_at = closed if closed <= moment else None
        return {
            "resolved_at": resolved,
            "closed_at": closed_at,
            "state": "Closed" if closed_at else "Resolved",
            "updated": closed_at or resolved,
            "made_sla": t.resolve_h is not None and t.resolve_h <= target,
        }
    elapsed_h = (moment - t.opened).total_seconds() / 3600
    return {
        "resolved_at": None,
        "closed_at": None,
        "state": random.Random(f"state:{t.number}").choice(["In Progress", "On Hold", "Work in Progress"]),
        "updated": min(moment - timedelta(minutes=30), t.opened + timedelta(hours=elapsed_h * 0.6)),
        "made_sla": elapsed_h <= target,
    }


def _row(t: SapTicket, state: dict[str, Any]) -> list[Any]:
    resolved = state["resolved_at"] is not None
    return [
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
        "Application",
        t.short,
        t.desc,
        "Solved (Permanently)" if resolved else "",
        "Configuration corrected and the document reprocessed." if resolved else "",
        t.caller,
        t.assignee or "",
        t.group,
        t.app[1],
        "",
        "true" if state["made_sla"] else "false",
        t.reassignments,
        0,
        "",
        "",
        "",
        t.component,
    ]


def generate(paths: Paths, req: SynthRequest) -> dict[str, Any]:
    if paths.data_class != "synthetic":
        raise PreconditionFailed("`sed synth` only runs on synthetic/eval profiles, never on the real profile.")
    anchor = req.anchor or req.as_of
    if req.as_of < anchor:
        raise PreconditionFailed("--as-of must be on or after the pattern anchor date")
    window_start = month_start(anchor, req.months)
    moment = datetime(req.as_of.year, req.as_of.month, req.as_of.day, 6, 0, 0)
    inbox = paths.inbox
    inbox.mkdir(parents=True, exist_ok=True)
    if req.clean:
        inbox_manifest.clean(inbox, KEY)

    gen = SapGenerator(req.seed, anchor, window_start, req.as_of, req.scale)
    tickets: list[SapTicket] = []
    day = window_start
    while day < req.as_of:
        tickets += [t for t in gen.tickets_for_day(day) if t.opened < moment]
        day += timedelta(days=1)

    written: list[str] = []
    owner = gen.team[0]
    apps_header = ["number", "name", "u_application_family", "business_criticality", "life_cycle_stage",
                   "it_application_owner", "cost_center", "vendor"]  # fmt: skip
    W.write_csv(
        inbox / "sap_business_apps.csv",
        apps_header,
        [
            [app_id, name, "SAP", "1 - most critical", "Operational", owner, "CC-4100", ""]
            for app_id, name in (ECC_APP, S4_APP)
        ],
    )
    written.append("sap_business_apps.csv")

    by_month: dict[str, list[list[Any]]] = defaultdict(list)
    active: list[list[Any]] = []
    truth: list[list[Any]] = []
    for t in tickets:
        state = _state(t, moment)
        row = _row(t, state)
        by_month[t.opened.strftime("%Y-%m")].append(row)
        if state["resolved_at"] is None:
            active.append(row)
        truth.append([t.number, t.area or "unassigned", "s4" if t.app == S4_APP else "ecc", t.pattern, t.subcategory])
    for label, rows in sorted(by_month.items()):
        name = f"sap_incident_{label}.csv"
        W.write_csv(inbox / name, INCIDENT_FIELDS, rows)
        written.append(name)
    active_name = f"sap_incident_active_{req.as_of.isoformat()}.csv"
    W.write_csv(inbox / active_name, INCIDENT_FIELDS, active)
    written.append(active_name)

    section = {
        "generator_version": GENERATOR_VERSION,
        "seed": req.seed,
        "as_of": req.as_of.isoformat(),
        "anchor": anchor.isoformat(),
        "months": req.months,
        "scale": req.scale,
        "files": {name: file_sha256(inbox / name) for name in sorted(written)},
    }
    inbox_manifest.save_section(inbox, KEY, section)
    truth_dir = _ground_truth(paths.ground_truth / KEY, gen, tickets, truth, moment)
    counts = {"application": 2, "incident": len(tickets), "open_incident": len(active)}
    return {"inbox": str(inbox), "files": len(written), "counts": counts, "ground_truth": truth_dir}


def _ground_truth(
    folder: Path, gen: SapGenerator, tickets: list[SapTicket], truth: list[list[Any]], moment: datetime
) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "ticket_truth.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["number", "area", "landscape", "pattern", "sap_subcategory"])
        writer.writerows(truth)

    def pattern(name: str) -> list[SapTicket]:
        return [t for t in tickets if t.pattern == name]

    patterns = {
        "generator_version": GENERATOR_VERSION,
        "SP1": {
            "area": "ewm",
            "weeks_from": gen.sp1_start.isoformat(),
            "weeks_to": gen.sp1_end.isoformat(),
            "tickets": len(pattern("SP1")),
            "open_at_as_of": sum(1 for t in pattern("SP1") if t.resolved is None or t.resolved > moment),
        },
        "SP2": {"area": "fi_co", "tickets": len(pattern("SP2")), "days": sorted(d.isoformat() for d in gen.sp2_days)},
        "SN1": {
            "area": "sd",
            "tickets": len(pattern("SN1")),
            "from": gen.sn1_start.isoformat(),
            "to": gen.sn1_end.isoformat(),
        },
        "SC1": {"tickets": len(pattern("SC1")), "category": "SAP"},
        "SC2": {"tickets": len(pattern("SC2")), "custom_field": "u_sap_component"},
        "controls": {"SN1": "SD surge with matching closures: no backlog-growth finding"},
    }
    (folder / "patterns.json").write_text(json.dumps(patterns, indent=2), encoding="utf-8")
    (folder / "pii_injections.json").write_text(json.dumps(sorted(set(gen.pii)), indent=0), encoding="utf-8")
    return str(folder)
