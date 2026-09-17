"""`sed pull <connector>`: fetch each configured source and write export-shaped files into the inbox.

Delta sources (ServiceNow tables, Jira, Confluence) read from their watermark (`meta`
`connector.<connector>.<source>.watermark`, the newest update time already pulled) minus `overlap_minutes`; `--since`
overrides it and `--full` ignores it. SharePoint lists are full snapshots; SharePoint document libraries (`libraries`)
download the matching files changed since the watermark under their own names. The watermark advances only after the
files are written, and only to the newest update actually pulled (a row or file cap therefore resumes where it
stopped). `--dry-run` reads but writes nothing. Every pull is appended to `logs/pulls.jsonl`. `--import` runs the
normal import on the files written (`sed.sources.pull_and_import`).
"""

from __future__ import annotations

import base64
import csv
import io
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sed import db
from sed.connectors import sources as fetchers
from sed.connectors.config import CONNECTORS, ConnectorBase, SharePointLibrary, load_config
from sed.connectors.credentials import get_credential
from sed.connectors.http import Client, HttpTransport, Transport
from sed.errors import PreconditionFailed, ValidationFailed
from sed.paths import Paths


def watermark_key(connector: str, source: str) -> str:
    return f"connector.{connector}.{source}.watermark"


def _headers(paths: Paths, cfg: ConnectorBase) -> dict[str, str]:
    value = get_credential(paths, cfg.credential)
    if cfg.auth == "basic":
        token = base64.b64encode(f"{cfg.user}:{value}".encode()).decode("ascii")
        return {"Authorization": f"Basic {token}"}
    return {"Authorization": f"Bearer {value}"}


