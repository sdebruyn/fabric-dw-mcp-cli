"""Structured logging helpers for fabric_dw.

Provides a JSON-emitting stdlib logging setup and utilities to redact
sensitive values (Bearer tokens, SQL secrets, SAS URLs) before logging.
"""

from __future__ import annotations

import json
import logging
import re
import time

__all__ = [
    "redact_auth_header",
    "redact_sql",
    "setup_logging",
]

# Build the set of "standard" LogRecord attribute names by inspecting a fresh
# instance, then add the keys that are added during formatting.
_STANDARD_LOGRECORD_ATTRS: frozenset[str] = frozenset(
    logging.LogRecord(
        name="",
        level=logging.DEBUG,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__.keys()
) | {"message", "asctime"}


# Only "level" and "time" need guarding here: they are core payload keys that
# are NOT in _STANDARD_LOGRECORD_ATTRS, so they CAN arrive via extra={}.
# "name" and "msg" are already in _STANDARD_LOGRECORD_ATTRS and are stripped
# from extras before this check runs — guarding them would be dead code.
_CORE_PAYLOAD_KEYS: frozenset[str] = frozenset({"level", "time"})


class _JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter.

    Each record is emitted as a single-line JSON object with the keys:
    ``level``, ``name``, ``msg``, and ``time`` (ISO-8601 UTC).

    Any extra fields passed via ``extra={...}`` to the logging call are
    merged into the payload.  If an extra field's name collides with a core
    key it is stored under the prefix ``extra_<name>`` so that the core value
    is preserved and the extra value is never silently lost (C12).  Values that
    are not JSON-serialisable are coerced to ``str``.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Use the already-formatted message (handles % interpolation)
        message = record.getMessage()
        payload: dict[str, object] = {
            "level": record.levelname,
            "name": record.name,
            "msg": message,
            "time": self.formatTime(record, self.datefmt),
        }

        # Merge extra= fields; prefix colliding names to avoid silent data loss.
        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_LOGRECORD_ATTRS}
        for key, value in extras.items():
            out_key = f"extra_{key}" if key in _CORE_PAYLOAD_KEYS else key
            payload[out_key] = value

        return json.dumps(payload, default=str)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        # Always emit UTC ISO-8601
        ct = time.gmtime(record.created)
        if datefmt:
            s = time.strftime(datefmt, ct)
        else:
            t = time.strftime("%Y-%m-%dT%H:%M:%S", ct)
            s = f"{t}.{int(record.msecs):03d}Z"
        return s


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the ``fabric_dw`` package logger with a JSON formatter.

    Scoped to the ``fabric_dw`` named logger so that the host application's
    root logger is not mutated and third-party loggers (azure-identity, httpx,
    etc.) cannot emit sensitive data through our handler (C11).

    Safe to call multiple times; each call replaces the existing handlers on
    the ``fabric_dw`` logger so that the level and formatter are always
    up-to-date.

    Args:
        level: Logging level (default: ``logging.INFO``).
    """
    pkg_logger = logging.getLogger("fabric_dw")
    pkg_logger.setLevel(level)
    # Prevent records from reaching the root logger (which may have its own
    # handlers configured by the host application).
    pkg_logger.propagate = False

    # Remove any existing handlers to avoid duplicate output on repeated calls.
    for handler in list(pkg_logger.handlers):
        pkg_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(_JsonFormatter())
    pkg_logger.addHandler(handler)


# ---------------------------------------------------------------------------
# SQL secret redaction
# ---------------------------------------------------------------------------

# Matches SECRET = '...' (case-insensitive).  The value may contain doubled
# single-quotes (the _sq() escaping used by load.py), so we match a quoted
# string that may contain '' (two consecutive single-quotes) inside.
_RE_SQL_SECRET: re.Pattern[str] = re.compile(
    r"(SECRET\s*=\s*')" r"(?:[^']|'')*" r"'",
    re.IGNORECASE,
)

# Matches any URL scheme that can carry SAS/query secrets and contains a query
# string — replaces ?... with ?***.  Covers https://, http://, and Azure blob/
# dfs/OneLake schemes (abfss://, abfs://, wasbs://, wasb://) that can also
# appear inside SQL statements such as COPY INTO.
_RE_SAS_URL: re.Pattern[str] = re.compile(
    r"((?:https?|abfss?|wasbs?)://[^\s'\"]*)\?[^\s'\"]*",
    re.IGNORECASE,
)


def redact_sql(sql: str) -> str:
    """Return *sql* with embedded secrets replaced by ``***``.

    Two categories of secrets are redacted:

    1. **SQL credential literals** — ``SECRET = '<value>'`` (case-insensitive).
       The ``<value>`` is replaced with ``***``, giving ``SECRET = '***'``.
       The ``_sq()`` helper in :mod:`fabric_dw.services.load` escapes
       single-quotes by doubling them (``''``), so the regex handles values
       that contain ``''`` inside the quoted string.

    2. **SAS URL query strings** — any URL with a query string embedded in the
       SQL has the ``?<query-string>`` replaced with ``?***``.  Covers
       ``https://``, ``http://``, and Azure blob/dfs/OneLake schemes
       (``abfss://``, ``abfs://``, ``wasbs://``, ``wasb://``).  This masks
       ``sig=``, ``sv=``, and all other SAS parameters without enumerating
       them individually.

    The function is intentionally conservative (over-redacts rather than
    leaks).  It must be applied to the SQL string before emitting any log
    record so that no credential ever reaches a log sink.

    Args:
        sql: The raw SQL statement string.

    Returns:
        A copy of *sql* with secrets redacted.  The original string is not
        mutated.
    """
    # Step 1: redact SECRET = '...' values; step 2: strip SAS query strings.
    return _RE_SAS_URL.sub(r"\1?***", _RE_SQL_SECRET.sub(r"\g<1>***'", sql))


_SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "x-ms-authorization-auxiliary",
        "proxy-authorization",
        "cookie",
    }
)

# Headers for which the scheme word (e.g. "Bearer") is preserved in redacted output.
_SCHEME_PRESERVING_HEADERS: frozenset[str] = frozenset({"authorization", "proxy-authorization"})


def redact_auth_header(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with auth-bearing values replaced by ``***``.

    Sensitive headers (``Authorization``, ``Proxy-Authorization``,
    ``X-Ms-Authorization-Auxiliary``, ``Cookie``) are detected
    case-insensitively.  For ``Authorization``/``Proxy-Authorization``,
    the scheme word (e.g. ``Bearer``) is preserved so the token type is still
    visible in logs: ``Bearer ***``.  Other sensitive headers are replaced
    wholesale with ``***``.

    Args:
        headers: The original headers dict.  It is **not** mutated.

    Returns:
        A new dict with the same keys; sensitive credential values are redacted.
    """
    result: dict[str, str] = {}
    for key, value in headers.items():
        lower_key = key.lower()
        if lower_key in _SENSITIVE_HEADERS:
            # Preserve the scheme word (e.g. "Bearer") for token-bearing headers
            # so the token type is still visible in debug logs.
            parts = value.split(" ", 1)
            if len(parts) == 2 and lower_key in _SCHEME_PRESERVING_HEADERS:  # noqa: PLR2004
                result[key] = f"{parts[0]} ***"
            else:
                result[key] = "***"
        else:
            result[key] = value
    return result
