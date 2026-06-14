"""Tests for fabric_dw.auth — written before implementation (TDD)."""

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from azure.identity.aio import ClientAssertionCredential
from azure.identity.aio import ClientSecretCredential as AsyncClientSecretCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential

import fabric_dw.auth as auth_module
from fabric_dw.auth import (
    DEFAULT_INTERACTIVE_CLIENT_ID,
    FABRIC_SCOPE,
    SQL_SCOPE,
    CredentialMode,
    SyncCredentialAdapter,
    _SyncCredentialAdapter,
    get_credential,
)
from fabric_dw.exceptions import ConfigError


def test_fabric_scope_constant() -> None:
    assert FABRIC_SCOPE == "https://analysis.windows.net/powerbi/api/.default"


def test_sql_scope_constant() -> None:
    assert SQL_SCOPE == "https://database.windows.net/.default"


def test_get_credential_default_returns_default_azure_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    credential = get_credential(CredentialMode.DEFAULT)
    assert isinstance(credential, AsyncDefaultAzureCredential)


def test_get_credential_default_is_default_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    credential = get_credential()
    assert isinstance(credential, AsyncDefaultAzureCredential)


def test_get_credential_default_includes_interactive_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DefaultAzureCredential must NOT exclude the interactive browser credential."""
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    with patch("fabric_dw.auth.DefaultAzureCredential") as mock_dac:
        get_credential(CredentialMode.DEFAULT)
        _, kwargs = mock_dac.call_args
        assert kwargs.get("exclude_interactive_browser_credential") is False


def test_get_credential_service_principal_returns_client_secret_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_TENANT_ID", "test-tenant-id")
    monkeypatch.setenv("AZURE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "test-client-secret")

    credential = get_credential(CredentialMode.SERVICE_PRINCIPAL)
    assert isinstance(credential, AsyncClientSecretCredential)


@pytest.mark.parametrize(
    "missing_var",
    ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"],
)
def test_get_credential_service_principal_raises_config_error_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
    missing_var: str,
) -> None:
    # Set all three then remove one
    monkeypatch.setenv("AZURE_TENANT_ID", "test-tenant-id")
    monkeypatch.setenv("AZURE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.delenv(missing_var)

    with pytest.raises(ConfigError):
        get_credential(CredentialMode.SERVICE_PRINCIPAL)


def test_get_credential_service_principal_raises_config_error_when_all_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)

    with pytest.raises(ConfigError):
        get_credential(CredentialMode.SERVICE_PRINCIPAL)


def test_get_credential_interactive_returns_sync_adapter() -> None:
    credential = get_credential(CredentialMode.INTERACTIVE)
    assert isinstance(credential, SyncCredentialAdapter)


# ---------------------------------------------------------------------------
# SyncCredentialAdapter — get_token and close dispatch via asyncio.to_thread
# ---------------------------------------------------------------------------


async def test_sync_adapter_get_token_returns_token_from_inner() -> None:
    """get_token must return the token produced by the inner sync credential."""
    expected_token = AccessToken("my-access-token", 9999999999)
    inner = MagicMock()
    inner.get_token.return_value = expected_token

    adapter = SyncCredentialAdapter(inner)
    result = await adapter.get_token(FABRIC_SCOPE)

    assert result == expected_token
    inner.get_token.assert_called_once_with(
        FABRIC_SCOPE, claims=None, tenant_id=None, enable_cae=False
    )


async def test_sync_adapter_get_token_runs_in_worker_thread() -> None:
    """get_token must offload the inner call to a worker thread, not the test thread."""
    test_thread = threading.current_thread()
    inner_thread: list[threading.Thread] = []

    def fake_get_token(*_args: object, **_kwargs: object) -> AccessToken:
        inner_thread.append(threading.current_thread())
        return AccessToken("tok", 9999999999)

    inner = MagicMock()
    inner.get_token.side_effect = fake_get_token

    adapter = SyncCredentialAdapter(inner)
    await adapter.get_token(FABRIC_SCOPE)

    assert inner_thread, "inner.get_token was never called"
    assert inner_thread[0] is not test_thread, "get_token must run in a worker thread"


async def test_sync_adapter_get_token_dispatches_via_to_thread() -> None:
    """get_token must call asyncio.to_thread with the inner method."""
    inner = MagicMock()
    inner.get_token.return_value = AccessToken("tok", 9999999999)
    adapter = SyncCredentialAdapter(inner)

    with patch("fabric_dw.auth.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = AccessToken("tok", 9999999999)
        await adapter.get_token(FABRIC_SCOPE)
        mock_to_thread.assert_called_once_with(
            inner.get_token,
            FABRIC_SCOPE,
            claims=None,
            tenant_id=None,
            enable_cae=False,
        )


async def test_sync_adapter_close_dispatches_via_to_thread() -> None:
    """close() must offload the inner close call to a worker thread."""
    inner = MagicMock()
    adapter = SyncCredentialAdapter(inner)

    with patch("fabric_dw.auth.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = None
        await adapter.close()
        mock_to_thread.assert_called_once_with(inner.close)


async def test_sync_adapter_close_runs_in_worker_thread() -> None:
    """close() must execute inner.close in a worker thread, not the test thread."""
    test_thread = threading.current_thread()
    inner_thread: list[threading.Thread] = []

    def fake_close() -> None:
        inner_thread.append(threading.current_thread())

    inner = MagicMock()
    inner.close.side_effect = fake_close

    adapter = SyncCredentialAdapter(inner)
    await adapter.close()

    assert inner_thread, "inner.close was never called"
    assert inner_thread[0] is not test_thread, "close must run in a worker thread"


# ---------------------------------------------------------------------------
# DEFAULT_INTERACTIVE_CLIENT_ID constant
# ---------------------------------------------------------------------------


def test_default_interactive_client_id_constant() -> None:
    assert DEFAULT_INTERACTIVE_CLIENT_ID == "f666e5ee-2149-4c6a-87eb-13c9e1fdc70d"


# ---------------------------------------------------------------------------
# Interactive mode — shared client_id + env overrides
# ---------------------------------------------------------------------------


def test_interactive_mode_uses_default_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FABRIC_INTERACTIVE_CLIENT_ID", raising=False)
    with patch("fabric_dw.auth.InteractiveBrowserCredential") as mock_ibc:
        get_credential(CredentialMode.INTERACTIVE)
        _, kwargs = mock_ibc.call_args
        assert kwargs.get("client_id") == DEFAULT_INTERACTIVE_CLIENT_ID


def test_interactive_mode_respects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    custom_id = "deadbeef-0000-0000-0000-000000000001"
    monkeypatch.setenv("FABRIC_INTERACTIVE_CLIENT_ID", custom_id)
    with patch("fabric_dw.auth.InteractiveBrowserCredential") as mock_ibc:
        get_credential(CredentialMode.INTERACTIVE)
        _, kwargs = mock_ibc.call_args
        assert kwargs.get("client_id") == custom_id


def test_interactive_mode_uses_tenant_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FABRIC_INTERACTIVE_CLIENT_ID", raising=False)
    monkeypatch.setenv("FABRIC_INTERACTIVE_TENANT_ID", "my-tenant-id")
    with patch("fabric_dw.auth.InteractiveBrowserCredential") as mock_ibc:
        get_credential(CredentialMode.INTERACTIVE)
        _, kwargs = mock_ibc.call_args
        assert kwargs.get("tenant_id") == "my-tenant-id"


def test_interactive_mode_no_tenant_id_when_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FABRIC_INTERACTIVE_CLIENT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)
    with patch("fabric_dw.auth.InteractiveBrowserCredential") as mock_ibc:
        get_credential(CredentialMode.INTERACTIVE)
        _, kwargs = mock_ibc.call_args
        assert "tenant_id" not in kwargs


# ---------------------------------------------------------------------------
# Default mode — shared client_id forwarded to DefaultAzureCredential
# ---------------------------------------------------------------------------


def test_default_mode_passes_interactive_browser_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FABRIC_INTERACTIVE_CLIENT_ID", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    with patch("fabric_dw.auth.DefaultAzureCredential") as mock_dac:
        get_credential(CredentialMode.DEFAULT)
        _, kwargs = mock_dac.call_args
        assert kwargs.get("interactive_browser_client_id") == DEFAULT_INTERACTIVE_CLIENT_ID


def test_default_mode_respects_env_override_for_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_id = "deadbeef-0000-0000-0000-000000000002"
    monkeypatch.setenv("FABRIC_INTERACTIVE_CLIENT_ID", custom_id)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    with patch("fabric_dw.auth.DefaultAzureCredential") as mock_dac:
        get_credential(CredentialMode.DEFAULT)
        _, kwargs = mock_dac.call_args
        assert kwargs.get("interactive_browser_client_id") == custom_id


def test_default_mode_passes_interactive_browser_tenant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FABRIC_INTERACTIVE_CLIENT_ID", raising=False)
    monkeypatch.setenv("FABRIC_INTERACTIVE_TENANT_ID", "my-tenant-id")
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    with patch("fabric_dw.auth.DefaultAzureCredential") as mock_dac:
        get_credential(CredentialMode.DEFAULT)
        _, kwargs = mock_dac.call_args
        assert kwargs.get("interactive_browser_tenant_id") == "my-tenant-id"


def test_default_mode_no_interactive_browser_tenant_id_when_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FABRIC_INTERACTIVE_CLIENT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    with patch("fabric_dw.auth.DefaultAzureCredential") as mock_dac:
        get_credential(CredentialMode.DEFAULT)
        _, kwargs = mock_dac.call_args
        assert "interactive_browser_tenant_id" not in kwargs


# ---------------------------------------------------------------------------
# SP mode — unchanged, shared default does not apply
# ---------------------------------------------------------------------------


def test_sp_mode_does_not_use_interactive_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_TENANT_ID", "test-tenant-id")
    monkeypatch.setenv("AZURE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.delenv("FABRIC_INTERACTIVE_CLIENT_ID", raising=False)
    with patch("fabric_dw.auth.ClientSecretCredential") as mock_csc:
        get_credential(CredentialMode.SERVICE_PRINCIPAL)
        _, kwargs = mock_csc.call_args
        assert "interactive_browser_client_id" not in kwargs
        assert kwargs.get("client_id") == "test-client-id"


# ---------------------------------------------------------------------------
# Registry dispatch — one test per mode
# ---------------------------------------------------------------------------


def test_registry_dispatches_default_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registry must dispatch to the default factory for CredentialMode.DEFAULT."""
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    mock_factory = MagicMock(return_value=MagicMock())
    original = auth_module._CREDENTIAL_REGISTRY[CredentialMode.DEFAULT]
    try:
        auth_module._CREDENTIAL_REGISTRY[CredentialMode.DEFAULT] = mock_factory
        get_credential(CredentialMode.DEFAULT)
        mock_factory.assert_called_once()
    finally:
        auth_module._CREDENTIAL_REGISTRY[CredentialMode.DEFAULT] = original


