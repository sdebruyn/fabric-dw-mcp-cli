"""Sanitised export path for the MCP SDK's OpenTelemetry protocol spans.

Why this module exists
----------------------
MCP Python SDK v2 installs an ``OpenTelemetryMiddleware`` on every server it
creates, which emits one span per inbound protocol message.  That is free,
upstream-maintained instrumentation covering every message type, including the
ones this project's hand-written ``command_invoked`` event does not know about,
so it is worth collecting (#1049).

It cannot be collected as-is.  Parts of every span are chosen by the client and
are free text, and this project promises in ``docs/telemetry.md`` that no
identifiers and no free-text payload are ever sent.  Measured against a server
that registers no prompts at all, a single ``prompts/get`` puts a
client-supplied string into four places:

- the span name (``prompts/get <name>``),
- the status description (``Unknown prompt: <name>``),
- the ``gen_ai.prompt.name`` attribute,
- an ``exception`` span event, whose ``exception.message`` and
  ``exception.stacktrace`` both contain it.

Two more values are client-chosen even though they look structural:
``mcp.method.name`` is echoed verbatim for a method the server does not
implement (the middleware wraps the ``METHOD_NOT_FOUND`` path), and
``jsonrpc.request.id`` is whatever string the client put in the JSON-RPC
envelope.

Design
------
Rather than blocklisting the known-bad fields, every exported span is rebuilt
from an allowlist, so a field added by a future SDK release is dropped until
someone deliberately admits it:

- Spans whose instrumentation scope is not the MCP SDK's are dropped
  entirely.  ``configure_azure_monitor`` installs a process-wide provider, so
  this is what keeps a third-party library's spans (HTTP URLs carrying
  workspace and warehouse identifiers, say) out of the export path without
  depending on the auto-instrumentation switches staying off.
- ``mcp.method.name`` is kept only when it is a method the MCP protocol
  defines, using the SDK's own maps so new protocol methods are covered
  without a change here.  Anything else becomes ``<unknown>``.
- The span name is rebuilt from that sanitised method name, which drops the
  client-supplied suffix.
- ``mcp.protocol.version`` is kept only when it is a protocol revision the SDK
  knows.
- ``jsonrpc.request.id`` is kept only when it is an integer id.
- The status *code* is kept; the status *description* is dropped.
- Events and links are dropped, which is what removes the exception message
  and stacktrace.

Spans are read-only once ended, so the sanitiser builds a replacement
``ReadableSpan`` instead of editing in place.

The provider installed here loses to a host application that already installed
its own ``TracerProvider``: OpenTelemetry refuses to override an existing
global provider, and :func:`install_mcp_span_pipeline` detects that and stands
down, leaving the host's spans going to the host's own destination.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import os
import secrets
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from azure.monitor.opentelemetry.exporter import (
    AzureMonitorTraceExporter as _AzureMonitorTraceExporter,
)
from opentelemetry import trace as _trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import (
    DEFAULT_TRACE_STATE,
    SpanContext,
    TraceFlags,
)
from opentelemetry.trace import ProxyTracerProvider as _ProxyTracerProvider
from opentelemetry.trace.status import Status

if TYPE_CHECKING:
    from collections.abc import Sequence

# This module is imported lazily (from telemetry._get_tracer, on the MCP
# surface only), so the imports above cost nothing on a run with telemetry
# disabled, and nothing at all on the CLI surface.  By the time it is imported,
# configure_azure_monitor has already pulled in the Azure exporter package.
#
# ``_trace`` and ``_AzureMonitorTraceExporter`` are module-level handles rather
# than imports inside install_mcp_span_pipeline so that there is exactly one
# object to substitute when testing the install path, which must never run for
# real: it claims a process-wide global and opens a connection to Application
# Insights.

__all__ = [
    "MCP_SDK_SCOPE",
    "UNKNOWN_METHOD",
    "install_mcp_span_pipeline",
    "sanitise_span",
]

_log = logging.getLogger(__name__)

#: Instrumentation scope name used by ``mcp.shared._otel``.  Spans from any
#: other scope are dropped rather than exported.
MCP_SDK_SCOPE = "mcp-python-sdk"

#: Placeholder substituted for a method name the MCP protocol does not define
#: (which means the client chose the string).  Cannot collide with a real
#: method name.
UNKNOWN_METHOD = "<unknown>"

#: The only value ``OpenTelemetryMiddleware`` ever writes for this attribute.
#: Passing through the literal rather than the value keeps it bounded if the
#: SDK ever starts deriving it from the request.
_GEN_AI_OPERATION = "execute_tool"

#: The one non-numeric ``error.type`` the middleware writes itself.
_TOOL_ERROR = "tool_error"

#: Longest ``jsonrpc.request.id`` recorded, in digits: the int64 range.  The id
#: is client-chosen, and the attributes handed to the exporter are a plain dict,
#: so no OpenTelemetry attribute limit applies and nothing else bounds this.  A
#: 4000-digit id measured at 4824 bytes on the wire before this cap existed.
_MAX_REQUEST_ID_DIGITS = 19

#: Longest ``error.type`` recorded.  Server-side exception class names are far
#: shorter; the cap exists so the invariant does not rest on that.
_MAX_ERROR_TYPE_LEN = 64

#: Longest ``rpc.response.status_code`` recorded, in digits: the int32 range.
#: A JSON-RPC error code is a small integer (the spec reserves -32768..-32000
#: and implementations add their own nearby), but ``ErrorData.code`` is a plain
#: Python ``int``, so nothing upstream bounds it.  This used to borrow
#: :data:`_MAX_ERROR_TYPE_LEN`, which allowed a 64-digit code: three times
#: looser than the 19 digits :data:`_MAX_REQUEST_ID_DIGITS` allows for a request
#: id, under exactly the same argument that an unbounded numeric field is a
#: channel (#1052).
_MAX_STATUS_CODE_DIGITS = 10

#: The one qualname part that is not a Python identifier.  ``type(e).__qualname__``
#: for an exception class defined inside a function is ``outer.<locals>.Inner``,
#: which the identifier test alone would reject (#1052).
_QUALNAME_LOCALS = "<locals>"

# Per-process key for remapping trace ids.  The MCP SDK parents every inbound
# span on trace context lifted off the wire, so the trace id and the parent span
# id are chosen by the client, and Azure exports them as `ai.operation.id` and
# `ai.operation.parentId`.  Measured before this existed: a `traceparent` of
# `00-74656e616e742d7365637265742d3031-3862797465735858-01` put the bytes
# `tenant-secret-01` into `ai.operation.id`, on any message including a
# notification, which needs no response.
#
# An HMAC keyed on a value the client cannot see keeps spans that genuinely
# belong to one trace grouped, while making the exported id something the client
# neither chooses nor can predict.  It is not stored and dies with the process.
_TRACE_ID_SECRET = secrets.token_bytes(32)


def _remap_trace_id(trace_id: int) -> int:
    """Return a trace id derived from *trace_id* that the client cannot choose."""
    digest = hmac.new(
        _TRACE_ID_SECRET, trace_id.to_bytes(16, "big", signed=False), hashlib.sha256
    ).digest()
    # A trace id of 0 is invalid per the spec; the odds are negligible but the
    # fallback costs one comparison.
    return int.from_bytes(digest[:16], "big") or 1


# Caches for the two lookups below.  Deliberately not ``lru_cache``: the first
# call happens on the BatchSpanProcessor worker thread, and caching a failure
# there would pin every span in the process to UNKNOWN_METHOD until restart,
# announced only at debug level.  Only a successful read is remembered.
_methods_cache: frozenset[str] | None = None
_versions_cache: frozenset[str] | None = None


def _known_methods() -> frozenset[str]:
    """Return every method name the MCP protocol defines, in both directions.

    Read from ``mcp_types``, which is documented as supported public API and
    is updated upstream when the protocol gains a method.  Returns an empty set
    if the import fails, which degrades to every method being reported as
    :data:`UNKNOWN_METHOD` rather than to leaking an unvetted string, and
    retries on the next span rather than caching the failure.
    """
    global _methods_cache  # noqa: PLW0603

    if _methods_cache is not None:
        return _methods_cache
    try:
        from mcp_types.methods import (  # noqa: PLC0415
            SERVER_NOTIFICATIONS,
            SERVER_REQUESTS,
            SPEC_CLIENT_METHODS,
            SPEC_CLIENT_NOTIFICATION_METHODS,
        )
    except Exception:
        _log.debug("Could not read the MCP method maps", exc_info=True)
        return frozenset()

    server_side = {method for method, _version in SERVER_REQUESTS}
    server_side |= {method for method, _version in SERVER_NOTIFICATIONS}
    _methods_cache = frozenset(SPEC_CLIENT_METHODS | SPEC_CLIENT_NOTIFICATION_METHODS | server_side)
    return _methods_cache


def _known_protocol_versions() -> frozenset[str]:
    """Return the protocol revisions the SDK knows, or an empty set on failure.

    Same no-caching-of-failures rule as :func:`_known_methods`.
    """
    global _versions_cache  # noqa: PLW0603

    if _versions_cache is not None:
        return _versions_cache
    try:
        from mcp_types.version import KNOWN_PROTOCOL_VERSIONS  # noqa: PLC0415
    except Exception:
        _log.debug("Could not read the MCP protocol versions", exc_info=True)
        return frozenset()
    _versions_cache = frozenset(KNOWN_PROTOCOL_VERSIONS)
    return _versions_cache


def _safe_method(value: object) -> str:
    """Return *value* when it is a protocol method name, else :data:`UNKNOWN_METHOD`."""
    if isinstance(value, str) and value in _known_methods():
        return value
    return UNKNOWN_METHOD


def _safe_request_id(value: object) -> str | None:
    """Return *value* when it is a small integer JSON-RPC id, else ``None``.

    JSON-RPC allows a string id, and the client picks it, so a string id is
    free text and is dropped.  The digit test is used rather than ``int(value)``,
    which also accepts surrounding whitespace, digit separators (``1_000``) and
    non-ASCII numerals.

    Length is capped at the int64 range.  Digits alone are still a channel: a
    4000-digit id is 4 KB of client-chosen base-10 data, and leading zeros
    encode too.  Every SDK client counts small integers, so the cap costs
    nothing real.
    """
    if not isinstance(value, str):
        return None
    digits = value.removeprefix("-")
    if not digits.isascii() or not digits.isdigit():
        return None
    if len(digits) > _MAX_REQUEST_ID_DIGITS:
        return None
    return value


def _safe_status_code(value: object) -> str | None:
    """Return *value* when it is a JSON-RPC error code, else ``None``.

    Bounded at :data:`_MAX_STATUS_CODE_DIGITS`, which is what a JSON-RPC code
    can plausibly be, rather than at the ``error.type`` length: digits alone are
    still a channel, for the same reason they are on ``jsonrpc.request.id``.
    """
    if not isinstance(value, str):
        return None
    digits = value.removeprefix("-")
    if digits.isascii() and digits.isdigit() and len(digits) <= _MAX_STATUS_CODE_DIGITS:
        return value
    return None


def _is_qualname(value: str) -> bool:
    """Return whether *value* has the shape of a Python qualified name.

    ``<locals>`` is admitted as a whole part because it is what CPython puts in
    the qualname of a class defined inside a function, and an exception class
    defined in a function is ordinary code.

    Non-ASCII is admitted too.  ``str.isidentifier`` is the actual language rule
    and it is Unicode-aware, so ``isascii()`` on top of it rejected class names
    written in any other script while adding nothing: the shape test is what
    bounds the value, and the length cap bounds it in characters either way.
    """
    return all(part == _QUALNAME_LOCALS or part.isidentifier() for part in value.split("."))


def _safe_error_type(value: object) -> str | None:
    """Return *value* when it is a server-side error classification, else ``None``.

    The middleware writes one of three things here: a JSON-RPC error code, the
    literal ``tool_error``, or the qualified name of an exception class raised
    inside this process.  None of those can carry client input **today**, but
    that is a property of the current SDK rather than of this code, and the
    server-to-client direction (``sampling/createMessage`` and friends) would
    put a peer's error on a span.  So the invariant is checked rather than
    assumed: a numeric code, the known literal, or a Python qualified name.
    """
    if not isinstance(value, str) or not value or len(value) > _MAX_ERROR_TYPE_LEN:
        return None
    if value == _TOOL_ERROR or _safe_status_code(value) is not None:
        return value
    if _is_qualname(value):
        return value
    return None


def _sanitise_attributes(attributes: object) -> dict[str, Any]:
    """Return the allowlisted subset of *attributes* with each value vetted.

    A span's attributes are a ``BoundedAttributes`` mapping, not a ``dict``, so
    the guard here is on ``Mapping``.
    """
    if not isinstance(attributes, Mapping):
        return {"mcp.method.name": UNKNOWN_METHOD}

    out: dict[str, Any] = {"mcp.method.name": _safe_method(attributes.get("mcp.method.name"))}

    version = attributes.get("mcp.protocol.version")
    if isinstance(version, str) and version in _known_protocol_versions():
        out["mcp.protocol.version"] = version

    request_id = _safe_request_id(attributes.get("jsonrpc.request.id"))
    if request_id is not None:
        out["jsonrpc.request.id"] = request_id

    if attributes.get("gen_ai.operation.name") == _GEN_AI_OPERATION:
        out["gen_ai.operation.name"] = _GEN_AI_OPERATION

    error_type = _safe_error_type(attributes.get("error.type"))
    if error_type is not None:
        out["error.type"] = error_type

    status_code = _safe_status_code(attributes.get("rpc.response.status_code"))
    if status_code is not None:
        out["rpc.response.status_code"] = status_code

    return out


def _safe_parent(parent: object, trace_id: int, safe_trace_id: int) -> SpanContext | None:
    """Return a parent context safe to export, or ``None`` to cut the link.

    Azure writes the parent span id straight into ``ai.operation.parentId``, and
    for an inbound message the parent is the W3C context the client put in the
    request's ``_meta``: a value the client chooses, on a span it can produce at
    will.  That link is cut.

    A **locally** minted parent is a different thing and is kept, remapped onto
    the same trace id its child now carries.  It is what makes a server-initiated
    ``SpanKind.CLIENT`` span nest under the inbound server span that caused it in
    the App Insights transaction view.  Nothing in this server sends requests to a
    client today, so this is dormant until an elicitation or sampling call is
    added, at which point the nesting works rather than needing to be discovered.

    Everything that is not provably local fails closed, because getting this
    wrong exports a client-chosen span id:

    - ``is_remote`` must be exactly ``False``.  The strict identity test is
      deliberate: a missing attribute, ``None``, or any other object is treated
      as remote.  ``is_remote`` is written by OpenTelemetry's propagator when it
      extracts context off the wire, never by the value the client sent, so it
      is the real discriminator, but this code should not be the thing that
      trusts it loosely.
    - The parent must be a genuine :class:`SpanContext` with a valid, non-zero
      trace id and span id.
    - The parent must sit in the same trace as its child.  A local parent always
      does, by construction; a mismatch means something built this span in a way
      this code does not understand, and the fail-safe answer is no link.

    Args:
        parent: ``ReadableSpan.parent``, in whatever shape it arrived.
        trace_id: The child's original (pre-remap) trace id.
        safe_trace_id: The remapped trace id the child will be exported with.

    Returns:
        A :class:`SpanContext` carrying the local parent's span id under the
        remapped trace id, or ``None``.
    """
    if not isinstance(parent, SpanContext):
        return None
    # `is not False` rather than `not ...`: only the actual boolean passes, so a
    # context type that reports something else here is treated as remote.
    if parent.is_remote is not False:
        return None
    if not parent.is_valid or parent.trace_id != trace_id:
        return None
    return SpanContext(
        trace_id=safe_trace_id,
        span_id=parent.span_id,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=DEFAULT_TRACE_STATE,
    )


def sanitise_span(span: ReadableSpan) -> ReadableSpan | None:
    """Return a replacement span safe to export, or ``None`` to drop it.

    ``None`` is returned for any span that did not come from the MCP SDK's
    tracer, and for one carrying no span context, which the exporter could not
    serialise anyway.  A span from that tracer whose shape this code does not
    recognise is still exported, but reduced to :data:`UNKNOWN_METHOD` and a
    status code.

    Args:
        span: The ended span handed to the exporter.

    Returns:
        A new :class:`~opentelemetry.sdk.trace.ReadableSpan` carrying only
        vetted values, or ``None`` when the span must not be exported.
    """
    scope = span.instrumentation_scope
    if scope is None or scope.name != MCP_SDK_SCOPE:
        return None

    context = span.get_span_context()
    if context is None:
        return None

    attributes = _sanitise_attributes(span.attributes)
    method = attributes["mcp.method.name"]

    # The status description is dropped, not rewritten: for `prompts/get` it is
    # the "Unknown prompt: <name>" message that echoes the client's string, and
    # every other description is equally free-form.  The status code survives,
    # and `error.type` carries the machine-readable classification.
    status = Status(span.status.status_code)

    # The span's identity is rebuilt, not copied.  The SDK parents each inbound
    # span on the W3C context in the request's `_meta`, so the trace id, the
    # parent span id and the tracestate all come off the wire, and Azure exports
    # the first two as `ai.operation.id` and `ai.operation.parentId`.  Only the
    # span id is minted locally, so only the span id is kept as-is; the trace id
    # is remapped so spans of one trace stay grouped without the client choosing
    # the value, and the tracestate is dropped rather than relying on the
    # exporter continuing not to serialise it.
    safe_trace_id = _remap_trace_id(context.trace_id)
    safe_context = SpanContext(
        trace_id=safe_trace_id,
        span_id=context.span_id,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=DEFAULT_TRACE_STATE,
    )

    return ReadableSpan(
        # The client-supplied target is a suffix on the SDK's span name, so the
        # name is rebuilt from the vetted method instead.  The span kind, which
        # is preserved, is what distinguishes an inbound message (SERVER, the
        # `requests` table) from a server-initiated one (CLIENT, `dependencies`).
        name=method,
        context=safe_context,
        parent=_safe_parent(span.parent, context.trace_id, safe_trace_id),
        resource=span.resource,
        attributes=attributes,
        # Dropped: the SDK's `record_exception` branch puts the exception
        # message and full stacktrace in an event, and a `prompts/get` for an
        # unregistered name reaches exactly that branch.
        events=(),
        links=(),
        kind=span.kind,
        instrumentation_scope=scope,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
    )


class SanitisingSpanExporter(SpanExporter):
    """Wrap a :class:`SpanExporter`, sanitising every span on the way out.

    Implemented as a wrapper rather than a ``SpanProcessor`` because a
    processor cannot stop the exporter that sits behind it from seeing the
    original span; only the exporter seam can drop and replace.
    """

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan], **kwargs: Any) -> SpanExportResult:  # noqa: ANN401
        """Export the sanitised form of *spans*, dropping the ones that fail vetting."""
        safe: list[ReadableSpan] = []
        for span in spans:
            try:
                sanitised = sanitise_span(span)
            except Exception:
                # A span this code cannot process is dropped, never forwarded:
                # failing open would export exactly the values this class exists
                # to remove.
                _log.debug("Dropping a span the sanitiser could not process", exc_info=True)
                continue
            if sanitised is not None:
                safe.append(sanitised)

        if not safe:
            return SpanExportResult.SUCCESS
        return self._inner.export(safe, **kwargs)

    def shutdown(self) -> None:
        """Shut down the wrapped exporter."""
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Flush the wrapped exporter."""
        return self._inner.force_flush(timeout_millis)


