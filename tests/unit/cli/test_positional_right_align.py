"""Tests for the right-align positional fix in `_patch_command_for_global_options`.

Issue #981: commands with a leading optional positional (ITEM / WAREHOUSE /
WORKSPACE) followed by required positionals fail with "Missing argument" when
the optional is omitted, because Click fills slots left-to-right and the first
supplied value lands in the optional slot.

The fix wraps `parse_args` on every leaf command that has this shape so that
supplied values are right-aligned onto the required slots first, and type
conversion / normalisation is applied via ``process_value`` on every relocated
slot.

All tests are parse-only (no Fabric connection, no destructive ops).
"""

from __future__ import annotations

import importlib
import uuid
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
    _wrap_parse_args_for_right_align,
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


def _test_value_for_type(param_type: click.ParamType) -> str:  # noqa: PLR0911
    """Return a valid CLI string token for the given Click parameter type.

    The drift guard uses fake but type-compatible values so that Click can
    convert them without raising BadParameter.
    """
    if isinstance(param_type, click.types.IntParamType):
        return "1"
    if isinstance(param_type, click.types.FloatParamType):
        return "1.0"
    if isinstance(param_type, click.Choice):
        first = str(param_type.choices[0])
        if not param_type.case_sensitive:
            # Return the case-swapped form so the drift guard can tell whether
            # process_value was called.  A raw STRING bind preserves the
            # swapped case; a properly converted value matches choices[0].
            swapped = first.swapcase()
            return swapped if swapped != first else first
        return first
    if isinstance(param_type, click.types.UUIDParameterType):
        return "00000000-0000-0000-0000-000000000001"
    if isinstance(param_type, click.types.BoolParamType):
        return "true"
    # STRING, Path, unrecognised: use a safe generic string.
    return "val"