def _csv_text(headers: list[str], rows: list[list[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(text.encode("utf-8-sig"))
    tmp.replace(path)


def _parse_since(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationFailed(f"--since must be an ISO date or date-time, got '{value}'") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def pull(
    paths: Paths,
    connector: str,
    *,
    source: str | None = None,
    since: str | None = None,
    full: bool = False,
    dry_run: bool = False,
    transport: Transport | None = None,
    now: datetime | None = None,
    allow_synthetic: bool = False,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    if connector not in CONNECTORS:
        raise ValidationFailed(f"Unknown connector '{connector}' (available: {', '.join(CONNECTORS)})")
    if not paths.db.exists():
        raise PreconditionFailed(f"No database for profile '{paths.profile}'; run `sed init` first.")
    config = load_config(paths)
    cfg = getattr(config, connector)
    if cfg is None or not cfg.enabled:
        raise PreconditionFailed(f"Connector '{connector}' is not enabled in connectors.yaml of this profile")
    conn = db.connect(paths.db)
    try:
        data_class = db.get_meta(conn, "data_class") or "synthetic"
        if data_class == "synthetic" and not allow_synthetic:
            raise PreconditionFailed("Connectors pull real systems: use the real profile (the synthetic one refuses)")
        wanted: list[Any] = [s for s in cfg.sources if source is None or s.key == source]
        if connector == "sharepoint":
            wanted += [lib for lib in cfg.libraries if source is None or lib.key == source]
        if not wanted:
            raise ValidationFailed(f"Connector '{connector}' has no source '{source}'")
        defaults = config.defaults
        http = transport or HttpTransport(defaults.timeout_seconds)
        extra = {"sleep": sleep} if sleep is not None else {}

        def client_for(src: Any) -> Client:
            # A source may name its own gateway and account (SAP systems); otherwise the connector's.
            overrides = {k: getattr(src, k) for k in ("base_url", "user", "credential") if getattr(src, k, None)}
            effective = cfg.model_copy(update=overrides) if overrides else cfg
            return Client(
                http,
                effective.base_url,
                _headers(paths, effective),
                pause_seconds=defaults.pause_seconds,
                max_pages=defaults.max_pages,
                **extra,
            )

        clients: dict[tuple[str, str], Client] = {}
        stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S")
        results = []
        for src in wanted:
            identity = (
                getattr(src, "base_url", None) or cfg.base_url,
                getattr(src, "credential", None) or cfg.credential,
            )
            client = clients.setdefault(identity, client_for(src))
            key = watermark_key(connector, src.key)
            stored = db.get_meta(conn, key)
            start: datetime | None = None
            if since:
                start = _parse_since(since)
            elif stored and not full and not getattr(src, "snapshot", False):
                start = datetime.fromisoformat(stored) - timedelta(minutes=defaults.overlap_minutes)
            library = isinstance(src, SharePointLibrary)
            if library:
                fetched = fetchers.fetch_library(client, src, start, defaults.page_size)
                count = len(fetched.files)
            else:
                fetched, target, text = _fetch(client, connector, cfg, src, start, defaults, paths, stamp)
                count = len(fetched.pages) if connector == "confluence" else len(fetched.rows)
            if getattr(src, "snapshot", False) and fetched.capped:
                raise PreconditionFailed(
                    f"{connector}/{src.key} is a snapshot source and reached max_rows: raise defaults.max_rows so the "
                    "whole table fits (a partial snapshot would mark the missing rows deleted)"
                )
            files: list[str] = []
            if not dry_run and count:
                if library:
                    files = _download_library(client, src, fetched.files, paths.inbox)
                elif connector == "confluence":
                    for name, page_text in text.items():
                        _write(target / name, page_text)
                    files = [target.name]
                else:
                    _write(target, text)
                    files = [target.name]
            written = files[0] if files else None
            advanced = None
            if not dry_run and fetched.newest is not None:
                advanced = fetched.newest.astimezone(UTC).isoformat()
                if stored is None or advanced > stored:
                    with db.write_tx(conn):
                        db.set_meta(conn, key, advanced)
                else:
                    advanced = stored
            entry = {
                "connector": connector,
                "source": src.key,
                "from": start.astimezone(UTC).isoformat() if start else None,
                "rows": count,
                "capped": fetched.capped,
                "file": written,
                "files": files,
                "watermark": advanced if not dry_run else stored,
                "dry_run": dry_run,
            }
            results.append(entry)
            if not dry_run:
                _log(paths, {**entry, "pulled_at": (now or datetime.now(UTC)).isoformat(), "profile": paths.profile})
    finally:
        conn.close()
    return {
        "connector": connector,
        "profile": paths.profile,
        "sources": results,
        "pages": sum(c.pages for c in clients.values()),
        "next": "uv run sed import --inbox" if any(r["file"] for r in results) else None,
    }


def _download_library(client: Client, src: SharePointLibrary, files: list[Any], inbox: Path) -> list[str]:
    """Download each listed file into the inbox under its sanitised name (never overwriting); types SED cannot read
    are skipped."""
    from sed.ingest.upload import MAX_BYTES, TABLE_SUFFIXES, free_path, safe_name, write_atomic

    inbox.mkdir(parents=True, exist_ok=True)
    written = []
    for item in files:
        name = safe_name(item.name)
        if Path(name).suffix.lower() not in TABLE_SUFFIXES:
            continue
        content = client.download(item.download_url, src.download_hosts, MAX_BYTES)
        target = free_path(inbox, name)
        write_atomic(target, content)
        written.append(target.name)
    return written


def _fetch(client, connector, cfg, src, start, defaults, paths, stamp) -> tuple[Any, Path, Any]:
    inbox = paths.inbox
    if connector == "servicenow":
        fetched = fetchers.fetch_servicenow(client, src, start, defaults.page_size, defaults.max_rows)
        return fetched, inbox / f"{src.file_prefix}_pull_{stamp}.csv", _csv_text(fetched.headers, fetched.rows)
    if connector == "jira":
        fetched = fetchers.fetch_jira(client, cfg.api_path, src, start, defaults.page_size, defaults.max_rows)
        return fetched, inbox / f"{src.file_prefix}_{stamp}.csv", _csv_text(fetched.headers, fetched.rows)
    if connector == "sap":
        fetched = fetchers.fetch_sap(client, cfg.odata_version, src, start, defaults.page_size, defaults.max_rows)
        target = inbox / f"{src.file_prefix}_pull_{src.key}_{stamp}.csv"
        return fetched, target, _csv_text(fetched.headers, fetched.rows)
    if connector == "sharepoint":
        fetched = fetchers.fetch_sharepoint(client, src, defaults.page_size, defaults.max_rows)
        return fetched, inbox / f"{src.file_prefix}_pull_{stamp}.csv", _csv_text(fetched.headers, fetched.rows)
    fetched = fetchers.fetch_confluence(client, cfg.api_path, src, start, defaults.page_size, defaults.max_rows)
    files = {"index.html": f"<html><body><h1>{src.space}</h1></body></html>\n"}
    for page in fetched.pages:
        safe = "".join(ch if ch.isalnum() else "-" for ch in page["title"])[:60].strip("-") or "page"
        files[f"{safe}_{page['id']}.html"] = fetchers.confluence_page_html(src.space, page)
    return fetched, inbox / f"confluence_{src.space}_pull_{stamp}", files


def _log(paths: Paths, entry: dict[str, Any]) -> None:
    log = paths.data_dir / "logs" / "pulls.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def status(paths: Paths) -> dict[str, Any]:
    """Configured connectors with their sources, secret location and watermarks (no network, no secret values)."""
    from sed.connectors.credentials import describe

    config = load_config(paths)
    conn = None
    if paths.db.exists():
        try:
            conn = db.connect(paths.db, readonly=True)
            conn.execute("SELECT 1 FROM meta LIMIT 1").fetchall()
        except sqlite3.DatabaseError:  # unreadable database: doctor reports it separately
            if conn is not None:
                conn.close()
            conn = None
    try:
        out = []
        for name in CONNECTORS:
            cfg = getattr(config, name)
            if cfg is None:
                continue
            out.append(
                {
                    "connector": name,
                    "enabled": cfg.enabled,
                    "credential": cfg.credential,
                    "credential_found_in": describe(paths, cfg.credential),
                    "sources": [
                        {
                            "source": s.key,
                            "watermark": db.get_meta(conn, watermark_key(name, s.key)) if conn else None,
                            **(
                                {"credential": s.credential, "credential_found_in": describe(paths, s.credential)}
                                if getattr(s, "credential", None)
                                else {}
                            ),
                        }
                        for s in cfg.sources
                    ],
                }
            )
        return {"profile": paths.profile, "connectors": out}
    finally:
        if conn is not None:
            conn.close()
