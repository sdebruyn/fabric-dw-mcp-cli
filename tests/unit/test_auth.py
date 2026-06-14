"""Tests for fabric_dw.auth — written before implementation (TDD)."""

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from azure.identity import ClientAssertionCredential as SyncClientAssertionCredential
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
    """_fetch_github_oidc_jwt must GET the OIDC endpoint and return the JWT.

    Uses a realistic GitHub Actions URL that already contains a query string
    (api-version=2.0) to verify robust URL parameter handling.
    """
    # Real GitHub Actions URLs always include ?api-version=2.0
    request_url = "https://pipelines.actions.githubusercontent.com/serviceHosts/abc/_apis/distributedtask/hubs/Gates/plans/def/jobs/ghi/idtoken?api-version=2.0"
    runner_token = "gha-runner-token-abc"  # noqa: S105
    expected_jwt = "eyJhbGciOiJSUzI1NiJ9.fake.jwt"

    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", request_url)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", runner_token)

    with respx.mock:
        # The audience param must be appended alongside the existing api-version param
        route = respx.get(
            "https://pipelines.actions.githubusercontent.com/serviceHosts/abc/_apis/distributedtask/hubs/Gates/plans/def/jobs/ghi/idtoken",
            params={"api-version": "2.0", "audience": "api://AzureADTokenExchange"},
        ).mock(return_value=httpx.Response(200, json={"value": expected_jwt}))
        result = auth_module._fetch_github_oidc_jwt()

    assert result == expected_jwt
    assert route.called
    sent_request = route.calls.last.request
    assert sent_request.headers["authorization"] == f"Bearer {runner_token}"
    # Confirm both query params are present in the final URL
    sent_url = str(sent_request.url)
    assert "api-version=2.0" in sent_url
    assert (
        "audience=api%3A%2F%2FAzureADTokenExchange" in sent_url
        or "audience=api://AzureADTokenExchange" in sent_url
    )


def test_fetch_github_oidc_jwt_sends_correct_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fetch_github_oidc_jwt must append audience=api://AzureADTokenExchange."""
    request_url = "https://pipelines.actions.githubusercontent.com/oidc/token?api-version=2.0"
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", request_url)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "tok")

    with respx.mock:
        route = respx.get(
            "https://pipelines.actions.githubusercontent.com/oidc/token",
            params={"api-version": "2.0", "audience": "api://AzureADTokenExchange"},
        ).mock(return_value=httpx.Response(200, json={"value": "jwt-token"}))
        auth_module._fetch_github_oidc_jwt()

    # Confirm both params are present — audience added via copy_add_param preserving api-version
    sent_url = str(route.calls.last.request.url)
    assert "audience=api%3A%2F%2FAzureADTokenExchange" in sent_url
    assert "api-version=2.0" in sent_url


def test_fetch_github_oidc_jwt_raises_config_error_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fetch_github_oidc_jwt must raise ConfigError when the endpoint returns non-2xx."""
    request_url = "https://pipelines.actions.githubusercontent.com/oidc/token?api-version=2.0"
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", request_url)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "tok")

    with respx.mock:
        respx.get(
            "https://pipelines.actions.githubusercontent.com/oidc/token",
            params={"api-version": "2.0", "audience": "api://AzureADTokenExchange"},
        ).mock(return_value=httpx.Response(403, text="Forbidden"))
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


def test_get_credential_default_returns_sync_adapter_wrapping_client_assertion_in_github_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFAULT mode returns SyncCredentialAdapter(SyncClientAssertionCredential) in GHA.

    The sync credential is used (not the aio variant) so the blocking _fetch_github_oidc_jwt
    runs in a worker thread via SyncCredentialAdapter, keeping the event loop free.
    """
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    monkeypatch.setenv("AZURE_TENANT_ID", "my-tenant-id")
    monkeypatch.setenv("AZURE_CLIENT_ID", "my-client-id")

    with patch("fabric_dw.auth.SyncClientAssertionCredential") as mock_cac:
        mock_cac.return_value = MagicMock(spec=SyncClientAssertionCredential)
        credential = get_credential(CredentialMode.DEFAULT)
        mock_cac.assert_called_once()
        _, kwargs = mock_cac.call_args
        assert kwargs["tenant_id"] == "my-tenant-id"
        assert kwargs["client_id"] == "my-client-id"
        assert kwargs["func"] is auth_module._fetch_github_oidc_jwt
        # The result must be a SyncCredentialAdapter wrapping the sync credential
        assert isinstance(credential, SyncCredentialAdapter)


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


