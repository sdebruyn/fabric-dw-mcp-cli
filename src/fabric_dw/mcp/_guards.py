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
    ``clear_table``, ``delete_sql_pool``, ``reset_sql_pools``.
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

# Pattern that matches a line comment and everything after it until EOL.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")

# Pattern that matches a C-style block comment (non-greedy).
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

_ALLOWED_FIRST_TOKENS = frozenset({"SELECT", "WITH"})


def _strip_comments(sql: str) -> str:
    """Return *sql* with line comments and block comments removed."""
    return _LINE_COMMENT_RE.sub(" ", _BLOCK_COMMENT_RE.sub(" ", sql))


def assert_readonly_sql(statement: str) -> None:
    """Raise :class:`ToolError` when *statement* is not allowed in read-only mode.

    Called only when ``FABRIC_MCP_READONLY`` is truthy.

    Rules
    -----
    1. After stripping leading whitespace and SQL comments the first keyword
       must be ``SELECT`` or ``WITH``.
    2. A semicolon anywhere *except* as an optional trailing terminator is
       treated as a multi-statement batch and rejected (pragmatic heuristic;
       avoids the complexity of full SQL parsing).

    Args:
        statement: The raw SQL string supplied by the caller.

    Raises:
        ToolError: When the statement is not a plain SELECT/WITH query or when
            it appears to be a multi-statement batch.
    """
    stripped = _strip_comments(statement).strip()

    # Reject multi-statement batches: a ';' anywhere except at the very end.
    inner = stripped.rstrip(";").rstrip()
    if ";" in inner:
        raise ToolError(  # noqa: TRY003
            "read-only mode (FABRIC_MCP_READONLY) blocks multi-statement batches"
        )

    first_token = stripped.split()[0].upper() if stripped.split() else ""
    if first_token not in _ALLOWED_FIRST_TOKENS:
        raise ToolError(  # noqa: TRY003
            f"read-only mode (FABRIC_MCP_READONLY) blocks non-SELECT statements "
            f"(got {first_token!r})"
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
        raise ToolError(  # noqa: TRY003
            f"workspace {workspace_arg!r} is not in the FABRIC_MCP_WORKSPACES allowlist"
        )
