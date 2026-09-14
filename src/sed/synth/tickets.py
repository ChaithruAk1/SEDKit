"""Ticket generation (incidents, requests, changes, problems, task SLAs) with planted patterns.

Determinism: every day is generated from its own RNG seeded by (seed, stream, day), and planted patterns are
anchored to ``anchor`` (not ``as_of``). A later ``as_of`` with the same anchor therefore produces a superset of
tickets; only the lifecycle state of still-open tickets moves forward.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from sed.synth import templates as T
from sed.synth.catalog import App, Catalog

EPOCH = date(2020, 1, 1)
RESOLUTION_TARGET_H = {1: 4, 2: 8, 3: 40, 4: 120, 5: 240}
RESPONSE_TARGET_H = {1: 0.5, 2: 1, 3: 8, 4: 24, 5: 48}
RESOLUTION_MEDIAN_H = {1: 2.0, 2: 4.0, 3: 14.0, 4: 45.0, 5: 80.0}
RESOLUTION_SIGMA = {1: 0.6, 2: 0.6, 3: 0.75, 4: 0.7, 5: 0.7}
PRIORITY_LABEL = {1: "1 - Critical", 2: "2 - High", 3: "3 - Moderate", 4: "4 - Low", 5: "5 - Planning"}
IMPACT_LABEL = {1: "1 - High", 2: "2 - Medium", 3: "3 - Low"}
MONTH_INCIDENTS = 4000
MONTH_REQUESTS = 1600
MONTH_CHANGES = 350
MONTH_PROBLEMS = 25
WEEKDAY_WEIGHT = [1.0, 1.0, 1.0, 1.0, 1.0, 0.441, 0.441]
WEIGHTED_DAYS_PER_MONTH = 21.7 + 8.7 * 0.441
P7_APPS = ["Keyring Identity", "Waypoint Intranet", "Northstar CRM", "Atlas HR Core", "Lumen BI"]
P6_APPS = ["Atlas HR Core", "Waypoint Intranet"]
NORDWIND_GROUPS = {"NWD-ERP-L2", "NWD-FIN-L2", "NWD-SCM-L2"}
KEEL_GROUPS = {"KEEL-MFG-L2", "KEEL-ENG-L2"}
NORDWIND_BASELINE = 0.96


@dataclass
class Ticket:
    kind: str
    number: str
    opened: datetime
    app: App | None
    priority: int
    group: str
    caller: str | None
    assignee: str | None
    short: str
    desc: str
    ci: str | None
    business_service: str | None
    category: str | None
    subcategory: str | None
    resolve_h: float | None  # hours from open to resolution; None = stays open
    target_breach: bool | None = None
    reassignments: int = 0
    reopen: int = 0
    close_code: str | None = None
    close_notes: str | None = None
    caused_by: str | None = None
    parent_incident: str | None = None
    problem_id: str | None = None
    change_type: str | None = None
    risk: str | None = None
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    truth: dict[str, Any] = field(default_factory=dict)

    @property
    def resolved(self) -> datetime | None:
        return None if self.resolve_h is None else self.opened + timedelta(hours=self.resolve_h)


@dataclass
class PlanContext:
    seed: int
    anchor: date
    window_start: date
    as_of: date
    scale: float
    catalog: Catalog
    p1_change: str = ""
    p1_problem: str = ""
    p7_parent: str = ""
    p8_change: str = ""
    pii_injections: list[str] = field(default_factory=list)


def day_index(day: date) -> int:
    return (day - EPOCH).days


# Per-day number blocks: background 0000-1999, planted patterns in reserved ranges (no collisions).
BLOCK = {
    "P1": 2000,
    "P2": 3000,
    "P5": 2300,
    "P6": 2400,
    "P7": 2500,
    "P9": 2700,
    "P13": 2800,
    "CTL": 2900,
    "TRICKLE": 3500,
    "SPECIAL": 9000,
}


def number(prefix: str, day: date, i: int) -> str:
    return f"{prefix}{day_index(day):04d}{i:04d}"


def month_start(day: date, back: int) -> date:
    idx = day.year * 12 + day.month - 1 - back
    return date(idx // 12, idx % 12 + 1, 1)


def business_days_of_month(year: int, month: int) -> list[date]:
    d = date(year, month, 1)
    out = []
    while d.month == month:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _typo(text: str, rnd: random.Random) -> str:
    if len(text) < 8 or rnd.random() > 0.05:
        return text
    i = rnd.randrange(1, len(text) - 2)
    return text[:i] + text[i + 1] + text[i] + text[i + 2 :]


def _fill(template: str, rnd: random.Random, app: App | None, ci: str | None) -> str:
    return template.format(
        app=app.name if app else "the application",
        ci=ci or "the server",
        n=rnd.randint(2, 950),
        doc=f"DOC-{rnd.randint(100000, 999999)}",
        site=rnd.choice(T.SITES),
        queue=rnd.choice(T.QUEUES),
        report=rnd.choice(T.REPORTS),
        module=rnd.choice(T.MODULES),
    )


def _priority(rnd: random.Random) -> int:
    x = rnd.random()
    return 1 if x < 0.004 else 2 if x < 0.044 else 3 if x < 0.624 else 4 if x < 0.994 else 5


def _local_time(day: date, rnd: random.Random) -> datetime:
    peak = rnd.random() < 0.55
    hour = rnd.choice([9, 9, 10, 10, 11]) if peak else min(23, max(0, int(rnd.gauss(13.5, 3.5))))
    return datetime(day.year, day.month, day.day, hour, rnd.randrange(60), rnd.randrange(60))


def _signature(rnd: random.Random, name: str) -> str:
    first, last = name.split(" ", 1)
    marker = rnd.choice(["Regards,", "Best regards,", "Cordialement,", "Thanks,"])
    phone = "+33 " + " ".join(str(rnd.randint(1, 9) if k == 0 else rnd.randint(10, 99)) for k in range(5))
    email = f"{first.lower()}.{last.lower().replace(' ', '')}@example.com"
    team = rnd.choice(["Finance Ops", "Plant Support", "Sales Admin", "HR Services"])
    return f"\n\n{marker}\n{name}\n{team}\nTel {phone}\n{email}"


class TicketGenerator:
    def __init__(self, ctx: PlanContext) -> None:
        self.ctx = ctx
        self.cat = ctx.catalog
        apps = self.cat.apps
        self.app_names = [a.name for a in apps]
        self.base_weights = np.array([a.weight for a in apps], dtype=float)
        self.symptom_weights = [s.weight for s in T.SYMPTOMS]
        a = ctx.anchor
        self.p1_change_day = a - timedelta(days=60)
        self.p7_day = a - timedelta(days=26)
        while self.p7_day.weekday() >= 5:
            self.p7_day -= timedelta(days=1)
        self.p8_change_day = a - timedelta(days=75)
        self.golive_day = a - timedelta(days=90)
        self.coincidence_change_day = a - timedelta(days=40)
        self.p9_start = a - timedelta(weeks=10)
        self.p11_quiet_start = month_start(a, 6)  # margin: still quiet for 5 months at an as-of before the anchor
        self.degrade_months = {month_start(a, k): rate for k, rate in ((4, 0.93), (3, 0.86), (2, 0.78), (1, 0.70))}
        days = [self.p1_change_day + timedelta(days=i) for i in range(1, 19)]
        weights = np.array([math.exp(-i / 6) for i in range(len(days))])
        counts = np.random.default_rng([ctx.seed, 101]).multinomial(230, weights / weights.sum())
        self.p1_counts = {d: int(c) for d, c in zip(days, counts, strict=True)}
        self.p5_counts: dict[date, int] = {}
        rng5 = np.random.default_rng([ctx.seed, 105])
        for back in range(1, 13):
            ms = month_start(a, back)
            bdays = business_days_of_month(ms.year, ms.month)[:2]
            total = int(rng5.integers(32, 39))
            self.p5_counts[bdays[0]] = total // 2 + total % 2
            self.p5_counts[bdays[1]] = total // 2
        window_days = [ctx.window_start + timedelta(days=i) for i in range((a - ctx.window_start).days)]
        p6 = np.random.default_rng([ctx.seed, 106]).multinomial(700, np.ones(len(window_days)) / len(window_days))
        self.p6_counts = {d: int(c) for d, c in zip(window_days, p6, strict=True) if c}
        # P2: absolute stream of Nordwind-handled incidents (60 per month, last 8 months) so the vendor SLA decline is
        # measurable at any --scale; SLA outcomes follow the same monthly made-SLA rates as background Nordwind tickets.
        self.p2_counts: dict[date, int] = {}
        rng2 = np.random.default_rng([ctx.seed, 102])
        for back in range(1, 9):
            ms = month_start(a, back)
            bdays = business_days_of_month(ms.year, ms.month)
            for d, c in zip(bdays, rng2.multinomial(60, np.ones(len(bdays)) / len(bdays)), strict=True):
                if c:
                    self.p2_counts[d] = int(c)
        p13 = np.random.default_rng([ctx.seed, 113]).multinomial(400, np.ones(len(window_days)) / len(window_days))
        self.p13_counts = {d: int(c) for d, c in zip(window_days, p13, strict=True) if c}
        ctx.p1_change = number("CHG", self.p1_change_day, BLOCK["SPECIAL"])
        ctx.p1_problem = number("PRB", self.p1_change_day + timedelta(days=14), BLOCK["SPECIAL"])
        ctx.p7_parent = number("INC", self.p7_day, BLOCK["P7"])
        ctx.p8_change = number("CHG", self.p8_change_day, BLOCK["SPECIAL"] + 1)

    # -- helpers ---------------------------------------------------------------------------------------------

    def _rnd(self, stream: str, day: date) -> random.Random:
        return random.Random(f"{self.ctx.seed}:{stream}:{day.isoformat()}")

    def _app_weights(self, day: date) -> np.ndarray:
        w = self.base_weights.copy()
        for idx, name in enumerate(self.app_names):
            if name == "Quarry Data Catalog" and day >= self.p11_quiet_start:
                w[idx] = 0.0
            if name == "Pathway Learning" and day < self.golive_day:
                w[idx] = 0.0
            if name == "Pathway Learning" and self.golive_day <= day < self.golive_day + timedelta(days=14):
                w[idx] *= 4
        return w / w.sum()

    def _pick_app(self, rnd: random.Random, day: date, weights_cache: dict[date, list[float]]) -> App:
        if day not in weights_cache:
            weights_cache[day] = list(np.cumsum(self._app_weights(day)))
        x = rnd.random()
        cum = weights_cache[day]
        lo, hi = 0, len(cum) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if cum[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        return self.cat.apps[lo]

    def _group_for(self, rnd: random.Random, app: App) -> str:
        x = rnd.random()
        if x < 0.70:
            return app.l2_group
        if x < 0.88:
            return self.cat.service_desk
        return app.l3_group if rnd.random() < 0.7 else "IT-INFRA-L3"

    def _assignee(self, rnd: random.Random, group: str) -> str:
        return rnd.choice(self.cat.groups[group].members)

    def _resolution(self, rnd: random.Random, priority: int, group: str, opened: datetime) -> tuple[float, bool]:
        target = RESOLUTION_TARGET_H[priority]
        month = date(opened.year, opened.month, 1)
        if group in NORDWIND_GROUPS:
            rate = self.degrade_months.get(month, NORDWIND_BASELINE)
            slow = 1.3 if month in self.degrade_months else 1.0
            return self._draw_with_rate(rnd, target, rate, slow)
        if group in KEEL_GROUPS:
            noise = random.Random(f"{self.ctx.seed}:keel:{month.isoformat()}").gauss(0, 0.025)
            return self._draw_with_rate(rnd, target, min(0.99, max(0.85, 0.93 + noise)), 1.0)
        hours = math.exp(rnd.gauss(math.log(RESOLUTION_MEDIAN_H[priority]), RESOLUTION_SIGMA[priority]))
        return hours, hours > target

    @staticmethod
    def _draw_with_rate(rnd: random.Random, target: float, made_rate: float, slow: float) -> tuple[float, bool]:
        if rnd.random() < made_rate:
            return target * min(0.97, rnd.uniform(0.08, 0.75) * slow), False
        return target * rnd.uniform(1.05, 2.8), True

    def _symptom(self, rnd: random.Random) -> T.Symptom:
        return rnd.choices(T.SYMPTOMS, weights=self.symptom_weights, k=1)[0]

    def _text(
        self, rnd: random.Random, symptom: T.Symptom, app: App | None, ci: str | None, caller: str
    ) -> tuple[str, str]:
        french = bool(symptom.short_fr) and rnd.random() < 0.10
        short = _fill(rnd.choice(symptom.short_fr if french else symptom.short_en), rnd, app, ci)
        desc = _fill(rnd.choice(symptom.desc), rnd, app, ci) if symptom.desc else short
        if rnd.random() < 0.20:
            desc += _signature(rnd, caller)
        return _typo(short, rnd)[:160], desc

    def _base_incident(
        self,
        rnd: random.Random,
        day: date,
        i: int,
        app: App,
        symptom: T.Symptom,
        pattern: str | None,
        priority: int | None = None,
        group: str | None = None,
    ) -> Ticket:
        opened = _local_time(day, rnd)
        pr = priority or _priority(rnd)
        grp = group or self._group_for(rnd, app)
        caller = rnd.choice(self.cat.people)
        ci = rnd.choice(app.cis) if rnd.random() < 0.9 else None
        short, desc = self._text(rnd, symptom, app, ci, caller)
        hours, breach = self._resolution(rnd, pr, grp, opened)
        category, subcategory = (symptom.sn_category, symptom.sn_subcategory)
        if rnd.random() < 0.15:
            category, subcategory = T.SN_GENERIC_CATEGORY
        degraded = grp in NORDWIND_GROUPS and date(opened.year, opened.month, 1) in self.degrade_months
        reassign_rate = 0.6 * (1.6 if degraded else 1.0)
        reassignments = _poisson(rnd, reassign_rate)
        reopen = 1 if rnd.random() < 0.03 else 0
        if reopen:
            hours *= 1.6
        t = Ticket(
            kind="incident",
            number=number("INC", day, i),
            opened=opened,
            app=app,
            priority=pr,
            group=grp,
            caller=caller,
            assignee=self._assignee(rnd, grp),
            short=short,
            desc=desc,
            ci=ci,
            business_service=app.name if rnd.random() < 0.85 else None,
            category=category,
            subcategory=subcategory,
            resolve_h=hours,
            target_breach=breach,
            reassignments=reassignments,
            reopen=reopen,
            close_code=rnd.choice(T.CLOSE_CODES),
            close_notes=rnd.choice(symptom.close),
            truth={
                "symptom_key": symptom.key,
                "am_category": symptom.am_category,
                "am_subcategory": symptom.am_subcategory,
                "misfiled_as": "none",
                "pattern": pattern or "",
            },
        )
        if grp == "IT-HR-L2" and day >= self.p9_start and rnd.random() < 0.16:
            t.resolve_h = None  # P9: backlog grows in this internal group
            t.truth["pattern"] = t.truth["pattern"] or "P9"
        return t

    # -- daily generation ------------------------------------------------------------------------------------

    def incidents_for_day(self, day: date) -> list[Ticket]:
        rnd = self._rnd("inc", day)
        cache: dict[date, list[float]] = {}
        rate = MONTH_INCIDENTS / WEIGHTED_DAYS_PER_MONTH * WEEKDAY_WEIGHT[day.weekday()] * self.ctx.scale
        if day.month == 8:
            rate *= 0.7
        count = _poisson(rnd, rate)
        out: list[Ticket] = []
        i = 0
        for _ in range(count):
            app = self._pick_app(rnd, day, cache)
            if app.family == "Finance" and _is_month_edge(day) and rnd.random() < 0.25:
                out.append(self._base_incident(rnd, day, i, app, self._symptom(rnd), None))
                i += 1
            symptom = T.GO_LIVE_HOWTO if app.name == "Pathway Learning" and rnd.random() < 0.5 else self._symptom(rnd)
            out.append(self._base_incident(rnd, day, i, app, symptom, None))
            i += 1
            if i >= 1999:
                break
        if day >= self.p9_start and day.weekday() < 5:  # P9: +15% arrivals for the HR L2 group
            hr_rnd = self._rnd("p9", day)
            hr_apps = [a for a in self.cat.apps if a.family == "HR" and a.l2_group == "IT-HR-L2"]
            for k in range(min(_poisson(hr_rnd, 1.2), 99)):
                app = hr_rnd.choice(hr_apps)
                out.append(
                    self._base_incident(
                        hr_rnd, day, BLOCK["P9"] + k, app, self._symptom(hr_rnd), "P9", group="IT-HR-L2"
                    )
                )
        if day.day == 12:  # one incident per live app per month at any --scale, so only P11 is ever "quiet"
            trickle_rnd = self._rnd("trickle", day)
            weights = self._app_weights(day)
            for k, app in enumerate(self.cat.apps):
                if weights[self.app_names.index(app.name)] > 0:
                    out.append(
                        self._base_incident(
                            trickle_rnd, day, BLOCK["TRICKLE"] + k, app, self._symptom(trickle_rnd), None
                        )
                    )
        out += self._pattern_incidents(day)
        return out

    def _pattern_incidents(self, day: date) -> list[Ticket]:
        out: list[Ticket] = []
        orion = self.cat.app("Orion ERP")
        n = self.p1_counts.get(day, 0)
        if n:
            rnd = self._rnd("p1", day)
            for k in range(n):
                t = self._base_incident(
                    rnd, day, BLOCK["P1"] + k, orion, T.P1_INTERFACE_TIMEOUT, "P1", group="NWD-ERP-L2"
                )
                t.ci = "orion-erp-prd-app01"
                if k == 0 and (day - self.p1_change_day).days <= 6:
                    t.caused_by = self.ctx.p1_change
                if day >= self.p1_change_day + timedelta(days=14):
                    t.problem_id = self.ctx.p1_problem
                out.append(t)
        n = self.p2_counts.get(day, 0)
        if n:
            rnd = self._rnd("p2", day)
            nordwind_apps = [a for a in self.cat.apps if a.l2_group in NORDWIND_GROUPS]
            for k in range(n):
                app = rnd.choice(nordwind_apps)
                out.append(
                    self._base_incident(rnd, day, BLOCK["P2"] + k, app, self._symptom(rnd), "P2", group=app.l2_group)
                )
        n = self.p5_counts.get(day, 0)
        if n:
            rnd = self._rnd("p5", day)
            ledger = self.cat.app("Ledgerline Finance")
            for k in range(n):
                t = self._base_incident(
                    rnd, day, BLOCK["P5"] + k, ledger, T.P5_BATCH_FAILURE, "P5", priority=3, group="NWD-FIN-L2"
                )
                t.opened = datetime(day.year, day.month, day.day, rnd.randint(5, 8), rnd.randrange(60))
                out.append(t)
        n = self.p6_counts.get(day, 0)
        if n:
            rnd = self._rnd("p6", day)
            for k in range(n):
                app = self.cat.app(rnd.choice(P6_APPS))
                t = self._base_incident(rnd, day, BLOCK["P6"] + k, app, T.P6_ACCESS_REQUEST, "P6", priority=4)
                if rnd.random() < 0.7:
                    t.category, t.subcategory = "Software", "Access"
                t.truth["misfiled_as"] = "request"
                out.append(t)
        if day == self.p7_day:
            rnd = self._rnd("p7", day)
            keyring = self.cat.app("Keyring Identity")
            parent = self._base_incident(
                rnd, day, BLOCK["P7"], keyring, T.P7_SSO_OUTAGE, "P7", priority=1, group="IT-IAM-L2"
            )
            parent.opened = datetime(day.year, day.month, day.day, 8, 2, 0)
            parent.resolve_h = 5.5
            parent.short = "MAJOR: identity provider outage - SSO unavailable for multiple applications"
            out.append(parent)
            for k in range(160):
                app = self.cat.app(P7_APPS[k % len(P7_APPS)])
                t = self._base_incident(rnd, day, BLOCK["P7"] + 1 + k, app, T.P7_SSO_OUTAGE, "P7", priority=2)
                t.opened = datetime(day.year, day.month, day.day, 8, 5 + k // 4, rnd.randrange(60))
                t.resolve_h = 5.5 - (t.opened.hour - 8) - t.opened.minute / 60 + rnd.uniform(0.1, 0.8)
                t.parent_incident = parent.number if rnd.random() < 0.85 else None
                out.append(t)
        n = self.p13_counts.get(day, 0)
        if n:
            rnd = self._rnd("p13", day)
            for k in range(n):
                app = self._pick_app(rnd, day, {})
                person = rnd.choice(self.cat.people)
                first, last = person.split(" ", 1)
                email = f"{first.lower()}.{last.lower().replace(' ', '')}@example.org"
                phone = f"+44 20 {rnd.randint(1000, 9999)} {rnd.randint(1000, 9999)}"
                t = self._base_incident(rnd, day, BLOCK["P13"] + k, app, self._symptom(rnd), "P13")
                t.desc = f"{t.desc}\nPlease contact {person} ({email}, {phone}) who can reproduce it."
                t.truth["pii"] = [person, email, phone]
                self.ctx.pii_injections += [person, email, phone]
                out.append(t)
        if self.coincidence_change_day < day <= self.coincidence_change_day + timedelta(days=3):
            rnd = self._rnd("ctl-coincidence", day)
            anvil = self.cat.app("Anvil Quality")
            for k in range(10):
                t = self._base_incident(
                    rnd, day, BLOCK["CTL"] + k, anvil, self._symptom(rnd), "CTL-coincidental-change"
                )
                t.ci = "anvil-quality-prd-app01"
                out.append(t)
        return out

    def requests_for_day(self, day: date) -> list[Ticket]:
        rnd = self._rnd("req", day)
        cache: dict[date, list[float]] = {}
        rate = MONTH_REQUESTS / WEIGHTED_DAYS_PER_MONTH * WEEKDAY_WEIGHT[day.weekday()] * self.ctx.scale
        out = []
        for i in range(min(_poisson(rnd, rate), 1999)):
            app = self._pick_app(rnd, day, cache)
            item, text = rnd.choice(T.REQUEST_ITEMS)
            grp = self.cat.service_desk if rnd.random() < 0.6 else app.l2_group
            caller = rnd.choice(self.cat.people)
            short = _fill(text, rnd, app, None)
            out.append(
                Ticket(
                    kind="sc_req_item",
                    number=number("RITM", day, i),
                    opened=_local_time(day, rnd),
                    app=app,
                    priority=4,
                    group=grp,
                    caller=caller,
                    assignee=self._assignee(rnd, grp),
                    short=short,
                    desc=f"{short}. Requested via the portal.",
                    ci=None,
                    business_service=app.name if rnd.random() < 0.9 else None,
                    category=item,
                    subcategory=None,
                    resolve_h=rnd.uniform(2, 120) if rnd.random() > 0.02 else None,
                )
            )
        return out

    def changes_for_day(self, day: date) -> list[Ticket]:
        rnd = self._rnd("chg", day)
        cache: dict[date, list[float]] = {}
        rate = MONTH_CHANGES / WEIGHTED_DAYS_PER_MONTH * WEEKDAY_WEIGHT[day.weekday()] * self.ctx.scale
        out = []
        for i in range(min(_poisson(rnd, rate), 1999)):
            app = self._pick_app(rnd, day, cache)
            out.append(self._change(rnd, day, i, app, rnd.choice(T.CHANGE_TEXTS), None))
        if day == self.p1_change_day:
            t = self._change(
                rnd, day, BLOCK["SPECIAL"], self.cat.app("Orion ERP"), "Upgrade GL interface middleware for {app}", "P1"
            )
            t.close_code, t.ci = "successful_issues", "orion-erp-prd-app01"
            t.close_notes = "Implemented; minor issues observed with posting performance."
            out.append(t)
        if day == self.p8_change_day:
            t = self._change(
                rnd, day, BLOCK["SPECIAL"] + 1, self.cat.app("Atlas PLM"), "Migrate {app} to new hosting platform", "P8"
            )
            t.close_code, t.ci = "successful", "atlas-plm-prd-app01"
            out.append(t)
        if day == self.coincidence_change_day:
            t = self._change(
                rnd,
                day,
                BLOCK["SPECIAL"] + 2,
                self.cat.app("Forge MES"),
                "Database maintenance on {ci}",
                "CTL-coincidental-change",
            )
            t.close_code, t.ci = "successful", "forge-mes-prd-db01"
            out.append(t)
        return out

    def _change(self, rnd: random.Random, day: date, i: int, app: App, text: str, pattern: str | None) -> Ticket:
        ci = rnd.choice(app.cis)
        opened = _local_time(day, rnd)
        start = opened + timedelta(days=rnd.randint(1, 10), hours=rnd.randint(0, 6))
        duration = rnd.uniform(1, 6)
        x = rnd.random()
        code = "successful" if x < 0.90 else "successful_issues" if x < 0.97 else "unsuccessful"
        grp = app.l3_group if rnd.random() < 0.5 else "IT-INFRA-L3"
        return Ticket(
            kind="change_request",
            number=number("CHG", day, i),
            opened=opened,
            app=app,
            priority=3,
            group=grp,
            caller=None,
            assignee=self._assignee(rnd, grp),
            short=_fill(text, rnd, app, ci),
            desc="Planned change. Implementation and backout plans attached.",
            ci=ci,
            business_service=app.name,
            category=None,
            subcategory=None,
            resolve_h=(start - opened).total_seconds() / 3600 + duration + rnd.uniform(1, 48),
            close_code=code,
            close_notes="Change implemented as planned." if code == "successful" else "See review notes.",
            change_type=rnd.choices(["normal", "standard", "emergency"], weights=[50, 45, 5])[0],
            risk=rnd.choice(["Low", "Moderate", "High"]),
            planned_start=start,
            planned_end=start + timedelta(hours=duration),
            truth={"pattern": pattern or ""},
        )

    def problems_for_day(self, day: date) -> list[Ticket]:
        rnd = self._rnd("prb", day)
        cache: dict[date, list[float]] = {}
        rate = MONTH_PROBLEMS / 30.4 * self.ctx.scale
        out = []
        for i in range(min(_poisson(rnd, rate), 1999)):
            app = self._pick_app(rnd, day, cache)
            out.append(self._problem(rnd, day, i, app, rnd.choice(T.PROBLEM_TEXTS), None))
        if day == self.p1_change_day + timedelta(days=14):
            t = self._problem(
                rnd, day, BLOCK["SPECIAL"], self.cat.app("Orion ERP"), "Invoice posting timeouts in {app}", "P1"
            )
            t.resolve_h = None
            out.append(t)
        return out

    def _problem(self, rnd: random.Random, day: date, i: int, app: App, text: str, pattern: str | None) -> Ticket:
        grp = app.l3_group
        return Ticket(
            kind="problem",
            number=number("PRB", day, i),
            opened=_local_time(day, rnd),
            app=app,
            priority=3,
            group=grp,
            caller=None,
            assignee=self._assignee(rnd, grp),
            short=_fill(text, rnd, app, None),
            desc="Problem raised to investigate recurring incidents.",
            ci=rnd.choice(app.cis),
            business_service=app.name,
            category="Software",
            subcategory=None,
            resolve_h=rnd.uniform(24 * 5, 24 * 60) if rnd.random() < 0.8 else None,
            close_notes="Root cause identified and fixed.",
            truth={"pattern": pattern or ""},
        )


def _poisson(rnd: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 60:
        return max(0, round(rnd.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rnd.random()
        if p <= limit:
            return k
        k += 1


def _is_month_edge(day: date) -> bool:
    nxt = day + timedelta(days=3)
    return day.day <= 2 or nxt.month != day.month
