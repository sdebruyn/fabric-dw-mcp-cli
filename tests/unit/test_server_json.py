"""Validate server.json (the MCP Registry manifest) and its Dockerfile ownership tie-in.

server.json registers `fabric-dw-mcp` in the official MCP Registry via the "oci" package
type (see docs/install.md#install-from-the-mcp-registry). A PyPI package entry cannot be
used here: the registry forces `identifier` to equal the published PyPI package name
(`fabric-dw`), and every registry-aware client inserts `identifier` as its own literal
command token, so the composed command always resolves to the `fabric-dw` CLI rather than
the `fabric-dw-mcp` script - there is no server.json encoding that separates the package
name from the script name for a self-fetching runtime like `uvx`. The OCI image sidesteps
this: its `ENTRYPOINT` already starts `fabric-dw-mcp` directly, and ownership is proven via
a Docker `LABEL` rather than identifier-matching. This test guards both halves of that tie:
server.json's shape and the Dockerfile LABEL staying in sync with it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_SERVER_JSON = json.loads((_REPO_ROOT / "server.json").read_text(encoding="utf-8"))
_DOCKERFILE = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

_EXPECTED_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
_EXPECTED_NAME = "io.github.sdebruyn/fabric-dw-mcp"


def test_schema_url() -> None:
    """server.json declares the pinned 2025-12-11 registry schema."""
    assert _SERVER_JSON["$schema"] == _EXPECTED_SCHEMA


def test_server_name() -> None:
    """The registered server name matches the reverse-DNS namespace decided in #969."""
    assert _SERVER_JSON["name"] == _EXPECTED_NAME


def test_version_present_and_not_a_range() -> None:
    """version is a concrete string (CI overwrites the placeholder from the release tag)."""
    version = _SERVER_JSON["version"]
    assert isinstance(version, str)
    assert version not in ("", "latest")


def test_single_oci_package() -> None:
    """packages[] holds exactly one 'oci' entry; no 'pypi' entry (see module docstring)."""
    packages = _SERVER_JSON["packages"]
    assert len(packages) == 1
    package = packages[0]
    assert package["registryType"] == "oci"


def test_oci_identifier_points_at_the_published_ghcr_image() -> None:
    """identifier targets the image .github/workflows/docker.yml actually pushes."""
    identifier = _SERVER_JSON["packages"][0]["identifier"]
    assert identifier.startswith("ghcr.io/sdebruyn/fabric-dw:")


def test_stdio_transport() -> None:
    """The MCP server communicates over stdio (no HTTP/SSE port to expose)."""
    assert _SERVER_JSON["packages"][0]["transport"]["type"] == "stdio"


def test_dockerfile_label_matches_server_json_name() -> None:
    """Dockerfile LABEL io.modelcontextprotocol.server.name must equal server.json's name.

    This is the OCI ownership-verification tie-in: the MCP Registry checks this label
    against server.json's `name`, not identifier-matching (unlike PyPI/NuGet/cargo, which
    check an `mcp-name:` string in the package README).
    """
    match = re.search(
        r'LABEL io\.modelcontextprotocol\.server\.name="([^"]+)"',
        _DOCKERFILE,
    )
    assert match, "Dockerfile is missing the io.modelcontextprotocol.server.name LABEL"
    assert match.group(1) == _EXPECTED_NAME
    assert match.group(1) == _SERVER_JSON["name"]
