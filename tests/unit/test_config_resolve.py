"""Tests for config_resolve — shared 3-layer knob resolution helpers."""

from __future__ import annotations

import math

import pytest

from fabric_dw.config_resolve import resolve_float_knob, resolve_int_knob

# ---------------------------------------------------------------------------
# resolve_int_knob
# ---------------------------------------------------------------------------


class TestResolveIntKnob:
    def test_cli_value_wins_over_env_and_config(self) -> None:
        """CLI value takes precedence over env and config."""
        result = resolve_int_knob(
            cli_value=5,
            env_key="SOME_ENV",
            env={"SOME_ENV": "99"},
            config_value=3,
            min_val=1,
            knob_name="test_knob",
        )
        assert result == 5

    def test_env_wins_over_config(self) -> None:
        """Env var takes precedence over config when cli_value is None."""
        result = resolve_int_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={"SOME_ENV": "20"},
            config_value=3,
            min_val=1,
            knob_name="test_knob",
        )
        assert result == 20

    def test_config_used_when_no_cli_no_env(self) -> None:
        """Config value is used when neither CLI nor env are set."""
        result = resolve_int_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={},
            config_value=7,
            min_val=1,
            knob_name="test_knob",
        )
        assert result == 7

    def test_none_returned_when_no_source(self) -> None:
        """Returns None when no source supplies a value."""
        result = resolve_int_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={},
            config_value=None,
            min_val=1,
            knob_name="test_knob",
        )
        assert result is None

    def test_bad_env_value_skipped_falls_to_config(self) -> None:
        """Malformed env var is skipped; falls through to config."""
        result = resolve_int_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={"SOME_ENV": "not-a-number"},
            config_value=4,
            min_val=1,
            knob_name="test_knob",
        )
        assert result == 4

    def test_below_min_env_value_skipped_falls_to_config(self) -> None:
        """Env var below min_val is skipped; falls through to config."""
        result = resolve_int_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={"SOME_ENV": "0"},
            config_value=5,
            min_val=1,
            knob_name="test_knob",
        )
        assert result == 5

    def test_below_min_env_value_skipped_returns_none(self) -> None:
        """Env var below min_val is skipped; None when no config."""
        result = resolve_int_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={"SOME_ENV": "0"},
            config_value=None,
            min_val=1,
            knob_name="test_knob",
        )
        assert result is None

    def test_float_formatted_int_env_accepted(self) -> None:
        """Float-formatted int env var (e.g. '20.0') is accepted as integer."""
        result = resolve_int_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={"SOME_ENV": "20.0"},
            config_value=None,
            min_val=1,
            knob_name="test_knob",
        )
        assert result == 20

    def test_below_min_config_skipped(self) -> None:
        """Config value below min_val is skipped; returns None."""
        result = resolve_int_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={},
            config_value=0,
            min_val=1,
            knob_name="test_knob",
        )
        assert result is None

    def test_defaults_env_to_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When env=None, falls back to os.environ."""
        monkeypatch.setenv("SOME_ENV_KEY", "42")
        result = resolve_int_knob(
            cli_value=None,
            env_key="SOME_ENV_KEY",
            env=None,
            config_value=None,
            min_val=1,
            knob_name="test_knob",
        )
        assert result == 42


# ---------------------------------------------------------------------------
# resolve_float_knob
# ---------------------------------------------------------------------------


class TestResolveFloatKnob:
    def test_cli_value_wins_over_env_and_config(self) -> None:
        """CLI value takes precedence over env and config."""
        result = resolve_float_knob(
            cli_value=1.5,
            env_key="SOME_ENV",
            env={"SOME_ENV": "99.9"},
            config_value=0.5,
            min_val=0.1,
            knob_name="test_knob",
        )
        assert result == 1.5

    def test_env_wins_over_config(self) -> None:
        """Env var takes precedence over config when cli_value is None."""
        result = resolve_float_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={"SOME_ENV": "300.0"},
            config_value=0.5,
            min_val=0.1,
            knob_name="test_knob",
        )
        assert result == 300.0

    def test_config_used_when_no_cli_no_env(self) -> None:
        """Config value is used when neither CLI nor env are set."""
        result = resolve_float_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={},
            config_value=45.0,
            min_val=0.1,
            knob_name="test_knob",
        )
        assert result == 45.0

    def test_none_returned_when_no_source(self) -> None:
        """Returns None when no source supplies a value."""
        result = resolve_float_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={},
            config_value=None,
            min_val=0.1,
            knob_name="test_knob",
        )
        assert result is None

    def test_bad_env_value_skipped_falls_to_config(self) -> None:
        """Malformed env var is skipped; falls through to config."""
        result = resolve_float_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={"SOME_ENV": "not-a-float"},
            config_value=5.0,
            min_val=0.1,
            knob_name="test_knob",
        )
        assert result == 5.0

    def test_below_min_env_value_skipped_falls_to_config(self) -> None:
        """Env var below min_val is skipped; falls through to config."""
        result = resolve_float_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={"SOME_ENV": "0.0"},
            config_value=5.0,
            min_val=0.1,
            knob_name="test_knob",
        )
        assert result == 5.0

    @pytest.mark.parametrize("val", ["inf", "-inf", "nan"])
    def test_non_finite_env_value_skipped(self, val: str) -> None:
        """Non-finite env var values are skipped; falls through to config."""
        result = resolve_float_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={"SOME_ENV": val},
            config_value=3.0,
            min_val=0.1,
            knob_name="test_knob",
        )
        assert result == 3.0

    def test_non_finite_cli_value_skipped(self) -> None:
        """Non-finite CLI value is skipped; falls through to env/config."""
        result = resolve_float_knob(
            cli_value=math.inf,
            env_key="SOME_ENV",
            env={"SOME_ENV": "7.0"},
            config_value=None,
            min_val=0.1,
            knob_name="test_knob",
        )
        assert result == 7.0

    def test_below_min_config_skipped(self) -> None:
        """Config value below min_val is skipped; returns None."""
        result = resolve_float_knob(
            cli_value=None,
            env_key="SOME_ENV",
            env={},
            config_value=0.0,
            min_val=0.1,
            knob_name="test_knob",
        )
        assert result is None

    def test_defaults_env_to_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When env=None, falls back to os.environ."""
        monkeypatch.setenv("SOME_FLOAT_KEY", "1.23")
        result = resolve_float_knob(
            cli_value=None,
            env_key="SOME_FLOAT_KEY",
            env=None,
            config_value=None,
            min_val=0.1,
            knob_name="test_knob",
        )
        assert result == pytest.approx(1.23)
