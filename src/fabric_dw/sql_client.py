"""Async wrapper around mssql-python with a connection pool per (workspace, database)."""

from __future__ import annotations

import asyncio
import importlib
import re
import types
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import AuthError

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


# Sentinel strings that signal Entra auth failures in driver error messages.
_AUTH_ERROR_FRAGMENTS = (
    "login failed",
    "token",
    "authentication",
    "unauthorized",
    "access denied",
    "invalid authorization",
    "28000",
)

# Mapping from CredentialMode to the ActiveDirectory auth type suffix.
_MODE_TO_AD_AUTH: dict[CredentialMode, str] = {
    CredentialMode.DEFAULT: "ActiveDirectoryDefault",
    CredentialMode.SERVICE_PRINCIPAL: "ActiveDirectoryServicePrincipal",
    CredentialMode.INTERACTIVE: "ActiveDirectoryInteractive",
}


def _has_key(connection_string: str, key: str) -> bool:
    """Return True if *key* is already present in the ODBC connection string."""
    pattern = re.compile(r"(?:^|;)\s*" + re.escape(key) + r"\s*=", re.IGNORECASE)
    return bool(pattern.search(connection_string))


def _set_key(connection_string: str, key: str, value: str) -> str:
    """Append *key=value* to *connection_string* if *key* is not already set."""
    if _has_key(connection_string, key):
        return connection_string
    sep = ";" if connection_string.rstrip().rstrip(";").strip() else ""
    return f"{connection_string.rstrip().rstrip(';')}{sep};{key}={value}".lstrip(";")


def _augment_connection_string(
    connection_string: str,
    database: str,
    mode: CredentialMode,
) -> str:
    """Return the connection string augmented with auth, encryption and database settings.

    The operation is idempotent: calling it twice with the same arguments returns
    the identical string.

    Args:
        connection_string: The raw connection string from the Fabric/Power BI API.
        database: The target database name, added only if not already present.
        mode: The credential mode, used to select the ActiveDirectory auth variant.

    Returns:
        The augmented connection string.
    """
    cs = _set_key(connection_string, "Authentication", _MODE_TO_AD_AUTH[mode])
    cs = _set_key(cs, "Encrypt", "yes")
    cs = _set_key(cs, "TrustServerCertificate", "no")
    return _set_key(cs, "Database", database)


class _Cursor(Protocol):
    """Minimal DB-API 2.0 cursor protocol."""

    description: list[tuple[str, Any]]

    def execute(self, sql: str, params: Sequence[Any] = ...) -> None: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...

    rowcount: int


@runtime_checkable
class _Connection(Protocol):
    """Minimal DB-API 2.0 connection protocol."""

    def cursor(self) -> _Cursor: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...


def _is_auth_error(exc: BaseException) -> bool:
    """Return True if *exc* looks like an Entra / login-failure error."""
    msg = str(exc).lower()
    return any(fragment in msg for fragment in _AUTH_ERROR_FRAGMENTS)


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


class FabricSqlClient:
    """Async MSSQL client with one cached connection per (workspace, database).

    All blocking driver calls are dispatched to a thread pool via
    ``asyncio.to_thread`` so they never block the event loop.

    Usage::

        async with FabricSqlClient(mode=CredentialMode.DEFAULT) as client:
            rows = await client.execute(target, "SELECT TOP 10 * FROM dbo.t")
    """

    def __init__(self, *, mode: CredentialMode = CredentialMode.DEFAULT) -> None:
        self._mode = mode
        # Cache: (workspace_id, database) → connection
        self._cache: dict[tuple[str, str], _Connection] = {}
        # Per-key locks to prevent concurrent stampedes
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # Global lock protecting _locks dict itself
        self._meta_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_lock(self, key: tuple[str, str]) -> asyncio.Lock:
        """Return (creating if necessary) the per-key asyncio.Lock."""
        async with self._meta_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    async def _get_connection(self, target: SqlTarget) -> _Connection:
        """Return a cached connection, opening one if necessary."""
        key = (target.workspace_id, target.database)
        lock = await self._get_lock(key)

        async with lock:
            if key not in self._cache:
                cs = _augment_connection_string(
                    target.connection_string, target.database, self._mode
                )
                try:
                    conn: _Connection = await asyncio.to_thread(_get_mssql().connect, cs)
                except Exception as exc:
                    if _is_auth_error(exc):
                        raise AuthError(str(exc)) from exc
                    raise
                self._cache[key] = conn
            return self._cache[key]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        target: SqlTarget,
        sql: str,
        params: Sequence[Any] = (),
    ) -> list[dict[str, Any]]:
        """Execute a SELECT (or any statement that returns rows).

        Args:
            target: The warehouse to connect to.
            sql: The SQL statement to execute.
            params: Optional sequence of parameters bound to ``?`` placeholders.

        Returns:
            A list of dicts mapping column names to row values.

        Raises:
            AuthError: If the driver raises an Entra authentication error.
        """
        conn = await self._get_connection(target)

        def _run() -> list[dict[str, Any]]:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            description: list[tuple[str, Any]] = cursor.description or []
            columns = [col[0] for col in description]
            rows: list[tuple[Any, ...]] = cursor.fetchall() or []
            return [dict(zip(columns, row, strict=False)) for row in rows]

        return await asyncio.to_thread(_run)

    async def execute_nonquery(
        self,
        target: SqlTarget,
        sql: str,
        params: Sequence[Any] = (),
    ) -> int:
        """Execute a DML/DDL statement that does not return rows.

        Commits the transaction implicitly after execution.

        Args:
            target: The warehouse to connect to.
            sql: The SQL statement to execute.
            params: Optional sequence of parameters bound to ``?`` placeholders.

        Returns:
            The number of affected rows (``cursor.rowcount``).

        Raises:
            AuthError: If the driver raises an Entra authentication error.
        """
        conn = await self._get_connection(target)

        def _run() -> int:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rowcount: int = cursor.rowcount
            conn.commit()
            return rowcount

        return await asyncio.to_thread(_run)

    async def close(self) -> None:
        """Close all cached connections and clear the cache."""
        async with self._meta_lock:
            keys = list(self._cache.keys())

        for key in keys:
            lock = await self._get_lock(key)
            async with lock:
                conn = self._cache.pop(key, None)
                if conn is not None:
                    await asyncio.to_thread(conn.close)

    async def __aenter__(self) -> FabricSqlClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
