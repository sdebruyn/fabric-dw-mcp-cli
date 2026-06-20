"""SVG renderer for SHOWPLAN_XML execution plans via the system Graphviz binary.

Converts a list of :class:`~fabric_dw.cli._plan_parse.PlanOperator` trees to an
SVG image by piping the DOT output from :func:`~fabric_dw.cli._plan_dot.render_plan_dot`
through the system ``dot`` binary (``dot -Tsvg``).

Graphviz is an **optional system dependency** — not a Python package.  When the
``dot`` binary is absent, :func:`render_plan_svg` raises a
:class:`click.UsageError` with an actionable install hint rather than crashing.

Public API
----------
- :func:`render_plan_svg` — render all statements to SVG bytes.
"""

from __future__ import annotations

import shutil
import subprocess

import click

from fabric_dw.cli._plan_dot import render_plan_dot
from fabric_dw.cli._plan_parse import PlanOperator

__all__ = ["render_plan_svg"]

_DOT_BINARY = "dot"
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
        click.UsageError: When the ``dot`` binary is not found on ``PATH``
            (includes an install hint).
        click.ClickException: When ``dot`` exits with a non-zero status code
            (includes the captured stderr for diagnosis).
    """
    if shutil.which(_DOT_BINARY) is None:
        raise click.UsageError(_MISSING_BINARY_MSG)

    dot_text = render_plan_dot(operators)

    try:
        proc = subprocess.run(  # noqa: S603
            [_DOT_BINARY, "-Tsvg"],
            input=dot_text.encode(),
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        # Race condition: binary disappeared between which() and run().
        raise click.UsageError(_MISSING_BINARY_MSG) from exc

    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()
        raise click.ClickException(f"Graphviz 'dot' exited with status {proc.returncode}: {stderr}")

    return proc.stdout
