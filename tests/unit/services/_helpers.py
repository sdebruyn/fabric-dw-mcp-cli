"""Shared test helpers for tests/unit/services/.

These helpers are *not* pytest fixtures — they are plain module-level
callables imported directly by each service test module.  This avoids
duplicate definitions while preserving the calling convention used by
every test (``client = await _make_client()`` followed by
``async with client:``).

ODBC helpers
------------
``_make_target``, ``_make_conn``, and ``_make_conn_for_ddl`` are the
single canonical ODBC mock factories shared across *all* SQL-service test
modules (T08 fix).  Previously each module had its own near-identical copy;
centralising here ensures that improvements to the mock contract (e.g. adding
``nextset()`` return value or ``rowcount`` defaults) propagate everywhere.
"""

from __future__ import annotations

import time
from datetime import tzinfo
from unittest.mock import AsyncMock, MagicMock

from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential

from fabric_dw.http_client import FabricHttpClient

# A long-lived fake token shared across all service unit tests.  The
# ``expires_on`` is set 1 h into the future so token-refresh logic is
# not triggered during normal test runs.
_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)  # noqa: S106


def _make_credential(token: AccessToken = _FAKE_TOKEN) -> AsyncTokenCredential:
    """Return a mock ``AsyncTokenCredential`` that yields *token*."""
    cred = MagicMock(spec=AsyncTokenCredential)
    cred.get_token = AsyncMock(return_value=token)
    return cred


async def _make_client(rps: int = 100) -> FabricHttpClient:
    """Return a ``FabricHttpClient`` backed by a fake credential.

    The *rps* default is 100 (generous) so that rate-limiting does not
    slow down unit tests.  Pass a smaller value when testing throttling
    behaviour explicitly.
    """
    return FabricHttpClient(credential=_make_credential(), rps=rps)


# ---------------------------------------------------------------------------
# ODBC / SQL mock helpers (T08: single canonical copy for all SQL service tests)
# ---------------------------------------------------------------------------


def _make_target() -> MagicMock:
    """Return a mock :class:`fabric_dw.sql.SqlTarget`."""
    return MagicMock()


def _make_conn(
    rows: list[tuple[object, ...]],
    columns: list[str],
    *,
    rowcount: int = -1,
) -> MagicMock:
    """Return a mock DB-API connection whose cursor returns *rows* / *columns*.

    The cursor's ``nextset()`` returns ``False`` by default (single result set).
    *rowcount* defaults to ``-1`` (driver does not know the row count) so that
    tests which check rowcount fall back to ``len(rows)``.  Pass a positive
    value when testing DML/driver-reported row counts.
    """
    cursor = MagicMock()
    cursor.description = [(c, None) for c in columns] if columns else None
    cursor.fetchall.return_value = rows
    cursor.rowcount = rowcount
    cursor.nextset.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def _make_no_result_conn(*, rowcount: int = 1) -> MagicMock:
    """Return a mock DB-API connection for DML/DDL statements with no result set.

    ``cursor.description`` is ``None`` and ``fetchall`` returns ``[]``.
    *rowcount* defaults to ``1`` (typical for a single-row DML); pass ``0``
    for DDL or multi-row variants as needed.
    """
    cursor = MagicMock()
    cursor.description = None
    cursor.fetchall.return_value = []
    cursor.rowcount = rowcount
    cursor.nextset.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def _make_conn_for_ddl() -> MagicMock:
    """Return a mock DB-API connection for DDL statements (no result set).

    ``cursor.description`` is ``None`` and ``fetchall`` returns ``[]``.
    Alias for ``_make_no_result_conn(rowcount=0)`` retained for backward
    compatibility with the modules that already import it by name.
    """
    return _make_no_result_conn(rowcount=0)


class _NoOffsetTz(tzinfo):
    """Quasi-naive tzinfo: tzinfo is set but utcoffset() returns None.

    Used in tests to exercise the quasi-naive guard in coerce_to_utc, which
    treats such datetimes the same as naive ones (i.e. stamps them as UTC).
    """

    def utcoffset(self, _dt: object) -> None:
        return None

    def tzname(self, _dt: object) -> str:
        return "NoOffset"

    def dst(self, _dt: object) -> None:
        return None


class _FakeRow:
    """Non-tuple sequence that mimics ``mssql_python.row.Row`` for testing.

    ``mssql_python`` returns ``Row`` objects from ``fetchall()`` /
    ``fetchone()``.  They are iterable and index-accessible but are **not**
    ``tuple`` subclasses.  This class reproduces that contract so
    Row-normalisation tests are driver-independent.

    Shared here so every service test module imports the same definition
    instead of maintaining per-file copies.
    """

    def __init__(self, *values: object) -> None:
        self._values = values

    def __iter__(self):  # type: ignore[return]
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __getitem__(self, index: int) -> object:
        return self._values[index]
