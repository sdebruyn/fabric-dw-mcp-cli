"""Tests for fabric_dw.telemetry_spans — the sanitised MCP span export path.

The headline test drives a real MCP server over in-memory streams with
hand-written JSON-RPC, so the spans under test are produced by the SDK's own
``OpenTelemetryMiddleware`` rather than by a fixture's idea of what that
middleware writes.  That fidelity is the point: the first draft of the
sanitiser passed a hand-rolled test and dropped every real span on the floor,
because a span's attributes are a ``BoundedAttributes`` mapping and not a
``dict``.

None of these tests touch the global ``TracerProvider``.  The MCP SDK holds its
tracer in ``mcp.shared._otel._tracer``, so pointing that at a test-local
provider keeps the process global untouched and the tests independent of
execution order.
"""

from __future__ import annotations

import contextlib
import sys
from types import SimpleNamespace
from typing import Any

import mcp.shared._otel
import pytest
from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
from azure.monitor.opentelemetry.exporter.export._base import ExportResult
from azure.monitor.opentelemetry.exporter.statsbeat.customer._state import (
    get_customer_stats_manager,
)
from mcp.server.mcpserver import MCPServer
from mcp_types import JSONRPCRequest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, SpanKind, StatusCode, TraceState
from opentelemetry.trace.status import Status

from fabric_dw import telemetry_spans
from fabric_dw.telemetry_spans import (
    MCP_SDK_SCOPE,
    UNKNOWN_METHOD,
    SanitisingSpanExporter,
    install_mcp_span_pipeline,
    sanitise_span,
)
from tests.unit._mcp_session import HANDSHAKE_VERSION, exchange, handshake

# A value no protocol field may ever carry into Application Insights.  Every
# assertion below searches for this string rather than for a specific field, so
# a future SDK release that moves the client's input somewhere new still fails
# the test.
MARKER = "GEHEIM-wachtwoord-hunter2-klant-ACME"

# The same marker in the two places a client puts bytes of its own choosing into
# a span's *identity* rather than its payload: the trace id and the parent span
# id of the `traceparent` it sends, which Azure exports as `ai.operation.id` and
# `ai.operation.parentId`. A traceparent is fixed-width hex, so the marker is
# carried 16 and 8 bytes at a time.
MARKER_TRACE_BYTES = MARKER.encode()[:16]
MARKER_PARENT_BYTES = MARKER.encode()[16:24]
MARKER_TRACEPARENT = f"00-{MARKER_TRACE_BYTES.hex()}-{MARKER_PARENT_BYTES.hex()}-01"
MARKER_TRACESTATE = f"leak={MARKER_TRACE_BYTES.decode()}"

_CONNECTION_STRING = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://127.0.0.1:1/"
)


class _CapturingExporter:
    """Stands in for the Azure exporter and records what it was handed."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []
        self.shutdown_calls = 0
        self.flush_calls = 0

    def export(self, spans: Any, **_kwargs: Any) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # noqa: ARG002
        self.flush_calls += 1
        return True


def _install_test_tracer(monkeypatch: pytest.MonkeyPatch) -> _CapturingExporter:
    """Point the MCP SDK's tracer at a provider that exports through the sanitiser."""
    captured = _CapturingExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(SanitisingSpanExporter(captured)))  # ty: ignore[invalid-argument-type]
    monkeypatch.setattr(mcp.shared._otel, "_tracer", provider.get_tracer(MCP_SDK_SCOPE))
    return captured


