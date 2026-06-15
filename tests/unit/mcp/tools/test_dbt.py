"""Unit tests for fabric_dw.mcp.tools.dbt — generate_dbt_profile tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import yaml
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.services.dbt_scaffold import DbtAuthMode
from tests.unit.mcp.conftest import WH_NAME, WS_NAME, make_item_entry

# ---------------------------------------------------------------------------
# generate_dbt_profile — happy paths
# ---------------------------------------------------------------------------


async def test_generate_dbt_profile_returns_dict(
    mock_ctx,  # noqa: ARG001
    ctx_patch,
) -> None:
    """generate_dbt_profile returns a dict with the expected keys."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with ctx_patch:
        result = await mcp._tool_manager.call_tool(
            "generate_dbt_profile",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert isinstance(result, dict)
    assert "profiles_yml" in result
    assert "dbt_project_yml" in result
    assert "sources_yml" in result
    assert "requirements_txt" in result
    assert "gitignore" in result


async def test_generate_dbt_profile_profiles_yml_is_valid_yaml(
    mock_ctx,  # noqa: ARG001
    ctx_patch,
) -> None:
    """profiles_yml in the returned dict must be valid YAML."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with ctx_patch:
        result = await mcp._tool_manager.call_tool(
            "generate_dbt_profile",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    parsed = yaml.safe_load(result["profiles_yml"])
    assert isinstance(parsed, dict)


async def test_generate_dbt_profile_dbt_project_yml_is_valid_yaml(
    mock_ctx,  # noqa: ARG001
    ctx_patch,
) -> None:
    """dbt_project_yml in the returned dict must be valid YAML."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with ctx_patch:
        result = await mcp._tool_manager.call_tool(
            "generate_dbt_profile",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    parsed = yaml.safe_load(result["dbt_project_yml"])
    assert isinstance(parsed, dict)


async def test_generate_dbt_profile_contains_host(mock_ctx, ctx_patch) -> None:
    """The profiles_yml must contain the warehouse's connection string as host."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    entry = make_item_entry(connection_string="testhost.datawarehouse.fabric.microsoft.com")
    mock_ctx.resolver.item = AsyncMock(return_value=entry)

    with ctx_patch:
        result = await mcp._tool_manager.call_tool(
            "generate_dbt_profile",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert "testhost.datawarehouse.fabric.microsoft.com" in result["profiles_yml"]


async def test_generate_dbt_profile_contains_database(mock_ctx, ctx_patch) -> None:
    """The profiles_yml must contain the warehouse display_name as database."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    entry = make_item_entry(display_name="MySalesWarehouse")
    mock_ctx.resolver.item = AsyncMock(return_value=entry)

    with ctx_patch:
        result = await mcp._tool_manager.call_tool(
            "generate_dbt_profile",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert "MySalesWarehouse" in result["profiles_yml"]


async def test_generate_dbt_profile_sp_emits_env_var_placeholders(
    mock_ctx,  # noqa: ARG001
    ctx_patch,
) -> None:
    """ServicePrincipal auth must use env_var() placeholders, never literal secrets."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with ctx_patch:
        result = await mcp._tool_manager.call_tool(
            "generate_dbt_profile",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "authentication": DbtAuthMode.SERVICE_PRINCIPAL,
            },
        )

    assert "env_var('AZURE_TENANT_ID')" in result["profiles_yml"]
    assert "env_var('AZURE_CLIENT_ID')" in result["profiles_yml"]
    assert "env_var('AZURE_CLIENT_SECRET')" in result["profiles_yml"]


async def test_generate_dbt_profile_requirements_contains_dbt_core(
    mock_ctx,  # noqa: ARG001
    ctx_patch,
) -> None:
    """requirements_txt must include dbt-core."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with ctx_patch:
        result = await mcp._tool_manager.call_tool(
            "generate_dbt_profile",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert "dbt-core" in result["requirements_txt"]
    assert "dbt-fabric" in result["requirements_txt"]


async def test_generate_dbt_profile_gitignore_contains_target(
    mock_ctx,  # noqa: ARG001
    ctx_patch,
) -> None:
    """gitignore must include target/."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with ctx_patch:
        result = await mcp._tool_manager.call_tool(
            "generate_dbt_profile",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert "target/" in result["gitignore"]


# ---------------------------------------------------------------------------
# generate_dbt_profile — with_sources
# ---------------------------------------------------------------------------


async def test_generate_dbt_profile_with_sources_calls_list_schemas(
    mock_ctx,  # noqa: ARG001
    ctx_patch,
) -> None:
    """with_sources=True triggers schema and table listing."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.schemas.list_schemas",
            new=AsyncMock(return_value=[]),
        ) as mock_schemas,
        patch(
            "fabric_dw.services.tables.list_tables",
            new=AsyncMock(return_value=[]),
        ) as mock_tables,
    ):
        result = await mcp._tool_manager.call_tool(
            "generate_dbt_profile",
            {"workspace": WS_NAME, "item": WH_NAME, "with_sources": True},
        )

    mock_schemas.assert_called_once()
    mock_tables.assert_called_once()
    assert "sources_yml" in result


# ---------------------------------------------------------------------------
# generate_dbt_profile — error paths
# ---------------------------------------------------------------------------


async def test_generate_dbt_profile_no_connection_string_raises_tool_error(
    mock_ctx, ctx_patch
) -> None:
    """Missing connection_string must raise ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    entry = make_item_entry(connection_string=None)
    mock_ctx.resolver.item = AsyncMock(return_value=entry)

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "generate_dbt_profile",
            {"workspace": WS_NAME, "item": WH_NAME},
        )


async def test_generate_dbt_profile_workspace_guard(
    mock_ctx,  # noqa: ARG001
    ctx_patch,
    monkeypatch,
) -> None:
    """Workspace not in allowlist raises ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_MCP_WORKSPACES", "other-workspace")

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "generate_dbt_profile",
            {"workspace": WS_NAME, "item": WH_NAME},
        )
