"""Row-level security service functions for Microsoft Fabric Data Warehouses.

Reads from ``sys.security_policies`` / ``sys.security_predicates`` and issues
``CREATE SECURITY POLICY``, ``ALTER SECURITY POLICY``, and
``DROP SECURITY POLICY`` statements.

FILTER predicates only (#966)
------------------------------
Fabric Data Warehouse rejects BLOCK predicates for both ``CREATE`` and
``ALTER SECURITY POLICY`` ("BLOCK PREDICATE is not supported"). BLOCK support
was added in #919 but shipped untested and always fails against real Fabric,
so it has been removed entirely: this module builds FILTER predicates only,
and no function here accepts a predicate-type argument -- there is nothing
to choose between.

Statement-building safety
--------------------------
All statements are built from:
- Validated, bracket-quoted identifiers via
  :func:`~fabric_dw.identifiers.validate_identifier` +
  :func:`~fabric_dw.identifiers.quote_identifier`.
- Column names for predicate function arguments validated via
  :func:`~fabric_dw.identifiers.validate_column_name` +
  :func:`~fabric_dw.identifiers.quote_identifier`.

No SQL text is ever parsed or rewritten.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fabric_dw.auth import CredentialMode
from fabric_dw.identifiers import (
    parse_qualified_name,
    quote_identifier,
    validate_column_name,
    validate_identifier,
)
from fabric_dw.models import SecurityPolicy, SecurityPredicate
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "add_predicate",
    "create_security_policy",
    "drop_predicate",
    "drop_security_policy",
    "list_security_policies",
    "set_policy_state",
]

# ---------------------------------------------------------------------------
# SQL templates (read)
# ---------------------------------------------------------------------------

_LIST_POLICIES_SQL = """\
SELECT
    sp.name AS policy_name,
    ss.name AS policy_schema,
    sp.is_enabled,
    pred.predicate_type_desc,
    pred.predicate_definition,
    pred.operation_desc,
    OBJECT_SCHEMA_NAME(pred.target_object_id) AS table_schema,
    OBJECT_NAME(pred.target_object_id) AS table_name
