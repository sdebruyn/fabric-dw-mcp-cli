"""Unit tests for the MCP audit tool wrappers.

Target: src/fabric_dw/mcp/tools/audit.py
Goal:   ≥95 % branch coverage.

Strategy
--------
- All calls routed via ``mcp._tool_manager.call_tool``.
- ``ServerContext`` injected by patching ``fabric_dw.mcp._context._SERVER_CTX``
  with the shared ``mock_ctx`` fixture.
- Service layer fully mocked — no real network.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.models import AuditSettings
from tests.unit.mcp.conftest import (
    WH_NAME,
    WS_ID,
    WS_NAME,
    make_item_entry,
)

# ---------------------------------------------------------------------------
# Module-level import of the mcp server (registers all tools)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _import_server() -> None:
    from fabric_dw.mcp.server import mcp  # noqa: F401, PLC0415


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_audit_settings(
    *,
    state: str = "Enabled",
    retention_days: int = 30,
    action_groups: list[str] | None = None,
) -> AuditSettings:
    return AuditSettings.model_validate(
        {
            "state": state,
            "retentionDays": retention_days,
            "auditActionsAndGroups": action_groups or ["BATCH_COMPLETED_GROUP"],
        }
    )


# ---------------------------------------------------------------------------
# get_audit_settings — happy path
# ---------------------------------------------------------------------------


async def test_get_audit_settings_happy_path(mock_ctx, ctx_patch) -> None:
    """get_audit_settings resolves workspace + warehouse, returns settings dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.get_settings", new=AsyncMock(return_value=settings)),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_audit_settings",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert isinstance(result, dict)
    assert result["state"] == "Enabled"
    assert result["retentionDays"] == 30
    assert "BATCH_COMPLETED_GROUP" in result["auditActionsAndGroups"]
    mock_ctx.resolver.item.assert_called_once_with(str(WS_ID), WH_NAME)


async def test_get_audit_settings_disabled_state(mock_ctx, ctx_patch) -> None:
    """get_audit_settings returns Disabled state when audit is off."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings(state="Disabled", retention_days=0, action_groups=[])
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.get_settings", new=AsyncMock(return_value=settings)),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_audit_settings",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert result["state"] == "Disabled"


# ---------------------------------------------------------------------------
# get_audit_settings — error / guard paths
# ---------------------------------------------------------------------------


async def test_get_audit_settings_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """get_audit_settings converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.get_settings",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_audit_settings",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


