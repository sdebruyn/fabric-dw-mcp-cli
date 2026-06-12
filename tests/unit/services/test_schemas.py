"""Unit tests for services.schemas — DMV-mock tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, NotFoundError, PermissionDeniedError
from fabric_dw.models import Schema, WarehouseKind
from fabric_dw.services import schemas
from fabric_dw.services.schemas import validate_identifier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_target() -> MagicMock:
    return MagicMock()


def _make_conn(rows: list[tuple[object, ...]], columns: list[str]) -> MagicMock:
    cursor = MagicMock()
    cursor.description = [(c, None) for c in columns]
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def _make_conn_for_ddl() -> MagicMock:
    cursor = MagicMock()
    cursor.description = None
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_LIST_COLS = ["name", "principal_id"]
_SCHEMA_ROW_1: tuple[object, ...] = ("dbo", 1)
_SCHEMA_ROW_2: tuple[object, ...] = ("sales", 5)
_SCHEMA_ROW_SYS: tuple[object, ...] = ("sys", 4)

# Object rows for cascade tests: (obj_name, obj_type)
_OBJ_COLS = ["obj_name", "obj_type"]


# ===========================================================================
# validate_identifier — re-exported from views
# ===========================================================================


class TestValidateIdentifierReexport:
    def test_valid_identifier_passes(self) -> None:
        assert validate_identifier("my_schema") == "my_schema"

    def test_rejects_injection(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("s]; DROP TABLE users--")


# ===========================================================================
# list_schemas
# ===========================================================================


class TestListSchemas:
    async def test_returns_empty_when_no_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await schemas.list_schemas(target)
        assert result == []

    async def test_returns_schema_instances(self) -> None:
        target = _make_target()
        conn = _make_conn([_SCHEMA_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await schemas.list_schemas(target)
        assert len(result) == 1
        assert isinstance(result[0], Schema)

    async def test_parses_fields_correctly(self) -> None:
        target = _make_target()
        conn = _make_conn([_SCHEMA_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await schemas.list_schemas(target)
        s = result[0]
        assert s.name == "dbo"
        assert s.principal_id == 1

    async def test_returns_all_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([_SCHEMA_ROW_1, _SCHEMA_ROW_2], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await schemas.list_schemas(target)
        assert len(result) == 2

    async def test_sql_references_sys_schemas(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await schemas.list_schemas(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.schemas" in call_sql

    async def test_sql_excludes_sys_schema(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await schemas.list_schemas(target)
        cursor = conn.cursor.return_value
        call_args = cursor.execute.call_args
        call_sql: str = call_args[0][0]
        # The NOT IN clause uses ? placeholders; 'sys' is in the params.
        assert "NOT IN" in call_sql
        params = call_args[0][1] if len(call_args[0]) > 1 else []
        assert "sys" in list(params)

    async def test_sql_excludes_information_schema(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await schemas.list_schemas(target)
        cursor = conn.cursor.return_value
        call_args = cursor.execute.call_args
        params = call_args[0][1] if len(call_args[0]) > 1 else []
        assert "INFORMATION_SCHEMA" in list(params)

    async def test_sql_excludes_db_prefix_via_like(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await schemas.list_schemas(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "db[_]%" in call_sql

    async def test_sql_excludes_guest_schema(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await schemas.list_schemas(target)
        cursor = conn.cursor.return_value
        call_args = cursor.execute.call_args
        params = call_args[0][1] if len(call_args[0]) > 1 else []
        # 'guest' must appear in the NOT IN params
        assert "guest" in list(params)

    async def test_closes_connection(self) -> None:
        target = _make_target()
        conn = _make_conn([_SCHEMA_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await schemas.list_schemas(target)
        conn.close.assert_called_once()

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object sys.schemas")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await schemas.list_schemas(target)

    async def test_maps_auth_error(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("Authentication failed for user ''")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(AuthError),
        ):
            await schemas.list_schemas(target)

    async def test_unrelated_error_propagates(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("network timeout")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(RuntimeError, match="network timeout"),
        ):
            await schemas.list_schemas(target)


# ===========================================================================
# create_schema
# ===========================================================================


class TestCreateSchema:
    async def test_emits_create_schema(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_SCHEMA_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await schemas.create_schema(target, "dbo")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "CREATE SCHEMA" in call_sql

    async def test_uses_bracket_quoting(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_SCHEMA_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await schemas.create_schema(target, "dbo")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql

    async def test_returns_schema_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_SCHEMA_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await schemas.create_schema(target, "dbo")
        assert isinstance(result, Schema)

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_SCHEMA_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await schemas.create_schema(target, "dbo")
        ddl_conn.commit.assert_called_once()

    async def test_validates_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await schemas.create_schema(target, "bad]schema")

    async def test_rejects_injection_in_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await schemas.create_schema(target, "x]; DROP TABLE users--")

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on database")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await schemas.create_schema(target, "dbo")

    async def test_fetch_raises_not_found_when_no_rows(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([], _LIST_COLS)
        with (
            patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]),
            pytest.raises(NotFoundError),
        ):
            await schemas.create_schema(target, "dbo")


# ===========================================================================
# delete_schema — no cascade
# ===========================================================================


class TestDeleteSchema:
    async def test_emits_drop_schema(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await schemas.delete_schema(target, "dbo")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP SCHEMA" in call_sql

    async def test_uses_bracket_quoting(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await schemas.delete_schema(target, "dbo")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await schemas.delete_schema(target, "dbo")
        conn.commit.assert_called_once()

    async def test_closes_connection(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await schemas.delete_schema(target, "dbo")
        conn.close.assert_called_once()

    async def test_validates_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await schemas.delete_schema(target, "bad]schema")

    async def test_rejects_injection_in_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await schemas.delete_schema(target, "x]; DROP TABLE users--")

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to drop schema")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await schemas.delete_schema(target, "dbo")

    async def test_unrelated_error_propagates(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("connection reset")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(RuntimeError, match="connection reset"),
        ):
            await schemas.delete_schema(target, "dbo")


# ===========================================================================
# delete_schema — cascade (Warehouse: all types)
# ===========================================================================


class TestDeleteSchemaCascade:
    async def test_cascade_drops_table_then_schema(self) -> None:
        """cascade=True on WAREHOUSE: list → run_statements (DROP TABLE) → DROP SCHEMA."""
        target = _make_target()
        list_conn = _make_conn([("orders", "U")], _OBJ_COLS)
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ):
            await schemas.delete_schema(target, "sales", cascade=True)
        cursor = drop_objects_conn.cursor.return_value
        drop_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP TABLE" in drop_sql

    async def test_cascade_drops_view_then_schema(self) -> None:
        """cascade=True on WAREHOUSE: list → run_statements (DROP VIEW) → DROP SCHEMA."""
        target = _make_target()
        list_conn = _make_conn([("vw_orders", "V")], _OBJ_COLS)
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ):
            await schemas.delete_schema(target, "sales", cascade=True)
        cursor = drop_objects_conn.cursor.return_value
        drop_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP VIEW" in drop_sql

    async def test_cascade_drops_procedure_then_schema(self) -> None:
        """cascade=True on WAREHOUSE: DROP PROCEDURE for stored procs (type P)."""
        target = _make_target()
        list_conn = _make_conn([("usp_load", "P")], _OBJ_COLS)
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ):
            await schemas.delete_schema(target, "sales", cascade=True)
        cursor = drop_objects_conn.cursor.return_value
        drop_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP PROCEDURE" in drop_sql

    async def test_cascade_drops_function_then_schema(self) -> None:
        """cascade=True on WAREHOUSE: DROP FUNCTION for scalar functions (type FN)."""
        target = _make_target()
        list_conn = _make_conn([("fn_calc", "FN")], _OBJ_COLS)
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ):
            await schemas.delete_schema(target, "sales", cascade=True)
        cursor = drop_objects_conn.cursor.return_value
        drop_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP FUNCTION" in drop_sql

    async def test_cascade_drops_multiple_objects(self) -> None:
        """Multiple objects (table + view + proc + function) all dropped on ONE connection.

        Connection count:
        - 1: list objects (run_query)
        - 2: ALL DROP statements (run_statements — single connection)
        - 3: DROP SCHEMA (run_query)
        Total: 3.
        """
        target = _make_target()
        list_conn = _make_conn(
            [("t1", "U"), ("v1", "V"), ("usp_p", "P"), ("fn_f", "FN")], _OBJ_COLS
        )
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ) as mock_open:
            await schemas.delete_schema(target, "sales", cascade=True)
        # 3 connections total: list + all-object-drops + schema-drop
        assert mock_open.call_count == 3
        cursor = drop_objects_conn.cursor.return_value
        assert cursor.execute.call_count == 4
        sqls = [str(c[0][0]).upper() for c in cursor.execute.call_args_list]
        assert any("DROP TABLE" in s and "[T1]" in s for s in sqls)
        assert any("DROP VIEW" in s and "[V1]" in s for s in sqls)
        assert any("DROP PROCEDURE" in s and "[USP_P]" in s for s in sqls)
        assert any("DROP FUNCTION" in s and "[FN_F]" in s for s in sqls)

    async def test_cascade_false_does_not_enumerate_objects(self) -> None:
        """cascade=False must not enumerate or drop any objects."""
        target = _make_target()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[drop_schema_conn],
        ) as mock_open:
            await schemas.delete_schema(target, "sales", cascade=False)
        # Only one connection opened (the DROP SCHEMA itself)
        assert mock_open.call_count == 1

    async def test_cascade_empty_schema_drops_schema(self) -> None:
        """When schema has no objects, skip run_statements and only DROP SCHEMA."""
        target = _make_target()
        list_conn = _make_conn([], _OBJ_COLS)
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_schema_conn],
        ):
            await schemas.delete_schema(target, "empty_schema", cascade=True)
        cursor = drop_schema_conn.cursor.return_value
        drop_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP SCHEMA" in drop_sql

    async def test_cascade_uses_bracket_quoting_for_objects(self) -> None:
        """Object DROP statements must use bracket-quoted names."""
        target = _make_target()
        list_conn = _make_conn([("my_table", "U")], _OBJ_COLS)
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ):
            await schemas.delete_schema(target, "sales", cascade=True)
        cursor = drop_objects_conn.cursor.return_value
        drop_sql: str = cursor.execute.call_args[0][0]
        assert "[sales]" in drop_sql
        assert "[my_table]" in drop_sql

    async def test_cascade_default_kind_is_warehouse(self) -> None:
        """Default kind=WAREHOUSE means cascade=True drops tables without passing kind."""
        target = _make_target()
        list_conn = _make_conn([("t1", "U")], _OBJ_COLS)
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ):
            # kind defaults to WAREHOUSE — must drop the table
            await schemas.delete_schema(target, "sales", cascade=True)
        cursor = drop_objects_conn.cursor.return_value
        drop_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP TABLE" in drop_sql


# ===========================================================================
# delete_schema — cascade on SQL Analytics Endpoint (tables excluded)
# ===========================================================================


class TestDeleteSchemaCascadeEndpoint:
    async def test_cascade_endpoint_drops_view_not_table(self) -> None:
        """cascade=True on SQL_ENDPOINT: views ARE dropped, tables are NOT.

        Mock returns a mix of a table and a view.  Only DROP VIEW must be issued.
        """
        target = _make_target()
        # Schema contains one table and one view
        list_conn = _make_conn([("orders", "U"), ("vw_orders", "V")], _OBJ_COLS)
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ):
            # Must not raise — cascade on endpoint is allowed
            await schemas.delete_schema(
                target, "sales", cascade=True, kind=WarehouseKind.SQL_ENDPOINT
            )
        cursor = drop_objects_conn.cursor.return_value
        sqls = [str(c[0][0]).upper() for c in cursor.execute.call_args_list]
        # VIEW must be dropped
        assert any("DROP VIEW" in s for s in sqls)
        # TABLE must NOT be dropped
        assert not any("DROP TABLE" in s for s in sqls)

    async def test_cascade_endpoint_drops_procedure(self) -> None:
        """cascade=True on SQL_ENDPOINT: stored procedures ARE dropped."""
        target = _make_target()
        list_conn = _make_conn([("usp_load", "P")], _OBJ_COLS)
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ):
            await schemas.delete_schema(
                target, "sales", cascade=True, kind=WarehouseKind.SQL_ENDPOINT
            )
        cursor = drop_objects_conn.cursor.return_value
        drop_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP PROCEDURE" in drop_sql

    async def test_cascade_endpoint_drops_function(self) -> None:
        """cascade=True on SQL_ENDPOINT: functions (FN/IF/TF) ARE dropped."""
        target = _make_target()
        list_conn = _make_conn([("fn_calc", "FN")], _OBJ_COLS)
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ):
            await schemas.delete_schema(
                target, "sales", cascade=True, kind=WarehouseKind.SQL_ENDPOINT
            )
        cursor = drop_objects_conn.cursor.return_value
        drop_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP FUNCTION" in drop_sql

    async def test_cascade_endpoint_only_tables_skips_object_drop_connection(self) -> None:
        """If schema only has tables on an endpoint, no object-drop connection is opened.

        All tables are excluded → ddl_statements is empty → run_statements is not called.
        Connection count: list (1) + DROP SCHEMA (2) = 2 total.
        """
        target = _make_target()
        list_conn = _make_conn([("orders", "U"), ("customers", "U")], _OBJ_COLS)
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_schema_conn],
        ) as mock_open:
            await schemas.delete_schema(
                target, "sales", cascade=True, kind=WarehouseKind.SQL_ENDPOINT
            )
        # Only 2 connections: list objects + DROP SCHEMA (no object-drop connection)
        assert mock_open.call_count == 2

    async def test_cascade_endpoint_mixed_objects_drops_only_non_tables(self) -> None:
        """Mixed schema on endpoint: only non-table objects are dropped."""
        target = _make_target()
        list_conn = _make_conn(
            [("t1", "U"), ("vw1", "V"), ("usp1", "P"), ("fn1", "FN")],
            _OBJ_COLS,
        )
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ):
            await schemas.delete_schema(
                target, "sales", cascade=True, kind=WarehouseKind.SQL_ENDPOINT
            )
        cursor = drop_objects_conn.cursor.return_value
        # 3 DROP statements: VIEW + PROCEDURE + FUNCTION (not TABLE)
        assert cursor.execute.call_count == 3
        sqls = [str(c[0][0]).upper() for c in cursor.execute.call_args_list]
        assert not any("DROP TABLE" in s for s in sqls)
        assert any("DROP VIEW" in s for s in sqls)
        assert any("DROP PROCEDURE" in s for s in sqls)
        assert any("DROP FUNCTION" in s for s in sqls)

    async def test_cascade_true_on_sql_endpoint_does_not_raise(self) -> None:
        """cascade=True on a SQL_ENDPOINT must NOT raise — the blanket guard is gone."""
        target = _make_target()
        list_conn = _make_conn([], _OBJ_COLS)
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_schema_conn],
        ):
            # Must succeed without any exception
            await schemas.delete_schema(
                target, "empty_schema", cascade=True, kind=WarehouseKind.SQL_ENDPOINT
            )

    async def test_cascade_false_on_sql_endpoint_is_allowed(self) -> None:
        """DROP SCHEMA without cascade must succeed on a SQL Analytics Endpoint."""
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            # Must not raise
            await schemas.delete_schema(
                target, "sales", cascade=False, kind=WarehouseKind.SQL_ENDPOINT
            )
        cursor = conn.cursor.return_value
        drop_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP SCHEMA" in drop_sql

    async def test_cascade_true_on_warehouse_drops_all_types(self) -> None:
        """cascade=True on a Warehouse must drop ALL object types including tables."""
        target = _make_target()
        list_conn = _make_conn(
            [("t1", "U"), ("vw1", "V"), ("usp1", "P"), ("fn1", "FN")],
            _OBJ_COLS,
        )
        drop_objects_conn = _make_conn_for_ddl()
        drop_schema_conn = _make_conn_for_ddl()
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[list_conn, drop_objects_conn, drop_schema_conn],
        ):
            await schemas.delete_schema(target, "sales", cascade=True, kind=WarehouseKind.WAREHOUSE)
        cursor = drop_objects_conn.cursor.return_value
        sqls = [str(c[0][0]).upper() for c in cursor.execute.call_args_list]
        assert any("DROP TABLE" in s for s in sqls)
        assert any("DROP VIEW" in s for s in sqls)
        assert any("DROP PROCEDURE" in s for s in sqls)
        assert any("DROP FUNCTION" in s for s in sqls)
