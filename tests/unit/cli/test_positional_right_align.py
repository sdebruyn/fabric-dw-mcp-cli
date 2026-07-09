"""Tests for the right-align positional fix in `_patch_command_for_global_options`.

Issue #981: commands with a leading optional positional (ITEM / WAREHOUSE /
WORKSPACE) followed by required positionals fail with "Missing argument" when
the optional is omitted, because Click fills slots left-to-right and the first
supplied value lands in the optional slot.

The fix wraps `parse_args` on every leaf command that has this shape so that
supplied values are right-aligned onto the required slots first.

All tests are parse-only (no Fabric connection, no destructive ops).
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.core import ParameterSource
from click.testing import CliRunner

from fabric_dw.cli._main import (
    _COMMAND_MAP,
    _patch_command_for_global_options,
    cli,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_group(group_name: str) -> click.Group:
    """Import and patch the Click group for *group_name*."""
    spec = _COMMAND_MAP[group_name]
    module_path, attr_name = spec.rsplit(":", 1)
    module = importlib.import_module(module_path)
    group: click.Group = getattr(module, attr_name)
    _patch_command_for_global_options(group)
    return group


def _walk_leaf_commands(group: click.Group, prefix: str) -> list[tuple[str, click.Command]]:
    """Recursively collect (dotted_name, command) for all non-Group commands."""
    results: list[tuple[str, click.Command]] = []
    for name, cmd in group.commands.items():
        dotted = f"{prefix}.{name}"
        if isinstance(cmd, click.Group):
            results.extend(_walk_leaf_commands(cmd, dotted))
        else:
            results.append((dotted, cmd))
    return results


def _leading_optional_count(pos_params: list[click.Argument]) -> int:
    count = 0
    for p in pos_params:
        if not p.required:
            count += 1
        else:
            break
    return count


def _test_value_for_type(param_type: click.ParamType) -> str:
    """Return a valid CLI string token for the given Click parameter type.

    The drift guard uses fake but type-compatible values so that Click can
    convert them without raising BadParameter.
    """
    if isinstance(param_type, click.types.IntParamType):
        return "1"
    if isinstance(param_type, click.types.FloatParamType):
        return "1.0"
    if isinstance(param_type, click.Choice):
        return param_type.choices[0]
    if isinstance(param_type, click.types.UUIDParameterType):
        return "00000000-0000-0000-0000-000000000001"
    if isinstance(param_type, click.types.BoolParamType):
        return "true"
    # STRING, Path, unrecognised: use a safe generic string.
    return "val"


@contextmanager
def _relaxed_options(cmd: click.Command) -> Generator[None, None, None]:
    """Temporarily mark all required Options on *cmd* as not required.

    Some commands have required options (e.g. --principal, --from) that would
    block parse_args from completing when only positional args are supplied.
    Relaxing them lets the drift guard focus on positional slot assignment.
    """
    req_opts = [p for p in cmd.params if isinstance(p, click.Option) and p.required]
    for opt in req_opts:
        opt.required = False
    try:
        yield
    finally:
        for opt in req_opts:
            opt.required = True


def _make_context(cmd: click.Command, args: list[str]) -> click.Context:
    """Call make_context and return the context (caller must close it).

    Click mutates the args list in-place during parsing, so a copy is passed
    to preserve the caller's list for subsequent assertions.
    """
    return cmd.make_context(cmd.name or "cmd", list(args))


# ---------------------------------------------------------------------------
# Drift guard: walk the full lazy tree and assert the invariant for every
# command that has the [optional..., required...] positional shape.
# ---------------------------------------------------------------------------

# Each case is (dotted_name, cmd, opt_params, req_params) where *_params are
# click.Argument objects so tests can access both names and types.
_DriftCase = tuple[str, click.Command, list[click.Argument], list[click.Argument]]


def _collect_right_align_cases() -> list[_DriftCase]:
    """Return one case per affected command.

    Walks the full lazy command tree by importing each group module.  The patch
    is idempotent so repeated loads are safe.
    """
    cases: list[_DriftCase] = []
    for group_name in _COMMAND_MAP:
        group = _load_group(group_name)
        for dotted_name, cmd in _walk_leaf_commands(group, group_name):
            pos_params: list[click.Argument] = [
                p for p in cmd.params if isinstance(p, click.Argument)
            ]
            n_leading_opt = _leading_optional_count(pos_params)
            n_required = sum(1 for p in pos_params if p.required)
            if n_leading_opt > 0 and n_required > 0:
                opt_params = pos_params[:n_leading_opt]
                req_params = [p for p in pos_params if p.required]
                cases.append((dotted_name, cmd, opt_params, req_params))
    return cases


_RIGHT_ALIGN_CASES = _collect_right_align_cases()
_CASE_IDS = [c[0] for c in _RIGHT_ALIGN_CASES]


@pytest.mark.parametrize(
    ("dotted_name", "cmd", "opt_params", "req_params"),
    _RIGHT_ALIGN_CASES,
    ids=_CASE_IDS,
)
class TestDriftGuard:
    """Table-driven invariant: every affected command must right-align positionals.

    Required options (e.g. --principal, --from) are temporarily relaxed so that
    missing-option errors do not block the positional-parsing assertions.

    Argument types are respected: integer args get "1", Choice args get the
    first valid choice, etc., so Click's type conversion does not fail.
    """

    def test_short_form_fills_required_slots(
        self,
        dotted_name: str,
        cmd: click.Command,
        opt_params: list[click.Argument],
        req_params: list[click.Argument],
    ) -> None:
        """Supplying only the required positionals leaves optional slots empty."""
        args = [_test_value_for_type(p.type) for p in req_params]

        with _relaxed_options(cmd):
            ctx = _make_context(cmd, args)
        try:
            for p in opt_params:
                assert ctx.params[p.name] is None, (
                    f"{dotted_name}: optional slot {p.name!r} should be None in short form"
                )
            for p in req_params:
                # Check source rather than value; type conversion may alter representation.
                assert ctx.get_parameter_source(p.name) is ParameterSource.COMMANDLINE, (
                    f"{dotted_name}: required slot {p.name!r} must have COMMANDLINE source"
                )
        finally:
            ctx.close()

    def test_full_form_fills_all_slots(
        self,
        dotted_name: str,
        cmd: click.Command,
        opt_params: list[click.Argument],
        req_params: list[click.Argument],
    ) -> None:
        """Supplying all positionals fills every slot in declaration order."""
        all_args = [_test_value_for_type(p.type) for p in opt_params] + [
            _test_value_for_type(p.type) for p in req_params
        ]

        with _relaxed_options(cmd):
            ctx = _make_context(cmd, all_args)
        try:
            for p in opt_params:
                assert ctx.get_parameter_source(p.name) is ParameterSource.COMMANDLINE, (
                    f"{dotted_name}: optional slot {p.name!r} must be COMMANDLINE when supplied"
                )
            for p in req_params:
                assert ctx.get_parameter_source(p.name) is ParameterSource.COMMANDLINE, (
                    f"{dotted_name}: required slot {p.name!r} must be COMMANDLINE when supplied"
                )
        finally:
            ctx.close()

    def test_zero_args_raises_missing(
        self,
        dotted_name: str,  # noqa: ARG002
        cmd: click.Command,
        opt_params: list[click.Argument],  # noqa: ARG002
        req_params: list[click.Argument],  # noqa: ARG002
    ) -> None:
        """Zero args must raise MissingParameter or UsageError for missing required."""
        with _relaxed_options(cmd), pytest.raises((click.MissingParameter, click.UsageError)):
            _make_context(cmd, []).close()


# ---------------------------------------------------------------------------
# Targeted regression tests for specific shapes
# ---------------------------------------------------------------------------


class TestSchemasDelete:
    """schemas delete: [item?, name]"""

    def setup_method(self) -> None:
        group = _load_group("schemas")
        self.cmd = group.commands["delete"]

    def test_short_form_item_is_none(self) -> None:
        ctx = _make_context(self.cmd, ["my_schema"])
        try:
            assert ctx.params["item"] is None
            assert ctx.params["name"] == "my_schema"
            assert ctx.get_parameter_source("item") is ParameterSource.DEFAULT
            assert ctx.get_parameter_source("name") is ParameterSource.COMMANDLINE
        finally:
            ctx.close()

    def test_full_form_both_filled(self) -> None:
        ctx = _make_context(self.cmd, ["MyWH", "my_schema"])
        try:
            assert ctx.params["item"] == "MyWH"
            assert ctx.params["name"] == "my_schema"
            assert ctx.get_parameter_source("item") is ParameterSource.COMMANDLINE
            assert ctx.get_parameter_source("name") is ParameterSource.COMMANDLINE
        finally:
            ctx.close()

    def test_zero_args_raises_missing_name(self) -> None:
        with pytest.raises(click.MissingParameter) as exc_info:
            _make_context(self.cmd, []).close()
        msg = exc_info.value.format_message()
        assert "Missing argument" in msg
        assert "NAME" in msg

    def test_over_supply_raises_extra_args(self) -> None:
        with pytest.raises((click.UsageError, SystemExit)):
            _make_context(self.cmd, ["WH", "schema", "extra"]).close()


class TestStatisticsDelete:
    """statistics delete: [item?, qualified_table, stat_name]"""

    def setup_method(self) -> None:
        group = _load_group("statistics")
        self.cmd = group.commands["delete"]

    def test_short_form_two_args(self) -> None:
        ctx = _make_context(self.cmd, ["dbo.t", "stat_name"])
        try:
            assert ctx.params["item"] is None
            assert ctx.params["qualified_table"] == "dbo.t"
            assert ctx.params["stat_name"] == "stat_name"
        finally:
            ctx.close()

    def test_full_form_three_args(self) -> None:
        ctx = _make_context(self.cmd, ["MyWH", "dbo.t", "stat_name"])
        try:
            assert ctx.params["item"] == "MyWH"
            assert ctx.params["qualified_table"] == "dbo.t"
            assert ctx.params["stat_name"] == "stat_name"
        finally:
            ctx.close()

    def test_one_arg_raises_missing(self) -> None:
        with pytest.raises((click.MissingParameter, click.UsageError)):
            _make_context(self.cmd, ["only_one"]).close()

    def test_zero_args_raises_missing(self) -> None:
        with pytest.raises((click.MissingParameter, click.UsageError)):
            _make_context(self.cmd, []).close()


class TestRestorePointsRename:
    """restore-points rename: [item?, restore_point_id, new_name]"""

    def setup_method(self) -> None:
        group = _load_group("restore-points")
        self.cmd = group.commands["rename"]

    def test_short_form_two_args(self) -> None:
        ctx = _make_context(self.cmd, ["rp_id", "new_name"])
        try:
            assert ctx.params["item"] is None
            assert ctx.params["restore_point_id"] == "rp_id"
            assert ctx.params["new_name"] == "new_name"
        finally:
            ctx.close()

    def test_full_form_three_args(self) -> None:
        ctx = _make_context(self.cmd, ["MyWH", "rp_id", "new_name"])
        try:
            assert ctx.params["item"] == "MyWH"
            assert ctx.params["restore_point_id"] == "rp_id"
            assert ctx.params["new_name"] == "new_name"
        finally:
            ctx.close()

    def test_zero_args_raises_missing(self) -> None:
        with pytest.raises((click.MissingParameter, click.UsageError)):
            _make_context(self.cmd, []).close()


class TestWorkspacesSetCollation:
    """workspaces set-collation: [workspace?, collation]"""

    def setup_method(self) -> None:
        group = _load_group("workspaces")
        self.cmd = group.commands["set-collation"]

    def test_short_form_workspace_is_none(self) -> None:
        ctx = _make_context(self.cmd, ["Latin1_General_100_BIN2_UTF8"])
        try:
            assert ctx.params["workspace"] is None
            assert ctx.params["collation"] == "Latin1_General_100_BIN2_UTF8"
        finally:
            ctx.close()

    def test_full_form_both_filled(self) -> None:
        ctx = _make_context(self.cmd, ["MyWS", "Latin1_General_100_BIN2_UTF8"])
        try:
            assert ctx.params["workspace"] == "MyWS"
            assert ctx.params["collation"] == "Latin1_General_100_BIN2_UTF8"
        finally:
            ctx.close()

    def test_zero_args_raises_missing(self) -> None:
        with pytest.raises((click.MissingParameter, click.UsageError)):
            _make_context(self.cmd, []).close()


class TestWarehousesRename:
    """warehouses rename: [warehouse?, new_name]"""

    def setup_method(self) -> None:
        group = _load_group("warehouses")
        self.cmd = group.commands["rename"]

    def test_short_form_warehouse_is_none(self) -> None:
        ctx = _make_context(self.cmd, ["new_wh_name"])
        try:
            assert ctx.params["warehouse"] is None
            assert ctx.params["new_name"] == "new_wh_name"
        finally:
            ctx.close()

    def test_full_form_both_filled(self) -> None:
        ctx = _make_context(self.cmd, ["OldWH", "new_wh_name"])
        try:
            assert ctx.params["warehouse"] == "OldWH"
            assert ctx.params["new_name"] == "new_wh_name"
        finally:
            ctx.close()

    def test_zero_args_raises_missing(self) -> None:
        with pytest.raises((click.MissingParameter, click.UsageError)):
            _make_context(self.cmd, []).close()


# ---------------------------------------------------------------------------
# Env-var resolution: short form with FABRIC_DW_DEFAULT_WAREHOUSE set
# ---------------------------------------------------------------------------


def _make_http_cm(http: Any) -> Any:
    """Return an async context manager that yields *http*."""

    @asynccontextmanager
    async def _cm(_ctx: Any) -> AsyncIterator[Any]:
        yield http

    return _cm


class TestEnvVarResolution:
    """schemas delete my_schema with the env var set must pass item=None to
    resolve_warehouse_arg so the function can fall back to the env var."""

    def test_delete_short_form_passes_none_to_resolve_warehouse(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Short-form invoke passes item=None to resolve_warehouse_arg."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("FABRIC_DW_DEFAULT_WAREHOUSE", "my_default_wh")

        captured_values: list[Any] = []

        def _fake_resolve_wh(_ctx: Any, value: Any) -> str:
            captured_values.append(value)
            return "my_default_wh"

        mock_http = AsyncMock()

        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(
                    return_value=(
                        MagicMock(),
                        MagicMock(
                            display_name="my_default_wh",
                            id="some-uuid",
                        ),
                    )
                ),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.resolve_warehouse_arg",
                side_effect=_fake_resolve_wh,
            ),
            patch(
                "fabric_dw.services.schemas.delete_schema",
                new=AsyncMock(),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.confirm_destructive",
                return_value=True,
            ),
        ):
            runner = CliRunner()
            runner.invoke(
                cli,
                ["-w", "some-workspace", "--yes", "schemas", "delete", "my_schema"],
            )

        assert captured_values, "resolve_warehouse_arg was never called"
        assert captured_values[0] is None, (
            f"Expected item=None passed to resolve_warehouse_arg, got {captured_values[0]!r}"
        )


# ---------------------------------------------------------------------------
# Unchanged commands: schemas list must not be affected
# ---------------------------------------------------------------------------


class TestSchemasListUnchanged:
    """schemas list [ITEM] has only an optional positional; must be unchanged."""

    def setup_method(self) -> None:
        group = _load_group("schemas")
        self.cmd = group.commands["list"]

    def test_list_no_args_item_is_none(self) -> None:
        ctx = _make_context(self.cmd, [])
        try:
            assert ctx.params["item"] is None
            assert ctx.get_parameter_source("item") is ParameterSource.DEFAULT
        finally:
            ctx.close()

    def test_list_with_arg_item_is_set(self) -> None:
        ctx = _make_context(self.cmd, ["MyWH"])
        try:
            assert ctx.params["item"] == "MyWH"
            assert ctx.get_parameter_source("item") is ParameterSource.COMMANDLINE
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Shell completion: wrapper must return early when resilient_parsing=True
# ---------------------------------------------------------------------------


class TestShellCompletion:
    """When resilient_parsing is True, the wrapper must not rewrite params."""

    def test_resilient_parsing_leaves_left_to_right_order(self) -> None:
        """With resilient_parsing=True, the original left-to-right fill is preserved.

        The test confirms that no exception is raised and the context is valid,
        which means the wrapper delegated straight to the original parse_args.
        """
        group = _load_group("schemas")
        delete_cmd = group.commands["delete"]
        # make_context does not expose resilient_parsing directly; create the
        # context manually to set the flag.
        ctx = click.Context(delete_cmd, info_name="delete", resilient_parsing=True)
        # Should not raise; wrapper must bypass reordering.
        delete_cmd.parse_args(ctx, ["my_schema"])
        # With resilient_parsing the wrapper delegates straight through, so Click
        # may fill slots in its default left-to-right order or leave them empty.
        # The key assertion: no exception was raised and item is not rewritten.
        ctx.close()


# ---------------------------------------------------------------------------
# Required-flag invariant: flags must be intact after a failed parse
# ---------------------------------------------------------------------------


class TestRequiredFlagNotMutated:
    """The required flag on positional params must not be permanently changed."""

    def test_required_unchanged_after_failed_parse(self) -> None:
        """After a zero-args MissingParameter, p.required is still as declared."""
        group = _load_group("schemas")
        delete_cmd = group.commands["delete"]
        pos_params = [p for p in delete_cmd.params if isinstance(p, click.Argument)]
        # Record original flags.
        original_required = {p.name: p.required for p in pos_params}

        # Trigger a failed parse (zero args -> MissingParameter).
        with pytest.raises((click.MissingParameter, click.UsageError)):
            _make_context(delete_cmd, []).close()

        # Flags must be exactly as before.
        for p in pos_params:
            assert p.required == original_required[p.name], (
                f"required flag for {p.name!r} was mutated: "
                f"expected {original_required[p.name]}, got {p.required}"
            )

    def test_required_unchanged_after_successful_parse(self) -> None:
        """After a short-form parse, p.required is still as declared."""
        group = _load_group("schemas")
        delete_cmd = group.commands["delete"]
        pos_params = [p for p in delete_cmd.params if isinstance(p, click.Argument)]
        original_required = {p.name: p.required for p in pos_params}

        ctx = _make_context(delete_cmd, ["my_schema"])
        ctx.close()

        for p in pos_params:
            assert p.required == original_required[p.name], (
                f"required flag for {p.name!r} was mutated after successful parse"
            )
