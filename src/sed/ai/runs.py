"""AI run lifecycle: start-run (select, trim, claim, batch, refs, packets) and finish-run (fail, release, sample).

`start_run` does everything that touches the database in ONE write transaction, in this order:
insert ai_run -> handler.select (no writes) -> trim to --limit -> refuse above --max-items (rollback: no run,
batches or claims) -> handler.claim (exactly the trimmed items) -> greedy batching under PacketLimits -> refs
T001.. per batch -> ai_batch and ai_batch_item rows. Packet files are written after the commit. A dry run selects
on a query_only connection and writes nothing.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Any

from sed import db
from sed.ai import packets
from sed.ai.contract import BatchInput, FinishSummary, PacketLimits, RunContext, RunPlan, StartParams, WorkItem
from sed.ai.findings import carry_forward
from sed.ai.hashing import sha256_text, skill_hash
from sed.ai.stats import proportional_allocation
from sed.calendar import iso_utc, local_midnight_utc, parse_period
from sed.errors import PreconditionFailed, ValidationFailed
from sed.paths import Paths, repo_root
from sed.settings import Settings, load_agent_config, load_settings

CONTEXT_REL = "in"
RUN_ID_ATTEMPTS = 5


class _NothingSelected(Exception):
    """Raised inside the start-run transaction to roll back when select returns no items."""


@dataclass(frozen=True)
class ScopeBounds:
    scope: str
    start_iso: str
    end_iso: str | None


def scope_bounds(scope: str, settings: Settings, data_as_of: date | None) -> ScopeBounds:
    """UTC bounds of a run scope: new | since:YYYY-MM-DD | period:<label>.

    `new` starts at local midnight of (data_as_of - backfill_days); `since:D` at local midnight of D; `period:L`
    covers [start, end) of the period. Syntax errors exit 2; `new` without any imported data exits 4.
    """
    tz = settings.reporting_tz
    if scope == "new":
        if data_as_of is None:
            raise PreconditionFailed("scope=new needs a data as-of date: import data first (or use since:/period:)")
        start = local_midnight_utc(data_as_of - timedelta(days=settings.ai.backfill_days), tz)
        return ScopeBounds(scope, iso_utc(start), None)
    if scope.startswith("since:"):
        try:
            day = date.fromisoformat(scope.removeprefix("since:"))
        except ValueError as exc:
            raise ValidationFailed(f"Invalid scope '{scope}' (use since:YYYY-MM-DD)") from exc
        return ScopeBounds(scope, iso_utc(local_midnight_utc(day, tz)), None)
    if scope.startswith("period:"):
        period = parse_period(scope.removeprefix("period:"), tz, settings.fiscal_year_start)
        return ScopeBounds(scope, period.start_iso, period.end_iso)
    raise ValidationFailed(f"Invalid scope '{scope}' (use new, since:YYYY-MM-DD or period:<label>)")


def new_run_id(skill: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{skill.removeprefix('sed-')}-{secrets.token_hex(2)}"


def lease_until(hours: int, now: datetime | None = None) -> str:
    return iso_utc((now or datetime.now(UTC)) + timedelta(hours=hours))


def posix_abs(path: Path) -> str:
    """Absolute path with forward slashes (agents run Git Bash, where backslashes are escapes)."""
    return path.resolve().as_posix()


def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def get_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM ai_run WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise PreconditionFailed(f"Unknown AI run '{run_id}'")
    return row


def resolve_handler(paths: Paths, skill: str) -> Any:
    """The skill's handler; exit 2 for an unknown skill, exit 4 when its module is disabled."""
    from sed import modules

    module, _ = modules.skill(skill)
    modules.require_enabled(paths, module.key)
    return modules.handler(skill)


def open_db(paths: Paths, *, readonly: bool = False) -> sqlite3.Connection:
    if not paths.db.exists():
        raise PreconditionFailed(f"No database at {paths.db}; run `sed init --profile {paths.profile}` first.")
    return db.connect(paths.db, readonly=readonly)


def run_context(conn: sqlite3.Connection, paths: Paths, settings: Settings, run: sqlite3.Row) -> RunContext:
    from sed.ingest.freshness import data_as_of

    try:
        params = StartParams.model_validate_json(run["params_json"] or "{}")
    except ValueError:
        params = StartParams()
    run_dir = paths.runs / run["run_id"]
    return RunContext(
        conn=conn,
        paths=paths,
        settings=settings,
        run_id=run["run_id"],
        skill=run["skill"],
        params=params,
        data_as_of=data_as_of(conn, settings),
        run_dir=run_dir,
        in_dir=run_dir / "in",
        out_dir=run_dir / "out",
    )


