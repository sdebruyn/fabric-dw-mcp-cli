"""Opt-out anonymous usage telemetry for fabric-dw.

Telemetry is **on by default** but can be disabled via:

- ``FABRIC_DW_TELEMETRY_OPT_OUT=1`` (or any truthy value)
- ``DO_NOT_TRACK=1`` (consoledonottrack.com standard)
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
- ``tenant_id`` is always present in the envelope (``"unknown"`` when unresolved)
  so it is reliably queryable on every event.  The tenant is resolved from the
  access-token ``tid`` claim, the Fabric connection-string hostname,
  ``AZURE_TENANT_ID``/``FABRIC_INTERACTIVE_TENANT_ID``, and a locally-cached
  value (persisted under the config dir).  Token-claim extraction is via
  ``cache_tenant_id_from_token()`` (#366).
- Auto-HTTP instrumentation is explicitly disabled to prevent MSAL OAuth
  request URLs (containing tenant IDs) from leaking as span attributes.
- ``shutdown_on_exit`` is disabled; a bounded ``force_flush`` + ``provider.shutdown()``
  (≤8 s total) is performed at app exit in a daemon thread so the CLI never hangs.
  The explicit ``force_flush`` call before ``shutdown()`` is required to reliably
  deliver events emitted near process exit (``command_invoked``, ``app_exited``) —
  see ``shutdown_telemetry()`` docstring for the full analysis.
- ``enable_performance_counters=False`` suppresses the PerformanceCounters
  subsystem, which divides by zero on short-lived processes (#399).

Native App Insights customEvents (telemetry.py design)
-------------------------------------------------------
Events are emitted as OpenTelemetry **log records** (not spans), which causes
the Azure Monitor exporter to produce ``baseType=EventData`` envelopes.  These
land in the ``customEvents`` table and populate the App Insights
"Usage → Events" and "Usage → Users" blades.

Key attribute mappings in azure-monitor-opentelemetry-exporter 1.0.0b53:

Record-level (per-event):
- ``microsoft.custom_event.name`` → EventData.name  (``customEvents`` table)
- ``enduser.pseudo.id``            → tags["ai.user.id"] ("Users" blade)
  (DO NOT use ``enduser.id`` — that maps to ``ai.user.authUserId``, a PII field)
- ``ai.operation.name``            → tags["ai.operation.name"] ("operation_Name")
  Set to the command/tool name on ``command_invoked`` events.

Resource-level (set once, apply to all events via the OTel Resource):
- ``service.namespace`` + ``service.name`` → tags["ai.cloud.role"] (``cloud_RoleName``)
  Set to ``"fabric-dw"`` + surface (``"cli"`` | ``"mcp"``).
- ``service.instance.id``          → tags["ai.cloud.roleInstance"] (``cloud_RoleInstance``)
  Set to ``anonymous_install_id`` — NOT the machine hostname (#477 privacy fix).
- ``service.version``              → tags["ai.application.ver"] (``application_Version``)
  Populated from the package version.
- ``device.id``                    → tags["ai.device.id"]
  Set to ``anonymous_install_id`` to prevent hostname fallback (#477 privacy fix).

Privacy: setting ``service.instance.id`` and ``device.id`` on the Resource prevents
the exporter's hostname fallback (``platform.node()``) for ``cloud_RoleInstance``
and ``ai.device.id`` respectively.  Hostnames often embed the user's real name
(``sam-macbook``, ``DESKTOP-...``), which contradicts the project's anonymity stance.

Sessions limitation: ``ai.session.id`` has NO attribute mapping in exporter
1.0.0b53 (AI_SESSION_ID is never written to tags by the log exporter).  Native
"Sessions" is therefore not achievable via log-record attributes in this SDK
version.  ``session_id`` is kept as a custom dimension (customDimensions) so it
is at least query-able in the Logs blade.  This may be resolved in a future SDK
release — re-check when upgrading azure-monitor-opentelemetry-exporter.

The logs pipeline must be active for customEvents to flow, so
``disable_logging=False`` is passed to ``configure_azure_monitor``.  All other
safeguards (no auto-instrumentation, no metrics, no live metrics, no statsbeat,
no atexit hang) are kept exactly as before.
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
    "suppress_telemetry",
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
# Process-level suppression (used for --help/-h invocations)
# ---------------------------------------------------------------------------

_SUPPRESSED: bool = False


def suppress_telemetry(value: bool = True) -> None:  # noqa: FBT001, FBT002
    """Suppress (or un-suppress) telemetry for this process.

    When *value* is ``True`` (the default), :func:`telemetry_enabled` returns
    ``False`` for the remainder of the process lifetime, causing all telemetry
    functions to become no-ops.  This is checked **before** any env-var or
    config-file logic, so it is always authoritative.

    Pass ``value=False`` to restore the normal enable/disable evaluation.
    This is primarily useful in tests to reset state between test runs.

    Args:
        value: ``True`` to suppress telemetry (default), ``False`` to lift the
            suppression and let normal env/config checks apply.
    """
    global _SUPPRESSED  # noqa: PLW0603
    _SUPPRESSED = value


# ---------------------------------------------------------------------------
# Opt-out helpers
# ---------------------------------------------------------------------------

_FALSY_VALUES = frozenset({"", "0", "false", "no", "off"})


def _is_truthy(value: str) -> bool:
    """Return True when *value* is set and not in the falsy set.

    A value is truthy when it is non-empty and not one of ``""``, ``"0"``,
    ``"false"``, ``"no"``, or ``"off"`` (case-insensitive).  This matches the
    consoledonottrack.com convention and avoids the surprising case where an
    empty string is treated as truthy.
    """
    return value.strip().lower() not in _FALSY_VALUES


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

    - :func:`suppress_telemetry` has been called (process-level suppression,
      checked first — used by ``--help``/``-h`` invocations to skip all
      telemetry init and network I/O)
    - ``FABRIC_DW_TELEMETRY_OPT_OUT`` is truthy (set and not in
      ``{"", "0", "false", "no", "off"}``, case-insensitive)
    - ``DO_NOT_TRACK`` is truthy (same definition)
    - The config file has ``[telemetry] disabled = true``
    """
    # Process-level suppression (e.g. --help/-h) — checked first, always wins.
    if _SUPPRESSED:
        return False

    # FABRIC_DW_TELEMETRY_OPT_OUT truthy → disabled
    if _is_truthy(os.environ.get("FABRIC_DW_TELEMETRY_OPT_OUT", "")):
        return False

    # DO_NOT_TRACK standard (consoledonottrack.com)
    if _is_truthy(os.environ.get("DO_NOT_TRACK", "")):
        return False

    # Config-file opt-out
    return not _is_disabled_by_config()