def test_registry_dispatches_service_principal_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registry must dispatch to the SP factory for CredentialMode.SERVICE_PRINCIPAL."""
    monkeypatch.setenv("AZURE_TENANT_ID", "t")
    monkeypatch.setenv("AZURE_CLIENT_ID", "c")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "s")
    mock_factory = MagicMock(return_value=MagicMock())
    original = auth_module._CREDENTIAL_REGISTRY[CredentialMode.SERVICE_PRINCIPAL]
    try:
        auth_module._CREDENTIAL_REGISTRY[CredentialMode.SERVICE_PRINCIPAL] = mock_factory
        get_credential(CredentialMode.SERVICE_PRINCIPAL)
        mock_factory.assert_called_once()
    finally:
        auth_module._CREDENTIAL_REGISTRY[CredentialMode.SERVICE_PRINCIPAL] = original


def test_registry_dispatches_interactive_mode() -> None:
    """Registry must dispatch to the interactive factory for CredentialMode.INTERACTIVE."""
    mock_factory = MagicMock(return_value=MagicMock())
    original = auth_module._CREDENTIAL_REGISTRY[CredentialMode.INTERACTIVE]
    try:
        auth_module._CREDENTIAL_REGISTRY[CredentialMode.INTERACTIVE] = mock_factory
        get_credential(CredentialMode.INTERACTIVE)
        mock_factory.assert_called_once()
    finally:
        auth_module._CREDENTIAL_REGISTRY[CredentialMode.INTERACTIVE] = original


# ---------------------------------------------------------------------------
# SyncCredentialAdapter rename — backward-compat alias
# ---------------------------------------------------------------------------


def test_private_alias_is_same_class() -> None:
    """_SyncCredentialAdapter must be an alias for SyncCredentialAdapter."""
    assert _SyncCredentialAdapter is SyncCredentialAdapter


def test_sync_adapter_public_name() -> None:
    """SyncCredentialAdapter must be exported in __all__."""
    assert "SyncCredentialAdapter" in auth_module.__all__


# ---------------------------------------------------------------------------
# get_credential unknown mode — new explicit-error contract (registry refactor)
# ---------------------------------------------------------------------------


def test_get_credential_unknown_mode_raises_config_error() -> None:
    """get_credential must raise ConfigError for a mode not in the registry."""
    with pytest.raises(ConfigError, match="Unknown credential mode"):
        get_credential("not-a-real-mode")  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------
# GitHub Actions OIDC credential — _is_github_actions_oidc
# ---------------------------------------------------------------------------


def test_is_github_actions_oidc_true_when_both_vars_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "secret-runner-token")
    assert auth_module._is_github_actions_oidc() is True


def test_is_github_actions_oidc_false_when_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "secret-runner-token")
    assert auth_module._is_github_actions_oidc() is False


def test_is_github_actions_oidc_false_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    assert auth_module._is_github_actions_oidc() is False


def test_is_github_actions_oidc_false_when_both_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    assert auth_module._is_github_actions_oidc() is False


# ---------------------------------------------------------------------------
# GitHub Actions OIDC credential — _fetch_github_oidc_jwt
# ---------------------------------------------------------------------------


def test_fetch_github_oidc_jwt_returns_value_from_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fetch_github_oidc_jwt must GET the OIDC endpoint and return the JWT."""
    request_url = "https://pipelines.actions.githubusercontent.com/oidc/token"
    runner_token = "gha-runner-token-abc"  # noqa: S105
    expected_jwt = "eyJhbGciOiJSUzI1NiJ9.fake.jwt"

    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", request_url)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", runner_token)

    with respx.mock:
        route = respx.get(f"{request_url}&audience=api://AzureADTokenExchange").mock(
            return_value=httpx.Response(200, json={"value": expected_jwt})
        )
        result = auth_module._fetch_github_oidc_jwt()

    assert result == expected_jwt
    assert route.called
    sent_request = route.calls.last.request
    assert sent_request.headers["authorization"] == f"Bearer {runner_token}"