async def test_get_audit_settings_workspace_not_in_allowlist(ctx_patch) -> None:
    """get_audit_settings raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "get_audit_settings",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert "allowlist" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# enable_audit — happy path
# ---------------------------------------------------------------------------


async def test_enable_audit_happy_path(mock_ctx, ctx_patch) -> None:
    """enable_audit resolves workspace + warehouse, calls service, returns dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.enable", new=AsyncMock(return_value=settings)),
    ):
        result = await mcp._tool_manager.call_tool(
            "enable_audit",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert isinstance(result, dict)
    assert result["state"] == "Enabled"


async def test_enable_audit_with_retention_days(mock_ctx, ctx_patch) -> None:
    """enable_audit passes retention_days to service layer."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings(retention_days=90)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_svc = AsyncMock(return_value=settings)

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.enable", new=mock_svc),
    ):
        result = await mcp._tool_manager.call_tool(
            "enable_audit",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "retention_days": 90},
        )

    assert result["retentionDays"] == 90
    _, kwargs = mock_svc.call_args
    assert kwargs.get("retention_days") == 90


# ---------------------------------------------------------------------------
# enable_audit — error / guard paths
# ---------------------------------------------------------------------------


async def test_enable_audit_readonly_mode_blocked(ctx_patch) -> None:
    """enable_audit raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "enable_audit",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_enable_audit_workspace_not_in_allowlist(ctx_patch) -> None:
    """enable_audit raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "enable_audit",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


async def test_enable_audit_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """enable_audit converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.enable",
            new=AsyncMock(side_effect=FabricError("API error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "enable_audit",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


# ---------------------------------------------------------------------------
# disable_audit — happy path
# ---------------------------------------------------------------------------


async def test_disable_audit_happy_path(mock_ctx, ctx_patch) -> None:
    """disable_audit resolves workspace + warehouse, calls service, returns dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings(state="Disabled")
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.disable", new=AsyncMock(return_value=settings)),
    ):
        result = await mcp._tool_manager.call_tool(
            "disable_audit",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert isinstance(result, dict)
    assert result["state"] == "Disabled"


# ---------------------------------------------------------------------------
# disable_audit — error / guard paths
# ---------------------------------------------------------------------------


async def test_disable_audit_readonly_mode_blocked(ctx_patch) -> None:
    """disable_audit raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "disable_audit",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_disable_audit_workspace_not_in_allowlist(ctx_patch) -> None:
    """disable_audit raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "disable_audit",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


async def test_disable_audit_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """disable_audit converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.disable",
            new=AsyncMock(side_effect=FabricError("API error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "disable_audit",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


# ---------------------------------------------------------------------------
# set_audit_action_groups — happy path
# ---------------------------------------------------------------------------


async def test_set_audit_action_groups_happy_path(mock_ctx, ctx_patch) -> None:
    """set_audit_action_groups calls service with the specified groups list."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    groups = ["BATCH_COMPLETED_GROUP", "FAILED_DATABASE_AUTHENTICATION_GROUP"]
    settings = _make_audit_settings(action_groups=groups)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_svc = AsyncMock(return_value=settings)

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.set_action_groups", new=mock_svc),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_audit_action_groups",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "action_groups": ["BATCH_COMPLETED_GROUP", "FAILED_DATABASE_AUTHENTICATION_GROUP"],
            },
        )

    assert isinstance(result, dict)
    assert "BATCH_COMPLETED_GROUP" in result["auditActionsAndGroups"]
    mock_svc.assert_called_once()


async def test_set_audit_action_groups_passes_ensure_enabled_false(mock_ctx, ctx_patch) -> None:
    """set_audit_action_groups always passes ensure_enabled=False to the service.

    Regression guard for #876: the tool must not silently enable auditing on a
    Disabled warehouse, so it must call set_action_groups with ensure_enabled=False.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    groups = ["BATCH_COMPLETED_GROUP"]
    settings = _make_audit_settings(action_groups=groups)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_svc = AsyncMock(return_value=settings)

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.set_action_groups", new=mock_svc),
    ):
        await mcp._tool_manager.call_tool(
            "set_audit_action_groups",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "action_groups": groups},
        )

    _, kwargs = mock_svc.call_args
    assert kwargs.get("ensure_enabled") is False


async def test_set_audit_action_groups_disabled_warehouse_state_unchanged(
    mock_ctx, ctx_patch
) -> None:
    """set_audit_action_groups on a Disabled warehouse returns Disabled state.

    Regression guard for #876: the tool must not silently enable auditing.
    When the service returns Disabled state (because ensure_enabled=False preserved
    it), the tool must return that state to the caller unchanged.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    groups = ["BATCH_COMPLETED_GROUP"]
    settings = _make_audit_settings(state="Disabled", action_groups=groups)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.set_action_groups", new=AsyncMock(return_value=settings)),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_audit_action_groups",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "action_groups": groups},
        )

    assert result["state"] == "Disabled"
    assert groups[0] in result["auditActionsAndGroups"]


async def test_set_audit_action_groups_enabled_warehouse_state_unchanged(
    mock_ctx, ctx_patch
) -> None:
    """set_audit_action_groups on an Enabled warehouse preserves Enabled state.

    Companion to the Disabled case: replacing groups on an already-Enabled warehouse
    must keep state=Enabled in the result.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    groups = ["BATCH_COMPLETED_GROUP"]
    settings = _make_audit_settings(state="Enabled", action_groups=groups)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.set_action_groups", new=AsyncMock(return_value=settings)),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_audit_action_groups",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "action_groups": groups},
        )

    assert result["state"] == "Enabled"
    assert groups[0] in result["auditActionsAndGroups"]


# ---------------------------------------------------------------------------
# set_audit_action_groups — error / guard paths
# ---------------------------------------------------------------------------


async def test_set_audit_action_groups_readonly_mode_blocked(ctx_patch) -> None:
    """set_audit_action_groups raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "set_audit_action_groups",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "action_groups": ["BATCH_COMPLETED_GROUP"],
            },
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_set_audit_action_groups_workspace_not_in_allowlist(ctx_patch) -> None:
    """set_audit_action_groups raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "set_audit_action_groups",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "action_groups": ["BATCH_COMPLETED_GROUP"],
            },
        )


