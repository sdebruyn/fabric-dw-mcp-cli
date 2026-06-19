"""Tests for fabric_dw.models — Pydantic v2 round-trip, alias handling, and frozen behaviour."""

import json
from copy import deepcopy
from datetime import datetime
from typing import ClassVar
from uuid import UUID

import pytest
from pydantic import ValidationError

from fabric_dw.models import (
    AuditSettings,
    CreationModeType,
    ExecRequestHistory,
    ExecSessionHistory,
    FrequentlyRunQuery,
    ItemAccess,
    ItemAccessPrincipal,
    LongRunningQuery,
    PrincipalType,
    RestorePoint,
    RunningQuery,
    SqlPoolClassifier,
    SqlResult,
    Warehouse,
    WarehouseKind,
    WarehouseSnapshot,
    WarehouseSnapshotApiPayload,
    Workspace,
    as_props,
)
from tests.fixtures.api_payloads import (
    AUDIT_SETTINGS_PAYLOAD,
    ITEM_ACCESS_DETAILS_PAYLOAD,
    LAKEHOUSE_GET_PAYLOAD,
    RESTORE_POINT_PAYLOAD,
    WAREHOUSE_GET_PAYLOAD,
    WAREHOUSE_SNAPSHOT_PAYLOAD,
    WORKSPACE_LIST_PAYLOAD,
)


class TestWorkspace:
    def test_round_trip(self) -> None:
        raw = json.loads(WORKSPACE_LIST_PAYLOAD)
        workspace_data = raw["value"][0]
        obj = Workspace.model_validate(workspace_data)
        dumped = obj.model_dump(by_alias=True, mode="json", exclude_none=True)
        # Strip extra fields not modelled (e.g. "type") before comparing.
        modelled_keys = {
            "id",
            "displayName",
            "description",
            "capacityId",
            "defaultDatasetStorageFormat",
        }
        expected = {k: v for k, v in workspace_data.items() if k in modelled_keys and v is not None}
        assert dumped == expected

    def test_round_trip_no_capacity(self) -> None:
        raw = json.loads(WORKSPACE_LIST_PAYLOAD)
        workspace_data = raw["value"][1]
        obj = Workspace.model_validate(workspace_data)
        assert obj.capacity_id is None
        assert obj.description is None

    def test_extra_fields_ignored(self) -> None:
        raw = json.loads(WORKSPACE_LIST_PAYLOAD)
        workspace_data = dict(raw["value"][0])
        workspace_data["unexpectedField"] = "some_value"
        obj = Workspace.model_validate(workspace_data)
        assert obj.name == workspace_data["displayName"]

    def test_frozen(self) -> None:
        raw = json.loads(WORKSPACE_LIST_PAYLOAD)
        obj = Workspace.model_validate(raw["value"][0])
        with pytest.raises(ValidationError):
            obj.name = "changed"


class TestWarehouse:
    def test_from_api_warehouse_kind(self) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        obj = Warehouse.from_api(payload, kind=WarehouseKind.WAREHOUSE)
        expected_conn = payload["properties"]["connectionString"]
        assert obj.connection_string == expected_conn
        assert obj.kind == WarehouseKind.WAREHOUSE

    def test_from_api_sql_endpoint_kind(self) -> None:
        payload = json.loads(LAKEHOUSE_GET_PAYLOAD)
        obj = Warehouse.from_api(payload, kind=WarehouseKind.SQL_ENDPOINT)
        expected_conn = payload["properties"]["sqlEndpointProperties"]["connectionString"]
        assert obj.connection_string == expected_conn
        assert obj.kind == WarehouseKind.SQL_ENDPOINT

    def test_from_api_snapshot_kind_raises(self) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        with pytest.raises(ValueError, match="from_api does not support kind="):
            Warehouse.from_api(payload, kind=WarehouseKind.SNAPSHOT)

    def test_from_api_warehouse_collation_and_created_date(self) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        obj = Warehouse.from_api(payload, kind=WarehouseKind.WAREHOUSE)
        assert obj.collation == payload["properties"]["defaultCollation"]
        assert obj.created_date is not None

    def test_extra_fields_ignored(self) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        payload_copy = deepcopy(payload)
        payload_copy["totallyRandomExtraField"] = "noise"
        obj = Warehouse.from_api(payload_copy, kind=WarehouseKind.WAREHOUSE)
        assert obj.name == payload["displayName"]

    def test_frozen(self) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        obj = Warehouse.from_api(payload, kind=WarehouseKind.WAREHOUSE)
        with pytest.raises(ValidationError):
            obj.name = "changed"

    def test_workspace_id_field(self) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        obj = Warehouse.from_api(payload, kind=WarehouseKind.WAREHOUSE)
        assert str(obj.workspace_id) == payload["workspaceId"]


class TestWarehouseKind:
    def test_warehouse_value(self) -> None:
        assert WarehouseKind.WAREHOUSE.value == "Warehouse"

    def test_sql_endpoint_value(self) -> None:
        assert WarehouseKind.SQL_ENDPOINT.value == "SQLEndpoint"

    def test_snapshot_value(self) -> None:
        assert WarehouseKind.SNAPSHOT.value == "WarehouseSnapshot"

    def test_warehouse_label(self) -> None:
        assert WarehouseKind.WAREHOUSE.label == "Data Warehouse"

    def test_sql_endpoint_label(self) -> None:
        assert WarehouseKind.SQL_ENDPOINT.label == "SQL Analytics Endpoint"

    def test_snapshot_label(self) -> None:
        assert WarehouseKind.SNAPSHOT.label == "Warehouse Snapshot"

    def test_label_differs_from_raw_value_for_endpoint(self) -> None:
        # Guards against regressing to the raw camelCase enum value in prompts.
        assert WarehouseKind.SQL_ENDPOINT.label != WarehouseKind.SQL_ENDPOINT.value


