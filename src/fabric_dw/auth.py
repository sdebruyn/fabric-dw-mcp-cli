"""Azure credential chain with persistent token cache."""

import asyncio
import os
from enum import StrEnum

from azure.core.credentials import AccessToken, TokenCredential
from azure.identity import (
    ClientSecretCredential,
    DefaultAzureCredential,
    InteractiveBrowserCredential,
    TokenCachePersistenceOptions,
)

from fabric_dw.exceptions import ConfigError

FABRIC_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
SQL_SCOPE = "https://database.windows.net/.default"

#: Shared multi-tenant Entra app for the interactive browser sign-in path.
#: Users on any tenant can sign in without registering their own app.
#: Override with the ``FABRIC_INTERACTIVE_CLIENT_ID`` environment variable.
DEFAULT_INTERACTIVE_CLIENT_ID = "f666e5ee-2149-4c6a-87eb-13c9e1fdc70d"

_CACHE_OPTIONS = TokenCachePersistenceOptions(name="fabric-dw", allow_unencrypted_storage=True)


class CredentialMode(StrEnum):
    DEFAULT = "default"
    SERVICE_PRINCIPAL = "sp"
    INTERACTIVE = "interactive"


def _resolve_interactive_kwargs() -> dict[str, str]:
    """Build keyword arguments for the interactive browser credential path.

    Reads ``FABRIC_INTERACTIVE_CLIENT_ID`` (defaults to the shared app) and
    ``FABRIC_INTERACTIVE_TENANT_ID`` (omitted when not set) from the environment.

    Returns:
        A dict with at least ``client_id`` and optionally ``tenant_id``.
    """
    kwargs: dict[str, str] = {
        "client_id": os.environ.get("FABRIC_INTERACTIVE_CLIENT_ID", DEFAULT_INTERACTIVE_CLIENT_ID)
    }
    tenant = os.environ.get("FABRIC_INTERACTIVE_TENANT_ID")
    if tenant:
        kwargs["tenant_id"] = tenant
    return kwargs


def get_credential(mode: CredentialMode = CredentialMode.DEFAULT) -> TokenCredential:
    """Return an Azure credential for the given mode.

    Args:
        mode: The credential mode to use. Defaults to DEFAULT.

    Returns:
        A TokenCredential appropriate for the given mode.

    Raises:
        ConfigError: If mode is SERVICE_PRINCIPAL and any of AZURE_TENANT_ID,
            AZURE_CLIENT_ID, or AZURE_CLIENT_SECRET are missing from the environment.
    """
    if mode == CredentialMode.DEFAULT:
        interactive_kwargs = _resolve_interactive_kwargs()
        dac_kwargs: dict[str, object] = {
            "cache_persistence_options": _CACHE_OPTIONS,
            "exclude_interactive_browser_credential": False,
            "interactive_browser_client_id": interactive_kwargs["client_id"],
        }
        if "tenant_id" in interactive_kwargs:
            dac_kwargs["interactive_browser_tenant_id"] = interactive_kwargs["tenant_id"]
        return DefaultAzureCredential(**dac_kwargs)

    if mode == CredentialMode.SERVICE_PRINCIPAL:
        env_vars = {
            "AZURE_TENANT_ID": os.environ.get("AZURE_TENANT_ID"),
            "AZURE_CLIENT_ID": os.environ.get("AZURE_CLIENT_ID"),
            "AZURE_CLIENT_SECRET": os.environ.get("AZURE_CLIENT_SECRET"),
        }
        missing = [name for name, value in env_vars.items() if not value]
        if missing:
            raise ConfigError.missing_env_vars(missing)

        return ClientSecretCredential(
            tenant_id=env_vars["AZURE_TENANT_ID"] or "",
            client_id=env_vars["AZURE_CLIENT_ID"] or "",
            client_secret=env_vars["AZURE_CLIENT_SECRET"] or "",
        )

    # CredentialMode.INTERACTIVE
    return InteractiveBrowserCredential(
        cache_persistence_options=_CACHE_OPTIONS,
        **_resolve_interactive_kwargs(),
    )


async def get_token(credential: TokenCredential, scope: str) -> AccessToken:
    """Retrieve an access token from the credential asynchronously.

    Wraps the synchronous ``credential.get_token`` call in ``asyncio.to_thread``
    so it does not block the event loop.

    Args:
        credential: The Azure credential to use.
        scope: The OAuth2 scope to request a token for.

    Returns:
        The raw AccessToken returned by the credential.
    """
    return await asyncio.to_thread(credential.get_token, scope)
