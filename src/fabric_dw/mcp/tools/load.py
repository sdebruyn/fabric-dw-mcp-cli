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
from fabric_dw.services.load import (
    CopyIntoCsvOptions,
    IfExistsPolicy,
    copy_into_from_url,
)

__all__ = ["register"]

_log = logging.getLogger(__name__)


def _absent_table_msg(schema: str, table: str) -> str:
    """Return a friendly error message for a missing target table on remote-URL load."""
    return (
        f"Table [{schema}].[{table}] does not exist; "
        "remote-URL load cannot infer schema. "
        "Create the table first with create_empty_table or create_table, "
        "then retry."
    )


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
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
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)

        # Validate file_type
        if file_type not in ("CSV", "PARQUET"):
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
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
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

            from fabric_dw.exceptions import ItemKindError  # noqa: PLC0415
            from fabric_dw.models import WarehouseKind  # noqa: PLC0415
            from fabric_dw.services.load import table_exists  # noqa: PLC0415

            # SQL Endpoint: COPY INTO is Warehouse-only (check before table_exists so the
            # endpoint guard fires with the right message even when the table is absent).
            if entry.kind == WarehouseKind.SQL_ENDPOINT:
                raise ItemKindError("SQL Endpoints are read-only; COPY INTO not supported")

            # Pre-check: COPY INTO gives a raw "Invalid object name" error when the
            # target table is absent. Raise a friendly message instead.
            if not await table_exists(sql_target, schema, table_name, mode=ctx.auth_mode):
                raise ToolError(_absent_table_msg(schema, table_name))

            result = await copy_into_from_url(
                sql_target,
                schema,
                table_name,
                url,
                file_type=file_type,
                credential_type=credential_type,
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

    @mutating_tool(mcp, "import_table_from_url")
    async def import_table_from_url(  # noqa: PLR0913
        workspace: str,
        item: str,
        qualified_name: str,
        url: str,
        file_type: Annotated[
            Literal["CSV", "PARQUET"],
            Field(
                description=(
                    "File type.  JSON is not supported for remote URLs;"
                    " download and convert locally first."
                ),
            ),
        ],
        if_exists: Annotated[
            IfExistsPolicy,
            Field(
                description=(
                    "What to do when the target table exists or is absent. "
                    "'fail': error if the table already exists, or if it does not exist (default). "
                    "'append': load into the existing table; error if the table is absent. "
                    "'truncate': TRUNCATE then load (destructive). "
                    "'replace': DROP + recreate from inferred schema, then load (destructive)."
                ),
            ),
        ] = "fail",
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
        """Load data into an existing Data Warehouse table via ``COPY INTO`` from a remote URL.

        The target table **must already exist** and have a compatible schema.
        For auto-create with schema inference from local files, use the CLI
        ``tables load --file --create`` command instead.

        ``if_exists`` controls behaviour when the table already exists:

        - ``"fail"`` (default): raise an error if the table already exists, or if it
          does not exist (the table must be created first with create_empty_table or
          create_table).
        - ``"append"``: load rows into the existing table without modification.
          Raises an error if the table does not exist.
        - ``"truncate"``: TRUNCATE the existing table, then load, both inside a
          single transaction so a failed load leaves the existing rows intact
          (atomic replace).  Requires ``FABRIC_MCP_ALLOW_DESTRUCTIVE=1``.  Raises
          an error if the table does **not** exist.
        - ``"replace"``: not supported for remote URLs (schema inference requires
          downloading the file).  Use ``"truncate"`` to keep the current schema,
          or download locally and use the CLI with ``--create --if-exists replace``.

        Supported file types: ``CSV``, ``PARQUET``.  JSON remote URLs require
        downloading and converting locally first; use the CLI ``tables load``
        command for local files (including JSON).

        For OneLake or same-tenant URLs, no credential is needed.  For secured
        external URLs supply ``credential_type`` and the appropriate
        ``secret``/``identity`` values.

        CAUTION: ``truncate`` is permanently destructive.
        Confirm the source URL and target table before calling.

        Note: ``secret`` / ``identity`` values are accepted but are NEVER logged
        or included in any debug output.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.  SQL Analytics Endpoints are rejected.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
            url: Source URL (OneLake DFS URL or external Azure Blob URL).
            file_type: ``CSV`` or ``PARQUET``.
            if_exists: Policy when the target table already exists.
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
        from fabric_dw.exceptions import ItemKindError  # noqa: PLC0415
        from fabric_dw.models import WarehouseKind  # noqa: PLC0415

        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)

        # SQL Endpoint guard: COPY INTO is Warehouse-only.
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
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )

            # SQL Endpoint: COPY INTO is Warehouse-only.
            if entry.kind == WarehouseKind.SQL_ENDPOINT:
                raise ItemKindError("SQL Endpoints are read-only; COPY INTO not supported")

            _log.debug(
                "import_table_from_url ws=%s item=%s table=%s.%s url=%s "
                "file_type=%s if_exists=%s cred_type=%s",
                ws_id,
                entry.id,
                schema,
                table_name,
                url,
                file_type,
                if_exists,
                credential_type,
            )
            sql_target = make_sql_target(ws_id, entry, item)

            from mcp.server.fastmcp.exceptions import (  # noqa: PLC0415
                ToolError as _ToolError,
            )

            from fabric_dw.services.load import table_exists  # noqa: PLC0415

            # When truncate is requested, the TRUNCATE is deferred into the same
            # transaction as the COPY INTO (see copy_into_from_url truncate_first)
            # so a failed load leaves the existing rows intact (#863).
            truncate_first = False

            exists = await table_exists(sql_target, schema, table_name, mode=ctx.auth_mode)
            if exists:
                if if_exists == "fail":
                    raise _ToolError(
                        f"Table [{schema}].[{table_name}] already exists. "
                        "Use if_exists='append', 'truncate', or 'replace'."
                    )
                if if_exists == "truncate":
                    # Destructive guard: only fire when a TRUNCATE will actually execute.
                    from fabric_dw.mcp._guards import assert_destructive_allowed  # noqa: PLC0415

                    assert_destructive_allowed()
                    truncate_first = True
                elif if_exists == "replace":
                    # replace requires destructive flag even though it raises below —
                    # the intent is destructive regardless of the error.
                    from fabric_dw.mcp._guards import assert_destructive_allowed  # noqa: PLC0415

                    assert_destructive_allowed()
                    # For remote URLs we cannot infer schema without downloading;
                    # the user should use the CLI for auto-create from local files.
                    raise _ToolError(
                        "if_exists='replace' for remote URLs requires downloading the file "
                        "to infer schema. Use if_exists='truncate' to keep the current schema, "
                        "or download the file locally and use the CLI "
                        "'tables load --file --create --if-exists replace'."
                    )
                # "append": do nothing — COPY INTO will add rows to the existing table.
            # Table does not exist.
            elif if_exists == "truncate":
                raise _ToolError(
                    f"Table [{schema}].[{table_name}] does not exist; "
                    "nothing to truncate. Create the table first or use "
                    "if_exists='fail' / 'append'."
                )
            elif if_exists == "replace":
                raise _ToolError(
                    f"Table [{schema}].[{table_name}] does not exist; "
                    "if_exists='replace' for remote URLs requires downloading the file "
                    "to infer schema. Use the CLI "
                    "'tables load --file --create --if-exists replace' instead."
                )
            else:
                # "fail", "append", or any future policy: table must exist first because
                # remote-URL load cannot infer schema to auto-create it.
                raise _ToolError(_absent_table_msg(schema, table_name))

            result = await copy_into_from_url(
                sql_target,
                schema,
                table_name,
                url,
                file_type=file_type,
                credential_type=credential_type,
                secret=secret,
                identity=identity,
                csv_options=csv_options,
                max_errors=max_errors,
                rejected_row_location=rejected_row_location,
                kind=entry.kind,
                mode=ctx.auth_mode,
                truncate_first=truncate_first,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")
