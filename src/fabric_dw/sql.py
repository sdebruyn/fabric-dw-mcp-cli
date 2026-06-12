"""Stateless SQL helper for connecting to Microsoft Fabric Data Warehouses.

Public API
----------
- :class:`SqlTarget`          — frozen dataclass identifying a warehouse.
- :func:`build_connection_string` — augment the raw API connection string.
- :func:`open_connection`     — open a single sync connection (caller closes).
- :func:`map_driver_error`    — classify a driver exception → high-level error.
- :func:`run_query`           — open connection, execute, fetch, map errors.
- :func:`run_statements`      — execute multiple DDL statements on ONE connection.
"""

from __future__ import annotations

import functools
import importlib
import re
import types
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import AuthError, PermissionDenied

# ---------------------------------------------------------------------------
# Lazy driver import — @functools.cache avoids the module-level global and the
# PLW0603 noqa suppression.
# ---------------------------------------------------------------------------


@functools.cache
def _driver() -> types.ModuleType:
    """Return the ``mssql_python`` module, importing it on first call.

    The result is cached so the import happens at most once per process.
    Tests can monkeypatch :func:`_get_mssql` instead (kept as alias below).
    """
    return importlib.import_module("mssql_python")


# Legacy shim used by existing tests / callers that monkeypatch ``_mssql``.
# We keep it so tests that do ``monkeypatch.setattr(_sql_module, "_mssql", ...)``
# still work — they write to the module-level name which is checked first.
_mssql: types.ModuleType | None = None


def _get_mssql() -> types.ModuleType:
    """Return the mssql_python module, preferring the monkeypatched stub.

    Tests that use ``monkeypatch.setattr(_sql_module, "_mssql", mock)`` set
    ``_mssql`` to a non-None value.  Production code (where ``_mssql`` is
    ``None``) falls through to the cached :func:`_driver`.
    """
    return _mssql if _mssql is not None else _driver()


# ---------------------------------------------------------------------------
# Sentinel strings for error classification
# ---------------------------------------------------------------------------

# SQL permission-denial failures in driver error messages.
_PERMISSION_DENIED_FRAGMENTS = (
    "permission was denied",
    "denied the right to",
)

# Entra authentication failures in driver error messages.
_AUTH_FAILED_FRAGMENTS = ("authentication failed",)

# SQL Server native error numbers that indicate permission denied.
# 229: SELECT permission denied; 230: INSERT; 297: execute permission denied.
_PERMISSION_DENIED_ERROR_NUMBERS = frozenset({229, 230, 297})

# SQL Server native error number for authentication failure (login failed).
_AUTH_FAILED_ERROR_NUMBERS = frozenset({18456})

# Mapping from CredentialMode to the ActiveDirectory auth type suffix.
_MODE_TO_AD_AUTH: dict[CredentialMode, str] = {
    CredentialMode.DEFAULT: "ActiveDirectoryDefault",
    CredentialMode.SERVICE_PRINCIPAL: "ActiveDirectoryServicePrincipal",
    CredentialMode.INTERACTIVE: "ActiveDirectoryInteractive",
}

# Regex to extract the native SQL Server error number from a DDBC error string.
# The ODBC driver embeds it as e.g. "[SQL Server]Login failed ... Error: 18456"
# or "[SQL Server] ... (229)".
_NATIVE_ERROR_RE = re.compile(r"\b(?:Error:\s*|error\s+)(\d+)\b|\((\d+)\)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Minimal DB-API 2.0 Protocols (for type checking only)
# ---------------------------------------------------------------------------


class _Cursor(Protocol):
    description: list[tuple[str, Any]] | None
    rowcount: int

    def execute(self, sql: str, params: Sequence[object] | None = None) -> None: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]: ...

    def nextset(self) -> bool | None: ...

    def close(self) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# SqlTarget
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SqlTarget:
    """Identifies a specific Fabric warehouse to connect to.

    Attributes:
        workspace_id: The Fabric workspace GUID.
        database: The warehouse / database name.
        connection_string: The raw ODBC connection string from the Fabric API;
            no augmentation should be applied before passing it here.
    """

    workspace_id: str
    database: str
    connection_string: str


