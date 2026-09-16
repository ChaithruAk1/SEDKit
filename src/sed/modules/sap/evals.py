"""Offline eval of SAP subcategories in AI triage: a sed-triage-batch run's labels of SAP tickets against the synthetic
ground truth (ground_truth/sap/ticket_truth.csv, written by synth.py).

Prints scores only: counts, accuracies with Wilson 95% intervals, accuracy per expected subcategory and the most
frequent confusions as code pairs. No ticket text or numbers leave this function. Pass thresholds come from
evals/thresholds.yaml (`sap-triage`). Labels of tickets without SAP ground truth (ops tickets) are not scored.
"""

from __future__ import annotations

import csv
from collections import Counter
from typing import Any

import yaml

from sed import db
from sed.ai.stats import wilson_interval
from sed.errors import PreconditionFailed, ValidationFailed
from sed.paths import Paths, repo_root

SKILL = "sed-triage-batch"
THRESHOLDS_FILE = "evals/thresholds.yaml"
SECTION = "sap-triage"
TOP_CONFUSIONS = 10


def load_thresholds() -> dict[str, float]:
    path = repo_root() / THRESHOLDS_FILE
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationFailed(f"Cannot read {THRESHOLDS_FILE}: {exc}") from exc
    section = data.get(SECTION)
    keys = ("min_scored", "min_category_accuracy", "min_subcategory_accuracy")
    if not isinstance(section, dict) or any(not isinstance(section.get(k), int | float) for k in keys):
        raise ValidationFailed(f"{THRESHOLDS_FILE}: section '{SECTION}' needs numbers for {', '.join(keys)}")
    return {k: section[k] for k in keys}


def _truth(paths: Paths) -> dict[str, dict[str, str]]:
    if paths.data_class != "synthetic":
        raise PreconditionFailed("SAP triage ground truth exists only on synthetic and eval profiles")
    file = paths.ground_truth / "sap" / "ticket_truth.csv"
    if not file.is_file():
        raise PreconditionFailed(f"No SAP ground truth: run `sed synth --module sap --profile {paths.profile}` first")
    with file.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if rows and "am_category" not in rows[0]:
        raise PreconditionFailed("The SAP ground truth predates triage truth: run `sed synth --module sap` again")
    return {r["number"]: r for r in rows if r.get("am_category")}


def _rate(correct: int, n: int) -> dict[str, Any]:
    if n == 0:
        return {"accuracy": None, "ci_low": None, "ci_high": None}
    p = correct / n
    low, high = wilson_interval(p, n)
    return {"accuracy": round(p, 4), "ci_low": round(low, 4), "ci_high": round(high, 4)}


def evaluate_triage(paths: Paths, run_id: str) -> dict[str, Any]:
    truth = _truth(paths)
    thresholds = load_thresholds()
    if not paths.db.exists():
        raise PreconditionFailed(f"No database at {paths.db}")
    conn = db.connect(paths.db, readonly=True)
    try:
        run = conn.execute("SELECT skill, status FROM ai_run WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise PreconditionFailed(f"Unknown AI run '{run_id}'")
        if run["skill"] != SKILL:
            raise PreconditionFailed(f"Run '{run_id}' is a {run['skill']} run, not {SKILL}")
        labels = conn.execute(
            "SELECT t.number, l.am_category, l.am_subcategory, l.misfiled_as FROM ai_ticket_label l "
            "JOIN ticket t ON t.ticket_id = l.ticket_id WHERE l.run_id = ? ORDER BY t.number, l.stage",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    scored = [(label, truth[label["number"]]) for label in labels if label["number"] in truth]
    n = len(scored)
    category_ok = sum(1 for lb, tr in scored if lb["am_category"] == tr["am_category"])
    sub_ok = sum(1 for lb, tr in scored if (lb["am_subcategory"] or "") == tr["am_subcategory"])
    misfiled_ok = sum(1 for lb, tr in scored if lb["misfiled_as"] == (tr["misfiled_as"] or "none"))
    per_sub: dict[str, dict[str, int]] = {}
    confusions: Counter[tuple[str, str]] = Counter()
    for lb, tr in scored:
        expected, got = tr["am_subcategory"], lb["am_subcategory"] or "null"
        row = per_sub.setdefault(expected, {"n": 0, "correct": 0})
        row["n"] += 1
        if got == expected:
            row["correct"] += 1
        else:
            confusions[(f"{tr['am_category']}/{expected}", f"{lb['am_category']}/{got}")] += 1
    category, subcategory = _rate(category_ok, n), _rate(sub_ok, n)
    checks = {
        "min_scored": n >= thresholds["min_scored"],
        "min_category_accuracy": (category["accuracy"] or 0) >= thresholds["min_category_accuracy"],
        "min_subcategory_accuracy": (subcategory["accuracy"] or 0) >= thresholds["min_subcategory_accuracy"],
    }
    return {
        "run_id": run_id,
        "run_status": run["status"],
        "labels": len(labels),
        "scored": n,
        "category": category,
        "subcategory": subcategory,
        "misfiled_as": _rate(misfiled_ok, n),
        "sap_subcategory_share": round(
            sum(1 for lb, _ in scored if (lb["am_subcategory"] or "").startswith("sap_")) / n, 4
        )
        if n
        else None,
        "by_subcategory": {
            code: {**row, "accuracy": round(row["correct"] / row["n"], 4)} for code, row in sorted(per_sub.items())
        },
        "confusions": [
            {"expected": e, "labelled": g, "count": c}
            for (e, g), c in sorted(confusions.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_CONFUSIONS]
        ],
        "thresholds": thresholds,
        "checks": checks,
        "passed": all(checks.values()),
    }
