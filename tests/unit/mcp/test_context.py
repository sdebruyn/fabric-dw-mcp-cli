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

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import fabric_dw.mcp._context as _ctx_module
from fabric_dw import auth as _auth
from fabric_dw.config import Defaults, UserConfig, save_config
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

    def test_max_429_retries_env_var_wired_to_client(self) -> None:
        """FABRIC_DW_MAX_429_RETRIES must propagate to the HTTP client."""
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(environ={"FABRIC_AUTH": "default", "FABRIC_DW_MAX_429_RETRIES": "15"})
        _, kwargs = mock_cls.call_args
        assert kwargs.get("max_429_retries") == 15

    def test_retry_deadline_s_env_var_wired_to_client(self) -> None:
        """FABRIC_DW_RETRY_DEADLINE_S must propagate to the HTTP client as an int."""
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(environ={"FABRIC_AUTH": "default", "FABRIC_DW_RETRY_DEADLINE_S": "600"})
        _, kwargs = mock_cls.call_args
        assert kwargs.get("combined_deadline_s") == 600

    def test_no_retry_env_vars_no_kwargs_passed(self) -> None:
        """When no retry env vars are set, the client receives no explicit retries/deadline."""
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(environ={"FABRIC_AUTH": "default"})
        _, kwargs = mock_cls.call_args
        assert "max_429_retries" not in kwargs
        assert "combined_deadline_s" not in kwargs

    def test_malformed_max_429_retries_env_var_ignored(self) -> None:
        """A non-integer FABRIC_DW_MAX_429_RETRIES must be ignored (client uses its default)."""
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(environ={"FABRIC_AUTH": "default", "FABRIC_DW_MAX_429_RETRIES": "bad"})
        _, kwargs = mock_cls.call_args
        assert "max_429_retries" not in kwargs

    def test_malformed_retry_deadline_env_var_ignored(self) -> None:
        """A non-integer FABRIC_DW_RETRY_DEADLINE_S must be ignored."""
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(environ={"FABRIC_AUTH": "default", "FABRIC_DW_RETRY_DEADLINE_S": "bad"})
        _, kwargs = mock_cls.call_args
        assert "combined_deadline_s" not in kwargs

    def test_inf_retry_deadline_env_var_ignored(self) -> None:
        """A non-finite FABRIC_DW_RETRY_DEADLINE_S (inf) must be ignored (OverflowError)."""
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(environ={"FABRIC_AUTH": "default", "FABRIC_DW_RETRY_DEADLINE_S": "inf"})
        _, kwargs = mock_cls.call_args
        assert "combined_deadline_s" not in kwargs

    def test_float_formatted_int_deadline_accepted(self) -> None:
        """FABRIC_DW_RETRY_DEADLINE_S='300.0' is accepted as 300 (Docker float-int)."""
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(environ={"FABRIC_AUTH": "default", "FABRIC_DW_RETRY_DEADLINE_S": "300.0"})
        _, kwargs = mock_cls.call_args
        assert kwargs.get("combined_deadline_s") == 300

    def test_float_formatted_int_retries_accepted(self) -> None:
        """FABRIC_DW_MAX_429_RETRIES='20.0' is accepted as 20 (Docker float-int)."""
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(environ={"FABRIC_AUTH": "default", "FABRIC_DW_MAX_429_RETRIES": "20.0"})
        _, kwargs = mock_cls.call_args
        assert kwargs.get("max_429_retries") == 20


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

# ---------------------------------------------------------------------------
# build_context reads config.toml
# ---------------------------------------------------------------------------


