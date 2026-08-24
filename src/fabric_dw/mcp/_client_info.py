"""Server middleware that records which MCP client is on the other end (#1048).

What is collected and why it is safe
------------------------------------
``clientInfo.name`` is the client software describing itself (``claude-ai``,
``mcp-inspector``, an in-house integration), not user input travelling through
the server.  That is what separates it from, say, the prompt name on a
``prompts/get`` request, which is free text chosen by whoever composed the
request and is stripped out of the protocol spans by
:mod:`fabric_dw.telemetry_spans`.  The version is deliberately not recorded:
it changes with every client release and answers no question this project has.

Where the value comes from
--------------------------
Two protocol eras, two accessors, both public API:

- The handshake era (2025 and earlier) puts ``clientInfo`` in the ``initialize``
  request.  Middleware runs before params validation, so ``ctx.params`` is the
  raw wire mapping and the key is camelCase.  The value has to be read here,
  because the SDK commits it to the connection only *after* the middleware
  chain returns.
- The 2026-07-28 era drops the handshake and carries client info in each
  request's ``_meta`` envelope.  The SDK turns that into
  ``ctx.session.client_params`` before the first message reaches middleware.

Reading both means the client is recorded on the first message of a connection
in either era, and a client that identifies itself in neither place is recorded
as ``unknown``, which the protocol permits.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack, suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.context import CallNext, HandlerResult, ServerRequestContext

__all__ = ["ClientInfoMiddleware", "client_name_from_context"]


def client_name_from_context(ctx: ServerRequestContext[Any, Any]) -> object:
    """Return the raw ``clientInfo.name`` for *ctx*, or ``None``.

    Returns the value untouched; :func:`~fabric_dw.telemetry.normalise_mcp_client`
    owns turning it into something recordable.

    Args:
        ctx: The per-request context handed to the middleware.

    Returns:
        Whatever the client put in ``clientInfo.name``, or ``None`` when it
        supplied no client info this code can reach.
    """
    if ctx.method == "initialize":
        params: Mapping[str, Any] | None = ctx.params
        info = params.get("clientInfo") if params is not None else None
        # A middleware sees the unvalidated wire params, so nothing guarantees
        # the shape; anything else falls through to the session below.
        if isinstance(info, Mapping):
            return info.get("name")

    client_params = ctx.session.client_params
    if client_params is None or client_params.client_info is None:
        return None
    return client_params.client_info.name


class ClientInfoMiddleware:
    """Wrap every inbound message in a :func:`~fabric_dw.telemetry.mcp_client_scope`.

    Registered via the ``middleware=`` constructor argument of ``MCPServer``,
    which runs it inside the SDK's own built-ins, so the OpenTelemetry span for
    the message is already open by the time this runs.

    Telemetry must never break the server: a failure to read or record the
    client is swallowed and the message is handled exactly as if this middleware
    were not installed.
    """

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        """Record the connecting client, then hand the message on."""
        with ExitStack() as stack:
            # Only the recording is best-effort.  call_next stays outside the
            # suppression, so a handler's exception propagates untouched.
            with suppress(Exception):
                # Resolved per call rather than bound at import, which is how
                # every telemetry call site outside the two entry points reaches
                # this module (see telemetry_commands.emit_command_invoked and
                # auth.py).
                from fabric_dw.telemetry import mcp_client_scope  # noqa: PLC0415

                stack.enter_context(mcp_client_scope(client_name_from_context(ctx)))
            return await call_next(ctx)
