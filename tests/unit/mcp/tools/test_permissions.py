"""Unit tests for MCP permission tools.

Coverage
--------
1. Read tools (list_sql_permissions, list_database_principals, my_permissions)
   return data correctly.
2. grant_permission and deny_permission are blocked by FABRIC_MCP_READONLY but
   NOT by missing FABRIC_MCP_ALLOW_DESTRUCTIVE.
3. revoke_permission is blocked by FABRIC_MCP_READONLY AND by missing
   FABRIC_MCP_ALLOW_DESTRUCTIVE (destructive-gated).
4. Allowlist guard: invalid permissions produce ToolError.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from fabric_dw.models import DatabasePermission, DatabasePrincipal
from tests.unit.mcp.conftest import WH_NAME, WS_ID, WS_NAME, make_item_entry


def _make_db_permission() -> DatabasePermission:
    return DatabasePermission(
        principal_name="alice@contoso.com",
        principal_type="EXTERNAL_USER",
        state="GRANT",
        permission_name="SELECT",
        securable_class="DATABASE",
        schema_name=None,
        object_name=None,
    )


def _make_db_principal() -> DatabasePrincipal:
    return DatabasePrincipal(
        name="alice@contoso.com",
        type="EXTERNAL_USER",
        authentication_type="EXTERNAL",
    )


# ---------------------------------------------------------------------------
# 1. list_sql_permissions -- read-only happy path
# ---------------------------------------------------------------------------


async def test_list_sql_permissions_happy_path(mock_ctx, ctx_patch) -> None:
    """list_sql_permissions returns a list of serialised DatabasePermission dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.permissions.list_sql_permissions",
            new=AsyncMock(return_value=[_make_db_permission()]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_sql_permissions",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["principal_name"] == "alice@contoso.com"
    assert result[0]["state"] == "GRANT"


# ---------------------------------------------------------------------------
# 2. list_database_principals -- read-only happy path
# ---------------------------------------------------------------------------


async def test_list_database_principals_happy_path(mock_ctx, ctx_patch) -> None:
    """list_database_principals returns a list of serialised DatabasePrincipal dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.permissions.list_database_principals",
            new=AsyncMock(return_value=[_make_db_principal()]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_database_principals",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "alice@contoso.com"


# ---------------------------------------------------------------------------
# 3. my_permissions -- read-only happy path
# ---------------------------------------------------------------------------


async def test_my_permissions_happy_path(mock_ctx, ctx_patch) -> None:
    """my_permissions returns a list of permission dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.permissions.my_permissions",
            new=AsyncMock(return_value=[{"permission_name": "SELECT", "entity_name": ""}]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "my_permissions",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert isinstance(result, list)
    assert result[0]["permission_name"] == "SELECT"


# ---------------------------------------------------------------------------
# 4. grant_permission -- mutating, blocked by FABRIC_MCP_READONLY
# ---------------------------------------------------------------------------


async def test_grant_permission_happy_path(mock_ctx, ctx_patch) -> None:
    """grant_permission returns success dict when READONLY is not set."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {}, clear=False),
        patch("fabric_dw.services.permissions.grant_permission", new=AsyncMock(return_value=None)),
    ):
        # Must NOT raise even without FABRIC_MCP_ALLOW_DESTRUCTIVE
        os.environ.pop("FABRIC_MCP_READONLY", None)
        os.environ.pop("FABRIC_MCP_ALLOW_DESTRUCTIVE", None)
        result = await mcp._tool_manager.call_tool(
            "grant_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "DATABASE",
            },
        )

    assert result["granted"] is True
    assert result["permissions"] == "SELECT"


async def test_grant_permission_blocked_by_readonly(mock_ctx, ctx_patch) -> None:
    """grant_permission is blocked when FABRIC_MCP_READONLY is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only mode"),
    ):
        await mcp._tool_manager.call_tool(
            "grant_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "DATABASE",
            },
        )


