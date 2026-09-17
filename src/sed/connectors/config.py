"""Connector configuration (`connectors.yaml`, layered like every config file) validated with Pydantic."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sed.errors import ValidationFailed

CONFIG_FILE = "connectors.yaml"
PREFIX_PATTERN = r"^[a-z][a-z0-9_]{1,40}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Defaults(_Strict):
    page_size: int = Field(500, ge=1, le=10_000)
    max_rows: int = Field(50_000, ge=1)
    max_pages: int = Field(500, ge=1)
    pause_seconds: float = Field(0.5, ge=0, le=60)
    overlap_minutes: int = Field(60, ge=0, le=7 * 24 * 60)
    timeout_seconds: float = Field(60, gt=0, le=600)


class ConnectorBase(_Strict):
    enabled: bool = False
    base_url: str = Field(pattern=r"^https://[^\s/?#]+(/[^\s?#]*)?$")
    auth: Literal["basic", "bearer"] = "bearer"
    user: str | None = Field(None, max_length=120)  # basic auth user name (not a secret)
    credential: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,59}$")  # name of the secret, never the value

    @model_validator(mode="after")
    def _basic_needs_user(self) -> ConnectorBase:
        if self.auth == "basic" and not self.user:
            raise ValueError("auth: basic needs 'user'")
        return self


class ServiceNowSource(_Strict):
    key: str = Field(pattern=PREFIX_PATTERN)
    table: str = Field(pattern=r"^[a-z][a-z0-9_]{1,80}$")
    file_prefix: str = Field(pattern=PREFIX_PATTERN)
    fields: list[str] = Field(min_length=1)
    datetime_fields: list[str] = Field(default_factory=list)
    filter: str = Field("", max_length=2000)  # extra encoded query, ANDed with the watermark
    updated_field: str = "sys_updated_on"
    source_tz: str = "Europe/Paris"


class ServiceNowConfig(ConnectorBase):
    sources: list[ServiceNowSource] = Field(default_factory=list)


class JiraSource(_Strict):
    key: str = Field(pattern=PREFIX_PATTERN)
    jql: str = Field(min_length=1, max_length=2000)
    file_prefix: str = Field("jira_pull", pattern=PREFIX_PATTERN)
    story_points_field: str | None = Field(None, pattern=r"^customfield_\d+$")
    sprint_field: str | None = Field(None, pattern=r"^customfield_\d+$")
    source_tz: str = "Europe/Paris"


class JiraConfig(ConnectorBase):
    api_path: str = "/rest/api/2"
    sources: list[JiraSource] = Field(default_factory=list)


class SharePointSource(_Strict):
    key: str = Field(pattern=PREFIX_PATTERN)
    site_id: str = Field(min_length=1, max_length=300)
    list_id: str = Field(min_length=1, max_length=120)
    file_prefix: str = Field(pattern=PREFIX_PATTERN)
    # CSV header (what the mapping expects) -> list field internal name
    columns: dict[str, str] = Field(min_length=1)


class SharePointConfig(ConnectorBase):
    sources: list[SharePointSource] = Field(default_factory=list)


class ConfluenceSource(_Strict):
    key: str = Field(pattern=PREFIX_PATTERN)
    space: str = Field(pattern=r"^[A-Za-z0-9~_]{1,64}$")


class ConfluenceConfig(ConnectorBase):
    api_path: str = "/rest/api"
    sources: list[ConfluenceSource] = Field(default_factory=list)


class SapSource(_Strict):
    """One SAP Gateway OData entity set written as the export a SAP mapping reads (ChaRM changes, transport imports or
    IDocs). Service and property names depend on the landscape: confirm them with SAP Basis."""

    key: str = Field(pattern=PREFIX_PATTERN)
    service_path: str = Field(pattern=r"^/[^\s?#]+$")  # e.g. /sap/opu/odata/sap/<SERVICE>/<EntitySet>
    file_prefix: Literal["sap_charm_changes", "sap_transport_imports", "sap_idocs"]
    columns: dict[str, str] = Field(min_length=1)  # CSV header (a mapping field name) -> OData property
    datetime_columns: list[str] = Field(default_factory=list)
    updated_property: str | None = Field(None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")  # None = whole set each pull
    constants: dict[str, str] = Field(default_factory=dict)  # fixed columns, e.g. system_id: HP1
    source_tz: str = "Europe/Paris"
    # A production system's own gateway and account (IDocs are read per system); default: the connector's.
    base_url: str | None = Field(None, pattern=r"^https://[^\s/?#]+(/[^\s?#]*)?$")
    user: str | None = Field(None, max_length=120)
    credential: str | None = Field(None, pattern=r"^[a-z][a-z0-9._-]{1,59}$")


class SapConfig(ConnectorBase):
    odata_version: Literal[2, 4] = 2
    sources: list[SapSource] = Field(default_factory=list)


class ConnectorsConfig(_Strict):
    defaults: Defaults = Defaults()
    servicenow: ServiceNowConfig | None = None
    jira: JiraConfig | None = None
    sharepoint: SharePointConfig | None = None
    confluence: ConfluenceConfig | None = None
    sap: SapConfig | None = None


CONNECTORS = ("servicenow", "jira", "sharepoint", "confluence", "sap")


def load_config(paths: Any) -> ConnectorsConfig:
    from sed.settings import load_layered

    data = load_layered(CONFIG_FILE, paths) or {}
    try:
        return ConnectorsConfig.model_validate(data)
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid {CONFIG_FILE}", errors) from exc
