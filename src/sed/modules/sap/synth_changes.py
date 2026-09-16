"""Synthetic ChaRM data for the sap module: change documents (weekly delta exports), transport imports (monthly
exports of import events) and the SAP Jira stories that trace to them. Fictional; generic SAP vocabulary only.

Times are local (Europe/Paris) like the ticket exports. Background changes scale with --scale; planted patterns have
absolute counts and are anchored to the anchor date (ground_truth/sap/patterns.json, section "changes"):

* CP1 failed urgent import: an urgent SD change on S/4HANA whose production import (8 days before the anchor, 18:30)
  ends with return code 8; the incident generator plants 14 SD incidents on S/4HANA in the next two days.
* CP2 urgent creep in MM: 16 MM changes in the 8 weeks before the anchor week (10 urgent), 14 in the 8 weeks
  before that (1 urgent).
* CP3 stuck in test: 4 PP/QM changes on ECC "To Be Tested" since 45 days before the anchor.
* CP4 waiting for production: 3 FI/CO changes on ECC, 6 transports imported into QA 25 days before the anchor and not
  into production.
* CP5 without Jira: 5 open normal EWM changes with no Jira story either way.
* CN1 release weekend (control): 10 FI/CO changes, 30 transports imported into ECC production on the Saturday 16 days
  before the anchor week, return codes 0 or 4, no incidents planted after it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sed.synth import writers as W

STATUS = {
    "requested": "To Be Approved",
    "approved": "Approved",
    "in_development": "In Development",
    "in_test": "To Be Tested",
    "ready_for_production": "Authorized for Production",
    "in_production": "Imported into Production",
    "confirmed": "Confirmed",
    "withdrawn": "Withdrawn",
}
TYPES = {"normal": "SMMJ", "urgent": "SMHF", "standard": "SMAD", "defect_correction": "SMTM"}
SYSTEMS = {"ecc": ("ED1", "EQ1", "EP1"), "s4": ("HD1", "HQ1", "HP1")}
CYCLES = {"ecc": "ECC Maintenance", "s4": "S4 Release"}
JIRA_PROJECT = "SAPS4"
APP_NAMES = {"ecc": "SAP ECC", "s4": "SAP S/4HANA"}
# area -> (share of background changes, S/4 share, components, title templates)
AREAS: dict[str, tuple[float, float, list[str], list[str]]] = {
    "fi_co": (0.20, 0.45, ["FI-GL", "FI-AP", "CO-PC"], [
        "Adjust GL account determination for company code {cc}",
        "New withholding tax code for vendors in company code {cc}",
        "Correct cost center hierarchy for controlling area {ca}",
    ]),
    "sd": (0.16, 0.45, ["SD-SLS", "SD-BIL"], [
        "Pricing condition changes for sales organisation {so}",
        "New output type for billing documents",
        "Credit check settings for customer group {cg}",
    ]),
    "mm": (0.14, 0.45, ["MM-PUR", "MM-IM"], [
        "Release strategy for purchase orders above limit {limit}",
        "New movement type for stock transfers",
        "Adjust tolerance keys for invoice verification",
    ]),
    "pp_qm": (0.10, 0.45, ["PP-SFC", "QM-IM"], [
        "Inspection plan changes for plant {plant}",
        "Confirmation profile for production orders in plant {plant}",
    ]),
    "ewm": (0.10, 0.90, ["SCM-EWM-WOP", "SCM-EWM-DLP"], [
        "Warehouse process type for outbound deliveries in warehouse {wh}",
        "Storage bin determination change in warehouse {wh}",
    ]),
    "basis": (0.10, 0.45, ["BC-CCM-BTC", "BC-CST"], [
        "Background job schedule changes for batch window {win}",
        "Kernel parameter change for dialog work processes",
    ]),
    "security": (0.08, 0.45, ["BC-SEC-USR", "GRC-AC"], [
        "New role for accounts payable clerks",
        "Adjust authorisation objects of role {role}",
    ]),
    "integration": (0.08, 0.45, ["BC-MID-ALE", "BC-MID-RFC"], [
        "Partner profile for message type {mtype}",
        "RFC destination change for the planning system",
    ]),
    "abap": (0.04, 0.45, ["BC-DWB-CEX"], [
        "Enhancement of custom report {prog}",
        "Correct custom user exit {prog}",
    ]),
}  # fmt: skip
BLOCK_BACKGROUND = 10_000
BLOCK_PATTERNS = 9_000
CHANGES_PER_WEEK = 10.0
URGENT_SHARE = 0.06
CP1_IMPORT_DAYS_BEFORE_ANCHOR = 8  # the incident generator plants the CP1 incidents in the two days after


@dataclass
class SynthChange:
    number: int
    change_type: str
    area: str
    landscape: str
    title: str
    component: str
    requester: str
    developer: str
    manager: str
    created: datetime
    events: list[tuple[datetime, str]] = field(default_factory=list)  # (moment, stage)
    imports: list[tuple[str, str, datetime, int]] = field(default_factory=list)  # (transport, system, moment, rc)
    transports: list[str] = field(default_factory=list)
    jira: bool = True
    pattern: str = ""

    @property
    def change_id(self) -> str:
        return f"80000{self.number:05d}"

    def stage_at(self, moment: datetime) -> tuple[datetime, str] | None:
        past = [e for e in self.events if e[0] <= moment]
        return past[-1] if past else None


class ChangeGenerator:
    def __init__(self, seed: int, anchor: date, window_start: date, moment: datetime, scale: float, people: list[str]):
        self.seed, self.anchor, self.window_start, self.moment, self.scale = seed, anchor, window_start, moment, scale
        self.people = people
        self.anchor_monday = anchor - timedelta(days=anchor.weekday())
        self.pii: list[str] = []
        self._transport_seq = 0

    def _rnd(self, stream: str, key: Any) -> random.Random:
        return random.Random(f"{self.seed}:sap-charm:{stream}:{key}")

    def _title(self, rnd: random.Random, area: str) -> str:
        template = rnd.choice(AREAS[area][3])
        return template.format(
            cc=rnd.choice(["1000", "1100", "2000"]),
            ca=rnd.choice(["A000", "B000"]),
            so=rnd.choice(["S100", "S200"]),
            cg=rnd.choice(["01", "02", "05"]),
            limit=rnd.choice(["10000", "50000"]),
            plant=rnd.choice(["P100", "P200", "P310"]),
            wh=rnd.choice(["W001", "W002"]),
            win=rnd.choice(["night", "month-end"]),
            role=f"Z_{rnd.choice(['FI', 'SD', 'MM'])}_{rnd.randint(10, 99)}",
            mtype=rnd.choice(["ORDERS", "INVOIC", "DESADV"]),
            prog=f"Z_{rnd.choice(['SD', 'FI', 'MM'])}_{rnd.randint(100, 999)}",
        )

    def _new(
        self, rnd: random.Random, number: int, change_type: str, area: str, created: datetime, *, landscape: str = ""
    ) -> SynthChange:
        land = landscape or ("s4" if rnd.random() < AREAS[area][1] else "ecc")
        requester = rnd.choice(self.people)
        title = self._title(rnd, area)
        if rnd.random() < 0.05:
            title += f" (requested by {requester})"
            self.pii.append(requester)
        return SynthChange(
            number=number,
            change_type=change_type,
            area=area,
            landscape=land,
            title=title,
            component=rnd.choice(AREAS[area][2]),
            requester=requester,
            developer=rnd.choice(self.people[-40:]),
            manager=rnd.choice(self.people[-10:]),
            created=created,
        )

    def _transports(self, change: SynthChange, count: int) -> list[str]:
        dev = SYSTEMS[change.landscape][0]
        out = []
        for _ in range(count):
            self._transport_seq += 1
            out.append(f"{dev}K9{self._transport_seq:05d}")
        change.transports = out
        return out

    def _lifecycle(
        self,
        rnd: random.Random,
        change: SynthChange,
        *,
        stop_after: str | None = None,
        withdraw: bool = False,
        durations: dict[str, float] | None = None,
        prod_rc: int | None = None,
    ) -> SynthChange:
        """Walk the stages from creation with the given (or type-typical) durations in days."""
        urgent = change.change_type in ("urgent", "defect_correction")
        d = durations or (
            {"requested": rnd.uniform(0.1, 0.5), "approved": 0.05, "in_development": rnd.uniform(0.3, 1.5),
             "in_test": rnd.uniform(0.2, 1.0), "ready_for_production": rnd.uniform(0.1, 0.5),
             "in_production": rnd.uniform(1, 3)}
            if urgent
            else {"requested": rnd.uniform(1, 4), "approved": rnd.uniform(0.5, 2), "in_development": rnd.uniform(3, 12),
                  "in_test": rnd.uniform(2, 6), "ready_for_production": rnd.uniform(0.5, 4),
                  "in_production": rnd.uniform(2, 7)}
        )  # fmt: skip
        transports = change.transports or self._transports(change, rnd.choice([1, 1, 2, 3]))
        _, qa, prod = SYSTEMS[change.landscape]
        moment = change.created
        change.events = [(moment, "requested")]
        order = ["requested", "approved", "in_development", "in_test", "ready_for_production", "in_production"]
        for stage, following in zip(order, [*order[1:], "confirmed"], strict=True):
            if stop_after == stage:
                break
            moment = moment + timedelta(days=d[stage])
            if withdraw and following == "in_test":
                change.events.append((moment, "withdrawn"))
                return change
            change.events.append((moment, following))
            if following == "in_test":
                for k, transport in enumerate(transports):
                    at = moment + timedelta(minutes=10 + k)
                    rc = rnd.choice([0, 0, 4])
                    if rnd.random() < 0.03:
                        change.imports.append((transport, qa, at, 8))
                        at, rc = at + timedelta(hours=4), 4
                    change.imports.append((transport, qa, at, rc))
            if following == "in_production":
                for k, transport in enumerate(transports):
                    at = moment - timedelta(minutes=5 - k)
                    rc = prod_rc if prod_rc is not None else rnd.choice([0, 0, 0, 4])
                    change.imports.append((transport, prod, at, rc))
        return change

    # -- background -------------------------------------------------------------------------------------------------

    def background(self) -> list[SynthChange]:
        out: list[SynthChange] = []
        codes = list(AREAS)
        weights = [AREAS[c][0] for c in codes]
        week = self.window_start - timedelta(days=self.window_start.weekday())
        number = BLOCK_BACKGROUND
        while week < self.anchor:
            rnd = self._rnd("bg", week.isoformat())
            rate = CHANGES_PER_WEEK * self.scale
            count = int(rate) + (1 if rnd.random() < rate - int(rate) else 0)
            for _ in range(count):
                area = rnd.choices(codes, weights=weights, k=1)[0]
                x = rnd.random()
                change_type = (
                    "urgent" if x < URGENT_SHARE else "standard" if x < 0.20 else "defect_correction" if x < 0.24
                    else "normal"
                )  # fmt: skip
                day = week + timedelta(days=rnd.randrange(5))
                created = datetime(day.year, day.month, day.day, rnd.choice([9, 10, 11, 14, 15]), rnd.randrange(60))
                if day < self.window_start:
                    continue
                change = self._new(rnd, number, change_type, area, created)
                number += 1
                self._lifecycle(rnd, change, withdraw=change_type == "normal" and rnd.random() < 0.04)
                out.append(change)
            week += timedelta(days=7)
        return out

    # -- planted patterns -------------------------------------------------------------------------------------------

    def patterns(self) -> list[SynthChange]:
        out: list[SynthChange] = []
        number = BLOCK_PATTERNS
        a = datetime(self.anchor.year, self.anchor.month, self.anchor.day)

        def nxt() -> int:
            nonlocal number
            number += 1
            return number

        # CP1: urgent SD change on S/4HANA, production import with return code 8.
        rnd = self._rnd("cp1", 0)
        imported = a - timedelta(days=CP1_IMPORT_DAYS_BEFORE_ANCHOR) + timedelta(hours=18, minutes=30)
        cp1 = self._new(rnd, nxt(), "urgent", "sd", imported - timedelta(hours=25), landscape="s4")
        cp1.title = "Urgent correction of billing document output for sales organisation S100"
        durations = {"requested": 2 / 24, "approved": 1 / 24, "in_development": 4 / 24, "in_test": 16 / 24,
                     "ready_for_production": 2 / 24, "in_production": 30}  # fmt: skip
        self._transports(cp1, 1)
        self._lifecycle(rnd, cp1, durations=durations, prod_rc=8, stop_after="in_production")
        cp1.pattern = "CP1"
        out.append(cp1)

        # CP2: MM urgent share rises in the 8 weeks before the anchor week.
        for window, count, urgent in ((1, 16, 10), (2, 14, 1)):
            start = self.anchor_monday - timedelta(weeks=8 * window)
            rnd = self._rnd("cp2", window)
            flags = [True] * urgent + [False] * (count - urgent)
            rnd.shuffle(flags)
            for k, is_urgent in enumerate(flags):
                day = start + timedelta(days=(k * 56) // count)
                day += timedelta(days=(7 - day.weekday()) % 7 if day.weekday() >= 5 else 0)
                created = datetime(day.year, day.month, day.day, 10, 15)
                change = self._new(rnd, nxt(), "urgent" if is_urgent else "normal", "mm", created)
                self._lifecycle(rnd, change)
                change.pattern = "CP2"
                out.append(change)

        # CP3: PP/QM changes stuck in test on ECC.
        rnd = self._rnd("cp3", 0)
        for k in range(4):
            created = a - timedelta(days=70 - k, hours=-10)
            change = self._new(rnd, nxt(), "normal", "pp_qm", created, landscape="ecc")
            test_at = a - timedelta(days=45, hours=-11 - k)
            days_to_test = (test_at - created).total_seconds() / 86400
            durations = {"requested": 2, "approved": 1, "in_development": days_to_test - 3}
            self._lifecycle(rnd, change, durations={**durations, "in_test": 99}, stop_after="in_test")
            change.pattern = "CP3"
            out.append(change)

        # CP4: FI/CO transports waiting for production on ECC.
        rnd = self._rnd("cp4", 0)
        for k in range(3):
            created = a - timedelta(days=40, hours=-9 - k)
            change = self._new(rnd, nxt(), "normal", "fi_co", created, landscape="ecc")
            self._transports(change, 2)
            test_at = a - timedelta(days=25, hours=-10 - k)
            ready_at = a - timedelta(days=5, hours=-12)
            durations = {
                "requested": 3,
                "approved": 1,
                "in_development": (test_at - created).total_seconds() / 86400 - 4,
                "in_test": (ready_at - test_at).total_seconds() / 86400,
            }
            self._lifecycle(rnd, change, durations=durations, stop_after="ready_for_production")
            change.pattern = "CP4"
            out.append(change)

        # CP5: open normal EWM changes without any Jira story.
        rnd = self._rnd("cp5", 0)
        for k in range(5):
            created = a - timedelta(days=20 - k, hours=-9)
            change = self._new(rnd, nxt(), "normal", "ewm", created, landscape="s4")
            self._lifecycle(rnd, change, durations={"requested": 1, "approved": 1}, stop_after="in_development")
            change.jira = False
            change.pattern = "CP5"
            out.append(change)

        # CN1: a clean release weekend into ECC production.
        rnd = self._rnd("cn1", 0)
        saturday = self.anchor_monday - timedelta(days=16)
        release = datetime(saturday.year, saturday.month, saturday.day, 10, 0)
        for k in range(10):
            created = release - timedelta(days=30 - k)
            change = self._new(rnd, nxt(), "normal", "fi_co", created, landscape="ecc")
            self._transports(change, 3)
            to_test = (release - timedelta(days=9) - created).total_seconds() / 86400
            durations = {"requested": 2, "approved": 1, "in_development": to_test - 3, "in_test": 6,
                         "ready_for_production": 3 + (k * 10) / (24 * 60), "in_production": 4}  # fmt: skip
            self._lifecycle(rnd, change, durations=durations)
            change.imports = [
                (t, s, m, min(rc, 4)) for t, s, m, rc in change.imports
            ]  # a clean weekend: no failed import, not even one fixed later
            change.pattern = "CN1"
            out.append(change)
        return out


def _export_row(change: SynthChange, moment: datetime, stage: str, external_ref: str) -> list[Any]:
    return [
        change.change_id,
        TYPES[change.change_type],
        change.title,
        STATUS[stage],
        "1 - Very High" if change.change_type == "urgent" else "3 - Medium",
        change.component,
        CYCLES[change.landscape],
        W.fmt_dt(change.created),
        W.fmt_dt(moment),
        change.requester,
        change.developer,
        change.manager,
        external_ref,
        "",
    ]


CHANGE_HEADER = [
    "change_id", "transaction_type", "title", "status", "priority", "component", "cycle", "created_at", "changed_at",
    "requester", "developer", "change_manager", "external_ref", "ticket_ref",
]  # fmt: skip
TRANSPORT_HEADER = [
    "transport", "system_id", "change_id", "transport_type", "description", "owner", "released_at", "import_status",
    "return_code", "imported_at",
]  # fmt: skip
JIRA_HEADER = [
    "Issue key", "Project key", "Project name", "Summary", "Issue Type", "Status", "Status Category", "Priority",
    "Assignee", "Created", "Updated", "Resolved", "Labels", "Labels", "Component/s",
]  # fmt: skip
JIRA_FMT = "%d/%b/%y %I:%M %p"


def write_exports(inbox: Path, changes: list[SynthChange], moment: datetime, as_of: date) -> list[str]:
    """Weekly change deltas (state at each Sunday night, the last file at the export moment), monthly transport import
    events and one Jira export. Returns the file names written."""
    written: list[str] = []
    jira_keys = {c.change_id: f"{JIRA_PROJECT}-{i + 1}" for i, c in enumerate(sorted(changes, key=lambda c: c.created))}
    first_monday = min(c.created for c in changes).date()
    first_monday -= timedelta(days=first_monday.weekday())
    week = first_monday
    while week <= as_of:
        end = datetime(week.year, week.month, week.day) + timedelta(days=7)
        cut = min(end, moment)
        start = datetime(week.year, week.month, week.day)
        rows = []
        for change in changes:
            if any(start <= m < cut for m, _ in change.events):
                last_moment, stage = change.stage_at(cut - timedelta(microseconds=1)) or change.events[0]
                ref = jira_keys[change.change_id] if change.jira and change.number % 2 == 0 else ""
                rows.append(_export_row(change, last_moment, stage, ref))
        if rows:
            label = (cut.date() - timedelta(days=1)).isoformat() if cut == end else as_of.isoformat()
            name = f"sap_charm_changes_{label}.csv"
            W.write_csv(inbox / name, CHANGE_HEADER, sorted(rows))
            written.append(name)
        week += timedelta(days=7)

    by_month: dict[str, list[list[Any]]] = {}
    for change in changes:
        released = {t: min(m for tt, _, m, _ in change.imports if tt == t) for t, *_ in change.imports}
        for transport, system, moment_i, rc in change.imports:
            if moment_i >= moment:
                continue
            status = "Imported" if rc == 0 else "Imported with warnings" if rc == 4 else "Imported with errors"
            by_month.setdefault(moment_i.strftime("%Y-%m"), []).append(
                [transport, system, change.change_id, "Workbench", change.title, change.developer,
                 W.fmt_dt(released[transport] - timedelta(hours=1)), status, rc, W.fmt_dt(moment_i)]
            )  # fmt: skip
    for label, rows in sorted(by_month.items()):
        name = f"sap_transport_imports_{label}.csv"
        W.write_csv(inbox / name, TRANSPORT_HEADER, sorted(rows, key=lambda r: (r[9], r[0], r[1])))
        written.append(name)

    jira_rows = []
    for change in sorted(changes, key=lambda c: c.created):
        if not change.jira or change.change_type not in ("normal", "urgent", "defect_correction"):
            continue
        created = change.created - timedelta(days=2)
        if created >= moment:
            continue
        now = change.stage_at(moment)
        stage = now[1] if now else "requested"
        done = stage in ("confirmed", "withdrawn")
        updated = max(created, now[0] if now else created)
        jira_rows.append(
            [jira_keys[change.change_id], JIRA_PROJECT, "SAP S/4 and ECC changes", change.title, "Story",
             "Done" if done else "In Progress", "Done" if done else "In Progress", "Medium", change.developer,
             created.strftime(JIRA_FMT), updated.strftime(JIRA_FMT), updated.strftime(JIRA_FMT) if done else "",
             "sap", f"charm-{change.change_id}", APP_NAMES[change.landscape]]
        )  # fmt: skip
    name = f"jira_export_{JIRA_PROJECT}.csv"
    W.write_csv(inbox / name, JIRA_HEADER, jira_rows)
    written.append(name)
    return written


def truth_rows(changes: list[SynthChange], moment: datetime) -> list[list[Any]]:
    rows = []
    for c in sorted(changes, key=lambda c: c.change_id):
        now = c.stage_at(moment)
        rows.append([c.change_id, c.pattern, c.change_type, c.area, c.landscape, now[1] if now else "", int(c.jira)])
    return rows


def patterns_section(changes: list[SynthChange], anchor: date) -> dict[str, Any]:
    def of(name: str) -> list[SynthChange]:
        return [c for c in changes if c.pattern == name]

    cp1 = of("CP1")[0]
    return {
        "CP1": {
            "change_id": cp1.change_id,
            "transport": cp1.transports[0],
            "system": "HP1",
            "return_code": 8,
            "imported_at_local": W.fmt_dt(next(m for _, s, m, _ in cp1.imports if s == "HP1")),
            "incidents": 14,
        },
        "CP2": {"area": "mm", "recent": {"changes": 16, "urgent": 10}, "previous": {"changes": 14, "urgent": 1}},
        "CP3": {"area": "pp_qm", "changes": sorted(c.change_id for c in of("CP3")), "in_test_days": 45},
        "CP4": {
            "landscape": "ecc",
            "changes": sorted(c.change_id for c in of("CP4")),
            "transports": sorted(t for c in of("CP4") for t in c.transports),
        },
        "CP5": {"area": "ewm", "changes": sorted(c.change_id for c in of("CP5"))},
        "CN1": {
            "area": "fi_co",
            "landscape": "ecc",
            "changes": sorted(c.change_id for c in of("CN1")),
            "transports": 30,
            "release_day": (anchor - timedelta(days=anchor.weekday()) - timedelta(days=16)).isoformat(),
        },
        "expected_findings": [
            f"sap_change_risk:failed_import:{cp1.transports[0]}:HP1",
            "sap_change_risk:urgent_ratio:mm",
            "sap_change_risk:stuck:pp_qm",
            "sap_change_risk:waiting:ecc",
        ],
    }
