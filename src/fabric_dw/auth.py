"""Azure credential chain with persistent token cache."""

import asyncio
import os
import struct
from collections.abc import Callable
from enum import StrEnum
from types import TracebackType
from typing import Protocol

import httpx
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity import (
    ClientAssertionCredential as SyncClientAssertionCredential,
)
from azure.identity import (
    InteractiveBrowserCredential,
    TokenCachePersistenceOptions,
)
from azure.identity.aio import (
    ClientSecretCredential,
    DefaultAzureCredential,
)

from fabric_dw.exceptions import ConfigError

FABRIC_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
SQL_SCOPE = "https://database.windows.net/.default"

#: Shared multi-tenant Entra app for the interactive browser sign-in path.
#: Users on any tenant can sign in without registering their own app.
#: Override with the ``FABRIC_INTERACTIVE_CLIENT_ID`` environment variable.
DEFAULT_INTERACTIVE_CLIENT_ID = "f666e5ee-2149-4c6a-87eb-13c9e1fdc70d"

_CACHE_OPTIONS = TokenCachePersistenceOptions(name="fabric-dw", allow_unencrypted_storage=True)

_GITHUB_OIDC_AUDIENCE = "api://AzureADTokenExchange"
_GITHUB_OIDC_TIMEOUT = 10  # seconds

__all__ = [
    "DEFAULT_INTERACTIVE_CLIENT_ID",
    "FABRIC_SCOPE",
    "SQL_SCOPE",
    "CredentialMode",
    "SyncCredentialAdapter",
    "get_credential",
    "get_sql_token_struct",
]


class CredentialMode(StrEnum):
    DEFAULT = "default"
    SERVICE_PRINCIPAL = "sp"
    INTERACTIVE = "interactive"


class _CloseableTokenCredential(Protocol):
    """A synchronous token credential that also supports close()."""

    def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: object,
    ) -> AccessToken: ...

    def close(self) -> None: ...


class SyncCredentialAdapter:
    """Adapts a synchronous TokenCredential to the AsyncTokenCredential protocol.

    Used because ``azure.identity.aio.InteractiveBrowserCredential`` does not
    exist in azure-identity 1.25.x, and because ``azure.identity.aio.ClientAssertionCredential``
    invokes its ``func`` callback bare on the event loop (no ``asyncio.to_thread`` wrapping)
    in azure-identity 1.25.x.  Wrapping a synchronous credential here ensures that both
    ``get_token`` and ``close`` are offloaded to a worker thread via
    :func:`asyncio.to_thread` so the event loop is never blocked.

    Remove the ``InteractiveBrowserCredential`` usage of this adapter and switch to
    ``azure.identity.aio.InteractiveBrowserCredential`` once that ships in a stable release.
    """

    def __init__(self, inner: _CloseableTokenCredential) -> None:
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


def _is_github_actions_oidc() -> bool:
    """Return True when GitHub Actions OIDC env vars are present.

    Both ``ACTIONS_ID_TOKEN_REQUEST_URL`` and ``ACTIONS_ID_TOKEN_REQUEST_TOKEN``
    must be set (non-empty) for the OIDC endpoint to be usable.
    """
    return bool(
        os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
        and os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    )


def _fetch_github_oidc_jwt() -> str:
    """Fetch a fresh GitHub Actions OIDC JWT.

    This is a *synchronous* callable because ``azure.identity.ClientAssertionCredential``
    (sync variant) expects ``func: Callable[[], str]``.  The whole credential is wrapped
    in :class:`SyncCredentialAdapter`, which offloads ``get_token`` (and therefore this
    function) to a worker thread via :func:`asyncio.to_thread`, keeping the event loop free.

    It is invoked by azure-identity on every token acquisition (not on every HTTP
    request), so the blocking GET runs safely off the event loop.

    Returns:
        The OIDC JWT string from the GitHub token endpoint.

    Raises:
        ConfigError: If the required environment variables are missing or the
            OIDC endpoint returns a non-2xx response.
    """
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not request_url or not request_token:
        raise ConfigError(
            "GitHub Actions OIDC environment variables are not set. "
            "Ensure the workflow has 'permissions: id-token: write' and that "
            "ACTIONS_ID_TOKEN_REQUEST_URL and ACTIONS_ID_TOKEN_REQUEST_TOKEN are available."
        )

    # Build the final URL by appending audience= to any existing query params.
    # Using httpx.URL.copy_add_param preserves params already in the URL
    # (e.g. api-version=2.0 that GitHub always includes), whereas passing
    # params= to httpx.get() would replace the entire query string.
    url = httpx.URL(request_url).copy_add_param("audience", _GITHUB_OIDC_AUDIENCE)
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {request_token}"},
            timeout=_GITHUB_OIDC_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ConfigError(
            f"GitHub OIDC token endpoint returned HTTP {exc.response.status_code}."
        ) from exc
    except httpx.RequestError as exc:
        raise ConfigError(f"Failed to reach GitHub OIDC token endpoint: {exc}.") from exc

    return str(response.json()["value"])


