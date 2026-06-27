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
    ``clear_table``, ``delete_sql_pool``, ``drop_view``, ``drop_procedure``,
    and ``refresh_sql_endpoint_metadata`` when ``recreate_tables=True``.
    Defaults to **disabled** (secure-by-default).

``FABRIC_MCP_WORKSPACES``
    Comma-separated list of workspace names or GUIDs the MCP server is
    allowed to touch.  This is the highest-priority layer of the 3-layer
    workspace allowlist knob; see :func:`resolve_workspace_allowlist` for
    the full resolution order.  An empty or whitespace-only value is treated
    as absent (falls through to the config layer).  When unset every
    workspace is allowed.  Matching is case-insensitive and whitespace-trimmed.

``FABRIC_MCP_ALLOW_REMOTE``
    Set to ``1``, ``true``, or ``yes`` to allow the HTTP transport to bind on
    a non-loopback address.  Without this flag ``run()`` exits immediately if
    ``--host`` is not 127.0.0.1, ::1, or localhost.  When the flag is set a
    prominent WARNING is logged reminding operators to front the transport with
    an authenticating reverse proxy.

Workspace allowlist — 3-layer resolution
-----------------------------------------
The workspace allowlist controls which workspaces the MCP server may operate
on.  It is resolved in the following priority order (highest first):

1. ``FABRIC_MCP_WORKSPACES`` env var (comma-separated list of names / GUIDs).
   An empty or whitespace-only value is treated as absent and falls through to
   the next layer.
2. ``[mcp] workspace_allowlist`` in ``config.toml`` (a TOML array of strings).
   An empty array ``[]`` is treated as absent (no restriction) — consistent
   with the unset case; it does NOT mean "block all workspaces".
3. Built-in default: no restriction (all workspaces allowed).

Use :func:`resolve_workspace_allowlist` to obtain the effective frozenset,
and :func:`workspace_allowlist_active` to check whether any restriction is in
effect without materialising the full set.
"""

from __future__ import annotations

import os
import re
import uuid as _uuid_mod
from collections.abc import Sequence

from mcp.server.fastmcp.exceptions import ToolError

__all__ = [
    "assert_destructive_allowed",
    "assert_readonly_sql",
    "assert_workspace_allowed",
    "assert_writes_allowed",
    "env_flag",
    "resolve_workspace_allowlist",
    "workspace_allowlist_active",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TRUTHY = frozenset({"1", "true", "yes"})


def env_flag(name: str) -> bool:
    """Return True when *name* is set to a truthy value (case-insensitive)."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


# Keep the private alias for backward compatibility during the transition period.
_env_flag = env_flag


def _canonicalize_entry(entry: str) -> str:
    """Return a canonical form of a workspace allowlist entry.

    GUID-shaped entries (braced, unhyphenated, ``urn:uuid:``, or canonical) are
    normalised to RFC-4122 lower-cased hyphenated form via :func:`uuid.UUID`.
    Non-GUID strings are lower-cased and trimmed as before.

    This prevents wrongful denials when a hand-edited ``config.toml`` entry uses
    a non-canonical GUID form such as ``{a1b2c3d4-...}`` or a 32-hex string.
    """
    stripped = entry.strip()
    if _looks_like_uuid(stripped):
        return str(_uuid_mod.UUID(stripped))
    return stripped.lower()


