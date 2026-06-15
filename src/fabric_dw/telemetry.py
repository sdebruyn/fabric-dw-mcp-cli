"""Opt-out anonymous usage telemetry for fabric-dw.

Telemetry is **on by default** but can be disabled via:

- ``FABRIC_TELEMETRY=0`` (or ``false``, ``no``, ``off``)
- ``FABRIC_DISABLE_TELEMETRY=1`` (or any truthy value)
- ``DO_NOT_TRACK=1`` (consoledonottrack.com standard)
- Any CI environment (``CI``, ``GITHUB_ACTIONS``, ``JENKINS_URL``, etc.)
- Config file: ``[telemetry] disabled = true`` in
  ``$XDG_CONFIG_HOME/fabric-dw/config.toml``

See https://fdw.debruyn.dev/telemetry/ for full documentation.

Architecture notes
------------------
- The Azure Monitor OpenTelemetry SDK is imported **lazily** and only when
  telemetry is enabled.  Disabled runs pay zero import cost.
- All public functions are fire-and-forget: they catch every exception and
  never propagate errors to the caller.
- No network calls are made when telemetry is disabled.
- ``tenant_id`` is included in the envelope only when telemetry is enabled
  (it comes from env vars; token-claim extraction is deferred to #366).
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import tomllib
import uuid
from pathlib import Path

__all__ = [
    "emit_event",
    "maybe_print_first_run_notice",
    "record_app_exited",
    "record_app_started",
    "record_mcp_server_started",
    "set_tenant_id",
    "telemetry_enabled",
]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection string (write-only ingestion key — safe to embed per Microsoft docs)
# ---------------------------------------------------------------------------

_DEFAULT_CONNECTION_STRING = (
    "InstrumentationKey=bd1668b7-aa94-49cc-8998-9a09a6b232c6;"
    "IngestionEndpoint=https://westeurope-5.in.applicationinsights.azure.com/;"
    "LiveEndpoint=https://westeurope.livediagnostics.monitor.azure.com/;"
    "ApplicationId=36d5e7bd-b436-4445-a693-8c93c25cc2fb"
)

# ---------------------------------------------------------------------------
# Per-process session ID (generated once at module load)
# ---------------------------------------------------------------------------

_SESSION_ID: str = str(uuid.uuid4())

# ---------------------------------------------------------------------------
# CI environment variable markers
# ---------------------------------------------------------------------------

_CI_VARS = frozenset(
    {
        "CI",
        "GITHUB_ACTIONS",
        "JENKINS_URL",
        "TRAVIS",
        "CIRCLECI",
        "GITLAB_CI",
        "TF_BUILD",
    }
)


def _is_ci() -> bool:
    """Return True when any known CI marker is present in the environment."""
    return any(os.environ.get(var) for var in _CI_VARS)


# ---------------------------------------------------------------------------
# Opt-out helpers
# ---------------------------------------------------------------------------

_FALSY_VALUES = frozenset({"0", "false", "no", "off"})


def _is_truthy(value: str) -> bool:
    """Return True when *value* looks like an affirmative string."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _config_dir() -> Path:
    """Return the fabric-dw configuration directory."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "fabric-dw"


def _is_disabled_by_config() -> bool:
    """Return True when the config file contains ``[telemetry] disabled = true``."""
    config_file = _config_dir() / "config.toml"
    if not config_file.exists():
        return False
    with contextlib.suppress(Exception):
        raw = config_file.read_text(encoding="utf-8")
        data = tomllib.loads(raw)
        telemetry_section = data.get("telemetry", {})
        if isinstance(telemetry_section, dict):
            return bool(telemetry_section.get("disabled", False))
    return False


def telemetry_enabled() -> bool:
    """Return True when anonymous telemetry is active for this process.

    Telemetry is ON by default.  Any of the following disables it:

    - ``FABRIC_TELEMETRY`` in ``{"0", "false", "no", "off"}``
    - ``FABRIC_DISABLE_TELEMETRY`` is truthy
    - ``DO_NOT_TRACK`` is truthy
    - A CI environment is detected (``CI``, ``GITHUB_ACTIONS``, etc.)
    - The config file has ``[telemetry] disabled = true``
    """
    # Explicit opt-out via FABRIC_TELEMETRY=<falsy>
    fabric_tel = os.environ.get("FABRIC_TELEMETRY", "").strip().lower()
    if fabric_tel in _FALSY_VALUES:
        return False

    # FABRIC_DISABLE_TELEMETRY truthy → disabled
    if _is_truthy(os.environ.get("FABRIC_DISABLE_TELEMETRY", "")):
        return False

    # DO_NOT_TRACK standard (consoledonottrack.com)
    if _is_truthy(os.environ.get("DO_NOT_TRACK", "")):
        return False

    # CI detection
    if _is_ci():
        return False

    # Config-file opt-out
    return not _is_disabled_by_config()


# ---------------------------------------------------------------------------
# Install-ID persistence
# ---------------------------------------------------------------------------

_INSTALL_ID_FILE = "install_id"
_install_id_cache: str | None = None


def _get_install_id() -> str:
    """Return the anonymous install UUID, generating and persisting it on first call."""
    global _install_id_cache  # noqa: PLW0603
    if _install_id_cache is not None:
        return _install_id_cache

    config_dir = _config_dir()
    id_file = config_dir / _INSTALL_ID_FILE

    with contextlib.suppress(Exception):
        if id_file.exists():
            existing = id_file.read_text(encoding="utf-8").strip()
            if existing:
                _install_id_cache = existing
                return _install_id_cache

    # Generate a new install ID
    new_id = str(uuid.uuid4())
    with contextlib.suppress(Exception):
        config_dir.mkdir(parents=True, exist_ok=True)
        id_file.write_text(new_id, encoding="utf-8")

    _install_id_cache = new_id
    return _install_id_cache


# ---------------------------------------------------------------------------
# Envelope fields
# ---------------------------------------------------------------------------


def _detect_install_method() -> str:
    """Best-effort detection of how this package was installed."""
    # Check for uv: uv sets UV_VIRTUAL_ENV or VIRTUAL_ENV path often contains '.venv'
    # and the uv runner sets UV in the environment
    if os.environ.get("UV") or os.environ.get("UV_VIRTUAL_ENV"):
        return "uv"

    # Check for pipx: PIPX_HOME or the executable path contains 'pipx'
    if os.environ.get("PIPX_HOME"):
        return "pipx"
    executable = sys.executable or ""
    if "pipx" in executable:
        return "pipx"
    if ".venv" in executable or "venv" in executable:
        return "uv"

    # Check if running from source (editable install or no dist-info)
    with contextlib.suppress(Exception):
        import importlib.metadata  # noqa: PLC0415

        importlib.metadata.version("fabric-dw")
        return "pip"

    return "unknown"


def _detect_auth_mode() -> str:
    """Return a categorical auth mode string based on environment signals.

    Returns one of: ``service_principal``, ``github_oidc``, ``azure_cli``,
    ``interactive``.
    """
    # GitHub Actions OIDC
    if os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL") and os.environ.get(
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN"
    ):
        return "github_oidc"

    # Service principal
    if os.environ.get("AZURE_CLIENT_SECRET"):
        return "service_principal"

    # Azure CLI hint
    if os.environ.get("AZURE_CONFIG_DIR"):
        return "azure_cli"

    # Default / interactive fallback
    return "interactive"


def _build_envelope(surface: str) -> dict[str, object]:
    """Build the shared telemetry envelope attached to every event."""
    import platform as _platform  # noqa: PLC0415

    try:
        from fabric_dw._version import __version__ as _version  # noqa: PLC0415
    except Exception:
        _version = "unknown"

    python_info = sys.version_info
    python_version = f"{python_info.major}.{python_info.minor}"

    tenant_id = (
        os.environ.get("AZURE_TENANT_ID") or os.environ.get("FABRIC_INTERACTIVE_TENANT_ID") or None
    )

    return {
        "anonymous_install_id": _get_install_id(),
        "session_id": _SESSION_ID,
        "app_version": _version,
        "python_version": python_version,
        "os": _platform.system().lower(),
        "arch": _platform.machine().lower(),
        "install_method": _detect_install_method(),
        "surface": surface,
        "is_ci": _is_ci(),
        "auth_mode": _detect_auth_mode(),
        "tenant_id": tenant_id,
    }


# ---------------------------------------------------------------------------
# SDK initialisation (lazy, fail-safe)
# ---------------------------------------------------------------------------

_tracer: object | None = None
_sdk_initialised: bool = False
_current_surface: str = "cli"


def _get_connection_string() -> str:
    """Return the active App Insights connection string."""
    return os.environ.get("FABRIC_TELEMETRY_CONNECTION_STRING", _DEFAULT_CONNECTION_STRING)


def _get_tracer() -> object | None:
    """Lazily initialise the Azure Monitor OpenTelemetry tracer.

    Returns the tracer on success, or None if initialisation fails.
    Raises nothing.
    """
    global _tracer, _sdk_initialised  # noqa: PLW0603

    if _sdk_initialised:
        return _tracer

    _sdk_initialised = True

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor  # noqa: PLC0415
        from opentelemetry import trace  # noqa: PLC0415

        configure_azure_monitor(
            connection_string=_get_connection_string(),
            logger_name="fabric_dw.telemetry",
            # Disable automatic instrumentation we don't need
            disable_logging=True,
            disable_metrics=False,
        )
        _tracer = trace.get_tracer("fabric_dw.telemetry")
    except Exception:
        _log.debug("Failed to initialise Azure Monitor OpenTelemetry SDK", exc_info=True)
        _tracer = None

    return _tracer


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def emit_event(name: str, attributes: dict[str, object]) -> None:
    """Emit a telemetry event as an OpenTelemetry span.

    Fire-and-forget: never raises, never blocks the caller noticeably.
    When telemetry is disabled, this is a guaranteed no-op.
    """
    if not telemetry_enabled():
        return

    try:
        tracer = _get_tracer()
        if tracer is None or not hasattr(tracer, "start_as_current_span"):
            return

        envelope = _build_envelope(_current_surface)
        merged = {**envelope, **attributes}

        # Emit as a zero-duration span (event pattern).
        # getattr is intentional: `tracer` is typed as `object` to avoid importing
        # the OpenTelemetry tracer type (lazy SDK import), so attribute access would
        # fail static analysis; we guard with hasattr above and suppress B009 here.
        start_span = getattr(tracer, "start_as_current_span")  # noqa: B009
        with start_span(name, attributes=merged):
            pass
    except Exception:
        _log.debug("Failed to emit telemetry event %r", name, exc_info=True)


def record_app_started(surface: str) -> None:
    """Emit an ``app_started`` lifecycle event.

    Args:
        surface: Either ``"cli"`` or ``"mcp"``.
    """
    global _current_surface  # noqa: PLW0603
    _current_surface = surface
    emit_event("app_started", {"surface": surface})


def record_app_exited(
    *,
    duration_ms: float,
    exit_status: str,
    error_category: str | None,
) -> None:
    """Emit an ``app_exited`` lifecycle event.

    Args:
        duration_ms: Total process wall-clock duration in milliseconds.
        exit_status: One of ``"ok"``, ``"user_error"``, ``"api_error"``.
        error_category: Optional error category string (e.g. ``"AuthError"``).
    """
    attrs: dict[str, object] = {
        "duration_ms": duration_ms,
        "exit_status": exit_status,
    }
    if error_category is not None:
        attrs["error_category"] = error_category
    emit_event("app_exited", attrs)


def record_mcp_server_started() -> None:
    """Emit an ``mcp_server_started`` lifecycle event."""
    emit_event("mcp_server_started", {})


def maybe_print_first_run_notice() -> None:
    """Print a one-line telemetry notice to stderr on first invocation.

    The notice is suppressed when:
    - Telemetry is disabled.
    - Running in a CI environment.
    - The marker file already exists (notice was already shown).
    """
    if not telemetry_enabled():
        return
    if _is_ci():
        return

    marker_file = _config_dir() / ".telemetry_notice_shown"

    with contextlib.suppress(Exception):
        if marker_file.exists():
            return

    with contextlib.suppress(Exception):
        _config_dir().mkdir(parents=True, exist_ok=True)
        marker_file.write_text("1", encoding="utf-8")

    print(  # noqa: T201
        "fabric-dw collects anonymous usage telemetry to improve the tool. "
        "To opt out: set FABRIC_DISABLE_TELEMETRY=1. "
        "See https://fdw.debruyn.dev/telemetry/ for details.",
        file=sys.stderr,
    )


def set_tenant_id(tenant_id: str) -> None:
    """Stub for #366: set tenant ID from token claims.

    In this foundation PR the tenant ID is read from env vars only.
    Follow-up #366 will decode the ``tid`` claim from the access token
    and call this function to update the global envelope at runtime.
    """
