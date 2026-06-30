"""Unit tests for T-SQL permission functions in fabric_dw.services.permissions."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from fabric_dw.models import DatabasePermission, DatabasePrincipal
from fabric_dw.services import permissions as perms_svc
from fabric_dw.sql import SqlTarget

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TARGET = SqlTarget(
    workspace_id="ws-1",
    database="SalesDW",
    connection_string="server.datawarehouse.fabric.microsoft.com",
)

_DB_COLS = [
    "principal_name",
    "principal_type",
    "state",
    "permission_name",
    "securable_class",
    "major_id",
    "minor_id",
    "column_name",
]
_PRINCIPAL_COLS = ["name", "type_desc", "authentication_type_desc"]
_OBJECT_COLS = ["major_id", "schema_name", "object_name"]
_SCHEMA_COLS = ["schema_id", "schema_name"]


# ---------------------------------------------------------------------------
# validate_permissions helper (private, tested via grant_permission)
# ---------------------------------------------------------------------------


class TestValidatePermissions:
    def test_valid_object_permissions_accepted(self) -> None:
        from fabric_dw.services.permissions import _validate_permissions  # noqa: PLC0415

        result = _validate_permissions("SELECT,INSERT", "OBJECT")
        assert "SELECT" in result
        assert "INSERT" in result

    def test_invalid_permission_raises(self) -> None:
        from fabric_dw.services.permissions import _validate_permissions  # noqa: PLC0415

        with pytest.raises(ValueError, match="Invalid permission"):
            _validate_permissions("SELECTX", "OBJECT")

    def test_unknown_scope_raises(self) -> None:
        from fabric_dw.services.permissions import _validate_permissions  # noqa: PLC0415

        with pytest.raises(ValueError, match="Unknown scope class"):
            _validate_permissions("SELECT", "TABLE")

    def test_empty_permissions_raises(self) -> None:
        from fabric_dw.services.permissions import _validate_permissions  # noqa: PLC0415

        with pytest.raises(ValueError, match="At least one permission"):
            _validate_permissions("", "DATABASE")

    def test_multiword_permission_accepted(self) -> None:
        from fabric_dw.services.permissions import _validate_permissions  # noqa: PLC0415

        result = _validate_permissions("VIEW DEFINITION", "SCHEMA")
        assert "VIEW DEFINITION" in result

    def test_create_table_accepted_for_database(self) -> None:
        from fabric_dw.services.permissions import _validate_permissions  # noqa: PLC0415

        result = _validate_permissions("CREATE TABLE", "DATABASE")
        assert "CREATE TABLE" in result

    def test_create_table_rejected_for_schema(self) -> None:
        from fabric_dw.services.permissions import _validate_permissions  # noqa: PLC0415

        with pytest.raises(ValueError, match="Invalid permission"):
            _validate_permissions("CREATE TABLE", "SCHEMA")


# ---------------------------------------------------------------------------
# build_scope_clause helper
# ---------------------------------------------------------------------------


class TestBuildScopeClause:
    def test_database_scope(self) -> None:
        from fabric_dw.services.permissions import _build_scope_clause  # noqa: PLC0415

        # DATABASE is the implicit scope; the ON clause is omitted entirely.
        result = _build_scope_clause("DATABASE")
        assert result == ""

    def test_schema_scope(self) -> None:
        from fabric_dw.services.permissions import _build_scope_clause  # noqa: PLC0415

        result = _build_scope_clause("SCHEMA", schema="dbo")
        assert result == "ON SCHEMA::[dbo]"

    def test_object_scope(self) -> None:
        from fabric_dw.services.permissions import _build_scope_clause  # noqa: PLC0415

        result = _build_scope_clause("OBJECT", object_name="dbo.sales")
        assert result == "ON OBJECT::[dbo].[sales]"

    def test_schema_scope_missing_name_raises(self) -> None:
        from fabric_dw.services.permissions import _build_scope_clause  # noqa: PLC0415

        with pytest.raises(ValueError, match="--schema NAME"):
            _build_scope_clause("SCHEMA")

    def test_object_scope_missing_name_raises(self) -> None:
        from fabric_dw.services.permissions import _build_scope_clause  # noqa: PLC0415

        with pytest.raises(ValueError, match=r"--object SCHEMA\.NAME"):
            _build_scope_clause("OBJECT")

    def test_invalid_identifier_raises(self) -> None:
        from fabric_dw.services.permissions import _build_scope_clause  # noqa: PLC0415

        with pytest.raises(ValueError, match="Invalid"):
            _build_scope_clause("SCHEMA", schema="dbo; DROP")


# ---------------------------------------------------------------------------
# grant_permission statement building
# ---------------------------------------------------------------------------


class TestGrantPermission:
    async def test_grant_select_on_database_builds_correct_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.grant_permission(_TARGET, "SELECT", "user@contoso.com", "DATABASE")

        assert len(captured) == 1
        stmt = captured[0]
        # DATABASE is the implicit scope; no ON clause is emitted.
        assert stmt == "GRANT SELECT TO [user@contoso.com];"

    async def test_grant_select_insert_on_object(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.grant_permission(
                _TARGET,
                "SELECT,INSERT",
                "user@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
            )

        stmt = captured[0]
        assert "GRANT" in stmt
        assert "SELECT" in stmt
        assert "INSERT" in stmt
        assert "ON OBJECT::[dbo].[sales]" in stmt
        assert "TO [user@contoso.com]" in stmt
        assert stmt.endswith(";")

    async def test_grant_with_grant_option(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.grant_permission(
                _TARGET,
                "SELECT",
                "user@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                with_grant_option=True,
            )

        assert "WITH GRANT OPTION" in captured[0]

    async def test_grant_invalid_permission_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid permission"):
            await perms_svc.grant_permission(_TARGET, "SELECTX", "user@contoso.com", "DATABASE")

    async def test_grant_invalid_principal_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid principal"):
            await perms_svc.grant_permission(_TARGET, "SELECT", "a];DROP TABLE dbo.t--", "DATABASE")

    async def test_grant_on_schema_scope(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.grant_permission(_TARGET, "EXECUTE", "analysts", "SCHEMA", schema="dbo")

        stmt = captured[0]
        assert stmt == "GRANT EXECUTE ON SCHEMA::[dbo] TO [analysts];"

    async def test_grant_select_on_object_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.grant_permission(
                _TARGET, "SELECT", "alice@contoso.com", "OBJECT", object_name="dbo.sales"
            )

        stmt = captured[0]
        assert stmt == "GRANT SELECT ON OBJECT::[dbo].[sales] TO [alice@contoso.com];"

    async def test_grant_with_grant_option_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.grant_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "SCHEMA",
                schema="dbo",
                with_grant_option=True,
            )

        stmt = captured[0]
        assert stmt == "GRANT SELECT ON SCHEMA::[dbo] TO [alice@contoso.com] WITH GRANT OPTION;"


# ---------------------------------------------------------------------------
# deny_permission statement building
# ---------------------------------------------------------------------------


class TestDenyPermission:
    async def test_deny_execute_on_schema_builds_correct_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.deny_permission(_TARGET, "EXECUTE", "analysts", "SCHEMA", schema="dbo")

        stmt = captured[0]
        assert stmt == "DENY EXECUTE ON SCHEMA::[dbo] TO [analysts];"

    async def test_deny_select_on_database_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.deny_permission(_TARGET, "SELECT", "user@contoso.com", "DATABASE")

        stmt = captured[0]
        # DATABASE is the implicit scope; no ON clause is emitted.
        assert stmt == "DENY SELECT TO [user@contoso.com];"

    async def test_deny_select_on_object_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.deny_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
            )

        stmt = captured[0]
        assert stmt == "DENY SELECT ON OBJECT::[dbo].[sales] TO [alice@contoso.com];"

    async def test_deny_invalid_permission_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid permission"):
            await perms_svc.deny_permission(_TARGET, "SELECTX", "user@contoso.com", "DATABASE")


# ---------------------------------------------------------------------------
# revoke_permission statement building
# ---------------------------------------------------------------------------


class TestRevokePermission:
    async def test_revoke_basic(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(
                _TARGET,
                "UPDATE",
                "user@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
            )

        stmt = captured[0]
        assert stmt.startswith("REVOKE UPDATE")
        assert "FROM [user@contoso.com]" in stmt
        assert "ON OBJECT::[dbo].[sales]" in stmt
        assert stmt.endswith(";")

    async def test_revoke_grant_option_only(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(
                _TARGET,
                "UPDATE",
                "user@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                grant_option_only=True,
            )

        assert "GRANT OPTION FOR UPDATE" in captured[0]

    async def test_revoke_cascade(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(
                _TARGET,
                "UPDATE",
                "user@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                cascade=True,
            )

        assert "CASCADE" in captured[0]

    async def test_revoke_grant_option_for_with_cascade(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(
                _TARGET,
                "UPDATE",
                "user@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                grant_option_only=True,
                cascade=True,
            )

        stmt = captured[0]
        assert "REVOKE GRANT OPTION FOR UPDATE" in stmt
        assert "CASCADE" in stmt


# ---------------------------------------------------------------------------
# list_sql_permissions (reads from sys.database_permissions)
# ---------------------------------------------------------------------------


class TestListSqlPermissions:
    async def test_returns_database_permission_objects(self) -> None:
        perm_rows = [
            ("alice@contoso.com", "EXTERNAL_USER", "GRANT", "SELECT", "DATABASE", 0, 0, None),
        ]
        obj_rows: list = []
        schema_rows: list = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            if "sys.database_permissions" in sql and "sys.database_principals" in sql:
                return _DB_COLS, perm_rows
            if "OBJECT_SCHEMA_NAME" in sql:
                return _OBJECT_COLS, obj_rows
            if "sys.schemas" in sql:
                return _SCHEMA_COLS, schema_rows
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            result = await perms_svc.list_sql_permissions(_TARGET)

        assert len(result) == 1
        perm = result[0]
        assert isinstance(perm, DatabasePermission)
        assert perm.principal_name == "alice@contoso.com"
        assert perm.securable_class == "DATABASE"
        assert perm.state == "GRANT"
        assert perm.permission_name == "SELECT"

    async def test_schema_class_resolved_to_schema_name(self) -> None:
        perm_rows = [
            ("alice@contoso.com", "EXTERNAL_USER", "GRANT", "SELECT", "SCHEMA", 5, 0, None),
        ]
        obj_rows: list = []
        schema_rows = [(5, "dbo")]

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            if "sys.database_permissions" in sql and "sys.database_principals" in sql:
                return _DB_COLS, perm_rows
            if "OBJECT_SCHEMA_NAME" in sql:
                return _OBJECT_COLS, obj_rows
            if "sys.schemas" in sql:
                return _SCHEMA_COLS, schema_rows
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            result = await perms_svc.list_sql_permissions(_TARGET)

        assert len(result) == 1
        assert result[0].securable_class == "SCHEMA"
        assert result[0].schema_name == "dbo"

    async def test_object_class_resolved_to_object_names(self) -> None:
        perm_rows = [
            (
                "alice@contoso.com",
                "EXTERNAL_USER",
                "GRANT",
                "SELECT",
                "OBJECT_OR_COLUMN",
                10,
                0,
                None,
            ),
        ]
        obj_rows = [(10, "dbo", "sales")]
        schema_rows: list = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            if "sys.database_permissions" in sql and "sys.database_principals" in sql:
                return _DB_COLS, perm_rows
            if "OBJECT_SCHEMA_NAME" in sql:
                return _OBJECT_COLS, obj_rows
            if "sys.schemas" in sql:
                return _SCHEMA_COLS, schema_rows
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            result = await perms_svc.list_sql_permissions(_TARGET)

        assert len(result) == 1
        assert result[0].securable_class == "OBJECT"
        assert result[0].schema_name == "dbo"
        assert result[0].object_name == "sales"

    async def test_filter_by_principal(self) -> None:
        perm_rows = [
            ("alice@contoso.com", "EXTERNAL_USER", "GRANT", "SELECT", "DATABASE", 0, 0, None),
            ("bob@contoso.com", "EXTERNAL_USER", "GRANT", "INSERT", "DATABASE", 0, 0, None),
        ]

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            if "sys.database_permissions" in sql and "sys.database_principals" in sql:
                return _DB_COLS, perm_rows
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            result = await perms_svc.list_sql_permissions(_TARGET, principal="alice@contoso.com")

        assert len(result) == 1
        assert result[0].principal_name == "alice@contoso.com"


# ---------------------------------------------------------------------------
# list_database_principals
# ---------------------------------------------------------------------------


class TestListDatabasePrincipals:
    async def test_returns_database_principal_objects(self) -> None:
        principal_rows = [
            ("alice@contoso.com", "EXTERNAL_USER", "EXTERNAL"),
            ("db_owner", "DATABASE_ROLE", "NONE"),
        ]

        def _mock_run_query(_target: object, _sql: str, **_kwargs: object) -> tuple:
            return _PRINCIPAL_COLS, principal_rows

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            result = await perms_svc.list_database_principals(_TARGET)

        assert len(result) == 2
        assert all(isinstance(p, DatabasePrincipal) for p in result)

    async def test_filter_user_type(self) -> None:
        principal_rows = [
            ("alice@contoso.com", "EXTERNAL_USER", "EXTERNAL"),
            ("db_owner", "DATABASE_ROLE", "NONE"),
        ]

        def _mock_run_query(_target: object, _sql: str, **_kwargs: object) -> tuple:
            return _PRINCIPAL_COLS, principal_rows

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            result = await perms_svc.list_database_principals(_TARGET, principal_type="user")

        assert len(result) == 1
        assert result[0].name == "alice@contoso.com"

    async def test_filter_role_type(self) -> None:
        principal_rows = [
            ("alice@contoso.com", "EXTERNAL_USER", "EXTERNAL"),
            ("db_owner", "DATABASE_ROLE", "NONE"),
        ]

        def _mock_run_query(_target: object, _sql: str, **_kwargs: object) -> tuple:
            return _PRINCIPAL_COLS, principal_rows

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            result = await perms_svc.list_database_principals(_TARGET, principal_type="role")

        assert len(result) == 1
        assert result[0].name == "db_owner"

    async def test_filter_all_returns_all(self) -> None:
        principal_rows = [
            ("alice@contoso.com", "EXTERNAL_USER", "EXTERNAL"),
            ("db_owner", "DATABASE_ROLE", "NONE"),
        ]

        def _mock_run_query(_target: object, _sql: str, **_kwargs: object) -> tuple:
            return _PRINCIPAL_COLS, principal_rows

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            result = await perms_svc.list_database_principals(_TARGET, principal_type="all")

        assert len(result) == 2

    async def test_principals_sql_does_not_exclude_roles(self) -> None:
        """_LIST_PRINCIPALS_SQL must not have WHERE type NOT IN ('R','A').

        The Python filter must narrow, not the SQL, so that --type role returns rows.
        """
        from fabric_dw.services.permissions import _LIST_PRINCIPALS_SQL  # noqa: PLC0415

        assert "type NOT IN" not in _LIST_PRINCIPALS_SQL
        assert "'R'" not in _LIST_PRINCIPALS_SQL


# ---------------------------------------------------------------------------
# revoke_permission exact SQL pins
# ---------------------------------------------------------------------------


class TestRevokePermissionExactSql:
    async def test_revoke_object_scope_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(
                _TARGET, "SELECT", "alice@contoso.com", "OBJECT", object_name="dbo.sales"
            )

        stmt = captured[0]
        assert stmt == "REVOKE SELECT ON OBJECT::[dbo].[sales] FROM [alice@contoso.com];"

    async def test_revoke_database_scope_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(_TARGET, "SELECT", "alice@contoso.com", "DATABASE")

        stmt = captured[0]
        # DATABASE scope: no ON clause emitted.
        assert stmt == "REVOKE SELECT FROM [alice@contoso.com];"

    async def test_revoke_grant_option_only_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "SCHEMA",
                schema="dbo",
                grant_option_only=True,
            )

        stmt = captured[0]
        assert stmt == ("REVOKE GRANT OPTION FOR SELECT ON SCHEMA::[dbo] FROM [alice@contoso.com];")

    async def test_revoke_cascade_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "SCHEMA",
                schema="dbo",
                cascade=True,
            )

        stmt = captured[0]
        assert stmt == "REVOKE SELECT ON SCHEMA::[dbo] FROM [alice@contoso.com] CASCADE;"


# ---------------------------------------------------------------------------
# column-level grants (minor_id != 0)
# ---------------------------------------------------------------------------


class TestColumnLevelPermissions:
    async def test_column_level_grant_surfaces_column_name(self) -> None:
        """list_sql_permissions must surface the column name for minor_id != 0 rows."""
        perm_rows = [
            (
                "alice@contoso.com",
                "EXTERNAL_USER",
                "GRANT",
                "SELECT",
                "OBJECT_OR_COLUMN",
                10,
                3,
                "email",  # column_name returned by COL_NAME(10, 3)
            ),
        ]
        obj_rows = [(10, "dbo", "customers")]
        schema_rows: list = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            if "sys.database_permissions" in sql and "sys.database_principals" in sql:
                return _DB_COLS, perm_rows
            if "OBJECT_SCHEMA_NAME" in sql:
                return _OBJECT_COLS, obj_rows
            if "sys.schemas" in sql:
                return _SCHEMA_COLS, schema_rows
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            result = await perms_svc.list_sql_permissions(_TARGET)

        assert len(result) == 1
        perm = result[0]
        assert perm.securable_class == "OBJECT"
        assert perm.schema_name == "dbo"
        assert perm.object_name == "customers"
        assert perm.column_name == "email"

    async def test_table_level_grant_has_null_column(self) -> None:
        """list_sql_permissions must set column_name=None for minor_id=0 rows."""
        perm_rows = [
            (
                "alice@contoso.com",
                "EXTERNAL_USER",
                "GRANT",
                "SELECT",
                "OBJECT_OR_COLUMN",
                10,
                0,
                None,
            ),
        ]
        obj_rows = [(10, "dbo", "sales")]
        schema_rows: list = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            if "sys.database_permissions" in sql and "sys.database_principals" in sql:
                return _DB_COLS, perm_rows
            if "OBJECT_SCHEMA_NAME" in sql:
                return _OBJECT_COLS, obj_rows
            if "sys.schemas" in sql:
                return _SCHEMA_COLS, schema_rows
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            result = await perms_svc.list_sql_permissions(_TARGET)

        assert len(result) == 1
        assert result[0].column_name is None


# ---------------------------------------------------------------------------
# my_permissions -- emitted argument pinning
# ---------------------------------------------------------------------------


class TestMyPermissionsArgumentPinning:
    async def test_schema_scope_emits_unbracketed_name(self) -> None:
        """my_permissions must pass the schema name without bracket-quoting."""
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return ["entity_name", "subentity_name", "permission_name"], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.my_permissions(_TARGET, scope="schema:dbo")

        sql = captured[0]
        # Must contain 'dbo' unbracketed, NOT '[dbo]'.
        assert "'dbo'" in sql
        assert "'[dbo]'" not in sql

    async def test_object_scope_emits_unbracketed_name(self) -> None:
        """my_permissions must pass the qualified name without bracket-quoting."""
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return ["entity_name", "subentity_name", "permission_name"], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.my_permissions(_TARGET, scope="object:dbo.sales")

        sql = captured[0]
        # Must contain 'dbo.sales' unbracketed.
        assert "'dbo.sales'" in sql
        assert "'[dbo].[sales]'" not in sql


# ---------------------------------------------------------------------------
# _validate_permissions -- input order preserved (not sorted)
# ---------------------------------------------------------------------------


class TestValidatePermissionsOrder:
    def test_input_order_preserved(self) -> None:
        from fabric_dw.services.permissions import _validate_permissions  # noqa: PLC0415

        result = _validate_permissions("DELETE,INSERT,SELECT", "DATABASE")
        assert result == ["DELETE", "INSERT", "SELECT"]

    def test_unsorted_input_preserved(self) -> None:
        from fabric_dw.services.permissions import _validate_permissions  # noqa: PLC0415

        result = _validate_permissions("SELECT,DELETE", "DATABASE")
        # Result must preserve input order: SELECT first, then DELETE.
        assert result == ["SELECT", "DELETE"]


# ---------------------------------------------------------------------------
# Column-level security (CLS)
# ---------------------------------------------------------------------------


class TestBuildColumnList:
    """Tests for the _build_column_list helper."""

    def test_single_column(self) -> None:
        from fabric_dw.services.permissions import _build_column_list  # noqa: PLC0415

        result = _build_column_list(["email"])
        assert result == " ([email])"

    def test_multiple_columns(self) -> None:
        from fabric_dw.services.permissions import _build_column_list  # noqa: PLC0415

        result = _build_column_list(["first_name", "last_name", "email"])
        assert result == " ([first_name], [last_name], [email])"

    def test_empty_list_raises(self) -> None:
        from fabric_dw.services.permissions import _build_column_list  # noqa: PLC0415

        with pytest.raises(ValueError, match="At least one column"):
            _build_column_list([])

    def test_invalid_identifier_raises(self) -> None:
        from fabric_dw.services.permissions import _build_column_list  # noqa: PLC0415

        with pytest.raises(ValueError, match="Invalid"):
            _build_column_list(["ok_col", "bad; col"])


class TestColumnApplicablePermissions:
    """COLUMN_APPLICABLE_PERMISSIONS allowlist."""

    def test_select_is_column_applicable(self) -> None:
        assert "SELECT" in perms_svc.COLUMN_APPLICABLE_PERMISSIONS

    def test_update_is_column_applicable(self) -> None:
        assert "UPDATE" in perms_svc.COLUMN_APPLICABLE_PERMISSIONS

    def test_references_is_column_applicable(self) -> None:
        assert "REFERENCES" in perms_svc.COLUMN_APPLICABLE_PERMISSIONS

    def test_insert_is_not_column_applicable(self) -> None:
        assert "INSERT" not in perms_svc.COLUMN_APPLICABLE_PERMISSIONS

    def test_delete_is_not_column_applicable(self) -> None:
        assert "DELETE" not in perms_svc.COLUMN_APPLICABLE_PERMISSIONS

    def test_execute_is_not_column_applicable(self) -> None:
        assert "EXECUTE" not in perms_svc.COLUMN_APPLICABLE_PERMISSIONS


class TestGrantPermissionWithColumns:
    """grant_permission with column list produces the correct SQL."""

    async def test_grant_select_on_columns_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.grant_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                columns=["email", "phone"],
            )

        stmt = captured[0]
        assert stmt == (
            "GRANT SELECT ON OBJECT::[dbo].[sales] ([email], [phone]) TO [alice@contoso.com];"
        )

    async def test_grant_update_on_single_column(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.grant_permission(
                _TARGET,
                "UPDATE",
                "editor@contoso.com",
                "OBJECT",
                object_name="dbo.customers",
                columns=["status"],
            )

        stmt = captured[0]
        assert stmt == (
            "GRANT UPDATE ON OBJECT::[dbo].[customers] ([status]) TO [editor@contoso.com];"
        )

    async def test_grant_with_grant_option_and_columns(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.grant_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                columns=["revenue"],
                with_grant_option=True,
            )

        stmt = captured[0]
        assert stmt == (
            "GRANT SELECT ON OBJECT::[dbo].[sales] ([revenue])"
            " TO [alice@contoso.com] WITH GRANT OPTION;"
        )

    async def test_columns_with_database_scope_raises(self) -> None:
        with pytest.raises(ValueError, match="columns may only be specified for OBJECT scope"):
            await perms_svc.grant_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "DATABASE",
                columns=["col1"],
            )

    async def test_columns_with_schema_scope_raises(self) -> None:
        with pytest.raises(ValueError, match="columns may only be specified for OBJECT scope"):
            await perms_svc.grant_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "SCHEMA",
                schema="dbo",
                columns=["col1"],
            )

    async def test_non_column_applicable_permission_raises(self) -> None:
        with pytest.raises(ValueError, match="Column-level permissions must be one of"):
            await perms_svc.grant_permission(
                _TARGET,
                "INSERT",
                "alice@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                columns=["col1"],
            )


class TestDenyPermissionWithColumns:
    """deny_permission with column list produces the correct SQL."""

    async def test_deny_select_on_columns_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.deny_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                columns=["ssn"],
            )

        stmt = captured[0]
        assert stmt == "DENY SELECT ON OBJECT::[dbo].[sales] ([ssn]) TO [alice@contoso.com];"

    async def test_deny_columns_with_schema_scope_raises(self) -> None:
        with pytest.raises(ValueError, match="columns may only be specified for OBJECT scope"):
            await perms_svc.deny_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "SCHEMA",
                schema="dbo",
                columns=["col1"],
            )


class TestRevokePermissionWithColumns:
    """revoke_permission with column list produces the correct SQL."""

    async def test_revoke_select_on_columns_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                columns=["email"],
            )

        stmt = captured[0]
        assert stmt == (
            "REVOKE SELECT ON OBJECT::[dbo].[sales] ([email]) FROM [alice@contoso.com];"
        )

    async def test_revoke_columns_with_cascade(self) -> None:
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                columns=["email", "phone"],
                cascade=True,
            )

        stmt = captured[0]
        assert stmt == (
            "REVOKE SELECT ON OBJECT::[dbo].[sales]"
            " ([email], [phone]) FROM [alice@contoso.com] CASCADE;"
        )

    async def test_revoke_columns_with_database_scope_raises(self) -> None:
        with pytest.raises(ValueError, match="columns may only be specified for OBJECT scope"):
            await perms_svc.revoke_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "DATABASE",
                columns=["col1"],
            )

    async def test_revoke_grant_option_for_with_columns_exact_sql(self) -> None:
        """REVOKE GRANT OPTION FOR with columns: exact SQL pin (fix #2)."""
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                columns=["email", "phone"],
                grant_option_only=True,
            )

        stmt = captured[0]
        assert stmt == (
            "REVOKE GRANT OPTION FOR SELECT ON OBJECT::[dbo].[sales]"
            " ([email], [phone]) FROM [alice@contoso.com];"
        )

    async def test_revoke_grant_option_for_with_columns_and_cascade_exact_sql(self) -> None:
        """REVOKE GRANT OPTION FOR + columns + CASCADE: exact SQL pin for the 3-way combination.

        Fabric requires CASCADE whenever GRANT OPTION FOR is revoked from a principal
        that holds the permission WITH GRANT OPTION, including at column level. The
        integration test ``test_revoke_grant_option_for_column_level_grant`` depends on
        this exact statement shape; pin it here so a future f-string refactor of
        ``revoke_permission`` can't silently break the 3-way path.
        """
        captured: list[str] = []

        def _mock_run_query(_target: object, sql: str, **_kwargs: object) -> tuple:
            captured.append(sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock_run_query):
            await perms_svc.revoke_permission(
                _TARGET,
                "SELECT",
                "alice@contoso.com",
                "OBJECT",
                object_name="dbo.sales",
                columns=["email"],
                grant_option_only=True,
                cascade=True,
            )

        stmt = captured[0]
        assert stmt == (
            "REVOKE GRANT OPTION FOR SELECT ON OBJECT::[dbo].[sales]"
            " ([email]) FROM [alice@contoso.com] CASCADE;"
        )
