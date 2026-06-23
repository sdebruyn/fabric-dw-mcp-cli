"""Tests for config_resolve — shared 3-layer knob resolution helpers."""

from __future__ import annotations

import pytest

from fabric_dw.config_resolve import resolve_auth_mode, resolve_int_knob

_VALID = frozenset({"default", "sp", "interactive"})

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
# resolve_auth_mode
# ---------------------------------------------------------------------------


class TestResolveAuthMode:
    """Unit tests for the 4-layer credential-mode resolution helper."""

    # --- precedence ----------------------------------------------------------

    def test_cli_wins_over_env_and_config(self) -> None:
        """Explicit --auth flag overrides env and config."""
        result = resolve_auth_mode(
            cli_value="sp",
            env={"FABRIC_AUTH": "interactive"},
            config_value="interactive",
            valid_modes=_VALID,
        )
        assert result == "sp"

    def test_env_wins_over_config(self) -> None:
        """FABRIC_AUTH env var overrides config when cli_value is None."""
        result = resolve_auth_mode(
            cli_value=None,
            env={"FABRIC_AUTH": "interactive"},
            config_value="sp",
            valid_modes=_VALID,
        )
        assert result == "interactive"

    def test_config_wins_over_builtin_default(self) -> None:
        """Config value is used when cli_value and env are absent."""
        result = resolve_auth_mode(
            cli_value=None,
            env={},
            config_value="sp",
            valid_modes=_VALID,
        )
        assert result == "sp"

    def test_builtin_default_when_all_absent(self) -> None:
        """Returns 'default' when no source supplies a value."""
        result = resolve_auth_mode(
            cli_value=None,
            env={},
            config_value=None,
            valid_modes=_VALID,
        )
        assert result == "default"

    # --- cli_value sentinel --------------------------------------------------

    def test_cli_none_does_not_win(self) -> None:
        """cli_value=None means flag was not set; falls through to env."""
        result = resolve_auth_mode(
            cli_value=None,
            env={"FABRIC_AUTH": "sp"},
            config_value=None,
            valid_modes=_VALID,
        )
        assert result == "sp"

    # --- case-insensitivity --------------------------------------------------

    @pytest.mark.parametrize(
        "raw", ["DEFAULT", "Default", "SP", "Sp", "INTERACTIVE", "Interactive"]
    )
    def test_cli_value_case_insensitive(self, raw: str) -> None:
        """--auth is case-insensitive; result is always lowercase."""
        result = resolve_auth_mode(
            cli_value=raw,
            env={},
            config_value=None,
            valid_modes=_VALID,
        )
        assert result == raw.lower()

    @pytest.mark.parametrize("raw", ["DEFAULT", "SP", "INTERACTIVE"])
    def test_env_value_case_insensitive(self, raw: str) -> None:
        """FABRIC_AUTH is case-insensitive; result is always lowercase."""
        result = resolve_auth_mode(
            cli_value=None,
            env={"FABRIC_AUTH": raw},
            config_value=None,
            valid_modes=_VALID,
        )
        assert result == raw.lower()

    # --- empty / whitespace env fall-through ---------------------------------

    @pytest.mark.parametrize("val", ["", "  ", "\t"])
    def test_empty_env_falls_through_to_config(self, val: str) -> None:
        """Empty/whitespace FABRIC_AUTH falls through to config, not an error."""
        result = resolve_auth_mode(
            cli_value=None,
            env={"FABRIC_AUTH": val},
            config_value="sp",
            valid_modes=_VALID,
        )
        assert result == "sp"

    @pytest.mark.parametrize("val", ["", "  "])
    def test_empty_env_falls_through_to_default(self, val: str) -> None:
        """Empty/whitespace FABRIC_AUTH with no config yields 'default'."""
        result = resolve_auth_mode(
            cli_value=None,
            env={"FABRIC_AUTH": val},
            config_value=None,
            valid_modes=_VALID,
        )
        assert result == "default"

    # --- invalid value error handling ----------------------------------------

    def test_invalid_env_raises_value_error(self) -> None:
        """Unrecognised non-empty FABRIC_AUTH raises ValueError."""
        with pytest.raises(ValueError, match="invalid FABRIC_AUTH"):
            resolve_auth_mode(
                cli_value=None,
                env={"FABRIC_AUTH": "not-a-mode"},
                config_value=None,
                valid_modes=_VALID,
            )

    def test_invalid_cli_value_raises_value_error(self) -> None:
        """Unrecognised --auth value raises ValueError."""
        with pytest.raises(ValueError, match="invalid --auth"):
            resolve_auth_mode(
                cli_value="not-a-mode",
                env={},
                config_value=None,
                valid_modes=_VALID,
            )

    def test_invalid_config_value_falls_back_to_default(self) -> None:
        """Unrecognised config value falls through to built-in default with a warning."""
        result = resolve_auth_mode(
            cli_value=None,
            env={},
            config_value="bogus",
            valid_modes=_VALID,
        )
        assert result == "default"

    # --- os.environ fallback -------------------------------------------------

    def test_defaults_env_to_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When env=None, FABRIC_AUTH is read from os.environ."""
        monkeypatch.setenv("FABRIC_AUTH", "interactive")
        result = resolve_auth_mode(
            cli_value=None,
            env=None,
            config_value=None,
            valid_modes=_VALID,
        )
        assert result == "interactive"