def resolve_workspace_allowlist(
    config_allowlist: Sequence[str] | None = None,
) -> frozenset[str] | None:
    """Resolve the effective workspace allowlist from 3 layers (env > config > no restriction).

    Resolution order (highest priority first):

    1. ``FABRIC_MCP_WORKSPACES`` env var (comma-separated names / GUIDs).
       An empty or whitespace-only value — including one that contains only
       commas and/or spaces — is treated as **absent** and falls through to
       the next layer.  This prevents an accidental ``FABRIC_MCP_WORKSPACES=``
       from silently blocking all workspaces.
    2. ``[mcp] workspace_allowlist`` from ``config.toml`` (passed in as
       *config_allowlist*).  An empty list ``[]`` is treated as **absent**
       (no restriction) rather than "block everything" — consistent with
       the unset case and the least-surprising interpretation of an empty
       list.
    3. Built-in default: ``None`` — no restriction, all workspaces allowed.

    GUID-shaped entries are canonicalised to RFC-4122 lower-cased hyphenated
    form so that ``{guid}``, ``urn:uuid:guid``, or unhyphenated 32-hex entries
    reliably match the resolved workspace ID returned by the Fabric API.

    Args:
        config_allowlist: The ``McpConfig.workspace_allowlist`` value loaded
            from ``config.toml``.  Pass ``None`` when no config is available
            or when the key is absent.

    Returns:
        A non-empty :class:`frozenset` of canonicalised, trimmed workspace
        names / GUIDs when a restriction is in effect, or ``None`` when
        every workspace is allowed.
    """
    # Layer 1: env var
    raw_env = os.environ.get("FABRIC_MCP_WORKSPACES", "").strip()
    if raw_env:
        env_entries = frozenset(
            _canonicalize_entry(entry) for entry in raw_env.split(",") if entry.strip()
        )
        if env_entries:
            return env_entries

    # Layer 2: config.toml
    if config_allowlist is not None:
        config_entries = frozenset(
            _canonicalize_entry(entry) for entry in config_allowlist if entry.strip()
        )
        if config_entries:
            return config_entries

    # Layer 3: no restriction
    return None


def workspace_allowlist_active(config_allowlist: Sequence[str] | None = None) -> bool:
    """Return True when the effective workspace allowlist imposes a restriction.

    Consults all 3 layers via :func:`resolve_workspace_allowlist`.  A value
    that consists solely of commas and/or whitespace in the env var, or an
    empty list in the config, is treated as absent (no restriction).

    This is the single source of truth for "is the allowlist active?" used by
    tools that need to guard ``all_workspaces=True`` requests.

    Args:
        config_allowlist: The ``McpConfig.workspace_allowlist`` value loaded
            from ``config.toml``.  Pass ``None`` when no config is available.
    """
    return resolve_workspace_allowlist(config_allowlist) is not None


# ---------------------------------------------------------------------------
# SQL classifier
# ---------------------------------------------------------------------------

# Single block-comment pass (non-greedy, no nesting).
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Line comment: -- to end of line.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")

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
    """Return a comment-stripped copy of *statement* for token inspection.

    Steps (in order):
    1. Iteratively strip block comments (space replacement, never empty string).
    2. Strip line comments (``--`` to EOL).

    String literals and quoted identifiers are intentionally NOT masked.
    All checks run on the raw comment-stripped text so that forbidden keywords
    are physically present in the scanned text regardless of the context they
    appear in.  This fails closed: a query that embeds a write keyword or a
    semicolon inside a string literal or quoted identifier will be rejected.
    Unset FABRIC_MCP_READONLY for such queries.
    """
    text = _strip_block_comments(statement)
    return _LINE_COMMENT_RE.sub(" ", text)