# -- start-run ---------------------------------------------------------------------------------------------------


def start_run(paths: Paths, skill: str, params: StartParams) -> RunPlan:
    from sed.ingest.freshness import data_as_of

    handler = resolve_handler(paths, skill)
    settings = load_settings(paths)
    conn = open_db(paths, readonly=params.dry_run)
    try:
        if paths.data_class == "real" and db.get_meta(conn, "ai_real_data_approved") != "true":
            raise PreconditionFailed(
                "AI use on the real profile is not recorded as approved: run "
                "`sed init --profile real --ai-approval-note ...` first."
            )
        data_date = data_as_of(conn, settings)
        if params.resume:
            return _resume(conn, paths, settings, handler, skill, params, data_date)
        scope_bounds(params.scope, settings, data_date)
        limits = PacketLimits(
            max_items=params.batch_size or settings.ai.triage_batch_size,
            max_chars=params.max_chars or settings.ai.triage_packet_max_chars,
        )
        max_items = params.max_items or settings.ai.max_items_per_run
        if params.dry_run:
            return _dry_run(conn, paths, settings, handler, skill, params, data_date, limits, max_items)
        return _real_run(conn, paths, settings, handler, skill, params, data_date, limits, max_items)
    finally:
        conn.close()


def _plan_counts(items: int, batches: list[packets.Batch], claimed: int) -> dict[str, int]:
    return {
        "items": items,
        "batches": len(batches),
        "est_agents": len(batches),
        "max_chars_per_batch": max((b.chars for b in batches), default=0),
        "longest_line_chars": max((b.longest_line for b in batches), default=0),
        "claimed": claimed,
    }


def _trimmed(items: list[WorkItem], params: StartParams) -> list[WorkItem]:
    return items[: params.limit] if params.limit else list(items)


def _too_many(count: int, max_items: int, plan: dict[str, int]) -> PreconditionFailed:
    return PreconditionFailed(
        f"The run would take {count} items, above --max-items {max_items}; narrow the scope or use --limit.",
        {"plan": plan, "max_items": max_items},
    )


def _dry_run(conn, paths, settings, handler, skill, params, data_date, limits, max_items) -> RunPlan:
    ctx = RunContext(conn, paths, settings, None, skill, params, data_date, None, None, None)
    items = _trimmed(handler.select(ctx), params)
    batches = packets.plan_batches(items, limits)
    plan = _plan_counts(len(items), batches, 0)
    if len(items) > max_items:
        raise _too_many(len(items), max_items, plan)
    return RunPlan(
        run_id=None,
        skill=skill,
        status="planned",
        dry_run=True,
        plan=plan,
        run_dir=None,
        out_dir=None,
        context=[],
        inputs=[],
    )


def _insert_run(
    conn: sqlite3.Connection,
    paths: Paths,
    handler: Any,
    skill: str,
    params: StartParams,
    shash: str,
    commit: str | None,
) -> str:
    for _ in range(RUN_ID_ATTEMPTS):
        run_id = new_run_id(skill)
        try:
            conn.execute(
                "INSERT INTO ai_run (run_id, skill, skill_hash, schema_version, git_commit, model_arg, claude_version, "
                "invoked_via, profile, params_json, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'running', ?)",
                (
                    run_id,
                    skill,
                    shash,
                    int(handler.schema_version),
                    commit,
                    params.model_arg,
                    params.claude_version,
                    params.invoked_via,
                    paths.profile,
                    params.model_dump_json(),
                    db.utc_now(),
                ),
            )
            return run_id
        except sqlite3.IntegrityError:
            continue
    raise PreconditionFailed("Could not allocate a unique run id; retry.")


