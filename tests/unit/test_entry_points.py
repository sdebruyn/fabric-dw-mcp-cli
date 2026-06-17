"""Ensure the expected console-script entry points are declared in pyproject.toml.

Parsing pyproject.toml directly avoids a package-install requirement and keeps the
test fast and self-contained.  Any drift between pyproject.toml and this assertion
file will be caught immediately by the unit suite.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent.parent / "pyproject.toml"

_EXPECTED_SCRIPTS: dict[str, str] = {
    "fabric-dw": "fabric_dw.cli:main",
    "fdw": "fabric_dw.cli:main",
    "fabric-dw-mcp": "fabric_dw.mcp.server:run",
}

_SCRIPTS: dict[str, str] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"][
    "scripts"
]


def test_console_scripts_declared() -> None:
    """All expected console scripts are present and target the correct entry point."""
    for name, target in _EXPECTED_SCRIPTS.items():
        assert name in _SCRIPTS, f"console script {name!r} not found in [project.scripts]"
        assert _SCRIPTS[name] == target, (
            f"console script {name!r} targets {_SCRIPTS[name]!r}, expected {target!r}"
        )


def test_fdw_and_fabric_dw_same_target() -> None:
    """`fdw` and `fabric-dw` must point to the identical entry point."""
    assert _SCRIPTS["fdw"] == _SCRIPTS["fabric-dw"], (
        f"'fdw' ({_SCRIPTS['fdw']!r}) and 'fabric-dw' ({_SCRIPTS['fabric-dw']!r}) "
        "must share the same target"
    )