class TestBuildContextConfigRead:
    """Tests that build_context reads retry knobs from config.toml."""

    def test_config_max_429_retries_wired_to_client(self, tmp_path: Path) -> None:
        """[defaults] max_429_retries from config propagates to the HTTP client."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(defaults=Defaults(max_429_retries=7)), path)
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(environ={"FABRIC_AUTH": "default"}, config_path=path)
        _, kwargs = mock_cls.call_args
        assert kwargs.get("max_429_retries") == 7

    def test_config_retry_deadline_s_wired_to_client(self, tmp_path: Path) -> None:
        """[defaults] retry_deadline_s from config propagates to the HTTP client as int."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(defaults=Defaults(retry_deadline_s=120)), path)
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(environ={"FABRIC_AUTH": "default"}, config_path=path)
        _, kwargs = mock_cls.call_args
        assert kwargs.get("combined_deadline_s") == 120

    def test_env_beats_config_for_retries(self, tmp_path: Path) -> None:
        """Env var takes precedence over config for max_429_retries."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(defaults=Defaults(max_429_retries=3)), path)
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(
                environ={"FABRIC_AUTH": "default", "FABRIC_DW_MAX_429_RETRIES": "25"},
                config_path=path,
            )
        _, kwargs = mock_cls.call_args
        assert kwargs.get("max_429_retries") == 25

    def test_env_beats_config_for_deadline(self, tmp_path: Path) -> None:
        """Env var takes precedence over config for retry_deadline_s."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(defaults=Defaults(retry_deadline_s=60)), path)
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http) as mock_cls:
            build_context(
                environ={"FABRIC_AUTH": "default", "FABRIC_DW_RETRY_DEADLINE_S": "999"},
                config_path=path,
            )
        _, kwargs = mock_cls.call_args
        assert kwargs.get("combined_deadline_s") == 999

    def test_config_path_none_uses_platform_default(self) -> None:
        """config_path=None is accepted and load_config is called without error."""
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http):
            # This should not raise even if the default config file doesn't exist.
            ctx = build_context(environ={"FABRIC_AUTH": "default"}, config_path=None)
        assert isinstance(ctx, ServerContext)


# ---------------------------------------------------------------------------
# build_context — 3-layer auth_mode resolution
# ---------------------------------------------------------------------------


