"""SAP scope (config/sap/scope.yaml): which ops tickets are SAP L3 tickets, their SAP area and landscape.

Computed on read, nothing is stored. A ticket is an SAP ticket when its assignment group is a configured SAP group
(the group gives its area), or its ServiceNow category is a configured SAP category, or a kept custom field marks it;
the last two have area "unassigned" unless an SAP group also holds the ticket. The landscape comes from the ticket's
application ("unknown" when it is not listed). Real names live only in DATA_DIR\\config\\sap\\scope.yaml.

The scope predicate cannot use an index (it ORs groups, categories and JSON fields), so a request that runs many
metrics first calls `Scope.resolve(conn)`: one scan lists the SAP tickets, and every later predicate looks them up by
ticket id. Both forms select the same tickets.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from sed.errors import ValidationFailed
from sed.paths import Paths
from sed.settings import StrictModel, load_layered

SCOPE_FILE = "sap/scope.yaml"
UNASSIGNED = "unassigned"
UNKNOWN = "unknown"
CODE_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"


class AreaDef(StrictModel):
    code: str = Field(pattern=CODE_PATTERN)
    label: str = Field(min_length=1)


class GroupDef(StrictModel):
    name: str = Field(min_length=1)
    area: str


class CustomFieldDef(StrictModel):
    field: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_ ]{0,63}$")
    values: list[str] = Field(default_factory=list)


class LandscapeDef(StrictModel):
    code: str = Field(pattern=CODE_PATTERN)
    label: str = Field(min_length=1)
    apps: list[str] = Field(default_factory=list)


class ScopeConfig(StrictModel):
    version: Literal[1] = 1
    areas: list[AreaDef] = Field(default_factory=list)
    groups: list[GroupDef] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    custom_fields: list[CustomFieldDef] = Field(default_factory=list)
    landscapes: list[LandscapeDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> ScopeConfig:
        problems: list[str] = []
        for what, codes in (("area", [a.code for a in self.areas]), ("landscape", [x.code for x in self.landscapes])):
            problems += [f"duplicate {what} '{c}'" for c in sorted({c for c in codes if codes.count(c) > 1})]
            problems += [f"{what} code '{c}' is reserved" for c in codes if c in (UNASSIGNED, UNKNOWN)]
        areas = {a.code for a in self.areas}
        problems += [f"group '{g.name}' has unknown area '{g.area}'" for g in self.groups if g.area not in areas]
        names = [g.name for g in self.groups]
        problems += [f"group '{n}' is listed twice" for n in sorted({n for n in names if names.count(n) > 1})]
        apps = [a for x in self.landscapes for a in x.apps]
        problems += [f"app '{a}' is in two landscapes" for a in sorted({a for a in apps if apps.count(a) > 1})]
        if problems:
            raise ValueError("; ".join(problems))
        return self


@dataclass(frozen=True)
class Scope:
    config: ScopeConfig
    # (ticket_id, assignment_group, app_id) of every ticket in scope, once resolved (see `resolve`).
    tickets: tuple[tuple[str, str | None, str | None], ...] | None = None

    @property
    def configured(self) -> bool:
        c = self.config
        return bool(c.groups or c.categories or c.custom_fields)

    @property
    def area_labels(self) -> dict[str, str]:
        return {**{a.code: a.label for a in self.config.areas}, UNASSIGNED: "Unassigned"}

    @property
    def landscape_labels(self) -> dict[str, str]:
        return {**{x.code: x.label for x in self.config.landscapes}, UNKNOWN: "Unknown landscape"}

    @property
    def group_area(self) -> dict[str, str]:
        return {g.name: g.area for g in self.config.groups}

    @property
    def app_landscape(self) -> dict[str, str]:
        return {app: x.code for x in self.config.landscapes for app in x.apps}

    def area_of(self, group: str | None) -> str:
        return self.group_area.get(group or "", UNASSIGNED)

    def resolve(self, conn: sqlite3.Connection) -> Scope:
        """This scope with its tickets listed (one scan), so `ticket_sql` selects them by ticket id."""
        sql, params = self._predicate(None, None)
        rows = conn.execute(
            f"SELECT t.ticket_id, t.assignment_group, t.app_id FROM ticket t WHERE {sql.format(t='t')}", params
        ).fetchall()
        return replace(self, tickets=tuple((r[0], r[1], r[2]) for r in rows))

    def check_area(self, area: str | None) -> None:
        if area is not None and area not in self.area_labels:
            raise ValidationFailed(f"Unknown SAP area '{area}'", {"areas": sorted(self.area_labels)})

    def check_landscape(self, landscape: str | None) -> None:
        if landscape is not None and landscape not in self.landscape_labels:
            raise ValidationFailed(
                f"Unknown SAP landscape '{landscape}'", {"landscapes": sorted(self.landscape_labels)}
            )

    def ticket_sql(self, *, area: str | None = None, landscape: str | None = None) -> tuple[str, tuple[Any, ...]]:
        """Predicate on a ticket alias written `{t}` (metrics.Filters.scope_sql): SAP tickets, optionally of one area
        and one landscape. On a resolved scope it matches the listed ticket ids."""
        self.check_area(area)
        self.check_landscape(landscape)
        if self.tickets is None:
            return self._predicate(area, landscape)
        group_area, app_landscape = self.group_area, self.app_landscape
        ids = [
            ticket_id
            for ticket_id, group, app_id in self.tickets
            if (area is None or group_area.get(group or "", UNASSIGNED) == area)
            and (landscape is None or app_landscape.get(app_id or "", UNKNOWN) == landscape)
        ]
        return "{t}.ticket_id IN (SELECT value FROM json_each(?))", (json.dumps(ids),)

    def _predicate(self, area: str | None, landscape: str | None) -> tuple[str, tuple[Any, ...]]:
        c = self.config
        clauses: list[str] = []
        params: list[Any] = []
        if c.groups:
            clauses.append(f"{{t}}.assignment_group IN ({_marks(c.groups)})")
            params += [g.name for g in c.groups]
        if c.categories:
            clauses.append(f"{{t}}.category IN ({_marks(c.categories)})")
            params += list(c.categories)
        for cf in c.custom_fields:
            path = '$."' + cf.field + '"'
            if cf.values:
                clauses.append(f"json_extract({{t}}.raw_keep_json, ?) IN ({_marks(cf.values)})")
                params += [path, *cf.values]
            else:
                clauses.append("COALESCE(json_extract({t}.raw_keep_json, ?), '') <> ''")
                params.append(path)
        sql = f"({' OR '.join(clauses) or '0'})"
        if area is not None:
            names = (
                list(self.group_area) if area == UNASSIGNED else [n for n, a in self.group_area.items() if a == area]
            )
            if area == UNASSIGNED:
                sql += (
                    f" AND ({{t}}.assignment_group IS NULL OR {{t}}.assignment_group NOT IN ({_marks(names)}))"
                    if names
                    else ""
                )
            else:
                sql += f" AND {{t}}.assignment_group IN ({_marks(names)})" if names else " AND 0"
            params += names
        if landscape is not None:
            listed = [a for x in c.landscapes for a in x.apps]
            if landscape == UNKNOWN:
                sql += f" AND ({{t}}.app_id IS NULL OR {{t}}.app_id NOT IN ({_marks(listed)}))" if listed else ""
                params += listed
            else:
                apps = next(x.apps for x in c.landscapes if x.code == landscape)
                sql += f" AND {{t}}.app_id IN ({_marks(apps)})" if apps else " AND 0"
                params += apps
        return sql, tuple(params)


def _marks(values: list[Any]) -> str:
    return ", ".join("?" for _ in values)


def load_scope(paths: Paths | None) -> Scope:
    data = load_layered(SCOPE_FILE, paths)
    try:
        return Scope(ScopeConfig.model_validate(data))
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid {SCOPE_FILE}", errors) from exc