# ---------------------------------------------------------------------------
# get_sql_token_struct — SQL access-token injection for GitHub OIDC
# ---------------------------------------------------------------------------


def test_get_sql_token_struct_exported_in_all() -> None:
    assert "get_sql_token_struct" in auth_module.__all__


def test_get_sql_token_struct_returns_none_outside_github_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When not under OIDC, get_sql_token_struct must return None."""
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    from fabric_dw.auth import get_sql_token_struct  # noqa: PLC0415

    result = get_sql_token_struct()
    assert result is None


def test_get_sql_token_struct_mode_param_ignored_outside_oidc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mode parameter must not affect the None return outside OIDC."""
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    from fabric_dw.auth import get_sql_token_struct  # noqa: PLC0415

    result = get_sql_token_struct(CredentialMode.SERVICE_PRINCIPAL)
    assert result is None


def test_get_sql_token_struct_packs_token_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_sql_token_struct must pack the token as 4-byte LE length + UTF-16-LE bytes."""
    import struct  # noqa: PLC0415

    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    monkeypatch.setenv("AZURE_TENANT_ID", "my-tenant-id")
    monkeypatch.setenv("AZURE_CLIENT_ID", "my-client-id")

    # Reset cached credential so monkeypatched env vars take effect.
    import fabric_dw.auth as _auth_module  # noqa: PLC0415

    _auth_module._sql_oidc_credential = None

    known_token = "eyJhbGciOiJSUzI1NiJ9.test-token"  # noqa: S105
    mock_access_token = AccessToken(known_token, 9999999999)

    with patch("fabric_dw.auth.SyncClientAssertionCredential") as mock_cred_class:
        mock_cred_instance = MagicMock()
        mock_cred_instance.get_token.return_value = mock_access_token
        mock_cred_class.return_value = mock_cred_instance

        result = _auth_module.get_sql_token_struct()

    assert result is not None
    token_bytes = known_token.encode("UTF-16-LE")
    expected = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    assert result == expected
    # Verify correct scope was requested.
    mock_cred_instance.get_token.assert_called_once_with(SQL_SCOPE)


def test_get_sql_token_struct_struct_format_4_byte_le_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The packed struct must start with a 4-byte little-endian token length."""
    import struct  # noqa: PLC0415

    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    monkeypatch.setenv("AZURE_TENANT_ID", "my-tenant-id")
    monkeypatch.setenv("AZURE_CLIENT_ID", "my-client-id")

    import fabric_dw.auth as _auth_module  # noqa: PLC0415

    _auth_module._sql_oidc_credential = None

    known_token = "test"  # noqa: S105
    mock_access_token = AccessToken(known_token, 9999999999)

    with patch("fabric_dw.auth.SyncClientAssertionCredential") as mock_cred_class:
        mock_cred_instance = MagicMock()
        mock_cred_instance.get_token.return_value = mock_access_token
        mock_cred_class.return_value = mock_cred_instance

        result = _auth_module.get_sql_token_struct()

    assert result is not None
    length_field = struct.unpack_from("<I", result, 0)[0]
    token_bytes = known_token.encode("UTF-16-LE")
    assert length_field == len(token_bytes)
    assert result[4:] == token_bytes


def test_get_sql_token_struct_raises_config_error_missing_tenant_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_sql_token_struct must raise ConfigError if AZURE_TENANT_ID is missing."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    monkeypatch.setenv("AZURE_CLIENT_ID", "my-client-id")
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)

    import fabric_dw.auth as _auth_module  # noqa: PLC0415

    _auth_module._sql_oidc_credential = None
    from fabric_dw.auth import get_sql_token_struct  # noqa: PLC0415

    with pytest.raises(ConfigError, match="AZURE_TENANT_ID"):
        get_sql_token_struct()