class TestWarehouseSnapshot:
    def test_round_trip(self) -> None:
        payload = json.loads(WAREHOUSE_SNAPSHOT_PAYLOAD)
        obj = WarehouseSnapshot.model_validate(payload)
        dumped = obj.model_dump(by_alias=True, mode="json", exclude_none=True)
        assert dumped == payload

    def test_extra_fields_ignored(self) -> None:
        payload = json.loads(WAREHOUSE_SNAPSHOT_PAYLOAD)
        payload_copy = dict(payload)
        payload_copy["extraKey"] = "extraValue"
        obj = WarehouseSnapshot.model_validate(payload_copy)
        assert obj.name == payload["displayName"]

    def test_frozen(self) -> None:
        payload = json.loads(WAREHOUSE_SNAPSHOT_PAYLOAD)
        obj = WarehouseSnapshot.model_validate(payload)
        with pytest.raises(ValidationError):
            obj.name = "changed"


class TestRestorePoint:
    def test_round_trip(self) -> None:
        payload = json.loads(RESTORE_POINT_PAYLOAD)
        obj = RestorePoint.from_api(payload)
        assert obj.id == payload["id"]
        assert obj.name == payload["displayName"]
        assert obj.description == payload["description"]
        assert obj.creation_mode == payload["creationMode"]
        assert obj.event_date_time is not None

    def test_extra_fields_ignored(self) -> None:
        payload = json.loads(RESTORE_POINT_PAYLOAD)
        payload_copy = dict(payload)
        payload_copy["unknownProp"] = 42
        obj = RestorePoint.from_api(payload_copy)
        assert obj.name == payload["displayName"]

    def test_frozen(self) -> None:
        payload = json.loads(RESTORE_POINT_PAYLOAD)
        obj = RestorePoint.from_api(payload)
        with pytest.raises(ValidationError):
            obj.name = "changed"  # type: ignore[misc]


class TestAuditSettings:
    def test_round_trip(self) -> None:
        payload = json.loads(AUDIT_SETTINGS_PAYLOAD)
        obj = AuditSettings.model_validate(payload)
        dumped = obj.model_dump(by_alias=True, mode="json", exclude_none=True)
        assert dumped == payload

    def test_default_action_groups(self) -> None:
        obj = AuditSettings.model_validate({"state": "Disabled", "retentionDays": 7})
        assert obj.action_groups == []

    def test_extra_fields_ignored(self) -> None:
        payload = json.loads(AUDIT_SETTINGS_PAYLOAD)
        payload_copy = dict(payload)
        payload_copy["bogusField"] = "bogus"
        obj = AuditSettings.model_validate(payload_copy)
        assert obj.state == payload["state"]

    def test_frozen(self) -> None:
        payload = json.loads(AUDIT_SETTINGS_PAYLOAD)
        obj = AuditSettings.model_validate(payload)
        with pytest.raises(ValidationError):
            obj.state = "Disabled"


class TestRunningQuery:
    def test_round_trip(self) -> None:
        # Keys use DMV column names (aliases); total_elapsed_time is already in milliseconds.
        payload = {
            "session_id": 12,
            "request_id": "request-abc-123",
            "status": "running",
            "start_time": "2024-03-15T10:30:00Z",
            "total_elapsed_time": 5432,
            "login_name": "user@example.com",
            "command": "SELECT",
            "query_text": "SELECT * FROM sales.orders",
        }
        obj = RunningQuery.model_validate(payload)
        dumped = obj.model_dump(by_alias=True, mode="json", exclude_none=True)
        assert dumped == payload

    def test_nullable_fields(self) -> None:
        payload = {
            "session_id": 5,
            "request_id": "req-xyz",
            "status": "completed",
            "start_time": "2024-03-15T10:00:00Z",
            "total_elapsed_time": 100,
            "login_name": None,
            "command": None,
            "query_text": None,
        }
        obj = RunningQuery.model_validate(payload)
        assert obj.login_name is None
        assert obj.command is None
        assert obj.query_text is None

    def test_extra_fields_ignored(self) -> None:
        payload = {
            "session_id": 1,
            "request_id": "req-1",
            "status": "running",
            "start_time": "2024-03-15T10:00:00Z",
            "total_elapsed_time": 200,
            "login_name": "admin",
            "command": "SELECT",
            "query_text": "SELECT 1",
            "unknownColumn": "noise",
        }
        obj = RunningQuery.model_validate(payload)
        assert obj.session_id == 1

    def test_frozen(self) -> None:
        payload = {
            "session_id": 1,
            "request_id": "req-1",
            "status": "running",
            "start_time": "2024-03-15T10:00:00Z",
            "total_elapsed_time": 200,
            "login_name": None,
            "command": None,
            "query_text": None,
        }
        obj = RunningQuery.model_validate(payload)
        with pytest.raises(ValidationError):
            obj.status = "completed"

    def test_request_id_int_coerced_to_str(self) -> None:
        """The mssql driver returns request_id as int (0) for sessions with no active request."""
        payload = {
            "session_id": 7,
            "request_id": 0,
            "status": "running",
            "start_time": "2024-03-15T10:00:00Z",
            "total_elapsed_time": 0,
        }
        obj = RunningQuery.model_validate(payload)
        assert obj.request_id == "0"

    def test_request_id_none_accepted(self) -> None:
        """request_id may be None when there is no active request for a session."""
        payload = {
            "session_id": 7,
            "request_id": None,
            "status": "running",
            "start_time": "2024-03-15T10:00:00Z",
            "total_elapsed_time": 0,
        }
        obj = RunningQuery.model_validate(payload)
        assert obj.request_id is None


