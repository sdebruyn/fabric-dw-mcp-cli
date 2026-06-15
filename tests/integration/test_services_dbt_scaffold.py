"""Integration tests for dbt scaffold against an ephemeral Fabric Data Warehouse.

These tests verify that the scaffold command writes ALL required files and
directories, and that those files parse cleanly as YAML where applicable.

Marked ``integration`` — requires FABRIC_TEST_WORKSPACE_ID in environment.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from fabric_dw.auth import CredentialMode
from fabric_dw.services.dbt_scaffold import (
    _SAMPLE_MODEL_CONTENT,
    _STANDARD_DIRS,
    DbtAuthMode,
    DbtScaffoldConfig,
    scaffold,
)
from fabric_dw.sql import SqlTarget

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_dbt_folder(tmp_path: Path) -> Iterator[Path]:
    """Create a temporary empty folder for the scaffold, delete it afterwards."""
    folder = tmp_path / "test_dbt_project"
    folder.mkdir()
    try:
        yield folder
    finally:
        if folder.exists():
            shutil.rmtree(folder)


@pytest.fixture
def dbt_config(ephemeral_sql_target: SqlTarget) -> DbtScaffoldConfig:
    """Build a DbtScaffoldConfig from the ephemeral warehouse."""
    return DbtScaffoldConfig(
        host=ephemeral_sql_target.connection_string,
        database=ephemeral_sql_target.database,
        project_name="integration_test_project",
        dbt_auth=DbtAuthMode.AUTO,
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_scaffold_creates_dbt_project_yml(
    temp_dbt_folder: Path, dbt_config: DbtScaffoldConfig
) -> None:
    """dbt_project.yml is written and is valid YAML."""
    scaffold(dbt_config, temp_dbt_folder, force=True)
    dbt_project = temp_dbt_folder / "dbt_project.yml"
    assert dbt_project.is_file()
    parsed = yaml.safe_load(dbt_project.read_text())
    assert isinstance(parsed, dict)
    assert parsed.get("name") == dbt_config.project_name


@pytest.mark.integration
def test_scaffold_creates_profiles_yml(
    temp_dbt_folder: Path, dbt_config: DbtScaffoldConfig
) -> None:
    """profiles.yml is written, is valid YAML, and contains the host and database."""
    scaffold(dbt_config, temp_dbt_folder, force=True)
    profiles_yml = temp_dbt_folder / "profiles.yml"
    assert profiles_yml.is_file()
    content = profiles_yml.read_text()
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict)
    assert dbt_config.host in content
    assert dbt_config.database in content


@pytest.mark.integration
def test_scaffold_creates_requirements_txt(
    temp_dbt_folder: Path, dbt_config: DbtScaffoldConfig
) -> None:
    """requirements.txt is written and contains dbt-core and dbt-fabric."""
    scaffold(dbt_config, temp_dbt_folder, force=True)
    req = temp_dbt_folder / "requirements.txt"
    assert req.is_file()
    content = req.read_text()
    assert "dbt-core" in content
    assert "dbt-fabric" in content


@pytest.mark.integration
def test_scaffold_creates_gitignore(temp_dbt_folder: Path, dbt_config: DbtScaffoldConfig) -> None:
    """.gitignore is written and contains the standard dbt entries."""
    scaffold(dbt_config, temp_dbt_folder, force=True)
    gitignore = temp_dbt_folder / ".gitignore"
    assert gitignore.is_file()
    content = gitignore.read_text()
    assert "target/" in content
    assert "dbt_packages/" in content
    assert "logs/" in content
    assert ".user.yml" in content


@pytest.mark.integration
def test_scaffold_creates_standard_dirs_with_gitkeep(
    temp_dbt_folder: Path, dbt_config: DbtScaffoldConfig
) -> None:
    """All standard dbt directories are created, each with a .gitkeep file."""
    scaffold(dbt_config, temp_dbt_folder, force=True)
    for dirname in _STANDARD_DIRS:
        d = temp_dbt_folder / dirname
        assert d.is_dir(), f"Expected directory {dirname}"
        assert (d / ".gitkeep").is_file(), f"Expected .gitkeep in {dirname}"


@pytest.mark.integration
def test_scaffold_creates_sample_model(
    temp_dbt_folder: Path, dbt_config: DbtScaffoldConfig
) -> None:
    """models/my_first_model.sql is written with the greeting content."""
    scaffold(dbt_config, temp_dbt_folder, force=True)
    model = temp_dbt_folder / "models" / "my_first_model.sql"
    assert model.is_file()
    content = model.read_text()
    assert content == _SAMPLE_MODEL_CONTENT
    assert "hello from fabric-dw" in content


@pytest.mark.integration
def test_scaffold_creates_sources_yml(temp_dbt_folder: Path, dbt_config: DbtScaffoldConfig) -> None:
    """models/staging/_sources.yml is written and is valid YAML."""
    scaffold(dbt_config, temp_dbt_folder, force=True)
    sources = temp_dbt_folder / "models" / "staging" / "_sources.yml"
    assert sources.is_file()
    parsed = yaml.safe_load(sources.read_text())
    assert isinstance(parsed, dict)


@pytest.mark.integration
def test_scaffold_creates_readme(temp_dbt_folder: Path, dbt_config: DbtScaffoldConfig) -> None:
    """README.md is written."""
    scaffold(dbt_config, temp_dbt_folder, force=True)
    readme = temp_dbt_folder / "README.md"
    assert readme.is_file()
    assert readme.read_text().strip()  # non-empty


@pytest.mark.integration
def test_scaffold_with_sources_generates_real_schemas(
    temp_dbt_folder: Path,
    dbt_config: DbtScaffoldConfig,
    ephemeral_sql_target: SqlTarget,
) -> None:
    """--with-sources generates _sources.yml from actual warehouse schemas/tables."""
    import asyncio  # noqa: PLC0415

    schemas, tables = asyncio.run(
        _fetch_schemas_and_tables(ephemeral_sql_target, CredentialMode.DEFAULT)
    )

    cfg = DbtScaffoldConfig(
        host=dbt_config.host,
        database=dbt_config.database,
        project_name=dbt_config.project_name,
        dbt_auth=DbtAuthMode.AUTO,
        with_sources=True,
        schemas=schemas,
        tables=tables,
    )
    scaffold(cfg, temp_dbt_folder, force=True)
    sources = temp_dbt_folder / "models" / "staging" / "_sources.yml"
    assert sources.is_file()
    parsed = yaml.safe_load(sources.read_text())
    assert isinstance(parsed, dict)


async def _fetch_schemas_and_tables(target: SqlTarget, mode: CredentialMode) -> tuple:
    """Helper: fetch schemas and tables concurrently."""
    import asyncio  # noqa: PLC0415

    from fabric_dw.services import schemas as schemas_svc  # noqa: PLC0415
    from fabric_dw.services import tables as tables_svc  # noqa: PLC0415

    return await asyncio.gather(
        schemas_svc.list_schemas(target, mode=mode),
        tables_svc.list_tables(target, mode=mode),
    )
