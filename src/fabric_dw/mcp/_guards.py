"""Security guards for the fabric-dw MCP server.

All gating logic lives here so it survives the planned server.py split.

Environment variables
---------------------
``FABRIC_MCP_READONLY``
    Set to ``1``, ``true``, or ``yes`` (case-insensitive) to restrict
    ``execute_sql`` to SELECT/WITH statements only and block all mutating
    tools (create, update, delete, rename, restore, kill, clear, reset, set,
    takeover).

``FABRIC_MCP_ALLOW_DESTRUCTIVE``
    Set to ``1``, ``true``, or ``yes`` to enable the permanently-destructive
    tools: ``delete_warehouse``, ``delete_snapshot``, ``delete_restore_point``,
    ``restore_warehouse_in_place``, ``delete_schema``, ``delete_table``,
    ``clear_table``, ``delete_sql_pool``.
    Defaults to **disabled** (secure-by-default).

``FABRIC_MCP_WORKSPACES``
    Comma-separated list of workspace names or GUIDs the MCP server is
    allowed to touch.  When unset every workspace is allowed.  Matching is
    case-insensitive and whitespace-trimmed.

``FABRIC_MCP_ALLOW_REMOTE``
    Set to ``1``, ``true``, or ``yes`` to allow the HTTP transport to bind on
    a non-loopback address.  Without this flag ``run()`` exits immediately if
    ``--host`` is not 127.0.0.1, ::1, or localhost.  When the flag is set a
    prominent WARNING is logged reminding operators to front the transport with
    an authenticating reverse proxy.
"""

from __future__ import annotations

import os
import re

from mcp.server.fastmcp.exceptions import ToolError

