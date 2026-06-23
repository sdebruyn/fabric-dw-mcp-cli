"""Shared resolution helpers for config knobs (CLI flag > env > config > built-in default).

Both the CLI (``cli/_main.py``, ``cli/commands/_utils.py``) and the MCP server
(``mcp/_context.py``) use these helpers so they cannot drift apart.

This module intentionally imports only :mod:`fabric_dw.config` — it must not
import anything from ``cli`` or ``mcp`` to avoid import cycles.  In particular,
:func:`resolve_auth_mode` deliberately does NOT import :mod:`fabric_dw.auth`
(which pulls in the azure-identity / msal chain) — it works on raw string
values and lets the caller convert to :class:`~fabric_dw.auth.CredentialMode`.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping

_log = logging.getLogger(__name__)

__all__ = [
    "resolve_auth_mode",
    "resolve_float_knob",
    "resolve_int_knob",
]


def resolve_int_knob(  # noqa: PLR0913
    *,
    cli_value: int | None,
    env_key: str,
    env: Mapping[str, str] | None = None,
    config_value: int | None,
    min_val: int,
    knob_name: str,
) -> int | None:
    """Resolve an integer knob with precedence CLI > env > config > None.

    Args:
        cli_value: Value supplied by the CLI option (already validated).
        env_key: The environment variable name to read.
        env: Mapping to read env vars from; defaults to ``os.environ``.
        config_value: Value from the config file (pre-parsed, may be ``None``).
        min_val: Minimum accepted value; below-min env values are skipped.
        knob_name: Human-readable name used in log warnings.

    Returns:
        The resolved integer, or ``None`` when no source supplies a valid value
        (caller should use the built-in default).
    """
    env_map = env if env is not None else os.environ

    # 1. CLI option (already validated)
    if cli_value is not None:
        return cli_value

    # 2. Environment variable — accept float-formatted ints (e.g. "20.0")
    raw = env_map.get(env_key)
    if raw is not None:
        try:
            v = int(float(raw))
            if v >= min_val:
                return v
            _log.warning("%s=%r is less than %d; ignoring", env_key, raw, min_val)
        except (ValueError, OverflowError):
            _log.warning("%s=%r is not a valid integer; ignoring", env_key, raw)

    # 3. Config file
    if config_value is not None:
        if config_value >= min_val:
            return config_value
        _log.warning("config %s=%r is less than %d; ignoring", knob_name, config_value, min_val)

    return None


def resolve_float_knob(  # noqa: PLR0913
    *,
    cli_value: float | None,
    env_key: str,
    env: Mapping[str, str] | None = None,
    config_value: float | None,
    min_val: float,
    knob_name: str,
) -> float | None:
    """Resolve a float knob with precedence CLI > env > config > None.

    Non-finite values (``inf``, ``nan``) from the env var are rejected with a
    warning; values from the config file are assumed to have been validated at
    write time.

    Args:
        cli_value: Value supplied by the CLI option (already validated).
        env_key: The environment variable name to read.
        env: Mapping to read env vars from; defaults to ``os.environ``.
        config_value: Value from the config file (pre-parsed, may be ``None``).
        min_val: Minimum accepted value; below-min env values are skipped.
        knob_name: Human-readable name used in log warnings.

    Returns:
        The resolved float, or ``None`` when no source supplies a valid value
        (caller should use the built-in default).
    """
    env_map = env if env is not None else os.environ

    # 1. CLI option (already validated); guard against non-finite values
    #    that slipped past click.FloatRange.
    if cli_value is not None:
        if math.isfinite(cli_value):
            return cli_value
        _log.warning("--%s %r is not finite; ignoring", knob_name, cli_value)

    # 2. Environment variable
    raw = env_map.get(env_key)
    if raw is not None:
        try:
            v = float(raw)
            if not math.isfinite(v):
                _log.warning("%s=%r is not finite; ignoring", env_key, raw)
            elif v >= min_val:
                return v
            else:
                _log.warning("%s=%r is less than %s; ignoring", env_key, raw, min_val)
        except ValueError:
            _log.warning("%s=%r is not a valid float; ignoring", env_key, raw)

    # 3. Config file (non-finite values were already rejected at load/set time)
    if config_value is not None:
        if config_value >= min_val:
            return config_value
        _log.warning("config %s=%r is less than %s; ignoring", knob_name, config_value, min_val)

    return None


def resolve_auth_mode(
    *,
    cli_value: str | None,
    env: Mapping[str, str] | None = None,
    config_value: str | None,
    valid_modes: frozenset[str],
) -> str:
    """Resolve the credential mode with precedence CLI > env > config > built-in default.

    This is the single source of truth for credential-mode resolution shared by
    the CLI and MCP server so both surfaces cannot drift apart.

    The function operates on raw string values only and does NOT import
    ``fabric_dw.auth`` — it leaves the final conversion to
    :class:`~fabric_dw.auth.CredentialMode` to the caller.  This keeps
    ``config_resolve`` a lightweight, azure-identity-free module.

    Resolution rules
    ----------------
    1. *cli_value* — wins when explicitly supplied (not ``None``).  Callers must
       pass ``None`` when the flag was not set by the user (not the Click
       default), so the flag only wins on an explicit command-line pass.
    2. ``FABRIC_AUTH`` environment variable — wins when non-empty and
       non-whitespace.  An empty/whitespace value is treated as absent (falls
       through).  An unrecognised non-empty value raises :class:`ValueError`.
    3. *config_value* — the ``[defaults] auth_mode`` entry from ``config.toml``.
       ``None`` means absent.
    4. Built-in default — ``"default"`` (``DefaultAzureCredential``).

    Args:
        cli_value: Value supplied by the ``--auth`` CLI flag, or ``None`` when
            the flag was not explicitly passed by the user.
        env: Mapping to read ``FABRIC_AUTH`` from; defaults to ``os.environ``.
        config_value: Value of ``[defaults] auth_mode`` from the loaded config
            (pre-normalised to lowercase, ``None`` when absent).
        valid_modes: Frozenset of recognised lowercase mode strings.  Callers
            should pass :data:`~fabric_dw.config.VALID_AUTH_MODES`.

    Returns:
        The resolved lowercase credential mode string (always a member of
        *valid_modes*).

    Raises:
        ValueError: When *cli_value* or a non-empty ``FABRIC_AUTH`` env value is
            not a recognised mode.  The error message names the offending value
            and lists the valid alternatives.
    """
    env_map = env if env is not None else os.environ

    # 1. Explicit CLI flag.
    if cli_value is not None:
        normalised = cli_value.strip().lower()
        if normalised not in valid_modes:
            raise ValueError(
                f"invalid --auth value {cli_value!r}; expected one of {sorted(valid_modes)}"
            )
        return normalised

    # 2. Environment variable (case-insensitive; empty/whitespace falls through).
    raw_env = env_map.get("FABRIC_AUTH", "").strip().lower()
    if raw_env:
        if raw_env not in valid_modes:
            raise ValueError(
                f"invalid FABRIC_AUTH value {raw_env!r}; expected one of {sorted(valid_modes)}"
            )
        return raw_env

    # 3. Config file value (already normalised to lowercase by the config layer;
    #    belt-and-suspenders: skip unrecognised values with a warning rather than
    #    hard-failing — the config layer already rejected bad values at write time,
    #    so this branch can only be reached via direct file edits or regressions).
    if config_value is not None:
        normalised_cfg = config_value.strip().lower()
        if normalised_cfg in valid_modes:
            return normalised_cfg
        _log.warning(
            "[defaults] auth_mode %r from config is not a recognised credential mode; "
            "falling back to built-in default.",
            config_value,
        )

    # 4. Built-in default.
    return "default"
