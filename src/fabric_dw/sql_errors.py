"""Error classification helpers for the Microsoft Fabric Data Warehouse SQL driver.

This is a pure leaf module: its only production dependency is
:mod:`fabric_dw.exceptions` (plus the stdlib ``re`` module).  It contains no
pool, config, or auth state.

Public API
----------
- :func:`map_driver_error`              - classify a driver exception to a high-level error.
- :func:`_clean_driver_error_message`   - strip driver-noise prefix from an error message.
- :func:`_wrap_unmapped_driver_error`   - wrap an unclassified driver SQL error.
- :func:`is_transient_connection_error` - True when an exception is a retryable TDS drop.
- :func:`is_snapshot_not_ready_error`   - True when a snapshot DB is still provisioning.
- :func:`is_auth_failed_message`        - True when a message contains an auth-failure fragment.
- :func:`_is_connect_retryable`         - True when a connect-phase exception can be retried.
"""

from __future__ import annotations

import re

from fabric_dw.exceptions import AuthError, FabricServerError, NotFoundError, PermissionDeniedError

# ---------------------------------------------------------------------------
# Sentinel strings for error classification
# ---------------------------------------------------------------------------

# SQL permission-denial failures in driver error messages.
_PERMISSION_DENIED_FRAGMENTS = (
    "permission was denied",
    "denied the right to",
)

# Entra authentication failures in driver error messages.
# "could not login" covers the bare SQL Server 18456 message form
# ("Could not login (18456)") that does NOT embed "authentication failed".
_AUTH_FAILED_FRAGMENTS = ("authentication failed", "could not login")

# SQL Server native error numbers that indicate permission denied.
# 229: SELECT permission denied; 230: INSERT; 297: execute permission denied.
_PERMISSION_DENIED_ERROR_NUMBERS = frozenset({229, 230, 297})

# SQL Server native error number for authentication failure (login failed).
_AUTH_FAILED_ERROR_NUMBERS = frozenset({18456})

# SQL Server native error numbers that indicate a missing object.
# 208:  Invalid object name (table/view not found).
# 2812: Could not find stored procedure '<name>'.
# 3701: Cannot drop the <object type> '<name>' because it does not exist or you
#       do not have permission (DROP FUNCTION / DROP VIEW / DROP PROCEDURE on a
#       non-existent object).
_NOT_FOUND_ERROR_NUMBERS = frozenset({208, 2812, 3701})

# Message fragments that indicate a missing database object.
_NOT_FOUND_FRAGMENTS = (
    "invalid object name",
    "base table or view not found",
    # SQL Server 3701: "Cannot drop the function/procedure/view '<name>' because
    # it does not exist or you do not have permission."
    "cannot drop the",
)

# Fragments that indicate a freshly-created snapshot database has not yet
# finished provisioning at the SQL layer ("eventual consistency" lag).  The
# full error from the Fabric TDS endpoint reads:
#   "User does not have permission to alter database '<name>', the database
#    does not exist, or the database is not in a state that allows access checks."
# All three clauses are surfaced as a single PermissionDeniedError (the driver
# maps them via the permission-denied fragment path), so we detect them by
# matching the unique sub-phrase that distinguishes provisioning lag from a real
# permission denial.
_SNAPSHOT_NOT_READY_FRAGMENTS = ("not in a state that allows access checks",)

# Fragments that indicate a transient connection-level drop (TCP tear-down,
# server-side restart, or fabric warm-up).  These are safe to retry because
# the statement has NOT been executed on the server yet (connection failed).
# Keep this list tight - we must NOT retry real SQL or auth-config errors.
_TRANSIENT_FRAGMENTS = (
    # mssql_python / ODBC Driver 18 wording:
    "communication link failure",
    "connection was forcibly closed",
    "a transport-level error",
    # Covers the DDBC "TCP Provider: Error code <hex>" family, including the
    # connect/login timeout 0x102 (decimal 258) seen during a slow/warming-up
    # Fabric capacity (#972) — retryable because the connection never reached
    # the server, so no statement could have been executed.
    "tcp provider",
    # Generic socket/timeout seen during heavy transient:
    "connection timed out",
    "connection reset by peer",
    # NOTE: "database was not found" is intentionally NOT listed here.  The real
    # Fabric TDS driver embeds native error number 18456 alongside this message, so
    # map_driver_error() converts it to AuthError before is_transient_connection_error
    # is consulted - making a "database was not found" entry dead code in the
    # run_query / run_statements retry paths.  Including it would also incorrectly
    # retry a genuine wrong-database-name error for the full backoff window.
    # _wait_for_sql_readiness in tests/integration/conftest.py handles the warm-up
    # case correctly by inspecting the AuthError message directly.
)

