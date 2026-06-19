"""Unit tests for the fabric_dw.docgen module.

Covers:
- Drift guard: the committed docs/reference/command-tool-reference.md must equal
  what the generator produces in memory.  Any command/tool addition that forgets
  to regenerate will fail here.
- Known-entry spot-checks: a selection of expected CLI paths and MCP tool names
  must appear in the collected entries.
- Domain-mapping sentinel: collect_cli_entries() and collect_mcp_entries() raise
  ValueError when a CLI group or MCP tool has no DOMAIN_MAP entry.
"""

from __future__ import annotations

import pytest

from fabric_dw.docgen import OUTPUT_PATH, collect_cli_entries, collect_mcp_entries, render_reference

# ---------------------------------------------------------------------------
# Smoke / spot-check tests
# ---------------------------------------------------------------------------


def test_collect_cli_entries_returns_list() -> None:
    entries = collect_cli_entries()
    assert isinstance(entries, list)
    assert len(entries) > 0


def test_collect_cli_known_command_present() -> None:
    entries = collect_cli_entries()
    paths = {path for _, path, _ in entries}
    assert "tables list" in paths, f"Expected 'tables list' in CLI paths, got: {sorted(paths)}"
    assert "warehouses list" in paths
    assert "schemas list" in paths


def test_collect_cli_entry_has_summary() -> None:
    entries = collect_cli_entries()
    for domain, path, summary in entries:
        assert domain, f"Empty domain for CLI path {path!r}"
        assert path, "Empty path"
        assert summary, f"Empty summary for CLI path {path!r}"


def test_collect_mcp_entries_returns_list() -> None:
    entries = collect_mcp_entries()
    assert isinstance(entries, list)
    assert len(entries) > 0


def test_collect_mcp_known_tool_present() -> None:
    entries = collect_mcp_entries()
    names = {name for _, name, _ in entries}
    missing = sorted({"list_warehouses", "execute_sql", "list_tables"} - names)
    assert not missing, f"MCP tools missing from entries: {missing}"
    assert "execute_sql" in names
    assert "list_tables" in names


def test_collect_mcp_entry_has_summary() -> None:
    entries = collect_mcp_entries()
    for domain, name, summary in entries:
        assert domain, f"Empty domain for MCP tool {name!r}"
        assert name, "Empty tool name"
        assert summary, f"Empty summary for MCP tool {name!r}"


# ---------------------------------------------------------------------------
# Domain-mapping sentinel (FAIL loudly on missing DOMAIN_MAP entry)
# ---------------------------------------------------------------------------


def test_cli_unknown_group_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """collect_cli_entries() must raise ValueError for a group not in DOMAIN_MAP."""
    import click  # noqa: PLC0415

    from fabric_dw.cli import _main as _cli_main  # noqa: PLC0415

    # Patch the cli.commands dict to include a fake group with no DOMAIN_MAP entry.
    original_commands = dict(_cli_main.cli.commands)
    fake_group = click.Group("__no_such_domain__")
    fake_group.add_command(click.Command("do-thing", callback=lambda: None, help="Test command."))
    patched = {**original_commands, "__no_such_domain__": fake_group}
    monkeypatch.setattr(_cli_main.cli, "commands", patched)

    with pytest.raises(ValueError, match="DOMAIN_MAP"):
        collect_cli_entries()


def test_mcp_unknown_tool_raises() -> None:
    """collect_mcp_entries() must raise ValueError for a tool not in DOMAIN_MAP."""
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415

    from fabric_dw.mcp.tools import register_all  # noqa: PLC0415

    # Build a server with all real tools plus one orphan with no DOMAIN_MAP entry.
    mcp = FastMCP("test-sentinel")
    register_all(mcp)

    async def _orphan_tool() -> str:
        """Orphan tool with no DOMAIN_MAP entry."""
        return "ok"

    mcp.add_tool(_orphan_tool, name="__orphan_tool_xyz__")

    # Pass the pre-built server directly via the _mcp parameter.
    with pytest.raises(ValueError, match="DOMAIN_MAP"):
        collect_mcp_entries(_mcp=mcp)


# ---------------------------------------------------------------------------
# Drift guard
# ---------------------------------------------------------------------------


def test_generated_page_matches_committed_file() -> None:
    """The committed docs page must equal what the generator produces in memory.

    If this test fails, run ``just gen-docs`` and commit the updated file.
    """
    cli_entries = collect_cli_entries()
    mcp_entries = collect_mcp_entries()
    generated = render_reference(cli_entries, mcp_entries)

    if not OUTPUT_PATH.exists():
        pytest.fail(
            f"Generated reference page not found at {OUTPUT_PATH}. "
            "Run `just gen-docs` to create it."
        )

    committed = OUTPUT_PATH.read_text(encoding="utf-8")
    assert generated == committed, (
        "The committed docs/reference/command-tool-reference.md is out of date. "
        "Run `just gen-docs` and commit the updated file."
    )