class TestCreationModeType:
    """CreationModeType is now a proper StrEnum."""

    def test_is_str_enum(self) -> None:
        assert issubclass(CreationModeType, str)

    def test_user_defined_value(self) -> None:
        assert CreationModeType.USER_DEFINED == "UserDefined"

    def test_system_created_value(self) -> None:
        assert CreationModeType.SYSTEM_CREATED == "SystemCreated"

    def test_compare_with_raw_string(self) -> None:
        """StrEnum compares equal to its string value — open-enum pattern works."""
        assert CreationModeType.USER_DEFINED == "UserDefined"
        assert CreationModeType.SYSTEM_CREATED == "SystemCreated"

    @pytest.mark.parametrize("mode_str", ["UserDefined", "SystemCreated"])
    def test_restore_point_known_values_pass_through(self, mode_str: str) -> None:
        """Known creationMode values round-trip via RestorePoint.creation_mode."""
        payload = {
            "id": "1726617378000",
            "creationMode": mode_str,
        }
        obj = RestorePoint.model_validate(payload)
        assert obj.creation_mode == mode_str

    def test_restore_point_unknown_value_pass_through(self) -> None:
        """Unknown creationMode values pass through without error (open enum)."""
        payload = {
            "id": "1726617378000",
            "creationMode": "FutureNewMode",
        }
        obj = RestorePoint.model_validate(payload)
        assert obj.creation_mode == "FutureNewMode"

    def test_restore_point_null_creation_mode(self) -> None:
        """Null creationMode is accepted (system-created restore points)."""
        payload = {"id": "1726617378000", "creationMode": None}
        obj = RestorePoint.model_validate(payload)
        assert obj.creation_mode is None


class TestWarehouseModelValidator:
    """Warehouse flattening via model_validator(mode='before') and from_api shim."""

    def test_model_validate_warehouse_kind(self) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        obj = Warehouse.model_validate({**payload, "kind": WarehouseKind.WAREHOUSE})
        assert obj.kind == WarehouseKind.WAREHOUSE
        assert obj.connection_string == payload["properties"]["connectionString"]

    def test_model_validate_sql_endpoint_kind(self) -> None:
        payload = json.loads(LAKEHOUSE_GET_PAYLOAD)
        obj = Warehouse.model_validate({**payload, "kind": WarehouseKind.SQL_ENDPOINT})
        assert obj.kind == WarehouseKind.SQL_ENDPOINT
        expected = payload["properties"]["sqlEndpointProperties"]["connectionString"]
        assert obj.connection_string == expected

    def test_model_validate_collation_and_created_date(self) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        obj = Warehouse.model_validate({**payload, "kind": WarehouseKind.WAREHOUSE})
        assert obj.collation == payload["properties"]["defaultCollation"]
        assert obj.created_date is not None

    def test_from_api_shim_warehouse(self) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        obj = Warehouse.from_api(payload, kind=WarehouseKind.WAREHOUSE)
        assert obj.connection_string == payload["properties"]["connectionString"]

    def test_from_api_shim_sql_endpoint(self) -> None:
        payload = json.loads(LAKEHOUSE_GET_PAYLOAD)
        obj = Warehouse.from_api(payload, kind=WarehouseKind.SQL_ENDPOINT)
        expected = payload["properties"]["sqlEndpointProperties"]["connectionString"]
        assert obj.connection_string == expected

    def test_from_api_shim_snapshot_raises(self) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        with pytest.raises(ValueError, match="from_api does not support kind="):
            Warehouse.from_api(payload, kind=WarehouseKind.SNAPSHOT)

    def test_model_validate_and_from_api_consistent(self) -> None:
        """model_validate and from_api shim return equivalent objects."""
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        via_validator = Warehouse.model_validate({**payload, "kind": WarehouseKind.WAREHOUSE})
        via_shim = Warehouse.from_api(payload, kind=WarehouseKind.WAREHOUSE)
        assert via_validator == via_shim


class TestRestorePointModelValidator:
    """RestorePoint flattening via model_validator(mode='before') and from_api shim."""

    def test_model_validate_flattens_event_date_time(self) -> None:
        payload = json.loads(RESTORE_POINT_PAYLOAD)
        obj = RestorePoint.model_validate(payload)
        assert obj.event_date_time is not None

    def test_from_api_shim_delegates_to_model_validate(self) -> None:
        payload = json.loads(RESTORE_POINT_PAYLOAD)
        obj = RestorePoint.from_api(payload)
        assert obj.id == payload["id"]
        assert obj.event_date_time is not None

    def test_model_validate_and_from_api_consistent(self) -> None:
        """model_validate and from_api shim return equivalent objects."""
        payload = json.loads(RESTORE_POINT_PAYLOAD)
        via_validator = RestorePoint.model_validate(payload)
        via_shim = RestorePoint.from_api(payload)
        assert via_validator == via_shim

    def test_already_flat_dict_not_double_flattened(self) -> None:
        """Providing eventDateTime at top-level skips the creationDetails lookup."""
        flat = {
            "id": "1726617378000",
            "eventDateTime": "2024-03-15T06:00:00Z",
        }
        obj = RestorePoint.model_validate(flat)
        assert obj.event_date_time is not None

    def test_missing_creation_details_returns_none_event_date(self) -> None:
        """Missing creationDetails results in event_date_time=None (graceful)."""
        payload = {"id": "1726617378000", "displayName": "TestRP"}
        obj = RestorePoint.model_validate(payload)
        assert obj.event_date_time is None