async def test_set_audit_action_groups_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """set_audit_action_groups converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.set_action_groups",
            new=AsyncMock(side_effect=FabricError("API error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "set_audit_action_groups",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "action_groups": ["BATCH_COMPLETED_GROUP"],
            },
        )


# ---------------------------------------------------------------------------
# add_audit_group — happy path
# ---------------------------------------------------------------------------


async def test_add_audit_group_happy_path(mock_ctx, ctx_patch) -> None:
    """add_audit_group resolves workspace + warehouse, returns updated settings dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.add_action_group", new=AsyncMock(return_value=settings)),
    ):
        result = await mcp._tool_manager.call_tool(
            "add_audit_group",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )

    assert isinstance(result, dict)
    assert result["state"] == "Enabled"


# ---------------------------------------------------------------------------
# add_audit_group — error / guard paths
# ---------------------------------------------------------------------------


async def test_add_audit_group_readonly_mode_blocked(ctx_patch) -> None:
    """add_audit_group raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "add_audit_group",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_add_audit_group_workspace_not_in_allowlist(ctx_patch) -> None:
    """add_audit_group raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "add_audit_group",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )


async def test_add_audit_group_value_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """add_audit_group converts ValueError (audit disabled) to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.add_action_group",
            new=AsyncMock(side_effect=ValueError("audit is disabled")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "add_audit_group",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )

    assert "disabled" in str(exc_info.value).lower()


async def test_add_audit_group_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """add_audit_group converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.add_action_group",
            new=AsyncMock(side_effect=FabricError("API error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "add_audit_group",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )


# ---------------------------------------------------------------------------
# remove_audit_group — happy path
# ---------------------------------------------------------------------------


async def test_remove_audit_group_happy_path(mock_ctx, ctx_patch) -> None:
    """remove_audit_group resolves workspace + warehouse, returns updated settings dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings(action_groups=[])
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_svc = AsyncMock(return_value=settings)

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.remove_action_group", new=mock_svc),
    ):
        result = await mcp._tool_manager.call_tool(
            "remove_audit_group",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )

    assert isinstance(result, dict)
    # settings has no groups; the serialised dict should reflect that
    assert result["state"] == "Enabled"
    mock_svc.assert_called_once()


# ---------------------------------------------------------------------------
# remove_audit_group — error / guard paths
# ---------------------------------------------------------------------------


async def test_remove_audit_group_readonly_mode_blocked(ctx_patch) -> None:
    """remove_audit_group raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "remove_audit_group",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_remove_audit_group_workspace_not_in_allowlist(ctx_patch) -> None:
    """remove_audit_group raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "remove_audit_group",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )


async def test_remove_audit_group_value_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """remove_audit_group converts ValueError (audit disabled) to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.remove_action_group",
            new=AsyncMock(side_effect=ValueError("audit is disabled")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "remove_audit_group",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )

    assert "disabled" in str(exc_info.value).lower()


async def test_remove_audit_group_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """remove_audit_group converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.remove_action_group",
            new=AsyncMock(side_effect=FabricError("API error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "remove_audit_group",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )


# ---------------------------------------------------------------------------
# set_audit_retention — happy path
# ---------------------------------------------------------------------------


async def test_set_audit_retention_happy_path(mock_ctx, ctx_patch) -> None:
    """set_audit_retention resolves workspace + warehouse, returns updated settings dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings(retention_days=90)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.audit.set_retention", new=AsyncMock(return_value=settings)),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_audit_retention",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "days": 90},
        )

    assert isinstance(result, dict)
    assert result["retentionDays"] == 90
    assert result["state"] == "Enabled"


# ---------------------------------------------------------------------------
# set_audit_retention — error / guard paths
# ---------------------------------------------------------------------------


async def test_set_audit_retention_readonly_mode_blocked(ctx_patch) -> None:
    """set_audit_retention raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "set_audit_retention",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "days": 30},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_set_audit_retention_workspace_not_in_allowlist(ctx_patch) -> None:
    """set_audit_retention raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "set_audit_retention",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "days": 30},
        )


async def test_set_audit_retention_value_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """set_audit_retention converts ValueError (disabled audit) to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.set_retention",
            new=AsyncMock(side_effect=ValueError("audit is disabled; enable first")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "set_audit_retention",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "days": 30},
        )

    assert "disabled" in str(exc_info.value).lower()


async def test_set_audit_retention_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """set_audit_retention converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.set_retention",
            new=AsyncMock(side_effect=FabricError("API error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "set_audit_retention",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "days": 30},
        )
