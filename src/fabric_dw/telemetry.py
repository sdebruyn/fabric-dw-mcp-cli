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
- Auto-HTTP instrumentation is explicitly disabled to prevent MSAL OAuth
  request URLs (containing tenant IDs) from leaking as span attributes.
- ``shutdown_on_exit`` is disabled; a bounded ``provider.shutdown()`` (≤2 s)
  is performed at app exit in a daemon thread so the CLI never hangs.
  ``provider.shutdown()`` flushes all pending spans before tearing down
  processors, so a separate ``force_flush`` step is not required.
- ``enable_performance_counters=False`` suppresses the PerformanceCounters
  subsystem, which divides by zero on short-lived processes (#399).
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import threading
import tomllib
import uuid
from pathlib import Path

__all__ = [
    "cache_tenant_id_from_token",
    "decode_tid_from_token",
    "emit_event",
    "flush_telemetry",
    "maybe_print_first_run_notice",
    "record_app_exited",
    "record_app_started",
    "record_mcp_server_started",
    "set_tenant_id",
    "shutdown_telemetry",
    "telemetry_enabled",
]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection string (write-only ingestion key — safe to embed per Microsoft docs)
# ---------------------------------------------------------------------------

_DEFAULT_CONNECTION_STRING = (
    "InstrumentationKey=bd1668b7-aa94-49cc-8998-9a09a6b232c6;"  # gitleaks:allow
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
    """Best-effort detection of how this package was installed.

    Detection priority:
    1. ``UV`` / ``UV_VIRTUAL_ENV`` env vars → ``"uv"`` (set by uv runner).
    2. ``PIPX_HOME`` or ``"pipx"`` in ``sys.executable`` → ``"pipx"``.
    3. Editable / source checkout (no dist-info or ``.egg-link``) → ``"source"``.
    4. ``importlib.metadata`` resolves the package version → ``"pip"``.
    5. Fallback → ``"unknown"``.

    Note: a plain ``.venv`` in ``sys.executable`` is NOT used to infer ``"uv"``
    because pip can also install into a ``.venv``; that would be a false positive.
    """
    # uv explicitly sets UV or UV_VIRTUAL_ENV in the runner environment.
    if os.environ.get("UV") or os.environ.get("UV_VIRTUAL_ENV"):
        return "uv"

    # pipx: either the dedicated env var or the executable lives inside a pipx dir.
    if os.environ.get("PIPX_HOME"):
        return "pipx"
    executable = sys.executable or ""
    if "pipx" in executable:
        return "pipx"

    # Source / editable checkout: importlib.metadata won't find the package
    # (no dist-info installed), or it will resolve with a direct_url that has
    # "editable": true.
    with contextlib.suppress(Exception):
        import importlib.metadata  # noqa: PLC0415

        dist = importlib.metadata.distribution("fabric-dw")
        # Check for an editable install via direct_url.json
        direct_url = dist.read_text("direct_url.json")
        if direct_url and '"editable": true' in direct_url:
            return "source"
        return "pip"

    # importlib.metadata raised PackageNotFoundError → running from source tree.
    return "source"


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


# ---------------------------------------------------------------------------
# Tenant ID override (set_tenant_id / #366 hook)
# ---------------------------------------------------------------------------

_tenant_id_override: str | None = None


def _build_envelope(surface: str) -> dict[str, object]:
    """Build the shared telemetry envelope attached to every event."""
    import platform as _platform  # noqa: PLC0415

    try:
        from fabric_dw._version import __version__ as _version  # noqa: PLC0415
    except Exception:
        _version = "unknown"

    python_info = sys.version_info
    python_version = f"{python_info.major}.{python_info.minor}"

    # Prefer the runtime-set override (populated by #366 token-claim hook),
    # then fall back to environment variables.
    tenant_id = _tenant_id_override or (
        os.environ.get("AZURE_TENANT_ID") or os.environ.get("FABRIC_INTERACTIVE_TENANT_ID") or None
    )

    envelope: dict[str, object] = {
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
    }
    # Only include tenant_id when a value is known — OTel attributes do not
    # accept None, and omitting the key is cleaner than an empty string.
    if tenant_id is not None:
        envelope["tenant_id"] = tenant_id
    return envelope


# ---------------------------------------------------------------------------
# SDK initialisation (lazy, fail-safe)
# ---------------------------------------------------------------------------

_tracer: object | None = None
_sdk_initialised: bool = False
_current_surface: str = "cli"

# Instrumentation options passed to configure_azure_monitor.
# ALL auto-HTTP / Azure SDK instrumentors are DISABLED so that MSAL's OAuth
# token-request URLs (which contain tenant IDs) are never captured as span
# attributes.  We only emit our own explicit events — no auto-instrumentation.
_INSTRUMENTATION_OPTIONS: dict[str, dict[str, bool]] = {
    "azure_sdk": {"enabled": False},
    "django": {"enabled": False},
    "fastapi": {"enabled": False},
    "flask": {"enabled": False},
    "psycopg2": {"enabled": False},
    "requests": {"enabled": False},
    "urllib": {"enabled": False},
    "urllib3": {"enabled": False},
}


def _get_connection_string() -> str:
    """Return the active App Insights connection string."""
    return os.environ.get("FABRIC_TELEMETRY_CONNECTION_STRING", _DEFAULT_CONNECTION_STRING)


def _harden_azure_sdk_logging() -> None:
    """Raise the level of noisy Azure SDK loggers to CRITICAL and detach them from root.

    When the App Insights / Live Metrics endpoint is unreachable (offline user,
    firewall, or CI with a bogus endpoint) the Azure SDK writes full Python
    tracebacks and retry warnings to stderr via its own loggers.  This is
    independent of ``disable_logging=True`` passed to configure_azure_monitor,
    which only controls the OTel log exporter — not the SDK's own logger tree.

    The two noise sources suppressed here (#411):

    1. ``azure.monitor.opentelemetry.exporter``
       Covers ``export/_base.py`` ("Retrying due to server request error") and
       ``_quickpulse/_exporter.py`` (full traceback from ``_ping`` / ``is_subscribed``
       when the LiveEndpoint is unreachable, even with ``enable_live_metrics=False``
       as belt-and-suspenders). Also covers ``statsbeat/_manager.py``
       ("Exporter is missing a valid region.").

    2. ``azure.core.pipeline.policies``
       The azure-core retry policy logs "Retrying due to server request error" at
       WARNING level via ``azure.core.pipeline.policies._retry``.

    We set CRITICAL (instead of logging.NOTSET) and propagate=False so that no
    record at WARNING/ERROR/EXCEPTION from these trees ever reaches the root
    handler (typically StreamHandler → stderr).  A NullHandler is attached so
    that the "No handlers could be found" last-resort message is also suppressed.

    This function is idempotent: calling it multiple times is safe.
    """
    for name in (
        # A2: Azure Monitor exporter — covers retry warnings, statsbeat "missing a
        # valid region", and quickpulse _ping tracebacks via a single parent logger.
        "azure.monitor.opentelemetry.exporter",
        # A3: azure-core pipeline retry policy — "Retrying due to server request error"
        # at WARNING, emitted by azure.core.pipeline.policies._retry and siblings.
        "azure.core.pipeline.policies",
    ):
        lgr = logging.getLogger(name)
        lgr.setLevel(logging.CRITICAL)
        lgr.propagate = False
        if not any(isinstance(h, logging.NullHandler) for h in lgr.handlers):
            lgr.addHandler(logging.NullHandler())


def _get_tracer() -> object | None:
    """Lazily initialise the Azure Monitor OpenTelemetry tracer.

    Returns the tracer on success, or None if initialisation fails.
    Raises nothing.

    Privacy / hang safeguards
    -------------------------
    - ``instrumentation_options`` explicitly disables all auto-HTTP and Azure SDK
      instrumentors so MSAL OAuth URLs (containing tenant IDs) are never captured
      as span attributes (B1).
    - ``disable_metrics=True`` prevents generic metric exporters from starting.
    - ``enable_performance_counters=False`` disables the PerformanceCounters
      subsystem (CPU / memory poller) which is NOT covered by ``disable_metrics``
      in azure-monitor-opentelemetry 1.8+.  On short-lived processes its
      ``_get_processor_time`` callback divides by zero and logs a full traceback
      to stderr (A1 / #399).
    - ``shutdown_on_exit=False`` prevents the default 30-second atexit flush that
      can hang the CLI process (B2).  A bounded ``provider.shutdown()`` is
      performed by ``shutdown_telemetry()`` instead.
    - ``enable_live_metrics=False`` is set explicitly so QuickPulse never pings
      the LiveEndpoint, belt-and-suspenders against a future default change (A2).
    - ``_harden_azure_sdk_logging()`` is called before ``configure_azure_monitor``
      so the SDK's own logger tree is silenced before any network attempt (#411).
    """
    global _tracer, _sdk_initialised  # noqa: PLW0603

    if _sdk_initialised:
        return _tracer

    _sdk_initialised = True

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor  # noqa: PLC0415
        from opentelemetry import trace  # noqa: PLC0415

        # A2/A3: silence Azure SDK logger trees before any network attempt (#411).
        _harden_azure_sdk_logging()

        configure_azure_monitor(
            connection_string=_get_connection_string(),
            logger_name="fabric_dw.telemetry",
            disable_logging=True,
            disable_metrics=True,
            # A1: disable PerformanceCounters — not covered by disable_metrics in
            # azure-monitor-opentelemetry 1.8+; its _get_processor_time callback
            # divides by zero on short-lived processes and logs a traceback (#399).
            enable_performance_counters=False,
            # A2: belt-and-suspenders — QuickPulse must never ping the LiveEndpoint.
            # Suppresses _quickpulse/_exporter.py::_ping tracebacks on connection
            # refused even if the default changes in a future SDK version (#411).
            enable_live_metrics=False,
            # B2: disable unbounded (30 s) atexit flush — we do our own bounded flush.
            shutdown_on_exit=False,
            # B1: disable all auto-HTTP / Azure SDK instrumentors (privacy).
            instrumentation_options=_INSTRUMENTATION_OPTIONS,
        )
        _tracer = trace.get_tracer("fabric_dw.telemetry")
    except Exception:
        _log.debug("Failed to initialise Azure Monitor OpenTelemetry SDK", exc_info=True)
        _tracer = None

    return _tracer


def flush_telemetry(timeout_ms: int = 2000) -> None:
    """Flush pending telemetry spans with a bounded timeout.

    Runs in a daemon thread so it can never block process exit even if the
    exporter is slow or unreachable.  The thread is daemon so the OS kills it
    when the main thread exits (no hang possible).

    Args:
        timeout_ms: Maximum milliseconds to wait for the flush.  Defaults to
            2000 (2 s) to satisfy the B2 hang requirement.
    """
    if not _sdk_initialised or _tracer is None:
        return

    def _do_flush() -> None:
        with contextlib.suppress(Exception):
            from opentelemetry import trace as _trace  # noqa: PLC0415

            provider = _trace.get_tracer_provider()
            force_flush = getattr(provider, "force_flush", None)
            if callable(force_flush):
                force_flush(timeout_millis=timeout_ms)

    t = threading.Thread(target=_do_flush, daemon=True)
    t.start()
    t.join(timeout=timeout_ms / 1000 + 0.1)  # join with a slightly larger wall-clock timeout


# Track whether we have already shut down so shutdown_telemetry is idempotent.
_sdk_shutdown: bool = False


def shutdown_telemetry(timeout_ms: int = 2000) -> None:
    """Shut down the OpenTelemetry provider with a bounded timeout.

    Calling ``provider.shutdown()`` flushes remaining spans AND closes all span
    processors and their exporters, which releases the ``requests``/urllib3
    connection pool held by the Azure Monitor exporter.  Without this call the
    pool is finalized by the GC at interpreter exit — after the ``queue`` module
    globals have been torn down — which triggers:

        AttributeError: 'NoneType' object has no attribute 'Empty'

    in ``urllib3.connectionpool._close_pool_connections``.

    The shutdown runs in a daemon thread so it can never block process exit
    (same bounded pattern as :func:`flush_telemetry`).  A ≤2 s join ensures
    the call returns promptly even if the exporter's HTTP session is slow to
    drain.

    This function is idempotent: subsequent calls after the first shutdown
    are silent no-ops.

    Implementation note — ``_sdk_shutdown`` is set to ``True`` on the *calling*
    thread, before the daemon thread is started.  This is intentional: if the
    daemon thread is killed (process exit) or the provider raises, the flag
    remains set so no retry is attempted.  Retrying ``provider.shutdown()`` at
    exit would be unsafe and is not needed — the process is terminating.

    Args:
        timeout_ms: Maximum milliseconds to wait for the shutdown.  Defaults to
            2000 (2 s).  Must not exceed the B2 hang budget.
    """
    global _sdk_shutdown  # noqa: PLW0603

    if not _sdk_initialised or _tracer is None:
        return
    if _sdk_shutdown:
        return
    _sdk_shutdown = True

    def _do_shutdown() -> None:
        with contextlib.suppress(Exception):
            from opentelemetry import trace as _trace  # noqa: PLC0415

            provider = _trace.get_tracer_provider()
            shutdown_fn = getattr(provider, "shutdown", None)
            if callable(shutdown_fn):
                shutdown_fn()

    t = threading.Thread(target=_do_shutdown, daemon=True)
    t.start()
    t.join(timeout=timeout_ms / 1000 + 0.1)  # join with a slightly larger wall-clock timeout


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

    The marker file is written **after** the notice is successfully printed
    (A3) so that a failed print does not permanently suppress future notices.

    The output always goes to stderr so it can never pollute MCP stdio output.
    """
    if not telemetry_enabled():
        return
    if _is_ci():
        return

    marker_file = _config_dir() / ".telemetry_notice_shown"

    with contextlib.suppress(Exception):
        if marker_file.exists():
            return

    # Print the notice first; only write the marker if this succeeds (A3).
    print(  # noqa: T201
        "fabric-dw collects anonymous usage telemetry to improve the tool. "
        "To opt out: set FABRIC_DISABLE_TELEMETRY=1. "
        "See https://fdw.debruyn.dev/telemetry/ for details.",
        file=sys.stderr,
    )

    # Write marker after successful print (A3: a print failure won't suppress future notices).
    with contextlib.suppress(Exception):
        _config_dir().mkdir(parents=True, exist_ok=True)
        marker_file.write_text("1", encoding="utf-8")


def set_tenant_id(tenant_id: str) -> None:
    """Store the tenant ID so the envelope reads it at runtime.

    The ``tid`` claim decoded from an access token by :func:`decode_tid_from_token`
    is propagated here so every subsequent event envelope carries the tenant.
    Env-var fallback (``AZURE_TENANT_ID`` / ``FABRIC_INTERACTIVE_TENANT_ID``) is
    used by :func:`_build_envelope` when this override has not been set.

    Args:
        tenant_id: The tenant UUID string extracted from the access token.
    """
    global _tenant_id_override  # noqa: PLW0603
    _tenant_id_override = tenant_id


def decode_tid_from_token(token: str) -> str | None:
    """Decode the ``tid`` claim from a JWT access token without verification.

    Only the payload segment (the middle of three base64url-encoded parts) is
    decoded — no signature verification, no network call, no new dependency.

    The function is entirely fail-safe: any malformed, missing, or garbage
    token returns ``None`` and never raises.

    Args:
        token: A JWT string in the form ``header.payload.signature``.

    Returns:
        The ``tid`` claim value as a string, or ``None`` if it cannot be read.
    """
    import base64  # noqa: PLC0415 (stdlib, always available)
    import json  # noqa: PLC0415

    try:
        parts = token.split(".")
        if len(parts) != 3:  # noqa: PLR2004
            return None

        payload_b64 = parts[1]
        # JWT payloads use base64url encoding ('-' → 62, '_' → 63).
        # urlsafe_b64decode handles both standard and URL-safe alphabets.
        # Padding is added to satisfy the 4-byte block requirement.
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        claims = json.loads(payload_bytes)
        tid = claims.get("tid")
        return str(tid) if isinstance(tid, str) and tid else None
    except Exception:
        return None


def cache_tenant_id_from_token(token: str) -> None:
    """Decode ``tid`` from *token* and cache it via :func:`set_tenant_id`.

    A no-op when:
    - Telemetry is disabled (avoids any decode work on opt-out paths).
    - The tenant ID override is already set (idempotent — avoids redundant work
      on subsequent token refreshes within the same session).
    - The ``tid`` claim cannot be decoded from *token*.

    Call this once after acquiring any access token.  Thread-safe for
    concurrent callers on the asyncio event loop (the assignment to
    ``_tenant_id_override`` is atomic on CPython).

    Args:
        token: The raw JWT access-token string returned by
            ``credential.get_token(...).token``.
    """
    try:
        if _tenant_id_override is not None:
            return
        if not telemetry_enabled():
            return
        tid = decode_tid_from_token(token)
        if tid is not None:
            set_tenant_id(tid)
    except Exception:  # noqa: S110
        pass  # telemetry must never break the auth path
