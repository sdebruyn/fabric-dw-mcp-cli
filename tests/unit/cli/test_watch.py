"""Tests for fabric_dw.cli._watch -- the shared --watch loop (issue #1027).

Covers the loop mechanics used by both ``queries`` and ``sql exec``:
interval validation, the immediate-then-repeat cadence, and -- the subject of
PR #1028 review round 1 -- that a cycle fetches its data *before* clearing the
terminal, so a slow fetch never blanks the screen.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import click
import pytest

from fabric_dw.cli._watch import validate_watch, watch_loop
from tests.unit.cli.conftest import _StopWatchError


class TestValidateWatch:
    """validate_watch -- reject --watch + --json before any network client opens."""

    def test_allows_watch_without_json(self) -> None:
        validate_watch(json_output=False, watch=5)

    def test_allows_json_without_watch(self) -> None:
        validate_watch(json_output=True, watch=None)

    def test_allows_neither(self) -> None:
        validate_watch(json_output=False, watch=None)

    def test_rejects_watch_with_json(self) -> None:
        with pytest.raises(click.UsageError, match="--watch cannot be used with --json"):
            validate_watch(json_output=True, watch=5)


class TestWatchLoop:
    """watch_loop -- the shared loop behind ``queries`` and ``sql exec``."""

    @pytest.mark.anyio
    async def test_runs_once_without_clearing_when_interval_is_none(self) -> None:
        fetch = AsyncMock(return_value="data")
        render = Mock()
        with (
            patch("fabric_dw.cli._watch.click.clear") as clear,
            patch("fabric_dw.cli._watch.asyncio.sleep") as sleep,
        ):
            await watch_loop(interval=None, command="fdw sql exec", fetch=fetch, render=render)
        fetch.assert_awaited_once()
        render.assert_called_once_with("data")
        clear.assert_not_called()
        sleep.assert_not_called()

    @pytest.mark.anyio
    async def test_fetches_before_clearing_then_renders_and_repeats(self) -> None:
        """Regression: a cycle must fetch before clearing the terminal.

        Clearing before the fetch completes would blank the screen for the
        duration of a slow fetch and would sample the header timestamp before
        the data was actually read. Verified here via explicit call-order
        assertions, not just call counts (which cannot distinguish the two
        orderings).
        """
        manager = MagicMock()
        fetch = AsyncMock(side_effect=["first", "second"])
        render = Mock()
        manager.attach_mock(fetch, "fetch")
        manager.attach_mock(render, "render")
        sleep = AsyncMock(side_effect=[None, _StopWatchError()])
        with (
            patch("fabric_dw.cli._watch.click.clear") as clear,
            patch("fabric_dw.cli._watch.click.echo") as echo,
            patch("fabric_dw.cli._watch.asyncio.sleep", new=sleep),
        ):
            manager.attach_mock(clear, "clear")
            with pytest.raises(_StopWatchError):
                await watch_loop(interval=5, command="fdw sql exec", fetch=fetch, render=render)

        assert fetch.await_count == 2
        assert render.call_args_list == [(("first",),), (("second",),)]
        assert clear.call_count == 2
        assert sleep.await_args_list == [((5,),), ((5,),)]
        assert "Every 5s: fdw sql exec" in echo.call_args_list[0].args[0]

        relevant = {"fetch", "clear", "render"}
        call_order = [name for name, _args, _kwargs in manager.mock_calls if name in relevant]
        assert call_order == ["fetch", "clear", "render", "fetch", "clear", "render"]

    @pytest.mark.anyio
    async def test_fetch_error_propagates_uncaught(self) -> None:
        async def _boom() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await watch_loop(interval=None, command="fdw sql exec", fetch=_boom, render=Mock())