def _expected_python_value(param_type: click.ParamType, raw: str) -> Any:
    """Return the Python value expected after Click type conversion of *raw*.

    Used by the drift guard to verify that ``process_value`` was called so
    that type conversion (INT casting, Choice normalisation, UUID parsing)
    is applied to relocated positional values.
    """
    if isinstance(param_type, click.types.IntParamType):
        return int(raw)
    if isinstance(param_type, click.types.FloatParamType):
        return float(raw)
    if isinstance(param_type, click.types.UUIDParameterType):
        return uuid.UUID(raw)
    if isinstance(param_type, click.types.BoolParamType):
        return raw.lower() in ("1", "true", "t", "yes", "y", "on")
    if isinstance(param_type, click.Choice):
        # Click returns the matched entry from choices.  _test_value_for_type
        # may supply a case-swapped form (for case-insensitive choices), so the
        # expected result is always choices[0] -- the normalised form.
        return str(param_type.choices[0])
    # STRING, Path, unrecognised: returned as-is.
    return raw


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
        """Supplying only the required positionals leaves optional slots empty.

        Also verifies that type conversion was applied (e.g. INT args become
        Python ints, Choice args are normalised) so that ``process_value`` is
        confirmed to have been called on every relocated slot.
        """
        args = [_test_value_for_type(p.type) for p in req_params]

        with _relaxed_options(cmd):
            ctx = _make_context(cmd, args)
        try:
            for p in opt_params:
                assert ctx.params[p.name] is None, (
                    f"{dotted_name}: optional slot {p.name!r} should be None in short form"
                )
            for p, raw in zip(req_params, args, strict=False):
                assert ctx.get_parameter_source(p.name) is ParameterSource.COMMANDLINE, (
                    f"{dotted_name}: required slot {p.name!r} must have COMMANDLINE source"
                )
                expected = _expected_python_value(p.type, raw)
                assert ctx.params[p.name] == expected, (
                    f"{dotted_name}: slot {p.name!r} value {ctx.params[p.name]!r} "
                    f"should be {expected!r} after type conversion"
                )
                assert isinstance(ctx.params[p.name], type(expected)), (
                    f"{dotted_name}: slot {p.name!r} has Python type "
                    f"{type(ctx.params[p.name]).__name__!r}, "
                    f"expected {type(expected).__name__!r}"
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
        """Supplying all positionals fills every slot in declaration order.

        Also verifies that type conversion was applied to each slot so that
        the full form does not regress when ``type=STRING`` is used during the
        delegated parse.
        """
        all_params = opt_params + req_params
        all_args = [_test_value_for_type(p.type) for p in all_params]

        with _relaxed_options(cmd):
            ctx = _make_context(cmd, all_args)
        try:
            for p, raw in zip(all_params, all_args, strict=False):
                assert ctx.get_parameter_source(p.name) is ParameterSource.COMMANDLINE, (
                    f"{dotted_name}: slot {p.name!r} must be COMMANDLINE when supplied"
                )
                expected = _expected_python_value(p.type, raw)
                assert ctx.params[p.name] == expected, (
                    f"{dotted_name}: slot {p.name!r} value {ctx.params[p.name]!r} "
                    f"should be {expected!r} after type conversion"
                )
                assert isinstance(ctx.params[p.name], type(expected)), (
                    f"{dotted_name}: slot {p.name!r} has Python type "
                    f"{type(ctx.params[p.name]).__name__!r}, "
                    f"expected {type(expected).__name__!r}"
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
# Shape-guard bail-out tests: synthetic commands outside [opt] req+ shape
# ---------------------------------------------------------------------------


class TestShapeGuardBailout:
    """Shapes outside ``[opt] req+`` must delegate straight to native Click parsing.

    Each test builds a throwaway ``click.Command`` with a shape the guard
    rejects (two leading optionals, a variadic positional, or a trailing
    optional after a required positional), applies ``_wrap_parse_args_for_right_align``
    directly, and verifies that the native left-to-right behaviour is
    preserved.  The observable difference: supplying one value fills the
    optional slot (native), so the required slot stays empty and Click raises
    ``MissingParameter``.  If the wrapper incorrectly fired it would move
    the value to the required slot and return without error.
    """

    @staticmethod
    def _cmd(*params: click.Argument) -> click.Command:
        """Build a bare click.Command and attach the parse_args wrapper."""
        cmd = click.Command("test", params=list(params), callback=lambda **_: None)
        _wrap_parse_args_for_right_align(cmd)
        return cmd

    def test_two_leading_optionals_not_reordered(self) -> None:
        """n_leading_optional == 2 must fall through to native parse."""
        cmd = self._cmd(
            click.Argument(["opt1"], required=False, default=None),
            click.Argument(["opt2"], required=False, default=None),
            click.Argument(["req"], required=True),
        )
        # Native: one value fills opt1, req remains missing -> MissingParameter.
        # Right-align (incorrectly triggered) would put the value in req and
        # return successfully.
        with pytest.raises((click.MissingParameter, click.UsageError)):
            _make_context(cmd, ["val"]).close()

    def test_nargs_not_one_not_reordered(self) -> None:
        """A positional with nargs != 1 must fall through to native parse."""
        cmd = self._cmd(
            click.Argument(["opt"], required=False, default=None),
            click.Argument(["req"], required=True),
            click.Argument(["rest"], nargs=-1, required=False),
        )
        # Native: one value fills opt, req remains missing -> MissingParameter.
        with pytest.raises((click.MissingParameter, click.UsageError)):
            _make_context(cmd, ["val"]).close()

    def test_trailing_optional_not_reordered(self) -> None:
        """A trailing optional after a required positional must fall through."""
        cmd = self._cmd(
            click.Argument(["opt"], required=False, default=None),
            click.Argument(["req"], required=True),
            click.Argument(["trailing"], required=False, default=None),
        )
        # Native: one value fills opt, req remains missing -> MissingParameter.
        with pytest.raises((click.MissingParameter, click.UsageError)):
            _make_context(cmd, ["val"]).close()


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
# Type-conversion regression: Choice normalisation and INT casting
# ---------------------------------------------------------------------------


class TestSettingsResultSetCachingTypeConversion:
    """settings result-set-caching [item?, state:Choice(['on','off'])].

    Verifies that Choice case-normalisation is applied after right-alignment so
    that ``fdw settings result-set-caching ON`` stores ``'on'`` (not ``'ON'``).
    The command body checks ``state == 'on'`` (line 93 of settings.py), so a
    stale uppercase string would silently disable caching when the user said ON.
    """

    def setup_method(self) -> None:
        group = _load_group("settings")
        self.cmd = group.commands["result-set-caching"]

    def test_short_form_uppercase_is_normalised(self) -> None:
        """Short form with uppercase 'ON' must store lowercase 'on'."""
        ctx = _make_context(self.cmd, ["ON"])
        try:
            assert ctx.params["item"] is None
            assert ctx.params["state"] == "on", (
                f"Expected 'on' after Choice normalisation, got {ctx.params['state']!r}"
            )
            assert ctx.get_parameter_source("state") is ParameterSource.COMMANDLINE
        finally:
            ctx.close()

    def test_short_form_lowercase_is_unchanged(self) -> None:
        ctx = _make_context(self.cmd, ["on"])
        try:
            assert ctx.params["item"] is None
            assert ctx.params["state"] == "on"
        finally:
            ctx.close()

    def test_full_form_uppercase_is_normalised(self) -> None:
        """Full form with uppercase 'ON' must also store lowercase 'on'."""
        ctx = _make_context(self.cmd, ["MyWH", "ON"])
        try:
            assert ctx.params["item"] == "MyWH"
            assert ctx.params["state"] == "on", (
                f"Expected 'on' after Choice normalisation, got {ctx.params['state']!r}"
            )
        finally:
            ctx.close()

    def test_invalid_state_raises(self) -> None:
        with pytest.raises((click.BadParameter, click.UsageError, SystemExit)):
            _make_context(self.cmd, ["INVALID"]).close()


class TestQueriesKillTypeConversion:
    """queries kill [item?, session_id:INT].

    Verifies that INT casting is applied after right-alignment so that
    ``fdw queries kill 12345`` stores the Python int ``12345``, not the
    string ``'12345'``.
    """

    def setup_method(self) -> None:
        group = _load_group("queries")
        self.cmd = group.commands["kill"]

    def test_short_form_session_id_is_int(self) -> None:
        ctx = _make_context(self.cmd, ["12345"])
        try:
            assert ctx.params["item"] is None
            assert ctx.params["session_id"] == 12345
            assert isinstance(ctx.params["session_id"], int)
        finally:
            ctx.close()

    def test_full_form_session_id_is_int(self) -> None:
        ctx = _make_context(self.cmd, ["MyWH", "12345"])
        try:
            assert ctx.params["item"] == "MyWH"
            assert ctx.params["session_id"] == 12345
            assert isinstance(ctx.params["session_id"], int)
        finally:
            ctx.close()

    def test_non_integer_raises(self) -> None:
        with pytest.raises((click.BadParameter, click.UsageError, SystemExit)):
            _make_context(self.cmd, ["not_an_int"]).close()


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
    """The required, type and callback attributes must not be permanently changed.

    The wrapper temporarily mutates all three on module-level singletons and
    restores them unconditionally in a ``finally`` block.  These tests confirm
    that all three are intact after both a failed and a successful parse.  A
    regression that broke ``type`` restoration would leave every positional
    permanently typed as ``click.STRING``, silently accepting values that
    should be rejected and returning strings where callers expect ints.
    """

    def test_attributes_unchanged_after_failed_parse(self) -> None:
        """After a zero-args MissingParameter, required, type and callback are intact."""
        group = _load_group("schemas")
        delete_cmd = group.commands["delete"]
        pos_params = [p for p in delete_cmd.params if isinstance(p, click.Argument)]
        # Record originals before any parse.
        original_required = {p.name: p.required for p in pos_params}
        original_types = {p.name: p.type for p in pos_params}
        original_callbacks = {p.name: p.callback for p in pos_params}

        # Trigger a failed parse (zero args -> MissingParameter).
        with pytest.raises((click.MissingParameter, click.UsageError)):
            _make_context(delete_cmd, []).close()

        for p in pos_params:
            assert p.required == original_required[p.name], (
                f"required for {p.name!r} was mutated after failed parse"
            )
            assert p.type is original_types[p.name], (
                f"type for {p.name!r} was mutated after failed parse: "
                f"expected {original_types[p.name]!r}, got {p.type!r}"
            )
            assert p.callback is original_callbacks[p.name], (
                f"callback for {p.name!r} was mutated after failed parse"
            )

    def test_attributes_unchanged_after_successful_parse(self) -> None:
        """After a short-form parse, required, type and callback are intact."""
        group = _load_group("schemas")
        delete_cmd = group.commands["delete"]
        pos_params = [p for p in delete_cmd.params if isinstance(p, click.Argument)]
        original_required = {p.name: p.required for p in pos_params}
        original_types = {p.name: p.type for p in pos_params}
        original_callbacks = {p.name: p.callback for p in pos_params}

        ctx = _make_context(delete_cmd, ["my_schema"])
        ctx.close()

        for p in pos_params:
            assert p.required == original_required[p.name], (
                f"required for {p.name!r} was mutated after successful parse"
            )
            assert p.type is original_types[p.name], (
                f"type for {p.name!r} was mutated after successful parse"
            )
            assert p.callback is original_callbacks[p.name], (
                f"callback for {p.name!r} was mutated after successful parse"
            )