class TestItemAccessPrincipalRegistry:
    """ItemAccessPrincipal flattener registry covers all known types + unknown fallback."""

    def _user_payload(self) -> dict:
        return {
            "id": "f3052d1c-61a9-46fb-8df9-0d78916ae041",
            "displayName": "Jacob Hancock",
            "type": "User",
            "userDetails": {"userPrincipalName": "jacob@example.com"},
        }

    def _group_payload(self) -> dict:
        return {
            "id": "c7db8e03-c8cb-4d4c-9f64-1dcd327c9d3c",
            "displayName": "TestSecurityGroup",
            "type": "Group",
            "groupDetails": {"groupType": "SecurityGroup"},
        }

    def _sp_payload(self) -> dict:
        return {
            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "displayName": "MyServicePrincipal",
            "type": "ServicePrincipal",
            "servicePrincipalDetails": {"aadAppId": "b2c3d4e5-f6a7-8901-bcde-f01234567891"},
        }

    def test_user_flattened(self) -> None:
        obj = ItemAccessPrincipal.model_validate(self._user_payload())
        assert obj.user_principal_name == "jacob@example.com"
        assert obj.group_type is None
        assert obj.aad_app_id is None

    def test_group_flattened(self) -> None:
        obj = ItemAccessPrincipal.model_validate(self._group_payload())
        assert obj.group_type == "SecurityGroup"
        assert obj.user_principal_name is None
        assert obj.aad_app_id is None

    def test_service_principal_flattened(self) -> None:
        obj = ItemAccessPrincipal.model_validate(self._sp_payload())
        assert str(obj.aad_app_id) == "b2c3d4e5-f6a7-8901-bcde-f01234567891"
        assert obj.user_principal_name is None
        assert obj.group_type is None

    def test_service_principal_profile_no_crash(self) -> None:
        """ServicePrincipalProfile has no sub-fields — should parse without error."""
        payload = {
            "id": "11111111-1111-1111-1111-111111111111",
            "displayName": "My SPP",
            "type": PrincipalType.SERVICE_PRINCIPAL_PROFILE,
        }
        obj = ItemAccessPrincipal.model_validate(payload)
        assert str(obj.type) == PrincipalType.SERVICE_PRINCIPAL_PROFILE

    def test_entire_tenant_no_crash(self) -> None:
        """EntireTenant principal parses without error."""
        payload = {
            "id": "22222222-2222-2222-2222-222222222222",
            "displayName": None,
            "type": PrincipalType.ENTIRE_TENANT,
        }
        obj = ItemAccessPrincipal.model_validate(payload)
        assert str(obj.type) == PrincipalType.ENTIRE_TENANT

    def test_unknown_principal_type_no_crash(self) -> None:
        """Unknown principal type falls back to noop flattener — no crash."""
        payload = {
            "id": "33333333-3333-3333-3333-333333333333",
            "displayName": "Mystery",
            "type": "FutureNewPrincipalType",
        }
        obj = ItemAccessPrincipal.model_validate(payload)
        assert obj.type == "FutureNewPrincipalType"
        assert obj.user_principal_name is None
        assert obj.group_type is None
        assert obj.aad_app_id is None

    def test_all_known_principal_types_from_fixture(self) -> None:
        """Full ItemAccess fixture round-trips all three common principal types."""
        raw = json.loads(ITEM_ACCESS_DETAILS_PAYLOAD)
        access_list = [ItemAccess.model_validate(entry) for entry in raw["accessDetails"]]
        types = {str(a.principal.type) for a in access_list}
        assert types == {"User", "Group", "ServicePrincipal"}


class TestSqlPoolClassifierAlias:
    """Dropped redundant alias on SqlPoolClassifier.type — serialisation unchanged."""

    def test_type_field_serialises_as_type(self) -> None:
        obj = SqlPoolClassifier.model_validate({"type": "Application Name", "value": ["MyApp"]})
        dumped = obj.model_dump(by_alias=True, mode="json")
        assert dumped["type"] == "Application Name"

    def test_type_field_without_alias_still_works(self) -> None:
        obj = SqlPoolClassifier(type="Application Name Regex")
        assert obj.type == "Application Name Regex"

    def test_round_trip_by_alias(self) -> None:
        payload = {"type": "Application Name", "value": ["Alpha", "Beta"]}
        obj = SqlPoolClassifier.model_validate(payload)
        dumped = obj.model_dump(by_alias=True, mode="json")
        assert dumped == payload


class TestAsProps:
    """as_props boundary helper."""

    def test_dict_passed_through(self) -> None:
        d = {"foo": 1, "bar": "baz"}
        assert as_props(d) is d

    def test_none_returns_empty(self) -> None:
        assert as_props(None) == {}

    def test_string_returns_empty(self) -> None:
        assert as_props("not a dict") == {}

    def test_list_returns_empty(self) -> None:
        assert as_props([1, 2, 3]) == {}

    def test_nested_api_props_extraction(self) -> None:
        item = {"id": "abc", "properties": {"parentWarehouseId": "def"}}
        props = as_props(item.get("properties"))
        assert props["parentWarehouseId"] == "def"


class TestWarehouseSnapshotApiPayload:
    """WarehouseSnapshotApiPayload boundary model for snapshot API responses."""

    _TYPED_ITEM: ClassVar[dict] = {
        "id": "f6a7b8c9-d0e1-2345-f012-34567890abcd",
        "displayName": "MySalesSnapshot",
        "properties": {
            "parentWarehouseId": "d4e5f6a7-b8c9-0123-def0-123456789abc",
            "snapshotDateTime": "2024-03-15T08:00:00Z",
        },
    }

    def test_props_from_item_extracts_parent_id(self) -> None:
        props = WarehouseSnapshotApiPayload.props_from_item(self._TYPED_ITEM)
        assert props.parent_warehouse_id == "d4e5f6a7-b8c9-0123-def0-123456789abc"

    def test_props_from_item_extracts_snapshot_datetime(self) -> None:
        props = WarehouseSnapshotApiPayload.props_from_item(self._TYPED_ITEM)
        assert props.snapshot_date_time == "2024-03-15T08:00:00Z"

    def test_props_from_item_missing_properties_returns_empty(self) -> None:
        item: dict = {"id": "abc", "displayName": "Test"}
        props = WarehouseSnapshotApiPayload.props_from_item(item)
        assert props.parent_warehouse_id is None
        assert props.snapshot_date_time is None

    def test_props_from_item_non_dict_properties_returns_empty(self) -> None:
        item: dict = {"id": "abc", "properties": "unexpected_string"}
        props = WarehouseSnapshotApiPayload.props_from_item(item)
        assert props.parent_warehouse_id is None

    def test_extra_fields_ignored(self) -> None:
        item = dict(self._TYPED_ITEM)
        item["properties"] = {**item["properties"], "unknownFutureField": "value"}  # type: ignore[index]
        props = WarehouseSnapshotApiPayload.props_from_item(item)
        assert props.parent_warehouse_id is not None


# ---------------------------------------------------------------------------
# Parametric round-trip: Warehouse flattening
# ---------------------------------------------------------------------------