def _blob(spans: list[ReadableSpan]) -> str:
    """Render every field of every span into one string, for marker searching.

    Identity is in here as well as payload, and that is not decoration: a
    client picks the trace id and the parent span id through the `traceparent`
    it sends, so a sweep over names and attributes alone would have called the
    first version of this sanitiser clean while it was exporting 24 bytes of
    the client's choosing per message.  Rendering the ids as hex is how a
    marker smuggled in as bytes shows up as text.
    """
    parts = []
    for span in spans:
        context = span.get_span_context()
        identity = ""
        if context is not None:
            identity = (
                f"{context.trace_id:032x} {context.span_id:016x} "
                f"{bytes.fromhex(f'{context.trace_id:032x}')!r} "
                f"{bytes.fromhex(f'{context.span_id:016x}')!r} "
                f"{dict(context.trace_state.items() if context.trace_state else {})}"
            )
        parent = ""
        if span.parent is not None:
            parent = (
                f"{span.parent.trace_id:032x} {span.parent.span_id:016x} "
                f"{bytes.fromhex(f'{span.parent.span_id:016x}')!r}"
            )
        parts.append(
            f"{span.name} {span.status.status_code} {span.status.description} "
            f"{dict(span.attributes or {})} "
            f"{[(e.name, dict(e.attributes or {})) for e in span.events]} "
            f"{list(span.links)} {identity} {parent}"
        )
    return "\n".join(parts)


async def _exchange(messages: list[Any]) -> list[dict[str, Any]]:
    """Run a bare MCP server, with one tool and no middleware, against *messages*."""
    server: MCPServer[Any] = MCPServer("span-test-server", version="0.0.0")

    @server.tool(name="echo")
    async def _echo(value: str) -> str:
        """Echo the value back."""
        return value

    return await exchange(server, messages)


def _handshake() -> list[Any]:
    return handshake("span-test-client")


# ---------------------------------------------------------------------------
# The whole point: nothing the client chose reaches the exporter
# ---------------------------------------------------------------------------


async def test_client_supplied_values_never_reach_the_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A marker sent through every client-controlled field must not be exported.

    Measured against a server that registers no prompts, ``prompts/get`` puts
    the client's string in four places at once: the span name, the status
    description, ``gen_ai.prompt.name``, and an ``exception`` event carrying
    both the message and the stacktrace.  ``tools/call`` echoes an unregistered
    tool name into the span name and ``gen_ai.tool.name``.  An unknown method
    lands in ``mcp.method.name`` verbatim, because the middleware wraps the
    METHOD_NOT_FOUND path.  A string JSON-RPC id is client-chosen too.

    Each of those is a request that the server *rejects*, which is exactly why
    registering no prompts and no unknown tools is not protection: the error
    text is what carries the value.

    The `traceparent` and `tracestate` carry the same marker through the span's
    identity rather than its payload. They are here rather than only in the
    dedicated tests below because this is the test that advertises itself as the
    guarantee: reverting the identity rebuild has to fail *this* one.
    """
    captured = _install_test_tracer(monkeypatch)

    responses = await _exchange(
        [
            *_handshake(),
            JSONRPCRequest(
                jsonrpc="2.0",
                id=2,
                method="prompts/get",
                params={
                    "name": MARKER,
                    "_meta": {
                        "traceparent": MARKER_TRACEPARENT,
                        "tracestate": MARKER_TRACESTATE,
                    },
                },
            ),
            JSONRPCRequest(
                jsonrpc="2.0",
                id=3,
                method="tools/call",
                params={"name": MARKER, "arguments": {}},
            ),
            JSONRPCRequest(jsonrpc="2.0", id=4, method=f"{MARKER}/probe", params={}),
            JSONRPCRequest(jsonrpc="2.0", id=MARKER, method="tools/list", params={}),
        ]
    )

    # The server really did see the marker: without this the test could pass by
    # never having sent anything.
    assert MARKER in str(responses), "the marker never reached the server"
    assert captured.spans, "no spans were exported at all"

    blob = _blob(captured.spans)
    assert MARKER not in blob
    # As sent, as hex, and as the bytes the hex decodes back to.
    assert MARKER_TRACEPARENT not in blob
    assert MARKER_TRACESTATE not in blob
    assert MARKER_TRACE_BYTES.hex() not in blob
    assert MARKER_PARENT_BYTES.hex() not in blob
    assert MARKER_TRACE_BYTES.decode() not in blob
    assert MARKER_PARENT_BYTES.decode() not in blob


async def test_the_useful_fields_survive_sanitising(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanitising must not reduce the spans to something worthless.

    The method name, the negotiated protocol revision, an integer request id,
    the operation marker and the error classification are all SDK-generated and
    all survive; timings and span kind come along untouched.
    """
    captured = _install_test_tracer(monkeypatch)

    await _exchange(
        [
            *_handshake(),
            JSONRPCRequest(
                jsonrpc="2.0",
                id=7,
                method="tools/call",
                params={"name": "echo", "arguments": {"value": "hello"}},
            ),
        ]
    )

    by_name = {span.name: span for span in captured.spans}
    assert set(by_name) >= {"initialize", "notifications/initialized", "tools/call"}

    call = by_name["tools/call"]
    assert dict(call.attributes or {}) == {
        "mcp.method.name": "tools/call",
        "mcp.protocol.version": HANDSHAKE_VERSION,
        "jsonrpc.request.id": "7",
        "gen_ai.operation.name": "execute_tool",
    }
    assert call.kind is SpanKind.SERVER
    assert call.start_time is not None
    assert call.end_time is not None
    assert call.end_time > call.start_time