def install_mcp_span_pipeline(connection_string: str, resource: object | None) -> bool:
    """Install the global TracerProvider that exports sanitised MCP spans.

    Called once, from ``telemetry._get_tracer``, and only on the MCP surface:
    the CLI produces no protocol spans, so there is nothing there for this
    pipeline to carry and no reason for it to claim the process-wide provider.

    A host application that already installed a ``TracerProvider`` keeps it,
    and this function stands down without side effects.  Two checks, in this
    order, and both are needed:

    - Before building anything, the current global is inspected and this
      function returns early unless it is still the placeholder
      ``ProxyTracerProvider``.  Without it, standing down was not free.
      Measured in an embedded host: a real ``AzureMonitorTraceExporter`` was
      constructed and thrown away, the process-wide
      ``APPLICATIONINSIGHTS_OPENTELEMETRY_RESOURCE_METRIC_DISABLED`` was set on
      the way there, and the host got an ``Overriding of current TracerProvider
      is not allowed`` warning in its logs, all for a pipeline that never ran
      (#1052).
    - The claim itself still happens **last**, after the provider is fully
      built, and is verified afterwards.  That is what covers a host that
      claims the global between the check and the claim, and the ordering
      matters on its own: claiming first and then failing to build the exporter
      would leave an empty provider installed permanently, recording every span
      in the process into nothing and locking the host out for good, while
      still reporting failure to the caller.

    Args:
        connection_string: The Application Insights connection string.
        resource: The OTel ``Resource`` shared with the logs pipeline, so spans
            carry the same ``cloud_RoleName`` / ``application_Version`` fields
            as the events.  ``None`` falls back to the SDK default resource.

    Returns:
        ``True`` when this process now exports MCP protocol spans, ``False``
        when a host provider won the race or setup failed.  The caller records
        this: ``flush_telemetry`` and ``shutdown_telemetry`` must only tear down
        a provider this function installed.  Never raises.
    """
    provider = None
    try:
        # A host that configured OTEL_PYTHON_TRACER_PROVIDER rather than calling
        # set_tracer_provider() has not claimed the global yet; this read is what
        # makes OpenTelemetry load and install it, so the check below sees it.
        #
        # ProxyTracerProvider is the placeholder OpenTelemetry hands out while no
        # real provider has been set, so anything else means a host owns the
        # global and set_tracer_provider() would be refused.  Returning here is
        # what keeps standing down free of side effects: everything below builds
        # a live Azure exporter.
        if not isinstance(_trace.get_tracer_provider(), _ProxyTracerProvider):
            _log.debug("A TracerProvider was already installed; leaving it alone")
            return False

        # The Azure trace exporter appends an `_OTELRESOURCE_` MetricData
        # envelope to every non-empty export unless this is set, so switching
        # spans on would otherwise start sending metrics that docs/telemetry.md
        # says are never sent.  It is read at export time, not at construction,
        # so it cannot be scoped and restored the way the OTEL_*_EXPORTER pair
        # is.  setdefault, like APPLICATIONINSIGHTS_SDKSTATS_DISABLED: the gate
        # is `!= "true"`, so an operator's explicit value is a real override.
        #
        # One of four now: statsbeat, customer sdkstats, this, and the
        # OneSettings control plane (APPLICATIONINSIGHTS_CONTROLPLANE_DISABLED,
        # set in telemetry.py, #1053).  Assume any Azure Monitor exporter has a
        # side channel behind an environment variable until proven otherwise,
        # and expect a fifth: each of the four was found by measuring a running
        # process, none by reading the library's API surface.
        os.environ.setdefault("APPLICATIONINSIGHTS_OPENTELEMETRY_RESOURCE_METRIC_DISABLED", "true")

        exporter = _AzureMonitorTraceExporter(connection_string=connection_string)
        provider = TracerProvider(
            resource=resource,  # ty: ignore[invalid-argument-type]
            # ALWAYS_ON rather than the default ParentBased sampler: an MCP
            # client propagates W3C trace context into `_meta`, so the default
            # would hand our collection decision to the client's own sampler
            # and silently drop spans for any client that samples below 100%.
            sampler=ALWAYS_ON,
            # Same reason as the logs pipeline: the default atexit hook has an
            # unbounded flush that can hang the process.  shutdown_telemetry()
            # tears this provider down with a bounded timeout instead.
            shutdown_on_exit=False,
        )
        provider.add_span_processor(BatchSpanProcessor(SanitisingSpanExporter(exporter)))

        _trace.set_tracer_provider(provider)
        if _trace.get_tracer_provider() is not provider:
            _log.debug("A TracerProvider was already installed; leaving it alone")
            # Stop the batch worker and close the exporter we built but lost.
            provider.shutdown()
            return False
    except Exception:
        _log.debug("Failed to install the MCP span pipeline", exc_info=True)
        if provider is not None:
            with contextlib.suppress(Exception):
                provider.shutdown()
        return False
    return True
