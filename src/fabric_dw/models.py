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


class CreationModeType:
    """Known values for the ``creationMode`` field of a :class:`RestorePoint`.

    MS Learn notes that additional creation mode types may be added over time,
    so this is an **open** set of constants rather than a closed ``StrEnum``.
    The ``creation_mode`` field on :class:`RestorePoint` is typed as ``str | None``
    so that future unknown values and API responses with null fields pass
    Pydantic validation without error.
    """

    USER_DEFINED: str = "UserDefined"
    SYSTEM_CREATED: str = "SystemCreated"


class RestorePoint(_FabricBase):
    """A restore point for a Warehouse.

    Note: ``id`` is a string timestamp (e.g. ``"1726617378000"``), *not* a UUID.
    The Fabric API may return ``None`` for ``displayName`` and ``creationMode``
    on system-created restore points; both fields are therefore optional.
    """

    id: str
    name: str | None = Field(default=None, alias="displayName")
    description: str | None = None
    creation_mode: str | None = Field(default=None, alias="creationMode")
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


class Connection(_FabricBase):
    """An active SQL connection on a Fabric Data Warehouse or SQL Analytics Endpoint.

    Sourced from ``sys.dm_exec_connections``.
    """

    session_id: int | None = None
    connect_time: datetime
    client_net_address: str | None = None
    auth_scheme: str | None = None
    encrypt_option: str | None = None
    net_transport: str
    most_recent_session_id: int | None = None


class ExecRequestHistory(_FabricBase):
    """A completed SQL request from ``queryinsights.exec_requests_history``."""

    distributed_statement_id: UUID | None = None
    database_name: str | None = None
    submit_time: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    is_distributed: int | None = None
    statement_type: str | None = None
    total_elapsed_time_ms: int | None = None
    login_name: str | None = None
    row_count: int
    status: str | None = None
    session_id: int | None = None
    connection_id: UUID | None = None
    program_name: str | None = None
    batch_id: UUID | None = None
    root_batch_id: UUID | None = None
    query_hash: str | None = None
    label: str | None = None
    result_cache_hit: int | None = None
    sql_pool_name: str | None = None
    allocated_cpu_time_ms: int | None = None
    data_scanned_remote_storage_mb: float | None = None
    data_scanned_memory_mb: float | None = None
    data_scanned_disk_mb: float | None = None
    command: str | None = None
    error_code: int | None = None


class ExecSessionHistory(_FabricBase):
    """A completed session from ``queryinsights.exec_sessions_history``."""

    session_id: int
    connection_id: UUID
    session_start_time: datetime
    session_end_time: datetime | None = None
    program_name: str | None = None
    login_name: str
    status: str
    context_info: bytes | None = None
    total_query_elapsed_time_ms: int
    last_request_start_time: datetime
    last_request_end_time: datetime | None = None
    is_user_process: bool
    prev_error: int
    group_id: int
    database_id: int | None = None
    authenticating_database_id: int | None = None
    open_transaction_count: int | None = None
    text_size: int
    language: str | None = None
    date_format: str | None = None
    date_first: int
    quoted_identifier: bool
    arithabort: bool
    ansi_null_dflt_on: bool
    ansi_defaults: bool
    ansi_warnings: bool
    ansi_padding: bool
    ansi_nulls: bool
    concat_null_yields_null: bool
    transaction_isolation_level: int
    lock_timeout: int
    deadlock_priority: int
    original_security_id: bytes
    database_name: str | None = None


class FrequentlyRunQuery(_FabricBase):
    """A frequently-run query from ``queryinsights.frequently_run_queries``."""

    last_run_start_time: datetime | None = None
    last_run_command: str | None = None
    number_of_runs: int
    avg_total_elapsed_time_ms: int
    last_run_total_elapsed_time_ms: int
    last_dist_statement_id: UUID | None = None
    last_run_session_id: int | None = None
    min_run_total_elapsed_time_ms: int
    max_run_total_elapsed_time_ms: int
    number_of_successful_runs: int
    number_of_failed_runs: int
    number_of_cancelled_runs: int
    query_hash: str | None = None


class LongRunningQuery(_FabricBase):
    """A long-running query from ``queryinsights.long_running_queries``."""

    last_run_start_time: datetime | None = None
    last_run_command: str | None = None
    median_total_elapsed_time_ms: int
    number_of_runs: int
    last_run_total_elapsed_time_ms: int
    last_dist_statement_id: UUID | None = None
    last_run_session_id: int | None = None
    query_hash: str | None = None


class SqlPoolInsight(_FabricBase):
    """A SQL pool insight event from ``queryinsights.sql_pool_insights``."""

    sql_pool_name: str | None = None
    timestamp: datetime | None = None
    max_resource_percentage: int | None = None
    is_optimized_for_reads: bool | None = None
    current_workspace_capacity: str | None = None
    is_pool_under_pressure: bool | None = None