class TestWarehouseFlatteningParametric:
    """Parametric tests for Warehouse._flatten_api_payload via model_validate.

    Covers the three kind branches (WAREHOUSE, SQL_ENDPOINT, SNAPSHOT) and
    the already-flat-dict short-circuit path.
    """

    @pytest.mark.parametrize(
        ("kind", "expected_conn"),
        [
            (
                WarehouseKind.WAREHOUSE,
                "saleswarehouse.datawarehouse.fabric.microsoft.com",
            ),
            (
                WarehouseKind.SQL_ENDPOINT,
                "warehouse-sql-ep.datawarehouse.fabric.microsoft.com",
            ),
        ],
        ids=["warehouse_kind", "sql_endpoint_kind"],
    )
    def test_connection_string_resolved_by_kind(
        self, kind: WarehouseKind, expected_conn: str
    ) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        obj = Warehouse.model_validate({**payload, "kind": kind})
        assert obj.connection_string == expected_conn

    def test_snapshot_kind_connection_string_is_none(self) -> None:
        """SNAPSHOT kind has no connection string — validator leaves it None."""
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        obj = Warehouse.model_validate({**payload, "kind": WarehouseKind.SNAPSHOT})
        assert obj.connection_string is None

    def test_already_flat_dict_skips_properties(self) -> None:
        """When 'properties' is absent the validator passes the dict through untouched."""
        flat = {
            "id": "d4e5f6a7-b8c9-0123-def0-123456789abc",
            "displayName": "FlatWarehouse",
            "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "kind": WarehouseKind.WAREHOUSE,
            "connectionString": "already-flat.fabric.microsoft.com",
        }
        obj = Warehouse.model_validate(flat)
        assert obj.connection_string == "already-flat.fabric.microsoft.com"
        assert obj.name == "FlatWarehouse"

    @pytest.mark.parametrize(
        "kind",
        [WarehouseKind.WAREHOUSE, WarehouseKind.SQL_ENDPOINT],
        ids=["warehouse", "sql_endpoint"],
    )
    def test_from_api_and_model_validate_produce_equal_objects(self, kind: WarehouseKind) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        via_validator = Warehouse.model_validate({**payload, "kind": kind})
        via_shim = Warehouse.from_api(payload, kind=kind)
        assert via_validator == via_shim

    def test_from_api_snapshot_raises_value_error(self) -> None:
        payload = json.loads(WAREHOUSE_GET_PAYLOAD)
        with pytest.raises(ValueError, match="from_api does not support kind="):
            Warehouse.from_api(payload, kind=WarehouseKind.SNAPSHOT)


# ---------------------------------------------------------------------------
# Parametric round-trip: RestorePoint flattening
# ---------------------------------------------------------------------------


class TestRestorePointFlatteningParametric:
    """Parametric tests for RestorePoint._flatten_creation_details."""

    @pytest.mark.parametrize(
        ("payload", "expected_event_dt_is_none"),
        [
            # Full nested payload with creationDetails
            (
                {
                    "id": "1726617378000",
                    "displayName": "RP1",
                    "creationMode": "UserDefined",
                    "creationDetails": {"eventDateTime": "2024-10-18T22:17:09Z"},
                },
                False,
            ),
            # Already flat — eventDateTime at top level
            (
                {
                    "id": "1726617379000",
                    "eventDateTime": "2024-10-18T22:17:09Z",
                },
                False,
            ),
            # No creationDetails at all
            (
                {"id": "1726617380000"},
                True,
            ),
            # creationDetails present but eventDateTime is missing
            (
                {"id": "1726617381000", "creationDetails": {}},
                True,
            ),
        ],
        ids=["nested", "already_flat", "no_details", "empty_details"],
    )
    def test_event_date_time_resolved(
        self, payload: dict, *, expected_event_dt_is_none: bool
    ) -> None:
        obj = RestorePoint.model_validate(payload)
        if expected_event_dt_is_none:
            assert obj.event_date_time is None
        else:
            assert obj.event_date_time is not None

    @pytest.mark.parametrize(
        "mode_str",
        [CreationModeType.USER_DEFINED, CreationModeType.SYSTEM_CREATED, "FutureUnknownMode"],
        ids=["user_defined", "system_created", "unknown_passthrough"],
    )
    def test_creation_mode_passthrough(self, mode_str: str) -> None:
        payload = {"id": "1726617378000", "creationMode": mode_str}
        obj = RestorePoint.model_validate(payload)
        assert obj.creation_mode == mode_str

    def test_from_api_shim_delegates_to_model_validate(self) -> None:
        payload = json.loads(RESTORE_POINT_PAYLOAD)
        via_validator = RestorePoint.model_validate(payload)
        via_shim = RestorePoint.from_api(payload)
        assert via_validator == via_shim

    def test_null_creation_mode_accepted(self) -> None:
        obj = RestorePoint.model_validate({"id": "x", "creationMode": None})
        assert obj.creation_mode is None


# ---------------------------------------------------------------------------
# Parametric: ItemAccessPrincipal flattener registry
# ---------------------------------------------------------------------------