def test_get_sql_token_struct_raises_config_error_missing_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_sql_token_struct must raise ConfigError if AZURE_CLIENT_ID is missing."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    monkeypatch.setenv("AZURE_TENANT_ID", "my-tenant-id")
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)

    import fabric_dw.auth as _auth_module  # noqa: PLC0415
    from fabric_dw.auth import get_sql_token_struct  # noqa: PLC0415

    _auth_module._sql_oidc_credential = None

    with pytest.raises(ConfigError, match="AZURE_CLIENT_ID"):
        get_sql_token_struct()


def test_get_sql_token_struct_caches_credential_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sync credential must be created once and reused across multiple calls."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    monkeypatch.setenv("AZURE_TENANT_ID", "my-tenant-id")
    monkeypatch.setenv("AZURE_CLIENT_ID", "my-client-id")

    import fabric_dw.auth as _auth_module  # noqa: PLC0415

    _auth_module._sql_oidc_credential = None

    known_token = "my-sql-token"  # noqa: S105
    mock_access_token = AccessToken(known_token, 9999999999)

    with patch("fabric_dw.auth.SyncClientAssertionCredential") as mock_cred_class:
        mock_cred_instance = MagicMock()
        mock_cred_instance.get_token.return_value = mock_access_token
        mock_cred_class.return_value = mock_cred_instance

        _auth_module.get_sql_token_struct()
        _auth_module.get_sql_token_struct()

    # Credential class must be instantiated only once across multiple calls.
    mock_cred_class.assert_called_once()
    # get_token must be called each time (fresh struct per call).
    assert mock_cred_instance.get_token.call_count == 2


# ---------------------------------------------------------------------------
# C23 — SyncCredentialAdapter.get_token forwards extra **kwargs (bug fix)
# ---------------------------------------------------------------------------


async def test_sync_adapter_get_token_forwards_extra_kwargs() -> None:
    """Extra **kwargs must be forwarded to the inner get_token (C23).

    azure-identity may pass additional keyword args (e.g. a future ``pop_key``
    parameter) that this adapter must not silently drop.
    """
    inner = MagicMock()
    inner.get_token.return_value = AccessToken("tok", 9999999999)
    adapter = SyncCredentialAdapter(inner)

    await adapter.get_token(
        FABRIC_SCOPE, claims="custom-claim", tenant_id="t1", enable_cae=True, pop_key="pk"
    )

    inner.get_token.assert_called_once_with(
        FABRIC_SCOPE,
        claims="custom-claim",
        tenant_id="t1",
        enable_cae=True,
        pop_key="pk",
    )


