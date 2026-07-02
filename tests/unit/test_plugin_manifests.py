"""Tests for the plugin marketplace manifests (issue #968).

Validates the Claude Code and GitHub Copilot CLI plugin/marketplace manifests
stay internally consistent: the two marketplace.json mirrors are byte-for-byte
JSON-equal, every skills[] path resolves to a real SKILL.md with frontmatter,
mcpServers entries are well-formed, and any "version" fields present agree
with .claude-plugin/plugin.json (the single source of truth bumped by the
publish.yml sync-plugin-manifest job).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent

CLAUDE_MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
COPILOT_MARKETPLACE = REPO_ROOT / ".github" / "plugin" / "marketplace.json"
CLAUDE_PLUGIN = REPO_ROOT / ".claude-plugin" / "plugin.json"
COPILOT_PLUGIN = REPO_ROOT / ".github" / "plugin" / "plugin.json"

_MANIFEST_PATHS = [CLAUDE_MARKETPLACE, COPILOT_MARKETPLACE, CLAUDE_PLUGIN, COPILOT_PLUGIN]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _marketplace_plugin_entry(marketplace: dict[str, Any]) -> dict[str, Any]:
    plugins = marketplace["plugins"]
    assert len(plugins) == 1, "expected exactly one plugin entry in the marketplace"
    return plugins[0]


@pytest.mark.parametrize("path", _MANIFEST_PATHS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_manifest_parses_as_json(path: Path) -> None:
    """Every manifest is present and parses as JSON."""
    assert path.is_file(), f"expected manifest at {path}"
    _load_json(path)


def test_marketplace_mirrors_are_json_equal() -> None:
    """The Claude Code and Copilot CLI marketplace.json files describe the same plugin."""
    assert _load_json(CLAUDE_MARKETPLACE) == _load_json(COPILOT_MARKETPLACE)


def test_plugin_name_matches_across_manifests() -> None:
    """The marketplace plugin entry and both plugin.json manifests agree on "fabric-dw"."""
    marketplace = _load_json(CLAUDE_MARKETPLACE)
    entry = _marketplace_plugin_entry(marketplace)

    assert entry["name"] == "fabric-dw"
    assert _load_json(CLAUDE_PLUGIN)["name"] == "fabric-dw"
    assert _load_json(COPILOT_PLUGIN)["name"] == "fabric-dw"


def test_skills_paths_exist_and_have_valid_frontmatter() -> None:
    """Every skills[] entry resolves to a directory with a SKILL.md carrying name + description."""
    marketplace = _load_json(CLAUDE_MARKETPLACE)
    entry = _marketplace_plugin_entry(marketplace)
    skills = entry["skills"]

    assert skills, "expected at least one skill in the marketplace plugin entry"

    for skill_path in skills:
        skill_dir = REPO_ROOT / skill_path.removeprefix("./")
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.is_file(), f"missing SKILL.md for {skill_path!r} at {skill_md}"

        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---"), f"{skill_md} has no YAML frontmatter"
        _, frontmatter_raw, _ = text.split("---", 2)
        frontmatter = yaml.safe_load(frontmatter_raw)

        assert frontmatter.get("name"), f"{skill_md} frontmatter is missing a non-empty name"
        assert frontmatter.get("description"), (
            f"{skill_md} frontmatter is missing a non-empty description"
        )


@pytest.mark.parametrize(
    "path",
    [CLAUDE_MARKETPLACE, COPILOT_MARKETPLACE, CLAUDE_PLUGIN, COPILOT_PLUGIN],
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_mcp_servers_are_well_formed(path: Path) -> None:
    """Every mcpServers entry declares a command, and "stdio" is the only type used."""
    data = _load_json(path)
    mcp_servers = (
        _marketplace_plugin_entry(data)["mcpServers"] if "plugins" in data else data["mcpServers"]
    )

    assert mcp_servers, f"expected at least one mcpServers entry in {path}"
    for server_name, server in mcp_servers.items():
        assert isinstance(server, dict), f"mcpServers[{server_name!r}] in {path} is not an object"
        assert server.get("command"), f"mcpServers[{server_name!r}] in {path} has no command"
        if "type" in server:
            assert server["type"] == "stdio", (
                f"mcpServers[{server_name!r}] in {path} has unexpected type {server['type']!r}"
            )


def test_bundled_mcp_server_is_stable_not_dev() -> None:
    """The plugin bundles the stable @fabric-dw release, not the repo's dev .mcp.json config."""
    marketplace = _load_json(CLAUDE_MARKETPLACE)
    entry = _marketplace_plugin_entry(marketplace)
    args = entry["mcpServers"]["fabric-dw"]["args"]

    assert args == ["--from", "fabric-dw", "fabric-dw-mcp"], (
        f"expected the stable uvx invocation, got {args!r} "
        "(the dev .mcp.json uses '--prerelease allow' and 'fabric-dw@latest' - do not copy that)"
    )


def test_version_fields_are_mutually_consistent() -> None:
    """Any manifest that carries a "version" field agrees with .claude-plugin/plugin.json."""
    source_of_truth = _load_json(CLAUDE_PLUGIN)["version"]
    assert source_of_truth, ".claude-plugin/plugin.json must declare a non-empty version"

    for path in _MANIFEST_PATHS:
        data = _load_json(path)
        candidates: list[Any] = [data.get("version")]
        if "plugins" in data:
            candidates.append(_marketplace_plugin_entry(data).get("version"))
        if "metadata" in data:
            candidates.append(data["metadata"].get("version"))

        for version in candidates:
            if version is not None:
                assert version == source_of_truth, (
                    f"{path} declares version {version!r}, expected {source_of_truth!r} "
                    "to match .claude-plugin/plugin.json"
                )
