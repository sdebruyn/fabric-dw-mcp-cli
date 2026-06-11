"""Azure credential chain with persistent token cache."""

import asyncio
import os
from collections.abc import Callable
from enum import StrEnum
from types import TracebackType

from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity import (
    InteractiveBrowserCredential,
    TokenCachePersistenceOptions,
)
from azure.identity.aio import ClientSecretCredential, DefaultAzureCredential

from fabric_dw.exceptions import ConfigError

FABRIC_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
SQL_SCOPE = "https://database.windows.net/.default"

#: Shared multi-tenant Entra app for the interactive browser sign-in path.
#: Users on any tenant can sign in without registering their own app.
#: Override with the ``FABRIC_INTERACTIVE_CLIENT_ID`` environment variable.
DEFAULT_INTERACTIVE_CLIENT_ID = "f666e5ee-2149-4c6a-87eb-13c9e1fdc70d"

_CACHE_OPTIONS = TokenCachePersistenceOptions(name="fabric-dw", allow_unencrypted_storage=True)

__all__ = [
    "DEFAULT_INTERACTIVE_CLIENT_ID",
    "FABRIC_SCOPE",
    "SQL_SCOPE",
    "CredentialMode",
    "SyncCredentialAdapter",
    "get_credential",
]


class CredentialMode(StrEnum):
    DEFAULT = "default"
    SERVICE_PRINCIPAL = "sp"
    INTERACTIVE = "interactive"


class SyncCredentialAdapter:
    """Adapts a synchronous TokenCredential to the AsyncTokenCredential protocol.

    Used because ``azure.identity.aio.InteractiveBrowserCredential`` does not
    exist in azure-identity 1.25.x.  Both ``get_token`` and ``close`` are
    offloaded to a worker thread via :func:`asyncio.to_thread` so the event
    loop is never blocked.

    Remove this adapter and switch to ``azure.identity.aio.InteractiveBrowserCredential``
    once that ships in a stable release.
    """

    def __init__(self, inner: InteractiveBrowserCredential) -> None:
        self._inner = inner

    async def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: object,  # noqa: ARG002 — required by AsyncTokenCredential protocol
    ) -> AccessToken:
        return await asyncio.to_thread(
            self._inner.get_token,
            *scopes,
            claims=claims,
            tenant_id=tenant_id,
            enable_cae=enable_cae,
        )

    async def close(self) -> None:
        await asyncio.to_thread(self._inner.close)

    async def __aenter__(self) -> "SyncCredentialAdapter":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self.close()


# Keep a private alias so existing internal call sites and any external code
# that still references the old private name continue to work.
_SyncCredentialAdapter = SyncCredentialAdapter


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


def _make_default_credential() -> AsyncTokenCredential:
    interactive_kwargs = _resolve_interactive_kwargs()
    dac_kwargs: dict[str, object] = {
        "cache_persistence_options": _CACHE_OPTIONS,
        "exclude_interactive_browser_credential": False,
        "interactive_browser_client_id": interactive_kwargs["client_id"],
    }
    if "tenant_id" in interactive_kwargs:
        dac_kwargs["interactive_browser_tenant_id"] = interactive_kwargs["tenant_id"]
    return DefaultAzureCredential(**dac_kwargs)


def _make_service_principal_credential() -> AsyncTokenCredential:
    env_vars = {
        "AZURE_TENANT_ID": os.environ.get("AZURE_TENANT_ID"),
        "AZURE_CLIENT_ID": os.environ.get("AZURE_CLIENT_ID"),
        "AZURE_CLIENT_SECRET": os.environ.get("AZURE_CLIENT_SECRET"),
    }
    missing = [name for name, value in env_vars.items() if not value]
    if missing:
        raise ConfigError.missing_env_vars(missing)

    # Values are guaranteed non-empty by the missing check above; the dict
    # lookup and cast are safe — they only reach this point when all three
    # vars are present and non-empty.
    return ClientSecretCredential(
        tenant_id=str(env_vars["AZURE_TENANT_ID"]),
        client_id=str(env_vars["AZURE_CLIENT_ID"]),
        client_secret=str(env_vars["AZURE_CLIENT_SECRET"]),
    )


def _make_interactive_credential() -> AsyncTokenCredential:
    # InteractiveBrowserCredential has no aio variant in this release of
    # azure-identity; wrap in an adapter that offloads to a worker thread.
    return SyncCredentialAdapter(
        InteractiveBrowserCredential(
            cache_persistence_options=_CACHE_OPTIONS,
            **_resolve_interactive_kwargs(),
        )
    )


#: Registry mapping each CredentialMode to a factory that produces the
#: appropriate AsyncTokenCredential.  To add a new mode, register a new
#: factory here — no other code needs to change.
_CREDENTIAL_REGISTRY: dict[CredentialMode, Callable[[], AsyncTokenCredential]] = {
    CredentialMode.DEFAULT: _make_default_credential,
    CredentialMode.SERVICE_PRINCIPAL: _make_service_principal_credential,
    CredentialMode.INTERACTIVE: _make_interactive_credential,
}


def get_credential(mode: CredentialMode = CredentialMode.DEFAULT) -> AsyncTokenCredential:
    """Return an Azure credential for the given mode.

    Args:
        mode: The credential mode to use. Defaults to DEFAULT.

    Returns:
        An AsyncTokenCredential appropriate for the given mode.

    Raises:
        ConfigError: If mode is SERVICE_PRINCIPAL and any of AZURE_TENANT_ID,
            AZURE_CLIENT_ID, or AZURE_CLIENT_SECRET are missing from the environment.
        ConfigError: If *mode* is not a recognised :class:`CredentialMode`.
    """
    factory = _CREDENTIAL_REGISTRY.get(mode)
    if factory is None:
        raise ConfigError.unknown_credential_mode(mode)
    return factory()