# ---------------------------------------------------------------------------
# Install-ID persistence
# ---------------------------------------------------------------------------

_INSTALL_ID_FILE = "install_id"
_install_id_cache: str | None = None

# ---------------------------------------------------------------------------
# Tenant-ID persistence (#652)
# ---------------------------------------------------------------------------

_TENANT_ID_FILE = "tenant_id"
_UNSET: object = object()  # sentinel — distinguishes "not yet read" from None/"no value"
_tenant_id_cache: str | None | object = _UNSET  # _UNSET → not yet loaded; None → loaded, absent


def _get_cached_tenant_id() -> str | None:
    """Return the persisted tenant UUID, or None if missing/empty/unreadable.

    In-memory cached after the first read (sentinel ``_UNSET`` means not yet read).
    Never raises.
    """
    global _tenant_id_cache  # noqa: PLW0603
    if _tenant_id_cache is not _UNSET:
        # _tenant_id_cache is str | None here (set either below or by _persist_tenant_id).
        return _tenant_id_cache if isinstance(_tenant_id_cache, str) else None

    result: str | None = None
    with contextlib.suppress(Exception):
        id_file = _config_dir() / _TENANT_ID_FILE
        if id_file.exists():
            value = id_file.read_text(encoding="utf-8").strip()
            if value:
                result = value

    _tenant_id_cache = result
    return result


