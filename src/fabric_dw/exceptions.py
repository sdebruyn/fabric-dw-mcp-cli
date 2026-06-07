class FabricError(Exception):
    """Base error for all fabric-dw errors."""


class ConfigError(FabricError):
    """Raised when required configuration is missing or invalid."""