def test_fetch_github_oidc_jwt_sends_correct_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fetch_github_oidc_jwt must append audience=api://AzureADTokenExchange."""
    request_url = "https://pipelines.actions.githubusercontent.com/oidc/token?base=1"
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", request_url)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "tok")

    with respx.mock:
        route = respx.get(f"{request_url}&audience=api://AzureADTokenExchange").mock(
            return_value=httpx.Response(200, json={"value": "jwt-token"})
        )
        auth_module._fetch_github_oidc_jwt()

    # Confirm the audience param was appended (URL-encoded or raw both acceptable)
    sent_url = str(route.calls.last.request.url)
    assert (
        "audience=api%3A%2F%2FAzureADTokenExchange" in sent_url
        or "audience=api://AzureADTokenExchange" in sent_url
    )


def test_fetch_github_oidc_jwt_raises_config_error_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fetch_github_oidc_jwt must raise ConfigError when the endpoint returns non-2xx."""
    request_url = "https://pipelines.actions.githubusercontent.com/oidc/token"
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", request_url)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "tok")

    with respx.mock:
        respx.get(f"{request_url}&audience=api://AzureADTokenExchange").mock(
            return_value=httpx.Response(403, text="Forbidden")
        )
        with pytest.raises(ConfigError, match="403"):
            auth_module._fetch_github_oidc_jwt()


def test_fetch_github_oidc_jwt_raises_config_error_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fetch_github_oidc_jwt must raise ConfigError when env vars are absent."""
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="ACTIONS_ID_TOKEN_REQUEST_URL"):
        auth_module._fetch_github_oidc_jwt()