def _persist_tenant_id(tid: str) -> None:
    """Write the tenant UUID to the config directory.  Fail-safe: never raises.

    The in-memory cache is only updated when the write succeeds, so a
    read-only-FS failure does not leave the cache in a "loaded but not
    persisted" state.  The current process stays correct regardless via
    ``_tenant_id_override``.
    """
    global _tenant_id_cache  # noqa: PLW0603
    with contextlib.suppress(Exception):
        config_dir = _config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / _TENANT_ID_FILE).write_text(tid, encoding="utf-8")
        # Cache only after a successful write (C1: don't cache on FS failure).
        _tenant_id_cache = tid


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
    3. ``importlib.metadata`` resolves the package dist-info and the install
       was editable (``"editable": true`` in ``direct_url.json``) → ``"source"``.
    4. ``importlib.metadata`` resolves the package version without an editable
       marker → ``"pip"``.
    5. ``importlib.metadata`` raises ``PackageNotFoundError`` (running from
       source tree, no dist-info installed) → ``"source"``.

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


def _build_envelope() -> dict[str, object]:
    """Build the shared telemetry envelope attached to every event.

    Custom dimensions included here are those with no native Part A mapping.
    Fields that have native App Insights homes are set via the OTel Resource
    (``_build_otel_resource``) and are NOT duplicated here:

    - ``app_version``  → native ``application_Version`` (resource ``service.version``)
    - ``surface``      → native ``cloud_RoleName``       (resource ``service.name``)

    Fields omitted entirely (dropped in #477):
    - ``anonymous_install_id`` — already shipped natively as ``user_Id`` (← ``enduser.pseudo.id``)
    - ``is_ci``                — dropped; carries no useful signal.

    ``tenant_id`` is always present (``"unknown"`` when unresolved) so it is
    reliably queryable on every event.  No native Part A slot is reachable for
    tenant on the log-record path in this exporter version; ``customDimensions``
    is the correct mechanism here.
    """
    import platform as _platform  # noqa: PLC0415

    python_info = sys.version_info
    python_version = f"{python_info.major}.{python_info.minor}"

    # Prefer the runtime-set override (populated by #366 token-claim hook),
    # then fall back to environment variables, then the persisted cache (#652),
    # then "unknown" so the key is always present on every event (Finding 2 / #477).
    # Bounded staleness: if telemetry was disabled on the previous run, the cache
    # may hold a tenant from an earlier authenticated run against a different tenant.
    # This is the accepted trade-off — at most one misattributed lifecycle event
    # (e.g. app_started) before set_tenant_id() corrects it in the same process.
    tenant_id: str = (
        _tenant_id_override
        or os.environ.get("AZURE_TENANT_ID")
        or os.environ.get("FABRIC_INTERACTIVE_TENANT_ID")
        or _get_cached_tenant_id()
        or "unknown"
    )

    return {
        "session_id": _SESSION_ID,
        "python_version": python_version,
        "os": _platform.system().lower(),
        "arch": _platform.machine().lower(),
        "install_method": _detect_install_method(),
        "auth_mode": _detect_auth_mode(),
        "tenant_id": tenant_id,
    }