def _real_run(conn, paths, settings, handler, skill, params, data_date, limits, max_items) -> RunPlan:
    skill_dir = repo_root() / ".claude" / "skills" / skill
    if not skill_dir.is_dir():
        raise PreconditionFailed(f"Skill folder {skill_dir.as_posix()} is missing; agents cannot follow the skill.")
    shash = skill_hash(repo_root(), skill, handler.config_inputs(paths))
    commit = git_commit()  # outside the write transaction: never hold the write lock while running git
    try:
        with db.write_tx(conn):
            run_id = _insert_run(conn, paths, handler, skill, params, shash, commit)
            run_dir = paths.runs / run_id
            ctx = RunContext(
                conn, paths, settings, run_id, skill, params, data_date, run_dir, run_dir / "in", run_dir / "out"
            )
            items = _trimmed(handler.select(ctx), params)
            if not items:
                raise _NothingSelected
            batches = packets.plan_batches(items, limits)
            if len(items) > max_items:
                raise _too_many(len(items), max_items, _plan_counts(len(items), batches, 0))
            handler.claim(ctx, items)
            input_runs = getattr(handler, "input_run_ids", None)
            if input_runs is not None:  # label runs a finding skill's packet used (stale-input tracking)
                conn.execute(
                    "UPDATE ai_run SET input_run_ids_json = ? WHERE run_id = ?",
                    (json.dumps(sorted(set(input_runs(ctx, items)))), run_id),
                )
            context_files = dict(handler.context_files(ctx, items))
            reserved = {f"{b.name}.jsonl" for b in batches}
            for name in context_files:
                packets.check_file_name(name, reserved)
            aux: dict[str, dict[str, str]] = {}
            for batch in batches:
                files = dict(handler.batch_files(ctx, batch.name, batch.items))
                for name in files:
                    packets.check_file_name(name, reserved | set(context_files))
                aux[batch.name] = files
            for batch in batches:
                batch_id = f"{run_id}/{batch.name}"
                conn.execute(
                    "INSERT INTO ai_batch (batch_id, run_id, seq, packet_path, packet_sha, item_count, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'planned')",
                    (batch_id, run_id, batch.seq, f"in/{batch.name}.jsonl", batch.sha256, len(batch.items)),
                )
                conn.executemany(
                    "INSERT INTO ai_batch_item (batch_id, ref, item_id, stage, input_hash) VALUES (?, ?, ?, ?, ?)",
                    [
                        (batch_id, ref, item.item_id, item.stage, item.input_hash)
                        for ref, item in zip(batch.refs, batch.items, strict=True)
                    ],
                )
            manifest_text = _manifest_text(run_id, skill, shash, handler, paths, params, batches, context_files, aux)
            plan = _plan_counts(len(items), batches, len(items))
            conn.execute(
                "UPDATE ai_run SET input_manifest_sha = ?, counts_json = ? WHERE run_id = ?",
                (sha256_text(manifest_text), json.dumps(plan, sort_keys=True), run_id),
            )
    except _NothingSelected:
        return RunPlan(
            run_id=None,
            skill=skill,
            status="planned",
            dry_run=False,
            plan=_plan_counts(0, [], 0),
            run_dir=None,
            out_dir=None,
            context=[],
            inputs=[],
        )
    packets.write_run_files(run_dir, batches, context_files, aux, manifest_text)
    return RunPlan(
        run_id=run_id,
        skill=skill,
        status="running",
        dry_run=False,
        plan=plan,
        run_dir=posix_abs(run_dir),
        out_dir=posix_abs(run_dir / "out"),
        context=[posix_abs(run_dir / "in" / name) for name in context_files],
        inputs=[
            BatchInput(
                batch=b.name,
                packet=posix_abs(run_dir / "in" / f"{b.name}.jsonl"),
                aux=[posix_abs(run_dir / "in" / name) for name in aux.get(b.name, {})],
                out=posix_abs(run_dir / "out" / f"{b.name}.json"),
                items=len(b.items),
                chars=b.chars,
            )
            for b in batches
        ],
    )


