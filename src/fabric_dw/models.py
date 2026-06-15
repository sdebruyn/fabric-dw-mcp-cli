"""Pydantic v2 models for Microsoft Fabric Data Warehouse domain objects."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Shared type aliases and low-level helpers
# ---------------------------------------------------------------------------

_AnyDict = dict[str, Any]
_Flattener = Callable[[_AnyDict], _AnyDict]


def _as_dict(data: object) -> _AnyDict | None:
    """Return *data* cast to ``_AnyDict`` when it is a :class:`dict`, else ``None``.

    Used in ``@model_validator(mode="before")`` methods to avoid repeating the
    ``isinstance`` guard and ``cast`` on every validator:

    .. code-block:: python

        d = _as_dict(data)
        if d is None:
            return data
        # work with d ...

    """
    if isinstance(data, dict):
        return cast("_AnyDict", data)
    return None


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

    @model_validator(mode="before")
    @classmethod
    def _flatten_api_payload(cls, data: object) -> object:
        """Flatten nested ``properties`` to top-level Pydantic fields.

        Picks up the connection string from the correct nested path depending
        on the ``kind`` field (WAREHOUSE uses ``properties.connectionString``;
        SQL_ENDPOINT uses ``properties.sqlEndpointProperties.connectionString``).

        If ``data`` is already a flat dict (e.g. already processed or assembled
        manually), the properties-flattening is skipped so the standard
        Pydantic constructor path keeps working.
        """
        d = _as_dict(data)
        if d is None:
            return data
        if "properties" not in d:
            # Already flat (or manually constructed without properties key).
            return d

        props = d.get("properties")
        props_dict: dict[str, Any] = props if isinstance(props, dict) else {}

        kind_val = d.get("kind")
        conn_string: Any = None
        if kind_val == WarehouseKind.WAREHOUSE:
            conn_string = props_dict.get("connectionString")
        elif kind_val == WarehouseKind.SQL_ENDPOINT:
            sql_ep = props_dict.get("sqlEndpointProperties")
            sql_ep_dict: dict[str, Any] = sql_ep if isinstance(sql_ep, dict) else {}
            conn_string = sql_ep_dict.get("connectionString")
        # For SNAPSHOT kind or unknown kinds, conn_string stays None.

        return {
            **d,
            "connectionString": d.get("connectionString", conn_string),
            "defaultCollation": d.get("defaultCollation", props_dict.get("defaultCollation")),
            "createdDate": d.get("createdDate", props_dict.get("createdDate")),
        }

    @classmethod
    def from_api(cls, payload: dict[str, object], kind: WarehouseKind) -> Warehouse:
        """Build a Warehouse from a raw API response dict.

        Delegates to :meth:`model_validate` with *kind* merged into the payload.
        The :meth:`_flatten_api_payload` before-validator handles nested
        ``properties`` → top-level field flattening transparently.

        Use ``Warehouse.model_validate({**payload, "kind": kind})`` directly
        when you do not need the SNAPSHOT guard below.

        Args:
            payload: Raw dict from the Fabric REST API.
            kind: The item kind; must not be :attr:`WarehouseKind.SNAPSHOT`.

        Raises:
            ValueError: If *kind* is :attr:`WarehouseKind.SNAPSHOT`.  Snapshots
                have their own model (:class:`WarehouseSnapshot`); passing a
                snapshot payload here would silently produce an invalid object.
        """
        if kind == WarehouseKind.SNAPSHOT:
            msg = f"from_api does not support kind={kind}"
            raise ValueError(msg)
        return cls.model_validate({**payload, "kind": kind})


class WarehouseSnapshot(_FabricBase):
    """A point-in-time snapshot of a Warehouse."""

    id: UUID
    name: str = Field(alias="displayName")
    parent_warehouse_id: UUID = Field(alias="parentWarehouseId")
    snapshot_dt: datetime | None = Field(default=None, alias="snapshotDateTime")


class CreationModeType(StrEnum):
    """Known values for the ``creationMode`` field of a :class:`RestorePoint`.

    This is an **open** enum — MS Learn notes that additional creation mode
    types may be added over time.  The ``creation_mode`` field on
    :class:`RestorePoint` is typed as ``str | None`` so that future unknown
    values and API responses with null fields pass Pydantic validation without
    error.  Use these constants when you need to compare against known values.
    """

    USER_DEFINED = "UserDefined"
    SYSTEM_CREATED = "SystemCreated"


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

    @model_validator(mode="before")
    @classmethod
    def _flatten_creation_details(cls, data: object) -> object:
        """Flatten ``creationDetails.eventDateTime`` to the top level.

        If ``eventDateTime`` is already present at the top level (e.g. when
        calling :meth:`model_validate` directly on an already-flat dict), the
        existing value is preserved.
        """
        d = _as_dict(data)
        if d is None:
            return data
        if "eventDateTime" in d or "creationDetails" not in d:
            return d
        details = d.get("creationDetails")
        details_dict: dict[str, Any] = details if isinstance(details, dict) else {}
        return {**d, "eventDateTime": details_dict.get("eventDateTime")}

    @classmethod
    def from_api(cls, payload: dict[str, object]) -> RestorePoint:
        """Build a RestorePoint from the raw API response dict.

        .. deprecated::
            Prefer ``RestorePoint.model_validate(payload)`` directly.
            This shim delegates to :meth:`model_validate` and is kept for
            backward compatibility with existing call sites.
        """
        return cls.model_validate(payload)


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
    """A completed SQL request from ``queryinsights.exec_requests_history``.

    .. note::
        This model is an intentional 1-to-1 projection of the DMV / queryinsights
        view columns.  **Do not restructure into sub-models** — that would
        destabilise the query_insights service and the CLI rendering layer.
        Adding a new column?  Append it in the appropriate group below.
    """

    # --- identifiers ---
    distributed_statement_id: UUID | None = None
    session_id: int | None = None
    connection_id: UUID | None = None
    batch_id: UUID | None = None
    root_batch_id: UUID | None = None

    # --- timing ---
    submit_time: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    total_elapsed_time_ms: int | None = None

    # --- details ---
    database_name: str | None = None
    is_distributed: int | None = None
    statement_type: str | None = None
    login_name: str | None = None
    row_count: int
    status: str | None = None
    program_name: str | None = None
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
    """A completed session from ``queryinsights.exec_sessions_history``.

    .. note::
        This model is an intentional 1-to-1 projection of the DMV / queryinsights
        view columns.  **Do not restructure into sub-models** — that would
        destabilise the query_insights service and the CLI rendering layer.
        Adding a new column?  Append it in the appropriate group below.
    """

    # --- identifiers ---
    session_id: int
    connection_id: UUID

    # --- timing ---
    session_start_time: datetime
    session_end_time: datetime | None = None
    total_query_elapsed_time_ms: int
    last_request_start_time: datetime
    last_request_end_time: datetime | None = None

    # --- details ---
    program_name: str | None = None
    login_name: str
    status: str
    context_info: bytes | None = None
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
    min_run_total_elapsed_time_ms: int
    max_run_total_elapsed_time_ms: int
    number_of_successful_runs: int
    number_of_failed_runs: int
    number_of_canceled_runs: int
    query_hash: str | None = None


class LongRunningQuery(_FabricBase):
    """A long-running query from ``queryinsights.long_running_queries``."""

    last_run_start_time: datetime | None = None
    last_run_command: str | None = None
    median_total_elapsed_time_ms: int
    number_of_runs: int
    last_run_total_elapsed_time_ms: int
    last_dist_statement_id: UUID | None = None
    query_hash: str | None = None


class SqlPoolInsight(_FabricBase):
    """A SQL pool insight event from ``queryinsights.sql_pool_insights``."""

    sql_pool_name: str | None = None
    timestamp: datetime | None = None
    max_resource_percentage: int | None = None
    is_optimized_for_reads: bool | None = None
    current_workspace_capacity: str | None = None
    is_pool_under_pressure: bool | None = None


class TableSyncError(_FabricBase):
    """Error details for a single table synchronization failure."""

    error_code: str | None = Field(default=None, alias="errorCode")
    message: str | None = None


class TableSyncStatus(_FabricBase):
    """Per-table result returned by a SQL analytics endpoint metadata refresh.

    Corresponds to the ``TableSyncStatus`` object in the Fabric REST API.
    """

    table_name: str = Field(alias="tableName")
    status: str
    start_date_time: datetime | None = Field(default=None, alias="startDateTime")
    end_date_time: datetime | None = Field(default=None, alias="endDateTime")
    last_successful_sync_date_time: datetime | None = Field(
        default=None, alias="lastSuccessfulSyncDateTime"
    )
    error: TableSyncError | None = None


class View(_FabricBase):
    """A SQL view on a Fabric Data Warehouse or SQL Analytics Endpoint."""

    schema_name: str
    name: str
    qualified_name: str
    definition: str | None = None
    created: datetime
    modified: datetime


class StoredProcedure(_FabricBase):
    """A stored procedure on a Fabric Data Warehouse or SQL Analytics Endpoint."""

    schema_name: str
    name: str
    qualified_name: str
    definition: str | None = None
    created: datetime
    modified: datetime


class Schema(_FabricBase):
    """A SQL schema on a Fabric Data Warehouse."""

    name: str
    principal_id: int | None = None


class Table(_FabricBase):
    """A SQL table on a Fabric Data Warehouse or SQL Analytics Endpoint."""

    schema_name: str
    name: str
    qualified_name: str
    created: datetime
    modified: datetime


# ---------------------------------------------------------------------------
# SQL Pools (beta)
# ---------------------------------------------------------------------------

#: Known classifier type values from the API — open enum; more may be added.
CLASSIFIER_TYPE_APPLICATION_NAME = "Application Name"
CLASSIFIER_TYPE_APPLICATION_NAME_REGEX = "Application Name Regex"


class SqlPoolClassifier(_FabricBase):
    """A classifier element that routes sessions to a SQL pool.

    ``type`` is treated as an open string rather than a closed enum because
    the API explicitly states "Additional classifier element types may be
    added over time".  Use :data:`CLASSIFIER_TYPE_APPLICATION_NAME` and
    :data:`CLASSIFIER_TYPE_APPLICATION_NAME_REGEX` for the known values.
    """

    type: str
    value: list[str] = Field(default_factory=list, alias="value")


class SqlPool(_FabricBase):
    """A single custom SQL pool element."""

    name: str
    is_default: bool = Field(default=False, alias="isDefault")
    max_resource_percentage: Annotated[int, Field(ge=1, le=100)] = Field(
        alias="maxResourcePercentage"
    )
    optimize_for_reads: bool = Field(default=True, alias="optimizeForReads")
    classifier: SqlPoolClassifier | None = Field(default=None, alias="classifier")


class SqlPoolsConfiguration(_FabricBase):
    """SQL pools configuration for a workspace (beta API)."""

    custom_sql_pools_enabled: bool = Field(alias="customSQLPoolsEnabled")
    custom_sql_pools: list[SqlPool] = Field(default_factory=list, alias="customSQLPools")

    def validate_for_patch(self) -> None:
        """Validate constraints that must hold before issuing a PATCH request.

        This is intentionally *not* a Pydantic model validator so that GET
        responses with server-side state that violates these constraints
        (e.g. during a beta API drift or a race condition) can still be
        deserialised without raising.  Call this explicitly before any write
        operation.

        Raises:
            ValueError: If the sum of ``maxResourcePercentage`` exceeds 100,
                or if more than one pool is marked as default.
        """
        pools = self.custom_sql_pools
        if not pools:
            return

        total = sum(p.max_resource_percentage for p in pools)
        max_resource_percentage = 100
        if total > max_resource_percentage:
            msg = (
                f"Sum of maxResourcePercentage across all SQL pools is {total}, which exceeds 100."
            )
            raise ValueError(msg)

        defaults = [p for p in pools if p.is_default]
        if len(defaults) > 1:
            names = ", ".join(p.name for p in defaults)
            msg = f"Exactly one SQL pool may be marked as default; got {len(defaults)}: {names}"
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# Item access details (admin API)
# ---------------------------------------------------------------------------


class PrincipalType(StrEnum):
    """The type of the principal returned by the item access details API.

    Note: additional types may be added by Microsoft over time.
    """

    USER = "User"
    GROUP = "Group"
    SERVICE_PRINCIPAL = "ServicePrincipal"
    SERVICE_PRINCIPAL_PROFILE = "ServicePrincipalProfile"
    ENTIRE_TENANT = "EntireTenant"


def _flatten_user(data: _AnyDict) -> _AnyDict:
    """Extract ``userPrincipalName`` from the ``userDetails`` sub-object."""
    user_details = data.get("userDetails")
    if isinstance(user_details, dict):
        return {**data, "user_principal_name": user_details.get("userPrincipalName")}
    return data


def _flatten_group(data: _AnyDict) -> _AnyDict:
    """Extract ``groupType`` from the ``groupDetails`` sub-object."""
    group_details = data.get("groupDetails")
    if isinstance(group_details, dict):
        return {**data, "group_type": group_details.get("groupType")}
    return data


def _flatten_service_principal(data: _AnyDict) -> _AnyDict:
    """Extract ``aadAppId`` from the ``servicePrincipalDetails`` sub-object."""
    sp_details = data.get("servicePrincipalDetails")
    if isinstance(sp_details, dict):
        return {**data, "aad_app_id": sp_details.get("aadAppId")}
    return data


def _flatten_noop(data: _AnyDict) -> _AnyDict:
    """No-op flattener — for principal types with no scalar sub-fields to extract."""
    return data


#: Registry mapping principal type strings to their flattening function.
#: Unknown principal types fall back to :func:`_flatten_noop` so parsing never
#: crashes when Microsoft adds a new variant.
_PRINCIPAL_FLATTENERS: dict[str, _Flattener] = {
    PrincipalType.USER: _flatten_user,
    PrincipalType.GROUP: _flatten_group,
    PrincipalType.SERVICE_PRINCIPAL: _flatten_service_principal,
    # ServicePrincipalProfile and EntireTenant: identity carried by top-level fields only.
    PrincipalType.SERVICE_PRINCIPAL_PROFILE: _flatten_noop,
    PrincipalType.ENTIRE_TENANT: _flatten_noop,
}


class ItemAccessPrincipal(_FabricBase):
    """Minimal representation of a principal in an item access record.

    Covers all five principal variants (User, Group, ServicePrincipal,
    ServicePrincipalProfile, EntireTenant).  Type-specific detail fields
    (``userPrincipalName``, ``aadAppId``, ``groupType``) are surfaced as
    optional top-level attributes so that consumers do not need to traverse
    nested detail sub-objects.
    """

    id: UUID
    display_name: str | None = Field(default=None, alias="displayName")
    type: str  # open string — new values may appear

    # User-specific
    user_principal_name: str | None = None

    # Group-specific
    group_type: str | None = None

    # ServicePrincipal-specific
    aad_app_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_detail(cls, data: object) -> object:
        """Flatten the type-specific detail sub-object to the top level.

        Dispatches to the appropriate flattener in :data:`_PRINCIPAL_FLATTENERS`
        keyed by the principal ``type`` string.  Unknown principal types fall
        back to a no-op so parsing never raises on new API variants.
        """
        d = _as_dict(data)
        if d is None:
            return data
        principal_type = str(d.get("type", ""))
        flattener = _PRINCIPAL_FLATTENERS.get(principal_type, _flatten_noop)
        return flattener(d)


class ItemAccessDetail(_FabricBase):
    """Item-level permission details for a single principal."""

    item_type: str | None = Field(default=None, alias="type")
    permissions: list[str] = Field(default_factory=list)
    additional_permissions: list[str] = Field(default_factory=list, alias="additionalPermissions")


class ItemAccess(_FabricBase):
    """Combined principal + permission record from the admin item-access API."""

    principal: ItemAccessPrincipal
    item_access_details: ItemAccessDetail = Field(alias="itemAccessDetails")

    @classmethod
    def from_api(cls, raw: dict[str, object]) -> ItemAccess:
        """Build an :class:`ItemAccess` from a raw ``accessDetails`` element."""
        return cls.model_validate(raw)


class SqlResult(_FabricBase):
    """Result set returned by :func:`~fabric_dw.services.sql_exec.execute`.

    Attributes:
        columns: Ordered list of column names from the last result set.
            Empty for DDL/DML statements that produce no result set.
        rows: Each element is one row; values are JSON-serialisable scalars
            (``str``, ``int``, ``float``, ``bool``, ``None``).
            ``datetime`` values are pre-serialised to ISO-8601 strings.
            ``Decimal`` values are pre-serialised to strings.
            ``bytes`` (varbinary) columns are base64-encoded strings; the
            corresponding column name is suffixed with ``__base64`` so
            callers can identify binary columns.
        rowcount: Number of rows affected (DML) or fetched (SELECT).
            May be ``-1`` if the driver does not report a count.
    """

    columns: list[str] = Field(default_factory=list)
    rows: list[list[object]] = Field(default_factory=list)
    rowcount: int = -1


# ---------------------------------------------------------------------------
# API payload helpers — boundary validation for raw HTTP response bodies
# ---------------------------------------------------------------------------


def as_props(raw: object) -> _AnyDict:
    """Return *raw* as a dict if it is one, else an empty dict.

    Use this at the API boundary instead of ``cast(...)`` + ``isinstance``
    to validate that a nested ``properties`` value is a plain dict.

    Args:
        raw: The value to coerce (typically ``response.get("properties")``).

    Returns:
        The original dict if *raw* is a :class:`dict`, otherwise ``{}``.
    """
    return cast("_AnyDict", raw) if isinstance(raw, dict) else {}


class WarehouseSnapshotApiPayload(_FabricBase):
    """Typed wrapper for the raw API response body of a warehouse-snapshot item.

    Used by :mod:`fabric_dw.services.snapshots` to validate/type-narrow the
    nested ``properties`` object returned by the
    ``GET /workspaces/{ws}/warehouseSnapshots`` and
    ``GET /workspaces/{ws}/warehouseSnapshots/{id}`` endpoints, avoiding
    reflexive ``cast(...)`` calls at each call site.
    """

    id: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    parent_warehouse_id: str | None = Field(default=None, alias="parentWarehouseId")
    snapshot_date_time: str | None = Field(default=None, alias="snapshotDateTime")

    @classmethod
    def props_from_item(cls, item: dict[str, object]) -> WarehouseSnapshotApiPayload:
        """Parse the ``properties`` sub-object of a snapshot API item response.

        Returns a :class:`WarehouseSnapshotApiPayload` populated from the
        ``properties`` dict, or an empty instance if the key is absent.
        """
        return cls.model_validate(as_props(item.get("properties")))
