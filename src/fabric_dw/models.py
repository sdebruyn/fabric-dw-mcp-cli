"""Pydantic v2 models for Microsoft Fabric Data Warehouse domain objects."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _FabricBase(BaseModel):
    """Shared config for all Fabric domain models."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class Workspace(_FabricBase):
    """A Microsoft Fabric workspace."""

    id: UUID
    name: str = Field(alias="displayName")
    description: str | None = None
    capacity_id: UUID | None = Field(default=None, alias="capacityId")
    default_dataset_storage_format: str | None = Field(
        default=None, alias="defaultDatasetStorageFormat"
    )
    # Undocumented in WorkspaceInfo; the GET endpoint returns it in practice.
    collation: str | None = Field(default=None, alias="defaultDataWarehouseCollation")


class WarehouseKind(StrEnum):
    """Discriminates between a full warehouse, a SQL analytics endpoint, and a snapshot."""

    WAREHOUSE = "Warehouse"
    SQL_ENDPOINT = "SQLEndpoint"
    SNAPSHOT = "WarehouseSnapshot"


class Warehouse(_FabricBase):
    """A Microsoft Fabric Data Warehouse or SQL analytics endpoint."""

    id: UUID
    name: str = Field(alias="displayName")
    description: str | None = None
    workspace_id: UUID = Field(alias="workspaceId")
    kind: WarehouseKind
    connection_string: str | None = Field(default=None, alias="connectionString")
    collation: str | None = Field(default=None, alias="defaultCollation")
    created_date: datetime | None = Field(default=None, alias="createdDate")

    @classmethod
    def from_api(cls, payload: dict[str, object], kind: WarehouseKind) -> Warehouse:
        """Build a Warehouse from a raw API response dict.

        Picks up the connection string from the correct nested properties path
        depending on whether the item is a WAREHOUSE or SQL_ENDPOINT.
        """
        _raw_props = payload.get("properties")
        props: dict[str, object] = (
            cast("dict[str, object]", _raw_props) if isinstance(_raw_props, dict) else {}
        )

        if kind == WarehouseKind.WAREHOUSE:
            conn_string = props.get("connectionString")
        elif kind == WarehouseKind.SQL_ENDPOINT:
            _raw_sql_ep = props.get("sqlEndpointProperties")
            sql_ep: dict[str, object] = (
                cast("dict[str, object]", _raw_sql_ep) if isinstance(_raw_sql_ep, dict) else {}
            )
            conn_string = sql_ep.get("connectionString")
        else:
            msg = f"from_api does not support kind={kind}"
            raise ValueError(msg)

        flat: dict[str, object] = {
            "id": payload.get("id"),
            "displayName": payload.get("displayName"),
            "description": payload.get("description"),
            "workspaceId": payload.get("workspaceId"),
            "kind": kind,
            "connectionString": conn_string,
            "defaultCollation": props.get("defaultCollation"),
            "createdDate": props.get("createdDate"),
        }
        return cls.model_validate(flat)


class WarehouseSnapshot(_FabricBase):
    """A point-in-time snapshot of a Warehouse."""

    id: UUID
    name: str = Field(alias="displayName")
    parent_warehouse_id: UUID = Field(alias="parentWarehouseId")
    snapshot_dt: datetime | None = Field(default=None, alias="snapshotDateTime")


class CreationModeType(StrEnum):
    """Whether the restore point was created by a user or by the system."""

    USER_DEFINED = "UserDefined"
    SYSTEM_CREATED = "SystemCreated"


class RestorePoint(_FabricBase):
    """A restore point for a Warehouse.

    Note: ``id`` is a string timestamp (e.g. ``"1726617378000"``), *not* a UUID.
    """

    id: str
    name: str = Field(alias="displayName")
    description: str | None = None
    creation_mode: CreationModeType = Field(alias="creationMode")
    event_date_time: datetime | None = Field(default=None, alias="eventDateTime")

    @classmethod
    def from_api(cls, payload: dict[str, object]) -> RestorePoint:
        """Build a RestorePoint from the raw API response dict.

        Flattens ``creationDetails.eventDateTime`` to the top level so the
        standard Pydantic ``model_validate`` path can handle it.
        """
        _raw_details = payload.get("creationDetails")
        details: dict[str, object] = (
            cast("dict[str, object]", _raw_details) if isinstance(_raw_details, dict) else {}
        )
        flat: dict[str, object] = {
            "id": payload.get("id"),
            "displayName": payload.get("displayName"),
            "description": payload.get("description"),
            "creationMode": payload.get("creationMode"),
            "eventDateTime": details.get("eventDateTime"),
        }
        return cls.model_validate(flat)


class AuditSettings(_FabricBase):
    """Auditing configuration for a Warehouse."""

    state: Literal["Enabled", "Disabled"]
    retention_days: int = Field(alias="retentionDays")
    action_groups: list[str] = Field(default_factory=list, alias="auditActionsAndGroups")


class RunningQuery(_FabricBase):
    """A currently-executing or recently-completed SQL query."""

    session_id: int
    request_id: str | None = None
    status: str
    start_time: datetime
    total_elapsed_time_ms: int = Field(alias="total_elapsed_time")
    login_name: str | None = None
    command: str | None = None
    query_text: str | None = None

    @field_validator("request_id", mode="before")
    @classmethod
    def _coerce_request_id(cls, v: object) -> str | None:
        if v is None:
            return None
        return str(v)
