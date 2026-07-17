"""End-to-end journey test: config defaults + short-form command invocations.

This test is the regression guard for the parse-time bug described in issue #981,
where a leading optional ITEM positional swallowed the first required positional
argument when a command was invoked without an explicit warehouse argument.

The journey binary is resolved from FABRIC_DW_JOURNEY_BIN, which the integration
workflow sets to a binary installed from the project wheel -- not an editable
source install.  If that variable is absent, the test is skipped.

Journey steps, in order:
  1.  workspaces list              -- REST smoke, assert non-empty JSON array
  2.  warehouses list              -- REST smoke, assert non-empty JSON array
  3.  config set workspace <id>    -- persist default workspace
  4.  schemas create <schema>      IN SHORT FORM (ITEM omitted, warehouse from env var)
  5.  schemas list                 IN SHORT FORM, assert schema is present
  6.  config set warehouse <name>  -- persist default warehouse
  7.  tables create --name <schema>.<table> --column id:INT   IN SHORT FORM
  8.  tables list --schema <schema> IN SHORT FORM, assert table is present
  9.  schemas delete <schema> --cascade --yes  IN SHORT FORM  (ITEM omitted, from config)
 10.  schemas list                 IN SHORT FORM, assert schema is gone

Steps 4 and 9 are the #981 regression guard: they pass NAME without the optional
leading ITEM positional, which was the form that triggered the bug.  Steps 4-5
resolve the warehouse from FABRIC_DW_DEFAULT_WAREHOUSE (env var path).  Steps 6-10
use env_post(), which pops both FABRIC_DW_DEFAULT_WORKSPACE and
FABRIC_DW_DEFAULT_WAREHOUSE, so the workspace written in step 3 and the warehouse
written in step 6 are the sole resolution sources -- the config-file path is
genuinely exercised for both defaults in phase 2.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import tempfile
import uuid

import pytest

from fabric_dw.services import schemas as _schemas_svc

from .conftest import SharedWarehouseTarget
from .test_cli_smoke import (
    _SQL_SMOKE_SUBPROCESS_TIMEOUT_S,
    _STDERR_FORBIDDEN,
    _child_env,
    _sanitize_stderr,
)

_logger = logging.getLogger(__name__)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Binary resolution: prefer FABRIC_DW_JOURNEY_BIN (wheel-installed binary).
# ---------------------------------------------------------------------------

_JOURNEY_BIN: str | None = os.environ.get("FABRIC_DW_JOURNEY_BIN")


@pytest.fixture(scope="session", autouse=True)
def _require_journey_binary() -> None:
    """Skip all journey tests when FABRIC_DW_JOURNEY_BIN is not set."""
    if not _JOURNEY_BIN:
        pytest.skip(
            "FABRIC_DW_JOURNEY_BIN is not set; the integration workflow exports it "
            "after building the wheel and installing it into a clean venv. "
            "To run locally, build the wheel and set the variable to the installed binary path."
        )


# ---------------------------------------------------------------------------
# Per-step runner.
# ---------------------------------------------------------------------------


def _step(
    *args: str,
    env: dict[str, str],
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run one journey step and return the CompletedProcess.

    Immediately fails the test (with captured stdout/stderr) when the exit code
    is non-zero or a forbidden stderr pattern appears.
    """
    assert _JOURNEY_BIN is not None, "_require_journey_binary should have skipped"
    try:
        result = subprocess.run(  # noqa: S603
            [_JOURNEY_BIN, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raw_out = exc.stdout or b""
        raw_err = exc.stderr or b""
        decoded_out = (
            raw_out.decode("utf-8", errors="replace") if isinstance(raw_out, bytes) else raw_out
        )
        decoded_err = (
            raw_err.decode("utf-8", errors="replace") if isinstance(raw_err, bytes) else raw_err
        )
        pytest.fail(
            f"journey step {list(args)!r} timed out after {timeout}s.\n"
            f"stdout:\n{decoded_out}\nstderr:\n{decoded_err}"
        )

    if result.returncode != 0:
        pytest.fail(
            f"journey step {list(args)!r} exited {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    sanitized = _sanitize_stderr(result.stderr)
    for forbidden in _STDERR_FORBIDDEN:
        if forbidden in sanitized:
            pytest.fail(
                f"journey step {list(args)!r} stderr contains forbidden pattern "
                f"{forbidden!r}:\n{result.stderr}"
            )
    return result


# ---------------------------------------------------------------------------
# Journey test.
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_require_journey_binary")
async def test_config_defaults_journey(  # noqa: PLR0915
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    """Walk a realistic user journey using config defaults and short-form invocations.

    Steps 4 and 9 are the regression guard for issue #981: they invoke a command
    whose first positional is an optional ITEM (warehouse) without supplying that
    argument, relying on a configured default instead.  These steps WILL FAIL on
    main until #981 is merged.
    """
    workspace_id = str(shared_warehouse.workspace_id)
    warehouse_name = shared_warehouse.warehouse.name

    # Unique names scoped to this run so concurrent suites cannot collide.
    schema_name = f"pytest_journey_{uuid.uuid4().hex[:8]}"
    table_name = f"t_{uuid.uuid4().hex[:8]}"
    qualified_table = f"{schema_name}.{table_name}"

    with tempfile.TemporaryDirectory() as config_home:
        # -- Env builders ---------------------------------------------------
        # For steps 1-5 (before warehouse config is written): workspace comes
        # from FABRIC_DW_DEFAULT_WORKSPACE env var; warehouse comes from
        # FABRIC_DW_DEFAULT_WAREHOUSE env var.
        def env_pre() -> dict[str, str]:
            """Child env for steps 1-5: workspace + warehouse from env vars."""
            return _child_env(
                {
                    "XDG_CONFIG_HOME": config_home,
                    "FABRIC_DW_DEFAULT_WORKSPACE": workspace_id,
                    "FABRIC_DW_DEFAULT_WAREHOUSE": warehouse_name,
                }
            )

        # For steps 6-10 (after both workspace and warehouse configs are written):
        # both defaults are resolved from the config file only.  Both env vars
        # are explicitly removed so the config file values written in steps 3 and
        # 6 are the sole resolution source, genuinely exercising the config-file
        # read path for workspace as well as warehouse.
        def env_post() -> dict[str, str]:
            """Child env for steps 6-10: workspace + warehouse from config file only."""
            base = _child_env({"XDG_CONFIG_HOME": config_home})
            base.pop("FABRIC_DW_DEFAULT_WORKSPACE", None)
            base.pop("FABRIC_DW_DEFAULT_WAREHOUSE", None)
            return base

        # -- Cleanup flag ---------------------------------------------------
        schema_was_created = False

        try:
            # Step 1: workspaces list -- REST smoke
            r1 = _step("--json", "workspaces", "list", env=env_pre())
            workspaces = json.loads(r1.stdout)
            assert isinstance(workspaces, list), (
                f"workspaces list --json did not return a list: {r1.stdout!r}"
            )
            assert len(workspaces) > 0, (
                f"workspaces list --json returned an empty list: {r1.stdout!r}"
            )

            # Step 2: warehouses list -- REST smoke
            r2 = _step("--json", "warehouses", "list", env=env_pre())
            warehouses = json.loads(r2.stdout)
            assert isinstance(warehouses, list), (
                f"warehouses list --json did not return a list: {r2.stdout!r}"
            )
            assert len(warehouses) > 0, (
                f"warehouses list --json returned an empty list: {r2.stdout!r}"
            )

            # Step 3: config set workspace -- persist default workspace
            _step("config", "set", "workspace", workspace_id, env=env_pre())

            # Step 4: schemas create IN SHORT FORM -- #981 regression guard.
            # The warehouse is OMITTED here; the right-align fix shipped in #981
            # ensures Click binds item=None, name=schema_name instead of
            # assigning schema_name to the optional ITEM slot.
            #
            # Set the flag BEFORE calling _step so the finally-block cleanup runs
            # even if _step raises on a forbidden stderr pattern after the schema
            # was already created server-side.
            schema_was_created = True
            _step(
                "--json",
                "schemas",
                "create",
                schema_name,
                env=env_pre(),
                timeout=_SQL_SMOKE_SUBPROCESS_TIMEOUT_S,
            )

            # Step 5: schemas list IN SHORT FORM -- assert schema is present
            r5 = _step(
                "--json",
                "schemas",
                "list",
                env=env_pre(),
                timeout=_SQL_SMOKE_SUBPROCESS_TIMEOUT_S,
            )
            schemas_after_create = json.loads(r5.stdout)
            assert isinstance(schemas_after_create, list), (
                f"schemas list --json did not return a list: {r5.stdout!r}"
            )
            schema_names_after_create = {s["name"] for s in schemas_after_create}
            assert schema_name in schema_names_after_create, (
                f"Expected schema {schema_name!r} in schemas list after create; "
                f"got: {sorted(schema_names_after_create)}"
            )

            # Step 6: config set warehouse -- persist default warehouse
            _step("config", "set", "warehouse", warehouse_name, env=env_post())

            # Step 7: tables create IN SHORT FORM (warehouse now from config file)
            _step(
                "--json",
                "tables",
                "create",
                "--name",
                qualified_table,
                "--column",
                "id:INT",
                env=env_post(),
                timeout=_SQL_SMOKE_SUBPROCESS_TIMEOUT_S,
            )

            # Step 8: tables list IN SHORT FORM -- assert table is present
            r8 = _step(
                "--json",
                "tables",
                "list",
                "--schema",
                schema_name,
                env=env_post(),
                timeout=_SQL_SMOKE_SUBPROCESS_TIMEOUT_S,
            )
            tables_after_create = json.loads(r8.stdout)
            assert isinstance(tables_after_create, list), (
                f"tables list --json did not return a list: {r8.stdout!r}"
            )
            table_names_in_schema = {
                t["name"] for t in tables_after_create if t.get("schema_name") == schema_name
            }
            assert table_name in table_names_in_schema, (
                f"Expected table {table_name!r} in schema {schema_name!r}; "
                f"got: {sorted(table_names_in_schema)}"
            )

            # Step 9: schemas delete IN SHORT FORM -- #981 regression guard.
            # NAME is supplied without the optional leading ITEM (warehouse).
            # --yes suppresses the interactive confirmation prompt.
            # The right-align fix (#981) ensures item=None, name=schema_name.
            _step(
                "--yes",
                "--json",
                "schemas",
                "delete",
                schema_name,
                "--cascade",
                env=env_post(),
                timeout=_SQL_SMOKE_SUBPROCESS_TIMEOUT_S,
            )
            schema_was_created = False

            # Step 10: schemas list IN SHORT FORM -- assert schema is gone
            r10 = _step(
                "--json",
                "schemas",
                "list",
                env=env_post(),
                timeout=_SQL_SMOKE_SUBPROCESS_TIMEOUT_S,
            )
            schemas_after_delete = json.loads(r10.stdout)
            assert isinstance(schemas_after_delete, list), (
                f"schemas list --json did not return a list: {r10.stdout!r}"
            )
            schema_names_after_delete = {s["name"] for s in schemas_after_delete}
            assert schema_name not in schema_names_after_delete, (
                f"Expected schema {schema_name!r} to be absent after delete; "
                f"still present in: {sorted(schema_names_after_delete)}"
            )

        finally:
            # Best-effort service-layer cleanup in case the journey failed before
            # the schema was dropped by step 9.
            if schema_was_created:
                with contextlib.suppress(Exception):
                    await _schemas_svc.delete_schema(
                        shared_warehouse.sql_target, schema_name, cascade=True
                    )
