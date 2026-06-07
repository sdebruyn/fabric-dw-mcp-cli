"""CLI entrypoint for fabric-dw."""

from __future__ import annotations

from fabric_dw.cli._main import cli


def main() -> None:
    """Entrypoint registered in pyproject.toml [project.scripts]."""
    cli()
