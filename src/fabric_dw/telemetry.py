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
- This package installs no metric exporter, and the only spans it exports are
  the MCP protocol spans emitted by MCP Python SDK v2, which installs an
  OpenTelemetry middleware on every server unconditionally (#1049).
  ``OTEL_TRACES_EXPORTER`` and ``OTEL_METRICS_EXPORTER`` are hard-set to
  ``none`` around the ``configure_azure_monitor`` call and restored afterwards;
  the matching kwargs are silently overwritten by the library and do not work.
  The trace pipeline is then built separately, on the MCP surface only, by
  :mod:`fabric_dw.telemetry_spans`, whose exporter drops every span that did not
  come from the MCP SDK and rebuilds the rest from an allowlist so no
  client-supplied string is exported.  Note the qualifier: a host process that
  embeds fabric-dw and has already installed its own ``TracerProvider`` keeps
  it, and its spans keep going wherever it chose.  That is correct and
  desirable; what this prevents is *this package* adding an export path the user
  did not ask for.
- Five side channels of the Azure Monitor stack are switched off, each behind an
  environment variable of its own that nothing in the library's API surface
  points to, and each found only by measuring a live process.  This list is the
  checklist to re-verify, by measurement, on every bump of this dependency:

  1. ``APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL`` (statsbeat, including the
     IMDS probe statsbeat itself makes; note that this does NOT cover the
     separate IMDS probe from the resource detectors, which is number 5).
     Set here, and deliberately never restored: see the comment at its
     assignment site.
  2. ``APPLICATIONINSIGHTS_SDKSTATS_DISABLED`` (customer sdkstats, separate from
     statsbeat, which would otherwise run its own metrics pipeline on our
     connection string).  Set and restored, via ``_SCOPED_SIDE_CHANNELS_OFF``.
  3. ``APPLICATIONINSIGHTS_OPENTELEMETRY_RESOURCE_METRIC_DISABLED`` (the
     ``_OTELRESOURCE_`` ``MetricData`` envelope appended to every span export).
     Set in :mod:`fabric_dw.telemetry_spans`, and only on the path where that
     module actually builds an exporter: a host process that already owns the
     global ``TracerProvider`` returns before this point, which is correct,
     because in that case no exporter of ours exists to smuggle a metric.  Never
     restored either: it is read at export time, not at construction.
  4. ``APPLICATIONINSIGHTS_CONTROLPLANE_DISABLED`` (the OneSettings control
     plane: an hourly outbound poll that also lets Microsoft toggle this
     installation's offline storage remotely, #1053).  Set and restored.
  5. ``OTEL_EXPERIMENTAL_RESOURCE_DETECTORS`` (``configure_azure_monitor``
     defaults it to ``azure_app_service,azure_vm`` and re-runs
     ``Resource.create()``; the ``azure_vm`` detector calls the Azure IMDS
     endpoint at 169.254.169.254 on every telemetry-enabled process, and on an
     Azure VM would merge ``cloud.resource_id``, ``host.id`` and ``host.name``
     into the exported Resource).  Set and restored.

  Treat that as a property of this stack rather than as five coincidences:
  assume a sixth exists and check for it by measurement, not by reading the
  configuration surface, whenever this dependency is upgraded or a new exporter
  is added.  Every one of the five was found this way, and none of them by
  reading the library's API surface.
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
safeguards (no tracing, no auto-instrumentation, no metrics, no live metrics,
no statsbeat, no atexit hang) are kept exactly as before.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import threading
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path

__all__ = [
    "MCP_CLIENT_OTHER",
    "MCP_CLIENT_UNKNOWN",
    "cache_tenant_id_from_token",
    "current_mcp_client",
    "decode_tid_from_token",
    "emit_event",
    "flush_telemetry",
    "maybe_print_first_run_notice",
    "mcp_client_scope",
    "normalise_mcp_client",
    "record_app_exited",
    "record_app_started",
    "record_mcp_server_started",
    "set_auth_mode",
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


# ---------------------------------------------------------------------------
# Config-disabled cache (#844)
# ---------------------------------------------------------------------------

# Computed once per process; None means not yet read from disk.
_config_disabled_cache: bool | None = None


def _is_disabled_by_config() -> bool:
    """Return True when the config file contains a truthy ``[telemetry] disabled``.

    Privacy-safety contract (fail-CLOSED):

    - File absent → not opted out (return False).
    - File present, opt-out key is truthy → opted out (return True).
    - File present but unreadable / unparseable → opted out (return True).
      A user who wrote ``[telemetry] disabled = true`` and whose config file
      becomes temporarily unreadable (e.g. another process holds the lock)
      must NOT have telemetry sent against their intent.

    This function uses a best-effort lock-free read with a very short
    timeout so it does not block the CLI startup path.  The normal
    :func:`~fabric_dw.config.load_config` path (used for all other config
    reads) keeps its lenient swallow-and-warn semantics; the opt-out
    decision is the sole caller that needs fail-closed behaviour.

    The result is cached after the first SUCCESSFUL read; config.toml does
    not change within a process run so a one-time read is correct (#844).
    Transient failures (IO error, TOCTOU race between exists() and
    read_text()) are NOT cached: the fail-closed value is returned for that
    call only, and the next call retries from scratch so it can recover.

    Concurrency note: two callers that race on the first read will both
    compute the same value (config.toml is process-stable), and the last
    write to ``_config_disabled_cache`` wins harmlessly.  A lock is not
    needed because the values are always identical and the cached type is
    ``bool``, which CPython assigns atomically on all supported platforms.
    """
    global _config_disabled_cache  # noqa: PLW0603
    if _config_disabled_cache is not None:
        return _config_disabled_cache

    import tomllib as _tomllib  # noqa: PLC0415

    config_file = _config_dir() / "config.toml"
    if not config_file.exists():
        _config_disabled_cache = False
        return False

    # File exists — read it.  Fail CLOSED on any read/parse error so that
    # a temporarily unreadable file does not accidentally enable telemetry.
    try:
        raw = config_file.read_text(encoding="utf-8")
        data = _tomllib.loads(raw)
        telemetry_section = data.get("telemetry", {})
        if not isinstance(telemetry_section, dict):
            _config_disabled_cache = False
            return False
        raw_disabled = telemetry_section.get("disabled")
        # Accept bool, int, and string — mirrors _parse_telemetry_section in
        # config.py.  CRITICAL: do NOT use bool(str_value) because
        # bool("false") is True and would silently re-enable telemetry.
        if isinstance(raw_disabled, bool):
            disabled = raw_disabled
        elif isinstance(raw_disabled, int):
            disabled = bool(raw_disabled)
        elif isinstance(raw_disabled, str):
            disabled = raw_disabled.strip().lower() not in _FALSY_VALUES
        else:
            disabled = False
    except Exception:
        # Any read or parse failure when the file EXISTS is treated as
        # opt-out (fail-closed) to honour the user's declared intent.
        # Do NOT cache this result: the error may be transient (IO error,
        # TOCTOU race between exists() and read_text()).  Caching True here
        # would permanently disable telemetry for the whole process after a
        # single transient failure.  The next call will retry and can cache
        # a successfully-computed result.
        return True
    else:
        _config_disabled_cache = disabled
        return disabled


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
# Install-method cache (#844)
# ---------------------------------------------------------------------------

# Computed once per process; None means not yet detected.
_install_method_cache: str | None = None

# ---------------------------------------------------------------------------
# Tenant-ID persistence (#652)
# ---------------------------------------------------------------------------

_TENANT_ID_FILE = "tenant_id"
_UNSET: object = object()  # sentinel — distinguishes "not yet read" from None/"no value"
_tenant_id_cache: str | object | None = _UNSET  # _UNSET → not yet loaded; None → loaded, absent


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

    The result is cached after the first detection; install method does not
    change within a process run so a one-time detection is correct (#844).
    """
    global _install_method_cache  # noqa: PLW0603
    if _install_method_cache is not None:
        return _install_method_cache

    # uv explicitly sets UV or UV_VIRTUAL_ENV in the runner environment.
    if os.environ.get("UV") or os.environ.get("UV_VIRTUAL_ENV"):
        result = "uv"
    # pipx: either the dedicated env var or the executable lives inside a pipx dir.
    elif os.environ.get("PIPX_HOME") or "pipx" in (sys.executable or ""):
        result = "pipx"
    else:
        # Source / editable checkout: importlib.metadata won't find the package
        # (no dist-info installed), or it will resolve with a direct_url that has
        # "editable": true.  Default to "source" when the metadata check fails.
        result = "source"
        with contextlib.suppress(Exception):
            import importlib.metadata  # noqa: PLC0415

            dist = importlib.metadata.distribution("fabric-dw")
            # Check for an editable install via direct_url.json
            direct_url = dist.read_text("direct_url.json")
            result = "source" if (direct_url and '"editable": true' in direct_url) else "pip"

    _install_method_cache = result
    return result


def _detect_auth_mode() -> str:
    """Return a categorical auth mode string based on environment signals.

    This is the *fallback* heuristic used when no authoritative override has
    been provided via :func:`set_auth_mode`.  It inspects environment variables
    rather than the credential the auth layer actually resolved, so it can
    mis-classify the ``DEFAULT`` path (e.g. ``AZURE_CONFIG_DIR`` is NOT set by a
    plain ``az login``, causing real Azure CLI sessions to fall through to
    ``"interactive"``).  Prefer calling :func:`set_auth_mode` from the auth
    layer for an accurate value.

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

    # Azure CLI hint — AZURE_CONFIG_DIR is only set when the caller explicitly
    # customises the Azure CLI config directory; a standard ``az login`` does NOT
    # set it.  This check therefore catches only a narrow subset of real CLI
    # sessions.  The authoritative path is via set_auth_mode() after token
    # acquisition (see http_client._get_token).
    if os.environ.get("AZURE_CONFIG_DIR"):
        return "azure_cli"

    # Default / interactive fallback
    return "interactive"


# ---------------------------------------------------------------------------
# Tenant ID override (set_tenant_id / #366 hook)
# ---------------------------------------------------------------------------

_tenant_id_override: str | None = None

# ---------------------------------------------------------------------------
# Auth-mode override (set_auth_mode / #665 hook)
# ---------------------------------------------------------------------------

# Set once per process by the auth layer after the credential is resolved.
# None means "not yet set" — _build_envelope falls back to _detect_auth_mode().
_auth_mode_override: str | None = None


def set_auth_mode(mode: str) -> None:
    """Record the auth mode derived from the credential the auth layer resolved.

    Call this once after it is known which credential successfully acquired a
    token.  Subsequent calls are silently ignored (idempotent: the first
    resolved credential wins, matching the ``cache_tenant_id_from_token``
    pattern).

    This is a no-op when telemetry is disabled so that the overhead of
    inspecting credentials is skipped on opt-out paths.

    Args:
        mode: One of ``"service_principal"``, ``"github_oidc"``, ``"azure_cli"``,
            ``"interactive"``, or ``"managed_identity"``.
    """
    global _auth_mode_override  # noqa: PLW0603
    if _auth_mode_override is not None:
        return
    if not telemetry_enabled():
        return
    _auth_mode_override = mode


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
        # Prefer the runtime-set override populated by set_auth_mode() (the
        # auth layer records the credential that actually resolved); fall back
        # to the env-var heuristic for processes that never reach token
        # acquisition (e.g. --help invocations, pre-auth failures).
        "auth_mode": _auth_mode_override or _detect_auth_mode(),
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
# Lock that guards the lazy SDK init.  Double-checked locking: the outer
# fast-path check (no lock) handles the common case; the inner re-check
# inside the lock makes init idempotent under concurrent first calls.
_sdk_init_lock: threading.Lock = threading.Lock()
_current_surface: str = "cli"
# True only when install_mcp_span_pipeline() actually claimed the process-wide
# TracerProvider.  False when a host application had already installed one, in
# which case that provider is theirs to flush and shut down, not ours.
_span_pipeline_installed: bool = False

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

# Environment applied around the configure_azure_monitor call to switch off the
# metric pipeline and to keep the library from building a trace pipeline of its
# own.  Only the exact string "none" works; see the long comment at the call
# site for why this is a hard set rather than a setdefault, and why
# OTEL_LOGS_EXPORTER is deliberately absent.
#
# OTEL_TRACES_EXPORTER stays "none" even though this package now DOES export
# spans: what the library would install is an unfiltered AzureMonitorTraceExporter
# that ships every span in the process verbatim.  The trace pipeline is instead
# built by telemetry_spans.install_mcp_span_pipeline, whose exporter drops
# non-MCP spans and rebuilds the rest from an allowlist.
_OTEL_EXPORTERS_OFF: dict[str, str] = {
    "OTEL_TRACES_EXPORTER": "none",
    "OTEL_METRICS_EXPORTER": "none",
}

# Side channels switched off with ``setdefault`` semantics AND restored
# afterwards, so an embedding host is not left holding this package's choices
# (#1052).  Each one was measured to be read only while the exporters are being
# built, which is what makes restoring safe.  Two related variables are
# deliberately NOT in here; both are documented at their assignment sites.
_SCOPED_SIDE_CHANNELS_OFF: dict[str, str] = {
    # The OneSettings control plane (#1053).  Its only read is
    # get_configuration_manager(), from BaseExporter.__init__.  Measured after a
    # restore, over a 35 s window covering the worker's 5-15 s startup delay: no
    # ConfigurationWorker thread and no contact with the settings endpoint.
    "APPLICATIONINSIGHTS_CONTROLPLANE_DISABLED": "true",
    # Customer sdkstats.  The export-time checks read `manager.is_enabled`, a
    # cached attribute rather than the environment, so a restore does not re-arm
    # it.  Measured: `_should_collect_customer_sdkstats()` stays False on the
    # live log exporter after the variable is removed.
    "APPLICATIONINSIGHTS_SDKSTATS_DISABLED": "true",
    # Resource detectors.  configure_azure_monitor does
    # `environ.setdefault(OTEL_EXPERIMENTAL_RESOURCE_DETECTORS,
    # "azure_app_service,azure_vm")` and then re-runs Resource.create(); the
    # azure_vm detector calls urlopen() against the Azure IMDS endpoint at
    # 169.254.169.254 on every telemetry-enabled process, on both surfaces.
    # Measured before this line existed: `dns:169.254.169.254:80` followed by
    # `tcp:('169.254.169.254', 80)`.  Nothing about the VM reaches the wire
    # today, but on an Azure VM the detector merges `cloud.resource_id`
    # (subscription id, resource group and VM name), `host.id` and `host.name`
    # into the Resource, one configuration change away from being exported.
    # Read only inside Resource.create(), so restoring is safe.
    "OTEL_EXPERIMENTAL_RESOURCE_DETECTORS": "",
}


@contextlib.contextmanager
def _scoped_env_defaults(defaults: dict[str, str]) -> Iterator[None]:
    """Apply *defaults* with ``setdefault`` semantics, then put back what was there.

    ``setdefault`` so an operator's explicit value still wins, and a restore
    because fabric-dw can be embedded as a library: a host that later builds its
    own Azure Monitor exporter must get its own defaults, not this package's.
    Without the restore that leak is silent and one-directional, and the
    provider-ownership check in :mod:`fabric_dw.telemetry_spans` does not cover
    it, because these are written long before that check runs.

    Only for variables measured to be read while the exporters are being built.
    One read later, at export time say, must be left set for the exporter's
    lifetime instead: restoring it would quietly re-arm the channel inside this
    very process.

    Args:
        defaults: Variable name to the value this package wants as the default.
    """
    previous = {key: os.environ.get(key) for key in defaults}
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


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
    - ``OTEL_TRACES_EXPORTER=none`` / ``OTEL_METRICS_EXPORTER=none``, hard-set
      around the ``configure_azure_monitor`` call and restored afterwards, are
      what actually keep the library from building trace and metric pipelines
      of its own.  The matching ``disable_tracing`` / ``disable_metrics`` kwargs
      do NOT work: the library overwrites caller-supplied values with its own
      defaults (see the long comment at the call site).  Verified by inspecting
      the global providers immediately after the call, which are the no-op
      ``ProxyTracerProvider`` and ``_ProxyMeterProvider``, not SDK providers with
      Azure exporters.  The meter provider stays that way; the tracer provider is
      replaced below, on the MCP surface, by one this package builds itself.

      Metrics stay off entirely.  Traces do not: MCP Python SDK v2 installs an
      OpenTelemetry middleware on every server unconditionally, emitting one
      SERVER span per inbound protocol message, and #1049 decided to collect
      those.  What the library would have installed is an unfiltered exporter,
      and parts of every such span are chosen by the client: the span name, the
      status description, ``gen_ai.prompt.name`` / ``gen_ai.tool.name``, an
      exception event carrying message and stacktrace, ``mcp.method.name`` for
      a method the server does not implement, and ``jsonrpc.request.id``.  So
      the span pipeline is built separately by
      :func:`fabric_dw.telemetry_spans.install_mcp_span_pipeline`, which wraps
      the Azure exporter in a sanitiser that drops non-MCP spans and rebuilds
      the rest from an allowlist.  It is installed only on the MCP surface,
      since the CLI emits no protocol spans.
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
    global _otel_logger, _tracer, _sdk_initialised, _span_pipeline_installed  # noqa: PLW0603

    # Fast path (no lock): already initialised by a previous call.
    if _sdk_initialised:
        return _otel_logger

    # Slow path: acquire the init lock and re-check under it (double-checked
    # locking).  A concurrent first call blocks here until the winner finishes;
    # the re-check then short-circuits so init runs at most once.
    with _sdk_init_lock:
        if _sdk_initialised:
            return _otel_logger

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

            # Customer sdkstats: a SECOND, separately-gated telemetry-about-telemetry
            # channel.  Note the different variable name; the statsbeat one above does
            # NOT gate this.
            #
            # AzureMonitorLogExporter.__init__ calls collect_customer_sdkstats()
            # unconditionally, which stands up a singleton manager holding its own
            # AzureMonitorMetricExporter on OUR connection string, a private
            # MeterProvider, and a PeriodicExportingMetricReader on a 15-minute
            # interval.  It exports item.success.count / item.drop.count /
            # item.retry.count with language, exporter version and compute type.
            # Measured without this line, and even with OTEL_METRICS_EXPORTER=none,
            # the manager reports enabled and initialised, holds a live
            # PeriodicExportingMetricReader, and the process carries an
            # "AzureMonitorMetricExporter Storage" thread alongside the reader's own.
            # That is a metrics pipeline, which contradicts docs/telemetry.md.  A CLI
            # run shorter than the 15-minute interval never reaches an export cycle,
            # but a long-lived MCP server does, and that is this project's main mode.
            #
            # setdefault, not a hard set: the gate is `disabled.lower() != "true"`, so
            # an operator setting it to "false" genuinely re-enables collection.  That
            # makes it a real override rather than a discarded value (unlike the
            # OTEL_*_EXPORTER pair below), and the failure direction is benign: more
            # Microsoft-internal telemetry, opted into deliberately.  It is applied,
            # and restored, by the _SCOPED_SIDE_CHANNELS_OFF block below.
            #
            # The statsbeat switch above is the one exception to that restore, and
            # deliberately so.  is_statsbeat_enabled() re-reads the environment on
            # every call, and BaseExporter._should_collect_stats() calls it on every
            # export, so putting the variable back would flip statsbeat bookkeeping
            # on inside this process for the rest of its life.  Measured with the
            # variable removed after init: is_statsbeat_enabled() False -> True and
            # _should_collect_stats() False -> True on the live log exporter.  No
            # statsbeat manager is ever initialised so nothing transmits, but the
            # guarantee here is that the switch is off, not that the blast radius is
            # small.  So it stays set for the exporter's lifetime and an embedding
            # host inherits it.  Pre-existing; unchanged by #1052.

            # Everything from here through the span-pipeline install runs with the
            # restorable side channels switched off.  The window deliberately extends
            # PAST configure_azure_monitor: install_mcp_span_pipeline builds a second
            # AzureMonitorTraceExporter afterwards, and that constructor reaches the
            # very same control-plane and resource-detector code.
            with _scoped_env_defaults(_SCOPED_SIDE_CHANNELS_OFF):
                # Build the OTel Resource that populates native Part A fields and prevents
                # hostname fallback for cloud_RoleInstance / ai.device.id (#477).
                resource = _build_otel_resource(_current_surface)

                configure_kwargs: dict[str, object] = {
                    "connection_string": _DEFAULT_CONNECTION_STRING,
                    "logger_name": "fabric_dw.telemetry",
                    # disable_logging=False (default) is intentional: the log/event
                    # exporter must be active so customEvents land in the customEvents
                    # table.  Without the logs pipeline, log records carrying
                    # microsoft.custom_event.name are silently dropped and events never
                    # appear in the App Insights "Usage → Events" or "Usage → Users" blades.
                    "disable_logging": False,
                    # NOTE: these next two are statements of intent, NOT the mechanism.
                    # azure-monitor-opentelemetry clobbers both with its own defaults
                    # (see _OTEL_EXPORTERS_OFF below, which is what actually disables
                    # these pipelines).  They are kept so the intent is visible at the
                    # call site and so the arguments become effective for free if
                    # upstream ever stops overwriting caller kwargs.
                    "disable_metrics": True,
                    "disable_tracing": True,
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

                # PRIVACY, load-bearing.  These two env vars are the ONLY working way
                # to switch off the trace and metric pipelines, and they must be set
                # HARD, not with setdefault.
                #
                # Why the kwargs above do not do it: in azure-monitor-opentelemetry
                # 1.8.9 `_get_configurations()` copies the caller's kwargs into a dict
                # and THEN runs a `_default_*` pass in which `_default_disable_tracing`
                # / `_default_disable_metrics` assign unconditionally, clobbering
                # whatever the caller passed.  Contrast `_default_connection_string`,
                # which early-returns when the key is already present.  Those defaults
                # consult only these env vars, and only for an exact `== "none"` after
                # lower/strip.
                #
                # Why NOT setdefault: any pre-existing value that is not literally
                # "none" leaves the pipeline ON, and what then gets installed is the
                # AzureMonitorTraceExporter pointed at the maintainer's ingestion
                # endpoint, NOT the exporter the operator asked for.  Measured:
                #     (unset)                     -> ProxyTracerProvider, no exporters
                #     OTEL_TRACES_EXPORTER=otlp   -> AzureMonitorTraceExporter installed
                #     OTEL_TRACES_EXPORTER=""     -> AzureMonitorTraceExporter installed
                # So setdefault gives the operator no control (outside the separate
                # auto-instrumentation entry point, `== "none"` is the only thing this
                # distro ever reads these for) and one silent third-party export path.
                # A host process that already installed its own TracerProvider is
                # unaffected either way: OpenTelemetry refuses to override an existing
                # provider, so the user's provider wins.
                #
                # Scoped and restored, because fabric-dw can be embedded as a library
                # and must not mutate the host process environment beyond this call.
                # Mutating os.environ is process-global, but the window is safe: it sits
                # inside the double-checked _sdk_init_lock so it runs exactly once, and
                # both entry points (the CLI and the MCP server) reach it on the main
                # thread before any transport or worker concurrency starts.
                # OTEL_LOGS_EXPORTER is deliberately NOT touched: a user setting it to
                # "none" disables our own customEvents, which is the safe direction.
                _prev_otel = {k: os.environ.get(k) for k in _OTEL_EXPORTERS_OFF}
                os.environ.update(_OTEL_EXPORTERS_OFF)
                try:
                    configure_azure_monitor(**configure_kwargs)
                finally:
                    for _key, _old in _prev_otel.items():
                        if _old is None:
                            os.environ.pop(_key, None)
                        else:
                            os.environ[_key] = _old

                # MCP protocol spans (#1049).  Only on the MCP surface: the CLI
                # produces no protocol spans, so installing a trace pipeline there
                # would claim the process-wide TracerProvider and run a batch
                # exporter thread for a stream that is always empty.
                if _current_surface == "mcp":
                    from fabric_dw.telemetry_spans import (  # noqa: PLC0415
                        install_mcp_span_pipeline,
                    )

                    # Load-bearing, not bookkeeping: flush_telemetry and
                    # shutdown_telemetry must not touch a TracerProvider this
                    # package did not install.  When a host application already had
                    # one, shutting it down at our exit would kill the host's own
                    # exporter for the rest of its life.
                    _span_pipeline_installed = install_mcp_span_pipeline(
                        _DEFAULT_CONNECTION_STRING, resource
                    )

            # Obtain the OTel Logger via the global LoggerProvider set up by
            # configure_azure_monitor.  This logger is used in emit_event to fire
            # customEvents as log records (not spans).
            #
            # Assign _otel_logger and _tracer BEFORE flipping _sdk_initialised so
            # that a concurrent emit which observes _sdk_initialised=True always
            # finds a valid logger (never None due to a lost race on the flag).
            _otel_logger = get_logger("fabric_dw.telemetry")
            _tracer = _otel_logger  # alias: existing tests check _tracer is not None
        except Exception:
            _log.debug("Failed to initialise Azure Monitor OpenTelemetry SDK", exc_info=True)
            _otel_logger = None
            _tracer = None
        finally:
            # Always flip the flag last, whether init succeeded or failed, so
            # it is never True while _otel_logger is in an intermediate state.
            _sdk_initialised = True

    return _otel_logger


def flush_telemetry(timeout_ms: int = 2000) -> None:
    """Flush pending telemetry events with a bounded timeout.

    Runs in a daemon thread so it can never block process exit even if the
    exporter is slow or unreachable.  The thread is daemon so the OS kills it
    when the main thread exits (no hang possible).

    Both the tracer provider (the MCP protocol spans, on the MCP surface) and the
    logger provider (customEvents log pipeline) are flushed so no records are lost.

    Args:
        timeout_ms: Maximum milliseconds to wait for the flush.  Defaults to
            2000 (2 s) to satisfy the B2 hang requirement.
    """
    if not _sdk_initialised or _otel_logger is None:
        return

    def _do_flush() -> None:
        # Each pipeline is flushed independently so a failure in one does
        # not prevent the other from running.

        # Tracer provider — carries the MCP protocol spans, and only ours.
        # _span_pipeline_installed is False on the CLI surface and whenever a
        # host application's provider won, and the global provider is then not
        # this package's to flush.
        if _span_pipeline_installed:
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

    Calls ``force_flush`` then ``shutdown()`` on the logger provider, and on the
    tracer provider when this package installed it.  The logger provider path is
    critical: it must export all pending customEvents (``command_invoked``,
    ``app_exited``) that were enqueued just before shutdown is called.

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

    Calling ``force_flush`` **before** ``shutdown()`` ensures all queued records
    are exported with a generous bound.  The remaining 2 s is
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
            round-trip) + ~2 s for provider cleanup.  With a span pipeline
            installed the flush half is split between the two pipelines, 3 s
            each; without one the logs pipeline keeps all 6 s.
    """
    global _sdk_shutdown  # noqa: PLW0603

    if not _sdk_initialised or _otel_logger is None:
        return
    if _sdk_shutdown:
        return
    _sdk_shutdown = True

    # Reserve ~2 s for the provider.shutdown() cleanup calls; the rest goes to
    # force_flush.  At the default timeout_ms=8000 that is 6 s, and it is split
    # in two ONLY when a span pipeline exists to spend half of it: the logs
    # branch runs first, so without a split a slow network there could eat the
    # whole budget and leave the last batch of spans unflushed.
    #
    # The condition is load-bearing, not tidiness.  The logs pipeline is the
    # critical one — it carries every command_invoked and app_exited — and the
    # App Insights POST typically takes 2 to 4 s.  Splitting unconditionally
    # would halve that budget on the CLI, which has no span pipeline to protect
    # and is the surface most exposed to a short-lived process.
    #
    # If timeout_ms were set below 4000 the floor kicks in, which still fits
    # inside the daemon-thread join cap (timeout_ms/1000 + 0.5 s) because the
    # thread is killed when the main thread exits — the cap is a worst-case
    # wall-clock bound, not a guarantee.
    flush_budget_ms = max(timeout_ms - 2000, 2000)
    flush_timeout_ms = (
        max(flush_budget_ms // 2, 1000) if _span_pipeline_installed else flush_budget_ms
    )

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

        # Tracer provider — the MCP protocol spans, and only ours.  Guarded on
        # _span_pipeline_installed: when a host application's provider won the
        # race, shutting it down here would permanently kill the exporter of the
        # process that embedded us.  The provider is created with
        # shutdown_on_exit=False, so this call is the only thing that tears the
        # trace pipeline down.
        #
        # force_flush first, for the same reason as the logs branch: the last
        # batch of spans is otherwise at the mercy of the worker's schedule.
        if _span_pipeline_installed:
            with contextlib.suppress(Exception):
                from opentelemetry import trace as _trace  # noqa: PLC0415

                provider = _trace.get_tracer_provider()
                span_force_flush = getattr(provider, "force_flush", None)
                if callable(span_force_flush):
                    span_force_flush(timeout_millis=flush_timeout_ms)
                shutdown_fn = getattr(provider, "shutdown", None)
                if callable(shutdown_fn):
                    shutdown_fn()

    t = threading.Thread(target=_do_shutdown, daemon=True)
    t.start()
    t.join(timeout=timeout_ms / 1000 + 0.5)  # extra 0.5 s wall-clock buffer


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def emit_event(
    name: str,
    attributes: dict[str, object],
    *,
    omit_keys: set[str] | None = None,
) -> None:
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

    Args:
        name: The event name (e.g. ``"app_started"``).
        attributes: Extra per-event dimensions merged on top of the shared
            envelope.  Caller-supplied keys win over envelope keys of the
            same name.
        omit_keys: Optional set of envelope keys to drop from the merged
            record before emission.  Use this for lifecycle-start events that
            fire before auth is resolved so that a potentially-wrong
            ``auth_mode`` value is never emitted (e.g. ``omit_keys={"auth_mode"}``).
            Only envelope keys (those present in the ``merged`` dict at the
            time of the pop) can be suppressed.  The three special App Insights
            keys written *after* the pop — ``microsoft.custom_event.name``,
            ``enduser.pseudo.id``, and ``ai.operation.name`` — are unaffected.
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

        # Drop any keys the caller asked to exclude.  This is used by
        # lifecycle-start events (app_started, mcp_server_started) that fire
        # before auth is resolved — omitting auth_mode avoids emitting a
        # possibly-wrong value derived from _detect_auth_mode()'s env heuristic.
        if omit_keys is not None:
            for key in omit_keys:
                merged.pop(key, None)

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
            attributes=merged,
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

    ``auth_mode`` is intentionally omitted from this event: it fires at process
    start before any token is acquired, so ``_auth_mode_override`` is still
    ``None`` and the env-heuristic fallback (``_detect_auth_mode()``) can
    mis-classify the session (e.g. ``interactive`` for a plain ``az login``).
    The accurate value is emitted on ``command_invoked`` and ``app_exited``
    after the auth layer calls :func:`set_auth_mode`.

    Args:
        surface: Either ``"cli"`` or ``"mcp"``.
    """
    global _current_surface  # noqa: PLW0603
    _current_surface = surface
    # ``surface`` is no longer sent as a custom dimension: it is shipped natively
    # as ``cloud_RoleName`` via the OTel Resource (``service.name`` = surface).
    emit_event("app_started", {}, omit_keys={"auth_mode"})


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
    """Emit an ``mcp_server_started`` lifecycle event.

    ``auth_mode`` is omitted for the same reason as :func:`record_app_started`:
    the MCP server boots before any token is acquired and the env-heuristic can
    mis-classify the session.  The accurate value is emitted on subsequent
    ``command_invoked`` events after the auth layer calls :func:`set_auth_mode`.
    """
    emit_event("mcp_server_started", {}, omit_keys={"auth_mode"})


# ---------------------------------------------------------------------------
# Connecting MCP client (#1048)
# ---------------------------------------------------------------------------

#: Value recorded when a client sends no ``clientInfo``, which the protocol
#: permits, or sends one this code cannot read.
MCP_CLIENT_UNKNOWN = "unknown"

#: Value recorded once this process has seen more distinct client names than
#: :data:`_MCP_CLIENT_LIMIT`.
MCP_CLIENT_OTHER = "other"

#: Longest client name recorded.
_MCP_CLIENT_MAX_LEN = 64

#: How many distinct client names one process records before collapsing the
#: rest into :data:`MCP_CLIENT_OTHER`.
#:
#: The cap is not about hostile clients alone.  On protocol revision 2026-07-28
#: the name rides every request's ``_meta`` rather than a handshake, so it can
#: differ per message on one connection, and a client that appends a build hash
#: or a session id to its name (an ordinary bug) would otherwise emit one
#: ``mcp_client_connected`` per request against the daily ingestion cap and grow
#: the seen-set without bound for the life of the process.  Eight keeps the
#: long-tail signal the field exists for while making it useless as a channel.
_MCP_CLIENT_LIMIT = 8

# The connecting client for the request being handled.  A ContextVar rather
# than a module global because one long-lived MCP server over streamable HTTP
# serves several connections concurrently, each with its own client; a global
# would report whichever connected last.
_mcp_client_var: ContextVar[str | None] = ContextVar("fabric_dw_mcp_client", default=None)

# Clients this process has already announced, so mcp_client_connected fires
# once per distinct client rather than once per inbound message.  Bounded by
# _MCP_CLIENT_LIMIT (+1 for the "other" bucket itself).
_seen_mcp_clients: set[str] = set()


def normalise_mcp_client(raw_name: object) -> str:
    """Return a bounded client name for *raw_name*.

    Recorded verbatim, bar the filtering below, rather than mapped onto a fixed
    list of known clients: the question this field answers is which clients are
    out there, and a fixed list can only ever confirm the ones already guessed,
    turning every new or renamed client into ``other`` until somebody notices.

    Verbatim still means vetted.  Non-printable characters are removed before
    the length cap, because ``strip()`` leaves embedded newlines, tabs and NULs
    in the middle of a name and those went into a custom dimension as-is.

    Args:
        raw_name: The ``clientInfo.name`` value, or anything at all.

    Returns:
        The filtered, trimmed, truncated name, or :data:`MCP_CLIENT_UNKNOWN`
        when *raw_name* is not a usable string.
    """
    if not isinstance(raw_name, str):
        return MCP_CLIENT_UNKNOWN
    # str.isprintable() is False for control characters, newline, tab and NUL,
    # and True for ordinary space and for the letters of any script.
    name = "".join(char for char in raw_name if char.isprintable()).strip()
    if not name:
        return MCP_CLIENT_UNKNOWN
    return name[:_MCP_CLIENT_MAX_LEN]


def _bounded_mcp_client(name: str) -> str:
    """Return *name*, or :data:`MCP_CLIENT_OTHER` once the per-process cap is full."""
    if name in _seen_mcp_clients:
        return name
    if len(_seen_mcp_clients) >= _MCP_CLIENT_LIMIT:
        return MCP_CLIENT_OTHER
    return name


def current_mcp_client() -> str | None:
    """Return the MCP client handling the current request, or ``None``.

    ``None`` on the CLI surface, and on any MCP code path reached outside a
    request (nothing sets the context variable there).
    """
    return _mcp_client_var.get()


@contextlib.contextmanager
def mcp_client_scope(raw_name: object) -> Iterator[None]:
    """Record *raw_name* as the connecting client for the enclosing block.

    Emits ``mcp_client_connected`` the first time this process sees a given
    client, and makes the name readable via :func:`current_mcp_client` for the
    duration of the block so ``command_invoked`` can carry it.  Both use the
    bounded name, so the two events always agree.

    Fire-and-forget: nothing in here raises.

    Args:
        raw_name: The ``clientInfo.name`` the client sent, in whatever shape it
            arrived.
    """
    name = _bounded_mcp_client(normalise_mcp_client(raw_name))
    token = _mcp_client_var.set(name)
    try:
        if name not in _seen_mcp_clients:
            _seen_mcp_clients.add(name)
            # auth_mode is omitted for the same reason as the other lifecycle
            # events: the first client connects before any token is acquired,
            # so only the env heuristic would be available here.
            emit_event("mcp_client_connected", {"mcp_client": name}, omit_keys={"auth_mode"})
        yield
    finally:
        with contextlib.suppress(ValueError):
            _mcp_client_var.reset(token)


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
