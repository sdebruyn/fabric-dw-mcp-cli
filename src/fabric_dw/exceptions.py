from __future__ import annotations

import warnings


class FabricCliError(Exception):
    """Common base for all fabric-dw exceptions raised to the CLI / MCP boundary.

    Both :class:`FabricError` (HTTP/API errors) and :class:`ConfigError` (local
    configuration errors) inherit from this class so that a single broad catch at
    the CLI or MCP boundary can present both cleanly without collapsing the two
    distinct semantic hierarchies.
    """


class FabricError(FabricCliError):
    """Base error for all fabric-dw HTTP/API errors.

    Attributes:
        status:     HTTP status code that triggered this error, or None.
        request_id: Value of the ``x-ms-request-id`` response header, or None.
        body:       Best-effort parsed JSON response body, or None.
        hint:       Optional human-readable remediation hint.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
        body: dict[str, object] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.request_id = request_id
        self.body = body
        self.hint = hint

    def __str__(self) -> str:
        msg = super().__str__()
        if self.hint:
            msg = f"{msg}\nHint: {self.hint}"
        if self.request_id:
            msg = f"{msg} (request-id: {self.request_id})"
        return msg


class AuthError(FabricError):
    """Raised on HTTP 401 - authentication token missing or invalid."""


class PermissionDeniedError(FabricError):
    """Raised on HTTP 403 - caller lacks the required permission."""


class NotFoundError(FabricError):
    """Raised on HTTP 404 - requested resource does not exist."""


class RateLimitedError(FabricError):
    """Raised when the server returns 429 more times than the max consecutive limit."""


class FabricServerError(FabricError):
    """Raised on persistent 5xx errors or a failed LRO operation.

    Attributes:
        is_retriable: Mirrors the ``isRetriable`` flag from the Fabric error
            envelope when present.  ``True`` by default (safe for existing
            callers).  When ``False``, the HTTP retry layer will NOT retry the
            request, failing fast instead of waiting through back-off cycles.
    """

    def __init__(  # noqa: PLR0913
        self,
        message: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
        body: dict[str, object] | None = None,
        hint: str | None = None,
        is_retriable: bool = True,
    ) -> None:
        super().__init__(message, status=status, request_id=request_id, body=body, hint=hint)
        self.is_retriable = is_retriable


class CapacityUnavailableError(FabricServerError):
    """Raised when a workspace's capacity is paused or has no capacity.

    This is a subclass of :class:`FabricServerError` with ``is_retriable=False``
    to prevent the HTTP client from retrying the ~22s-hanging data-plane call.
    It is used as a signal for proactive and defensive per-workspace skipping
    in ``-A`` scans.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
        body: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            message,
            status=status,
            request_id=request_id,
            body=body,
            is_retriable=False,
        )


class BadRequestError(FabricError):
    """Raised on HTTP 400 - the request body or parameters were invalid.

    The ``body`` attribute contains the parsed Fabric error JSON (errorCode /
    message) when available, giving callers visibility into the exact reason the
    request was rejected.
    """


class AlreadyExistsError(FabricError):
    """Raised when a resource with the given name already exists."""


class ConfigError(FabricCliError):
    """Raised when required configuration is missing or invalid.

    This is a local configuration error (missing env vars, unrecognised
    credential mode) and is intentionally *not* a subtype of
    :class:`FabricError`.  ``FabricError`` carries HTTP context
    (status, request_id, body) that has no meaning for a missing env var.
    Keeping the hierarchies separate ensures that broad
    ``except FabricError`` handlers in HTTP/API call sites do **not**
    silently swallow configuration problems.

    Both :class:`ConfigError` and :class:`FabricError` share the common base
    :class:`FabricCliError`, so the CLI and MCP boundaries can catch
    ``FabricCliError`` to present *both* kinds of error cleanly without
    collapsing the two distinct semantics.
    """

    @classmethod
    def missing_env_vars(cls, names: list[str]) -> ConfigError:
        """Create a ConfigError for missing environment variables."""
        return cls(
            f"Missing required environment variable(s) for service principal auth: "
            f"{', '.join(names)}"
        )

    @classmethod
    def unknown_credential_mode(cls, mode: object) -> ConfigError:
        """Create a ConfigError for an unrecognised credential mode."""
        return cls(f"Unknown credential mode: {mode!r}")


class ItemKindError(FabricError):
    """Raised when an operation is not valid for the resolved item kind."""


# ---------------------------------------------------------------------------
# Deprecated name aliases (C19)
# ---------------------------------------------------------------------------
# These short names were used before the *Error suffix convention was adopted.
# They emit a DeprecationWarning on first import via module __getattr__ below.
# Remove after the next major release.

_DEPRECATED_ALIASES: dict[str, type] = {
    "PermissionDenied": PermissionDeniedError,
    "NotFound": NotFoundError,
    "AlreadyExists": AlreadyExistsError,
}


def __getattr__(name: str) -> type:
    """Emit a DeprecationWarning when a deprecated alias is imported."""
    if name in _DEPRECATED_ALIASES:
        warnings.warn(
            f"fabric_dw.exceptions.{name} is deprecated; "
            f"use {_DEPRECATED_ALIASES[name].__name__} instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return _DEPRECATED_ALIASES[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