async def test_sync_adapter_get_token_dispatches_extra_kwargs_via_to_thread() -> None:
    """asyncio.to_thread must receive the forwarded extra kwargs (C23)."""
    inner = MagicMock()
    inner.get_token.return_value = AccessToken("tok", 9999999999)
    adapter = SyncCredentialAdapter(inner)

    with patch("fabric_dw.auth.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = AccessToken("tok", 9999999999)
        await adapter.get_token(FABRIC_SCOPE, future_param="x")
        mock_to_thread.assert_called_once_with(
            inner.get_token,
            FABRIC_SCOPE,
            claims=None,
            tenant_id=None,
            enable_cae=False,
            future_param="x",
        )


# ---------------------------------------------------------------------------
# C05 — token cache does not silently fall back to unencrypted storage
# ---------------------------------------------------------------------------


def test_build_cache_options_default_does_not_allow_unencrypted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_build_cache_options must default to allow_unencrypted_storage=False (C05).

    Unencrypted persistence must NOT be enabled without an explicit opt-in.
    """
    monkeypatch.delenv("FABRIC_ALLOW_UNENCRYPTED_TOKEN_CACHE", raising=False)
    opts = auth_module._build_cache_options()
    # TokenCachePersistenceOptions stores the flag as allow_unencrypted_storage.
    assert opts.allow_unencrypted_storage is False


def test_build_cache_options_opt_in_allows_unencrypted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting FABRIC_ALLOW_UNENCRYPTED_TOKEN_CACHE=1 enables plaintext fallback (C05)."""
    monkeypatch.setenv("FABRIC_ALLOW_UNENCRYPTED_TOKEN_CACHE", "1")
    opts = auth_module._build_cache_options()
    assert opts.allow_unencrypted_storage is True


def test_build_cache_options_opt_in_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A clear WARNING must be emitted when unencrypted cache is opted-in (C05)."""
    import logging  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_ALLOW_UNENCRYPTED_TOKEN_CACHE", "1")
    with caplog.at_level(logging.WARNING, logger="fabric_dw.auth"):
        auth_module._build_cache_options()
    assert any("FABRIC_ALLOW_UNENCRYPTED_TOKEN_CACHE" in r.message for r in caplog.records)
    assert any("WARNING" in r.levelname or r.levelno >= logging.WARNING for r in caplog.records)


def test_build_cache_options_no_warning_when_not_set(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No warning must be emitted when the env var is absent (C05)."""
    import logging  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_ALLOW_UNENCRYPTED_TOKEN_CACHE", raising=False)
    with caplog.at_level(logging.WARNING, logger="fabric_dw.auth"):
        auth_module._build_cache_options()
    assert not any("FABRIC_ALLOW_UNENCRYPTED_TOKEN_CACHE" in r.message for r in caplog.records)


def test_build_cache_options_falsy_values_do_not_enable_unencrypted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Values other than '1' must not enable unencrypted storage (C05)."""
    for falsy in ("0", "false", "yes", "true", ""):
        monkeypatch.setenv("FABRIC_ALLOW_UNENCRYPTED_TOKEN_CACHE", falsy)
        opts = auth_module._build_cache_options()
        assert opts.allow_unencrypted_storage is False, f"Expected False for value {falsy!r}"


# ---------------------------------------------------------------------------
# C21 — _fetch_github_oidc_jwt guards the response body
# ---------------------------------------------------------------------------


def test_fetch_github_oidc_jwt_raises_config_error_on_missing_value_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fetch_github_oidc_jwt must raise ConfigError when 'value' key is absent (C21)."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://pipelines.example.com/token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-tok")

    with respx.mock:
        respx.get("https://pipelines.example.com/token").mock(
            return_value=httpx.Response(200, json={"not_value": "something"})
        )
        with pytest.raises(ConfigError, match="value"):
            auth_module._fetch_github_oidc_jwt()


def test_fetch_github_oidc_jwt_raises_config_error_on_non_string_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fetch_github_oidc_jwt must raise ConfigError when 'value' is not a string (C21)."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://pipelines.example.com/token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-tok")

    with respx.mock:
        respx.get("https://pipelines.example.com/token").mock(
            return_value=httpx.Response(200, json={"value": 12345})
        )
        with pytest.raises(ConfigError, match="non-string"):
            auth_module._fetch_github_oidc_jwt()


def test_fetch_github_oidc_jwt_raises_config_error_on_empty_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fetch_github_oidc_jwt must raise ConfigError on non-JSON or empty body (C21)."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://pipelines.example.com/token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-tok")

    with respx.mock:
        respx.get("https://pipelines.example.com/token").mock(
            return_value=httpx.Response(200, text="not-json")
        )
        with pytest.raises(ConfigError):
            auth_module._fetch_github_oidc_jwt()


# ---------------------------------------------------------------------------
# C22 — ConfigError is not a FabricError
# ---------------------------------------------------------------------------


def test_config_error_is_not_caught_by_fabric_error_handler() -> None:
    """ConfigError must NOT be caught by 'except FabricError' handlers (C22).

    Broad except-FabricError blocks in MCP tool / CLI call sites must not
    silently absorb local configuration problems.
    """
    from fabric_dw.exceptions import ConfigError, FabricError  # noqa: PLC0415

    assert not issubclass(ConfigError, FabricError)
