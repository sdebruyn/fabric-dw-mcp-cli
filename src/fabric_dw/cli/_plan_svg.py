"""SVG renderer for SHOWPLAN_XML execution plans via the system Graphviz binary.

Converts a list of :class:`~fabric_dw.cli._plan_parse.PlanOperator` trees to an
SVG image by piping the DOT output from :func:`~fabric_dw.cli._plan_dot.render_plan_dot`
through the system ``dot`` binary (``dot -Tsvg``).

Graphviz is an **optional system dependency** — not a Python package.  When the
``dot`` binary is absent, :func:`render_plan_svg` raises a
:class:`click.ClickException` with an actionable install hint rather than crashing.

Public API
----------
- :func:`render_plan_svg` — render all statements to SVG bytes.
"""

from __future__ import annotations

import shutil
import subprocess

import click

from fabric_dw.cli._plan_dot import render_plan_dot
from fabric_dw.plan._parse import PlanOperator

__all__ = ["render_plan_svg"]

_DOT_BINARY = "dot"
_DOT_TIMEOUT = 30  # seconds; a hung dot would block the CLI indefinitely
_MISSING_BINARY_MSG = (
    "Graphviz 'dot' binary not found; install graphviz to use --format svg "
    "(https://graphviz.org/download/)."
)


def render_plan_svg(operators: list[PlanOperator]) -> bytes:
    """Render execution-plan operator trees as an SVG image via the system ``dot`` binary.

    Generates DOT text from *operators* using :func:`~fabric_dw.cli._plan_dot.render_plan_dot`,
    then pipes it to ``dot -Tsvg``.  The resulting SVG is returned as ``bytes``.

    Args:
        operators: The list of root operator nodes returned by
            :func:`~fabric_dw.cli._plan_parse.parse_showplan`.

    Returns:
        SVG image content as ``bytes``.

    Raises:
        click.ClickException: When the ``dot`` binary is not found on ``PATH``
            (includes an install hint), when ``dot`` exits with a non-zero status
            (includes the captured stderr), when ``dot`` produces no output, or
            when ``dot`` does not respond within :data:`_DOT_TIMEOUT` seconds.
    """
    if shutil.which(_DOT_BINARY) is None:
        raise click.ClickException(_MISSING_BINARY_MSG)

    dot_text = render_plan_dot(operators)

    try:
        proc = subprocess.run(  # noqa: S603
            [_DOT_BINARY, "-Tsvg"],
            input=dot_text.encode(),
            capture_output=True,
            check=False,
            timeout=_DOT_TIMEOUT,
        )
    except FileNotFoundError as exc:
        # Race condition: binary disappeared between which() and run().
        raise click.ClickException(_MISSING_BINARY_MSG) from exc
    except subprocess.TimeoutExpired as exc:
        raise click.ClickException(
            f"Graphviz 'dot' did not respond within {_DOT_TIMEOUT}s; "
            "check your DOT input or upgrade Graphviz."
        ) from exc

    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()
        raise click.ClickException(f"Graphviz 'dot' exited with status {proc.returncode}: {stderr}")

    if not proc.stdout:
        raise click.ClickException("Graphviz 'dot' produced no output; check your DOT input.")

    return proc.stdout
