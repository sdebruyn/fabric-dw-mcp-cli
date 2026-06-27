"""Server-side database settings for Fabric Data Warehouses.

Public API
----------
- :func:`get_settings`              — read current settings from ``sys.databases``.
- :func:`set_result_set_caching`    — toggle result-set caching via ``ALTER DATABASE``.
- :func:`set_time_travel_retention` — set time-travel retention period via ``ALTER DATABASE``.

SQL-level safety
----------------
``ALTER DATABASE CURRENT SET …`` statements are DDL that **cannot** be bound via
SQL parameters.  The two write functions therefore embed values as literals:

- ``set_result_set_caching`` embeds ``ON`` or ``OFF`` — derived from a Python
  :class:`bool`, never from arbitrary user input.
- ``set_time_travel_retention`` embeds an integer literal after range-validating
  the value as a Python :class:`int` (1-120).  Floats and strings are rejected
  before the literal is formed.

Autocommit
----------
Both ``ALTER DATABASE`` statements **must** run with ``autocommit=True``.
Using a regular (autocommit-off) connection raises:

    "ALTER DATABASE statement not allowed within multi-statement transaction."

The :func:`~fabric_dw.sql.run_query` helper accepts ``autocommit=True`` for
exactly this use case.

SQL Analytics Endpoint note
---------------------------
``get_settings`` is dual-target — both Data Warehouses and SQL Analytics
Endpoints respond to the ``sys.databases`` read query.

The ``ALTER DATABASE`` write operations (``set_result_set_caching`` and
``set_time_travel_retention``) are DWH-only.  SQL Analytics Endpoints are
read-only at the SQL layer; ``ALTER DATABASE`` is silently rejected or
produces unexpected results.  Both write functions guard against this via
``_assert_not_sql_endpoint`` and accept a ``kind`` parameter (defaulting to
:attr:`~fabric_dw.models.WarehouseKind.WAREHOUSE`) so that callers can pass
the resolved item kind.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from datetime import date as _date
from typing import cast

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import FabricError, ItemKindError
from fabric_dw.models import WarehouseKind, WarehouseSettings
from fabric_dw.services._helpers import coerce_to_utc
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "RETENTION_MAX",
    "RETENTION_MIN",
    "get_settings",
    "set_result_set_caching",
    "set_time_travel_retention",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Minimum allowed time-travel retention period in days.
RETENTION_MIN = 1
#: Maximum allowed time-travel retention period in days.
RETENTION_MAX = 120

# Backward-compatible aliases (private — kept for internal use only).
_RETENTION_MIN = RETENTION_MIN
_RETENTION_MAX = RETENTION_MAX

# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------

_SQL_ENDPOINT_ALTER_MSG = (
    "ALTER DATABASE is not supported on SQL Analytics Endpoints; "
    "use a Fabric Data Warehouse to change settings"
)


def _assert_not_sql_endpoint(kind: WarehouseKind) -> None:
    """Raise :class:`~fabric_dw.exceptions.ItemKindError` for SQL Endpoint items.

    Args:
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the resolved item.

    Raises:
        ItemKindError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
    """
    if kind == WarehouseKind.SQL_ENDPOINT:
        raise ItemKindError(_SQL_ENDPOINT_ALTER_MSG)


# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

_GET_SETTINGS_SQL = """\
SELECT
    name,
    is_result_set_caching_on,
    time_travel_retention_period_days,
    time_travel_retention_cutoff_date