# ---------------------------------------------------------------------------
# Internal connection-string helpers
# ---------------------------------------------------------------------------


def _has_key(connection_string: str, key: str) -> bool:
    """Return True if *key* is already present in the ODBC connection string."""
    pattern = re.compile(r"(?:^|;)\s*" + re.escape(key) + r"\s*=", re.IGNORECASE)
    return bool(pattern.search(connection_string))


def _set_key(connection_string: str, key: str, value: str) -> str:
    """Append *key=value* to *connection_string* if *key* is not already set."""
    if _has_key(connection_string, key):
        return connection_string
    stripped = connection_string.rstrip().rstrip(";")
    sep = ";" if stripped else ""
    return f"{stripped}{sep}{key}={value}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_connection_string(
    target: SqlTarget,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> str:
    """Augment the API-provided connection string with auth, encryption and database settings.

    The operation is idempotent: calling it twice with the same target and mode
    returns the identical string.

    Args:
        target: The :class:`SqlTarget` whose ``connection_string`` and ``database``
            are used as inputs.
        mode: The credential mode, used to select the ActiveDirectory auth variant.

    Returns:
        The augmented ODBC connection string, ready to pass to the driver.
    """
    # The Fabric API returns the warehouse FQDN as a bare hostname with no
    # "Server=" prefix.  The mssql_python driver requires a proper ODBC key=value
    # format, so prepend "Server=" when the raw string has no Server key.
    raw = target.connection_string
    if not _has_key(raw, "Server"):
        raw = f"Server={raw}"
    cs = _set_key(raw, "Authentication", _MODE_TO_AD_AUTH[mode])
    cs = _set_key(cs, "Encrypt", "yes")
    cs = _set_key(cs, "TrustServerCertificate", "no")
    return _set_key(cs, "Database", target.database)