def assert_readonly_sql(statement: str) -> None:
    """Raise :class:`ToolError` when *statement* is not allowed in read-only mode.

    Called only when ``FABRIC_MCP_READONLY`` is truthy.

    Design
    ------
    The classifier is **conservative-by-design** (fail-closed): it rejects
    anything it cannot prove is a plain read-only query, rather than trying to
    exhaustively parse T-SQL.  All checks run on the raw comment-stripped text,
    so forbidden keywords are physically present in the scanned text regardless
    of whether they appear inside a string literal, a bracket-quoted identifier,
    or a double-quoted identifier.

    This means read-only mode may also reject otherwise-harmless reads that
    embed a write keyword or a ``;`` inside a string literal or quoted
    identifier (e.g. ``SELECT * FROM cdc WHERE op='DELETE'``,
    ``SELECT [delete] FROM t``).  This is by design.  Unset
    ``FABRIC_MCP_READONLY`` to run such queries.

    Sanitisation pipeline
    ~~~~~~~~~~~~~~~~~~~~~
    1. Iteratively strip block comments (non-greedy sub loop until stable,
       replacing with a space so adjacent tokens cannot merge into a keyword).
       Residual ``/*`` or ``*/`` after stabilisation → rejected.
    2. Strip ``--`` line comments.

    String literals and quoted identifiers are NOT masked.

    Checks (all on the comment-stripped raw text)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
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
        ``SELECT 1\\nWAITFOR DELAY '99:0:0'`` or ``SELECT 1\\nUSE master``.

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
    if env_flag("FABRIC_MCP_READONLY"):
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
    if not env_flag("FABRIC_MCP_ALLOW_DESTRUCTIVE"):
        raise ToolError(_DESTRUCTIVE_MSG)


# ---------------------------------------------------------------------------
# Workspace allowlist
# ---------------------------------------------------------------------------


def _looks_like_uuid(value: str) -> bool:
    """Return True when *value* is a valid UUID string."""
    try:
        _uuid_mod.UUID(value)
    except ValueError:
        return False
    else:
        return True


def assert_workspace_allowed(
    workspace_arg: str,
    resolved_id: str | None = None,
    config_allowlist: Sequence[str] | None = None,
) -> None:
    """Raise :class:`ToolError` when *workspace_arg* is not in the allowlist.

    The effective allowlist is resolved via the 3-layer stack (env > config >
    no restriction) using :func:`resolve_workspace_allowlist`.  When no
    restriction is in effect every workspace is allowed.  When a restriction
    is active, the raw argument **or** the resolved GUID must match an entry
    (case-insensitive, whitespace-trimmed).

    Pre-resolve vs post-resolve behaviour
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    This function is called twice per tool invocation: once before the
    workspace GUID is resolved (``resolved_id=None``), and once after
    (``resolved_id=<guid>``).

    When called *pre-resolve* and the allowlist contains only GUID-shaped
    entries, the raw name cannot be authoritatively matched — skipping early
    rejection here prevents false negatives for callers who supply a workspace
    name against a GUID-only allowlist.  The post-resolve call (with the
    actual GUID) is then the authoritative gate.

    Args:
        workspace_arg: The raw workspace parameter as supplied by the caller
            (name or GUID).
        resolved_id: The resolved workspace GUID string, if already available.
            Pass ``None`` when the ID has not been resolved yet.
        config_allowlist: The ``McpConfig.workspace_allowlist`` value loaded
            from ``config.toml``.  Pass ``None`` when no config is available.

    Raises:
        ToolError: When the workspace is not in the effective allowlist.
    """
    allowed = resolve_workspace_allowlist(config_allowlist)
    if allowed is None:
        return  # no restriction — every workspace is allowed

    # Build the candidate set using the same canonicalisation applied to the
    # allowlist entries so that non-canonical GUID forms match correctly.
    candidates = {_canonicalize_entry(workspace_arg)}
    if resolved_id is not None:
        candidates.add(_canonicalize_entry(resolved_id))
    else:
        # Pre-resolve: when the raw arg is a name (not a GUID) and the
        # allowlist contains at least one GUID-shaped entry, we cannot
        # determine whether this name resolves to a listed GUID.  Defer to
        # the post-resolve call to prevent false denials.  This covers both
        # the all-GUIDs case and the mixed (names + GUIDs) case.
        # If the allowlist has no GUID entries at all, a name can be
        # matched (or rejected) immediately by name comparison.
        arg_is_name = not _looks_like_uuid(workspace_arg.strip())
        allowlist_has_guids = any(_looks_like_uuid(e) for e in allowed)
        if arg_is_name and allowlist_has_guids:
            return  # cannot decide pre-resolve; post-resolve call will gate

    if candidates.isdisjoint(allowed):
        raise ToolError(f"workspace {workspace_arg!r} is not in the workspace allowlist")
