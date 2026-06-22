"""Shared resolution helpers for 3-layer config knobs (CLI > env > config > None).

Both the CLI (``cli/commands/_utils.py``) and the MCP server
(``mcp/_context.py``) use these helpers so they cannot drift apart.

This module intentionally imports only :mod:`fabric_dw.config` — it must not
import anything from ``cli`` or ``mcp`` to avoid import cycles.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping

_log = logging.getLogger(__name__)

__all__ = [
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