FROM sys.security_policies AS sp
JOIN sys.schemas AS ss ON sp.schema_id = ss.schema_id
LEFT JOIN sys.security_predicates AS pred ON pred.object_id = sp.object_id
ORDER BY sp.name, pred.predicate_type_desc;
"""

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_policy_ref(policy_name: str) -> str:
    """Return a bracket-quoted policy reference for use in DDL.

    Accepts ``"schema.name"`` (two-part) or ``"name"`` (bare name).
    Both parts are validated via :func:`validate_identifier`.

    Args:
        policy_name: The policy name, optionally schema-qualified.

    Returns:
        A bracket-quoted string, e.g. ``"[rls].[MySalesFilter]"`` or
        ``"[MySalesFilter]"``.

    Raises:
        ValueError: If any identifier part is invalid.
    """
    if "." in policy_name:
        schema, name = parse_qualified_name(policy_name, "policy")
        validate_identifier(schema)
        validate_identifier(name)
        return f"{quote_identifier(schema)}.{quote_identifier(name)}"
    validate_identifier(policy_name)
    return quote_identifier(policy_name)


def _build_fn_call(
    fn_schema: str | None,
    fn_name: str,
    fn_args: list[str],
) -> str:
    """Build a bracket-quoted predicate function call expression.

    Produces ``[fn_schema].[fn_name]([col1], [col2])`` or
    ``[fn_name]([col1], [col2])`` when *fn_schema* is ``None`` or empty.

    Args:
        fn_schema: Schema of the predicate function, or ``None`` / ``""``
            when not schema-qualified.
        fn_name: Name of the predicate function.
        fn_args: Column names passed to the function.  Each is validated via
            :func:`validate_column_name` and bracket-quoted.

    Returns:
        The complete function call expression.

    Raises:
        ValueError: If any identifier or column name is invalid, or if
            *fn_args* is empty.
    """
    if not fn_args:
        msg = "Predicate function must have at least one column argument"
        raise ValueError(msg)
    validate_identifier(fn_name)
    for col in fn_args:
        validate_column_name(col)
    cols_sql = ", ".join(quote_identifier(c) for c in fn_args)
    if fn_schema:
        validate_identifier(fn_schema)
        return f"{quote_identifier(fn_schema)}.{quote_identifier(fn_name)}({cols_sql})"
    return f"{quote_identifier(fn_name)}({cols_sql})"


def _build_predicate_clause(
    fn_schema: str | None,
    fn_name: str,
    fn_args: list[str],
    table_schema: str,
    table_name: str,
) -> str:
    """Build one ``ADD FILTER PREDICATE fn(...) ON table`` clause.

    Args:
        fn_schema: Schema of the predicate function (may be ``None``).
        fn_name: Name of the predicate function.
        fn_args: Column name arguments for the predicate function.
        table_schema: Schema of the target table.
        table_name: Name of the target table.

    Returns:
        The ``ADD FILTER PREDICATE ...`` clause string (no trailing
        semicolon).
    """
    fn_call = _build_fn_call(fn_schema, fn_name, fn_args)
    validate_identifier(table_schema)
    validate_identifier(table_name)
    table_ref = f"{quote_identifier(table_schema)}.{quote_identifier(table_name)}"
    return f"ADD FILTER PREDICATE {fn_call} ON {table_ref}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_security_policies(
    target: SqlTarget,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[SecurityPolicy]:
    """Return all security policies with their predicates.

    Reads from ``sys.security_policies`` joined to ``sys.security_predicates``.
    Policies with no predicates are returned with an empty ``predicates`` list.

    Args:
        target: SQL connection target.
        mode: Credential mode for Entra authentication.

    Returns:
        List of :class:`~fabric_dw.models.SecurityPolicy` objects.
    """

    def _run() -> list[SecurityPolicy]:
        cols, rows = run_query(target, _LIST_POLICIES_SQL, mode=mode)

        # Preserve insertion order (Python 3.7+ dict guarantee).
        policy_meta: dict[tuple[str, str], tuple[str, str, bool]] = {}
        predicates_by_key: dict[tuple[str, str], list[SecurityPredicate]] = defaultdict(list)

        for row in rows:
            d = dict(zip(cols, row, strict=True))
            p_schema = str(d["policy_schema"])
            p_name = str(d["policy_name"])
            key = (p_schema, p_name)

            if key not in policy_meta:
                policy_meta[key] = (p_schema, p_name, bool(d["is_enabled"]))

            if d["predicate_type_desc"] is not None:
                raw_op = d["operation_desc"]
                pred = SecurityPredicate(
                    predicate_type=str(d["predicate_type_desc"]),
                    operation=raw_op.replace(" ", "_") if raw_op is not None else None,
                    schema_name=d["table_schema"],
                    table_name=d["table_name"],
                    predicate_definition=str(d["predicate_definition"]),
                )
                predicates_by_key[key].append(pred)

        return [
            SecurityPolicy(
                policy_schema=schema,
                policy_name=name,
                is_enabled=is_enabled,
                predicates=predicates_by_key[key],
            )
            for key, (schema, name, is_enabled) in policy_meta.items()
        ]

    return await asyncio.to_thread(_run)


async def create_security_policy(
    target: SqlTarget,
    policy_name: str,
    predicates: list[dict[str, Any]],
    *,
    state: bool = True,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Create a security policy with one or more FILTER predicates.

    Executes ``CREATE SECURITY POLICY ... ADD FILTER PREDICATE ...``. There is
    no predicate-type option (#966): Fabric Data Warehouse supports FILTER
    predicates only, so every predicate built here is a FILTER predicate.

    Each element of *predicates* must be a dict with exactly these keys (no
    more, no fewer -- unrecognised keys are rejected rather than silently
    ignored, so e.g. a stray ``"predicate_type": "BLOCK"`` surfaces as an
    error instead of silently building a FILTER predicate):

    - ``fn_schema``: schema of the predicate function (``str`` or ``None``)
    - ``fn_name``: name of the predicate function
    - ``fn_args``: list of column name strings
    - ``table_schema``: schema of the target table
    - ``table_name``: name of the target table

    Args:
        target: SQL connection target.
        policy_name: Security policy name, optionally schema-qualified
            (e.g. ``"rls.MySalesFilter"`` or ``"MySalesFilter"``).
        predicates: Non-empty list of predicate specification dicts.
        state: Initial policy state -- ``True`` for ``ON`` (default),
            ``False`` for ``OFF``.
        mode: Credential mode for Entra authentication.

    Raises:
        ValueError: If *predicates* is empty, a spec is missing a required
            key, or a spec contains an unrecognised key.
    """
    if not predicates:
        msg = "At least one predicate is required to create a security policy"
        raise ValueError(msg)

    policy_ref = _resolve_policy_ref(policy_name)
    state_sql = "ON" if state else "OFF"

    clauses: list[str] = []
    for i, spec in enumerate(predicates):
        _required = {"fn_name", "fn_args", "table_schema", "table_name"}
        _allowed = _required | {"fn_schema"}
        _missing = _required - spec.keys()
        if _missing:
            missing_list = ", ".join(sorted(_missing))
            msg = f"Predicate at index {i} is missing required key(s): {missing_list}"
            raise ValueError(msg)
        # Reject unrecognised keys outright rather than silently ignoring them (#967).
        # This is generic key-set hygiene, not a predicate-type check: a stray
        # "predicate_type": "BLOCK" is caught the same way a typo like "table" would
        # be, so a caller never gets a silent FILTER built from a spec that implied
        # something else was requested.
        _unknown = spec.keys() - _allowed
        if _unknown:
            unknown_list = ", ".join(sorted(_unknown))
            msg = f"Predicate at index {i} has unknown key(s): {unknown_list}"
            raise ValueError(msg)
        fn_schema_raw = spec.get("fn_schema")
        fn_schema = str(fn_schema_raw) if fn_schema_raw else None
        fn_name = str(spec["fn_name"])
        fn_args = [str(a) for a in spec["fn_args"]]
        table_schema = str(spec["table_schema"])
        table_name = str(spec["table_name"])
        clauses.append(
            _build_predicate_clause(fn_schema, fn_name, fn_args, table_schema, table_name)
        )

    predicate_sql = ",\n    ".join(clauses)
    ddl = (
        f"CREATE SECURITY POLICY {policy_ref}\n    {predicate_sql}\n    WITH (STATE = {state_sql});"
    )

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)