class TestItemAccessPrincipalFlatteningParametric:
    """Parametric coverage for all five PrincipalType variants + unknown fallback."""

    @pytest.mark.parametrize(
        ("payload", "expected_upn", "expected_group_type", "expected_aad_app_id"),
        [
            # User
            (
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "type": "User",
                    "userDetails": {"userPrincipalName": "alice@example.com"},
                },
                "alice@example.com",
                None,
                None,
            ),
            # Group
            (
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "type": "Group",
                    "groupDetails": {"groupType": "SecurityGroup"},
                },
                None,
                "SecurityGroup",
                None,
            ),
            # ServicePrincipal
            (
                {
                    "id": "33333333-3333-3333-3333-333333333333",
                    "type": "ServicePrincipal",
                    "servicePrincipalDetails": {"aadAppId": "44444444-4444-4444-4444-444444444444"},
                },
                None,
                None,
                UUID("44444444-4444-4444-4444-444444444444"),
            ),
            # ServicePrincipalProfile — noop flattener, no extra fields
            (
                {
                    "id": "55555555-5555-5555-5555-555555555555",
                    "type": "ServicePrincipalProfile",
                },
                None,
                None,
                None,
            ),
            # EntireTenant — noop flattener, no extra fields
            (
                {
                    "id": "66666666-6666-6666-6666-666666666666",
                    "type": "EntireTenant",
                },
                None,
                None,
                None,
            ),
            # Unknown future type — falls back to noop, no crash
            (
                {
                    "id": "77777777-7777-7777-7777-777777777777",
                    "type": "FuturePrincipalType",
                },
                None,
                None,
                None,
            ),
        ],
        ids=["user", "group", "service_principal", "spp", "entire_tenant", "unknown"],
    )
    def test_flattening_by_type(
        self,
        payload: dict,
        expected_upn: str | None,
        expected_group_type: str | None,
        expected_aad_app_id: UUID | None,
    ) -> None:
        obj = ItemAccessPrincipal.model_validate(payload)
        assert obj.user_principal_name == expected_upn
        assert obj.group_type == expected_group_type
        assert obj.aad_app_id == expected_aad_app_id


# ---------------------------------------------------------------------------
# CreationModeType StrEnum parametric
# ---------------------------------------------------------------------------