def _build_otel_resource(surface: str) -> object | None:
    """Build an OTel Resource that populates native Part A context fields.

    The Resource is passed to ``configure_azure_monitor`` so the exporter
    sets Part A tags from it rather than using hostname fallbacks.

    Mappings (#477):
    - ``service.namespace`` + ``service.name`` → ``cloud_RoleName`` / ``AppRoleName``
      Gives a meaningful role name (e.g. ``"fabric-dw.cli"``) instead of
      ``unknown_service:*``, and creates two Application Map nodes.
    - ``service.instance.id`` = install_id → ``cloud_RoleInstance`` / ``AppRoleInstance``
      Prevents hostname fallback (``platform.node()``).  The pseudonymous install UUID
      is non-identifying and gives meaningful per-install instance counts.
    - ``service.version`` = app_version → ``application_Version`` / ``AppVersion``
      Enables version-adoption and release-regression views.
    - ``device.id`` = install_id → ``ai.device.id``
      ``_populate_part_a_fields`` overrides ``ai.device.id`` with this value only when
      it is truthy; an empty string would leave the hostname default in place.

    Returns the ``opentelemetry.sdk.resources.Resource`` object, or ``None`` if
    the SDK import fails (the caller falls back to the default resource).
    """
    try:
        from opentelemetry.sdk.resources import Resource  # noqa: PLC0415

        try:
            from fabric_dw._version import __version__ as _version  # noqa: PLC0415
        except Exception:
            _version = "unknown"

        install_id = _get_install_id()

        return Resource.create(
            {
                "service.namespace": "fabric-dw",
                "service.name": surface,  # "cli" | "mcp" → cloud_RoleName = "fabric-dw.cli|mcp"
                "service.instance.id": install_id,  # → cloud_RoleInstance (not hostname)
                "service.version": _version,  # → application_Version
                "device.id": install_id,  # → ai.device.id (not hostname)
            }
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# SDK initialisation (lazy, fail-safe)
# ---------------------------------------------------------------------------

# _otel_logger holds the OTel Logger used to emit customEvents via the logs API.
# This replaces the old _tracer (spans→dependencies path).  The name is kept
# generic so existing tests that poke _sdk_initialised / _tracer still work after
# we alias _tracer → _otel_logger below.
_otel_logger: object | None = None
_tracer: object | None = None  # alias kept for backward-compat with existing tests
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
       Belt-and-suspenders suppression.  At the pinned versions (azure-monitor-opentelemetry
       1.8.8 / exporter 1.0.0b53) ``azure.core.pipeline.policies._retry`` defines ``_LOGGER``
       but has **zero call sites** — the "Retrying due to server request error" message is
       actually emitted by ``azure.monitor.opentelemetry.exporter.export._base`` (already
       covered by entry 1).  This entry guards against future SDK versions adding log calls
       to the azure-core pipeline tree.

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
        # A3: azure-core pipeline — belt-and-suspenders only at pinned versions
        # (azure.core.pipeline.policies._retry defines _LOGGER but has zero call sites;
        # the "Retrying due to server request error" message comes from
        # azure.monitor.opentelemetry.exporter.export._base, already covered above).
        # Kept here to guard against future SDK versions emitting from this tree.
        "azure.core.pipeline.policies",
    ):
        lgr = logging.getLogger(name)
        lgr.setLevel(logging.CRITICAL)
        lgr.propagate = False
        if not any(isinstance(h, logging.NullHandler) for h in lgr.handlers):
            lgr.addHandler(logging.NullHandler())


