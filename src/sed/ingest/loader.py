"""`sed import`: one path for every source file.

scan inbox -> sha256 dedupe -> choose mapping -> read -> map + transform -> PII -> resolve -> validate
-> chunked BEGIN IMMEDIATE upserts -> load-mode post-steps -> DQ record -> move file to processed/.

Reading, mapping and scrubbing happen BEFORE any write transaction; each chunk is a short transaction so agents
and the API are never blocked for long.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sed import db
from sed.errors import PreconditionFailed, SedError, ValidationFailed
from sed.ingest.mapping import MappingSpec, choose_mapping, load_all_mappings, resolve_as_of, resolve_columns
from sed.ingest.pii import PiiConfig, PiiProcessor
from sed.ingest.readers import RawTable
from sed.ingest.resolve import Resolver, mark_resolved, refresh_suggestions
from sed.ingest.targets import KIND_ORDER, TARGET_ORDER, TARGETS, Ctx, Reject, Target
from sed.ingest.transforms import TransformError, apply_transform
from sed.paths import Paths
from sed.salt import require_salt
from sed.settings import load_layered, load_settings, read_yaml

CHUNK = 5000
MANIFEST = "_manifest.json"
SOFT_DELETE_GUARD = 0.20
SKIP_NAMES = {MANIFEST, "desktop.ini", "thumbs.db"}


@dataclass
class ImportOptions:
    files: list[Path] = field(default_factory=list)
    inbox: bool = False
    mapping: str | None = None
    dry_run: bool = False
    force: bool = False
    as_of: date | None = None
    allow_unmanifested: bool = False
    move_files: bool = True
    sample_rows: int = 20


@dataclass
class Entry:
    path: Path
    sha256: str
    in_inbox: bool
    spec: MappingSpec | None = None
    table: RawTable | None = None
    score: float = 0.0
    error: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    if path.is_dir():
        for child in sorted(p for p in path.rglob("*") if p.is_file()):
            h.update(child.relative_to(path).as_posix().encode("utf-8"))
            h.update(hashlib.sha256(child.read_bytes()).digest())
        return h.hexdigest()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _inbox_entries(paths: Paths) -> list[Path]:
    if not paths.inbox.is_dir():
        return []
    out = []
    for p in sorted(paths.inbox.iterdir()):
        if p.name.lower() in SKIP_NAMES or p.name in {"processed", "rejected"} or p.name.startswith("~$"):
            continue
        if p.is_dir() and not (p / "index.html").is_file():
            continue
        out.append(p)
    return out


def _load_manifest(inbox: Path) -> dict[str, Any] | None:
    f = inbox / MANIFEST
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationFailed(f"Invalid {MANIFEST} in inbox: {exc}") from exc


def _check_data_class(paths: Paths, entries: list[Entry], opts: ImportOptions) -> None:
    manifest = _load_manifest(paths.inbox) or {}
    manifest_class = manifest.get("data_class")
    listed = manifest.get("files", {})
    if paths.data_class == "real":
        synthetic = [e.path.name for e in entries if manifest_class == "synthetic" and e.path.name in listed]
        if manifest_class == "synthetic" or synthetic:
            raise PreconditionFailed(
                "The inbox contains a SYNTHETIC data manifest; refusing to import synthetic files into the real "
                "profile.",
                synthetic[:10],
            )
        return
    if opts.allow_unmanifested:
        return
    unlisted = [e.path.name for e in entries if e.path.name not in listed or manifest_class != "synthetic"]
    if unlisted:
        raise PreconditionFailed(
            "Synthetic profile only imports files listed in the generator manifest (use --allow-unmanifested for "
            "hand-made synthetic fixtures). Real exports belong in the real profile.",
            unlisted[:10],
        )


def _target_order(entry: Entry) -> tuple[int, int, int, str]:
    spec = entry.spec
    assert spec is not None
    kind_rank = KIND_ORDER.get(spec.constants.get("kind", ""), 0)
    mode_rank = 1 if spec.load_mode == "active_snapshot" else 0
    return (TARGET_ORDER.index(spec.target), mode_rank, kind_rank, entry.path.name)


def _move(path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / path.name
    counter = 1
    while target.exists():
        target = dest_dir / f"{path.stem}.{counter}{path.suffix}"
        counter += 1
    shutil.move(str(path), str(target))
    return target


# ---------------------------------------------------------------------------
# mapping rows -> records
# ---------------------------------------------------------------------------


@dataclass
class MappedFile:
    rows: list[dict[str, Any]]
    rejects: list[tuple[int, str, dict[str, Any]]]
    transform_errors: Counter[str]
    duplicates: int
    missing_optional: list[str]
    samples: list[dict[str, Any]]
    ctx: Ctx


def map_table(
    spec: MappingSpec, table: RawTable, target: Target, ctx: Ctx, pii: PiiProcessor, display_names: bool, samples: int
) -> MappedFile:
    index, missing = resolve_columns(spec, table.columns)
    if missing:
        raise ValidationFailed(
            f"Required columns missing for mapping '{spec.name}': {missing}", {"columns": table.columns}
        )
    missing_optional = [f for f in spec.fields if f not in index]
    raw_keep_idx = {name: i for i, name in enumerate(table.columns) if name in spec.raw_keep}

    person_fields = [f for f, s in spec.fields.items() if s.pii == "person" and f in index]
    for row in table.rows:  # pass 1: grow the person dictionary before scrubbing any text
        for f in person_fields:
            value = row[index[f]]
            if value not in (None, ""):
                pii.register_person(str(value))

    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    rejects: list[tuple[int, str, dict[str, Any]]] = []
    transform_errors: Counter[str] = Counter()
    duplicates = 0
    sample_out: list[dict[str, Any]] = []
    for r_idx, row in enumerate(table.rows, start=table.header_row + 1):
        rec: dict[str, Any] = {}
        raw_text: dict[str, Any] = {}
        bad: str | None = None
        for fname, fspec in spec.fields.items():
            value = row[index[fname]] if fname in index else fspec.default
            opts = dict(fspec.options)
            if fspec.formats:
                opts["formats"] = fspec.formats
            if fspec.transform == "datetime":
                opts.setdefault("tz", spec.source_tz)
            try:
                value = apply_transform(fspec.transform, value, **opts)
            except TransformError as exc:
                transform_errors[fname] += 1
                if fspec.required:
                    bad = f"invalid {fname}: {exc}"
                    break
                value = None
            if value in (None, "") and fspec.required:
                bad = f"missing {fname}"
                break
            if fspec.pii == "person":
                raw_person = value
                value = pii.register_person(value) if value not in (None, "") else None
                if display_names:
                    pii.remember_display_name(value, raw_person)
            elif fspec.pii == "free_text":
                raw_text[fname] = value
                value = pii.scrub(value, fname) if isinstance(value, str) else value
            rec[fname] = value
        if bad is None and raw_keep_idx:
            kept = {name: row[i] for name, i in raw_keep_idx.items() if row[i] not in (None, "")}
            rec["__raw_keep__"] = json.dumps(kept, ensure_ascii=False, default=str) if kept else None
        if bad is None:
            try:
                built = target.build(rec, raw_text, ctx)
            except Reject as exc:
                bad = str(exc)
            except (ValueError, TypeError) as exc:
                bad = f"invalid value: {exc}"
        if bad is not None:
            payload = {k: v for k, v in rec.items() if spec.fields.get(k) is None or spec.fields[k].pii != "free_text"}
            rejects.append((r_idx, bad, payload))
            continue
        key = tuple(built[k] for k in target.key)
        if key in rows:
            duplicates += 1
        rows[key] = built
        if len(sample_out) < samples:
            sample_out.append(built)
    return MappedFile(list(rows.values()), rejects, transform_errors, duplicates, missing_optional, sample_out, ctx)


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def _batch_column(target: Target) -> str:
    return "source_batch_id" if target.table == "doc_page" else "last_batch_id"


def upsert_sql(target: Target) -> str:
    cols = [*target.columns, _batch_column(target)]
    non_key = [c for c in target.columns if c not in target.key]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in [*non_key, _batch_column(target)])
    differs = " OR ".join(f"{target.table}.{c} IS NOT excluded.{c}" for c in non_key) or "0"
    if target.updated_field:
        u = target.updated_field
        condition = (
            f"excluded.{u} > {target.table}.{u} OR {target.table}.{u} IS NULL "
            f"OR (excluded.{u} = {target.table}.{u} AND ({differs}))"
        )
    else:
        condition = differs
    return (
        f"INSERT INTO {target.table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(target.key)}) DO UPDATE SET {updates} WHERE {condition}"
    )


def _existing_keys(conn, target: Target, keys: list[tuple[Any, ...]]) -> int:
    if not keys:
        return 0
    if len(target.key) == 1:
        marks = ", ".join("?" for _ in keys)
        sql = f"SELECT COUNT(*) FROM {target.table} WHERE {target.key[0]} IN ({marks})"
        return conn.execute(sql, [k[0] for k in keys]).fetchone()[0]
    values = ", ".join("(" + ", ".join("?" for _ in target.key) + ")" for _ in keys)
    sql = f"SELECT COUNT(*) FROM {target.table} WHERE ({', '.join(target.key)}) IN (VALUES {values})"
    return conn.execute(sql, [v for k in keys for v in k]).fetchone()[0]


def write_rows(conn, target: Target, rows: list[dict[str, Any]], batch_id: int) -> dict[str, int]:
    stats = {"inserted": 0, "updated": 0, "unchanged": 0}
    if not target.table:  # registry-only targets (people directory)
        stats["unchanged"] = len(rows)
        return stats
    sql = upsert_sql(target)
    chunk_keys = max(1, min(CHUNK, 30_000 // max(1, len(target.key))))
    for start in range(0, len(rows), chunk_keys):
        chunk = rows[start : start + chunk_keys]
        keys = [tuple(r[k] for k in target.key) for r in chunk]
        with db.write_tx(conn):
            existing = _existing_keys(conn, target, keys)
            # cursor.rowcount sums sqlite3_changes(), which excludes rows written by triggers (FTS index).
            cur = conn.executemany(sql, [[*(r[c] for c in target.columns), batch_id] for r in chunk])
            changed = max(0, cur.rowcount)
        inserted = len(chunk) - existing
        updated = max(0, changed - inserted)
        stats["inserted"] += inserted
        stats["updated"] += updated
        stats["unchanged"] += existing - updated
    return stats


# ---------------------------------------------------------------------------
# main entry points
# ---------------------------------------------------------------------------


def _seed_config_aliases(paths: Paths, resolver: Resolver) -> int:
    seeded = 0
    aliases_file = paths.config / "aliases.yaml"
    if aliases_file.is_file():
        for kind, mapping in (read_yaml(aliases_file) or {}).items():
            for raw, target_id in (mapping or {}).items():
                resolver.add_alias(kind, raw, str(target_id), "seed")
                seeded += 1
    ci_file = paths.config / "ci_to_app.yaml"
    if ci_file.is_file():
        for ci, app in (read_yaml(ci_file) or {}).items():
            resolver.add_alias("ci", ci, str(app), "seed")
            seeded += 1
    return seeded


def _apply_vendor_group_overrides(paths: Paths, resolver: Resolver) -> None:
    groups = load_layered("vendor_groups.yaml", paths).get("groups") or {}
    for group, vendor in groups.items():
        vendor_id = resolver.resolve("vendor", vendor, record_unmapped=False)
        if vendor and not vendor_id:
            resolver.unmapped["vendor"][str(vendor)] += 1
        resolver.set_group(group, vendor_id, None)


def run_import(paths: Paths, opts: ImportOptions) -> dict[str, Any]:
    if not paths.db.exists():
        raise PreconditionFailed(f"No database for profile '{paths.profile}'; run `sed init` first.")
    conn = db.connect(paths.db)
    try:
        return _run(conn, paths, opts)
    finally:
        conn.close()


def _run(conn, paths: Paths, opts: ImportOptions) -> dict[str, Any]:
    meta = db.all_meta(conn)
    if meta.get("data_class") != paths.data_class:
        raise PreconditionFailed(f"DB data_class {meta.get('data_class')} does not match profile {paths.profile}.")
    salt = require_salt(paths.salt_file, meta.get("salt_fingerprint"))
    settings = load_settings(paths)
    pii_cfg = PiiConfig.from_dict(load_layered("pii.yaml", paths))
    fx = {k.upper(): float(v) for k, v in (load_layered("fx.yaml", paths).get("rates") or {}).items()}
    mappings = load_all_mappings(paths)
    pii_mode = meta.get("pii_mode", "pseudonymize")
    display_names = bool(settings.display_names and paths.data_class == "real" and pii_mode == "pseudonymize")

    raw_paths = [p for p in opts.files] if opts.files else []
    if opts.inbox or not raw_paths:
        raw_paths += _inbox_entries(paths)
    inbox_resolved = paths.inbox.resolve()
    entries: list[Entry] = []
    seen_sha: set[str] = set()
    skipped: list[dict[str, Any]] = []
    imported_shas = {r[0] for r in conn.execute("SELECT file_sha256 FROM import_batch WHERE status = 'completed'")}
    for p in raw_paths:
        if not p.exists():
            raise ValidationFailed(f"File not found: {p}")
        sha = file_sha256(p)
        in_inbox = p.resolve().parent == inbox_resolved
        if sha in imported_shas or sha in seen_sha:
            skipped.append({"file": p.name, "reason": "already imported (same sha256)"})
            if in_inbox and opts.move_files and not opts.dry_run:
                _move(p, paths.processed / datetime.now(UTC).strftime("%Y-%m-%d"))
            continue
        seen_sha.add(sha)
        entries.append(Entry(p, sha, in_inbox))

    _check_data_class(paths, entries, opts)

    for entry in entries:
        try:
            entry.spec, entry.table, entry.score = choose_mapping(entry.path, mappings, opts.mapping)
        except SedError as exc:
            entry.error = exc.to_dict()
    planned = sorted((e for e in entries if e.spec), key=_target_order)
    unmatched = [e for e in entries if not e.spec]

    if not opts.dry_run and planned:
        has_data = conn.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] > 0
        age = db.last_backup_age_hours(paths.backups)
        if has_data and (age is None or age > 24):
            db.backup(conn, paths.backups, reason="pre-import")

    known_keys = {r[0]: r[1] for r in conn.execute("SELECT name_hash, pid FROM person_key")}
    pii = PiiProcessor(salt, pii_mode, pii_cfg, known_keys)
    people_file = paths.config / "people.csv"
    if people_file.is_file():
        from sed.ingest.readers import read_table

        people = read_table(people_file)
        name_col = next((i for i, c in enumerate(people.columns) if c.strip().lower() in {"name", "display_name"}), 0)
        pii.register_many(row[name_col] for row in people.rows)

    resolver = Resolver(conn)
    seeded = _seed_config_aliases(paths, resolver)
    if seeded and not opts.dry_run:
        with db.write_tx(conn):
            resolver.flush(None)

    results: list[dict[str, Any]] = []
    for entry in unmatched:
        results.append({"file": entry.path.name, "status": "error", "error": entry.error})
        if entry.in_inbox and opts.move_files and not opts.dry_run:
            _move(entry.path, paths.rejected)

    for entry in planned:
        try:
            result = _import_entry(
                conn, paths, opts, entry, resolver, pii, salt, settings.base_currency, fx, display_names
            )
        except SedError as exc:
            result = {"file": entry.path.name, "mapping": entry.spec.name, "status": "error", "error": exc.to_dict()}
            if entry.in_inbox and opts.move_files and not opts.dry_run and exc.exit_code == 2:
                _move(entry.path, paths.rejected)
        results.append(result)

    if not opts.dry_run:
        with db.write_tx(conn):
            refresh_suggestions(conn)
            mark_resolved(conn)

    errors = [r for r in results if r["status"] == "error"]
    return {
        "profile": paths.profile,
        "dry_run": opts.dry_run,
        "files": results,
        "skipped": skipped,
        "summary": {
            "imported": sum(1 for r in results if r["status"] == "completed"),
            "errors": len(errors),
            "skipped": len(skipped),
            "rows_read": sum(r.get("rows_read", 0) for r in results),
            "rows_rejected": sum(r.get("rows_rejected", 0) for r in results),
        },
    }


def _import_entry(conn, paths, opts, entry, resolver, pii, salt, base_currency, fx, display_names) -> dict[str, Any]:
    spec, table = entry.spec, entry.table
    target = TARGETS[spec.target]
    as_of = resolve_as_of(spec, entry.path, opts.as_of)
    if spec.target == "ticket" and spec.constants.get("kind") in {"incident", "sc_req_item"}:
        _apply_vendor_group_overrides(paths, resolver)
    ctx = Ctx(conn, resolver, pii, salt, None, as_of, base_currency, fx, dict(spec.constants))
    mapped = map_table(spec, table, target, ctx, pii, display_names, opts.sample_rows)
    rows = mapped.rows
    if spec.load_mode == "append_snapshot" and not as_of:
        raise ValidationFailed(f"{entry.path.name}: append_snapshot mapping '{spec.name}' needs an as-of date")

    dq: dict[str, Any] = {
        "reader": {
            "encoding": table.encoding,
            "delimiter": table.delimiter,
            "sheet": table.sheet,
            "header_row": table.header_row,
            "warnings": table.warnings,
        },
        "match_score": round(entry.score, 3),
        "missing_optional_fields": mapped.missing_optional,
        "transform_errors": dict(mapped.transform_errors),
        "rejects": dict(Counter(reason for _, reason, _ in mapped.rejects)),
        "duplicates_in_file": mapped.duplicates,
        "derived_warnings": dict(ctx.warnings),
    }

    result: dict[str, Any] = {
        "file": entry.path.name,
        "mapping": spec.name,
        "target": spec.target,
        "load_mode": spec.load_mode,
        "as_of": as_of,
        "rows_read": len(table.rows),
        "rows_valid": len(rows),
        "rows_rejected": len(mapped.rejects),
    }

    soft_delete_keys: list[tuple[Any, ...]] = []
    if spec.load_mode == "full_snapshot" and target.soft_delete:
        active = {
            tuple(r)
            for r in conn.execute(f"SELECT {', '.join(target.key)} FROM {target.table} WHERE is_deleted = 0").fetchall()
        }
        file_keys = {tuple(r[k] for k in target.key) for r in rows}
        soft_delete_keys = sorted(active - file_keys, key=str)
        if active and len(soft_delete_keys) / len(active) > SOFT_DELETE_GUARD and not opts.force:
            raise PreconditionFailed(
                f"{entry.path.name}: {len(soft_delete_keys)} of {len(active)} existing {target.table} rows would "
                f"disappear (> {int(SOFT_DELETE_GUARD * 100)}%). Is this a partial export? Re-run with --force if not.",
            )

    if opts.dry_run:
        unmapped = {k: dict(v.most_common(20)) for k, v in resolver.unmapped.items()}
        resolver.unmapped.clear()
        result.update(
            {
                "status": "dry_run",
                "dq": {**dq, "unmapped": unmapped, "would_soft_delete": len(soft_delete_keys)},
                "samples": mapped.samples,
            }
        )
        return result

    with db.write_tx(conn):
        cur = conn.execute(
            "INSERT INTO import_batch (file_name, file_sha256, mapping_name, mapping_sha256, load_mode, as_of, "
            "status, rows_read, imported_at) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)",
            (
                entry.path.name,
                entry.sha256,
                spec.name,
                spec.sha256,
                spec.load_mode,
                as_of,
                len(table.rows),
                db.utc_now(),
            ),
        )
        batch_id = int(cur.lastrowid)
    ctx.batch_id = batch_id

    try:
        if spec.load_mode == "append_snapshot" and target.snapshot_field:
            with db.write_tx(conn):
                if target.table == "cost_line":
                    line_type = (spec.constants.get("line_type") or "budget").lower()
                    conn.execute("DELETE FROM cost_line WHERE as_of = ? AND line_type = ?", (as_of, line_type))
                else:
                    conn.execute(f"DELETE FROM {target.table} WHERE {target.snapshot_field} = ?", (as_of,))
        stats = write_rows(conn, target, rows, batch_id)

        post: dict[str, Any] = {}
        with db.write_tx(conn):
            if soft_delete_keys:
                for key in soft_delete_keys:
                    where = " AND ".join(f"{k} = ?" for k in target.key)
                    conn.execute(
                        f"UPDATE {target.table} SET is_deleted = 1, last_batch_id = ? WHERE {where}", (batch_id, *key)
                    )
                post["soft_deleted"] = len(soft_delete_keys)
            if spec.load_mode == "active_snapshot" and target.active_scope:
                post["stale_open_flagged"] = _apply_active_snapshot(conn, target, rows, as_of)
            if target.after_load:
                target.after_load(ctx, rows)
            unmapped = resolver.flush(batch_id)
            for name_hash, pid in pii.new_keys():
                conn.execute("INSERT OR IGNORE INTO person_key (name_hash, pid) VALUES (?, ?)", (name_hash, pid))
            if display_names:
                conn.executemany(
                    "INSERT INTO person_display (pid, display_name) VALUES (?, ?) "
                    "ON CONFLICT (pid) DO UPDATE SET display_name = excluded.display_name",
                    list(pii.display_names.items()),
                )
                pii.display_names.clear()
            conn.executemany(
                "INSERT INTO row_reject (batch_id, row_num, reason, scrubbed_payload_json) VALUES (?, ?, ?, ?)",
                [
                    (batch_id, r, reason, json.dumps(payload, ensure_ascii=False, default=str))
                    for r, reason, payload in mapped.rejects[:5000]
                ],
            )
            dq.update({"unmapped": unmapped, **post, "derived_warnings": dict(ctx.warnings)})
            severity = "warn" if (mapped.rejects or unmapped or table.warnings or ctx.warnings) else "ok"
            dq["severity"] = severity
            conn.execute(
                "UPDATE import_batch SET status = 'completed', rows_inserted = ?, rows_updated = ?, rows_unchanged = "
                "?, "
                "rows_rejected = ?, rows_soft_deleted = ?, dq_json = ? WHERE batch_id = ?",
                (
                    stats["inserted"],
                    stats["updated"],
                    stats["unchanged"],
                    len(mapped.rejects),
                    post.get("soft_deleted", 0),
                    json.dumps(dq, ensure_ascii=False, default=str),
                    batch_id,
                ),
            )
    except BaseException as exc:
        with db.write_tx(conn):
            conn.execute(
                "UPDATE import_batch SET status = 'failed', dq_json = ? WHERE batch_id = ?",
                (json.dumps({"error": str(exc)}, ensure_ascii=False), batch_id),
            )
        raise

    if entry.in_inbox and opts.move_files:
        _move(entry.path, paths.processed / datetime.now(UTC).strftime("%Y-%m-%d"))
    result.update({"status": "completed", "batch_id": batch_id, **stats, "dq": dq})
    return result


def _apply_active_snapshot(conn, target: Target, rows: list[dict[str, Any]], as_of: str | None) -> int:
    column, value = target.active_scope  # type: ignore[misc]
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _active_keys (k TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _active_keys")
    conn.executemany("INSERT OR IGNORE INTO _active_keys (k) VALUES (?)", [(r[target.key[0]],) for r in rows])
    cutoff = f"{as_of}T23:59:59Z" if as_of else "9999-12-31"
    conn.execute(
        f"UPDATE {target.table} SET stale_open = 0 WHERE {column} = ? AND (is_open = 0 OR {target.key[0]} IN "
        "(SELECT k FROM _active_keys))",
        (value,),
    )
    cur = conn.execute(
        f"UPDATE {target.table} SET stale_open = 1 WHERE {column} = ? AND is_open = 1 "
        f"AND {target.key[0]} NOT IN (SELECT k FROM _active_keys) AND opened_at <= ? AND sys_updated_on <= ?",
        (value, cutoff, cutoff),
    )
    return cur.rowcount


def reresolve(paths: Paths) -> dict[str, int]:
    """Re-link rows from stored raw values after aliases changed (no re-import)."""
    conn = db.connect(paths.db)
    try:
        resolver = Resolver(conn)
        _apply_vendor_group_overrides(paths, resolver)
        counts: dict[str, int] = {}
        updates: dict[str, list[tuple[Any, ...]]] = {
            k: [] for k in ("ticket", "contract", "license", "cost_line", "application")
        }
        for r in conn.execute(
            "SELECT ticket_id, business_service_raw, cmdb_ci_raw, assignment_group, app_id, vendor_id FROM ticket"
        ):
            app = resolver.resolve_app(r["business_service_raw"], r["cmdb_ci_raw"])
            vendor = resolver.vendor_for_group(r["assignment_group"])
            if app != r["app_id"] or vendor != r["vendor_id"]:
                updates["ticket"].append((app, vendor, r["ticket_id"]))
        for table, key in (("contract", "contract_id"), ("license", "license_id"), ("cost_line", "cost_line_id")):
            for r in conn.execute(f"SELECT {key}, app_raw, vendor_raw, app_id, vendor_id FROM {table}"):
                app = resolver.resolve("app", r["app_raw"], record_unmapped=False)
                vendor = resolver.resolve("vendor", r["vendor_raw"], record_unmapped=False)
                if (app, vendor) != (r["app_id"], r["vendor_id"]):
                    updates[table].append((app, vendor, r[key]))
        for r in conn.execute("SELECT app_id, vendor_raw, primary_vendor_id FROM application"):
            vendor = resolver.resolve("vendor", r["vendor_raw"], record_unmapped=False)
            if vendor != r["primary_vendor_id"]:
                updates["application"].append((vendor, r["app_id"]))
        resolver.unmapped.clear()
        with db.write_tx(conn):
            conn.executemany("UPDATE ticket SET app_id = ?, vendor_id = ? WHERE ticket_id = ?", updates["ticket"])
            conn.executemany("UPDATE contract SET app_id = ?, vendor_id = ? WHERE contract_id = ?", updates["contract"])
            conn.executemany("UPDATE license SET app_id = ?, vendor_id = ? WHERE license_id = ?", updates["license"])
            conn.executemany(
                "UPDATE cost_line SET app_id = ?, vendor_id = ? WHERE cost_line_id = ?", updates["cost_line"]
            )
            conn.executemany("UPDATE application SET primary_vendor_id = ? WHERE app_id = ?", updates["application"])
            counts = {k: len(v) for k, v in updates.items()}
            counts["unmapped_marked_resolved"] = mark_resolved(conn)
            refresh_suggestions(conn)
        return counts
    finally:
        conn.close()
