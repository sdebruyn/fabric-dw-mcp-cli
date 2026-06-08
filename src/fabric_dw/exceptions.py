class FabricError(Exception):
    """Base error for all fabric-dw errors."""


class AuthError(FabricError):
    """Raised on HTTP 401 - authentication token missing or invalid."""


class PermissionDenied(FabricError):  # noqa: N818
    """Raised on HTTP 403 - caller lacks the required permission."""


class NotFound(FabricError):  # noqa: N818
    """Raised on HTTP 404 - requested resource does not exist."""


class RateLimitedError(FabricError):
    """Raised when the server returns 429 more times than the max consecutive limit."""


class FabricServerError(FabricError):
    """Raised on persistent 5xx errors or a failed LRO operation."""


class AlreadyExists(FabricError):  # noqa: N818
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
