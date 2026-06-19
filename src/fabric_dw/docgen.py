"""Code-driven reference documentation generator for fabric-dw.

This module introspects the live Click command tree and the MCP tool registry,
groups both surfaces by the authoritative ``DOMAIN_MAP`` from
:mod:`fabric_dw.telemetry_commands`, and renders a per-domain Markdown
reference page.

Public API
----------
- :func:`collect_cli_entries` — walk the Click tree; return a sorted list of
  ``(domain, path, summary)`` triples.
- :func:`collect_mcp_entries` — register all MCP tools; return a sorted list of
  ``(domain, name, summary)`` triples.
- :func:`render_reference` — produce the full Markdown string from both entry
  lists.
- :func:`main` — write the generated page to
  ``docs/reference/command-tool-reference.md`` (run via ``just gen-docs``).

Design notes
------------
- No network or filesystem side-effects in the collect/render functions — they
  are pure (modulo importing the live code tree).  Tests can call them and
  assert on the returned strings without touching disk.
- The generator FAILs loudly (raises :class:`ValueError`) when it encounters a
  CLI group or MCP tool whose top-level name has no entry in ``DOMAIN_MAP``.
  This keeps the map and the command/tool surface in sync; a forgotten
  ``DOMAIN_MAP`` update becomes a hard CI failure rather than a silent omission.
"""

from __future__ import annotations

import pathlib
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

__all__ = [
    "OUTPUT_PATH",
    "collect_cli_entries",
    "collect_mcp_entries",
    "main",
    "render_reference",
]


def _find_repo_root() -> pathlib.Path:
    """Walk up from this file's location to find the repo root.

    The repo root is identified as the directory containing both
    ``pyproject.toml`` and a ``docs/`` subdirectory.

    Raises:
        FileNotFoundError: When no repo root matching those criteria is found
            within the directory tree above this file.  This prevents silently
            writing the generated page into the wrong location (e.g. under
            site-packages after a wheel install).
    """
    candidate = pathlib.Path(__file__).resolve().parent
    for _ in range(20):  # guard against infinite loops on unusual filesystems
        if (candidate / "pyproject.toml").exists() and (candidate / "docs").is_dir():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    msg = (
        "Could not locate the repo root (a directory containing pyproject.toml and docs/) "
        f"while walking up from {pathlib.Path(__file__).resolve()}. "
        "Run `just gen-docs` from the repository root, or install the package in editable mode."
    )
    raise FileNotFoundError(msg)


OUTPUT_PATH = _find_repo_root() / "docs" / "reference" / "command-tool-reference.md"

_HEADER = """\
<!-- AUTO-GENERATED — do not edit by hand. Run `just gen-docs` to regenerate. -->

# CLI & MCP tool reference

This page is generated from the live code tree.  Every CLI command and MCP
tool is listed here, grouped by functional domain, with a one-line summary.

For full option details see the [CLI reference](../cli.md) and for full MCP
tool descriptions see [MCP tools](../mcp-tools.md).
"""

# ---------------------------------------------------------------------------
# CLI introspection
# ---------------------------------------------------------------------------


