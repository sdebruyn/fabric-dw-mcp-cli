"""Unit tests for row-level security service functions in fabric_dw.services.rls."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from fabric_dw.models import SecurityPolicy, SecurityPredicate
from fabric_dw.services import rls as rls_svc
from fabric_dw.sql import SqlTarget

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TARGET = SqlTarget(
    workspace_id="ws-1",
    database="SalesDW",
    connection_string="server.datawarehouse.fabric.microsoft.com",
)

_LIST_COLS = [
    "policy_name",
    "policy_schema",
    "is_enabled",
    "predicate_type_desc",
    "predicate_definition",
    "operation_desc",
    "table_schema",
    "table_name",
]


# ---------------------------------------------------------------------------
# _validate_predicate_type
# ---------------------------------------------------------------------------


class TestValidatePredicateType:
    def test_filter_accepted(self) -> None:
        from fabric_dw.services.rls import _validate_predicate_type  # noqa: PLC0415

        assert _validate_predicate_type("FILTER") == "FILTER"

    def test_block_accepted(self) -> None:
        from fabric_dw.services.rls import _validate_predicate_type  # noqa: PLC0415

        assert _validate_predicate_type("BLOCK") == "BLOCK"

    def test_case_insensitive(self) -> None:
        from fabric_dw.services.rls import _validate_predicate_type  # noqa: PLC0415

        assert _validate_predicate_type("filter") == "FILTER"
        assert _validate_predicate_type("block") == "BLOCK"

    def test_invalid_raises(self) -> None:
        from fabric_dw.services.rls import _validate_predicate_type  # noqa: PLC0415

        with pytest.raises(ValueError, match="Invalid predicate type"):
            _validate_predicate_type("SELECT")


# ---------------------------------------------------------------------------
# _validate_operation
# ---------------------------------------------------------------------------


class TestValidateOperation:
    def test_none_returns_none(self) -> None:
        from fabric_dw.services.rls import _validate_operation  # noqa: PLC0415

        assert _validate_operation(None) is None

    def test_after_insert_accepted(self) -> None:
        from fabric_dw.services.rls import _validate_operation  # noqa: PLC0415

        assert _validate_operation("AFTER_INSERT") == "AFTER_INSERT"

    def test_space_separated_normalised(self) -> None:
        from fabric_dw.services.rls import _validate_operation  # noqa: PLC0415

        assert _validate_operation("AFTER INSERT") == "AFTER_INSERT"

    def test_hyphen_normalised(self) -> None:
        from fabric_dw.services.rls import _validate_operation  # noqa: PLC0415

        assert _validate_operation("after-insert") == "AFTER_INSERT"

    def test_before_delete_accepted(self) -> None:
        from fabric_dw.services.rls import _validate_operation  # noqa: PLC0415

        assert _validate_operation("BEFORE_DELETE") == "BEFORE_DELETE"

    def test_invalid_raises(self) -> None:
        from fabric_dw.services.rls import _validate_operation  # noqa: PLC0415

        with pytest.raises(ValueError, match="Invalid block operation"):
            _validate_operation("AFTER_COMMIT")


# ---------------------------------------------------------------------------
# _resolve_policy_ref
# ---------------------------------------------------------------------------


class TestResolvePolicyRef:
    def test_bare_name(self) -> None:
        from fabric_dw.services.rls import _resolve_policy_ref  # noqa: PLC0415

        assert _resolve_policy_ref("MyPolicy") == "[MyPolicy]"

    def test_schema_qualified(self) -> None:
        from fabric_dw.services.rls import _resolve_policy_ref  # noqa: PLC0415

        assert _resolve_policy_ref("rls.MyPolicy") == "[rls].[MyPolicy]"

    def test_invalid_identifier_raises(self) -> None:
        from fabric_dw.services.rls import _resolve_policy_ref  # noqa: PLC0415

        with pytest.raises(ValueError, match="Invalid"):
            _resolve_policy_ref("bad; policy")


# ---------------------------------------------------------------------------
# _build_fn_call
# ---------------------------------------------------------------------------


class TestBuildFnCall:
    def test_schema_qualified_single_col(self) -> None:
        from fabric_dw.services.rls import _build_fn_call  # noqa: PLC0415

        result = _build_fn_call("rls", "fn_filter", ["SalesRep"])
        assert result == "[rls].[fn_filter]([SalesRep])"

    def test_no_schema_two_cols(self) -> None:
        from fabric_dw.services.rls import _build_fn_call  # noqa: PLC0415

        result = _build_fn_call(None, "fn_filter", ["col_a", "col_b"])
        assert result == "[fn_filter]([col_a], [col_b])"

    def test_empty_fn_schema_treated_as_none(self) -> None:
        from fabric_dw.services.rls import _build_fn_call  # noqa: PLC0415

        result = _build_fn_call("", "fn_filter", ["SalesRep"])
        assert result == "[fn_filter]([SalesRep])"

    def test_empty_args_raises(self) -> None:
        from fabric_dw.services.rls import _build_fn_call  # noqa: PLC0415

        with pytest.raises(ValueError, match="at least one column"):
            _build_fn_call("rls", "fn_filter", [])

    def test_invalid_col_raises(self) -> None:
        from fabric_dw.services.rls import _build_fn_call  # noqa: PLC0415

        with pytest.raises(ValueError, match="Invalid"):
            _build_fn_call("rls", "fn_filter", ["ok", "bad; col"])


# ---------------------------------------------------------------------------
# list_security_policies
# ---------------------------------------------------------------------------


class TestListSecurityPolicies:
    async def test_returns_policy_objects(self) -> None:
        rows = [
            (
                "SalesFilter",
                "rls",
                1,
                "FILTER",
                "[rls].[fn_filter]([SalesRep])",
                None,
                "dbo",
                "Sales",
            ),
        ]

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            return _LIST_COLS, rows

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            result = await rls_svc.list_security_policies(_TARGET)

        assert len(result) == 1
        pol = result[0]
        assert isinstance(pol, SecurityPolicy)
        assert pol.policy_name == "SalesFilter"
        assert pol.policy_schema == "rls"
        assert pol.is_enabled is True
        assert len(pol.predicates) == 1
        pred = pol.predicates[0]
        assert isinstance(pred, SecurityPredicate)
        assert pred.predicate_type == "FILTER"
        assert pred.operation is None
        assert pred.schema_name == "dbo"
        assert pred.table_name == "Sales"

    async def test_policy_with_no_predicates(self) -> None:
        rows = [
            ("EmptyPolicy", "dbo", 0, None, None, None, None, None),
        ]

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            return _LIST_COLS, rows

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            result = await rls_svc.list_security_policies(_TARGET)

        assert len(result) == 1
        assert result[0].predicates == []
        assert result[0].is_enabled is False

    async def test_policy_with_block_predicate_preserves_operation(self) -> None:
        rows = [
            (
                "SalesPolicy",
                "rls",
                1,
                "BLOCK",
                "[rls].[fn_block]([SalesRep])",
                "AFTER INSERT",
                "dbo",
                "Sales",
            ),
        ]

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            return _LIST_COLS, rows

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            result = await rls_svc.list_security_policies(_TARGET)

        pred = result[0].predicates[0]
        assert pred.predicate_type == "BLOCK"
        assert pred.operation == "AFTER INSERT"

    async def test_multiple_predicates_grouped_under_one_policy(self) -> None:
        rows = [
            ("SalesFilter", "rls", 1, "BLOCK", "[rls].[fn]([col])", "AFTER INSERT", "dbo", "Sales"),
            ("SalesFilter", "rls", 1, "FILTER", "[rls].[fn]([col])", None, "dbo", "Sales"),
        ]

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            return _LIST_COLS, rows

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            result = await rls_svc.list_security_policies(_TARGET)

        assert len(result) == 1
        assert len(result[0].predicates) == 2

    async def test_returns_empty_list_when_no_policies(self) -> None:
        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            return _LIST_COLS, []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            result = await rls_svc.list_security_policies(_TARGET)

        assert result == []


# ---------------------------------------------------------------------------
# create_security_policy - SQL shape
# ---------------------------------------------------------------------------


class TestCreateSecurityPolicy:
    async def test_filter_predicate_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.create_security_policy(
                _TARGET,
                "rls.SalesFilter",
                [
                    {
                        "predicate_type": "FILTER",
                        "fn_schema": "rls",
                        "fn_name": "fn_filter",
                        "fn_args": ["SalesRep"],
                        "table_schema": "dbo",
                        "table_name": "Sales",
                        "operation": None,
                    }
                ],
            )

        stmt = captured[0]
        assert stmt == (
            "CREATE SECURITY POLICY [rls].[SalesFilter]\n"
            "    ADD FILTER PREDICATE [rls].[fn_filter]([SalesRep]) ON [dbo].[Sales]\n"
            "    WITH (STATE = ON);"
        )

    async def test_state_off(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.create_security_policy(
                _TARGET,
                "MyPolicy",
                [
                    {
                        "predicate_type": "FILTER",
                        "fn_schema": None,
                        "fn_name": "fn_filter",
                        "fn_args": ["col"],
                        "table_schema": "dbo",
                        "table_name": "T",
                        "operation": None,
                    }
                ],
                state=False,
            )

        assert "STATE = OFF" in captured[0]

    async def test_block_predicate_with_operation(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.create_security_policy(
                _TARGET,
                "rls.SalesBlock",
                [
                    {
                        "predicate_type": "BLOCK",
                        "fn_schema": "rls",
                        "fn_name": "fn_block",
                        "fn_args": ["SalesRep"],
                        "table_schema": "dbo",
                        "table_name": "Sales",
                        "operation": "AFTER_INSERT",
                    }
                ],
            )

        stmt = captured[0]
        assert "ADD BLOCK PREDICATE" in stmt
        assert "AFTER INSERT" in stmt

    async def test_multi_predicate_create(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.create_security_policy(
                _TARGET,
                "rls.Combined",
                [
                    {
                        "predicate_type": "FILTER",
                        "fn_schema": "rls",
                        "fn_name": "fn_filter",
                        "fn_args": ["col"],
                        "table_schema": "dbo",
                        "table_name": "T",
                        "operation": None,
                    },
                    {
                        "predicate_type": "BLOCK",
                        "fn_schema": "rls",
                        "fn_name": "fn_block",
                        "fn_args": ["col"],
                        "table_schema": "dbo",
                        "table_name": "T",
                        "operation": "AFTER_INSERT",
                    },
                ],
            )

        stmt = captured[0]
        assert "ADD FILTER PREDICATE" in stmt
        assert "ADD BLOCK PREDICATE" in stmt

    async def test_empty_predicates_raises(self) -> None:
        with pytest.raises(ValueError, match="At least one predicate"):
            await rls_svc.create_security_policy(_TARGET, "MyPolicy", [])

    async def test_invalid_policy_name_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            await rls_svc.create_security_policy(
                _TARGET,
                "bad; policy",
                [
                    {
                        "predicate_type": "FILTER",
                        "fn_schema": "rls",
                        "fn_name": "fn",
                        "fn_args": ["col"],
                        "table_schema": "dbo",
                        "table_name": "T",
                        "operation": None,
                    }
                ],
            )


# ---------------------------------------------------------------------------
# add_predicate - SQL shape
# ---------------------------------------------------------------------------


class TestAddPredicate:
    async def test_filter_predicate_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.add_predicate(
                _TARGET,
                "rls.SalesFilter",
                "FILTER",
                "rls",
                "fn_filter",
                ["SalesRep"],
                "dbo",
                "Sales",
            )

        stmt = captured[0]
        assert stmt == (
            "ALTER SECURITY POLICY [rls].[SalesFilter]\n"
            "    ADD FILTER PREDICATE [rls].[fn_filter]([SalesRep]) ON [dbo].[Sales];"
        )

    async def test_block_predicate_with_operation_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.add_predicate(
                _TARGET,
                "rls.SalesBlock",
                "BLOCK",
                "rls",
                "fn_block",
                ["SalesRep"],
                "dbo",
                "Sales",
                operation="AFTER_INSERT",
            )

        stmt = captured[0]
        assert stmt == (
            "ALTER SECURITY POLICY [rls].[SalesBlock]\n"
            "    ADD BLOCK PREDICATE [rls].[fn_block]([SalesRep]) ON [dbo].[Sales] AFTER INSERT;"
        )

    async def test_block_predicate_without_operation(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.add_predicate(
                _TARGET,
                "rls.SalesBlock",
                "BLOCK",
                "rls",
                "fn_block",
                ["SalesRep"],
                "dbo",
                "Sales",
            )

        stmt = captured[0]
        # No AFTER/BEFORE clause when operation is None
        assert "ADD BLOCK PREDICATE" in stmt
        assert "AFTER" not in stmt
        assert "BEFORE" not in stmt

    async def test_invalid_predicate_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid predicate type"):
            await rls_svc.add_predicate(
                _TARGET, "MyPolicy", "DENY", "rls", "fn", ["col"], "dbo", "T"
            )


# ---------------------------------------------------------------------------
# drop_predicate - SQL shape
# ---------------------------------------------------------------------------


class TestDropPredicate:
    async def test_filter_predicate_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.drop_predicate(
                _TARGET,
                "rls.SalesFilter",
                "FILTER",
                "dbo",
                "Sales",
            )

        stmt = captured[0]
        assert stmt == (
            "ALTER SECURITY POLICY [rls].[SalesFilter]\n    DROP FILTER PREDICATE ON [dbo].[Sales];"
        )

    async def test_block_predicate_with_operation_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.drop_predicate(
                _TARGET,
                "rls.SalesBlock",
                "BLOCK",
                "dbo",
                "Sales",
                operation="AFTER_INSERT",
            )

        stmt = captured[0]
        assert stmt == (
            "ALTER SECURITY POLICY [rls].[SalesBlock]\n"
            "    DROP BLOCK PREDICATE ON [dbo].[Sales] AFTER INSERT;"
        )

    async def test_block_without_operation_emits_no_op_clause(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.drop_predicate(
                _TARGET,
                "rls.SalesBlock",
                "BLOCK",
                "dbo",
                "Sales",
            )

        stmt = captured[0]
        assert "DROP BLOCK PREDICATE ON [dbo].[Sales];" in stmt


# ---------------------------------------------------------------------------
# set_policy_state - SQL shape
# ---------------------------------------------------------------------------


class TestSetPolicyState:
    async def test_enable_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.set_policy_state(_TARGET, "rls.SalesFilter", enabled=True)

        assert captured[0] == "ALTER SECURITY POLICY [rls].[SalesFilter] WITH (STATE = ON);"

    async def test_disable_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.set_policy_state(_TARGET, "rls.SalesFilter", enabled=False)

        assert captured[0] == "ALTER SECURITY POLICY [rls].[SalesFilter] WITH (STATE = OFF);"

    async def test_bare_name(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.set_policy_state(_TARGET, "MyPolicy", enabled=True)

        assert captured[0] == "ALTER SECURITY POLICY [MyPolicy] WITH (STATE = ON);"


# ---------------------------------------------------------------------------
# drop_security_policy - SQL shape
# ---------------------------------------------------------------------------


class TestDropSecurityPolicy:
    async def test_schema_qualified_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.drop_security_policy(_TARGET, "rls.SalesFilter")

        assert captured[0] == "DROP SECURITY POLICY [rls].[SalesFilter];"

    async def test_bare_name_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.rls.run_query", side_effect=_mock):
            await rls_svc.drop_security_policy(_TARGET, "MyPolicy")

        assert captured[0] == "DROP SECURITY POLICY [MyPolicy];"

    async def test_invalid_name_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            await rls_svc.drop_security_policy(_TARGET, "bad; policy")
