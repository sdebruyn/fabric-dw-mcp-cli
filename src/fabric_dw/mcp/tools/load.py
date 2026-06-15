"""MCP tools for loading data into Fabric Data Warehouse tables via COPY INTO."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import assert_workspace_allowed
from fabric_dw.mcp._helpers import (
    make_sql_target,
    mutating_tool,
    parse_qualified_name,
    resolve_item,
    tool_err,
)
from fabric_dw.services.load import CopyIntoCsvOptions, copy_into_from_url

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register table-load tools against *mcp*."""

    @mutating_tool(mcp, "load_table_from_url")
    async def load_table_from_url(  # noqa: PLR0913
        workspace: str,
        item: str,
        qualified_name: str,
        url: str,
        file_type: Annotated[
            Literal["CSV", "PARQUET"],
            Field(
                description=(
                    "File type to load.  JSON is not supported for remote URLs;"
                    " download and convert locally first."
                ),
            ),
        ],
        credential_type: Annotated[
            Literal["none", "sas", "managed-identity", "service-principal", "account-key"],
            Field(
                description=(
                    "Credential type for secured external URLs."
                    " Use 'none' for OneLake or public URLs."
                ),
            ),
        ] = "none",
        secret: Annotated[
            str | None,
            Field(
                description=(
                    "Credential secret (SAS token, client secret, or account key)."
                    " NEVER log or echo this value."
                ),
                default=None,
            ),
        ] = None,
        identity: Annotated[
            str | None,
            Field(
                description=(
                    "Identity value for managed-identity or service-principal credential types."
                ),
                default=None,
            ),
        ] = None,
        delimiter: Annotated[
            str | None,
            Field(description="CSV column delimiter (e.g. ',', '\\t').", default=None),
        ] = None,
        has_header: Annotated[  # noqa: FBT002
            bool,
            Field(
                description="When True, the first CSV row is a header and is skipped.",
                default=True,
            ),
        ] = True,
        encoding: Annotated[
            str | None,
            Field(description="CSV file encoding (e.g. 'UTF8', 'UTF8BOM').", default=None),
        ] = None,
        field_quote: Annotated[
            str | None,
            Field(description="CSV field-quote character.", default=None),
        ] = None,
        row_terminator: Annotated[
            str | None,
            Field(
                description="CSV row terminator (e.g. '\\n', '\\r\\n').",
                default=None,
            ),
        ] = None,
        max_errors: Annotated[
            int | None,
            Field(description="Maximum number of errors before aborting.", default=None),
        ] = None,
        rejected_row_location: Annotated[
            str | None,
            Field(description="URL to write rejected rows to.", default=None),
        ] = None,
    ) -> dict[str, Any]:
        """Load data into a Data Warehouse table via ``COPY INTO`` from a remote URL.

        Supported file types: ``CSV``, ``PARQUET``.  JSON remote URLs require
        downloading and converting locally first; use the CLI ``tables load``
        command for local files (including JSON).

        For OneLake or same-tenant URLs, no credential is needed.  For secured
        external URLs (Azure Blob Storage SAS, etc.), supply ``credential_type``
        and the appropriate ``secret``/``identity`` values.

        CAUTION: This operation loads data into the target table.  Confirm the
        source URL and target table before calling.

        Note: ``secret`` / ``identity`` values are accepted but are NEVER logged
        or included in any debug output.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.  SQL Analytics Endpoints are rejected.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
            url: Source URL (OneLake DFS URL or external Azure Blob URL).
            file_type: ``CSV`` or ``PARQUET``.
            credential_type: Credential type for the source URL.
            secret: Credential secret (not logged).
            identity: Identity for managed-identity or service-principal.
            delimiter: CSV column delimiter.
            has_header: Whether the CSV file has a header row.
            encoding: CSV file encoding.
            field_quote: CSV field-quote character.
            row_terminator: CSV row terminator.
            max_errors: Maximum errors before aborting.
            rejected_row_location: URL for rejected-row output.
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        assert_workspace_allowed(workspace)
        ctx = get_context()

        # Validate file_type
        if file_type not in ("CSV", "PARQUET"):
            from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

            raise ToolError(f"Unsupported file_type {file_type!r}; must be CSV or PARQUET")

        # Build CSV options.
        csv_options: CopyIntoCsvOptions | None = None
        if file_type == "CSV":
            first_row = 2 if has_header else 1
            csv_options = CopyIntoCsvOptions(
                delimiter=delimiter,
                first_row=first_row,
                encoding=encoding,
                field_quote=field_quote,
                row_terminator=row_terminator,
            )

        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            # Log without secrets.
            _log.debug(
                "load_table_from_url ws=%s item=%s table=%s.%s url=%s file_type=%s cred_type=%s",
                ws_id,
                entry.id,
                schema,
                table_name,
                url,
                file_type,
                credential_type,
                # secret and identity are intentionally excluded from this log call
            )
            sql_target = make_sql_target(ws_id, entry, item)
            result = await copy_into_from_url(
                sql_target,
                schema,
                table_name,
                url,
                file_type=file_type,
                credential_type=credential_type,  # type: ignore[arg-type]
                secret=secret,
                identity=identity,
                csv_options=csv_options,
                max_errors=max_errors,
                rejected_row_location=rejected_row_location,
                kind=entry.kind,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")
