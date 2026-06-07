"""Completion sub-commands: install shell completion scripts."""

from __future__ import annotations

from pathlib import Path

import click
from click.shell_completion import BashComplete, FishComplete, ZshComplete

_SUPPORTED_SHELLS = ("bash", "zsh", "fish")

_COMPLETE_VAR = "_FABRIC_DW_COMPLETE"
_PROG_NAME = "fabric-dw"


def _completion_script(shell: str) -> str:
    """Return the Click-generated completion script for *shell*.

    Uses Click's public ``ShellComplete`` subclasses to generate the
    source script without spawning a subprocess.
    """
    # Import here to avoid a circular import at module load time.
    from fabric_dw.cli._main import cli  # noqa: PLC0415

    cls_map = {
        "bash": BashComplete,
        "zsh": ZshComplete,
        "fish": FishComplete,
    }
    cls = cls_map[shell.lower()]
    complete = cls(cli, {}, _PROG_NAME, _COMPLETE_VAR)
    return complete.source()


@click.group("completion")
def completion_group() -> None:
    """Manage shell completion scripts."""


@completion_group.command("install")
@click.argument("shell", type=click.Choice(_SUPPORTED_SHELLS, case_sensitive=False))
@click.option(
    "--print",
    "print_only",
    is_flag=True,
    default=False,
    help="Print the completion script to stdout instead of installing it.",
)
def install(shell: str, print_only: bool) -> None:
    """Generate and optionally install the completion script for SHELL.

    When --print is given (or no install location can be determined), the
    script is printed to stdout so you can source it yourself.

    Without --print the script is written to the conventional location:

    \b
    bash  → appended to ~/.bashrc  (idempotent)
    zsh   → appended to ~/.zshrc   (idempotent)
    fish  → written to ~/.config/fish/completions/fabric-dw.fish
    """
    shell = shell.lower()
    script = _completion_script(shell)

    if print_only:
        click.echo(script, nl=False)
        return

    home = Path.home()

    if shell == "bash":
        target = home / ".bashrc"
        _append_idempotent(target, script)
        click.echo(f"Completion script appended to {target}. Reload with: source {target}")
    elif shell == "zsh":
        target = home / ".zshrc"
        _append_idempotent(target, script)
        click.echo(f"Completion script appended to {target}. Reload with: source {target}")
    elif shell == "fish":
        target = home / ".config" / "fish" / "completions" / "fabric-dw.fish"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script)
        click.echo(f"Completion script written to {target}. Reload with: source {target}")


def _append_idempotent(path: Path, script: str) -> None:
    """Append *script* to *path* only if the script is not already present."""
    existing = path.read_text() if path.exists() else ""
    marker = script.strip()
    if marker and marker in existing:
        click.echo(f"Completion script already present in {path}. Nothing to do.")
        return
    with path.open("a") as fh:
        fh.write("\n" + script)
