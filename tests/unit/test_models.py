"""Tests for fabric_dw.models — Pydantic v2 round-trip, alias handling, and frozen behaviour."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from fabric_dw.models import (
    AuditSettings,
    RestorePoint,
    RunningQuery,
    Warehouse,
    WarehouseKind,
    WarehouseSnapshot,
    Workspace,
)
from tests.fixtures.api_payloads import (
    AUDIT_SETTINGS_PAYLOAD,
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
        obj = RestorePoint.model_validate(payload)
        dumped = obj.model_dump(by_alias=True, mode="json", exclude_none=True)
        assert dumped == payload

    def test_extra_fields_ignored(self) -> None:
        payload = json.loads(RESTORE_POINT_PAYLOAD)
        payload_copy = dict(payload)
        payload_copy["unknownProp"] = 42
        obj = RestorePoint.model_validate(payload_copy)
        assert obj.name == payload["name"]

    def test_frozen(self) -> None:
        payload = json.loads(RESTORE_POINT_PAYLOAD)
        obj = RestorePoint.model_validate(payload)
        with pytest.raises(ValidationError):
            obj.name = "changed"


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