async def add_predicate(
    target: SqlTarget,
    policy_name: str,
    fn_schema: str | None,
    fn_name: str,
    fn_args: list[str],
    table_schema: str,
    table_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Add a FILTER predicate to an existing security policy.

    Executes ``ALTER SECURITY POLICY ... ADD FILTER PREDICATE ...``. There is
    no predicate-type argument (#966): Fabric Data Warehouse supports FILTER
    predicates only.

    Args:
        target: SQL connection target.
        policy_name: Security policy name (optionally schema-qualified).
        fn_schema: Schema of the predicate function, or ``None``.
        fn_name: Name of the predicate function.
        fn_args: Column name arguments for the predicate function.
        table_schema: Schema of the target table.
        table_name: Name of the target table.
        mode: Credential mode for Entra authentication.

    Raises:
        ValueError: If any identifier or argument is invalid.
    """
    policy_ref = _resolve_policy_ref(policy_name)
    clause = _build_predicate_clause(fn_schema, fn_name, fn_args, table_schema, table_name)
    ddl = f"ALTER SECURITY POLICY {policy_ref}\n    {clause};"

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)


async def drop_predicate(
    target: SqlTarget,
    policy_name: str,
    table_schema: str,
    table_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop the FILTER predicate from an existing security policy.

    Executes ``ALTER SECURITY POLICY ... DROP FILTER PREDICATE ON ...``.
    There is no predicate-type argument (#966): Fabric Data Warehouse
    supports FILTER predicates only.

    The T-SQL ``DROP FILTER PREDICATE ON`` clause takes no operation
    qualifier -- the operation is not part of the drop syntax.

    Args:
        target: SQL connection target.
        policy_name: Security policy name (optionally schema-qualified).
        table_schema: Schema of the target table.
        table_name: Name of the target table.
        mode: Credential mode for Entra authentication.

    Raises:
        ValueError: If any identifier is invalid.
    """
    policy_ref = _resolve_policy_ref(policy_name)
    validate_identifier(table_schema)
    validate_identifier(table_name)
    table_ref = f"{quote_identifier(table_schema)}.{quote_identifier(table_name)}"
    ddl = f"ALTER SECURITY POLICY {policy_ref}\n    DROP FILTER PREDICATE ON {table_ref};"

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)


async def set_policy_state(
    target: SqlTarget,
    policy_name: str,
    *,
    enabled: bool,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Enable or disable a security policy.

    Executes ``ALTER SECURITY POLICY ... WITH (STATE = ON|OFF)``.

    Args:
        target: SQL connection target.
        policy_name: Security policy name (optionally schema-qualified).
        enabled: ``True`` to enable (STATE = ON), ``False`` to disable.
        mode: Credential mode for Entra authentication.

    Raises:
        ValueError: If the policy name is invalid.
    """
    policy_ref = _resolve_policy_ref(policy_name)
    state_sql = "ON" if enabled else "OFF"
    ddl = f"ALTER SECURITY POLICY {policy_ref} WITH (STATE = {state_sql});"

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)


async def drop_security_policy(
    target: SqlTarget,
    policy_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop a security policy.

    Executes ``DROP SECURITY POLICY [schema].[name]``.

    This is a destructive operation: the policy and all its predicates are
    removed permanently.

    Args:
        target: SQL connection target.
        policy_name: Security policy name (optionally schema-qualified).
        mode: Credential mode for Entra authentication.

    Raises:
        ValueError: If the policy name is invalid.
    """
    policy_ref = _resolve_policy_ref(policy_name)
    ddl = f"DROP SECURITY POLICY {policy_ref};"

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)