class TestBuildContextAuthModeResolution:
    """Tests for the 3-layer auth_mode resolution: env > config > built-in default."""

    def test_env_fabric_auth_takes_precedence_over_config(self, tmp_path: Path) -> None:
        """FABRIC_AUTH env var overrides [defaults] auth_mode in config."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(defaults=Defaults(auth_mode="interactive")), path)
        fake_http = MagicMock()
        with (
            patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http),
            patch("fabric_dw.mcp._context._auth.get_credential", return_value=MagicMock()),
        ):
            ctx = build_context(environ={"FABRIC_AUTH": "sp"}, config_path=path)
        assert ctx.auth_mode == _auth.CredentialMode.SERVICE_PRINCIPAL

    def test_config_auth_mode_used_when_env_absent(self, tmp_path: Path) -> None:
        """[defaults] auth_mode is used when FABRIC_AUTH is absent."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(defaults=Defaults(auth_mode="interactive")), path)
        fake_http = MagicMock()
        with (
            patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http),
            patch("fabric_dw.mcp._context._auth.get_credential", return_value=MagicMock()),
        ):
            ctx = build_context(environ={}, config_path=path)
        assert ctx.auth_mode == _auth.CredentialMode.INTERACTIVE

    def test_builtin_default_used_when_env_and_config_absent(self, tmp_path: Path) -> None:
        """Built-in default (CredentialMode.DEFAULT) is used when neither env nor config is set."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(), path)
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http):
            ctx = build_context(environ={}, config_path=path)
        assert ctx.auth_mode == _auth.CredentialMode.DEFAULT

    def test_empty_fabric_auth_falls_through_to_config(self, tmp_path: Path) -> None:
        """Empty FABRIC_AUTH (whitespace) falls through to config, not treated as invalid."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(defaults=Defaults(auth_mode="interactive")), path)
        fake_http = MagicMock()
        with (
            patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http),
            patch("fabric_dw.mcp._context._auth.get_credential", return_value=MagicMock()),
        ):
            ctx = build_context(environ={"FABRIC_AUTH": ""}, config_path=path)
        assert ctx.auth_mode == _auth.CredentialMode.INTERACTIVE

    def test_whitespace_only_fabric_auth_falls_through_to_config(self, tmp_path: Path) -> None:
        """Whitespace-only FABRIC_AUTH falls through to config."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(defaults=Defaults(auth_mode="interactive")), path)
        fake_http = MagicMock()
        with (
            patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http),
            patch("fabric_dw.mcp._context._auth.get_credential", return_value=MagicMock()),
        ):
            ctx = build_context(environ={"FABRIC_AUTH": "   "}, config_path=path)
        assert ctx.auth_mode == _auth.CredentialMode.INTERACTIVE

    def test_empty_fabric_auth_falls_through_to_builtin_default(self, tmp_path: Path) -> None:
        """Empty FABRIC_AUTH with no config falls through to built-in default."""
        path = tmp_path / "no-config.toml"  # does not exist
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http):
            ctx = build_context(environ={"FABRIC_AUTH": ""}, config_path=path)
        assert ctx.auth_mode == _auth.CredentialMode.DEFAULT

    def test_invalid_fabric_auth_raises_config_error(self) -> None:
        """A non-empty but unrecognised FABRIC_AUTH value raises ConfigError."""
        with pytest.raises(ConfigError, match="invalid FABRIC_AUTH"):
            build_context(environ={"FABRIC_AUTH": "managed_identity"})

    def test_invalid_fabric_auth_does_not_fall_through_to_config(self, tmp_path: Path) -> None:
        """An invalid non-empty FABRIC_AUTH must NOT silently fall through to a different mode."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(defaults=Defaults(auth_mode="interactive")), path)
        # Should raise, not silently pick 'interactive' from config.
        with pytest.raises(ConfigError, match="invalid FABRIC_AUTH"):
            build_context(environ={"FABRIC_AUTH": "bad-mode"}, config_path=path)

    def test_config_sp_mode_wired(self, tmp_path: Path) -> None:
        """[defaults] auth_mode = 'sp' selects SERVICE_PRINCIPAL."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(defaults=Defaults(auth_mode="sp")), path)
        fake_http = MagicMock()
        with (
            patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http),
            patch("fabric_dw.mcp._context._auth.get_credential", return_value=MagicMock()),
        ):
            ctx = build_context(environ={}, config_path=path)
        assert ctx.auth_mode == _auth.CredentialMode.SERVICE_PRINCIPAL

    def test_config_default_mode_wired(self, tmp_path: Path) -> None:
        """[defaults] auth_mode = 'default' selects DEFAULT."""
        path = tmp_path / "config.toml"
        save_config(UserConfig(defaults=Defaults(auth_mode="default")), path)
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http):
            ctx = build_context(environ={}, config_path=path)
        assert ctx.auth_mode == _auth.CredentialMode.DEFAULT

    def test_env_fabric_auth_interactive_maps_to_interactive(self) -> None:
        """FABRIC_AUTH=interactive selects CredentialMode.INTERACTIVE via the env path."""
        fake_http = MagicMock()
        with (
            patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http),
            patch("fabric_dw.mcp._context._auth.get_credential", return_value=MagicMock()),
        ):
            ctx = build_context(environ={"FABRIC_AUTH": "interactive"})
        assert ctx.auth_mode == _auth.CredentialMode.INTERACTIVE

    def test_env_fabric_auth_uppercase_sp_accepted(self) -> None:
        """FABRIC_AUTH=SP (uppercase) is accepted and maps to SERVICE_PRINCIPAL.

        The env path normalises to lowercase before lookup, so mixed-case values
        must work identically to their lowercase equivalents.
        """
        fake_http = MagicMock()
        with (
            patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http),
            patch("fabric_dw.mcp._context._auth.get_credential", return_value=MagicMock()),
        ):
            ctx = build_context(environ={"FABRIC_AUTH": "SP"})
        assert ctx.auth_mode == _auth.CredentialMode.SERVICE_PRINCIPAL

    def test_env_fabric_auth_mixed_case_interactive_accepted(self) -> None:
        """FABRIC_AUTH=Interactive (mixed case) is accepted and maps to INTERACTIVE."""
        fake_http = MagicMock()
        with (
            patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http),
            patch("fabric_dw.mcp._context._auth.get_credential", return_value=MagicMock()),
        ):
            ctx = build_context(environ={"FABRIC_AUTH": "Interactive"})
        assert ctx.auth_mode == _auth.CredentialMode.INTERACTIVE

    def test_env_fabric_auth_uppercase_default_accepted(self) -> None:
        """FABRIC_AUTH=DEFAULT (uppercase) is accepted and maps to DEFAULT."""
        fake_http = MagicMock()
        with patch("fabric_dw.mcp._context.FabricHttpClient", return_value=fake_http):
            ctx = build_context(environ={"FABRIC_AUTH": "DEFAULT"})
        assert ctx.auth_mode == _auth.CredentialMode.DEFAULT