def _make_github_oidc_credential() -> AsyncTokenCredential:
    """Build a self-refreshing ClientAssertionCredential backed by GitHub OIDC.

    Uses the *synchronous* ``azure.identity.ClientAssertionCredential`` wrapped in
    :class:`SyncCredentialAdapter`.  This ensures that the blocking ``_fetch_github_oidc_jwt``
    HTTP call runs in a worker thread (via :func:`asyncio.to_thread`) rather than on the
    event loop — the aio variant calls its ``func`` bare on the event loop in azure-identity
    1.25.x.

    Each time azure-identity needs to acquire or refresh an access token it calls *func*
    to obtain a fresh client assertion.  Because a GitHub OIDC JWT is only valid for ~5
    minutes, fetching a new one on every call prevents
    ``AADSTS700024: Client assertion is not within its valid time range`` errors that
    would otherwise terminate long-running CI jobs.

    Raises:
        ConfigError: If AZURE_TENANT_ID or AZURE_CLIENT_ID are missing.
    """
    missing = [name for name in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID") if not os.environ.get(name)]
    if missing:
        raise ConfigError(
            f"GitHub Actions OIDC credential requires {', '.join(missing)} "
            f"to be set in the environment."
        )

    return SyncCredentialAdapter(
        SyncClientAssertionCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            func=_fetch_github_oidc_jwt,
        )
    )


# Module-level cached sync credential for SQL token injection under GitHub OIDC.
# Caching allows azure-identity's in-memory token cache to work across connects,
# so tokens are only re-acquired when they genuinely expire — NOT on every connect.
_sql_oidc_credential: SyncClientAssertionCredential | None = None


def _get_sql_oidc_credential() -> SyncClientAssertionCredential:
    """Return (creating once) the sync OIDC credential used for SQL token injection."""
    global _sql_oidc_credential  # noqa: PLW0603
    if _sql_oidc_credential is None:
        missing = [
            name for name in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID") if not os.environ.get(name)
        ]
        if missing:
            raise ConfigError(
                f"GitHub Actions OIDC credential requires {', '.join(missing)} "
                f"to be set in the environment."
            )
        _sql_oidc_credential = SyncClientAssertionCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            func=_fetch_github_oidc_jwt,
        )
    return _sql_oidc_credential


def get_sql_token_struct(mode: CredentialMode = CredentialMode.DEFAULT) -> bytes | None:
    """Return a packed ODBC SQL access-token struct for injection under GitHub OIDC.

    When running under GitHub Actions OIDC (both ``ACTIONS_ID_TOKEN_REQUEST_URL``
    and ``ACTIONS_ID_TOKEN_REQUEST_TOKEN`` are set), acquires a fresh SQL access
    token from a module-level cached :class:`~azure.identity.ClientAssertionCredential`
    backed by :func:`_fetch_github_oidc_jwt` and packs it in the format required
    by the ``SQL_COPT_SS_ACCESS_TOKEN`` ODBC connection attribute (attribute 1256):
    a 4-byte little-endian length prefix followed by the token encoded as UTF-16-LE.

    When NOT under OIDC (local dev / interactive), returns ``None`` so the caller
    falls back to the ``Authentication=`` connection-string key.

    The ``mode`` parameter is accepted for API symmetry but is ignored on the OIDC
    path — the CI service-principal identity is fixed by the GitHub Actions environment.

    Args:
        mode: Ignored on the OIDC path; kept for symmetry with other auth helpers.

    Returns:
        Packed token bytes when running under GitHub Actions OIDC, otherwise ``None``.

    Raises:
        ConfigError: If OIDC is detected but AZURE_TENANT_ID or AZURE_CLIENT_ID
            are missing from the environment.
    """
    # Suppress unused-arg warning: mode is intentionally ignored on OIDC path.
    _ = mode

    if not _is_github_actions_oidc():
        return None

    credential = _get_sql_oidc_credential()
    # Call get_token on every connect — the credential's own in-memory cache returns
    # a cached access token when it is still valid; _fetch_github_oidc_jwt is only
    # invoked when the assertion itself needs refreshing (every ~5 min in CI).
    # We must NOT cache the resulting struct here — let the credential decide expiry.
    token = credential.get_token(SQL_SCOPE).token
    token_bytes = token.encode("UTF-16-LE")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


def _make_default_credential() -> AsyncTokenCredential:
    if _is_github_actions_oidc():
        return _make_github_oidc_credential()

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
