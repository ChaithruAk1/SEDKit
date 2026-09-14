"""Human review of AI runs: sample cards, verdicts, approve-run and reject-run.

Labels are approved per run, never per item and never on model confidence. finish-run draws a random stratified
sample (the only input to sample accuracy) plus the lowest-confidence items (shown separately). A reviewer records
verdicts in a JSON file keyed "<item_id>|<stage>" (`sample --template` writes one with null placeholders):
"correct", {"verdict": "incorrect", "category": ..., "subcategory": ...}, or null (skipped, not recorded).
approve-run needs a verdict for every random-sample item and stores the weighted accuracy with a Wilson 95% interval.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sed import db
from sed.ai.runs import get_run, open_db, resolve_handler
from sed.ai.stats import weighted_accuracy, wilson_interval
from sed.errors import PreconditionFailed, ValidationFailed
from sed.paths import Paths

CORRECTION_KEYS = {"verdict", "category", "subcategory"}


def _key(item_id: str, stage: str) -> str:
    return f"{item_id}|{stage}"


def _stored_verdict(row: Any) -> Any:
    if row["verdict"] is None:
        return None
    if row["verdict"] == "correct":
        return "correct"
    try:
        correction = json.loads(row["correction_json"] or "{}")
    except ValueError:
        correction = {}
    return {"verdict": "incorrect", **{k: v for k, v in correction.items() if k != "verdict"}}


def _sample_rows(conn, run_id: str) -> list[Any]:
    return conn.execute(
        "SELECT * FROM ai_sample WHERE run_id = ? ORDER BY sample_kind DESC, stratum, item_id, stage", (run_id,)
    ).fetchall()


def sample(paths: Paths, run_id: str, *, template: Path | None = None) -> dict[str, Any]:
    conn = open_db(paths, readonly=True)
    try:
        run = get_run(conn, run_id)
        if run["status"] == "running":
            raise PreconditionFailed(f"Run {run_id} is still running; run `sed ai finish-run {run_id}` first")
        handler = resolve_handler(paths, run["skill"])
        rows = _sample_rows(conn, run_id)
        keys = sorted({(r["item_id"], r["stage"]) for r in rows})
        card_data = handler.review_card(conn, run_id, keys) if keys else {}
    finally:
        conn.close()
    cards = card_data.get("cards", {}) if isinstance(card_data, dict) else {}
    out: dict[str, list[dict[str, Any]]] = {"random": [], "lowest_confidence": []}
    for r in rows:
        key = _key(r["item_id"], r["stage"])
        display = cards.get(key, {})
        card = {
            "item_id": r["item_id"],
            "stage": r["stage"],
            "sample_kind": r["sample_kind"],
            "stratum": r["stratum"],
            "weight": r["weight"],
            "verdict": _stored_verdict(r),
            "label": display.get("label", {}),
            "ticket": display.get("ticket", {}),
        }
        out["random" if r["sample_kind"] == "random" else "lowest_confidence"].append(card)
    result = {
        "run_id": run_id,
        "status": run["status"],
        "random": out["random"],
        "lowest_confidence": out["lowest_confidence"],
        "matrix": card_data.get("matrix", []) if isinstance(card_data, dict) else [],
        "misfiled": card_data.get("misfiled", {}) if isinstance(card_data, dict) else {},
    }
    if template is not None:
        placeholders = {_key(item_id, stage): None for item_id, stage in keys}
        target = Path(str(template).replace("\\", "/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(placeholders, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        target.write_bytes(text.encode("utf-8"))
        result["template"] = target.resolve().as_posix()
        result["template_items"] = len(placeholders)
    return result


def _parse_verdict(key: str, value: Any, problems: list[dict[str, Any]]) -> tuple[str, str | None] | None:
    """(verdict, correction_json) for a value, None for a skipped (null) value; problems are appended."""
    if value is None:
        return None
    if value == "correct":
        return "correct", None
    if isinstance(value, dict):
        unknown = sorted(set(value) - CORRECTION_KEYS)
        if unknown:
            problems.append({"loc": key, "msg": f"unknown fields {unknown}"})
            return None
        if value.get("verdict") == "correct" and set(value) == {"verdict"}:
            return "correct", None
        if value.get("verdict") != "incorrect":
            problems.append({"loc": key, "msg": 'verdict must be "correct" or "incorrect"'})
            return None
        for field in ("category", "subcategory"):
            if value.get(field) is not None and not isinstance(value.get(field), str):
                problems.append({"loc": f"{key}.{field}", "msg": "must be a string or null"})
                return None
        correction = {k: value.get(k) for k in ("category", "subcategory") if value.get(k) is not None}
        return "incorrect", json.dumps(correction, sort_keys=True, ensure_ascii=False)
    problems.append({"loc": key, "msg": 'use "correct", {"verdict": "incorrect", ...} or null'})
    return None


def record_verdicts(paths: Paths, run_id: str, file: Path, *, reviewer: str | None = None) -> dict[str, Any]:
    if reviewer is None:
        from sed.bootstrap import reviewer_name

        reviewer = reviewer_name()
    path = Path(str(file).replace("\\", "/"))
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
    except OSError as exc:
        raise ValidationFailed(f"Cannot read verdicts file {path.as_posix()}: {exc}") from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationFailed(f"Verdicts file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationFailed('Verdicts file must be a JSON object {"<item_id>|<stage>": verdict}')
    conn = open_db(paths)
    try:
        run = get_run(conn, run_id)
        if run["status"] != "completed":
            raise PreconditionFailed(f"Run {run_id} is {run['status']}; verdicts are recorded on completed runs")
        rows = _sample_rows(conn, run_id)
        sampled = {_key(r["item_id"], r["stage"]) for r in rows}
        problems: list[dict[str, Any]] = [
            {"loc": key, "msg": "not a sampled item of this run"} for key in sorted(data) if key not in sampled
        ]
        parsed = {}
        for key in sorted(k for k in data if k in sampled):
            verdict = _parse_verdict(key, data[key], problems)
            if verdict is not None:
                parsed[key] = verdict
        if problems:
            raise ValidationFailed(f"Verdicts rejected: {len(problems)} problems", problems)
        decided = db.utc_now()
        with db.write_tx(conn):
            for key, (verdict, correction) in parsed.items():
                item_id, stage = key.rsplit("|", 1)
                conn.execute(
                    "UPDATE ai_sample SET verdict = ?, correction_json = ?, reviewer = ?, decided_at = ? "
                    "WHERE run_id = ? AND item_id = ? AND stage = ?",
                    (verdict, correction, reviewer, decided, run_id, item_id, stage),
                )
            remaining = conn.execute(
                "SELECT sample_kind, COUNT(*) FROM ai_sample WHERE run_id = ? AND verdict IS NULL GROUP BY sample_kind",
                (run_id,),
            ).fetchall()
    finally:
        conn.close()
    missing = {r[0]: int(r[1]) for r in remaining}
    return {
        "run_id": run_id,
        "recorded": len(parsed),
        "skipped": sum(1 for key in data if data[key] is None),
        "incorrect": sum(1 for v, _ in parsed.values() if v == "incorrect"),
        "random_missing": missing.get("random", 0),
        "lowest_conf_missing": missing.get("lowest_conf", 0),
    }


def approve_run(paths: Paths, run_id: str, reviewer: str, note: str | None = None) -> dict[str, Any]:
    conn = open_db(paths)
    try:
        with db.write_tx(conn):
            run = get_run(conn, run_id)
            if run["status"] != "completed":
                raise PreconditionFailed(f"Run {run_id} is {run['status']}; only completed runs can be approved")
            rows = _sample_rows(conn, run_id)
            random_rows = [r for r in rows if r["sample_kind"] == "random"]
            missing = [_key(r["item_id"], r["stage"]) for r in random_rows if r["verdict"] is None]
            if not random_rows or missing:
                raise PreconditionFailed(
                    f"Approve needs a verdict for every random-sample item ({len(missing)} of {len(random_rows)} "
                    "missing); use `sed review sample --template` and `sed review verdicts`",
                    {"missing": missing[:50]},
                )
            accuracy = weighted_accuracy((r["weight"], r["verdict"] == "correct") for r in random_rows)
            n = len(random_rows)
            low, high = wilson_interval(accuracy if accuracy is not None else 0.0, n)
            lowest = [r for r in rows if r["sample_kind"] == "lowest_conf"]
            lowest_rate = None
            if lowest and all(r["verdict"] is not None for r in lowest):
                lowest_rate = sum(1 for r in lowest if r["verdict"] == "incorrect") / len(lowest)
            reviewed_at = db.utc_now()
            conn.execute(
                "UPDATE ai_run SET status = 'approved', sample_accuracy = ?, sample_ci_low = ?, sample_ci_high = ?, "
                "sample_n = ?, lowest_conf_error_rate = ?, reviewed_by = ?, reviewed_at = ?, review_note = ? "
                "WHERE run_id = ?",
                (accuracy, low, high, n, lowest_rate, reviewer, reviewed_at, note, run_id),
            )
            payload = {
                "sample_accuracy": accuracy,
                "sample_ci_low": low,
                "sample_ci_high": high,
                "sample_n": n,
                "lowest_conf_error_rate": lowest_rate,
                "note": note,
            }
            conn.execute(
                "INSERT INTO review_decision (target_type, target_id, decision, payload_json, reviewer, decided_at) "
                "VALUES ('run', ?, 'approve_run', ?, ?, ?)",
                (run_id, json.dumps(payload, sort_keys=True), reviewer, reviewed_at),
            )
    finally:
        conn.close()
    return {"run_id": run_id, "status": "approved", "reviewed_by": reviewer, "reviewed_at": reviewed_at, **payload}


def reject_run(paths: Paths, run_id: str, reviewer: str, note: str) -> dict[str, Any]:
    if not note or not note.strip():
        raise ValidationFailed("reject-run needs a --note explaining why")
    conn = open_db(paths)
    try:
        with db.write_tx(conn):
            run = get_run(conn, run_id)
            if run["status"] not in {"completed", "failed", "approved"}:
                raise PreconditionFailed(
                    f"Run {run_id} is {run['status']}; finish it first (running) or it is already rejected"
                )
            reviewed_at = db.utc_now()
            conn.execute(
                "UPDATE ai_run SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, review_note = ? "
                "WHERE run_id = ?",
                (reviewer, reviewed_at, note.strip(), run_id),
            )
            conn.execute(
                "INSERT INTO review_decision (target_type, target_id, decision, payload_json, reviewer, decided_at) "
                "VALUES ('run', ?, 'reject_run', ?, ?, ?)",
                (run_id, json.dumps({"note": note.strip(), "previous_status": run["status"]}), reviewer, reviewed_at),
            )
    finally:
        conn.close()
    return {"run_id": run_id, "status": "rejected", "reviewed_by": reviewer, "reviewed_at": reviewed_at}