# Regex to extract the native SQL Server error number from a DDBC error string.
# The ODBC driver embeds it in two forms:
#   - "Error: 18456" or "error 229"   - captured by the first alternative.
#   - "[SQL Server] ... (229)" where the parenthesised number is anchored to an
#     explicit SQL-Server/Msg/Error context word so that incidental numbers in
#     unrelated text (port numbers, byte counts, row counts) are not matched.
#
# Note: the second alternative deliberately reuses the word "Error" (distinct
# from "Error:" with the colon+space in alt-1) to match patterns like
# "Error (229)".  Since re.finditer processes left-to-right and the code
# returns on the first recognised number, first-match-wins is intentional -
# there is no ambiguity between the two alternatives in practice.
_NATIVE_ERROR_RE = re.compile(
    r"\b(?:Error:\s*|error\s+)(\d+)\b"
    r"|(?:SQL\s+Server|Msg|Error)\b[^(]*\((\d+)\)",
    re.IGNORECASE,
)

# Pattern that matches the driver noise prefix produced by mssql_python.
# Format: Driver Error: <short category>; DDBC Error: <sql server message>
# We strip everything up to and including the DDBC Error label so callers
# see only the meaningful SQL Server portion.
_DRIVER_NOISE_RE = re.compile(
    r"^Driver Error:[^;]*;\s*DDBC Error:\s*",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Error classification functions
# ---------------------------------------------------------------------------


def map_driver_error(exc: BaseException) -> Exception | None:
    """Return a mapped exception for known driver error categories, or ``None``.

    Matching strategy (in priority order):

    1. **Native SQL Server error numbers** - inspect ``exc.ddbc_error`` for
       embedded error numbers (e.g. ``Error: 229``, ``(18456)``).  This is the
       most reliable signal and survives locale / driver-version changes.
    2. **Message-fragment fallback** - scan the stringified exception for known
       English substrings.  Kept so that behaviour never regresses when error
       numbers are unavailable (e.g. mock exceptions in tests).

    Permission-denied is checked before auth-failure in both strategies so a
    message containing both fragments resolves to
    :class:`~fabric_dw.exceptions.PermissionDeniedError`.

    Args:
        exc: The raw exception raised by the driver.

    Returns:
        A :class:`~fabric_dw.exceptions.PermissionDeniedError`,
        :class:`~fabric_dw.exceptions.AuthError`, or
        :class:`~fabric_dw.exceptions.NotFoundError` instance if the error
        message matches a known fragment or error number, otherwise ``None``.
    """
    # --- Strategy 1: native error number (primary, locale-independent) ---
    ddbc_error = getattr(exc, "ddbc_error", None)
    if ddbc_error:
        for match in _NATIVE_ERROR_RE.finditer(str(ddbc_error)):
            raw_num = match.group(1) or match.group(2)
            if raw_num:
                err_num = int(raw_num)
                if err_num in _PERMISSION_DENIED_ERROR_NUMBERS:
                    return PermissionDeniedError(str(exc))
                if err_num in _AUTH_FAILED_ERROR_NUMBERS:
                    return AuthError(str(exc))
                if err_num in _NOT_FOUND_ERROR_NUMBERS:
                    return NotFoundError(str(exc))

    # --- Strategy 2: message-fragment fallback (locale-dependent, documented) ---
    msg = str(exc).lower()
    for cls, fragments in (
        (PermissionDeniedError, _PERMISSION_DENIED_FRAGMENTS),
        (AuthError, _AUTH_FAILED_FRAGMENTS),
        (NotFoundError, _NOT_FOUND_FRAGMENTS),
    ):
        if any(fragment in msg for fragment in fragments):
            return cls(str(exc))
    return None


def _clean_driver_error_message(msg: str) -> str:
    """Strip the mssql_python driver-noise prefix from *msg*.

    The driver wraps SQL Server errors with a prefix of the form::

        Driver Error: <short category>; DDBC Error: <SQL Server message>

    This helper returns the SQL Server message only.  If the prefix is not
    present the original *msg* is returned unchanged.

    Args:
        msg: The stringified driver exception message.

    Returns:
        The cleaned message, with driver-noise prefix removed when present.
    """
    return _DRIVER_NOISE_RE.sub("", msg, count=1)


def _wrap_unmapped_driver_error(exc: BaseException) -> FabricServerError | None:
    """Wrap a driver SQL error that was not classified by :func:`map_driver_error`.

    When the driver attaches a ``ddbc_error`` attribute to an exception it
    signals a genuine SQL Server error (e.g. "Invalid column name", syntax
    errors, constraint violations).  These are distinct from internal
    cursor-state errors (which carry no ``ddbc_error``) and from transient
    network errors.

    A ``ddbc_error`` present on an unclassified exception means
    :func:`map_driver_error` recognised no specific category (not a
    permission/auth/not-found error).  We surface it as a
    :class:`~fabric_dw.exceptions.FabricServerError` with a cleaned message
    so the CLI can catch it via ``except (ValueError, FabricError)`` and print
    a friendly error instead of a raw traceback.

    Args:
        exc: The raw exception raised by the driver.

    Returns:
        A :class:`~fabric_dw.exceptions.FabricServerError` when *exc* carries
        a ``ddbc_error`` attribute (unmapped driver SQL error), otherwise
        ``None`` (not a driver SQL error - let the caller re-raise as-is).
    """
    ddbc_error = getattr(exc, "ddbc_error", None)
    if not ddbc_error:
        return None
    # ddbc_error contains the SQL Server-level message without driver-noise prefix.
    # The `if not ddbc_error` guard above already handles the empty/falsy case, so
    # str(ddbc_error) is always non-empty here.
    return FabricServerError(str(ddbc_error).strip(), is_retriable=False)


def is_transient_connection_error(exc: BaseException) -> bool:
    """Return True when *exc* represents a retryable TDS connection-level drop.

    Matches only transport / warm-up errors, NOT auth failures or SQL errors.
    Used by :func:`~fabric_dw.sql.run_query` and
    :func:`~fabric_dw.sql.run_statements` to gate the small bounded retry loop
    that guards against transient Fabric TDS drops.

    Args:
        exc: The raw exception raised by the driver.

    Returns:
        ``True`` when the exception message matches a known transient fragment,
        ``False`` for all other errors (auth, permission, SQL syntax, etc.).
    """
    msg = str(exc).lower()
    return any(fragment in msg for fragment in _TRANSIENT_FRAGMENTS)


def is_snapshot_not_ready_error(exc: BaseException) -> bool:
    """Return True when *exc* indicates a snapshot DB is still provisioning.

    A freshly-created Fabric warehouse snapshot database is not immediately
    accessible at the SQL layer.  During the provisioning window the TDS
    endpoint returns a message of the form:

        "User does not have permission to alter database '<name>', the database
         does not exist, or the database is not in a state that allows access
         checks."

    This is surfaced as a :class:`~fabric_dw.exceptions.PermissionDeniedError`
    by :func:`map_driver_error` because the message contains the
    "permission was denied" / "permission" fragment.  However, retrying is
    safe here - the statement was rejected *before* it could execute, and once
    provisioning finishes the same ``ALTER DATABASE`` will succeed.

    Args:
        exc: The exception raised by the driver or by :func:`map_driver_error`.

    Returns:
        ``True`` when the message matches a known snapshot-not-ready fragment,
        ``False`` for all other errors.
    """
    msg = str(exc).lower()
    return any(fragment in msg for fragment in _SNAPSHOT_NOT_READY_FRAGMENTS)


def is_auth_failed_message(msg: str) -> bool:
    """Return True when *msg* contains a known Entra authentication-failure fragment.

    This is the public counterpart to the private :data:`_AUTH_FAILED_FRAGMENTS`
    tuple.  It centralises the check so that callers outside this module (e.g.
    integration test readiness probes) do not need to import the private constant.

    Matching is case-insensitive.  Covered fragments include:

    * ``"authentication failed"`` - the common Entra/ODBC wording.
    * ``"could not login"`` - the bare SQL Server 18456 form that does **not**
      embed the "authentication failed" substring (e.g. "Could not login (18456)").

    Args:
        msg: A string to test, typically ``str(exc)`` from a driver exception.

    Returns:
        ``True`` when *msg* contains at least one auth-failure fragment,
        ``False`` otherwise.
    """
    lower = msg.lower()
    return any(fragment in lower for fragment in _AUTH_FAILED_FRAGMENTS)


def _is_connect_retryable(exc: BaseException) -> bool:
    """Return True when *exc* is retryable on the connect/login path.

    In addition to the standard transient TDS transport errors, authentication
    failures (error 18456 / SQLSTATE 28000) are also treated as retryable here
    because a freshly-created or warming-up Fabric warehouse may reject the
    login with "authentication failed" / "could not login" until the TDS
    endpoint finishes provisioning - even when the credentials are correct.

    **Warm-up window**: :func:`~fabric_dw.sql._with_connect_retry` retries
    retryable errors for up to ``_CONNECT_RETRY_TIMEOUT_S`` seconds (~120 s by
    default), which is enough margin to cover observed Fabric warehouse warm-up
    durations (~60-90 s).

    **Trade-off**: a genuinely-wrong credential will now hang up to ~120 s
    before the AuthError is surfaced to the caller, because the retry loop
    cannot distinguish "wrong credential" from "warehouse still warming up".
    This is intentional and accepted: the warm-up case is far more common in
    production usage.

    Scope: this helper is ONLY used by :func:`~fabric_dw.sql._with_connect_retry`.
    It is NOT used in the execute-phase retry logic - auth errors there are still
    mapped to :class:`~fabric_dw.exceptions.AuthError` and raised immediately.
    """
    if is_transient_connection_error(exc):
        return True
    # Also retry auth-failed errors on the connect/login path.
    # Strategy 1: native error number (same approach as map_driver_error).
    ddbc_error = getattr(exc, "ddbc_error", None)
    if ddbc_error:
        for match in _NATIVE_ERROR_RE.finditer(str(ddbc_error)):
            raw_num = match.group(1) or match.group(2)
            if raw_num and int(raw_num) in _AUTH_FAILED_ERROR_NUMBERS:
                return True
    # Strategy 2: message-fragment fallback via the public helper.
    return is_auth_failed_message(str(exc))
