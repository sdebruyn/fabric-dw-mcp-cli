"""MCP tool for generating dbt-fabric project files.

The MCP server cannot write to the user's local filesystem, so this tool
returns the generated file contents as text strings rather than writing files.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import assert_workspace_allowed
from fabric_dw.mcp._helpers import (
    make_sql_target,
    resolve_item,
    tool_err,
)
from fabric_dw.services.dbt_scaffold import (
    DbtAuthMode,
    DbtScaffoldConfig,
    auth_mode_to_dbt,
    render_dbt_project_yml,
    render_profiles_yml,
    render_sources_yml,
    sanitize_project_name,
)

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register dbt tools against *mcp*."""

    @mcp.tool(name="generate_dbt_profile")
    async def generate_dbt_profile(  # noqa: PLR0913
        workspace: str,
        item: str,
        project_name: str = "",
        profile_name: str = "",
        schema: str = "dbo",
        target: str = "dev",
        threads: int = 4,
        authentication: str = "",
        with_sources: bool = False,  # noqa: FBT001, FBT002
    ) -> dict[str, str]:
        """Generate dbt-fabric project file contents for a Fabric Data Warehouse.

        Returns the generated file contents as text strings.  Because the MCP server
        cannot write to the caller's local filesystem, it is the caller's responsibility
        to write the returned strings to the appropriate files.

        Authentication note: dbt-fabric is Entra-only.  ServicePrincipal mode emits
        ``{{ env_var(...) }}`` placeholders for tenant_id / client_id / client_secret
        — no literal secrets are included in the output.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.
            project_name: dbt project name (default: sanitized warehouse name).
            profile_name: dbt profile name (default: same as project_name).
            schema: Default schema (default: ``dbo``).
            target: dbt output target name (default: ``dev``).
            threads: Number of dbt threads (default: 4).
            authentication: dbt-fabric authentication string —
                ``auto`` (DefaultAzureCredential), ``CLI`` (interactive),
                or ``ServicePrincipal``.  Defaults to the server's auth mode.
            with_sources: When ``True``, generate a ``_sources.yml`` from the
                warehouse's actual schemas and tables.

        Returns:
            A dict with keys:
            - ``profiles_yml``: content for ``profiles.yml``.
            - ``dbt_project_yml``: content for ``dbt_project.yml``.
            - ``sources_yml``: content for ``models/staging/_sources.yml``.
            - ``requirements_txt``: content for ``requirements.txt``.
            - ``gitignore``: content for ``.gitignore``.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()

        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("generate_dbt_profile ws=%s item=%s", ws_id, entry.id)
            sql_target = make_sql_target(ws_id, entry, item)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc

        # Derive project name from warehouse display name if not supplied.
        raw_name = project_name or entry.display_name or item
        try:
            safe_project_name = sanitize_project_name(raw_name)
        except ValueError as exc:
            raise tool_err(exc) from exc

        # Resolve authentication.
        dbt_auth = authentication or auth_mode_to_dbt(ctx.auth_mode)
        if dbt_auth not in (DbtAuthMode.AUTO, DbtAuthMode.CLI, DbtAuthMode.SERVICE_PRINCIPAL):
            dbt_auth = DbtAuthMode.AUTO

        # Fetch schemas/tables when with_sources requested.
        schemas = []
        tables = []
        if with_sources:
            import asyncio  # noqa: PLC0415

            from fabric_dw.services import schemas as schemas_svc  # noqa: PLC0415
            from fabric_dw.services import tables as tables_svc  # noqa: PLC0415

            try:
                schemas, tables = await asyncio.gather(
                    schemas_svc.list_schemas(sql_target, mode=ctx.auth_mode),
                    tables_svc.list_tables(sql_target, mode=ctx.auth_mode),
                )
            except (ValueError, FabricError) as exc:
                raise tool_err(exc) from exc

        cfg = DbtScaffoldConfig(
            host=sql_target.connection_string,
            database=sql_target.database,
            project_name=safe_project_name,
            profile_name=profile_name or safe_project_name,
            schema=schema,
            target=target,
            threads=threads,
            dbt_auth=dbt_auth,
            with_sources=with_sources,
            schemas=schemas,
            tables=tables,
        )

        from fabric_dw.services.dbt_scaffold import (  # noqa: PLC0415
            _GITIGNORE_CONTENT,
            _REQUIREMENTS_CONTENT,
        )

        return {
            "profiles_yml": render_profiles_yml(cfg),
            "dbt_project_yml": render_dbt_project_yml(cfg),
            "sources_yml": render_sources_yml(cfg),
            "requirements_txt": _REQUIREMENTS_CONTENT,
            "gitignore": _GITIGNORE_CONTENT,
        }
