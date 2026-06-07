"""Shared CLI context dataclass passed to all commands."""

from __future__ import annotations

from dataclasses import dataclass, field

from fabric_dw.auth import CredentialMode


@dataclass
class CliContext:
    """Carries parsed global options and lazily-constructed service clients.

    Passed through Click's ``ctx.obj`` to every sub-command.
    """

    json_output: bool = False
    yes: bool = False
    auth: CredentialMode = field(default_factory=lambda: CredentialMode.DEFAULT)
    verbose: bool = False
