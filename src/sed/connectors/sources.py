"""Fetchers: one function per connector turning API pages into export-shaped rows.

Each returns a `Fetched` (headers, rows, the newest update time in UTC, whether the row cap stopped it). They only read
through `Client.get`; the orchestration in `pull.py` writes files and watermarks.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from sed.connectors.config import ConfluenceSource, JiraSource, SapSource, ServiceNowSource, SharePointSource
from sed.connectors.http import Client
from sed.errors import PreconditionFailed

LOCAL_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class Fetched:
    headers: list[str]
    rows: list[list[Any]]
    newest: datetime | None = None
    capped: bool = False
    pages: list[dict[str, Any]] = field(default_factory=list)  # Confluence pages (not tabular)


def _local(value: datetime, tz: str) -> str:
    return value.astimezone(ZoneInfo(tz)).strftime(LOCAL_FORMAT)


def _newest(current: datetime | None, value: datetime | None) -> datetime | None:
    if value is None:
        return current
    return value if current is None or value > current else current


# -- ServiceNow Table API --------------------------------------------------------------------------------------------


def _sn_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, LOCAL_FORMAT).replace(tzinfo=UTC)


def fetch_servicenow(
    client: Client, source: ServiceNowSource, since: datetime | None, page_size: int, max_rows: int
) -> Fetched:
    """GET /api/now/table/<table>, oldest update first, `sysparm_display_value=all`: display values for choices and
    references (as in a list export), raw UTC values for the datetime fields converted to the source time zone."""
    query = [f"{source.updated_field}>={since.astimezone(UTC).strftime(LOCAL_FORMAT)}"] if since else []
    if source.filter:
        query.append(source.filter)
    query.append(f"ORDERBY{source.updated_field}")
    fields = list(dict.fromkeys([*source.fields, source.updated_field]))
    out = Fetched(headers=list(source.fields), rows=[])
    offset = 0
    while True:
        body = client.get(
            f"/api/now/table/{source.table}",
            {
                "sysparm_query": "^".join(query),
                "sysparm_fields": ",".join(fields),
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
                "sysparm_limit": page_size,
                "sysparm_offset": offset,
            },
        )
        result = (body or {}).get("result")
        if not isinstance(result, list):
            raise PreconditionFailed(f"ServiceNow table {source.table}: unexpected response (no result list)")
        for record in result:
            row = []
            for name in source.fields:
                cell = record.get(name)
                raw = cell.get("value") if isinstance(cell, dict) else cell
                shown = cell.get("display_value") if isinstance(cell, dict) else cell
                if name in source.datetime_fields:
                    moment = _sn_utc(raw)
                    row.append(_local(moment, source.source_tz) if moment else "")
                else:
                    row.append("" if shown is None else str(shown))
            updated = record.get(source.updated_field)
            out.newest = _newest(out.newest, _sn_utc(updated.get("value") if isinstance(updated, dict) else updated))
            out.rows.append(row)
            if len(out.rows) >= max_rows:
                out.capped = True
                return out
        if len(result) < page_size:
            return out
        offset += page_size


# -- Jira REST search --------------------------------------------------------------------------------------------------

JIRA_HEADERS = [
    "Issue key", "Project key", "Project name", "Summary", "Issue Type", "Status", "Status Category", "Priority",
    "Assignee", "Created", "Updated", "Resolved", "Parent", "Custom field (Story Points)",
]  # fmt: skip
JIRA_LISTS = ["Sprint", "Labels", "Component/s", "Fix Version/s"]


def _jira_time(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _name(value: Any, key: str = "name") -> str:
    return str(value.get(key) or "") if isinstance(value, dict) else ("" if value is None else str(value))


def fetch_jira(
    client: Client, api_path: str, source: JiraSource, since: datetime | None, page_size: int, max_rows: int
) -> Fetched:
    """GET <api>/search with the source JQL, oldest update first; multi-valued fields become repeated columns as in
    a Jira "all fields" CSV export."""
    jql = f"({source.jql})"
    if since:
        jql += f' AND updated >= "{since.astimezone(ZoneInfo(source.source_tz)).strftime("%Y/%m/%d %H:%M")}"'
    jql += " ORDER BY updated ASC"
    fields = [
        "project", "summary", "issuetype", "status", "priority", "assignee", "created", "updated", "resolutiondate",
        "parent", "labels", "components", "fixVersions",
    ]  # fmt: skip
    fields += [f for f in (source.story_points_field, source.sprint_field) if f]
    records: list[tuple[list[Any], dict[str, list[str]]]] = []
    newest: datetime | None = None
    start = 0
    capped = False
    while True:
        body = client.get(
            f"{api_path.rstrip('/')}/search",
            {"jql": jql, "startAt": start, "maxResults": page_size, "fields": ",".join(fields)},
        )
        issues = (body or {}).get("issues")
        if not isinstance(issues, list):
            raise PreconditionFailed("Jira search: unexpected response (no issues list)")
        for issue in issues:
            f = issue.get("fields") or {}
            created, updated, resolved = (_jira_time(f.get(k)) for k in ("created", "updated", "resolutiondate"))
            newest = _newest(newest, updated)
            status = f.get("status") or {}
            points = f.get(source.story_points_field) if source.story_points_field else None
            row = [
                issue.get("key", ""),
                _name(f.get("project"), "key"),
                _name(f.get("project")),
                f.get("summary") or "",
                _name(f.get("issuetype")),
                _name(status),
                _name(status.get("statusCategory") if isinstance(status, dict) else None),
                _name(f.get("priority")),
                _name(f.get("assignee"), "displayName"),
                _local(created, source.source_tz) if created else "",
                _local(updated, source.source_tz) if updated else "",
                _local(resolved, source.source_tz) if resolved else "",
                _name(f.get("parent"), "key"),
                "" if points is None else str(points),
            ]
            sprints = f.get(source.sprint_field) if source.sprint_field else None
            lists = {
                "Sprint": [_name(s) for s in sprints or []] if isinstance(sprints, list) else [],
                "Labels": [str(x) for x in f.get("labels") or []],
                "Component/s": [_name(c) for c in f.get("components") or []],
                "Fix Version/s": [_name(v) for v in f.get("fixVersions") or []],
            }
            records.append((row, lists))
            if len(records) >= max_rows:
                capped = True
                break
        if capped or len(issues) < page_size:
            break
        start += len(issues)
    widths = {name: max([1, *(len(lists[name]) for _, lists in records)]) for name in JIRA_LISTS}
    headers = list(JIRA_HEADERS) + [name for name in JIRA_LISTS for _ in range(widths[name])]
    rows = []
    for row, lists in records:
        extra = []
        for name in JIRA_LISTS:
            values = lists[name] + [""] * (widths[name] - len(lists[name]))
            extra += values
        rows.append(row + extra)
    return Fetched(headers=headers, rows=rows, newest=newest, capped=capped)


# -- SharePoint lists through Microsoft Graph --------------------------------------------------------------------------


def fetch_sharepoint(client: Client, source: SharePointSource, page_size: int, max_rows: int) -> Fetched:
    """GET /sites/<site>/lists/<list>/items?expand=fields: the whole list (registers are full snapshots)."""
    select = ",".join(dict.fromkeys(source.columns.values()))
    params: dict[str, Any] = {"expand": f"fields($select={select})", "$top": page_size}
    path = f"/sites/{source.site_id}/lists/{source.list_id}/items"
    out = Fetched(headers=list(source.columns), rows=[])
    while True:
        body = client.get(path, params) or {}
        values = body.get("value")
        if not isinstance(values, list):
            raise PreconditionFailed(f"SharePoint list {source.key}: unexpected response (no value list)")
        for item in values:
            fields = item.get("fields") or {}
            out.rows.append(["" if fields.get(f) is None else str(fields.get(f)) for f in source.columns.values()])
            if len(out.rows) >= max_rows:
                out.capped = True
                return out
        next_link = body.get("@odata.nextLink")
        if not next_link:
            return out
        parts = urlsplit(next_link)
        base = urlsplit(client.base_url)
        if parts.netloc != base.netloc:
            raise PreconditionFailed("SharePoint returned a next page on another host; stopped")
        path = parts.path.removeprefix(base.path.rstrip("/"))
        params = {k: v[0] for k, v in parse_qs(parts.query).items()}


# -- Confluence content search ---------------------------------------------------------------------------------------


def fetch_confluence(
    client: Client, api_path: str, source: ConfluenceSource, since: datetime | None, page_size: int, max_rows: int
) -> Fetched:
    """GET <api>/content/search with CQL on the space (and last modified since the watermark), bodies in storage
    format; pages are written as a Confluence HTML export folder."""
    cql = f'space = "{source.space}" and type = page'
    if since:
        cql += f' and lastmodified >= "{since.astimezone(UTC).strftime("%Y/%m/%d %H:%M")}"'
    cql += " order by lastmodified asc"
    out = Fetched(headers=[], rows=[])
    start = 0
    while True:
        body = client.get(
            f"{api_path.rstrip('/')}/content/search",
            {"cql": cql, "start": start, "limit": page_size, "expand": "body.storage,version,metadata.labels,history"},
        )
        results = (body or {}).get("results")
        if not isinstance(results, list):
            raise PreconditionFailed("Confluence search: unexpected response (no results list)")
        for page in results:
            version = page.get("version") or {}
            modified = _jira_time(version.get("when"))
            out.newest = _newest(out.newest, modified)
            out.pages.append(
                {
                    "id": str(page.get("id", "")),
                    "title": str(page.get("title", "")),
                    "author": _name((page.get("history") or {}).get("createdBy"), "displayName"),
                    "modified": modified,
                    "labels": [_name(x) for x in ((page.get("metadata") or {}).get("labels") or {}).get("results", [])],
                    "body": ((page.get("body") or {}).get("storage") or {}).get("value", ""),
                }
            )
            if len(out.pages) >= max_rows:
                out.capped = True
                return out
        if len(results) < page_size:
            return out
        start += len(results)


def confluence_page_html(space: str, page: dict[str, Any]) -> str:
    """One page in the markup of a Confluence space HTML export (title, metadata, labels, main content)."""
    modified = page["modified"].strftime("%b %d, %Y") if page.get("modified") else ""
    labels = "".join(f'<a class="label" href="#">{html.escape(label)}</a>' for label in page["labels"])
    author = html.escape(page.get("author") or "")
    return (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
        f"<title>{html.escape(space)} : {html.escape(page['title'])}</title></head><body>\n"
        f'<div class="page-metadata">Created by {author}, last modified on {modified}</div>\n'
        f'<div class="labels">{labels}</div>\n'
        f'<div id="main-content" class="wiki-content">{page["body"]}</div>\n'
        "</body></html>\n"
    )


# -- SAP Gateway OData ------------------------------------------------------------------------------------------------

_ODATA_V2_DATE = re.compile(r"^/Date\((-?\d+)([+-]\d{4})?\)/$")


def odata_time(value: Any) -> datetime | None:
    """Edm.DateTime(Offset) as OData v2 JSON (`/Date(ms)/`, UTC) or ISO 8601 (v4; no offset means UTC)."""
    if value in (None, ""):
        return None
    text = str(value)
    match = _ODATA_V2_DATE.match(text)
    if match:
        return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=UTC)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def fetch_sap(
    client: Client, version: int, source: SapSource, since: datetime | None, page_size: int, max_rows: int
) -> Fetched:
    """GET an OData entity set with $select/$top/$skip, oldest change first when `updated_property` is set (delta from
    the watermark); dates are written in the mapping's time zone and `constants` are added as fixed columns."""
    params: dict[str, Any] = {"$select": ",".join(dict.fromkeys(source.columns.values()))}
    if version == 2:
        params["$format"] = "json"
    prop = source.updated_property
    if prop:
        params["$orderby"] = f"{prop} asc"
        if since:
            moment = since.astimezone(UTC)
            params["$filter"] = (
                f"{prop} ge datetime'{moment:%Y-%m-%dT%H:%M:%S}'"
                if version == 2
                else f"{prop} ge {moment:%Y-%m-%dT%H:%M:%SZ}"
            )
    out = Fetched(headers=[*source.columns, *source.constants], rows=[])
    skip = 0
    while True:
        body = client.get(source.service_path, {**params, "$top": page_size, "$skip": skip}) or {}
        if version == 2:
            data = body.get("d")
            results = data.get("results") if isinstance(data, dict) else data
        else:
            results = body.get("value")
        if not isinstance(results, list):
            raise PreconditionFailed(f"SAP OData {source.key}: unexpected response (no result list)")
        for record in results:
            row = []
            for header, name in source.columns.items():
                value = record.get(name)
                if header in source.datetime_columns:
                    moment = odata_time(value)
                    row.append(_local(moment, source.source_tz) if moment else "")
                else:
                    row.append("" if value is None else str(value))
            row += list(source.constants.values())
            if prop:
                out.newest = _newest(out.newest, odata_time(record.get(prop)))
            out.rows.append(row)
            if len(out.rows) >= max_rows:
                out.capped = True
                return out
        if len(results) < page_size:
            return out
        skip += len(results)