async def test_a_failing_tool_call_keeps_its_error_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``error.type`` and the ERROR status code are the point of collecting these."""
    captured = _install_test_tracer(monkeypatch)

    await _exchange(
        [
            *_handshake(),
            JSONRPCRequest(
                jsonrpc="2.0", id=8, method="tools/call", params={"name": MARKER, "arguments": {}}
            ),
        ]
    )

    call = next(s for s in captured.spans if s.name == "tools/call")
    assert call.status.status_code is StatusCode.ERROR
    assert call.status.description is None, "the status description is dropped, not rewritten"
    assert (call.attributes or {}).get("error.type") == "tool_error"


async def test_an_unknown_method_is_reported_without_its_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A method the protocol does not define is counted, but not quoted.

    Somebody probing this server with made-up methods is worth seeing in the
    telemetry; the strings they probed with are not.
    """
    captured = _install_test_tracer(monkeypatch)

    await _exchange(
        [*_handshake(), JSONRPCRequest(jsonrpc="2.0", id=9, method=f"{MARKER}/probe", params={})]
    )

    span = next(s for s in captured.spans if s.name == UNKNOWN_METHOD)
    assert (span.attributes or {})["mcp.method.name"] == UNKNOWN_METHOD
    assert span.status.status_code is StatusCode.ERROR
    assert (span.attributes or {}).get("rpc.response.status_code") == "-32601"


