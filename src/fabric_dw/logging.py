"""Structured logging helpers for fabric_dw.

Provides a JSON-emitting stdlib logging setup and a utility to redact
sensitive values (Bearer tokens) from HTTP headers before logging.
"""

from __future__ import annotations

import json
import logging
import time

__all__ = [
    "redact_auth_header",
    "setup_logging",
]

_SENTINEL = object()

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


class _JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter.

    Each record is emitted as a single-line JSON object with the keys:
    ``level``, ``name``, ``msg``, and ``time`` (ISO-8601 UTC).

    Any extra fields passed via ``extra={...}`` to the logging call are
    merged into the payload without overwriting the core keys.  Values that
    are not JSON-serialisable are coerced to ``str``.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Use the already-formatted message (handles % interpolation)
        message = record.getMessage()
        payload = {
            "level": record.levelname,
            "name": record.name,
            "msg": message,
            "time": self.formatTime(record, self.datefmt),
        }

        # Merge extra= fields without overwriting core keys.
        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_LOGRECORD_ATTRS}
        for key, value in extras.items():
            if key not in payload:
                payload[key] = value

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
    """Configure the root logger with a JSON formatter.

    Safe to call multiple times; each call replaces the existing handlers on
    the root logger so that the level and formatter are always up-to-date.

    Args:
        level: Logging level for the root logger (default: ``logging.INFO``).
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers to avoid duplicate output
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)


def redact_auth_header(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with the Bearer token replaced by ``***``.

    Only the ``Authorization`` header is affected; the value is replaced with
    ``Bearer ***`` when the original starts with ``Bearer `` (case-sensitive).
    All other headers are copied as-is.

    Args:
        headers: The original headers dict.  It is **not** mutated.

    Returns:
        A new dict with the same keys; ``Authorization: Bearer <token>``
        becomes ``Authorization: Bearer ***``.
    """
    result = dict(headers)
    auth = result.get("Authorization", "")
    if auth.startswith("Bearer "):
        result["Authorization"] = "Bearer ***"
    return result