class TestCreationModeTypeParametric:
    """CreationModeType is an open StrEnum — known values + unknown passthrough."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("UserDefined", CreationModeType.USER_DEFINED),
            ("SystemCreated", CreationModeType.SYSTEM_CREATED),
        ],
        ids=["user_defined", "system_created"],
    )
    def test_known_values_match_enum_members(self, raw: str, expected: CreationModeType) -> None:
        assert raw == expected
        # StrEnum members compare equal to their string value
        assert CreationModeType(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["UserDefined", "SystemCreated"],
        ids=["user_defined", "system_created"],
    )
    def test_known_values_round_trip_via_restore_point(self, raw: str) -> None:
        obj = RestorePoint.model_validate({"id": "x", "creationMode": raw})
        assert obj.creation_mode == raw

    def test_unknown_value_passes_through_restore_point(self) -> None:
        obj = RestorePoint.model_validate({"id": "x", "creationMode": "YetAnotherMode"})
        assert obj.creation_mode == "YetAnotherMode"

    def test_str_enum_is_str(self) -> None:
        assert isinstance(CreationModeType.USER_DEFINED, str)
        assert isinstance(CreationModeType.SYSTEM_CREATED, str)


# ---------------------------------------------------------------------------
# SqlResult — binary/base64 handling
# ---------------------------------------------------------------------------


class TestSqlResult:
    """SqlResult model — basic field handling and base64 column convention."""

    def test_default_fields(self) -> None:
        obj = SqlResult()
        assert obj.columns == []
        assert obj.rows == []
        assert obj.rowcount == -1

    def test_columns_and_rows(self) -> None:
        obj = SqlResult(
            columns=["id", "name"],
            rows=[[1, "Alice"], [2, "Bob"]],
            rowcount=2,
        )
        assert obj.columns == ["id", "name"]
        assert len(obj.rows) == 2
        assert obj.rowcount == 2

    def test_base64_column_name_convention(self) -> None:
        """Binary columns are named with __base64 suffix — SqlResult stores them as-is."""
        cols = ["id", "payload__base64"]
        rows: list[list[object]] = [[1, "AQIDBA=="]]
        obj = SqlResult(columns=cols, rows=rows)
        assert "__base64" in obj.columns[1]
        assert obj.rows[0][1] == "AQIDBA=="

    def test_empty_select_rowcount_minus_one(self) -> None:
        obj = SqlResult(columns=["a"], rows=[], rowcount=-1)
        assert obj.rowcount == -1

    def test_ddl_statement_no_columns(self) -> None:
        obj = SqlResult(rowcount=0)
        assert obj.columns == []
        assert obj.rows == []

    def test_model_validate_round_trip(self) -> None:
        data = {"columns": ["x", "y"], "rows": [[1, 2], [3, 4]], "rowcount": 2}
        obj = SqlResult.model_validate(data)
        assert obj.columns == ["x", "y"]
        assert obj.rowcount == 2

    def test_frozen(self) -> None:
        obj = SqlResult()
        with pytest.raises(ValidationError):
            obj.rowcount = 42  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ExecRequestHistory — field parsing (god-model)
# ---------------------------------------------------------------------------


class TestExecRequestHistoryFieldParsing:
    """ExecRequestHistory is a 1-to-1 DMV projection — test required vs optional fields."""

    _MINIMAL: ClassVar[dict] = {
        "row_count": 0,
    }

    _FULL: ClassVar[dict] = {
        "distributed_statement_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "session_id": 42,
        "connection_id": "b2c3d4e5-f6a7-8901-bcde-f01234567891",
        "batch_id": "c3d4e5f6-a7b8-9012-cdef-012345678912",
        "root_batch_id": "d4e5f6a7-b8c9-0123-def0-123456789abc",
        "submit_time": "2024-03-15T10:00:00Z",
        "start_time": "2024-03-15T10:00:01Z",
        "end_time": "2024-03-15T10:00:05Z",
        "total_elapsed_time_ms": 4000,
        "database_name": "SalesDB",
        "is_distributed": 0,
        "statement_type": "SELECT",
        "login_name": "user@example.com",
        "row_count": 100,
        "status": "Succeeded",
        "program_name": "fabric-dw",
        "query_hash": "abc123",
        "label": None,
        "result_cache_hit": 0,
        "sql_pool_name": None,
        "allocated_cpu_time_ms": 250,
        "data_scanned_remote_storage_mb": 1.5,
        "data_scanned_memory_mb": 0.5,
        "data_scanned_disk_mb": 0.0,
        "command": "SELECT * FROM sales",
        "error_code": None,
    }

    def test_minimal_parses(self) -> None:
        obj = ExecRequestHistory.model_validate(self._MINIMAL)
        assert obj.row_count == 0
        assert obj.distributed_statement_id is None
        assert obj.session_id is None

    def test_full_parses(self) -> None:
        obj = ExecRequestHistory.model_validate(self._FULL)
        assert obj.session_id == 42
        assert obj.row_count == 100
        assert obj.database_name == "SalesDB"
        assert obj.statement_type == "SELECT"

    def test_uuid_fields_parsed(self) -> None:
        obj = ExecRequestHistory.model_validate(self._FULL)
        assert isinstance(obj.distributed_statement_id, UUID)
        assert isinstance(obj.connection_id, UUID)

    def test_datetime_fields_parsed(self) -> None:
        obj = ExecRequestHistory.model_validate(self._FULL)
        assert isinstance(obj.start_time, datetime)
        assert obj.start_time.tzinfo is not None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("status", "Running"),
            ("login_name", "admin@corp.com"),
            ("database_name", "MyDB"),
            ("statement_type", "INSERT"),
        ],
        ids=["status", "login_name", "database_name", "statement_type"],
    )
    def test_optional_string_fields(self, field: str, value: str) -> None:
        payload = {**self._MINIMAL, field: value}
        obj = ExecRequestHistory.model_validate(payload)
        assert getattr(obj, field) == value

    def test_extra_fields_ignored(self) -> None:
        payload = {**self._FULL, "future_column": "ignored"}
        obj = ExecRequestHistory.model_validate(payload)
        assert obj.row_count == 100

    def test_frozen(self) -> None:
        obj = ExecRequestHistory.model_validate(self._MINIMAL)
        with pytest.raises(ValidationError):
            obj.row_count = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ExecSessionHistory — field parsing (god-model)
# ---------------------------------------------------------------------------


class TestExecSessionHistoryFieldParsing:
    """ExecSessionHistory is a 1-to-1 DMV projection — test required vs optional fields."""

    _MINIMAL: ClassVar[dict] = {
        "session_id": 1,
        "connection_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "session_start_time": "2024-03-15T10:00:00Z",
        "total_query_elapsed_time_ms": 0,
        "last_request_start_time": "2024-03-15T10:00:00Z",
        "login_name": "user@example.com",
        "status": "running",
        "is_user_process": True,
        "prev_error": 0,
        "group_id": 0,
        "text_size": -1,
        "date_first": 7,
        "quoted_identifier": True,
        "arithabort": True,
        "ansi_null_dflt_on": True,
        "ansi_defaults": False,
        "ansi_warnings": True,
        "ansi_padding": True,
        "ansi_nulls": True,
        "concat_null_yields_null": True,
        "transaction_isolation_level": 2,
        "lock_timeout": -1,
        "deadlock_priority": 0,
        "original_security_id": b"\x01\x02\x03",
    }

    def test_minimal_parses(self) -> None:
        obj = ExecSessionHistory.model_validate(self._MINIMAL)
        assert obj.session_id == 1
        assert obj.login_name == "user@example.com"
        assert obj.status == "running"

    def test_uuid_connection_id_parsed(self) -> None:
        obj = ExecSessionHistory.model_validate(self._MINIMAL)
        assert isinstance(obj.connection_id, UUID)

    def test_datetime_session_start_time_parsed(self) -> None:
        obj = ExecSessionHistory.model_validate(self._MINIMAL)
        assert isinstance(obj.session_start_time, datetime)
        assert obj.session_start_time.tzinfo is not None

    def test_optional_session_end_time_none(self) -> None:
        obj = ExecSessionHistory.model_validate(self._MINIMAL)
        assert obj.session_end_time is None

    def test_optional_database_name_none(self) -> None:
        obj = ExecSessionHistory.model_validate(self._MINIMAL)
        assert obj.database_name is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("program_name", "My App"),
            ("language", "English"),
            ("date_format", "mdy"),
        ],
        ids=["program_name", "language", "date_format"],
    )
    def test_optional_string_fields(self, field: str, value: str) -> None:
        payload = {**self._MINIMAL, field: value}
        obj = ExecSessionHistory.model_validate(payload)
        assert getattr(obj, field) == value

    def test_extra_fields_ignored(self) -> None:
        payload = {**self._MINIMAL, "future_dmv_column": "ignored"}
        obj = ExecSessionHistory.model_validate(payload)
        assert obj.session_id == 1

    def test_frozen(self) -> None:
        obj = ExecSessionHistory.model_validate(self._MINIMAL)
        with pytest.raises(ValidationError):
            obj.status = "completed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ExecSessionHistory — elapsed-time fields are float
# ---------------------------------------------------------------------------


class TestExecSessionHistoryFloatElapsedTime:
    """queryinsights.exec_sessions_history returns elapsed-time columns as floats.

    Pydantic v2 raises ``int_from_float`` for fractional values on ``int`` fields.
    ``total_query_elapsed_time_ms`` must be typed ``float``.
    """

    _MINIMAL: ClassVar[dict] = {
        "session_id": 1,
        "connection_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "session_start_time": "2024-03-15T10:00:00Z",
        "total_query_elapsed_time_ms": 0,
        "last_request_start_time": "2024-03-15T10:00:00Z",
        "login_name": "user@example.com",
        "status": "running",
        "is_user_process": True,
        "prev_error": 0,
        "group_id": 0,
        "text_size": -1,
        "date_first": 7,
        "quoted_identifier": True,
        "arithabort": True,
        "ansi_null_dflt_on": True,
        "ansi_defaults": False,
        "ansi_warnings": True,
        "ansi_padding": True,
        "ansi_nulls": True,
        "concat_null_yields_null": True,
        "transaction_isolation_level": 2,
        "lock_timeout": -1,
        "deadlock_priority": 0,
        "original_security_id": b"\x01\x02\x03",
    }

    def test_total_query_elapsed_time_ms_accepts_float(self) -> None:
        """Fabric returns total_query_elapsed_time_ms as float — must not raise int_from_float."""
        payload = {**self._MINIMAL, "total_query_elapsed_time_ms": 1234.5}
        obj = ExecSessionHistory.model_validate(payload)
        assert obj.total_query_elapsed_time_ms == 1234.5
        assert isinstance(obj.total_query_elapsed_time_ms, float)


# ---------------------------------------------------------------------------
# FrequentlyRunQuery — elapsed-time fields are float
# ---------------------------------------------------------------------------


class TestFrequentlyRunQueryFloatElapsedTime:
    """queryinsights.frequently_run_queries returns elapsed-time columns as floats.

    Pydantic v2 raises ``int_from_float`` for fractional values on ``int`` fields.
    All ``*_elapsed_time_ms`` fields must be typed ``float``.
    """

    _MINIMAL: ClassVar[dict] = {
        "number_of_runs": 5,
        "avg_total_elapsed_time_ms": 200,
        "last_run_total_elapsed_time_ms": 180,
        "min_run_total_elapsed_time_ms": 100,
        "max_run_total_elapsed_time_ms": 300,
        "number_of_successful_runs": 4,
        "number_of_failed_runs": 1,
        "number_of_canceled_runs": 0,
    }

    @pytest.mark.parametrize(
        "field",
        [
            "avg_total_elapsed_time_ms",
            "last_run_total_elapsed_time_ms",
            "min_run_total_elapsed_time_ms",
            "max_run_total_elapsed_time_ms",
        ],
    )
    def test_elapsed_time_field_accepts_fractional_float(self, field: str) -> None:
        """Fractional ms values must validate without int_from_float error."""
        payload = {**self._MINIMAL, field: 1234.5}
        obj = FrequentlyRunQuery.model_validate(payload)
        assert getattr(obj, field) == 1234.5
        assert isinstance(getattr(obj, field), float)

    def test_number_of_runs_remains_int(self) -> None:
        """Count fields must stay int — not changed by this fix."""
        obj = FrequentlyRunQuery.model_validate(self._MINIMAL)
        assert isinstance(obj.number_of_runs, int)
        assert isinstance(obj.number_of_successful_runs, int)
        assert isinstance(obj.number_of_failed_runs, int)
        assert isinstance(obj.number_of_canceled_runs, int)

    @pytest.mark.parametrize(
        "count_field",
        [
            "number_of_runs",
            "number_of_successful_runs",
            "number_of_failed_runs",
            "number_of_canceled_runs",
        ],
    )
    def test_count_field_rejects_fractional_float(self, count_field: str) -> None:
        """Count fields must be int — fractional input must raise int_from_float."""
        with pytest.raises(ValidationError, match="int_from_float"):
            FrequentlyRunQuery.model_validate({**self._MINIMAL, count_field: 5.5})


# ---------------------------------------------------------------------------
# LongRunningQuery — elapsed-time fields are float
# ---------------------------------------------------------------------------


class TestLongRunningQueryFloatElapsedTime:
    """queryinsights.long_running_queries returns elapsed-time columns as floats.

    ``median_total_elapsed_time_ms`` is a statistical median and is virtually
    always fractional.  ``last_run_total_elapsed_time_ms`` is also returned as
    float by Fabric.  Both must be typed ``float``; ``number_of_runs`` is a
    genuine integer count and must not change.
    """

    _MINIMAL: ClassVar[dict] = {
        "median_total_elapsed_time_ms": 5000,
        "number_of_runs": 3,
        "last_run_total_elapsed_time_ms": 6000,
    }

    def test_median_elapsed_time_accepts_fractional_float(self) -> None:
        """Fractional median_total_elapsed_time_ms must not raise int_from_float."""
        payload = {**self._MINIMAL, "median_total_elapsed_time_ms": 1234.5}
        obj = LongRunningQuery.model_validate(payload)
        assert obj.median_total_elapsed_time_ms == 1234.5
        assert isinstance(obj.median_total_elapsed_time_ms, float)

    def test_last_run_elapsed_time_accepts_fractional_float(self) -> None:
        """Fractional last_run_total_elapsed_time_ms must not raise int_from_float."""
        payload = {**self._MINIMAL, "last_run_total_elapsed_time_ms": 5999.7}
        obj = LongRunningQuery.model_validate(payload)
        assert obj.last_run_total_elapsed_time_ms == 5999.7
        assert isinstance(obj.last_run_total_elapsed_time_ms, float)

    def test_number_of_runs_remains_int(self) -> None:
        """number_of_runs is a count — must stay int."""
        obj = LongRunningQuery.model_validate(self._MINIMAL)
        assert isinstance(obj.number_of_runs, int)

    def test_number_of_runs_rejects_fractional_float(self) -> None:
        """number_of_runs is a count — fractional input must raise int_from_float."""
        with pytest.raises(ValidationError, match="int_from_float"):
            LongRunningQuery.model_validate({**self._MINIMAL, "number_of_runs": 5.5})


# ---------------------------------------------------------------------------
# ExecRequestHistory — elapsed-time / cpu-time fields are float
# ---------------------------------------------------------------------------


class TestExecRequestHistoryFloatElapsedTime:
    """queryinsights.exec_requests_history returns elapsed/cpu time as floats."""

    _MINIMAL: ClassVar[dict] = {
        "row_count": 0,
    }

    def test_total_elapsed_time_ms_accepts_fractional_float(self) -> None:
        """Fractional total_elapsed_time_ms must not raise int_from_float."""
        payload = {**self._MINIMAL, "total_elapsed_time_ms": 4321.9}
        obj = ExecRequestHistory.model_validate(payload)
        assert obj.total_elapsed_time_ms == 4321.9
        assert isinstance(obj.total_elapsed_time_ms, float)

    def test_allocated_cpu_time_ms_accepts_fractional_float(self) -> None:
        """Fractional allocated_cpu_time_ms must not raise int_from_float."""
        payload = {**self._MINIMAL, "allocated_cpu_time_ms": 99.5}
        obj = ExecRequestHistory.model_validate(payload)
        assert obj.allocated_cpu_time_ms == 99.5
        assert isinstance(obj.allocated_cpu_time_ms, float)
