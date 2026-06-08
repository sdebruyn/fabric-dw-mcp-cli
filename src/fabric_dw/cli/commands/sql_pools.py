"""SQL Pools sub-commands for the fabric-dw CLI.

.. warning::
   SQL Pools is a **beta / preview** feature.  The underlying API may change
   before general availability.

.. warning::
   ``sql-pools set`` and ``sql-pools edit`` use a **destructive PATCH** — any
   pool *not* listed in the payload will be permanently deleted by the service.
   Both commands surface this risk explicitly before applying changes.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import click
from pydantic import ValidationError

from fabric_dw import auth as _auth
from fabric_dw.cache import LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import _coro, resolve_workspace_arg
from fabric_dw.exceptions import FabricError, PermissionDenied
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import SqlPoolsConfiguration
from fabric_dw.resolver import Resolver
from fabric_dw.services import sql_pools as _svc

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _build_http_client(ctx: CliContext) -> AsyncIterator[FabricHttpClient]:
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http


async def _resolve_workspace(http: FabricHttpClient, workspace: str) -> UUID:
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    return await resolver.workspace_id(workspace)


def _permission_hint(exc: PermissionDenied) -> click.ClickException:
    return click.ClickException(f"{exc}  (Hint: the caller must have the workspace admin role.)")


@click.group("sql-pools")
def sql_pools_group() -> None:
    """Manage workspace SQL Pools configuration (beta API)."""


@sql_pools_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@_coro
async def get_cmd(ctx: CliContext, workspace: str | None) -> None:
    """Fetch the SQL Pools configuration for WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with _build_http_client(ctx) as http:
            ws_id = await _resolve_workspace(http, ws)
            config = await _svc.get_configuration(http, ws_id)
            render(
                config.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
            )
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_pools_group.command("set")
@click.argument("workspace", required=False, default=None)
@click.option(
    "--from-file",
    "from_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Path to a JSON file containing the full SqlPoolsConfiguration payload.",
)
@click.pass_obj
@_coro
async def set_cmd(ctx: CliContext, workspace: str | None, from_file: str) -> None:
    """Replace the SQL Pools configuration for WORKSPACE from a JSON file.

    \b
    WARNING: This is a destructive PATCH — any pool NOT listed in the file
    will be permanently deleted by the Fabric service.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    raw_path = Path(from_file)
    try:
        payload = json.loads(raw_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"Cannot read {from_file}: {exc}") from exc  # noqa: TRY003

    try:
        config = SqlPoolsConfiguration.model_validate(payload)
    except ValidationError as exc:
        raise click.ClickException(f"Invalid configuration: {exc}") from exc  # noqa: TRY003

    click.echo(
        "WARNING: This is a destructive PATCH. Pools not in the file will be deleted.",
        err=True,
    )
    if not confirm("Apply configuration?", yes=ctx.yes):
        raise click.Abort()

    try:
        async with _build_http_client(ctx) as http:
            ws_id = await _resolve_workspace(http, ws)
            result = await _svc.update_configuration(http, ws_id, config)
            render(
                result.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
            )
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_pools_group.command("edit")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@_coro
async def edit_cmd(ctx: CliContext, workspace: str | None) -> None:
    """Open the current SQL Pools config in $EDITOR, then apply on save.

    Shows a diff and asks for confirmation before applying if any pool
    would be deleted (destructive PATCH semantics).
    """
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with _build_http_client(ctx) as http:
            ws_id = await _resolve_workspace(http, ws)
            current = await _svc.get_configuration(http, ws_id)

        current_json = json.dumps(current.model_dump(by_alias=True, mode="json"), indent=2)

        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or _default_editor()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix="fabric-dw-sql-pools-"
        ) as tmp:
            tmp.write(current_json)
            tmp_path = tmp.name

        try:
            subprocess.run([editor, tmp_path], check=True)  # noqa: S603
            edited_text = Path(tmp_path).read_text()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        if edited_text.strip() == current_json.strip():
            click.echo("No changes detected. Aborting.")
            return

        try:
            new_payload = json.loads(edited_text)
            new_config = SqlPoolsConfiguration.model_validate(new_payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise click.ClickException(f"Invalid configuration after editing: {exc}") from exc  # noqa: TRY003

        current_names = {p.name for p in current.custom_sql_pools}
        new_names = {p.name for p in new_config.custom_sql_pools}
        deleted_names = current_names - new_names

        diff_lines = list(
            difflib.unified_diff(
                current_json.splitlines(keepends=True),
                edited_text.splitlines(keepends=True),
                fromfile="current",
                tofile="new",
            )
        )
        if diff_lines:
            click.echo("".join(diff_lines))

        if deleted_names:
            click.echo(
                f"\nWARNING: The following pools will be DELETED: "
                f"{', '.join(sorted(deleted_names))}",
                err=True,
            )

        if not confirm("Apply configuration?", yes=ctx.yes):
            raise click.Abort()  # noqa: TRY301

        async with _build_http_client(ctx) as http:
            ws_id = await _resolve_workspace(http, ws)
            result = await _svc.update_configuration(http, ws_id, new_config)
            render(
                result.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
            )

    except click.Abort:
        raise
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_pools_group.command("enable")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@_coro
async def enable_cmd(ctx: CliContext, workspace: str | None) -> None:
    """Enable custom SQL Pools for WORKSPACE (preserves pool configuration)."""
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with _build_http_client(ctx) as http:
            ws_id = await _resolve_workspace(http, ws)
            result = await _svc.enable(http, ws_id)
            render(
                result.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
            )
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_pools_group.command("disable")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@_coro
async def disable_cmd(ctx: CliContext, workspace: str | None) -> None:
    """Disable custom SQL Pools for WORKSPACE (preserves pool configuration).

    Re-enabling with 'sql-pools enable' restores the previously saved configuration.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with _build_http_client(ctx) as http:
            ws_id = await _resolve_workspace(http, ws)
            result = await _svc.disable(http, ws_id)
            render(
                result.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
            )
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


def _default_editor() -> str:
    """Return a sensible default editor when neither $VISUAL nor $EDITOR is set."""
    if sys.platform == "win32":
        return "notepad"
    return "vi"