__all__ = [
    "assert_destructive_allowed",
    "assert_readonly_sql",
    "assert_workspace_allowed",
    "assert_writes_allowed",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TRUTHY = frozenset({"1", "true", "yes"})


def _env_flag(name: str) -> bool:
    """Return True when *name* is set to a truthy value (case-insensitive)."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# SQL classifier
# ---------------------------------------------------------------------------

# Single block-comment pass (non-greedy, no nesting).
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Line comment: -- to end of line.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")

# String literal: 'content' with '' as escape for single quote.
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")

# Bracket-quoted identifier: [name] with ]] as escape.
_BRACKET_IDENT_RE = re.compile(r"\[(?:[^\]]|\]\])*\]")

# Double-quoted identifier: "name" with "" as escape.
_DQUOTE_IDENT_RE = re.compile(r'"(?:[^"]|"")*"')

# Tokens that must never appear in a read-only statement (case-insensitive).
_FORBIDDEN_TOKENS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "INTO",
        "EXEC",
        "EXECUTE",
        "DROP",
        "ALTER",
        "CREATE",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "DENY",
        "KILL",
        "BACKUP",
        "RESTORE",
        "OPENROWSET",
        "OPENQUERY",
        "WRITETEXT",
        "UPDATETEXT",
        "SP_EXECUTESQL",
        "XP_CMDSHELL",
        # DoS / context-switch tokens — T-SQL batches don't require semicolons,
        # so these can appear after a newline following a valid SELECT and still
        # execute.  WAITFOR can hang a connection for hours; USE switches the
        # database context; SHUTDOWN, RECONFIGURE, and DBCC are admin-only
        # commands with no place in a read-only query.
        "WAITFOR",
        "USE",
        "SHUTDOWN",
        "RECONFIGURE",
        "DBCC",
    }
)

_ALLOWED_FIRST_TOKENS = frozenset({"SELECT", "WITH"})

# Simple word-token splitter (sequences of word characters).
_TOKEN_RE = re.compile(r"\w+")


def _strip_block_comments(sql: str) -> str:
    """Iteratively remove C-style block comments until the text is stable.

    Nested or malformed comments such as ``/* /* */ payload */`` are handled
    by repeating the substitution until no further change occurs.  Any residual
    ``/*`` or ``*/`` after stabilisation indicates unbalanced delimiters and
    causes the caller to reject the statement.
    """
    prev = None
    while prev != sql:
        prev = sql
        sql = _BLOCK_COMMENT_RE.sub(" ", sql)
    return sql


def _sanitise(statement: str) -> str:
    """Return a sanitised copy of *statement* suitable for token inspection.

    Steps (in order):
    1. Iteratively strip block comments.
    2. Strip line comments (``--`` to EOL).
    3. Replace string literals with ``''`` so semicolons/keywords inside
       strings do not trigger false positives.
    4. Replace bracket-quoted identifiers (``[delete]``) with ``[x]``.
    5. Replace double-quoted identifiers with ``[x]``.
    """
    text = _strip_block_comments(statement)
    text = _LINE_COMMENT_RE.sub(" ", text)
    text = _STRING_LITERAL_RE.sub("''", text)
    text = _BRACKET_IDENT_RE.sub("[x]", text)
    return _DQUOTE_IDENT_RE.sub("[x]", text)


def assert_readonly_sql(statement: str) -> None:
    """Raise :class:`ToolError` when *statement* is not allowed in read-only mode.

    Called only when ``FABRIC_MCP_READONLY`` is truthy.

    Design
    ------
    The classifier is **conservative-by-design**: it rejects anything it cannot
    prove is a plain read-only query, rather than trying to exhaustively parse
    T-SQL.  Legitimate bracket-quoted identifiers that collide with forbidden
    keywords (e.g. ``SELECT [delete] FROM t``) are preserved because bracketed
    names are replaced with ``[x]`` before the token scan.

    Sanitisation pipeline
    ~~~~~~~~~~~~~~~~~~~~~
    1. Iteratively strip block comments (non-greedy sub loop until stable).
       Residual ``/*`` or ``*/`` → rejected ("unbalanced comment").
    2. Strip ``--`` line comments.
    3. Replace string literals ``'(?:[^']|'')*'`` → ``''`` (preserves
       semicolons inside strings from triggering the multi-statement check).
    4. Replace bracketed identifiers ``[…]`` → ``[x]``; double-quoted
       identifiers ``"…"`` likewise.

    Checks (all on the sanitised text)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    (a) First token must be ``SELECT`` or ``WITH``.
    (b) A ``;`` followed by any non-whitespace character is rejected as a
        multi-statement batch.
    (c) Any word token (case-insensitive) that matches a forbidden keyword
        (INSERT, UPDATE, DELETE, MERGE, INTO, EXEC, EXECUTE, DROP, ALTER,
        CREATE, TRUNCATE, GRANT, REVOKE, DENY, KILL, BACKUP, RESTORE,
        OPENROWSET, OPENQUERY, WRITETEXT, UPDATETEXT, SP_EXECUTESQL,
        XP_CMDSHELL, WAITFOR, USE, SHUTDOWN, RECONFIGURE, DBCC) causes the
        statement to be rejected — regardless of where it appears.  This
        catches ``WITH x AS (SELECT 1) DELETE …``, ``SELECT * INTO backup FROM
        t``, and newline-separated DoS/context-switch payloads such as
        ``SELECT 1\nWAITFOR DELAY '99:0:0'`` or ``SELECT 1\nUSE master``.

    Args:
        statement: The raw SQL string supplied by the caller.

    Raises:
        ToolError: When the statement does not pass all read-only checks.
    """
    sanitised = _sanitise(statement)

    # Reject unbalanced block-comment delimiters left after stripping.
    if "/*" in sanitised or "*/" in sanitised:
        raise ToolError(
            "read-only mode (FABRIC_MCP_READONLY) blocks statements with unbalanced block comments"
        )

    sanitised = sanitised.strip()

    # Reject multi-statement batches: a ';' followed by non-whitespace.
    if re.search(r";\s*\S", sanitised):
        raise ToolError("read-only mode (FABRIC_MCP_READONLY) blocks multi-statement batches")

    tokens = _TOKEN_RE.findall(sanitised)
    first_token = tokens[0].upper() if tokens else ""

    if first_token not in _ALLOWED_FIRST_TOKENS:
        raise ToolError(
            f"read-only mode (FABRIC_MCP_READONLY) blocks non-SELECT statements "
            f"(got {first_token!r})"
        )

    # Scan every token for forbidden keywords.
    for tok in tokens:
        if tok.upper() in _FORBIDDEN_TOKENS:
            raise ToolError(
                f"read-only mode (FABRIC_MCP_READONLY) blocks statements containing "
                f"forbidden keyword {tok.upper()!r}"
            )


# ---------------------------------------------------------------------------
# Write guard
# ---------------------------------------------------------------------------

_READONLY_MSG = (
    "read-only mode (FABRIC_MCP_READONLY) is active; "
    "{tool_name!r} is a mutating tool and is disabled"
)


def assert_writes_allowed(tool_name: str) -> None:
    """Raise :class:`ToolError` when ``FABRIC_MCP_READONLY`` is truthy.

    Call this at the very start of every mutating MCP tool.

    Args:
        tool_name: The MCP tool name (used in the error message).

    Raises:
        ToolError: When ``FABRIC_MCP_READONLY`` is set to a truthy value.
    """
    if _env_flag("FABRIC_MCP_READONLY"):
        raise ToolError(_READONLY_MSG.format(tool_name=tool_name))


# ---------------------------------------------------------------------------
# Destructive-tool guard
# ---------------------------------------------------------------------------

_DESTRUCTIVE_MSG = "destructive tools are disabled; set FABRIC_MCP_ALLOW_DESTRUCTIVE=1 to enable"


def assert_destructive_allowed() -> None:
    """Raise :class:`ToolError` unless ``FABRIC_MCP_ALLOW_DESTRUCTIVE`` is truthy.

    Call this in every permanently-destructive tool (delete, clear, restore
    in-place, reset pools) **in addition to** :func:`assert_writes_allowed`.

    Raises:
        ToolError: When ``FABRIC_MCP_ALLOW_DESTRUCTIVE`` is not set to a
            truthy value.
    """
    if not _env_flag("FABRIC_MCP_ALLOW_DESTRUCTIVE"):
        raise ToolError(_DESTRUCTIVE_MSG)


# ---------------------------------------------------------------------------
# Workspace allowlist
# ---------------------------------------------------------------------------


def assert_workspace_allowed(workspace_arg: str, resolved_id: str | None = None) -> None:
    """Raise :class:`ToolError` when *workspace_arg* is not in the allowlist.

    When ``FABRIC_MCP_WORKSPACES`` is unset or empty every workspace is
    allowed.  When set, the raw argument **or** the resolved GUID must match
    an entry (case-insensitive, whitespace-trimmed).

    Args:
        workspace_arg: The raw workspace parameter as supplied by the caller
            (name or GUID).
        resolved_id: The resolved workspace GUID string, if already available.
            Pass ``None`` when the ID has not been resolved yet.

    Raises:
        ToolError: When the workspace is not in the allowlist.
    """
    raw = os.environ.get("FABRIC_MCP_WORKSPACES", "").strip()
    if not raw:
        return  # unset — everything allowed

    allowed = {entry.strip().lower() for entry in raw.split(",") if entry.strip()}
    if not allowed:
        return  # only commas / whitespace — treat as unset

    candidates = {workspace_arg.strip().lower()}
    if resolved_id is not None:
        candidates.add(resolved_id.strip().lower())

    if candidates.isdisjoint(allowed):
        raise ToolError(
            f"workspace {workspace_arg!r} is not in the FABRIC_MCP_WORKSPACES allowlist"
        )