FROM sys.databases
WHERE database_id = DB_ID();
"""

# ON/OFF are SQL keywords — embedded as a literal derived from a Python bool,
# never from arbitrary user input.
_SET_RSC_SQL_ON = "ALTER DATABASE CURRENT SET RESULT_SET_CACHING ON;"
_SET_RSC_SQL_OFF = "ALTER DATABASE CURRENT SET RESULT_SET_CACHING OFF;"

# <n> is an int literal (range-validated 1-120); not a SQL parameter.
_SET_RETENTION_SQL_TEMPLATE = "ALTER DATABASE CURRENT SET TIME_TRAVEL_RETENTION_PERIOD = {n} DAYS;"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_settings(cols: list[str], row: tuple[object, ...]) -> WarehouseSettings:
    """Build a :class:`~fabric_dw.models.WarehouseSettings` from a column-name list and a row."""
    data = dict(zip(cols, row, strict=True))
    raw_days = data["time_travel_retention_period_days"]
    raw_ts = data.get("time_travel_retention_cutoff_date")
    if isinstance(raw_ts, datetime):
        cutoff: datetime | None = coerce_to_utc(raw_ts)
    elif isinstance(raw_ts, _date):
        # Bare date (not datetime) — promote to midnight UTC.
        cutoff = datetime(raw_ts.year, raw_ts.month, raw_ts.day, tzinfo=UTC)
    else:
        cutoff = None
    return WarehouseSettings(
        database=str(data["name"]),
        result_set_caching=bool(data["is_result_set_caching_on"]),
        time_travel_retention_days=int(cast("int", raw_days)) if raw_days is not None else None,
        time_travel_retention_cutoff_date=cutoff,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_settings(
    target: SqlTarget,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> WarehouseSettings:
    """Return the current database settings for *target*.

    Reads from ``sys.databases`` using ``DB_ID()`` to scope the query to the
    connected database.

    Works on both Data Warehouses and SQL Analytics Endpoints.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.WarehouseSettings` instance reflecting
        the current settings.

    Raises:
        PermissionDeniedError: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
    """

    def _run() -> WarehouseSettings:
        cols, rows = run_query(target, _GET_SETTINGS_SQL, mode=mode)
        if not rows:
            msg = "Could not read warehouse settings: sys.databases returned no rows"
            raise FabricError(msg)
        return _row_to_settings(cols, rows[0])

    return await asyncio.to_thread(_run)


async def set_result_set_caching(
    target: SqlTarget,
    *,
    enabled: bool,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> WarehouseSettings:
    """Enable or disable result-set caching on *target*.

    Executes ``ALTER DATABASE CURRENT SET RESULT_SET_CACHING { ON | OFF }``
    with autocommit (required for ``ALTER DATABASE`` statements).

    Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).
    SQL Analytics Endpoints are rejected with
    :class:`~fabric_dw.exceptions.ItemKindError`.

    Args:
        target: The Data Warehouse to alter.
        enabled: ``True`` to enable result-set caching, ``False`` to disable it.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the resolved item.
            SQL Analytics Endpoints are rejected.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.WarehouseSettings` reflecting the settings
        *after* the change (fetched via a follow-up ``get_settings`` call).

    Raises:
        ItemKindError: If *kind* is
            :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        PermissionDeniedError: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
    """
    _assert_not_sql_endpoint(kind)
    ddl = _SET_RSC_SQL_ON if enabled else _SET_RSC_SQL_OFF

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)
    return await get_settings(target, mode=mode)


async def set_time_travel_retention(
    target: SqlTarget,
    days: int,
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> WarehouseSettings:
    """Set the time-travel retention period on *target*.

    Executes ``ALTER DATABASE CURRENT SET TIME_TRAVEL_RETENTION_PERIOD = <n> DAYS``
    with autocommit (required for ``ALTER DATABASE`` statements).

    Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).
    SQL Analytics Endpoints are rejected with
    :class:`~fabric_dw.exceptions.ItemKindError`.

    Args:
        target: The Data Warehouse to alter.
        days: Retention period in days.  Must be in the range 1-120 (inclusive).
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the resolved item.
            SQL Analytics Endpoints are rejected.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.WarehouseSettings` reflecting the settings
        *after* the change (fetched via a follow-up ``get_settings`` call).

    Raises:
        ItemKindError: If *kind* is
            :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If *days* is outside the valid range 1-120.
        PermissionDeniedError: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
    """
    _assert_not_sql_endpoint(kind)
    days_int = int(days)
    if not _RETENTION_MIN <= days_int <= _RETENTION_MAX:
        msg = (
            f"time_travel_retention_period_days must be between "
            f"{_RETENTION_MIN} and {_RETENTION_MAX}, got {days_int}"
        )
        raise ValueError(msg)

    ddl = _SET_RETENTION_SQL_TEMPLATE.format(n=days_int)

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)
    return await get_settings(target, mode=mode)