# ---------------------------------------------------------------------------
# GitHub Actions OIDC credential — get_credential DEFAULT mode routing
# ---------------------------------------------------------------------------


def test_get_credential_default_returns_client_assertion_in_github_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFAULT mode must return ClientAssertionCredential when OIDC env vars are present."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    monkeypatch.setenv("AZURE_TENANT_ID", "my-tenant-id")
    monkeypatch.setenv("AZURE_CLIENT_ID", "my-client-id")

    with patch("fabric_dw.auth.ClientAssertionCredential") as mock_cac:
        mock_cac.return_value = MagicMock(spec=ClientAssertionCredential)
        get_credential(CredentialMode.DEFAULT)
        mock_cac.assert_called_once()
        _, kwargs = mock_cac.call_args
        assert kwargs["tenant_id"] == "my-tenant-id"
        assert kwargs["client_id"] == "my-client-id"
        assert kwargs["func"] is auth_module._fetch_github_oidc_jwt


def test_get_credential_default_returns_default_azure_credential_outside_github_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFAULT mode must fall back to DefaultAzureCredential when OIDC env vars are absent."""
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)

    with patch("fabric_dw.auth.DefaultAzureCredential") as mock_dac:
        get_credential(CredentialMode.DEFAULT)
        mock_dac.assert_called_once()


def test_get_credential_default_raises_config_error_missing_tenant_id_in_github_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFAULT mode must raise ConfigError if AZURE_TENANT_ID is missing in GitHub Actions."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    monkeypatch.setenv("AZURE_CLIENT_ID", "my-client-id")
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)

    with pytest.raises(ConfigError, match="AZURE_TENANT_ID"):
        get_credential(CredentialMode.DEFAULT)


def test_get_credential_default_raises_config_error_missing_client_id_in_github_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFAULT mode must raise ConfigError if AZURE_CLIENT_ID is missing in GitHub Actions."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    monkeypatch.setenv("AZURE_TENANT_ID", "my-tenant-id")
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)

    with pytest.raises(ConfigError, match="AZURE_CLIENT_ID"):
        get_credential(CredentialMode.DEFAULT)


def test_get_credential_default_raises_config_error_missing_both_in_github_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFAULT mode must raise ConfigError if AZURE_TENANT_ID and AZURE_CLIENT_ID are missing."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)

    with pytest.raises(ConfigError):
        get_credential(CredentialMode.DEFAULT)