def _manifest_text(run_id, skill, shash, handler, paths, params, batches, context_files, aux) -> str:
    manifest = {
        "run_id": run_id,
        "skill": skill,
        "skill_hash": shash,
        "schema_version": int(handler.schema_version),
        "profile": paths.profile,
        "params": params.model_dump(),
        "context": [{"file": f"in/{name}", "sha256": sha256_text(text)} for name, text in context_files.items()],
        "batches": [
            {
                "batch": b.name,
                "packet": f"in/{b.name}.jsonl",
                "packet_sha": b.sha256,
                "items": len(b.items),
                "chars": b.chars,
                "aux": [{"file": f"in/{name}", "sha256": sha256_text(text)} for name, text in aux[b.name].items()],
                "out": f"out/{b.name}.json",
            }
            for b in batches
        ],
    }
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _manifest(run_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _resume(conn, paths, settings, handler, skill, params, data_date) -> RunPlan:
    run = get_run(conn, params.resume)
    if run["skill"] != skill:
        raise PreconditionFailed(f"Run {params.resume} belongs to skill {run['skill']}, not {skill}")
    if run["status"] != "running":
        raise PreconditionFailed(f"Run {params.resume} is {run['status']}; only running runs can be resumed")
    ctx = run_context(conn, paths, settings, run)
    # The stored params come from the first start (resume unset); the handler must know this is a resume, so items
    # another run claimed after the leases expired are skipped instead of failing the whole resume as busy.
    ctx.params = ctx.params.model_copy(update={"resume": run["run_id"], "dry_run": params.dry_run})
    rows = conn.execute(
        "SELECT batch_id, seq, packet_path, item_count FROM ai_batch WHERE run_id = ? AND status <> 'ingested' "
        "ORDER BY seq",
        (run["run_id"],),
    ).fetchall()
    items = [
        WorkItem(r["item_id"], r["stage"], r["input_hash"], {})
        for r in conn.execute(
            "SELECT i.item_id, i.stage, i.input_hash FROM ai_batch_item i JOIN ai_batch b ON b.batch_id = i.batch_id "
            "WHERE b.run_id = ? AND b.status <> 'ingested' ORDER BY b.seq, i.ref",
            (run["run_id"],),
        )
    ]
    if items and not params.dry_run:
        with db.write_tx(conn):
            handler.claim(ctx, items)
    run_dir = ctx.run_dir
    manifest = _manifest(run_dir)
    aux_by_batch = {b.get("batch"): [a.get("file", "") for a in b.get("aux", [])] for b in manifest.get("batches", [])}
    context = [c.get("file", "") for c in manifest.get("context", [])]
    inputs, longest = [], 0
    for r in rows:
        name = r["batch_id"].split("/", 1)[1]
        packet = run_dir / r["packet_path"]
        try:
            text = packet.read_bytes().decode("utf-8")
        except OSError as exc:
            raise PreconditionFailed(f"Packet file for {name} is missing: {packet.as_posix()}") from exc
        longest = max([longest, *(len(line) for line in text.split("\n"))])
        aux = [run_dir / rel for rel in aux_by_batch.get(name, []) if rel]
        inputs.append(
            BatchInput(
                batch=name,
                packet=posix_abs(packet),
                aux=[posix_abs(p) for p in aux if _inside(p, run_dir / "in")],
                out=posix_abs(run_dir / "out" / f"{name}.json"),
                items=int(r["item_count"]),
                chars=len(text),
            )
        )
    plan = {
        "items": len(items),
        "batches": len(inputs),
        "est_agents": len(inputs),
        "max_chars_per_batch": max((i.chars for i in inputs), default=0),
        "longest_line_chars": longest,
        "claimed": 0 if params.dry_run else len(items),
    }
    if not params.dry_run:
        (run_dir / "out").mkdir(parents=True, exist_ok=True)
    return RunPlan(
        run_id=run["run_id"],
        skill=skill,
        status="planned" if params.dry_run else "running",
        dry_run=params.dry_run,
        plan=plan,
        run_dir=posix_abs(run_dir),
        out_dir=posix_abs(run_dir / "out"),
        context=[posix_abs(run_dir / rel) for rel in context if rel and _inside(run_dir / rel, run_dir / "in")],
        inputs=inputs,
    )


# -- finish-run --------------------------------------------------------------------------------------------------


def draw_sample(
    candidates: list[Any], run_id: str, *, random_n: int, lowest_n: int
) -> tuple[list[tuple[Any, float]], list[Any]]:
    """Random stratified sample (proportional, >= 1 per stratum, seeded by the run id) and lowest-confidence items.

    Returns ([(candidate, weight)], [candidate]); weight = stratum size / stratum sample size.
    """
    rng = Random(int(sha256_text(run_id)[:8], 16))
    strata: dict[str, list[Any]] = {}
    for c in sorted(candidates, key=lambda c: (c.item_id, c.stage)):
        strata.setdefault(c.stratum, []).append(c)
    alloc = proportional_allocation({k: len(v) for k, v in strata.items()}, random_n)
    random_sample: list[tuple[Any, float]] = []
    for stratum in sorted(strata):
        members, k = strata[stratum], alloc.get(stratum, 0)
        if k <= 0:
            continue
        weight = len(members) / k
        random_sample += [(c, weight) for c in rng.sample(members, k)]
    ranked = sorted(candidates, key=lambda c: (c.confidence if c.confidence is not None else -1.0, c.item_id, c.stage))
    return random_sample, ranked[: max(0, lowest_n)]


def finish_run(paths: Paths, run_id: str) -> FinishSummary:
    settings = load_settings(paths)
    conn = open_db(paths)
    try:
        run = get_run(conn, run_id)
        if run["status"] != "running":
            raise PreconditionFailed(f"Run {run_id} is {run['status']}; finish-run needs a running run")
        handler = resolve_handler(paths, run["skill"])
        ctx = run_context(conn, paths, settings, run)
        with db.write_tx(conn):
            run = get_run(conn, run_id)
            if run["status"] != "running":
                raise PreconditionFailed(f"Run {run_id} is {run['status']}; finish-run needs a running run")
            conn.execute("UPDATE ai_batch SET status = 'failed' WHERE run_id = ? AND status = 'planned'", (run_id,))
            released = int(handler.release(ctx))
            batches = conn.execute(
                "SELECT batch_id, status, item_count FROM ai_batch WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
            ingested = [b for b in batches if b["status"] == "ingested"]
            failed = [b["batch_id"].split("/", 1)[1] for b in batches if b["status"] == "failed"]
            candidates = list(handler.sample_candidates(conn, run_id))
            random_sample, lowest = draw_sample(
                candidates, run_id, random_n=settings.ai.review_sample_size, lowest_n=settings.ai.lowest_conf_sample
            )
            conn.execute("DELETE FROM ai_sample WHERE run_id = ?", (run_id,))
            conn.executemany(
                "INSERT INTO ai_sample (run_id, item_id, stage, sample_kind, stratum, weight) "
                "VALUES (?, ?, ?, 'random', ?, ?)",
                [(run_id, c.item_id, c.stage, c.stratum, w) for c, w in random_sample],
            )
            conn.executemany(
                "INSERT INTO ai_sample (run_id, item_id, stage, sample_kind, stratum, weight) "
                "VALUES (?, ?, ?, 'lowest_conf', ?, 1.0)",
                [(run_id, c.item_id, c.stage, c.stratum) for c in lowest],
            )
            try:
                started = json.loads(run["counts_json"] or "{}")
            except ValueError:
                started = {}
            threshold = settings.ai.low_confidence_threshold
            counts = {
                "items": int(sum(b["item_count"] for b in batches)),
                "claimed": int(started.get("claimed", 0)),
                "batches": len(batches),
                "ingested_batches": len(ingested),
                "failed_batches": len(failed),
                "labelled": len(candidates),
                "low_confidence": sum(1 for c in candidates if c.confidence is not None and c.confidence < threshold),
                "claims_released": released,
                "random_sample": len(random_sample),
                "lowest_conf_sample": len(lowest),
            }
            status = "completed" if ingested else "failed"
            if conn.execute("SELECT 1 FROM finding WHERE run_id = ? LIMIT 1", (run_id,)).fetchone():
                counts.update({f"findings_{k}": v for k, v in carry_forward(conn, run_id).items()})
            conn.execute(
                "UPDATE ai_run SET status = ?, counts_json = ?, finished_at = ? WHERE run_id = ?",
                (status, json.dumps(counts, sort_keys=True), db.utc_now(), run_id),
            )
    finally:
        conn.close()
    prefix = load_agent_config().command_prefix
    return FinishSummary(
        run_id=run_id,
        status=status,
        counts=counts,
        failed_batches=failed,
        review={
            "random_sample": len(random_sample),
            "lowest_conf": len(lowest),
            "next": f"{prefix} review sample {run_id} --profile {paths.profile} --json",
        },
    )


# -- listing -----------------------------------------------------------------------------------------------------


def run_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        counts = {k: int(v) for k, v in json.loads(row["counts_json"] or "{}").items() if isinstance(v, int | float)}
    except ValueError:
        counts = {}
    return {
        "run_id": row["run_id"],
        "skill": row["skill"],
        "skill_hash": row["skill_hash"],
        "status": row["status"],
        "invoked_via": row["invoked_via"],
        "model_arg": row["model_arg"],
        "model_reported": row["model_reported"],
        "claude_version": row["claude_version"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "counts": counts,
        "sample_accuracy": row["sample_accuracy"],
        "sample_ci_low": row["sample_ci_low"],
        "sample_ci_high": row["sample_ci_high"],
        "sample_n": row["sample_n"],
        "lowest_conf_error_rate": row["lowest_conf_error_rate"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
    }


def list_runs(paths: Paths, *, skill: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
    conn = open_db(paths, readonly=True)
    try:
        clauses: list[str] = []
        params_: list[Any] = []
        if skill:
            clauses.append("skill = ?")
            params_.append(skill)
        if status:
            clauses.append("status = ?")
            params_.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(f"SELECT * FROM ai_run {where} ORDER BY run_seq DESC LIMIT ?", [*params_, limit])
        return [run_row(r) for r in rows]
    finally:
        conn.close()
