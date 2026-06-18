"""Shared CLI context dataclass passed to all commands."""

from __future__ import annotations

from dataclasses import dataclass, field

from fabric_dw.auth import CredentialMode
from fabric_dw.config import UserConfig, load_config


@dataclass
class CliContext:
    """Carries parsed global options and lazily-constructed service clients.

    Passed through Click's ``ctx.obj`` to every sub-command.
    """

    json_output: bool = False
    yes: bool = False
    auth: CredentialMode = field(default_factory=lambda: CredentialMode.DEFAULT)
    workspace: str | None = None
    _config: UserConfig | None = field(default=None, repr=False, compare=False)

    @property
    def config(self) -> UserConfig:
        """Lazily load the user config from disk on first access."""
        if self._config is None:
            self._config = load_config()
        return self._config