async def test_grant_permission_not_destructive_gated(mock_ctx, ctx_patch) -> None:
    """grant_permission works WITHOUT FABRIC_MCP_ALLOW_DESTRUCTIVE (not destructive-gated)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {}, clear=False),
        patch("fabric_dw.services.permissions.grant_permission", new=AsyncMock(return_value=None)),
    ):
        os.environ.pop("FABRIC_MCP_READONLY", None)
        os.environ.pop("FABRIC_MCP_ALLOW_DESTRUCTIVE", None)
        # Must NOT raise a ToolError about FABRIC_MCP_ALLOW_DESTRUCTIVE
        result = await mcp._tool_manager.call_tool(
            "grant_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "DATABASE",
            },
        )

    assert result["granted"] is True


async def test_grant_permission_invalid_permissions_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """grant_permission raises ToolError when the permissions string is invalid."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {}),
        patch(
            "fabric_dw.services.permissions.grant_permission",
            new=AsyncMock(side_effect=ValueError("Invalid permission(s)")),
        ),
    ):
        os.environ.pop("FABRIC_MCP_READONLY", None)
        with pytest.raises(ToolError):
            await mcp._tool_manager.call_tool(
                "grant_permission",
                {
                    "workspace": WS_NAME,
                    "item": WH_NAME,
                    "permissions": "SELECTX",
                    "principal": "alice@contoso.com",
                    "scope": "DATABASE",
                },
            )


# ---------------------------------------------------------------------------
# 5. deny_permission -- mutating, blocked by FABRIC_MCP_READONLY
# ---------------------------------------------------------------------------


async def test_deny_permission_happy_path(mock_ctx, ctx_patch) -> None:
    """deny_permission returns success dict when READONLY is not set."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {}),
        patch("fabric_dw.services.permissions.deny_permission", new=AsyncMock(return_value=None)),
    ):
        os.environ.pop("FABRIC_MCP_READONLY", None)
        result = await mcp._tool_manager.call_tool(
            "deny_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "DATABASE",
            },
        )

    assert result["denied"] is True
    assert result["permissions"] == "SELECT"


async def test_deny_permission_blocked_by_readonly(mock_ctx, ctx_patch) -> None:
    """deny_permission is blocked when FABRIC_MCP_READONLY is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "true"}),
        pytest.raises(ToolError, match="read-only mode"),
    ):
        await mcp._tool_manager.call_tool(
            "deny_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "DATABASE",
            },
        )


async def test_deny_permission_not_destructive_gated(mock_ctx, ctx_patch) -> None:
    """deny_permission works WITHOUT FABRIC_MCP_ALLOW_DESTRUCTIVE."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {}),
        patch("fabric_dw.services.permissions.deny_permission", new=AsyncMock(return_value=None)),
    ):
        os.environ.pop("FABRIC_MCP_READONLY", None)
        os.environ.pop("FABRIC_MCP_ALLOW_DESTRUCTIVE", None)
        result = await mcp._tool_manager.call_tool(
            "deny_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "DATABASE",
            },
        )

    assert result["denied"] is True


# ---------------------------------------------------------------------------
# 6. revoke_permission -- mutating, blocked by FABRIC_MCP_READONLY
# ---------------------------------------------------------------------------


async def test_revoke_permission_happy_path(mock_ctx, ctx_patch) -> None:
    """revoke_permission returns success dict when READONLY is not set and ALLOW_DESTRUCTIVE=1."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch("fabric_dw.services.permissions.revoke_permission", new=AsyncMock(return_value=None)),
    ):
        os.environ.pop("FABRIC_MCP_READONLY", None)
        result = await mcp._tool_manager.call_tool(
            "revoke_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "DATABASE",
            },
        )

    assert result["revoked"] is True
    assert result["permissions"] == "SELECT"


async def test_revoke_permission_blocked_by_readonly(mock_ctx, ctx_patch) -> None:
    """revoke_permission is blocked when FABRIC_MCP_READONLY is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "yes"}),
        pytest.raises(ToolError, match="read-only mode"),
    ):
        await mcp._tool_manager.call_tool(
            "revoke_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "DATABASE",
            },
        )


async def test_revoke_permission_blocked_by_missing_destructive_env(mock_ctx, ctx_patch) -> None:
    """revoke_permission is blocked when FABRIC_MCP_ALLOW_DESTRUCTIVE is unset."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    os.environ.pop("FABRIC_MCP_READONLY", None)
    os.environ.pop("FABRIC_MCP_ALLOW_DESTRUCTIVE", None)
    with ctx_patch, patch.dict(os.environ, {}), pytest.raises(ToolError, match="destructive"):
        await mcp._tool_manager.call_tool(
            "revoke_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "DATABASE",
            },
        )