def open_connection(
    target: SqlTarget,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> _Connection:
    """Open a single synchronous connection to the target warehouse.

    This function is intentionally synchronous.  Callers that need to keep
    the event loop free should wrap the entire sync block in
    ``asyncio.to_thread``.

    The caller is responsible for closing the returned connection (e.g. via
    ``contextlib.closing``).

    Args:
        target: The :class:`SqlTarget` identifying the warehouse.
        mode: The credential mode for Entra authentication.

    Returns:
        A DB-API 2.0 connection object from the ``mssql_python`` driver.
    """
    cs = build_connection_string(target, mode=mode)
    conn: _Connection = _get_mssql().connect(cs)
    return conn


def map_driver_error(exc: BaseException) -> Exception | None:
    """Return a mapped exception for known driver error categories, or ``None``.

    Matching strategy (in priority order):

    1. **Native SQL Server error numbers** — inspect ``exc.ddbc_error`` for
       embedded error numbers (e.g. ``Error: 229``, ``(18456)``).  This is the
       most reliable signal and survives locale / driver-version changes.
    2. **Message-fragment fallback** — scan the stringified exception for known
       English substrings.  Kept so that behaviour never regresses when error
       numbers are unavailable (e.g. mock exceptions in tests).

    Permission-denied is checked before auth-failure in both strategies so a
    message containing both fragments resolves to
    :class:`~fabric_dw.exceptions.PermissionDenied`.

    Args:
        exc: The raw exception raised by the driver.

    Returns:
        A :class:`~fabric_dw.exceptions.PermissionDenied` or
        :class:`~fabric_dw.exceptions.AuthError` instance if the error message
        matches a known fragment or error number, otherwise ``None``.
    """
    # --- Strategy 1: native error number (primary, locale-independent) ---
    ddbc_error = getattr(exc, "ddbc_error", None)
    if ddbc_error:
        for match in _NATIVE_ERROR_RE.finditer(str(ddbc_error)):
            raw_num = match.group(1) or match.group(2)
            if raw_num:
                err_num = int(raw_num)
                if err_num in _PERMISSION_DENIED_ERROR_NUMBERS:
                    return PermissionDenied(str(exc))
                if err_num in _AUTH_FAILED_ERROR_NUMBERS:
                    return AuthError(str(exc))

    # --- Strategy 2: message-fragment fallback (locale-dependent, documented) ---
    msg = str(exc).lower()
    if any(fragment in msg for fragment in _PERMISSION_DENIED_FRAGMENTS):
        return PermissionDenied(str(exc))
    if any(fragment in msg for fragment in _AUTH_FAILED_FRAGMENTS):
        return AuthError(str(exc))
    return None


# ---------------------------------------------------------------------------
# TDS runner helpers
# ---------------------------------------------------------------------------


def run_query(  # noqa: PLR0913
    target: SqlTarget,
    statement: str,
    *,
    params: Sequence[object] | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
    commit: bool = False,
    fetch: Literal["all", "none", "one"] = "all",
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Open a connection, execute *statement*, fetch rows, close, and map errors.

    This is the single TDS execute-and-fetch helper that replaces ~20 copies of
    the open-connection / cursor / execute / map-error pattern across services.

    The ``mssql_python`` driver uses ``pyformat`` paramstyle (``%(name)s``) but
    also accepts qmark (``?``) style.  *params* is unpacked as variadic positional
    arguments because the driver's ``execute`` signature is
    ``execute(sql, *parameters)``.

    Args:
        target: The :class:`SqlTarget` identifying the warehouse.
        statement: The SQL statement to execute.
        params: Optional sequence of parameter values to bind.  Use ``?``
            placeholders in *statement*.  Identifiers (schema/table names) MUST
            be bracket-quoted via :func:`~fabric_dw.identifiers.quote_identifier`
            and validated via :func:`~fabric_dw.identifiers.validate_identifier`
            — they cannot be bound as parameters.
        mode: The credential mode for Entra authentication.
        commit: When ``True``, call ``conn.commit()`` after execute (for DDL/DML).
        fetch: One of:
            - ``"all"`` (default) — call ``fetchall()`` and return
              ``(columns, rows)``; columns are derived from ``cursor.description``.
            - ``"one"`` — call ``fetchone()`` (not yet used but available for
              future point-lookup helpers); returns ``(columns, [row])`` or
              ``([], [])`` when no row.
            - ``"none"`` — do not fetch; returns ``([], [])``.

    Returns:
        A ``(columns, rows)`` tuple where *columns* is a list of column-name
        strings and *rows* is a list of row tuples.

    Raises:
        PermissionDenied: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
        Exception: Any other driver error is propagated unchanged.
    """
    with closing(open_connection(target, mode=mode)) as conn:
        cursor = conn.cursor()
        try:
            if params:
                cursor.execute(statement, params)
            else:
                cursor.execute(statement)
            if commit:
                conn.commit()
        except Exception as exc:
            mapped = map_driver_error(exc)
            if mapped:
                raise mapped from exc
            raise

        if fetch == "none":
            return [], []

        cols = [c[0] for c in (cursor.description or [])]

        if fetch == "one":
            row = cursor.fetchone()  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            return cols, ([row] if row is not None else [])

        # Default: fetch all rows.
        rows: list[tuple[Any, ...]] = cursor.fetchall()
        return cols, rows


def run_statements(
    target: SqlTarget,
    statements: Sequence[str],
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Execute multiple DDL/DML *statements* on a **single** connection.

    Opens ONE connection, executes each statement in sequence (committing after
    each), then closes the connection.  This avoids the N x TCP+TLS+TDS handshake
    overhead that arises when opening a new connection per statement (relevant
    for ``_drop_schema_objects`` with many tables/views).

    Args:
        target: The :class:`SqlTarget` identifying the warehouse.
        statements: Sequence of SQL strings to execute in order.
        mode: The credential mode for Entra authentication.

    Raises:
        PermissionDenied: If the driver reports a permission error on any statement.
        AuthError: If the driver reports an authentication failure.
        Exception: Any other driver error is propagated unchanged.
    """
    with closing(open_connection(target, mode=mode)) as conn:
        cursor = conn.cursor()
        for stmt in statements:
            try:
                cursor.execute(stmt)
                conn.commit()
            except Exception as exc:
                mapped = map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise
