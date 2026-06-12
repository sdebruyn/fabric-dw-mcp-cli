"""Tests for fabric_dw.models — Pydantic v2 round-trip, alias handling, and frozen behaviour."""

import json
from copy import deepcopy
from typing import ClassVar

import pytest
from pydantic import ValidationError

from fabric_dw.models import (
    AuditSettings,
    CreationModeType,
    ItemAccess,
    ItemAccessPrincipal,
    PrincipalType,
    RestorePoint,
    RunningQuery,
    SqlPoolClassifier,
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
