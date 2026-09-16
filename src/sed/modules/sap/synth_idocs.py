"""Synthetic SAP IDocs for the sap module: daily IDoc monitor exports for the production systems EP1 (ECC) and HP1
(S/4HANA) over the 90 days before the export moment. Fictional partners and documents; standard SAP status codes.

Times are local (Europe/Paris). Background volume scales with --scale; planted patterns have absolute counts and are
anchored to the anchor date (ground_truth/sap/patterns.json, section "idocs"):

* IP1 INVOIC errors after the failed CP1 import: 60 outbound INVOIC IDocs on HP1 fail (status 26) within 36 hours after
  the CP1 production import; 35 are reprocessed 30 to 72 hours later, 25 stay in error.
* IP2 ORDERS errors growing for one partner: inbound ORDERS IDocs from PARTNER_0007 on EP1 fail (status 51) 2, 4, 7,
  11, 16 and 24 times in the 6 complete weeks before the anchor week, and none is reprocessed.
* IN1 quick reprocessing (control): 80 outbound MATMAS IDocs on EP1 fail (status 29) late on the Wednesday of the last
  complete week, so the day's export shows them in error, and all are reprocessed 4 to 10 hours later.

Exports show each IDoc's status at the end of the day, so an error fixed the same day never appears in them.
* IN2 cutover weekend (control): 600 extra IDocs on HP1 on the Saturday five weeks before the anchor week, all
  processed without errors.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sed.modules.sap.synth_changes import CP1_IMPORT_DAYS_BEFORE_ANCHOR
from sed.synth import writers as W

DAYS = 90
SYSTEMS = {"EP1": 150.0, "HP1": 250.0}  # IDocs per weekday at scale 1.0
WEEKEND_SHARE = 0.3
# message type -> (direction, basic type, partner type, share, systems)
TYPES: dict[str, tuple[str, str, str, float, tuple[str, ...]]] = {
    "ORDERS": ("inbound", "ORDERS05", "KU", 0.25, ("EP1", "HP1")),
    "ORDRSP": ("outbound", "ORDERS05", "KU", 0.10, ("EP1", "HP1")),
    "INVOIC": ("outbound", "INVOIC02", "KU", 0.20, ("EP1", "HP1")),
    "DESADV": ("outbound", "DELVRY07", "KU", 0.15, ("EP1", "HP1")),
    "MATMAS": ("outbound", "MATMAS05", "LS", 0.10, ("EP1", "HP1")),
    "DEBMAS": ("outbound", "DEBMAS07", "LS", 0.05, ("EP1", "HP1")),
    "WMMBXY": ("inbound", "WMMBID02", "LS", 0.10, ("HP1",)),
    "CREMAS": ("outbound", "CREMAS06", "LI", 0.05, ("EP1", "HP1")),
}
OK = {"inbound": ("53", "Application document posted"), "outbound": ("03", "Data passed to port OK")}
WAITING = {
    "inbound": ("64", "IDoc ready to be transferred to application"),
    "outbound": ("30", "IDoc ready for dispatch"),
}
CLOSED = {"inbound": ("68", "Error, no further processing"), "outbound": ("31", "Error, no further processing")}
ERRORS = {
    "inbound": [
        ("51", "Sold-to party {n} not maintained for sales area S100/01/00"),
        ("51", "Material {n} not maintained in plant P100"),
        ("56", "IDoc with errors added: partner profile not found"),
    ],
    "outbound": [
        ("26", "EDI: Syntax error in IDoc (segment E1EDK01 missing)"),
        ("29", "Error in ALE service: no receiver determined for message type {mt}"),
        ("02", "Error passing data to port"),
    ],
}
ERROR_RATE = {"inbound": 0.015, "outbound": 0.01}
IP1_TEXT = "EDI: Syntax error in IDoc (mandatory segment E1EDP01 missing) for billing document {n}"
IP2_PARTNER = "PARTNER_0007"
IP2_WEEKLY = (2, 4, 7, 11, 16, 24)


@dataclass
class SynthIdoc:
    system_id: str
    number: int
    message_type: str
    partner: str
    created: datetime
    events: list[tuple[datetime, str, str]] = field(default_factory=list)  # (moment, status code, text)
    pattern: str = ""

    @property
    def docnum(self) -> str:
        return f"{self.number:016d}"

    def state_at(self, moment: datetime) -> tuple[datetime, str, str] | None:
        past = [e for e in self.events if e[0] <= moment]
        return past[-1] if past else None


class IdocGenerator:
    def __init__(self, seed: int, anchor: date, moment: datetime, scale: float, people: list[str]):
        self.seed, self.anchor, self.moment, self.scale, self.people = seed, anchor, moment, scale, people
        self.anchor_monday = anchor - timedelta(days=anchor.weekday())
        self.pii: list[str] = []
        self._seq = {"EP1": 3_000_000_000, "HP1": 7_000_000_000}

    def _rnd(self, stream: str, key: Any) -> random.Random:
        return random.Random(f"{self.seed}:sap-idoc:{stream}:{key}")

    def _new(
        self, rnd: random.Random, system: str, message_type: str, created: datetime, pattern: str = ""
    ) -> SynthIdoc:
        self._seq[system] += 1
        kind = TYPES[message_type][2]
        partner = f"PARTNER_{rnd.randint(1, 40):04d}" if kind in ("KU", "LI") else f"{system}CLNT100"
        return SynthIdoc(system, self._seq[system], message_type, partner, created, pattern=pattern)

    def _flow(
        self,
        rnd: random.Random,
        idoc: SynthIdoc,
        *,
        error: tuple[str, str] | None = None,
        fix_after_h: float | None = None,
        close_after_h: float | None = None,
    ) -> SynthIdoc:
        direction = TYPES[idoc.message_type][0]
        t = idoc.created
        idoc.events.append((t, *WAITING[direction]))
        t += timedelta(minutes=rnd.uniform(1, 10))
        if error is None:
            idoc.events.append((t, *OK[direction]))
            return idoc
        code, text = error
        idoc.events.append((t, code, text.format(n=rnd.randint(100000, 999999), mt=idoc.message_type)))
        if fix_after_h is not None:
            idoc.events.append((t + timedelta(hours=fix_after_h), *OK[direction]))
        elif close_after_h is not None:
            idoc.events.append((t + timedelta(hours=close_after_h), *CLOSED[direction]))
        return idoc

    def background(self) -> list[SynthIdoc]:
        out: list[SynthIdoc] = []
        start = self.moment.date() - timedelta(days=DAYS)
        types = list(TYPES)
        for offset in range(DAYS + 1):
            day = start + timedelta(days=offset)
            for system, per_day in SYSTEMS.items():
                rnd = self._rnd(f"bg-{system}", day.isoformat())
                rate = per_day * self.scale * (WEEKEND_SHARE if day.weekday() >= 5 else 1.0)
                count = int(rate) + (1 if rnd.random() < rate - int(rate) else 0)
                allowed = [t for t in types if system in TYPES[t][4]]
                weights = [TYPES[t][3] for t in allowed]
                for _ in range(count):
                    message_type = rnd.choices(allowed, weights=weights, k=1)[0]
                    created = datetime(day.year, day.month, day.day, rnd.randrange(24), rnd.randrange(60))
                    idoc = self._new(rnd, system, message_type, created)
                    direction = TYPES[message_type][0]
                    if rnd.random() >= ERROR_RATE[direction]:
                        self._flow(rnd, idoc)
                    else:
                        code, text = rnd.choice(ERRORS[direction])
                        if rnd.random() < 0.03:
                            name = rnd.choice(self.people)
                            text += f" - please contact {name}"
                            self.pii.append(name)
                        if rnd.random() < 0.8:
                            self._flow(rnd, idoc, error=(code, text), fix_after_h=rnd.uniform(1, 20))
                        else:
                            self._flow(rnd, idoc, error=(code, text), close_after_h=rnd.uniform(26, 72))
                    out.append(idoc)
        return out

    def patterns(self) -> list[SynthIdoc]:
        out: list[SynthIdoc] = []
        a = datetime(self.anchor.year, self.anchor.month, self.anchor.day)

        rnd = self._rnd("ip1", 0)
        imported = a - timedelta(days=CP1_IMPORT_DAYS_BEFORE_ANCHOR) + timedelta(hours=18, minutes=25)
        for k in range(60):
            created = imported + timedelta(minutes=30 + k * 35)
            idoc = self._new(rnd, "HP1", "INVOIC", created, "IP1")
            fix = rnd.uniform(30, 72) if k < 35 else None
            self._flow(rnd, idoc, error=("26", IP1_TEXT), fix_after_h=fix)
            out.append(idoc)

        rnd = self._rnd("ip2", 0)
        for weeks_back, count in zip(range(6, 0, -1), IP2_WEEKLY, strict=True):
            monday = self.anchor_monday - timedelta(weeks=weeks_back)
            for k in range(count):
                day = monday + timedelta(days=k % 5)
                created = datetime(day.year, day.month, day.day, 8 + (k % 9), (7 * k) % 60)
                idoc = self._new(rnd, "EP1", "ORDERS", created, "IP2")
                idoc.partner = IP2_PARTNER
                self._flow(rnd, idoc, error=ERRORS["inbound"][0])
                out.append(idoc)

        rnd = self._rnd("in1", 0)
        wednesday = self.anchor_monday - timedelta(days=5)
        for k in range(80):
            created = datetime(wednesday.year, wednesday.month, wednesday.day, 21, 0) + timedelta(minutes=2 * k)
            idoc = self._new(rnd, "EP1", "MATMAS", created, "IN1")
            self._flow(rnd, idoc, error=ERRORS["outbound"][1], fix_after_h=rnd.uniform(4, 10))
            out.append(idoc)

        rnd = self._rnd("in2", 0)
        saturday = self.anchor_monday - timedelta(days=37)
        types = [t for t in TYPES if "HP1" in TYPES[t][4]]
        for k in range(600):
            created = datetime(saturday.year, saturday.month, saturday.day, 6, 0) + timedelta(minutes=k)
            idoc = self._new(rnd, "HP1", types[k % len(types)], created, "IN2")
            self._flow(rnd, idoc)
            out.append(idoc)
        return out


HEADER = [
    "system_id", "docnum", "direction", "message_type", "basic_type", "partner_type", "partner_number", "status_code",
    "status_text", "created_at", "status_at",
]  # fmt: skip


def write_exports(inbox: Path, idocs: list[SynthIdoc], moment: datetime) -> list[str]:
    """One file per day: the IDocs created or changed that day with their status at the end of the day (the last file
    at the export moment)."""
    by_day: dict[date, list[SynthIdoc]] = {}
    for idoc in idocs:
        for day in sorted({when.date() for when, _, _ in idoc.events if when < moment}):
            by_day.setdefault(day, []).append(idoc)
    written = []
    for day in sorted(by_day):
        cut = min(datetime(day.year, day.month, day.day) + timedelta(days=1), moment)
        rows = []
        for idoc in by_day[day]:
            state = idoc.state_at(cut - timedelta(microseconds=1))
            if state is None:
                continue
            when, code, text = state
            direction, basic, partner_type, _, _ = TYPES[idoc.message_type]
            rows.append(
                [idoc.system_id, idoc.docnum, "2" if direction == "inbound" else "1", idoc.message_type, basic,
                 partner_type, idoc.partner, code, text, W.fmt_dt(idoc.created), W.fmt_dt(when)]
            )  # fmt: skip
        name = f"sap_idocs_{day.isoformat()}.csv"
        W.write_csv(inbox / name, HEADER, sorted(rows, key=lambda r: (r[0], r[1])))
        written.append(name)
    return written


def truth_rows(idocs: list[SynthIdoc], moment: datetime) -> list[list[Any]]:
    rows = []
    for i in idocs:
        if i.pattern:
            state = i.state_at(moment)
            rows.append([i.system_id, i.docnum, i.pattern, i.message_type, i.partner, state[1] if state else ""])
    return sorted(rows)


def patterns_section(idocs: list[SynthIdoc], anchor: date, cp1_change_id: str) -> dict[str, Any]:
    def of(name: str) -> list[SynthIdoc]:
        return [i for i in idocs if i.pattern == name]

    monday = anchor - timedelta(days=anchor.weekday())
    return {
        "IP1": {"system": "HP1", "message_type": "INVOIC", "errors": len(of("IP1")), "open_at_as_of": 25},
        "IP2": {
            "system": "EP1",
            "message_type": "ORDERS",
            "partner": IP2_PARTNER,
            "weekly_errors": list(IP2_WEEKLY),
            "weeks_from": (monday - timedelta(weeks=6)).isoformat(),
        },
        "IN1": {
            "system": "EP1",
            "message_type": "MATMAS",
            "errors": len(of("IN1")),
            "day": (monday - timedelta(days=5)).isoformat(),
        },
        "IN2": {"system": "HP1", "idocs": len(of("IN2")), "day": (monday - timedelta(days=37)).isoformat()},
        "expected_findings": [
            f"sap_idoc_risk:spike:HP1:{cp1_change_id}",
            "sap_idoc_risk:backlog:HP1:INVOIC",
            "sap_idoc_risk:backlog:EP1:ORDERS",
            "sap_idoc_risk:growth:EP1:ORDERS:" + IP2_PARTNER,
            "sap_idoc_risk:aged:HP1",
            "sap_idoc_risk:aged:EP1",
        ],
    }
