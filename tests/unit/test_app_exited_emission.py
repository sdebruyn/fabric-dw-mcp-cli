"""Regression tests: app_exited is emitted before telemetry shutdown.

These tests prove that the fix for issue #664 works correctly: ``record_app_exited``
is called (and therefore enqueued in the telemetry pipeline) BEFORE
``shutdown_telemetry`` is invoked, so ``app_exited`` is not silently dropped by an
already-shut-down ``BatchLogRecordProcessor``.

This module is listed in ``_TELEMETRY_SELF_MANAGED_MODULES`` in ``tests/conftest.py``
so ``_disable_telemetry_globally`` does NOT inject ``FABRIC_DISABLE_TELEMETRY=1``,
and ``_isolate_telemetry_endpoint`` installs the fake connection string as a safe
default.  Ordering tests patch ``record_app_exited`` / ``shutdown_telemetry``
directly (no real SDK calls).  Delivery tests patch ``telemetry_enabled`` to
``True`` and spy on ``emit_event`` (no real SDK or network calls).

Implementation notes
--------------------
Tests invoke ``workspaces --help`` (or ``workspaces list`` with a patched invoke)
rather than root ``--help`` or a dynamically-registered command because:

- Root ``--help`` exits before the group callback runs
  (``invoke_without_command=False``), so ``record_app_started`` / ``_on_close``
  are never called.
- ``_LazyGroup`` resolves commands from ``_COMMAND_MAP``, not a ``commands``
  attribute, so dynamically-registered commands via ``@cli.command`` are not found.
- ``workspaces --help`` and ``workspaces list`` trigger the full group-callback
  path and cause ``_on_close`` to fire, without any real auth or network call.

Module-reload isolation note (TestAppExitedDelivery)
-----------------------------------------------------
``tests/unit/test_telemetry.py`` force-reloads ``fabric_dw.telemetry`` via
``importlib.reload`` / ``_reload_telemetry()`` during several tests.  After a
reload, ``sys.modules["fabric_dw.telemetry"]`` points to a new module object, but
functions imported via ``from fabric_dw.telemetry import X`` in ``_main.py``
retain their ``__globals__`` bound to the *original* module object.  A string-
based ``patch("fabric_dw.telemetry.emit_event", ...)`` would therefore patch the
*wrong* namespace (the reloaded module) while ``record_app_exited`` continues to
look up ``emit_event`` in the *original* module's globals.

The delivery tests avoid this by patching through ``_main_module``'s imported
references: ``record_app_exited``'s ``__globals__`` is the authoritative namespace
regardless of any reloads in other tests.  We obtain it at test-call time (not at
import time) to pick up the live object.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import fabric_dw.cli._main as _main_module
from fabric_dw.cli._main import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _workspaces_group():  # type: ignore[return]
    """Return the lazily-loaded workspaces group object."""
    import fabric_dw.cli.commands.workspaces as _ws  # noqa: PLC0415

    return _ws.workspaces_group


def _telemetry_globals() -> dict:
    """Return the live ``__globals__`` dict used by ``record_app_exited``.

    ``fabric_dw.cli._main`` imports ``record_app_exited`` via
    ``from fabric_dw.telemetry import record_app_exited``.  The function's
    ``__globals__`` is the ``fabric_dw.telemetry`` module namespace that was
    current at import time.

    If ``tests/unit/test_telemetry.py`` has force-reloaded ``fabric_dw.telemetry``
    (via ``_reload_telemetry()``), ``sys.modules["fabric_dw.telemetry"]`` points
    to a *new* module object, but ``_main_module.record_app_exited.__globals__``
    still points to the *original* module object — the one that the live
    ``_on_close`` closure uses.  Patching through this dict guarantees we hit
    the right namespace regardless of reload history.
    """
    return _main_module.record_app_exited.__globals__  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    """Return a CliRunner instance."""
    return CliRunner()


# ---------------------------------------------------------------------------
# Ordering tests (record_app_exited + shutdown_telemetry are mocked)
# ---------------------------------------------------------------------------


class TestAppExitedEmittedBeforeShutdown:
    """app_exited must be enqueued before shutdown_telemetry() force-flushes."""

    def test_record_app_exited_called_before_shutdown_telemetry_on_success(
        self,
        runner: CliRunner,
    ) -> None:
        """record_app_exited is called before shutdown_telemetry on a clean exit."""
        call_order: list[str] = []

        def _fake_record_app_exited(**_: object) -> None:
            call_order.append("record_app_exited")

        def _fake_shutdown_telemetry(**_: object) -> None:
            call_order.append("shutdown_telemetry")

        with (
            patch.object(_main_module, "record_app_exited", side_effect=_fake_record_app_exited),
            patch.object(_main_module, "shutdown_telemetry", side_effect=_fake_shutdown_telemetry),
            patch.object(_main_module, "record_app_started"),
            patch.object(_main_module, "maybe_print_first_run_notice"),
            patch.object(_main_module, "emit_command_invoked"),
        ):
            result = runner.invoke(cli, ["workspaces", "--help"], catch_exceptions=False)

        assert result.exit_code == 0
        assert "record_app_exited" in call_order, "record_app_exited was never called"
        assert "shutdown_telemetry" in call_order, "shutdown_telemetry was never called"
        idx_exited = call_order.index("record_app_exited")
        idx_shutdown = call_order.index("shutdown_telemetry")
        assert idx_exited < idx_shutdown, (
            f"shutdown_telemetry ({idx_shutdown}) ran before record_app_exited ({idx_exited}); "
            f"call order: {call_order}"
        )

    def test_record_app_exited_called_before_shutdown_telemetry_on_error(
        self,
        runner: CliRunner,
    ) -> None:
        """record_app_exited is called before shutdown_telemetry even when command errors."""
        call_order: list[str] = []

        def _fake_record_app_exited(**_: object) -> None:
            call_order.append("record_app_exited")

        def _fake_shutdown_telemetry(**_: object) -> None:
            call_order.append("shutdown_telemetry")

        with (
            patch.object(_main_module, "record_app_exited", side_effect=_fake_record_app_exited),
            patch.object(_main_module, "shutdown_telemetry", side_effect=_fake_shutdown_telemetry),
            patch.object(_main_module, "record_app_started"),
            patch.object(_main_module, "maybe_print_first_run_notice"),
            patch.object(_main_module, "emit_command_invoked"),
        ):
            result = runner.invoke(
                cli, ["workspaces", "nonexistent-subcommand"], catch_exceptions=True
            )

        assert result.exit_code != 0
        assert "record_app_exited" in call_order, "record_app_exited was never called"
        assert "shutdown_telemetry" in call_order, "shutdown_telemetry was never called"
        idx_exited = call_order.index("record_app_exited")
        idx_shutdown = call_order.index("shutdown_telemetry")
        assert idx_exited < idx_shutdown, (
            f"shutdown_telemetry ({idx_shutdown}) ran before record_app_exited ({idx_exited}); "
            f"call order: {call_order}"
        )

    def test_shutdown_telemetry_called_exactly_once(
        self,
        runner: CliRunner,
    ) -> None:
        """shutdown_telemetry is called exactly once per CLI invocation (no double-shutdown)."""
        mock_shutdown = MagicMock()

        with (
            patch.object(_main_module, "shutdown_telemetry", mock_shutdown),
            patch.object(_main_module, "record_app_started"),
            patch.object(_main_module, "record_app_exited"),
            patch.object(_main_module, "maybe_print_first_run_notice"),
            patch.object(_main_module, "emit_command_invoked"),
        ):
            runner.invoke(cli, ["workspaces", "--help"], catch_exceptions=False)

        assert mock_shutdown.call_count == 1, (
            f"shutdown_telemetry called {mock_shutdown.call_count} times (expected exactly 1)"
        )

    def test_shutdown_telemetry_called_even_if_record_app_exited_raises(
        self,
        runner: CliRunner,
    ) -> None:
        """shutdown_telemetry is called even when record_app_exited raises a BaseException.

        The try/finally guard in _on_close ensures the urllib3 pool is always
        released, preventing the GC-finaliser AttributeError on pool teardown.
        """
        call_order: list[str] = []

        def _fake_record_app_exited_raises(**_: object) -> None:
            call_order.append("record_app_exited")
            raise KeyboardInterrupt

        def _fake_shutdown(**_: object) -> None:
            call_order.append("shutdown_telemetry")

        with (
            patch.object(
                _main_module,
                "record_app_exited",
                side_effect=_fake_record_app_exited_raises,
            ),
            patch.object(_main_module, "shutdown_telemetry", side_effect=_fake_shutdown),
            patch.object(_main_module, "record_app_started"),
            patch.object(_main_module, "maybe_print_first_run_notice"),
            patch.object(_main_module, "emit_command_invoked"),
        ):
            # KeyboardInterrupt propagates through Click's runner;
            # catch_exceptions=True swallows it so the test does not abort.
            runner.invoke(cli, ["workspaces", "--help"], catch_exceptions=True)

        assert "record_app_exited" in call_order, "record_app_exited was never called"
        assert "shutdown_telemetry" in call_order, (
            "shutdown_telemetry was not called after record_app_exited raised"
        )
        idx_exited = call_order.index("record_app_exited")
        idx_shutdown = call_order.index("shutdown_telemetry")
        assert idx_exited < idx_shutdown, (
            f"shutdown_telemetry ({idx_shutdown}) did not follow record_app_exited "
            f"({idx_exited}); call order: {call_order}"
        )


# ---------------------------------------------------------------------------
# exit_status vocabulary tests
# ---------------------------------------------------------------------------


class TestExitStatusMapping:
    """_on_close must map exceptions to the correct app_exited exit_status values."""

    def test_clean_exit_produces_ok(self, runner: CliRunner) -> None:
        """A successful command produces exit_status='ok'."""
        captured: list[dict] = []

        def _capture(**kwargs: object) -> None:
            captured.append(dict(kwargs))

        with (
            patch.object(_main_module, "record_app_exited", side_effect=_capture),
            patch.object(_main_module, "shutdown_telemetry"),
            patch.object(_main_module, "record_app_started"),
            patch.object(_main_module, "maybe_print_first_run_notice"),
            patch.object(_main_module, "emit_command_invoked"),
        ):
            result = runner.invoke(cli, ["workspaces", "--help"], catch_exceptions=False)

        assert result.exit_code == 0
        assert captured, "record_app_exited was never called"
        assert captured[0]["exit_status"] == "ok", f"got {captured[0]['exit_status']!r}"

    def test_usage_error_produces_user_error(self, runner: CliRunner) -> None:
        """A UsageError (unknown subcommand) produces exit_status='user_error'."""
        captured: list[dict] = []

        def _capture(**kwargs: object) -> None:
            captured.append(dict(kwargs))

        with (
            patch.object(_main_module, "record_app_exited", side_effect=_capture),
            patch.object(_main_module, "shutdown_telemetry"),
            patch.object(_main_module, "record_app_started"),
            patch.object(_main_module, "maybe_print_first_run_notice"),
            patch.object(_main_module, "emit_command_invoked"),
        ):
            result = runner.invoke(
                cli, ["workspaces", "nonexistent-subcommand"], catch_exceptions=True
            )

        assert result.exit_code != 0
        assert captured, "record_app_exited was never called"
        assert captured[0]["exit_status"] == "user_error", f"got {captured[0]['exit_status']!r}"

    def test_unexpected_exception_produces_api_error(self, runner: CliRunner) -> None:
        """An unexpected/genuine exception produces exit_status='api_error'.

        Consistent with map_status() which returns 'api_error' for non-Click,
        non-ValueError exceptions.  Previously the else-branch incorrectly emitted
        'user_error', making command_invoked.status and app_exited.exit_status
        inconsistent for the same failure.

        The workspaces group's invoke is patched to raise a RuntimeError before any
        Click exception handling, so it propagates unchanged into _on_close's
        sys.exc_info() check and hits the else → 'api_error' branch.
        """
        captured: list[dict] = []

        def _capture(**kwargs: object) -> None:
            captured.append(dict(kwargs))

        ws_group = _workspaces_group()

        def _ws_raises(_ctx: object) -> None:
            raise RuntimeError("simulated api error from subcommand")

        with (
            patch.object(_main_module, "record_app_exited", side_effect=_capture),
            patch.object(_main_module, "shutdown_telemetry"),
            patch.object(_main_module, "record_app_started"),
            patch.object(_main_module, "maybe_print_first_run_notice"),
            patch.object(_main_module, "emit_command_invoked"),
            patch.object(ws_group, "invoke", _ws_raises),
        ):
            result = runner.invoke(cli, ["workspaces", "list"], catch_exceptions=True)

        assert result.exit_code != 0
        assert captured, "record_app_exited was never called"
        assert captured[0]["exit_status"] == "api_error", (
            f"Expected 'api_error' for unexpected exception, got {captured[0]['exit_status']!r}"
        )


# ---------------------------------------------------------------------------
# No-regression: app_started + command_invoked still emitted
# ---------------------------------------------------------------------------


class TestLifecycleEventsNotRegressed:
    """app_started and command_invoked must still be emitted after the fix."""

    def test_app_started_still_emitted(self, runner: CliRunner) -> None:
        """record_app_started is called on a normal CLI invocation."""
        mock_started = MagicMock()

        with (
            patch.object(_main_module, "record_app_started", mock_started),
            patch.object(_main_module, "record_app_exited"),
            patch.object(_main_module, "shutdown_telemetry"),
            patch.object(_main_module, "maybe_print_first_run_notice"),
            patch.object(_main_module, "emit_command_invoked"),
        ):
            runner.invoke(cli, ["workspaces", "--help"], catch_exceptions=False)

        mock_started.assert_called_once_with("cli")

    def test_emit_command_invoked_called_before_shutdown(self, runner: CliRunner) -> None:
        """emit_command_invoked is called before shutdown_telemetry (no regression)."""
        call_order: list[str] = []

        def _fake_emit_command_invoked(**_: object) -> None:
            call_order.append("emit_command_invoked")

        def _fake_shutdown(**_: object) -> None:
            call_order.append("shutdown_telemetry")

        with (
            patch.object(
                _main_module, "emit_command_invoked", side_effect=_fake_emit_command_invoked
            ),
            patch.object(_main_module, "shutdown_telemetry", side_effect=_fake_shutdown),
            patch.object(_main_module, "record_app_started"),
            patch.object(_main_module, "record_app_exited"),
            patch.object(_main_module, "maybe_print_first_run_notice"),
        ):
            result = runner.invoke(cli, ["workspaces", "--help"], catch_exceptions=False)

        assert result.exit_code == 0
        assert "emit_command_invoked" in call_order, "emit_command_invoked was never called"
        assert "shutdown_telemetry" in call_order, "shutdown_telemetry was never called"
        idx_cmd = call_order.index("emit_command_invoked")
        idx_shut = call_order.index("shutdown_telemetry")
        assert idx_cmd < idx_shut, (
            f"shutdown_telemetry ({idx_shut}) ran before emit_command_invoked ({idx_cmd}); "
            f"call order: {call_order}"
        )


# ---------------------------------------------------------------------------
# Delivery tests — prove app_exited actually reaches emit_event
# ---------------------------------------------------------------------------


class TestAppExitedDelivery:
    """app_exited must actually be passed to emit_event (not just ordered correctly).

    These tests patch ``telemetry_enabled`` to return ``True`` and spy on
    ``emit_event`` directly.  No real SDK initialisation or network call occurs
    because ``emit_event`` itself is replaced by the spy — ``_get_tracer()`` is
    never reached.  ``shutdown_telemetry`` is also mocked out to avoid a
    daemon-thread join against an uninitialised provider.

    Patching strategy
    -----------------
    We patch ``emit_event`` and ``telemetry_enabled`` via ``patch.dict`` on
    ``_telemetry_globals()`` rather than via the string ``"fabric_dw.telemetry.X"``.
    The reason: ``test_telemetry.py`` calls ``_reload_telemetry()`` which replaces
    ``sys.modules["fabric_dw.telemetry"]`` with a fresh module object.  After a
    reload, string-based patches land on the *new* module, but
    ``_main_module.record_app_exited`` (imported before the reload) still holds a
    reference to the *old* module's ``__globals__``.  ``_telemetry_globals()``
    returns that exact dict, so the spy is placed where ``record_app_exited``
    actually looks up ``emit_event`` at call time.
    """

    def test_app_exited_reaches_emit_event_on_success(
        self,
        runner: CliRunner,
    ) -> None:
        """emit_event is called with 'app_exited' and exit_status='ok' on clean exit."""
        emitted: list[tuple[str, dict]] = []

        def _spy(
            name: str,
            attributes: dict,
            *,
            omit_keys: set[str] | None = None,  # noqa: ARG001
        ) -> None:
            emitted.append((name, dict(attributes)))

        tel_globals = _telemetry_globals()
        with (
            patch.dict(tel_globals, {"emit_event": _spy, "telemetry_enabled": lambda: True}),
            patch.object(_main_module, "emit_command_invoked"),
            patch.object(_main_module, "maybe_print_first_run_notice"),
            patch.object(_main_module, "shutdown_telemetry"),
        ):
            result = runner.invoke(cli, ["workspaces", "--help"], catch_exceptions=False)

        assert result.exit_code == 0

        event_names = [name for name, _ in emitted]
        assert "app_exited" in event_names, (
            f"app_exited was not passed to emit_event; events seen: {event_names}"
        )
        app_exited_attrs = next(attrs for name, attrs in emitted if name == "app_exited")
        assert app_exited_attrs.get("exit_status") == "ok", (
            f"Expected exit_status='ok', got {app_exited_attrs.get('exit_status')!r}"
        )

    def test_app_exited_reaches_emit_event_on_unexpected_error(
        self,
        runner: CliRunner,
    ) -> None:
        """emit_event is called with 'app_exited' and exit_status='api_error' on error."""
        emitted: list[tuple[str, dict]] = []

        def _spy(
            name: str,
            attributes: dict,
            *,
            omit_keys: set[str] | None = None,  # noqa: ARG001
        ) -> None:
            emitted.append((name, dict(attributes)))

        ws_group = _workspaces_group()

        def _ws_raises(_ctx: object) -> None:
            raise RuntimeError("simulated api error")

        tel_globals = _telemetry_globals()
        with (
            patch.dict(tel_globals, {"emit_event": _spy, "telemetry_enabled": lambda: True}),
            patch.object(_main_module, "emit_command_invoked"),
            patch.object(_main_module, "maybe_print_first_run_notice"),
            patch.object(_main_module, "shutdown_telemetry"),
            patch.object(ws_group, "invoke", _ws_raises),
        ):
            runner.invoke(cli, ["workspaces", "list"], catch_exceptions=True)

        event_names = [name for name, _ in emitted]
        assert "app_exited" in event_names, (
            f"app_exited was not passed to emit_event; events seen: {event_names}"
        )
        app_exited_attrs = next(attrs for name, attrs in emitted if name == "app_exited")
        assert app_exited_attrs.get("exit_status") == "api_error", (
            f"Expected exit_status='api_error' for unexpected error, "
            f"got {app_exited_attrs.get('exit_status')!r}"
        )
