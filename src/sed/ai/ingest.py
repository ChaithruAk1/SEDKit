"""`sed ai ingest RUN FILE`: validate one agent output file and store it (whole-batch accept or reject).

Checks, in order (exit 4 for run problems, exit 2 for everything about the file):
* the run exists and is running;
* FILE (either path separator) resolves inside runs/<run>/out/ and its stem is a batch of this run;
* the packet on disk still has the sha recorded at start-run (a tampered packet cannot redirect labels, and refs are
  rebuilt from ai_batch_item only, never from the packet);
* the JSON parses and validates against the skill's output model;
* every ref of the batch appears exactly once, and no other ref appears;
* the handler's semantic checks pass.
Any error rejects the batch: attempts+1 and last_errors_json are recorded and no label rows are written. On success
the handler writes inside one transaction and the batch becomes `ingested` with its output sha. Ingesting the same
bytes again returns `unchanged`.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sed import db
from sed.ai.contract import IngestError, IngestResult, WorkItem
from sed.ai.hashing import sha256_bytes
from sed.ai.runs import get_run, open_db, resolve_handler, run_context
from sed.errors import PreconditionFailed, ValidationFailed
from sed.paths import Paths
from sed.settings import load_settings

MAX_REPORTED_ERRORS = 200
MAX_OUTPUT_BYTES = 10 * 1024 * 1024


def _no_constants(value: str) -> Any:
    raise ValueError(f"{value} is not valid JSON")


def _as_path(file: str | Path) -> Path:
    return Path(str(file).replace("\\", "/"))


def _same_dir(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _changed_inputs(run_dir: Path, manifest_sha: str | None, batch_name: str) -> list[str]:
    """Run files an agent read for this batch that no longer match start-run's record: manifest.json (against
    ai_run.input_manifest_sha), the shared context files and the batch's aux files (against the manifest). Agents may
    write inside runs/, so a changed shared context.md would otherwise mislead every later batch."""
    try:
        data = (run_dir / "manifest.json").read_bytes()
        manifest = json.loads(data)
    except (OSError, ValueError):
        return ["manifest.json"]
    if manifest_sha and sha256_bytes(data) != manifest_sha:
        return ["manifest.json"]
    entries = list(manifest.get("context") or [])
    for b in manifest.get("batches") or []:
        if b.get("batch") == batch_name:
            entries += list(b.get("aux") or [])
    changed = []
    for entry in entries:
        rel = str(entry.get("file") or "")
        try:
            ok = bool(rel) and sha256_bytes((run_dir / rel).read_bytes()) == entry.get("sha256")
        except OSError:
            ok = False
        if not ok:
            changed.append(rel or "(unnamed input)")
    return changed


def _error_dicts(errors: list[IngestError]) -> list[dict[str, Any]]:
    return [{"loc": e.loc, "msg": e.msg, "ref": e.ref} for e in errors]


def _reject(conn, batch_id: str | None, errors: list[IngestError]) -> ValidationFailed:
    details = _error_dicts(errors)
    if batch_id is not None:
        with db.write_tx(conn):
            conn.execute(
                "UPDATE ai_batch SET attempts = attempts + 1, last_errors_json = ? WHERE batch_id = ?",
                (json.dumps(details[:MAX_REPORTED_ERRORS], ensure_ascii=False), batch_id),
            )
    shown = details[:MAX_REPORTED_ERRORS]
    return ValidationFailed(f"Batch rejected: {len(details)} errors", shown)


def _model_errors(exc: ValidationError, raw: Any) -> list[IngestError]:
    items = raw.get("items") if isinstance(raw, dict) else None
    out = []
    for err in exc.errors():
        loc = [str(p) for p in err["loc"]]
        ref = None
        if len(loc) >= 2 and loc[0] == "items" and loc[1].isdigit() and isinstance(items, list):
            idx = int(loc[1])
            if idx < len(items) and isinstance(items[idx], dict) and isinstance(items[idx].get("ref"), str):
                ref = items[idx]["ref"]
        out.append(IngestError(".".join(loc) or "(root)", err["msg"], ref))
    return out


def _ref_errors(raw: Any, refs: dict[str, WorkItem]) -> list[IngestError]:
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    seen = Counter(i.get("ref") for i in items if isinstance(i, dict) and isinstance(i.get("ref"), str))
    errors = [IngestError("items", "ref missing from the output (label it once)", r) for r in refs if r not in seen]
    errors += [
        IngestError("items", f"ref appears {n} times (exactly once allowed)", r) for r, n in seen.items() if n > 1
    ]
    errors += [IngestError("items", "ref is not in this batch (invented)", r) for r in seen if r not in refs]
    return errors


def _packet_payloads(packet: bytes) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in packet.decode("utf-8").splitlines():
        if line.strip():
            data = json.loads(line)
            if isinstance(data, dict) and isinstance(data.get("ref"), str):
                out[data["ref"]] = {k: v for k, v in data.items() if k != "ref"}
    return out


def ingest_file(paths: Paths, run_id: str, file: str | Path) -> dict[str, Any]:
    settings = load_settings(paths)
    conn = open_db(paths)
    try:
        run = get_run(conn, run_id)
        if run["status"] != "running":
            raise PreconditionFailed(f"Run {run_id} is {run['status']}; only running runs accept output")
        handler = resolve_handler(paths, run["skill"])
        ctx = run_context(conn, paths, settings, run)
        out_dir = (paths.runs / run_id / "out").resolve()
        target = _as_path(file)
        if not target.is_absolute():
            target = Path.cwd() / target
        resolved = target.resolve()
        if not _same_dir(resolved.parent, out_dir):
            raise _reject(
                conn, None, [IngestError("file", f"{resolved.as_posix()} is not inside {out_dir.as_posix()}/")]
            )
        batch_name = resolved.stem
        batch_id = f"{run_id}/{batch_name}"
        batch = conn.execute("SELECT * FROM ai_batch WHERE batch_id = ? AND run_id = ?", (batch_id, run_id)).fetchone()
        if batch is None or resolved.suffix.lower() != ".json":
            raise _reject(conn, None, [IngestError("file", f"'{resolved.name}' is not an output file of a batch")])
        try:
            if resolved.stat().st_size > MAX_OUTPUT_BYTES:
                raise _reject(conn, batch_id, [IngestError("file", f"larger than {MAX_OUTPUT_BYTES} bytes")])
            data = resolved.read_bytes()
        except OSError as exc:
            raise _reject(conn, batch_id, [IngestError("file", f"cannot read {resolved.as_posix()}: {exc}")]) from exc
        output_sha = sha256_bytes(data)
        if batch["status"] == "ingested" and batch["output_sha"] == output_sha:
            return {
                "run_id": run_id,
                "batch": batch_name,
                "status": "unchanged",
                "items": int(batch["item_count"]),
                "low_confidence": 0,
                "warnings": [],
            }
        packet = paths.runs / run_id / batch["packet_path"]
        try:
            packet_bytes = packet.read_bytes()
        except OSError:
            packet_bytes = None
        packet_sha = sha256_bytes(packet_bytes) if packet_bytes is not None else None
        if packet_sha != batch["packet_sha"]:
            raise _reject(
                conn, batch_id, [IngestError("packet", f"packet {batch['packet_path']} changed or is missing")]
            )
        changed = _changed_inputs(paths.runs / run_id, run["input_manifest_sha"], batch_name)
        if changed:
            raise _reject(
                conn, batch_id, [IngestError("packet", f"run input changed or is missing: {', '.join(changed)}")]
            )
        # Each ref's payload is its line in the verified packet: what the agent read (text fields may be truncated).
        lines = _packet_payloads(packet_bytes or b"")
        refs = {
            r["ref"]: WorkItem(r["item_id"], r["stage"], r["input_hash"], lines.get(r["ref"], {}))
            for r in conn.execute(
                "SELECT ref, item_id, stage, input_hash FROM ai_batch_item WHERE batch_id = ? ORDER BY ref", (batch_id,)
            )
        }
        try:
            raw = json.loads(data.decode("utf-8-sig"), parse_constant=_no_constants)
        except (UnicodeDecodeError, ValueError) as exc:
            raise _reject(conn, batch_id, [IngestError("(root)", f"invalid JSON: {exc}")]) from exc
        errors: list[IngestError] = []
        output = None
        try:
            output = handler.output_model.model_validate(raw)
        except ValidationError as exc:
            errors += _model_errors(exc, raw)
        errors += _ref_errors(raw, refs)
        if output is not None and not errors:
            errors += list(handler.validate(ctx, output, refs))
        if errors:
            raise _reject(conn, batch_id, errors)
        meta = getattr(output, "meta", None)
        model = getattr(meta, "model", None)
        with db.write_tx(conn):
            current = get_run(conn, run_id)
            if current["status"] != "running":
                raise PreconditionFailed(f"Run {run_id} is {current['status']}; only running runs accept output")
            result: IngestResult = handler.write(ctx, batch_id, output, refs)
            conn.execute(
                "UPDATE ai_batch SET status = 'ingested', output_sha = ?, attempts = attempts + 1, "
                "last_errors_json = NULL WHERE batch_id = ?",
                (output_sha, batch_id),
            )
            if model:
                reported = [m for m in (current["model_reported"] or "").split(", ") if m]
                if model not in reported:
                    conn.execute(
                        "UPDATE ai_run SET model_reported = ? WHERE run_id = ?",
                        (", ".join([*reported, model])[:300], run_id),
                    )
    finally:
        conn.close()
    return {
        "run_id": run_id,
        "batch": batch_name,
        "status": "ingested",
        "items": result.items,
        "low_confidence": result.low_confidence,
        "warnings": list(result.warnings),
    }