def collect_cli_entries() -> list[tuple[str, str, str]]:
    """Walk the Click command tree and return ``(domain, path, summary)`` triples.

    The ``path`` is the full space-separated command path as it would be typed
    on the command line (e.g. ``tables read`` or ``config set workspace``).
    The ``summary`` is the one-line help string for the leaf command.

    Hidden commands are skipped.

    Raises:
        ValueError: When a top-level CLI group name has no entry in
            :data:`~fabric_dw.telemetry_commands.DOMAIN_MAP`.  This ensures
            the map stays in sync with the command surface.
    """
    import click  # noqa: PLC0415

    from fabric_dw.cli._main import cli  # noqa: PLC0415
    from fabric_dw.telemetry_commands import DOMAIN_MAP  # noqa: PLC0415

    entries: list[tuple[str, str, str]] = []

    def _walk(cmd: Any, path_parts: list[str]) -> None:  # noqa: ANN401
        if getattr(cmd, "hidden", False):
            return

        if isinstance(cmd, click.Group):
            top_name = path_parts[0] if path_parts else ""
            # Validate domain mapping for top-level groups only.
            if len(path_parts) == 1 and top_name not in DOMAIN_MAP:
                msg = (
                    f"CLI group {top_name!r} has no entry in DOMAIN_MAP. "
                    "Add it to fabric_dw.telemetry_commands.DOMAIN_MAP before regenerating."
                )
                raise ValueError(msg)
            for sub_name, sub_cmd in sorted(cmd.commands.items()):  # type: ignore[attr-defined]
                _walk(sub_cmd, [*path_parts, sub_name])
        else:
            # Leaf command — record it.
            top_name = path_parts[0] if path_parts else ""
            domain = DOMAIN_MAP.get(top_name, "")
            if not domain:
                msg = (
                    f"CLI command group {top_name!r} has no entry in DOMAIN_MAP. "
                    "Add it to fabric_dw.telemetry_commands.DOMAIN_MAP before regenerating."
                )
                raise ValueError(msg)
            path_str = " ".join(path_parts)
            summary = cmd.get_short_help_str(limit=120)
            if not summary and cmd.help:
                summary = cmd.help.splitlines()[0].strip()
            entries.append((domain, path_str, summary))

    for group_name, group_cmd in sorted(cli.commands.items()):
        _walk(group_cmd, [group_name])

    return sorted(entries)


# ---------------------------------------------------------------------------
# MCP tool introspection
# ---------------------------------------------------------------------------


def _build_mcp_server() -> FastMCP:
    """Build and return a fresh FastMCP instance with all tools registered."""
    from mcp.server.fastmcp import FastMCP as _FastMCP  # noqa: PLC0415

    from fabric_dw.mcp.tools import register_all  # noqa: PLC0415

    mcp = _FastMCP("docgen-introspect")
    register_all(mcp)
    return mcp


def collect_mcp_entries(_mcp: FastMCP | None = None) -> list[tuple[str, str, str]]:
    """Register all MCP tools against a fresh server and return ``(domain, name, summary)`` triples.

    Tools are registered via :func:`~fabric_dw.mcp.tools.register_all` against
    a fresh :class:`~mcp.server.fastmcp.FastMCP` instance (no lifespan, no
    network calls).  The registered tool list is obtained synchronously via
    ``mcp._tool_manager.list_tools()`` to avoid any async dependency.

    The ``summary`` is the first line of the tool's description.

    Args:
        _mcp: Optional pre-built FastMCP instance (for testing only).  When
            ``None`` (default), a fresh instance is built and all tools are
            registered via :func:`_build_mcp_server`.

    Raises:
        ValueError: When an MCP tool name has no entry in
            :data:`~fabric_dw.telemetry_commands.DOMAIN_MAP`.
    """
    from fabric_dw.telemetry_commands import DOMAIN_MAP  # noqa: PLC0415

    mcp = _mcp if _mcp is not None else _build_mcp_server()

    tools = mcp._tool_manager.list_tools()

    entries: list[tuple[str, str, str]] = []
    for tool in tools:
        name = tool.name
        domain = DOMAIN_MAP.get(name)
        if domain is None:
            msg = (
                f"MCP tool {name!r} has no entry in DOMAIN_MAP. "
                "Add it to fabric_dw.telemetry_commands.DOMAIN_MAP before regenerating."
            )
            raise ValueError(msg)
        description = tool.description or ""
        summary = description.splitlines()[0].strip() if description else ""
        entries.append((domain, name, summary))

    return sorted(entries)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Human-friendly display labels for each domain slug.