async def test_a_string_request_id_is_dropped_and_an_integer_one_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON-RPC lets the client pick the id, so only an integer id is recorded."""
    captured = _install_test_tracer(monkeypatch)

    await _exchange(
        [
            *_handshake(),
            JSONRPCRequest(jsonrpc="2.0", id=MARKER, method="tools/list", params={}),
            JSONRPCRequest(jsonrpc="2.0", id=11, method="ping", params={}),
        ]
    )

    listing = next(s for s in captured.spans if s.name == "tools/list")
    assert "jsonrpc.request.id" not in (listing.attributes or {})

    ping = next(s for s in captured.spans if s.name == "ping")
    assert (ping.attributes or {})["jsonrpc.request.id"] == "11"


# ---------------------------------------------------------------------------
# sanitise_span on spans from other producers
# ---------------------------------------------------------------------------


def _make_span(
    scope: str,
    name: str,
    attributes: dict[str, Any] | None = None,
) -> ReadableSpan:
    """Produce one real ended span from *scope* and return it, unsanitised."""
    exporter = _CapturingExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))  # ty: ignore[invalid-argument-type]
    tracer = provider.get_tracer(scope)
    with tracer.start_as_current_span(name, attributes=attributes):
        pass
    return exporter.spans[0]


def test_a_span_from_another_library_is_dropped_entirely() -> None:
    """Only the MCP SDK's spans are collected; everything else is refused.

    ``configure_azure_monitor`` installs a process-wide provider, so without
    this rule any library that starts emitting spans, or an auto-instrumentor
    someone re-enables, would be exporting to this project's Application
    Insights.  An HTTP client span in particular carries request URLs, and this
    project's URLs carry workspace and warehouse identifiers.
    """
    span = _make_span(
        "opentelemetry.instrumentation.requests",
        "GET",
        {"url.full": f"https://api.fabric.microsoft.com/v1/workspaces/{MARKER}"},
    )
    assert sanitise_span(span) is None


def test_an_unrecognised_mcp_span_is_reduced_rather_than_quoted() -> None:
    """A shape this code does not know is exported with everything stripped.

    Keeping the span records that something happened; the allowlist means an
    attribute a future SDK adds is not exported until somebody vets it.
    """
    span = _make_span(MCP_SDK_SCOPE, "something/new", {"some.new.attribute": MARKER})
    sanitised = sanitise_span(span)

    assert sanitised is not None
    assert sanitised.name == UNKNOWN_METHOD
    assert dict(sanitised.attributes or {}) == {"mcp.method.name": UNKNOWN_METHOD}


def test_an_unknown_protocol_version_is_dropped() -> None:
    """The version is only kept when the SDK recognises it as a revision."""
    span = _make_span(
        MCP_SDK_SCOPE,
        "ping",
        {"mcp.method.name": "ping", "mcp.protocol.version": MARKER},
    )
    sanitised = sanitise_span(span)

    assert sanitised is not None
    assert "mcp.protocol.version" not in (sanitised.attributes or {})


@pytest.mark.parametrize("request_id", ["1_0", " 12 ", "\u0661\u0662", "12a", ""])
def test_only_a_plain_decimal_request_id_is_kept(request_id: str) -> None:
    """Anything ``int()`` would accept is not automatically safe to export.

    ``int()`` also swallows surrounding whitespace, digit separators and
    non-ASCII numerals, so the check is a digit test instead.
    """
    span = _make_span(
        MCP_SDK_SCOPE,
        "ping",
        {"mcp.method.name": "ping", "jsonrpc.request.id": request_id},
    )
    sanitised = sanitise_span(span)

    assert sanitised is not None
    assert "jsonrpc.request.id" not in (sanitised.attributes or {})


def test_the_span_id_is_kept_and_the_trace_id_is_remapped() -> None:
    """Only the locally minted half of the span's identity survives as-is.

    The span id is minted by the SDK. The trace id is not: the middleware
    parents every inbound span on the W3C context in the request's `_meta`, so
    the client picks it, and Azure exports it as `ai.operation.id`. Remapping
    keeps spans of one trace grouped without the client choosing the value.
    """
    span = _make_span(MCP_SDK_SCOPE, "ping", {"mcp.method.name": "ping"})
    sanitised = sanitise_span(span)

    assert sanitised is not None
    original = span.get_span_context()
    kept = sanitised.get_span_context()
    assert original is not None
    assert kept is not None
    assert kept.span_id == original.span_id
    assert kept.trace_id != original.trace_id
    assert sanitised.resource is span.resource


def test_the_trace_id_remap_is_stable_within_the_process() -> None:
    """Two spans of one trace must still land in one operation.

    Minting a fresh random id per span would be just as safe and would throw
    away the grouping the field exists for, so the mapping has to be a function
    of the incoming id, not of the call.
    """
    first = _make_span(MCP_SDK_SCOPE, "ping", {"mcp.method.name": "ping"})
    context = first.get_span_context()
    assert context is not None
    second = ReadableSpan(
        name="ping",
        context=SpanContext(trace_id=context.trace_id, span_id=99, is_remote=False),
        instrumentation_scope=InstrumentationScope(MCP_SDK_SCOPE),
        attributes={"mcp.method.name": "ping"},
    )

    one = sanitise_span(first)
    two = sanitise_span(second)

    assert one is not None
    assert two is not None
    one_context = one.get_span_context()
    two_context = two.get_span_context()
    assert one_context is not None
    assert two_context is not None
    assert one_context.trace_id == two_context.trace_id


def test_the_parent_link_and_tracestate_are_dropped() -> None:
    """Both are lifted off the wire, and both reach Application Insights.

    `ai.operation.parentId` is exported straight from the parent span id, and
    `trace_state` only fails to arrive because the current exporter happens not
    to serialise it. Neither is left to chance.
    """
    remote_parent = SpanContext(
        trace_id=int("74656e616e742d7365637265742d3031", 16),
        span_id=int("3862797465735858", 16),
        is_remote=True,
        trace_state=TraceState([("client", "secret-value")]),
    )
    span = ReadableSpan(
        name="ping",
        context=SpanContext(
            trace_id=remote_parent.trace_id,
            span_id=7,
            is_remote=False,
            trace_state=remote_parent.trace_state,
        ),
        parent=remote_parent,
        instrumentation_scope=InstrumentationScope(MCP_SDK_SCOPE),
        attributes={"mcp.method.name": "ping"},
    )

    sanitised = sanitise_span(span)

    assert sanitised is not None
    assert sanitised.parent is None
    kept = sanitised.get_span_context()
    assert kept is not None
    assert not list(kept.trace_state.items())
    # The trace id decoded to `tenant-secret-01` before the remap existed.
    assert b"secret" not in bytes.fromhex(f"{kept.trace_id:032x}")
    assert "secret-value" not in _blob([sanitised])


# ---------------------------------------------------------------------------
# The exporter wrapper
# ---------------------------------------------------------------------------


def test_the_wrapper_does_not_call_the_inner_exporter_when_all_spans_are_dropped() -> None:
    """An export cycle carrying only foreign spans must be a no-op, not an empty send."""
    inner = _CapturingExporter()
    wrapper = SanitisingSpanExporter(inner)  # ty: ignore[invalid-argument-type]

    result = wrapper.export([_make_span("some.other.library", "work")])

    assert result is SpanExportResult.SUCCESS
    assert inner.spans == []


def test_a_span_the_sanitiser_cannot_process_is_dropped_not_forwarded() -> None:
    """Failing open would export exactly what the sanitiser exists to remove."""
    inner = _CapturingExporter()
    wrapper = SanitisingSpanExporter(inner)  # ty: ignore[invalid-argument-type]

    class _Exploding:
        @property
        def instrumentation_scope(self) -> Any:
            raise RuntimeError("boom")

    assert wrapper.export([_Exploding()]) is SpanExportResult.SUCCESS  # ty: ignore[invalid-argument-type]
    assert inner.spans == []


def test_the_wrapper_forwards_shutdown_and_flush() -> None:
    """The provider's teardown has to reach the real exporter through the wrapper."""
    inner = _CapturingExporter()
    wrapper = SanitisingSpanExporter(inner)  # ty: ignore[invalid-argument-type]

    wrapper.shutdown()
    assert wrapper.force_flush(1000) is True

    assert inner.shutdown_calls == 1
    assert inner.flush_calls == 1


