class FabricError(Exception):
    """Base error for all fabric-dw errors."""


class ConfigError(FabricError):
    """Raised when required configuration is missing or invalid."""

    @classmethod
    def missing_env_vars(cls, names: list[str]) -> "ConfigError":
        """Create a ConfigError for missing environment variables."""
        return cls(
            f"Missing required environment variable(s) for service principal auth: "
            f"{', '.join(names)}"
        )