# Every domain that appears in DOMAIN_MAP values (and in _KNOWN_DOMAINS from
# telemetry_commands) MUST have an explicit entry here.  The completeness guard
# below raises ValueError at import time for any missing entry.
_DOMAIN_LABELS: dict[str, str] = {
    "workspaces": "Workspaces",
    "warehouses": "Warehouses",
    "sql_endpoints": "SQL Analytics Endpoints",
    "sql": "SQL execution",
    "tables": "Tables",
    "views": "Views",
    "procedures": "Stored procedures",
    "schemas": "Schemas",
    "statistics": "Statistics",
    "functions": "Functions",
    "snapshots": "Snapshots",
    "restore_points": "Restore points",
    "audit": "Audit",
    "queries": "Queries",
    "sql_pools": "SQL Pools",
    "dbt": "dbt integration",
    "cache": "Cache",
    "config": "Configuration",
    "completion": "Shell completion",
    "settings": "Server-side settings",
}


def _assert_domain_labels_complete() -> None:
    """Raise ValueError if any known domain is missing from ``_DOMAIN_LABELS``.

    This guard runs once at module import time.  It ensures that adding a new
    domain to ``_KNOWN_DOMAINS`` (in :mod:`fabric_dw.telemetry_commands`) without
    adding a corresponding entry to ``_DOMAIN_LABELS`` is a hard failure rather
    than a silent wrong label in the generated page.
    """
    from fabric_dw.telemetry_commands import _KNOWN_DOMAINS  # noqa: PLC0415

    missing = sorted(_KNOWN_DOMAINS - set(_DOMAIN_LABELS))
    if missing:
        msg = (
            "The following domains are listed in _KNOWN_DOMAINS but have no entry in "
            f"_DOMAIN_LABELS in fabric_dw.docgen: {missing}. "
            "Add a human-friendly label for each missing domain to _DOMAIN_LABELS."
        )
        raise ValueError(msg)


_assert_domain_labels_complete()


def render_reference(
    cli_entries: list[tuple[str, str, str]],
    mcp_entries: list[tuple[str, str, str]],
) -> str:
    """Render the per-domain CLI + MCP reference as a Markdown string.

    Args:
        cli_entries: Output of :func:`collect_cli_entries`.
        mcp_entries: Output of :func:`collect_mcp_entries`.

    Returns:
        A complete Markdown document string (including the auto-generated
        header notice).
    """
    # Group entries by domain.
    cli_by_domain: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for domain, path, summary in cli_entries:
        cli_by_domain[domain].append((path, summary))

    mcp_by_domain: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for domain, name, summary in mcp_entries:
        mcp_by_domain[domain].append((name, summary))

    # Collect all domains in a stable order: known-domain order first,
    # then any extras sorted alphabetically.
    known_order = list(_DOMAIN_LABELS)
    all_domains = sorted(
        set(cli_by_domain) | set(mcp_by_domain),
        key=lambda d: (known_order.index(d) if d in known_order else len(known_order), d),
    )

    parts: list[str] = [_HEADER]

    for domain in all_domains:
        label = _DOMAIN_LABELS.get(domain, domain.replace("_", " ").title())
        parts.append(f"\n## {label}\n")

        cli_rows = cli_by_domain.get(domain, [])
        if cli_rows:
            parts.append("### CLI commands\n")
            parts.append("| Command | Summary |")
            parts.append("| ------- | ------- |")
            for path, summary in sorted(cli_rows):
                escaped_summary = summary.replace("|", "\\|")
                parts.append(f"| `fdw {path}` | {escaped_summary} |")
            parts.append("")

        mcp_rows = mcp_by_domain.get(domain, [])
        if mcp_rows:
            parts.append("### MCP tools\n")
            parts.append("| Tool | Summary |")
            parts.append("| ---- | ------- |")
            for name, summary in sorted(mcp_rows):
                escaped_summary = summary.replace("|", "\\|")
                parts.append(f"| `{name}` | {escaped_summary} |")
            parts.append("")

    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Generate the reference page and write it to ``docs/reference/command-tool-reference.md``."""
    cli_entries = collect_cli_entries()
    mcp_entries = collect_mcp_entries()
    content = render_reference(cli_entries, mcp_entries)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    print(f"Written {len(content)} bytes to {OUTPUT_PATH}")  # noqa: T201


if __name__ == "__main__":
    main()