# ---------------------------------------------------------------------------
# Installing the pipeline
# ---------------------------------------------------------------------------


def _patch_global_provider_hooks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    setter: Any,
    getter: Any,
) -> None:
    """Replace the module's handle on OpenTelemetry's global-provider functions.

    The real ones must not run: ``set_tracer_provider`` claims a process-wide
    global exactly once, so a test that reached it would both leak into the rest
    of the session and be unrepeatable.

    Patched through ``telemetry_spans._trace`` rather than on
    ``opentelemetry.trace`` itself, because other modules in this suite install
    fake ``opentelemetry.trace`` modules, which can leave the package attribute
    and the ``sys.modules`` entry as two different objects for the rest of the
    session.  Patching the wrong one of those two patches nothing at all and
    lets the real function run.
    """
    monkeypatch.setattr(
        telemetry_spans,
        "_trace",
        SimpleNamespace(set_tracer_provider=setter, get_tracer_provider=getter),
    )


def test_a_host_installed_tracer_provider_keeps_winning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An application that already set a provider must keep it, and its exporter.

    OpenTelemetry refuses to override an existing global provider, so the
    ``set_tracer_provider`` call below is silently ignored, exactly as it would
    be in a host process.  The pipeline has to notice and stand down instead of
    building an exporter nothing will ever feed.
    """
    host_provider = TracerProvider()
    _patch_global_provider_hooks(
        monkeypatch, setter=lambda _provider: None, getter=lambda: host_provider
    )

    captured = _CapturingExporter()
    monkeypatch.setattr(telemetry_spans, "_AzureMonitorTraceExporter", lambda **_kw: captured)

    assert install_mcp_span_pipeline(_CONNECTION_STRING, None) is False
    # The exporter is built before the global is claimed, so losing the race
    # means there is a batch worker and an exporter to close again.
    assert captured.shutdown_calls == 1, "the provider we built and lost must be shut down"


def test_installing_the_pipeline_wires_the_sanitiser_in_front_of_azure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path: our provider wins, and the Azure exporter sits behind the sanitiser."""
    installed: dict[str, Any] = {}
    _patch_global_provider_hooks(
        monkeypatch,
        setter=lambda provider: installed.setdefault("p", provider),
        getter=lambda: installed.get("p"),
    )

    captured = _CapturingExporter()
    monkeypatch.setattr(telemetry_spans, "_AzureMonitorTraceExporter", lambda **_kw: captured)

    assert install_mcp_span_pipeline(_CONNECTION_STRING, None) is True

    provider = installed["p"]
    # ALWAYS_ON, not the default ParentBased sampler: an MCP client propagates
    # trace context into `_meta`, so ParentBased would hand the collection
    # decision to the client's sampler.
    assert provider.sampler is ALWAYS_ON

    tracer = provider.get_tracer(MCP_SDK_SCOPE)
    with tracer.start_as_current_span(
        "prompts/get " + MARKER, attributes={"gen_ai.prompt.name": MARKER}
    ):
        pass
    provider.force_flush(5000)

    assert captured.spans, "the Azure exporter was never reached"
    assert MARKER not in _blob(captured.spans)

    provider.shutdown()


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_an_unreadable_method_map_degrades_to_unknown_rather_than_passing_it_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the SDK's method maps cannot be read, no method name is exported.

    The allowlist is what makes ``mcp.method.name`` safe, so losing it has to
    fail towards recording nothing rather than towards recording whatever the
    client sent.
    """
    monkeypatch.setitem(sys.modules, "mcp_types.methods", None)
    telemetry_spans._methods_cache = None
    try:
        span = _make_span(MCP_SDK_SCOPE, "ping", {"mcp.method.name": "ping"})
        sanitised = sanitise_span(span)

        assert sanitised is not None
        assert sanitised.name == UNKNOWN_METHOD
    finally:
        telemetry_spans._methods_cache = None


def test_an_unreadable_version_list_drops_the_protocol_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same failure direction for the protocol revision."""
    monkeypatch.setitem(sys.modules, "mcp_types.version", None)
    telemetry_spans._versions_cache = None
    try:
        span = _make_span(
            MCP_SDK_SCOPE,
            "ping",
            {"mcp.method.name": "ping", "mcp.protocol.version": HANDSHAKE_VERSION},
        )
        sanitised = sanitise_span(span)

        assert sanitised is not None
        assert "mcp.protocol.version" not in (sanitised.attributes or {})
    finally:
        telemetry_spans._versions_cache = None


