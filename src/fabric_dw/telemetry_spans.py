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

import logging
from collections.abc import Mapping
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from azure.monitor.opentelemetry.exporter import (
    AzureMonitorTraceExporter as _AzureMonitorTraceExporter,
)
from opentelemetry import trace as _trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
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

#: Attributes copied through untouched.  Both are server-side classifications:
#: a JSON-RPC error code, the string ``tool_error``, or the qualified name of
#: an exception class raised inside this process.  None can carry client input.
_PASSTHROUGH_ATTRIBUTES = frozenset({"error.type", "rpc.response.status_code"})


@lru_cache(maxsize=1)
def _known_methods() -> frozenset[str]:
    """Return every method name the MCP protocol defines, in both directions.

    Read from ``mcp_types``, which is documented as supported public API and
    is updated upstream when the protocol gains a method.  Returns an empty set
    if the import fails, which degrades to every method being reported as
    :data:`UNKNOWN_METHOD` rather than to leaking an unvetted string.
    """
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
    return frozenset(SPEC_CLIENT_METHODS | SPEC_CLIENT_NOTIFICATION_METHODS | server_side)


@lru_cache(maxsize=1)
def _known_protocol_versions() -> frozenset[str]:
    """Return the protocol revisions the SDK knows, or an empty set on failure."""
    try:
        from mcp_types.version import KNOWN_PROTOCOL_VERSIONS  # noqa: PLC0415
    except Exception:
        _log.debug("Could not read the MCP protocol versions", exc_info=True)
        return frozenset()
    return frozenset(KNOWN_PROTOCOL_VERSIONS)


def _safe_method(value: object) -> str:
    """Return *value* when it is a protocol method name, else :data:`UNKNOWN_METHOD`."""
    if isinstance(value, str) and value in _known_methods():
        return value
    return UNKNOWN_METHOD


def _safe_request_id(value: object) -> str | None:
    """Return *value* when it is an integer JSON-RPC id, else ``None``.

    JSON-RPC allows a string id, and the client picks it, so a string id is
    free text and is dropped.  Every SDK client counts integers.

    The check is a digit test rather than ``int(value)``, which also accepts
    surrounding whitespace, digit separators (``1_000``) and non-ASCII numerals.
    """
    if not isinstance(value, str):
        return None
    digits = value.removeprefix("-")
    if digits.isascii() and digits.isdigit():
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

    for key in _PASSTHROUGH_ATTRIBUTES:
        value = attributes.get(key)
        if value is not None:
            out[key] = value

    return out


def sanitise_span(span: ReadableSpan) -> ReadableSpan | None:
    """Return a replacement span safe to export, or ``None`` to drop it.

    ``None`` is returned for any span that did not come from the MCP SDK's
    tracer.  A span from that tracer whose shape this code does not recognise
    is still exported, but reduced to :data:`UNKNOWN_METHOD` and a status code.

    Args:
        span: The ended span handed to the exporter.

    Returns:
        A new :class:`~opentelemetry.sdk.trace.ReadableSpan` carrying only
        vetted values, or ``None`` when the span must not be exported.
    """
    scope = span.instrumentation_scope
    if scope is None or scope.name != MCP_SDK_SCOPE:
        return None

    attributes = _sanitise_attributes(span.attributes)
    method = attributes["mcp.method.name"]

    # The status description is dropped, not rewritten: for `prompts/get` it is
    # the "Unknown prompt: <name>" message that echoes the client's string, and
    # every other description is equally free-form.  The status code survives,
    # and `error.type` carries the machine-readable classification.
    status = Status(span.status.status_code)

    return ReadableSpan(
        # The client-supplied target is a suffix on the SDK's span name, so the
        # name is rebuilt from the vetted method instead.  The span kind, which
        # is preserved, is what distinguishes an inbound message (SERVER, the
        # `requests` table) from a server-initiated one (CLIENT, `dependencies`).
        name=method,
        context=span.get_span_context(),
        parent=span.parent,
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

    A host application that already installed a ``TracerProvider`` keeps it.
    OpenTelemetry refuses to override an existing global provider, so this
    function sets one and then checks whether the set took effect, standing
    down when it did not.  The provider is built before the exporter so that
    losing the race costs nothing.

    Args:
        connection_string: The Application Insights connection string.
        resource: The OTel ``Resource`` shared with the logs pipeline, so spans
            carry the same ``cloud_RoleName`` / ``application_Version`` fields
            as the events.  ``None`` falls back to the SDK default resource.

    Returns:
        ``True`` when this process now exports MCP protocol spans, ``False``
        when a host provider won the race or setup failed.  Never raises.
    """
    try:
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
        _trace.set_tracer_provider(provider)
        if _trace.get_tracer_provider() is not provider:
            _log.debug("A TracerProvider was already installed; leaving it alone")
            return False

        exporter = _AzureMonitorTraceExporter(connection_string=connection_string)
        provider.add_span_processor(BatchSpanProcessor(SanitisingSpanExporter(exporter)))
    except Exception:
        _log.debug("Failed to install the MCP span pipeline", exc_info=True)
        return False
    return True
