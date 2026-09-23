"""Core routes mounted at /api. Paths, parameters and response models are frozen for M2 (contracts/openapi.json).

GET routes only read, through the per-request query_only connection from `deps.read_conn`. Route functions carry
comments rather than docstrings, because FastAPI publishes docstrings into the generated OpenAPI contract.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from sed import __version__, db
from sed.api.deps import read_conn, resolve_as_of, write_conn
from sed.api.findings import published_findings
from sed.api.models import (
    AliasIn,
    AliasOut,
    AliasTarget,
    AliasTargetsOut,
    BrandingOut,
    DefinitionOut,
    FilterOption,
    FindingsOut,
    FreshnessRow,
    HealthOut,
    ImportRow,
    ImportsOut,
    LayoutDeletedOut,
    LayoutOut,
    LayoutRefIn,
    LayoutSaveIn,
    LayoutsOut,
    MetaOut,
    ModuleOut,
    ModuleRef,
    ModulesOut,
    NavItemOut,
    NavOut,
    PeriodsOut,
    RunRow,
    RunsOut,
    UnmappedList,
    UnmappedRow,
)
from sed.calendar import month_label, quarter_label, shift_label, week_label
from sed.errors import ValidationFailed
from sed.ingest.aliases import alias_targets as find_alias_targets
from sed.ingest.aliases import assign_alias
from sed.ingest.freshness import data_as_of, import_freshness

PERIODS_BACK = {"weeks": 12, "months": 18, "quarters": 6}
router = APIRouter(tags=["core"])


# -- helpers -----------------------------------------------------------------------------------------------------


def mounted_modules(request: Request) -> tuple[Any, ...]:
    """The module manifests this app serves (create_app's `modules`, default: the profile's enabled modules)."""
    return tuple(getattr(request.app.state, "module_manifests", ()))


def recent_periods(as_of: date, fiscal_year_start: int = 1) -> PeriodsOut:
    """Selectable report periods, newest first, starting with the period that contains `as_of`."""
    starts = {
        "weeks": week_label(as_of),
        "months": month_label(as_of),
        "quarters": quarter_label(as_of, fiscal_year_start),
    }
    return PeriodsOut(**{key: [shift_label(starts[key], -i) for i in range(n)] for key, n in PERIODS_BACK.items()})


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def entity_options(conn: sqlite3.Connection, entity: Any) -> list[FilterOption]:
    """Filter options for one registry EntityRef (live rows only; empty when its table does not exist)."""
    columns = _table_columns(conn, entity.table)
    if entity.id_col not in columns or entity.name_col not in columns:
        return []
    where = " WHERE is_deleted = 0" if "is_deleted" in columns else ""
    rows = conn.execute(
        f'SELECT "{entity.id_col}" AS id, "{entity.name_col}" AS name FROM "{entity.table}"{where} '
        f'ORDER BY "{entity.name_col}" COLLATE NOCASE, "{entity.id_col}"'
    ).fetchall()
    return [
        FilterOption(value=str(r["id"]), label=str(r["name"] if r["name"] is not None else r["id"]))
        for r in rows
        if r["id"] is not None
    ]


def module_definitions(mods: tuple[Any, ...]) -> dict[str, DefinitionOut]:
    """Metric and fact definitions of the served modules (same rules as sed.modules.metric_definitions)."""
    from sed import modules as registry

    out: dict[str, DefinitionOut] = {}
    for module in mods:
        if not module.metric_definitions:
            continue
        for key, value in dict(registry.load_ref(module.metric_definitions)).items():
            if key in out:
                raise ValidationFailed(f"Metric definition '{key}' declared twice (module {module.key})")
            out[key] = DefinitionOut(unit=str(value[0]), text=str(value[1]))
    return out


def _json_dict(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _int_counts(text: str | None) -> dict[str, int]:
    return {
        str(k): int(v) for k, v in _json_dict(text).items() if isinstance(v, int | float) and not isinstance(v, bool)
    }


# -- routes ------------------------------------------------------------------------------------------------------


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(ok=True, version=__version__)


@router.get("/branding", response_model=BrandingOut)
def branding() -> BrandingOut:
    # The strip title and whether a logo and a watermark are set on this machine (never the images themselves).
    from sed.branding import find_asset, title

    return BrandingOut(
        title=title(),
        logo=find_asset("logo") is not None,
        watermark=find_asset("watermark") is not None,
        watermark_dark=find_asset("watermark-dark") is not None,
    )


@router.get("/branding/{asset}", include_in_schema=False)
def branding_asset(asset: Literal["logo", "watermark", "watermark-dark"]) -> FileResponse:
    # A branding image kept on this machine (`sed branding logo|watermark <file>`); 404 when it is not set, and the
    # dashboard then shows the SED wordmark and no watermark.
    from sed.branding import MEDIA_TYPES, find_asset

    found = find_asset(asset)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No {asset} is set on this machine (sed branding {asset} <file>)")
    return FileResponse(found, media_type=MEDIA_TYPES[found.suffix], headers={"Cache-Control": "no-cache"})


@router.get("/meta", response_model=MetaOut)
def meta(request: Request, conn: sqlite3.Connection = Depends(read_conn)) -> MetaOut:
    # Profile facts, filter vocabularies, periods and definitions for the dashboard shell.
    from sed.settings import load_settings

    paths = request.app.state.paths
    settings = load_settings(paths)
    stored = db.all_meta(conn)
    as_of = data_as_of(conn, settings)
    mods = mounted_modules(request)
    entities: dict[str, list[FilterOption]] = {}
    for module in mods:
        for entity in module.entities:
            entities.setdefault(entity.key, entity_options(conn, entity))
    return MetaOut(
        data_class=stored.get("data_class") or paths.data_class,
        profile=paths.profile,
        pii_mode=stored.get("pii_mode") or settings.pii_mode,
        schema_version=db.user_version(conn),
        sed_version=__version__,
        reporting_tz=settings.reporting_tz,
        base_currency=settings.base_currency,
        as_of_default=as_of.isoformat() if as_of else None,
        periods=recent_periods(as_of or date.today(), settings.fiscal_year_start),
        entities=entities,
        freshness=[FreshnessRow(**row) for row in import_freshness(conn)],
        definitions=module_definitions(mods),
        modules=[ModuleRef(key=m.key, title=m.title) for m in mods],
    )


@router.get("/nav", response_model=NavOut)
def nav(request: Request) -> NavOut:
    # Core navigation plus the nav items of the served modules, ordered like sed.modules.nav.
    from sed import modules as registry

    items = [("core", item) for item in registry.CORE_NAV]
    items += [(m.key, item) for m in mounted_modules(request) for item in m.nav]
    items.sort(key=lambda kv: (kv[1].order, kv[1].id))
    return NavOut(
        items=[
            NavItemOut(id=item.id, module=key, label=item.label, path=item.path, order=item.order, icon=item.icon)
            for key, item in items
        ]
    )


@router.get("/modules", response_model=ModulesOut)
def modules_list(request: Request) -> ModulesOut:
    # Installed modules (plus any served module that is not installed), flagged enabled when this app serves them.
    from sed import modules as registry

    served = mounted_modules(request)
    served_keys = {m.key for m in served}
    known = list(registry.installed())
    known += [m for m in served if m.key not in {k.key for k in known}]
    return ModulesOut(
        modules=[
            ModuleOut(
                key=m.key,
                title=m.title,
                description=m.description,
                version=m.version,
                enabled=m.key in served_keys,
                reports=[r.key for r in m.reports],
                skills=[s.name for s in m.skills],
            )
            for m in known
        ]
    )


@router.get("/findings", response_model=FindingsOut)
def findings(
    request: Request,
    kind: str | None = None,
    origin: Literal["rule", "ai"] | None = None,
    status: Literal["published", "all"] = "published",
    subject_type: str | None = None,
    subject_id: str | None = None,
    as_of: date | None = None,
    limit: int = Query(100, ge=1, le=1000),
    conn: sqlite3.Connection = Depends(read_conn),
) -> FindingsOut:
    # Thin wrapper: published semantics live only in sed.api.findings (never refreshes rule findings).
    items = published_findings(
        conn,
        resolve_as_of(conn, request, as_of),
        kind=kind,
        origin=origin,
        status=status,
        subject_type=subject_type,
        subject_id=subject_id,
        limit=limit,
    )
    return FindingsOut(items=items)


@router.get("/imports", response_model=ImportsOut)
def imports(limit: int = Query(50, ge=1, le=1000), conn: sqlite3.Connection = Depends(read_conn)) -> ImportsOut:
    rows = conn.execute(
        "SELECT batch_id, file_name, mapping_name, load_mode, status, as_of, rows_read, rows_inserted, rows_updated, "
        "rows_unchanged, rows_rejected, rows_soft_deleted, dq_json, imported_at "
        "FROM import_batch ORDER BY batch_id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    items = []
    for r in rows:
        values = dict(r)
        dq = _json_dict(values.pop("dq_json"))
        severity = dq.get("severity")
        items.append(ImportRow(**values, dq=dq, dq_severity=severity if isinstance(severity, str) else None))
    return ImportsOut(items=items)


@router.get("/dq/unmapped", response_model=UnmappedList)
def dq_unmapped(
    kind: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
    conn: sqlite3.Connection = Depends(read_conn),
) -> UnmappedList:
    # Unresolved raw values, most frequent first, with the stored rapidfuzz suggestion.
    from sed import modules as registry

    sql = (
        "SELECT kind, raw_value, occurrences, suggestion, score, first_batch_id, last_batch_id "
        "FROM unmapped_value WHERE resolved = 0"
    )
    params: list[Any] = []
    if kind is not None:
        if kind not in registry.alias_kinds():
            raise ValidationFailed(f"Unknown alias kind '{kind}'", {"available": list(registry.alias_kinds())})
        sql += " AND kind = ?"
        params.append(kind)
    rows = conn.execute(sql + " ORDER BY occurrences DESC, kind, raw_value LIMIT ?", (*params, limit)).fetchall()
    return UnmappedList(items=[UnmappedRow(**dict(r)) for r in rows])


@router.get("/alias-targets", response_model=AliasTargetsOut)
def alias_targets(
    kind: str,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    conn: sqlite3.Connection = Depends(read_conn),
) -> AliasTargetsOut:
    return AliasTargetsOut(items=[AliasTarget(**hit) for hit in find_alias_targets(conn, kind, q, limit)])


@router.post("/aliases", response_model=AliasOut)
def create_alias(body: AliasIn, request: Request, conn: sqlite3.Connection = Depends(write_conn)) -> AliasOut:
    # Manual alias plus decision log, then re-link existing rows. Writes go through db.write_tx inside
    # assign_alias, so a held write lock surfaces as Busy -> 409 with Retry-After.
    from sed.bootstrap import reviewer_name

    result = assign_alias(
        request.app.state.paths, body.kind, body.raw_value, body.target, reviewer=reviewer_name(), reresolve=True
    )
    return AliasOut(
        kind=result["kind"],
        raw_value=result["raw"],
        target_id=result["target_id"],
        reresolved={str(k): int(v) for k, v in (result.get("reresolved") or {}).items()},
    )


@router.get("/runs", response_model=RunsOut)
def runs(
    skill: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    conn: sqlite3.Connection = Depends(read_conn),
) -> RunsOut:
    where: list[str] = []
    params: list[Any] = []
    for column, value in (("skill", skill), ("status", status)):
        if value is not None:
            where.append(f"{column} = ?")
            params.append(value)
    rows = conn.execute(
        "SELECT run_id, skill, status, invoked_via, started_at, finished_at, counts_json, sample_accuracy, "
        "sample_ci_low, sample_ci_high, sample_n, reviewed_by, reviewed_at FROM ai_run"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY run_seq DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    items = []
    for r in rows:
        values = dict(r)
        counts = _int_counts(values.pop("counts_json"))
        items.append(RunRow(**values, counts=counts))
    return RunsOut(items=items)


@router.get("/layouts", response_model=LayoutsOut)
def layouts(
    table: str = Query(min_length=1, max_length=80, description="table key, e.g. ops.tickets"),
    conn: sqlite3.Connection = Depends(read_conn),
) -> LayoutsOut:
    # Named column layouts for one table, the default first. Column keys only; no ticket data is involved.
    from sed.layouts import list_layouts

    return LayoutsOut(items=[LayoutOut(**row) for row in list_layouts(conn, table)])


@router.post("/layouts", response_model=LayoutOut)
def save_layout_route(body: LayoutSaveIn, conn: sqlite3.Connection = Depends(write_conn)) -> LayoutOut:
    # Create a layout, or replace the columns of one already saved under that name for that table.
    from sed import db
    from sed.layouts import save_layout

    with db.write_tx(conn):
        row = save_layout(conn, body.table_key, body.name, body.columns, make_default=body.make_default)
    return LayoutOut(**row)


@router.post("/layouts/default", response_model=LayoutOut)
def make_layout_default(body: LayoutRefIn, conn: sqlite3.Connection = Depends(write_conn)) -> LayoutOut:
    # The layout its table opens with. At most one per table, enforced by a partial unique index.
    from sed import db
    from sed.layouts import set_default

    with db.write_tx(conn):
        row = set_default(conn, body.layout_id)
    return LayoutOut(**row)


@router.post("/layouts/delete", response_model=LayoutDeletedOut)
def remove_layout(body: LayoutRefIn, conn: sqlite3.Connection = Depends(write_conn)) -> LayoutDeletedOut:
    # POST rather than DELETE: every write carries the per-launch token, and the dashboard sends it on POSTs.
    from sed import db
    from sed.layouts import delete_layout

    with db.write_tx(conn):
        row = delete_layout(conn, body.layout_id)
    return LayoutDeletedOut(**row)