def test_a_span_without_attributes_is_reduced_rather_than_rejected() -> None:
    """A span carrying no attributes at all must not break the exporter.

    ``ReadableSpan.attributes`` is a ``BoundedAttributes`` mapping when the SDK
    fills it and ``None`` when it does not, and an early draft of this code
    tested for ``dict``, which is neither.
    """
    span = ReadableSpan(
        name="something",
        context=SpanContext(trace_id=1, span_id=2, is_remote=False),
        instrumentation_scope=InstrumentationScope(MCP_SDK_SCOPE),
        attributes=None,
    )
    sanitised = sanitise_span(span)

    assert sanitised is not None
    assert dict(sanitised.attributes or {}) == {"mcp.method.name": UNKNOWN_METHOD}


def test_installing_the_pipeline_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telemetry setup failing must not take the MCP server down with it."""

    def _explode(_provider: Any) -> None:
        raise RuntimeError("boom")

    _patch_global_provider_hooks(monkeypatch, setter=_explode, getter=lambda: None)

    assert install_mcp_span_pipeline(_CONNECTION_STRING, None) is False


# ---------------------------------------------------------------------------
# Bounded values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("request_id", "kept"),
    [
        ("7", True),
        ("-7", True),
        ("9" * 19, True),
        ("9" * 20, False),
        ("0" * 400, False),
        ("1" * 4000, False),
    ],
)
def test_a_request_id_is_capped_at_the_int64_range(request_id: str, kept: bool) -> None:  # noqa: FBT001
    """Digits alone are still a channel, so length is bounded as well as shape.

    The attributes handed to the exporter are a plain dict rather than
    ``BoundedAttributes``, so no OpenTelemetry attribute limit applies either,
    and the only remaining ceiling was Azure's 8192-character field cap. A
    4000-digit id measured 4824 bytes on the wire. Every SDK client counts small
    integers.
    """
    span = _make_span(
        MCP_SDK_SCOPE,
        "ping",
        {"mcp.method.name": "ping", "jsonrpc.request.id": request_id},
    )
    sanitised = sanitise_span(span)

    assert sanitised is not None
    assert ("jsonrpc.request.id" in (sanitised.attributes or {})) is kept


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        ("tool_error", True),
        ("-32601", True),
        ("ValueError", True),
        ("mcp.shared.exceptions.MCPError", True),
        ("Unknown prompt: " + MARKER, False),
        ("a" * 65, False),
        ("", False),
    ],
)
def test_error_type_is_checked_rather_than_assumed_safe(value: str, kept: bool) -> None:  # noqa: FBT001
    """The three shapes the middleware writes are a property of the SDK, not of us.

    Today `error.type` is a JSON-RPC code, the literal `tool_error`, or a
    server-side exception class name, none of which can carry client input. That
    holds only while this server sends no requests to the client; the
    server-to-client direction would put a peer's error text here. Checking the
    shape costs one comparison and makes the invariant ours.
    """
    span = _make_span(MCP_SDK_SCOPE, "ping", {"mcp.method.name": "ping", "error.type": value})
    sanitised = sanitise_span(span)

    assert sanitised is not None
    assert ("error.type" in (sanitised.attributes or {})) is kept


# ---------------------------------------------------------------------------
# What the Azure exporter would actually put on the wire
# ---------------------------------------------------------------------------


def _envelopes_for(monkeypatch: pytest.MonkeyPatch, spans: list[ReadableSpan]) -> list[Any]:
    """Run *spans* through the real Azure exporter and capture the envelopes.

    Everything but the HTTP call is genuine: the real exporter, the real
    envelope conversion, the real resource-metric branch. Only `_transmit` is
    replaced, so nothing leaves the process.
    """
    monkeypatch.setenv("APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL", "true")
    monkeypatch.setenv("APPLICATIONINSIGHTS_SDKSTATS_DISABLED", "true")

    # The resource-metric branch reads `tracer_provider.resource`, so it only
    # fires when a real provider is in play, which is exactly the production
    # situation: install_mcp_span_pipeline builds one and claims the global.
    exporter = AzureMonitorTraceExporter(
        connection_string=_CONNECTION_STRING,
        disable_offline_storage=True,
        tracer_provider=TracerProvider(),
    )
    captured: list[Any] = []

    def _transmit(envelopes: Any) -> Any:
        captured.extend(envelopes)
        return ExportResult.SUCCESS

    monkeypatch.setattr(exporter, "_transmit", _transmit)
    try:
        SanitisingSpanExporter(exporter).export(spans)
    finally:
        exporter.shutdown()
        # Constructing a real exporter can start the customer-sdkstats metric
        # reader, which then tries to POST to the connection string's endpoint
        # on its own thread. Production never reaches that (telemetry.py sets
        # APPLICATIONINSIGHTS_SDKSTATS_DISABLED before the first exporter
        # exists), but in a test the manager may already be a live singleton
        # from an earlier module, and a unit test must make no network calls.
        with contextlib.suppress(Exception):
            get_customer_stats_manager().shutdown()
    return captured


def test_a_span_batch_produces_no_metric_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """The trace exporter smuggles a metric in unless an env var says otherwise.

    It appends an `_OTELRESOURCE_` `MetricData` envelope to every non-empty
    export batch, so switching spans on would have started sending metrics while
    the docs promise none. The variable is read at export time, so it cannot be
    scoped around construction the way the OTEL_*_EXPORTER pair is.
    """
    monkeypatch.delenv("APPLICATIONINSIGHTS_OPENTELEMETRY_RESOURCE_METRIC_DISABLED", raising=False)
    span = _make_span(MCP_SDK_SCOPE, "ping", {"mcp.method.name": "ping"})

    # Without the setting, to show the envelope is real and not hypothetical.
    leaky = _envelopes_for(monkeypatch, [span])
    assert any(e.data.base_type == "MetricData" for e in leaky)

    # With it, as install_mcp_span_pipeline sets it.
    monkeypatch.setenv("APPLICATIONINSIGHTS_OPENTELEMETRY_RESOURCE_METRIC_DISABLED", "true")
    clean = _envelopes_for(monkeypatch, [span])
    assert clean, "the span itself must still be exported"
    assert not [e for e in clean if e.data.base_type == "MetricData"]


def test_nothing_client_chosen_survives_into_the_azure_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end of the line: what the exporter would put on the wire.

    `ai.operation.id` and `ai.operation.parentId` are written straight from the
    span's trace id and parent span id, both of which the client controls
    through `traceparent`, and neither is truncated or validated on the way.
    """
    monkeypatch.setenv("APPLICATIONINSIGHTS_OPENTELEMETRY_RESOURCE_METRIC_DISABLED", "true")
    # "tenant-secret-01" and "8bytesXX" as hex, which is how a client sends them.
    remote = SpanContext(
        trace_id=int(b"tenant-secret-01".hex(), 16),
        span_id=int(b"8bytesXX".hex(), 16),
        is_remote=True,
    )
    span = ReadableSpan(
        name=f"prompts/get {MARKER}",
        context=SpanContext(trace_id=remote.trace_id, span_id=4242, is_remote=False),
        parent=remote,
        instrumentation_scope=InstrumentationScope(MCP_SDK_SCOPE),
        attributes={"mcp.method.name": "prompts/get", "gen_ai.prompt.name": MARKER},
        status=Status(StatusCode.ERROR, f"Unknown prompt: {MARKER}"),
        start_time=1,
        end_time=2,
    )

    envelopes = _envelopes_for(monkeypatch, [span])

    assert len(envelopes) == 1
    rendered = str(envelopes[0].as_dict())
    assert MARKER not in rendered
    operation_id = envelopes[0].tags["ai.operation.id"]
    assert b"tenant-secret" not in bytes.fromhex(operation_id)
    assert "ai.operation.parentId" not in envelopes[0].tags


def test_a_failed_exporter_build_never_claims_the_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claiming the global before the pipeline is whole is unrecoverable.

    ``set_tracer_provider`` spends OpenTelemetry's one-shot ``Once``. If the
    exporter then failed to build, an empty provider stayed installed for the
    life of the process: every span fully recorded into nothing, the host locked
    out for good, and the caller told the install had failed.
    """
    claimed: list[object] = []
    _patch_global_provider_hooks(
        monkeypatch, setter=claimed.append, getter=lambda: claimed[-1] if claimed else None
    )

    def _explode(**_kw: Any) -> Any:
        raise RuntimeError("no exporter for you")

    monkeypatch.setattr(telemetry_spans, "_AzureMonitorTraceExporter", _explode)

    assert install_mcp_span_pipeline(_CONNECTION_STRING, None) is False
    assert claimed == [], "the global was claimed despite the install failing"