async def test_revoke_permission_allowed_when_destructive_env_set(mock_ctx, ctx_patch) -> None:
    """revoke_permission succeeds when FABRIC_MCP_ALLOW_DESTRUCTIVE=1 is set."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch("fabric_dw.services.permissions.revoke_permission", new=AsyncMock(return_value=None)),
    ):
        os.environ.pop("FABRIC_MCP_READONLY", None)
        result = await mcp._tool_manager.call_tool(
            "revoke_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "DATABASE",
            },
        )

    assert result["revoked"] is True


# ---------------------------------------------------------------------------
# scope.upper() in return dicts
# ---------------------------------------------------------------------------


async def test_grant_permission_scope_uppercased_in_result(mock_ctx, ctx_patch) -> None:
    """grant_permission must uppercase the scope value in the returned dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {}),
        patch("fabric_dw.services.permissions.grant_permission", new=AsyncMock(return_value=None)),
    ):
        os.environ.pop("FABRIC_MCP_READONLY", None)
        result = await mcp._tool_manager.call_tool(
            "grant_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "database",  # lowercase input
            },
        )

    assert result["scope"] == "DATABASE"


# ---------------------------------------------------------------------------
# Removal contract: old MCP tool names must not be registered
# ---------------------------------------------------------------------------


async def test_old_mcp_tool_get_warehouse_permissions_removed() -> None:
    """get_warehouse_permissions must NOT be registered (moved to list_item_permissions)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "get_warehouse_permissions" not in tool_names, (
        "get_warehouse_permissions still registered; it was replaced by list_item_permissions"
    )


async def test_old_mcp_tool_get_sql_endpoint_permissions_removed() -> None:
    """get_sql_endpoint_permissions must NOT be registered."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "get_sql_endpoint_permissions" not in tool_names, (
        "get_sql_endpoint_permissions still registered; it was replaced by list_item_permissions"
    )


# ---------------------------------------------------------------------------
# Column-level security: grant/deny/revoke with columns parameter
# ---------------------------------------------------------------------------


async def test_grant_permission_with_columns_happy_path(mock_ctx, ctx_patch) -> None:
    """grant_permission passes columns list to the service correctly."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    mock_svc = AsyncMock(return_value=None)
    with (
        ctx_patch,
        patch.dict(os.environ, {}, clear=False),
        patch("fabric_dw.services.permissions.grant_permission", new=mock_svc),
    ):
        os.environ.pop("FABRIC_MCP_READONLY", None)
        os.environ.pop("FABRIC_MCP_ALLOW_DESTRUCTIVE", None)
        result = await mcp._tool_manager.call_tool(
            "grant_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "OBJECT",
                "object_name": "dbo.sales",
                "columns": ["email", "phone"],
            },
        )

    assert result["granted"] is True
    _args, kwargs = mock_svc.call_args
    assert kwargs.get("columns") == ["email", "phone"]


async def test_deny_permission_with_columns_happy_path(mock_ctx, ctx_patch) -> None:
    """deny_permission passes columns list to the service correctly."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    mock_svc = AsyncMock(return_value=None)
    with (
        ctx_patch,
        patch.dict(os.environ, {}),
        patch("fabric_dw.services.permissions.deny_permission", new=mock_svc),
    ):
        os.environ.pop("FABRIC_MCP_READONLY", None)
        result = await mcp._tool_manager.call_tool(
            "deny_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "OBJECT",
                "object_name": "dbo.sales",
                "columns": ["ssn"],
            },
        )

    assert result["denied"] is True
    _args, kwargs = mock_svc.call_args
    assert kwargs.get("columns") == ["ssn"]


async def test_revoke_permission_with_columns_happy_path(mock_ctx, ctx_patch) -> None:
    """revoke_permission passes columns list to the service correctly (destructive-gated)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    mock_svc = AsyncMock(return_value=None)
    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch("fabric_dw.services.permissions.revoke_permission", new=mock_svc),
    ):
        os.environ.pop("FABRIC_MCP_READONLY", None)
        result = await mcp._tool_manager.call_tool(
            "revoke_permission",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "permissions": "SELECT",
                "principal": "alice@contoso.com",
                "scope": "OBJECT",
                "object_name": "dbo.sales",
                "columns": ["email"],
            },
        )

    assert result["revoked"] is True
    _args, kwargs = mock_svc.call_args
    assert kwargs.get("columns") == ["email"]
