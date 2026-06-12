"""Stateless SQL helper for connecting to Microsoft Fabric Data Warehouses.

Public API
----------
- :class:`SqlTarget`          — frozen dataclass identifying a warehouse.
- :func:`build_connection_string` — augment the raw API connection string.
- :func:`open_connection`     — open a single sync connection (caller closes).
- :func:`map_driver_error`    — classify a driver exception → high-level error.
"""

from __future__ import annotations

import importlib
import re
import types
from dataclasses import dataclass
from typing import Any, Protocol

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import AuthError, PermissionDenied

# Thin indirection that lets tests monkeypatch ``_mssql`` without the native
# extension being imported at module-load time (the .so may not be loadable in
# every environment).  The real driver is imported lazily on first use.
_mssql: types.ModuleType | None = None


def _get_mssql() -> types.ModuleType:
    """Return the mssql_python module, importing it on first call."""
    global _mssql  # noqa: PLW0603
    if _mssql is None:
        _mssql = importlib.import_module("mssql_python")
    return _mssql


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

# Mapping from CredentialMode to the ActiveDirectory auth type suffix.
_MODE_TO_AD_AUTH: dict[CredentialMode, str] = {
    CredentialMode.DEFAULT: "ActiveDirectoryDefault",
    CredentialMode.SERVICE_PRINCIPAL: "ActiveDirectoryServicePrincipal",
    CredentialMode.INTERACTIVE: "ActiveDirectoryInteractive",
}


# ---------------------------------------------------------------------------
# Minimal DB-API 2.0 Protocols (for type checking only)
# ---------------------------------------------------------------------------


class _Cursor(Protocol):
    description: list[tuple[str, Any]] | None
    rowcount: int

    def execute(self, sql: str) -> None: ...

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

    Permission-denied is checked before auth-failure so a message that
    contains both fragments resolves to :class:`~fabric_dw.exceptions.PermissionDenied`.

    Args:
        exc: The raw exception raised by the driver.

    Returns:
        A :class:`~fabric_dw.exceptions.PermissionDenied` or
        :class:`~fabric_dw.exceptions.AuthError` instance if the error message
        matches a known fragment, otherwise ``None``.
    """
    msg = str(exc).lower()
    if any(fragment in msg for fragment in _PERMISSION_DENIED_FRAGMENTS):
        return PermissionDenied(str(exc))
    if any(fragment in msg for fragment in _AUTH_FAILED_FRAGMENTS):
        return AuthError(str(exc))
    return None
