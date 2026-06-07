"""Tests for fabric_dw.auth — written before implementation (TDD)."""

import threading
from unittest.mock import MagicMock

import pytest
from azure.core.credentials import AccessToken
from azure.identity import (
    ClientSecretCredential,
    DefaultAzureCredential,
    InteractiveBrowserCredential,
)

from fabric_dw.auth import (
    FABRIC_SCOPE,
    SQL_SCOPE,
    CredentialMode,
    get_credential,
    get_token,
)
from fabric_dw.exceptions import ConfigError


def test_fabric_scope_constant() -> None:
    assert FABRIC_SCOPE == "https://analysis.windows.net/powerbi/api/.default"


def test_sql_scope_constant() -> None:
    assert SQL_SCOPE == "https://database.windows.net/.default"


def test_get_credential_default_returns_default_azure_credential() -> None:
    credential = get_credential(CredentialMode.DEFAULT)
    assert isinstance(credential, DefaultAzureCredential)


def test_get_credential_default_is_default_argument() -> None:
    credential = get_credential()
    assert isinstance(credential, DefaultAzureCredential)


def test_get_credential_service_principal_returns_client_secret_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_TENANT_ID", "test-tenant-id")
    monkeypatch.setenv("AZURE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "test-client-secret")

    credential = get_credential(CredentialMode.SERVICE_PRINCIPAL)
    assert isinstance(credential, ClientSecretCredential)


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


def test_get_credential_interactive_returns_interactive_browser_credential() -> None:
    credential = get_credential(CredentialMode.INTERACTIVE)
    assert isinstance(credential, InteractiveBrowserCredential)


async def test_get_token_calls_credential_get_token_once() -> None:
    mock_token = AccessToken(token="test-token", expires_on=9999999999)  # noqa: S106
    mock_credential = MagicMock()
    mock_credential.get_token.return_value = mock_token

    result = await get_token(mock_credential, FABRIC_SCOPE)

    mock_credential.get_token.assert_called_once_with(FABRIC_SCOPE)
    assert result is mock_token


async def test_get_token_returns_access_token_unchanged() -> None:
    mock_token = AccessToken(token="my-access-token", expires_on=1234567890)  # noqa: S106
    mock_credential = MagicMock()
    mock_credential.get_token.return_value = mock_token

    result = await get_token(mock_credential, SQL_SCOPE)

    assert result.token == "my-access-token"  # noqa: S105
    assert result.expires_on == 1234567890


async def test_get_token_runs_on_non_main_thread() -> None:
    main_thread_id = threading.get_ident()
    captured_thread_ids: list[int] = []

    def side_effect(_scope: str) -> AccessToken:
        captured_thread_ids.append(threading.get_ident())
        return AccessToken(token="tok", expires_on=9999)  # noqa: S106

    mock_credential = MagicMock()
    mock_credential.get_token.side_effect = side_effect

    await get_token(mock_credential, FABRIC_SCOPE)

    assert len(captured_thread_ids) == 1
    assert captured_thread_ids[0] != main_thread_id
