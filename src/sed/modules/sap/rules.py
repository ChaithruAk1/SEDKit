"""SAP rule findings ("system-detected"), the sap module's `rule_findings` provider (see sed.rule_findings).

* sap_backlog_risk:growth:<area>: an SAP area received more tickets than it closed in most complete weeks of the window.
* sap_backlog_risk:aged:<area>: an SAP area holds many open tickets older than 30 days.
* sap_change_risk:stuck:<area>: several open changes of an area kept their status longer than charm.yaml allows.
* sap_change_risk:urgent_ratio:<area>: the urgent share of an area's new changes is high and rising.
* sap_change_risk:failed_import:<transport>:<system>: a production import ended at or above the failure return code.
* sap_change_risk:waiting:<landscape>: many tested transports imported into QA and not into production.
* sap_idoc_risk:backlog:<system>:<message type>: many IDocs of one message type in error on one system.
* sap_idoc_risk:growth:<system>:<message type>:<partner>: persistent IDoc errors of one partner rise.
* sap_idoc_risk:aged:<system>: many IDoc errors open for longer than idoc.yaml allows.
* sap_idoc_risk:spike:<system>:<change>: persistent IDoc errors jump after a production import into the system.

Thresholds live in config/sap/risk_rules.yaml (definitions in config/sap/charm.yaml and config/sap/idoc.yaml).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from pydantic import Field

from sed.calendar import as_of_end_utc, iso_utc, parse_period, week_label
from sed.errors import ValidationFailed
from sed.modules.sap.charm import load_charm
from sed.modules.sap.idoc import load_idoc
from sed.modules.sap.queries import changes, idocs, l3
from sed.modules.sap.scope import Scope, load_scope
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


class StuckRule(StrictModel):
    enabled: bool = True
    min_changes: int = Field(2, ge=1)


class UrgentRatioRule(StrictModel):
    enabled: bool = True
    window_weeks: int = Field(8, ge=2, le=26)
    min_changes: int = Field(8, ge=1)
    min_ratio_pct: float = Field(30, gt=0, le=100)
    min_rise_pp: float = Field(15, ge=0, le=100)


class FailedImportRule(StrictModel):
    enabled: bool = True
    lookback_days: int = Field(30, ge=1, le=365)


class WaitingRule(StrictModel):
    enabled: bool = True
    min_transports: int = Field(5, ge=1)


class IdocBacklogRule(StrictModel):
    enabled: bool = True
    min_errors: int = Field(25, ge=1)


class IdocGrowthRule(StrictModel):
    enabled: bool = True
    min_errors_week: int = Field(20, ge=1)
    min_growth_pct: float = Field(50, ge=0)


class IdocAgedRule(StrictModel):
    enabled: bool = True
    min_errors: int = Field(10, ge=1)


class IdocSpikeRule(StrictModel):
    enabled: bool = True
    min_lift: int = Field(20, ge=1)
    lookback_days: int = Field(30, ge=1, le=365)


class Rules(StrictModel):
    area_backlog_growth: GrowthRule = Field(default_factory=GrowthRule)
    area_aged_backlog: AgedRule = Field(default_factory=AgedRule)
    change_stuck: StuckRule = Field(default_factory=StuckRule)
    urgent_ratio: UrgentRatioRule = Field(default_factory=UrgentRatioRule)
    failed_production_import: FailedImportRule = Field(default_factory=FailedImportRule)
    waiting_for_production: WaitingRule = Field(default_factory=WaitingRule)
    idoc_error_backlog: IdocBacklogRule = Field(default_factory=IdocBacklogRule)
    idoc_error_growth: IdocGrowthRule = Field(default_factory=IdocGrowthRule)
    idoc_aged_errors: IdocAgedRule = Field(default_factory=IdocAgedRule)
    idoc_spike_after_import: IdocSpikeRule = Field(default_factory=IdocSpikeRule)


def load_rules(paths: Paths | None) -> Rules:
    from pydantic import ValidationError

    try:
        return Rules.model_validate(load_layered("sap/risk_rules.yaml", paths).get("rules") or {})
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed("Invalid sap/risk_rules.yaml", errors) from exc


def compute(conn: sqlite3.Connection, paths: Paths | None, as_of: date) -> list[dict[str, Any]]:
    scope = load_scope(paths).resolve(conn)
    rules = load_rules(paths)
    settings = load_settings(paths)
    # Ticket risks need the SAP ticket scope; change and IDoc risks come from their own exports and config.
    backlog = _backlog_findings(conn, as_of, scope, rules, settings) if scope.configured else []
    return (
        backlog
        + _change_findings(conn, paths, as_of, scope, rules, settings)
        + _idoc_findings(conn, paths, as_of, scope, rules, settings)
    )


def _backlog_findings(
    conn: sqlite3.Connection, as_of: date, scope: Scope, rules: Rules, settings: Any
) -> list[dict[str, Any]]:
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


def _finding(
    stable_key: str,
    subject_type: str,
    subject_id: str,
    severity: str,
    title: str,
    evidence: dict[str, Any],
    kind: str = "sap_change_risk",
) -> dict[str, Any]:
    return {
        "stable_key": stable_key,
        "kind": kind,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "severity": severity,
        "title": title,
        "evidence": [{"fact_key": k, "value": v} for k, v in evidence.items()],
    }


def _change_findings(
    conn: sqlite3.Connection, paths: Paths | None, as_of: date, scope: Scope, rules: Rules, settings: Any
) -> list[dict[str, Any]]:
    wanted = (rules.change_stuck, rules.urgent_ratio, rules.failed_production_import, rules.waiting_for_production)
    if not any(r.enabled for r in wanted):
        return []
    charm = load_charm(paths, scope)
    at = as_of_end_utc(as_of, settings.reporting_tz)
    cs = changes.load(conn, charm, at)
    out: list[dict[str, Any]] = []

    if rules.change_stuck.enabled:
        by_area: dict[str, list[dict[str, Any]]] = {}
        for row in changes.stuck(cs, changes.select(cs)):
            by_area.setdefault(row["area"], []).append(row)
        for code, rows in sorted(by_area.items()):
            if len(rows) < rules.change_stuck.min_changes:
                continue
            oldest = max(r["days_in_status"] for r in rows)
            worst = max(r["days_in_status"] / r["threshold_days"] for r in rows)
            label = scope.area_labels[code]
            out.append(
                _finding(
                    f"sap_change_risk:stuck:{code}",
                    "sap_area",
                    code,
                    "high" if worst >= 2 else "medium",
                    f"SAP {label}: {len(rows)} changes stuck in their status (oldest {oldest:.0f} days)",
                    {f"sap.changes.{code}.stuck": len(rows), f"sap.changes.{code}.stuck_oldest_days": oldest},
                )
            )

    ratio_rule = rules.urgent_ratio
    if ratio_rule.enabled:
        n = ratio_rule.window_weeks
        last = parse_period(week_label(as_of - timedelta(days=7)), settings.reporting_tz, settings.fiscal_year_start)
        weeks = [last.previous(k) for k in range(2 * n - 1, -1, -1)]
        for row in changes.urgent_by_area(cs, weeks[n:], weeks[:n]):
            ratio, before = row["ratio_pct"], row["previous_ratio_pct"] or 0.0
            if row["created"] < ratio_rule.min_changes or ratio is None or ratio < ratio_rule.min_ratio_pct:
                continue
            if ratio - before < ratio_rule.min_rise_pp:
                continue
            code = row["area"]
            out.append(
                _finding(
                    f"sap_change_risk:urgent_ratio:{code}",
                    "sap_area",
                    code,
                    "high" if ratio >= 2 * ratio_rule.min_ratio_pct else "medium",
                    f"SAP {row['label']}: {ratio:.0f}% of new changes urgent in the last {n} weeks (was {before:.0f}%)",
                    {
                        f"sap.changes.{code}.urgent_ratio_pct": ratio,
                        f"sap.changes.{code}.urgent_ratio_previous_pct": before,
                        f"sap.changes.{code}.created": row["created"],
                        f"sap.changes.{code}.urgent": row["urgent"],
                    },
                )
            )

    if rules.failed_production_import.enabled:
        since = iso_utc(at - timedelta(days=rules.failed_production_import.lookback_days))
        hours = charm.config.thresholds.incident_window_hours
        after = {
            (r["change_id"], r["system_id"]): r["incidents"]
            for r in changes.incidents_after_imports(conn, cs, since, iso_utc(at), limit=100_000)
        }
        for row in changes.failed_imports(cs, since):
            if row["role"] != "prod":
                continue
            count = after.get((row["change_id"], row["system_id"]), 0)
            transport, system_id = row["transport"], row["system_id"]
            title = (
                f"Failed production import: transport {transport} into {system_id} (return code {row['return_code']})"
            )
            if row["change_id"]:
                title += f" for change {row['change_id']}"
            if count:
                title += f"; {count} SAP incidents within {hours} h"
            out.append(
                _finding(
                    f"sap_change_risk:failed_import:{transport}:{system_id}",
                    "sap_transport",
                    transport,
                    "high",
                    title,
                    {
                        f"sap.transport.{transport}.return_code": row["return_code"],
                        f"sap.transport.{transport}.incidents_after": count,
                    },
                )
            )

    if rules.waiting_for_production.enabled:
        limit = charm.config.thresholds.waiting_for_production_days
        by_landscape: dict[str, list[dict[str, Any]]] = {}
        for row in changes.waiting_for_production(cs):
            by_landscape.setdefault(row["landscape"], []).append(row)
        for code, rows in sorted(by_landscape.items()):
            if len(rows) < rules.waiting_for_production.min_transports:
                continue
            oldest = max(r["days_waiting"] for r in rows)
            label = scope.landscape_labels.get(code, code)
            out.append(
                _finding(
                    f"sap_change_risk:waiting:{code}",
                    "sap_landscape",
                    code,
                    "high" if oldest >= 2 * limit else "medium",
                    f"{label}: {len(rows)} tested transports waiting for production "
                    f"(oldest {oldest:.0f} days since the QA import)",
                    {f"sap.transports.{code}.waiting": len(rows), f"sap.transports.{code}.waiting_oldest_days": oldest},
                )
            )
    return out


def _idoc_findings(
    conn: sqlite3.Connection, paths: Paths | None, as_of: date, scope: Scope, rules: Rules, settings: Any
) -> list[dict[str, Any]]:
    wanted = (rules.idoc_error_backlog, rules.idoc_error_growth, rules.idoc_aged_errors, rules.idoc_spike_after_import)
    if not any(r.enabled for r in wanted):
        return []
    idoc = load_idoc(paths, scope)
    at = as_of_end_utc(as_of, settings.reporting_tz)
    ids = idocs.load(conn, idoc, at)
    kind = "sap_idoc_risk"
    out: list[dict[str, Any]] = []

    if rules.idoc_error_backlog.enabled:
        for row in idocs.backlog_by_type(ids, ids.errors):
            if row["errors"] < rules.idoc_error_backlog.min_errors:
                continue
            key = f"{row['system_id']}:{row['message_type']}"
            out.append(
                _finding(
                    f"sap_idoc_risk:backlog:{key}",
                    "sap_idoc_type",
                    key,
                    "high" if row["errors"] >= 2 * rules.idoc_error_backlog.min_errors else "medium",
                    f"{row['system_id']} {row['message_type']}: {row['errors']} IDocs in error "
                    f"(oldest {row['oldest_hours'] / 24:.0f} days, {row['partners']} "
                    f"{'partner' if row['partners'] == 1 else 'partners'})",
                    {f"sap.idocs.{key}.errors": row["errors"], f"sap.idocs.{key}.aged": row["aged"]},
                    kind,
                )
            )

    growth = rules.idoc_error_growth
    if growth.enabled:
        last = parse_period(week_label(as_of - timedelta(days=7)), settings.reporting_tz, settings.fiscal_year_start)
        previous = [last.previous(k) for k in range(4, 0, -1)]
        counts: dict[tuple[str, str | None, str | None], list[int]] = {}
        for e in ids.errors:
            if not ids.persistent(e):
                continue
            slot = counts.setdefault((e.system_id, e.message_type, e.partner), [0] * 5)
            for i, p in enumerate([*previous, last]):
                if p.start_iso <= e.first_error_at < p.end_iso:
                    slot[i] += 1
        for (system_id, message_type, partner), weeks in sorted(counts.items(), key=lambda kv: str(kv[0])):
            now, avg = weeks[-1], sum(weeks[:-1]) / 4
            if now < growth.min_errors_week or now < avg * (1 + growth.min_growth_pct / 100):
                continue
            key = f"{system_id}:{message_type}:{partner}"
            out.append(
                _finding(
                    f"sap_idoc_risk:growth:{key}",
                    "sap_idoc_partner",
                    key,
                    "high" if now >= 2 * max(avg, 1) else "medium",
                    f"{system_id} {message_type} from {partner}: {now} persistent IDoc errors in {last.label} "
                    f"(4-week average {avg:.1f})",
                    {f"sap.idocs.{key}.persistent_week": now, f"sap.idocs.{key}.persistent_avg4w": round(avg, 2)},
                    kind,
                )
            )

    if rules.idoc_aged_errors.enabled:
        aged_h = idoc.config.thresholds.aged_error_hours
        by_system: dict[str, list[float]] = {}
        for e in ids.errors:
            if e.is_open and ids.age_hours(e) > aged_h:
                by_system.setdefault(e.system_id, []).append(ids.age_hours(e))
        for system_id, ages in sorted(by_system.items()):
            if len(ages) < rules.idoc_aged_errors.min_errors:
                continue
            out.append(
                _finding(
                    f"sap_idoc_risk:aged:{system_id}",
                    "sap_system",
                    system_id,
                    "high" if len(ages) >= 2 * rules.idoc_aged_errors.min_errors else "medium",
                    f"{system_id}: {len(ages)} IDoc errors open for more than {aged_h} hours "
                    f"(oldest {max(ages) / 24:.0f} days)",
                    {f"sap.idocs.{system_id}.aged": len(ages), f"sap.idocs.{system_id}.oldest_hours": max(ages)},
                    kind,
                )
            )

    spike = rules.idoc_spike_after_import
    if spike.enabled:
        cs = changes.load(conn, load_charm(paths, scope), at)
        since = iso_utc(at - timedelta(days=spike.lookback_days))
        hours = idoc.config.thresholds.spike_window_hours
        for row in idocs.spikes_after_imports(ids, cs, since, iso_utc(at), min_lift=spike.min_lift):
            ref = row["change_id"] or row["transport"]
            key = f"{row['system_id']}:{ref}"
            change = f"change {row['change_id']}" if row["change_id"] else "a transport"
            out.append(
                _finding(
                    f"sap_idoc_risk:spike:{key}",
                    "sap_system",
                    row["system_id"],
                    "high",
                    f"{row['system_id']}: {row['errors']} persistent IDoc errors within {hours} h after the production "
                    f"import of {change} ({row['errors_before']} before)",
                    {
                        f"sap.idocs.{key}.errors_after": row["errors"],
                        f"sap.idocs.{key}.errors_before": row["errors_before"],
                    },
                    kind,
                )
            )
    return out
