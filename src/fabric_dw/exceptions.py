class FabricError(Exception):
    """Base error for all fabric-dw errors.

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
    """Raised on persistent 5xx errors or a failed LRO operation."""


class AlreadyExistsError(FabricError):
    """Raised when a resource with the given name already exists."""


class ConfigError(FabricError):
    """Raised when required configuration is missing or invalid."""

    @classmethod
    def missing_env_vars(cls, names: list[str]) -> "ConfigError":
        """Create a ConfigError for missing environment variables."""
        return cls(
            f"Missing required environment variable(s) for service principal auth: "
            f"{', '.join(names)}"
        )

    @classmethod
    def unknown_credential_mode(cls, mode: object) -> "ConfigError":
        """Create a ConfigError for an unrecognised credential mode."""
        return cls(f"Unknown credential mode: {mode!r}")


class ItemKindError(FabricError):
    """Raised when an operation is not valid for the resolved item kind."""


# Deprecated aliases — remove after next release.
PermissionDenied = PermissionDeniedError
NotFound = NotFoundError
AlreadyExists = AlreadyExistsError
