"""Unit tests for fabric_dw.mcp._context — fabric_lifespan and build_context.

Coverage goals
--------------
- ``build_context`` constructs a ``ServerContext`` with the expected attributes.
- ``fabric_lifespan`` sets ``_SERVER_CTX`` during the ``yield`` and resets it
  to ``None`` on exit (both normal and exceptional exit).
- The HTTP client's ``__aenter__`` and ``__aexit__`` are called exactly once
  each, confirming the lifespan owns the connection lifecycle.
- No real network connections are made — the HTTP client is replaced with a
  fake async context manager.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import fabric_dw.mcp._context as _ctx_module
from fabric_dw import auth as _auth
from fabric_dw.exceptions import ConfigError
from fabric_dw.mcp._context import (
    ServerContext,
    build_context,
    fabric_lifespan,
    get_context,
)

# ---------------------------------------------------------------------------
# Fake HTTP client
# ---------------------------------------------------------------------------


class _FakeHttpClient:
    """Minimal async context manager that tracks enter/exit calls."""

    def __init__(self) -> None:
        self.entered: int = 0
        self.exited: int = 0

    async def __aenter__(self) -> _FakeHttpClient:
        self.entered += 1
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.exited += 1


# ---------------------------------------------------------------------------
# build_context tests
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_returns_server_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """build_context returns a fully populated ServerContext."""
        monkeypatch.setenv("FABRIC_AUTH", "default")
        # Patch FabricHttpClient so no real credential resolution happens.
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http):
            ctx = build_context()

        assert isinstance(ctx, ServerContext)
        assert ctx.http is fake_http
        assert ctx.auth_mode == _auth.CredentialMode.DEFAULT

    def test_explicit_environ_mapping(self) -> None:
        """build_context reads from the provided mapping, not os.environ."""
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http):
            ctx = build_context(environ={"FABRIC_AUTH": "default"})

        assert ctx.auth_mode == _auth.CredentialMode.DEFAULT

    def test_invalid_auth_raises_config_error(self) -> None:
        with pytest.raises(ConfigError, match="invalid FABRIC_AUTH"):
            build_context(environ={"FABRIC_AUTH": "not-a-valid-mode"})


# ---------------------------------------------------------------------------
# fabric_lifespan tests
# ---------------------------------------------------------------------------


class TestFabricLifespan:
    @pytest.mark.anyio
    async def test_ctx_set_during_yield_cleared_after(self) -> None:
        """_SERVER_CTX is set during the lifespan yield and None before/after."""
        fake_http = _FakeHttpClient()
        fake_ctx = ServerContext(
            http=fake_http,  # ty: ignore[invalid-argument-type]
            cache=MagicMock(),
            resolver=MagicMock(),
            auth_mode=_auth.CredentialMode.DEFAULT,
        )
        app_mock = MagicMock()

        # _SERVER_CTX starts as None.
        assert _ctx_module._SERVER_CTX is None

        ctx_during: ServerContext | None = None

        with patch("fabric_dw.mcp._context.build_context", return_value=fake_ctx):
            async with fabric_lifespan(app_mock):
                ctx_during = _ctx_module._SERVER_CTX

        # After exit, sentinel is None again.
        assert _ctx_module._SERVER_CTX is None
        assert ctx_during is fake_ctx

    @pytest.mark.anyio
    async def test_http_aenter_aexit_called(self) -> None:
        """The HTTP client's __aenter__ and __aexit__ are called exactly once."""
        fake_http = _FakeHttpClient()
        fake_ctx = ServerContext(
            http=fake_http,  # ty: ignore[invalid-argument-type]
            cache=MagicMock(),
            resolver=MagicMock(),
            auth_mode=_auth.CredentialMode.DEFAULT,
        )
        app_mock = MagicMock()

        with patch("fabric_dw.mcp._context.build_context", return_value=fake_ctx):
            async with fabric_lifespan(app_mock):
                pass

        assert fake_http.entered == 1
        assert fake_http.exited == 1

    @pytest.mark.anyio
    async def test_sentinel_set_and_cleared_via_build_context(self) -> None:
        """Full end-to-end: lifespan sets _SERVER_CTX; get_context() works inside."""
        fake_http = _FakeHttpClient()
        fake_ctx = ServerContext(
            http=fake_http,  # ty: ignore[invalid-argument-type]
            cache=MagicMock(),
            resolver=MagicMock(),
            auth_mode=_auth.CredentialMode.DEFAULT,
        )
        app_mock = MagicMock()

        ctx_inside: ServerContext | None = None

        with patch("fabric_dw.mcp._context.build_context", return_value=fake_ctx):
            async with fabric_lifespan(app_mock):
                ctx_inside = get_context()

        assert ctx_inside is fake_ctx
        # Sentinel is cleared after exit.
        assert _ctx_module._SERVER_CTX is None

    @pytest.mark.anyio
    async def test_sentinel_cleared_on_exception(self) -> None:
        """_SERVER_CTX is reset to None even when the body raises."""
        fake_http = _FakeHttpClient()
        fake_ctx = ServerContext(
            http=fake_http,  # ty: ignore[invalid-argument-type]
            cache=MagicMock(),
            resolver=MagicMock(),
            auth_mode=_auth.CredentialMode.DEFAULT,
        )
        app_mock = MagicMock()

        with (
            patch("fabric_dw.mcp._context.build_context", return_value=fake_ctx),
            pytest.raises(RuntimeError, match="boom"),
        ):
            async with fabric_lifespan(app_mock):
                raise RuntimeError("boom")

        assert _ctx_module._SERVER_CTX is None
        # __aexit__ must still have been called.
        assert fake_http.exited == 1
