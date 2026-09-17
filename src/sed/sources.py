"""Every source two ways (W8): pulled from the tool's API when a connector is configured, or imported from export files
(dropped into the inbox or uploaded in the dashboard). Both paths end in the same import.

- `overview(paths)`: for each mapping of the enabled modules, the connector sources that write files it reads (matched
  on the mapping's file-name globs) and its newest import; for each configured connector, whether it can pull now
  (enabled, credential found, real profile), its sources, watermarks and last pull. No network, no secret values.
- `pull_and_import(paths, connector, ...)`: `sed.connectors.pull.pull`, then the normal import of the files it wrote.
- `upload_and_import(paths, name, data, ...)`: `sed.ingest.upload.save_upload`, then the normal import of that file.

The dashboard runs the last two on its job worker (`/api/sources/{connector}/pull`, `/api/imports/upload`); the CLI
has `sed pull <connector> --import` and `sed sources`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sed import db
from sed.paths import Paths

SAMPLE_STAMP = "20260101T000000"
SNAPSHOT_MODES = ("full_snapshot", "active_snapshot")
KINDS = {"servicenow": "table", "jira": "query", "sharepoint": "list", "confluence": "space", "sap": "odata"}


def _sample_names(connector: str, source: Any) -> list[str]:
    """File names a connector source writes (the pull stamp replaced by a fixed one), for glob matching."""
    if connector == "confluence":
        return [f"confluence_{source.space}_pull_{SAMPLE_STAMP}"]
    if connector == "sap":
        return [f"{source.file_prefix}_pull_{source.key}_{SAMPLE_STAMP}.csv"]
    if connector == "jira":
        return [f"{source.file_prefix}_{SAMPLE_STAMP}.csv"]
    return [f"{source.file_prefix}_pull_{SAMPLE_STAMP}.csv"]


def _incremental(connector: str, source: Any) -> bool:
    """Whether a source pulls only what changed since its watermark (lists and libraries always deliver whole files)."""
    if connector == "servicenow":
        return not source.snapshot
    if connector == "sap":
        return source.updated_property is not None
    return connector in ("jira", "confluence")


def _library_matches(library: Any, spec: Any) -> bool:
    """A document library feeds a mapping when a file name can match both: each mapping glob with its wildcards filled
    in is tested against the library patterns (e.g. `delivery_plan*.xlsx` against `*.xlsx`)."""
    import fnmatch

    probes = [glob.lower().replace("*", "x").replace("?", "x") for glob in spec.match.glob]
    return any(fnmatch.fnmatch(probe, p.lower()) for probe in probes for p in library.patterns)


def _last_pulls(paths: Paths) -> dict[tuple[str, str], dict[str, Any]]:
    log = paths.data_dir / "logs" / "pulls.jsonl"
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not log.is_file():
        return out
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("connector") and entry.get("source"):
            out[(str(entry["connector"]), str(entry["source"]))] = entry
    return out


def overview(paths: Paths, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """The Sources overview. `conn`: a read-only connection to use (the API's per-request one); else one is opened."""
    from sed.connectors.config import CONNECTORS, load_config
    from sed.connectors.credentials import describe
    from sed.connectors.pull import watermark_key
    from sed.ingest.mapping import glob_matches, load_all_mappings
    from sed.ingest.upload import MAX_BYTES, SUFFIXES
    from sed.modules import mapping_index

    config = load_config(paths)
    mappings = load_all_mappings(paths)
    owners = mapping_index(paths)
    pulls = _last_pulls(paths)
    own = conn is None
    if own:
        conn = db.connect(paths.db, readonly=True) if paths.db.exists() else None
    try:
        data_class = (db.get_meta(conn, "data_class") if conn else None) or paths.data_class
        feeds: dict[str, list[str]] = {name: [] for name in mappings}
        connectors = []
        for name in CONNECTORS:
            cfg = getattr(config, name)
            if cfg is None:
                continue
            found = describe(paths, cfg.credential)
            sources = []
            entries = [(s, KINDS[name]) for s in cfg.sources]
            entries += [(lib, "library") for lib in getattr(cfg, "libraries", [])]
            for src, kind in entries:
                if kind == "library":
                    fed = [m for m, spec in mappings.items() if spec.format == "table" and _library_matches(src, spec)]
                else:
                    names = _sample_names(name, src)
                    fed = [m for m, spec in mappings.items() if any(glob_matches(spec, n) for n in names)]
                for mapping in fed:
                    feeds[mapping].append(f"{name}/{src.key}")
                last = pulls.get((name, src.key)) or {}
                snapshots = [m for m in fed if mappings[m].load_mode in SNAPSHOT_MODES]
                warning = None
                if snapshots and _incremental(name, src):
                    fix = "set `snapshot: true` on the source" if name == "servicenow" else "use a file export instead"
                    warning = f"incremental pulls feed snapshot exports ({', '.join(snapshots)}): {fix}"
                own_credential = getattr(src, "credential", None)
                sources.append(
                    {
                        "source": src.key,
                        "kind": kind,
                        "mappings": fed,
                        "watermark": db.get_meta(conn, watermark_key(name, src.key)) if conn else None,
                        "last_pull_at": last.get("pulled_at"),
                        "last_rows": last.get("rows"),
                        "last_file": last.get("file"),
                        "credential": own_credential,
                        "credential_found_in": describe(paths, own_credential) if own_credential else None,
                        "warning": warning,
                    }
                )
            reason = _refusal(cfg, bool(sources), found, data_class)
            connectors.append(
                {
                    "connector": name,
                    "enabled": cfg.enabled,
                    "credential": cfg.credential,
                    "credential_found_in": found,
                    "can_pull": reason is None,
                    "reason": reason,
                    "sources": sources,
                }
            )
        latest: dict[str, dict[str, Any]] = {}
        if conn is not None:
            for r in conn.execute(
                "SELECT mapping_name, file_name, status, imported_at FROM import_batch "
                "WHERE batch_id IN (SELECT MAX(batch_id) FROM import_batch GROUP BY mapping_name)"
            ):
                latest[r["mapping_name"]] = dict(r)
        files = []
        for name, spec in sorted(mappings.items()):
            last = latest.get(name) or {}
            files.append(
                {
                    "mapping": name,
                    "module": owners.get(name, (None, None))[0],
                    "target": spec.target,
                    "load_mode": spec.load_mode,
                    "format": spec.format,
                    "patterns": list(spec.match.glob),
                    "connector_sources": sorted(feeds[name]),
                    "last_file": last.get("file_name"),
                    "last_status": last.get("status"),
                    "last_imported_at": last.get("imported_at"),
                }
            )
    finally:
        if own and conn is not None:
            conn.close()
    return {
        "profile": paths.profile,
        "data_class": data_class,
        "upload": {"suffixes": list(SUFFIXES), "max_bytes": MAX_BYTES, "needs_confirmation": data_class != "real"},
        "connectors": connectors,
        "files": files,
    }


def _refusal(cfg: Any, has_sources: bool, credential_found: str | None, data_class: str) -> str | None:
    if not cfg.enabled:
        return "not enabled in connectors.yaml"
    if not has_sources:
        return "no sources configured"
    if credential_found is None:
        return f"credential '{cfg.credential}' not found"
    if data_class != "real":
        return "connectors pull real systems: use the real profile"
    return None


def pull_refusal(paths: Paths, connector: str) -> str | None:
    """Why `connector` cannot pull on this profile now, or None (the same rules the Sources overview shows)."""
    from sed.connectors.config import load_config
    from sed.connectors.credentials import describe

    cfg = getattr(load_config(paths), connector, None)
    if cfg is None:
        return "not configured in connectors.yaml"
    has_sources = bool(cfg.sources) or bool(getattr(cfg, "libraries", []))
    return _refusal(cfg, has_sources, describe(paths, cfg.credential), paths.data_class)


def import_paths(paths: Paths, files: list[Path], *, synthetic_ok: bool = False) -> dict[str, Any]:
    from sed.ingest.loader import ImportOptions, run_import

    opts = ImportOptions(files=files, allow_unmanifested=synthetic_ok and paths.data_class != "real")
    return run_import(paths, opts)


def upload_and_import(paths: Paths, name: str, data: bytes, *, synthetic_ok: bool = False) -> dict[str, Any]:
    from sed.ingest.upload import save_upload

    saved = save_upload(paths, name, data, synthetic_ok=synthetic_ok)
    result = import_paths(paths, [Path(saved["path"])], synthetic_ok=synthetic_ok)
    return {"upload": {k: v for k, v in saved.items() if k != "path"}, "import": result}


def pull_and_import(
    paths: Paths, connector: str, *, source: str | None = None, full: bool = False, **pull_options: Any
) -> dict[str, Any]:
    """Pull, then import exactly the files the pull wrote (nothing else in the inbox). `pull_options` go to `pull`
    (`since`, and in tests `transport`, `now`, `sleep`, `allow_synthetic`)."""
    from sed.connectors.pull import pull

    pulled = pull(paths, connector, source=source, full=full, **pull_options)
    written = [paths.inbox / name for s in pulled["sources"] for name in s.get("files") or []]
    synthetic_ok = bool(pull_options.get("allow_synthetic"))
    result = import_paths(paths, written, synthetic_ok=synthetic_ok) if written else None
    return {"pull": pulled, "import": result, "finished_at": datetime.now(UTC).isoformat()}