def _get_tracer() -> object | None:
    """Lazily initialise the Azure Monitor OpenTelemetry SDK and event logger.

    After initialisation the global ``_otel_logger`` (and its alias ``_tracer``)
    hold the OTel Logger used by :func:`emit_event` to fire customEvents via the
    logs API.  The function returns that logger on success, or None if
    initialisation fails.  Raises nothing.

    Privacy / hang safeguards
    -------------------------
    - ``resource`` is built via ``_build_otel_resource`` and passed to
      ``configure_azure_monitor`` so ``service.instance.id`` and ``device.id``
      are set explicitly.  This prevents the exporter from falling back to
      ``platform.node()`` for ``cloud_RoleInstance`` / ``ai.device.id``, which
      would leak the machine hostname on every event (#477 privacy fix).
    - ``instrumentation_options`` explicitly disables all auto-HTTP and Azure SDK
      instrumentors so MSAL OAuth URLs (containing tenant IDs) are never captured
      as span attributes (B1).
    - ``disable_logging=False`` activates the log/event exporter pipeline so that
      log records carrying ``microsoft.custom_event.name`` are exported as
      ``EventData`` (``customEvents`` table) — the reason this function now sets
      up a logger instead of a tracer.
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
    global _otel_logger, _tracer, _sdk_initialised  # noqa: PLW0603

    if _sdk_initialised:
        return _otel_logger

    _sdk_initialised = True

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor  # noqa: PLC0415
        from opentelemetry._logs import get_logger  # noqa: PLC0415

        # A2/A3: silence Azure SDK logger trees before any network attempt (#411).
        _harden_azure_sdk_logging()

        # A4: disable statsbeat (Azure Monitor internal telemetry-about-telemetry).
        # Statsbeat creates two sources of unclosed-socket ResourceWarnings on
        # short-lived CLI processes (#418):
        #   1. An urllib3 connection pool is allocated immediately in the statsbeat
        #      exporter __init__ (during StatsbeatManager initialisation).  On
        #      processes that exit in under ~15 s the pool is destroyed by the GC
        #      rather than closed cleanly, producing "Exception ignored in: ..." at
        #      interpreter shutdown.
        #   2. After a ~15 s warmup timer the statsbeat exporter probes the Azure
        #      IMDS endpoint (169.254.169.254:80) to detect whether the process runs
        #      on an Azure VM.  That probe socket is also left unclosed on exit.
        # Disabling statsbeat prevents both.  Use setdefault so an explicit operator
        # override (e.g. APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL=false) is still
        # respected.
        os.environ.setdefault("APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL", "true")

        # Build the OTel Resource that populates native Part A fields and prevents
        # hostname fallback for cloud_RoleInstance / ai.device.id (#477).
        resource = _build_otel_resource(_current_surface)

        configure_kwargs: dict[str, object] = {
            "connection_string": _get_connection_string(),
            "logger_name": "fabric_dw.telemetry",
            # disable_logging=False (default) is intentional: the log/event
            # exporter must be active so customEvents land in the customEvents
            # table.  Without the logs pipeline, log records carrying
            # microsoft.custom_event.name are silently dropped and events never
            # appear in the App Insights "Usage → Events" or "Usage → Users" blades.
            "disable_logging": False,
            "disable_metrics": True,
            # A1: disable PerformanceCounters — not covered by disable_metrics in
            # azure-monitor-opentelemetry 1.8+; its _get_processor_time callback
            # divides by zero on short-lived processes and logs a traceback (#399).
            "enable_performance_counters": False,
            # A2: belt-and-suspenders — QuickPulse must never ping the LiveEndpoint.
            # Suppresses _quickpulse/_exporter.py::_ping tracebacks on connection
            # refused even if the default changes in a future SDK version (#411).
            "enable_live_metrics": False,
            # B2: disable unbounded (30 s) atexit flush — we do our own bounded flush.
            "shutdown_on_exit": False,
            # B1: disable all auto-HTTP / Azure SDK instrumentors (privacy).
            "instrumentation_options": _INSTRUMENTATION_OPTIONS,
        }
        # Pass the resource when available so native Part A fields are populated
        # (cloud_RoleName, cloud_RoleInstance, application_Version, ai.device.id).
        if resource is not None:
            configure_kwargs["resource"] = resource

        configure_azure_monitor(**configure_kwargs)
        # Obtain the OTel Logger via the global LoggerProvider set up by
        # configure_azure_monitor.  This logger is used in emit_event to fire
        # customEvents as log records (not spans).
        _otel_logger = get_logger("fabric_dw.telemetry")
        _tracer = _otel_logger  # alias: existing tests check _tracer is not None
    except Exception:
        _log.debug("Failed to initialise Azure Monitor OpenTelemetry SDK", exc_info=True)
        _otel_logger = None
        _tracer = None

    return _otel_logger


def flush_telemetry(timeout_ms: int = 2000) -> None:
    """Flush pending telemetry events with a bounded timeout.

    Runs in a daemon thread so it can never block process exit even if the
    exporter is slow or unreachable.  The thread is daemon so the OS kills it
    when the main thread exits (no hang possible).

    Both the tracer provider (spans, if any) and the logger provider (customEvents
    log pipeline) are flushed so no records are lost.

    Args:
        timeout_ms: Maximum milliseconds to wait for the flush.  Defaults to
            2000 (2 s) to satisfy the B2 hang requirement.
    """
    if not _sdk_initialised or _otel_logger is None:
        return

    def _do_flush() -> None:
        # Each pipeline is flushed independently so a failure in one does
        # not prevent the other from running.

        # Tracer provider — belt-and-suspenders; no spans are emitted in the
        # new path, but kept here in case the SDK creates internal spans.
        with contextlib.suppress(Exception):
            from opentelemetry import trace as _trace  # noqa: PLC0415

            provider = _trace.get_tracer_provider()
            force_flush = getattr(provider, "force_flush", None)
            if callable(force_flush):
                force_flush(timeout_millis=timeout_ms)

        # Logger provider — this is the primary pipeline for customEvents.
        with contextlib.suppress(Exception):
            from opentelemetry._logs import get_logger_provider  # noqa: PLC0415

            log_provider = get_logger_provider()
            log_force_flush = getattr(log_provider, "force_flush", None)
            if callable(log_force_flush):
                log_force_flush(timeout_millis=timeout_ms)

    t = threading.Thread(target=_do_flush, daemon=True)
    t.start()
    t.join(timeout=timeout_ms / 1000 + 0.1)  # join with a slightly larger wall-clock timeout


# Track whether we have already shut down so shutdown_telemetry is idempotent.
_sdk_shutdown: bool = False


def shutdown_telemetry(timeout_ms: int = 8000) -> None:
    """Shut down the OpenTelemetry providers with a bounded timeout.

    Calls ``force_flush`` then ``shutdown()`` on both the tracer provider and
    the logger provider.  The logger provider path is critical: it must export
    all pending customEvents (``command_invoked``, ``app_exited``) that were
    enqueued just before shutdown is called.

    Why force_flush before shutdown?
    ---------------------------------
    The ``BatchLogRecordProcessor`` uses a background worker thread that sleeps
    for ``OTEL_BLRP_SCHEDULE_DELAY`` (default 5 000 ms) between export cycles.
    Events emitted immediately before ``shutdown()`` sit in the queue waiting for
    the next worker wake-up.  ``provider.shutdown()`` wakes the worker and waits
    for a final ``EXPORT_ALL`` pass, but *also* calls ``self._shutdown = True``
    which prevents any further ``emit()`` calls.  If the outer join timeout is
    shorter than the HTTP round-trip to the App Insights ingestion endpoint
    (typically 2-4 s), the daemon thread is killed before the POST completes.

    Calling ``force_flush(timeout_ms - 2000)`` **before** ``shutdown()`` ensures
    all queued records are exported with a generous bound.  The remaining 2 s is
    then used for the provider ``shutdown()`` which cleans up the connection pool
    (preventing the ``AttributeError: 'NoneType' object has no attribute 'Empty'``
    at interpreter exit when urllib3 pool is finalised after queue module teardown).

    Exit-latency trade-off
    ----------------------
    With ``timeout_ms=8000`` the CLI may add up to 8 s at exit on a fully-loaded
    or slow network.  In practice the HTTP POST completes in 2-4 s so the typical
    added latency is 3-5 s.  All logic runs in a daemon thread; the join caps the
    wait — the OS will kill the daemon thread if the main thread exits first.

    The shutdown runs in a daemon thread so it can never block process exit
    (same bounded pattern as :func:`flush_telemetry`).

    This function is idempotent: subsequent calls after the first shutdown
    are silent no-ops.

    Implementation note — ``_sdk_shutdown`` is set to ``True`` on the *calling*
    thread, before the daemon thread is started.  This is intentional: if the
    daemon thread is killed (process exit) or the provider raises, the flag
    remains set so no retry is attempted.  Retrying ``provider.shutdown()`` at
    exit would be unsafe and is not needed — the process is terminating.

    Args:
        timeout_ms: Maximum milliseconds to wait for the full flush+shutdown
            sequence.  Defaults to 8000 (8 s): ~6 s for force_flush (HTTP POST
            round-trip) + ~2 s for provider cleanup.
    """
    global _sdk_shutdown  # noqa: PLW0603

    if not _sdk_initialised or _otel_logger is None:
        return
    if _sdk_shutdown:
        return
    _sdk_shutdown = True

    # Reserve ~2 s for the provider.shutdown() cleanup call; the rest goes to
    # force_flush.  At the default timeout_ms=8000 this gives 6 s for
    # force_flush and 2 s for shutdown — both well within the daemon-thread
    # join cap (timeout_ms/1000 + 0.5 s).  If timeout_ms were set below 4000
    # the floor kicks in and both allocations become 2 s, which still fits
    # inside the join cap because the daemon thread is killed when the main
    # thread exits — the cap is a worst-case wall-clock bound, not a guarantee.
    flush_timeout_ms = max(timeout_ms - 2000, 2000)

    def _do_shutdown() -> None:
        # Each pipeline is flushed then shut down independently so a failure in
        # one does not prevent the other from running.

        # Logger provider — CRITICAL PATH for customEvents.
        # force_flush first: ensures command_invoked / app_exited records that
        # were enqueued microseconds before this call are exported before shutdown
        # closes the exporter.  Without the explicit force_flush, those records
        # depend on the BatchLogRecordProcessor worker waking up inside shutdown()
        # which races with our outer join timeout.
        # Resolve the provider once and reuse it for both flush and shutdown.
        with contextlib.suppress(Exception):
            from opentelemetry._logs import get_logger_provider  # noqa: PLC0415

            log_provider = get_logger_provider()
            log_force_flush = getattr(log_provider, "force_flush", None)
            if callable(log_force_flush):
                log_force_flush(timeout_millis=flush_timeout_ms)
            log_shutdown = getattr(log_provider, "shutdown", None)
            if callable(log_shutdown):
                log_shutdown()

        # Tracer provider — belt-and-suspenders; no spans are emitted in the
        # new path, but kept so any SDK-internal spans are flushed.
        with contextlib.suppress(Exception):
            from opentelemetry import trace as _trace  # noqa: PLC0415

            provider = _trace.get_tracer_provider()
            shutdown_fn = getattr(provider, "shutdown", None)
            if callable(shutdown_fn):
                shutdown_fn()

    t = threading.Thread(target=_do_shutdown, daemon=True)
    t.start()
    t.join(timeout=timeout_ms / 1000 + 0.5)  # extra 0.5 s wall-clock buffer


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def emit_event(name: str, attributes: dict[str, object]) -> None:
    """Emit a telemetry event as an Application Insights customEvent.

    The event is emitted via the OpenTelemetry logs API as a log record
    carrying ``microsoft.custom_event.name``.  The Azure Monitor log exporter
    maps this to ``baseType=EventData``, which lands in the ``customEvents``
    table and populates the App Insights "Usage → Events" blade.

    ``enduser.pseudo.id`` is set to the anonymous install UUID so the event
    carries ``ai.user.id`` (→ "Usage → Users" blade).  This is a randomly
    generated UUID — not a username, email, or any PII.

    Callers may pass ``ai.operation.name`` in *attributes* to populate the
    native ``operation_Name`` Part A field.  ``emit_command_invoked`` sets
    this to the command/tool name so it appears in the portal's "Operation
    Name" column instead of being blank.

    Sessions note: ``ai.session.id`` has no attribute mapping in
    azure-monitor-opentelemetry-exporter 1.0.0b53.  ``session_id`` is therefore
    kept as a custom dimension (customDimensions) so it is query-able in the
    Logs blade.  Re-check when upgrading the exporter.

    Fire-and-forget: never raises, never blocks the caller noticeably.
    When telemetry is disabled, this is a guaranteed no-op.
    """
    if not telemetry_enabled():
        return

    try:
        otel_logger = _get_tracer()
        if otel_logger is None or not hasattr(otel_logger, "emit"):
            return

        from opentelemetry._logs import LogRecord  # noqa: PLC0415

        envelope = _build_envelope()
        merged: dict[str, object] = {**envelope, **attributes}

        # Add the special attributes that drive native App Insights mapping:
        #   microsoft.custom_event.name → EventData.name (customEvents table)
        #   enduser.pseudo.id           → ai.user.id ("Users" blade)
        # NOTE: enduser.pseudo.id contains the anonymous install UUID — a random
        # UUID generated on first run and stored locally.  It is NOT a username,
        # email address, or any form of PII.  DO NOT replace with enduser.id,
        # which maps to ai.user.authUserId (authenticated / PII field).
        merged["microsoft.custom_event.name"] = name
        merged["enduser.pseudo.id"] = _get_install_id()
        # ai.operation.name may already be in merged (set by caller e.g. emit_command_invoked).
        # It is left as-is when present; only set the event name as fallback for lifecycle events.
        if "ai.operation.name" not in merged:
            merged["ai.operation.name"] = name

        record = LogRecord(  # ty: ignore[no-matching-overload]
            attributes=merged,  # type: ignore[arg-type]
        )

        # getattr is intentional: `otel_logger` is typed as `object` to avoid
        # importing the OTel Logger type at module level (lazy SDK import), so
        # attribute access would fail static analysis; we guard with hasattr above
        # and suppress B009 here.
        emit_fn = getattr(otel_logger, "emit")  # noqa: B009
        emit_fn(record)
    except Exception:
        _log.debug("Failed to emit telemetry event %r", name, exc_info=True)


def record_app_started(surface: str) -> None:
    """Emit an ``app_started`` lifecycle event.

    Args:
        surface: Either ``"cli"`` or ``"mcp"``.
    """
    global _current_surface  # noqa: PLW0603
    _current_surface = surface
    # ``surface`` is no longer sent as a custom dimension: it is shipped natively
    # as ``cloud_RoleName`` via the OTel Resource (``service.name`` = surface).
    emit_event("app_started", {})


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
    - Telemetry is disabled (via env var, DO_NOT_TRACK, or config file).
    - The marker file already exists (notice was already shown).

    The marker file is written **after** the notice is successfully printed
    (A3) so that a failed print does not permanently suppress future notices.

    The output always goes to stderr so it can never pollute MCP stdio output.
    """
    if not telemetry_enabled():
        return

    marker_file = _config_dir() / ".telemetry_notice_shown"

    with contextlib.suppress(Exception):
        if marker_file.exists():
            return

    # Print the notice first; only write the marker if this succeeds (A3).
    print(  # noqa: T201
        "fabric-dw collects anonymous usage telemetry to improve the tool. "
        "To opt out: set FABRIC_DW_TELEMETRY_OPT_OUT=1. "
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

    **Persistence**: when :func:`telemetry_enabled` returns ``True`` at the time
    of this call, the resolved tenant is also written to the persistent tenant
    store (``$XDG_CONFIG_HOME/fabric-dw/tenant_id``) so that subsequent process
    invocations can read it back before authentication completes.  When telemetry
    is disabled the value is kept only in-memory for the lifetime of the current
    process and nothing is written to disk.

    Args:
        tenant_id: The tenant UUID string extracted from the access token.
    """
    global _tenant_id_override  # noqa: PLW0603
    _tenant_id_override = tenant_id
    if telemetry_enabled():
        _persist_tenant_id(tenant_id)


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
