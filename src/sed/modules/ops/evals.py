"""Offline evals of the ops AI skills against the synthetic ground truth (ground_truth/ticket_truth.csv, patterns.json).

`sed ops eval-triage | eval-recurring | eval-risks <run>` print scores only (counts, rates with Wilson 95% intervals,
pass/fail per gate) and never ticket text or numbers. Each result is also written to `runs/<run_id>/eval.json`, and the
previous eval of the same skill is returned for comparison, so a skill change can be judged against the last passing
run. Thresholds come from evals/thresholds.yaml (`ops-triage`, `ops-recurring`, `ops-risks`).
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

import yaml

from sed import db
from sed.ai.stats import wilson_interval
from sed.errors import PreconditionFailed, ValidationFailed
from sed.ingest.freshness import data_as_of
from sed.paths import Paths, repo_root
from sed.settings import load_settings

THRESHOLDS_FILE = "evals/thresholds.yaml"
CLUSTER_PATTERNS = ("P1", "P5", "P7")
HISTORY_DAYS = 365


def thresholds(section: str) -> dict[str, float]:
    path = repo_root() / THRESHOLDS_FILE
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationFailed(f"Cannot read {THRESHOLDS_FILE}: {exc}") from exc
    values = data.get(section)
    if not isinstance(values, dict) or not all(isinstance(v, int | float) for v in values.values()):
        raise ValidationFailed(f"{THRESHOLDS_FILE}: section '{section}' must map names to numbers")
    return dict(values)


def _rate(ok: int, n: int) -> dict[str, Any]:
    if n == 0:
        return {"n": 0, "rate": None, "ci_low": None, "ci_high": None}
    p = ok / n
    low, high = wilson_interval(p, n)
    return {"n": n, "rate": round(p, 4), "ci_low": round(low, 4), "ci_high": round(high, 4)}


def _truth(paths: Paths) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    if paths.data_class != "synthetic":
        raise PreconditionFailed("Ops ground truth exists only on synthetic and eval profiles")
    folder = paths.ground_truth
    tickets_file, patterns_file = folder / "ticket_truth.csv", folder / "patterns.json"
    if not tickets_file.is_file() or not patterns_file.is_file():
        raise PreconditionFailed(f"No ops ground truth: run `sed synth --profile {paths.profile}` first")
    with tickets_file.open(encoding="utf-8", newline="") as fh:
        tickets = {row["number"]: row for row in csv.DictReader(fh)}
    return tickets, json.loads(patterns_file.read_text(encoding="utf-8"))


def _run(conn: Any, run_id: str, skills: tuple[str, ...]) -> Any:
    run = conn.execute("SELECT run_id, run_seq, skill, skill_hash, status FROM ai_run WHERE run_id = ?", (run_id,))
    row = run.fetchone()
    if row is None:
        raise PreconditionFailed(f"Unknown AI run '{run_id}'")
    if row["skill"] not in skills:
        raise PreconditionFailed(f"Run '{run_id}' is a {row['skill']} run, not {' or '.join(skills)}")
    return row


def _finish(paths: Paths, run: Any, section: str, scores: dict[str, Any], gates: dict[str, bool]) -> dict[str, Any]:
    result = {
        "run_id": run["run_id"],
        "skill": run["skill"],
        "skill_hash": run["skill_hash"],
        "run_status": run["status"],
        **scores,
        "thresholds": thresholds(section),
        "checks": gates,
        "passed": all(gates.values()),
    }
    previous = previous_eval(paths, run["skill"], run["run_seq"])
    if previous is not None:
        result["previous"] = {k: previous.get(k) for k in ("run_id", "skill_hash", "passed", "checks") if k in previous}
    folder = paths.runs / run["run_id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "eval.json").write_bytes((json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return result


def previous_eval(paths: Paths, skill: str, run_seq: int) -> dict[str, Any] | None:
    """The eval.json of the newest earlier run (by run_seq) of the same skill that has one."""
    conn = db.connect(paths.db, readonly=True)
    try:
        earlier = [
            r[0]
            for r in conn.execute(
                "SELECT run_id FROM ai_run WHERE skill = ? AND run_seq < ? ORDER BY run_seq DESC", (skill, run_seq)
            )
        ]
    finally:
        conn.close()
    for run_id in earlier:
        file = paths.runs / run_id / "eval.json"
        if not file.is_file():
            continue
        try:
            return json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


# -- triage --------------------------------------------------------------------------------------------------------


def evaluate_triage(paths: Paths, run_id: str) -> dict[str, Any]:
    truth, _ = _truth(paths)
    limits = thresholds("ops-triage")
    conn = db.connect(paths.db, readonly=True)
    try:
        run = _run(conn, run_id, ("sed-triage-batch", "sed-triage-open"))
        labels = conn.execute(
            "SELECT t.number, l.am_category, l.am_subcategory, l.symptom_key, l.misfiled_as, l.confidence "
            "FROM ai_ticket_label l JOIN ticket t ON t.ticket_id = l.ticket_id WHERE l.run_id = ?",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    scored = [
        (lb, truth[lb["number"]]) for lb in labels if lb["number"] in truth and truth[lb["number"]]["am_category"]
    ]
    category = sum(1 for lb, tr in scored if lb["am_category"] == tr["am_category"])
    subcategory = sum(
        1
        for lb, tr in scored
        if lb["am_category"] == tr["am_category"] and (lb["am_subcategory"] or "") == tr["am_subcategory"]
    )
    predicted = [(lb, tr) for lb, tr in scored if lb["misfiled_as"] == "request"]
    actual = [(lb, tr) for lb, tr in scored if tr["misfiled_as"] == "request"]
    both = sum(1 for lb, tr in predicted if tr["misfiled_as"] == "request")
    keys_by_symptom: dict[str, Counter[str]] = defaultdict(Counter)
    for lb, tr in scored:
        if tr["symptom_key"] and lb["symptom_key"]:
            keys_by_symptom[tr["symptom_key"]][lb["symptom_key"]] += 1
    eligible = {k: c for k, c in keys_by_symptom.items() if sum(c.values()) >= 5}
    consistent = sum(1 for c in eligible.values() if len(c) <= 2)
    confident = [(lb, tr) for lb, tr in scored if (lb["confidence"] or 0) >= 0.8]
    scores = {
        "labels": len(labels),
        "scored": len(scored),
        "category": _rate(category, len(scored)),
        "subcategory": _rate(subcategory, len(scored)),
        "misfiled_precision": _rate(both, len(predicted)),
        "misfiled_recall": _rate(both, len(actual)),
        "symptom_key_consistency": _rate(consistent, len(eligible)),
        "confident_category": _rate(
            sum(1 for lb, tr in confident if lb["am_category"] == tr["am_category"]), len(confident)
        ),
    }
    gates = {
        "min_scored": len(scored) >= limits.get("min_scored", 50),
        "category_accuracy": (scores["category"]["rate"] or 0) >= limits.get("min_category_accuracy", 0.85),
    }
    if len(actual) >= limits.get("min_misfiled_items", 20):
        gates["misfiled_precision"] = (scores["misfiled_precision"]["rate"] or 0) >= limits.get(
            "min_misfiled_precision", 0.8
        )
        gates["misfiled_recall"] = (scores["misfiled_recall"]["rate"] or 0) >= limits.get("min_misfiled_recall", 0.8)
    if eligible:
        gates["symptom_key_consistency"] = (scores["symptom_key_consistency"]["rate"] or 0) >= limits.get(
            "min_symptom_key_consistency", 0.8
        )
    return _finish(paths, run, "ops-triage", scores, gates)


# -- recurring -----------------------------------------------------------------------------------------------------


def evaluate_recurring(paths: Paths, run_id: str) -> dict[str, Any]:
    truth, patterns = _truth(paths)
    limits = thresholds("ops-recurring")
    conn = db.connect(paths.db, readonly=True)
    try:
        run = _run(conn, run_id, ("sed-find-recurring",))
        findings = conn.execute(
            "SELECT finding_id, severity, payload_json FROM finding WHERE run_id = ? AND kind = 'issue_cluster'",
            (run_id,),
        ).fetchall()
        members = {
            f["finding_id"]: {
                r[0]
                for r in conn.execute(
                    "SELECT t.number FROM ai_cluster_member m JOIN ticket t ON t.ticket_id = "
                    "m.ticket_id WHERE m.finding_id = ?",
                    (f["finding_id"],),
                )
            }
            for f in findings
        }
        # Clusters come from the scope's labels and 12 months of text candidates: count planted tickets in that span.
        as_of = data_as_of(conn, load_settings(paths)) or date.today()
        since = (as_of - timedelta(days=HISTORY_DAYS)).isoformat()
        in_db = {
            r[0] for r in conn.execute("SELECT number FROM ticket WHERE kind = 'incident' AND opened_at >= ?", (since,))
        }
        app_names = dict(conn.execute("SELECT app_id, name FROM application").fetchall())
    finally:
        conn.close()
    by_pattern: dict[str, set[str]] = defaultdict(set)
    for number, row in truth.items():
        if row.get("pattern") in CLUSTER_PATTERNS and number in in_db:
            by_pattern[row["pattern"]].add(number)
    scores: dict[str, Any] = {"clusters": len(findings), "patterns": {}}
    gates: dict[str, bool] = {}
    payloads = {f["finding_id"]: json.loads(f["payload_json"] or "{}") for f in findings}
    for pattern in CLUSTER_PATTERNS:
        planted = by_pattern.get(pattern, set())
        best = None
        for f in findings:
            overlap = len(members[f["finding_id"]] & planted)
            if not overlap:
                continue
            coverage, precision = overlap / len(planted), overlap / len(members[f["finding_id"]])
            f1 = 2 * coverage * precision / (coverage + precision)
            if best is None or f1 > best[0]:
                best = (f1, f, coverage, precision)
        entry: dict[str, Any] = {"planted": len(planted), "found": best is not None}
        if best is not None:
            _, f, coverage, precision = best
            payload = payloads[f["finding_id"]]
            entry.update(
                {
                    "coverage": round(coverage, 4),
                    "precision": round(precision, 4),
                    "periodicity": payload.get("periodicity"),
                    "recommendation": payload.get("recommendation"),
                    "apps": len(payload.get("apps") or []),
                    "names_change": payload.get("suspected_change") == patterns["P1"].get("change")
                    if pattern == "P1"
                    else None,
                }
            )
        scores["patterns"][pattern] = entry
        min_cov, min_prec = limits.get("min_coverage", 0.6), limits.get("min_precision", 0.6)
        gates[f"{pattern}_covered"] = bool(
            best is not None and entry["coverage"] >= min_cov and entry["precision"] >= min_prec
        )
    p1, p5, p7 = (scores["patterns"][p] for p in CLUSTER_PATTERNS)
    gates["P1_names_change"] = bool(p1.get("names_change"))
    gates["P1_action"] = p1.get("recommendation") in {"raise_problem", "kb_article", "fix"}
    gates["P5_periodic"] = p5.get("periodicity") == "monthly"
    gates["P7_cross_app"] = (p7.get("apps") or 0) >= 3
    controls = patterns.get("controls", {})
    go_live = controls.get("go_live_app")
    go_live_ids = {a for a, n in app_names.items() if n == go_live}
    flagged = [
        f["finding_id"]
        for f in findings
        if f["severity"] in ("high", "critical")
        and set(payloads[f["finding_id"]].get("apps") or []) <= go_live_ids
        and go_live_ids
    ]
    scores["go_live_control_high_clusters"] = len(flagged)
    gates["go_live_control_quiet"] = not flagged
    return _finish(paths, run, "ops-recurring", scores, gates)


# -- risks ---------------------------------------------------------------------------------------------------------


def evaluate_risks(paths: Paths, run_id: str) -> dict[str, Any]:
    _, patterns = _truth(paths)
    conn = db.connect(paths.db, readonly=True)
    try:
        run = _run(conn, run_id, ("sed-assess-risks",))
        findings = [dict(r) for r in conn.execute("SELECT * FROM finding WHERE run_id = ?", (run_id,))]
        vendors = dict(conn.execute("SELECT name, vendor_id FROM vendor").fetchall())
        contracts = dict(conn.execute("SELECT contract_number, contract_id FROM contract").fetchall())
    finally:
        conn.close()
    thresholds("ops-risks")  # validates the section exists
    p2_vendor = vendors.get(patterns["P2"]["vendor"])
    auto = [contracts.get(c["contract"]) for c in patterns.get("P4", []) if c.get("auto_renew")]
    noisy = vendors.get(patterns.get("controls", {}).get("noisy_stable_vendor", ""))
    by_subject: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for f in findings:
        by_subject[(f["subject_type"], f["subject_id"])].append(f)
    p2 = [f for f in by_subject.get(("vendor", p2_vendor), []) if f["kind"] == "vendor_risk"]
    p4 = {cid: [f for f in by_subject.get(("contract", cid), []) if f["kind"] == "renewal_risk"] for cid in auto if cid}
    noisy_high = [
        f
        for f in by_subject.get(("vendor", noisy), [])
        if f["kind"] == "vendor_risk" and f["severity"] in ("high", "critical")
    ]
    scores = {
        "findings": len(findings),
        "p2_vendor_findings": len(p2),
        "p4_auto_renew_covered": sum(1 for fs in p4.values() if fs),
        "p4_auto_renew_high": sum(1 for fs in p4.values() if any(f["severity"] in ("high", "critical") for f in fs)),
        "noisy_vendor_high": len(noisy_high),
    }
    gates = {
        "P2_vendor_risk": bool(p2),
        "P4_auto_renew_high": bool(p4) and scores["p4_auto_renew_high"] == len(p4),
        "noisy_vendor_control_quiet": not noisy_high,
    }
    return _finish(paths, run, "ops-risks", scores, gates)
