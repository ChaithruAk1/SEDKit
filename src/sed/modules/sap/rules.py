"""SAP rule findings ("system-detected"), the sap module's `rule_findings` provider (see sed.rule_findings).

* sap_backlog_risk:growth:<area>: an SAP area received more tickets than it closed in most complete weeks of the window.
* sap_backlog_risk:aged:<area>: an SAP area holds many open tickets older than 30 days.

Thresholds live in config/sap/risk_rules.yaml.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from pydantic import Field

from sed.calendar import as_of_end_utc, parse_period, week_label
from sed.errors import ValidationFailed
from sed.modules.sap.queries import l3
from sed.modules.sap.scope import load_scope
from sed.paths import Paths
from sed.settings import StrictModel, load_layered, load_settings


class GrowthRule(StrictModel):
    enabled: bool = True
    window_weeks: int = Field(8, ge=2, le=52)
    min_weeks_growing: int = Field(6, ge=1)
    min_net_growth: int = Field(15, ge=1)


class AgedRule(StrictModel):
    enabled: bool = True
    min_tickets: int = Field(10, ge=1)


class Rules(StrictModel):
    area_backlog_growth: GrowthRule = Field(default_factory=GrowthRule)
    area_aged_backlog: AgedRule = Field(default_factory=AgedRule)


def load_rules(paths: Paths | None) -> Rules:
    from pydantic import ValidationError

    try:
        return Rules.model_validate(load_layered("sap/risk_rules.yaml", paths).get("rules") or {})
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed("Invalid sap/risk_rules.yaml", errors) from exc


def compute(conn: sqlite3.Connection, paths: Paths | None, as_of: date) -> list[dict[str, Any]]:
    scope = load_scope(paths)
    if not scope.configured:
        return []
    scope = scope.resolve(conn)
    rules = load_rules(paths)
    settings = load_settings(paths)
    tz = settings.reporting_tz
    out: list[dict[str, Any]] = []

    growth = rules.area_backlog_growth
    if growth.enabled:
        last = parse_period(week_label(as_of - timedelta(days=7)), tz, settings.fiscal_year_start)
        weeks = [last.previous(k) for k in range(growth.window_weeks - 1, -1, -1)]
        per_area: dict[str, list[dict[str, Any]]] = {}
        for row in l3.flow_by_area(conn, scope, weeks):
            per_area.setdefault(row["area"], []).append(row)
        for code, rows in per_area.items():
            growing = sum(1 for r in rows if r["net"] > 0)
            net = sum(r["net"] for r in rows)
            if growing >= growth.min_weeks_growing and net >= growth.min_net_growth:
                label = rows[0]["label"]
                out.append(
                    {
                        "stable_key": f"sap_backlog_risk:growth:{code}",
                        "kind": "sap_backlog_risk",
                        "subject_type": "sap_area",
                        "subject_id": code,
                        "severity": "high" if net >= 2 * growth.min_net_growth else "medium",
                        "title": f"SAP {label} backlog growing: +{net} tickets over {growth.window_weeks} weeks "
                        f"({growing} weeks with more arrivals than closures)",
                        "evidence": [
                            {"fact_key": f"sap.area.{code}.net_growth", "value": net},
                            {"fact_key": f"sap.area.{code}.weeks_growing", "value": growing},
                            {"fact_key": f"sap.area.{code}.window_end", "value": weeks[-1].label},
                        ],
                    }
                )

    aged = rules.area_aged_backlog
    if aged.enabled:
        at = as_of_end_utc(as_of, tz)
        for row in l3.backlog(conn, scope, at)["by_area"]:
            count = row["d31_90"] + row["d90p"]
            if count >= aged.min_tickets:
                code = row["area"]
                out.append(
                    {
                        "stable_key": f"sap_backlog_risk:aged:{code}",
                        "kind": "sap_backlog_risk",
                        "subject_type": "sap_area",
                        "subject_id": code,
                        "severity": "high" if count >= 2 * aged.min_tickets else "medium",
                        "title": f"SAP {row['label']}: {count} open tickets older than 30 days",
                        "evidence": [
                            {"fact_key": f"sap.area.{code}.aged_30d", "value": count},
                            {"fact_key": f"sap.area.{code}.open", "value": row["total"]},
                        ],
                    }
                )
    return out
