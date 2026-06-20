"""Unit tests for services.dbt_scaffold (TDD)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from fabric_dw.auth import CredentialMode
from fabric_dw.models import Schema, Table
from fabric_dw.services.dbt_scaffold import (
    _GITIGNORE_CONTENT,
    _REQUIREMENTS_CONTENT,
    _SAMPLE_MODEL_CONTENT,
    _STANDARD_DIRS,
    DbtAuthMode,
    DbtScaffoldConfig,
    ProfilesDir,
    auth_mode_to_dbt,
    render_dbt_project_yml,
    render_profiles_yml,
    render_sources_yml,
    sanitize_project_name,
    scaffold,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cfg(**kwargs: object) -> DbtScaffoldConfig:
    defaults: dict[str, object] = {
        "host": "mywarehouse.datawarehouse.fabric.microsoft.com",
        "database": "SalesWarehouse",
        "project_name": "sales_warehouse",
    }
    defaults.update(kwargs)
    return DbtScaffoldConfig(**defaults)  # ty: ignore[invalid-argument-type]


def _make_schema(name: str) -> Schema:
    return Schema(name=name, principal_id=1)


def _make_table(schema_name: str, name: str) -> Table:
    """Build a Table fixture without hitting the DB."""
    return Table(
        schema_name=schema_name,
        name=name,
        qualified_name=f"{schema_name}.{name}",
        created=datetime.now(tz=UTC),
        modified=datetime.now(tz=UTC),
    )


# ---------------------------------------------------------------------------
# sanitize_project_name
# ---------------------------------------------------------------------------


class TestSanitizeProjectName:
    def test_already_valid(self) -> None:
        assert sanitize_project_name("sales_warehouse") == "sales_warehouse"

    def test_converts_to_lowercase(self) -> None:
        assert sanitize_project_name("SalesWarehouse") == "saleswarehouse"

    def test_replaces_spaces_with_underscore(self) -> None:
        assert sanitize_project_name("my project") == "my_project"

    def test_replaces_dashes(self) -> None:
        assert sanitize_project_name("my-project") == "my_project"

    def test_collapses_consecutive_underscores(self) -> None:
        assert sanitize_project_name("my--project") == "my_project"

    def test_strips_trailing_underscores(self) -> None:
        assert sanitize_project_name("project_") == "project"

    def test_leading_digit_gets_prefixed(self) -> None:
        result = sanitize_project_name("1project")
        assert not result[0].isdigit()
        assert "project" in result

    def test_empty_after_sanitize_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot derive"):
            sanitize_project_name("---")

    def test_preserves_underscores(self) -> None:
        assert sanitize_project_name("my_dbt_project") == "my_dbt_project"

    def test_replaces_dots(self) -> None:
        assert sanitize_project_name("my.project") == "my_project"


# ---------------------------------------------------------------------------
# auth_mode_to_dbt
# ---------------------------------------------------------------------------


class TestAuthModeToDbt:
    def test_default_maps_to_auto(self) -> None:
        assert auth_mode_to_dbt(CredentialMode.DEFAULT) == DbtAuthMode.AUTO

    def test_interactive_maps_to_cli(self) -> None:
        assert auth_mode_to_dbt(CredentialMode.INTERACTIVE) == DbtAuthMode.CLI

    def test_sp_maps_to_service_principal(self) -> None:
        assert auth_mode_to_dbt(CredentialMode.SERVICE_PRINCIPAL) == DbtAuthMode.SERVICE_PRINCIPAL


# ---------------------------------------------------------------------------
# DbtScaffoldConfig defaults
# ---------------------------------------------------------------------------


class TestDbtScaffoldConfig:
    def test_profile_name_defaults_to_project_name(self) -> None:
        cfg = _make_cfg()
        assert cfg.profile_name == cfg.project_name

    def test_explicit_profile_name_is_kept(self) -> None:
        cfg = _make_cfg(profile_name="my_profile")
        assert cfg.profile_name == "my_profile"

    def test_default_schema_is_dbo(self) -> None:
        cfg = _make_cfg()
        assert cfg.schema == "dbo"

    def test_default_threads_is_4(self) -> None:
        cfg = _make_cfg()
        assert cfg.threads == 4

    def test_default_auth_is_auto(self) -> None:
        cfg = _make_cfg()
        assert cfg.dbt_auth == DbtAuthMode.AUTO


# ---------------------------------------------------------------------------
# render_profiles_yml
# ---------------------------------------------------------------------------


class TestRenderProfilesYml:
    def test_contains_host(self) -> None:
        cfg = _make_cfg()
        content = render_profiles_yml(cfg)
        assert cfg.host in content

    def test_contains_database(self) -> None:
        cfg = _make_cfg()
        content = render_profiles_yml(cfg)
        assert cfg.database in content

    def test_contains_auth_mode(self) -> None:
        cfg = _make_cfg(dbt_auth=DbtAuthMode.CLI)
        content = render_profiles_yml(cfg)
        assert "authentication: CLI" in content

    def test_auto_auth_no_secrets(self) -> None:
        cfg = _make_cfg(dbt_auth=DbtAuthMode.AUTO)
        content = render_profiles_yml(cfg)
        assert "env_var" not in content

    def test_sp_emits_env_var_placeholders(self) -> None:
        cfg = _make_cfg(dbt_auth=DbtAuthMode.SERVICE_PRINCIPAL)
        content = render_profiles_yml(cfg)
        # The Jinja2 placeholders survive a YAML round-trip (safe_dump uses single-quoted
        # scalars which double inner single-quotes; the parsed value is the original string).
        parsed = yaml.safe_load(content)
        dev_output = parsed[cfg.profile_name]["outputs"][cfg.target]
        assert dev_output["tenant_id"] == "{{ env_var('AZURE_TENANT_ID') }}"
        assert dev_output["client_id"] == "{{ env_var('AZURE_CLIENT_ID') }}"
        assert dev_output["client_secret"] == "{{ env_var('AZURE_CLIENT_SECRET') }}"  # noqa: S105

    def test_sp_emits_no_literal_secrets(self) -> None:
        """SP mode must never write literal tenant_id/client_id/client_secret values."""
        cfg = _make_cfg(dbt_auth=DbtAuthMode.SERVICE_PRINCIPAL)
        content = render_profiles_yml(cfg)
        # env_var wrapper is present; literal secret placeholders NOT present
        assert "tenant_id: my-tenant" not in content

    def test_valid_yaml(self) -> None:
        cfg = _make_cfg()
        content = render_profiles_yml(cfg)
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)

    def test_yaml_special_chars_in_database_are_safe(self) -> None:
        """A database name containing YAML-special characters must not produce broken YAML."""
        cfg = _make_cfg(database="Sales: DWH #1")
        content = render_profiles_yml(cfg)
        parsed = yaml.safe_load(content)
        # Round-trip: the database value is preserved exactly.
        assert parsed[cfg.profile_name]["outputs"][cfg.target]["database"] == "Sales: DWH #1"

    def test_yaml_special_chars_in_schema_are_safe(self) -> None:
        """A schema name containing YAML-special characters must not produce broken YAML."""
        cfg = _make_cfg(schema="raw: staging")
        content = render_profiles_yml(cfg)
        parsed = yaml.safe_load(content)
        assert parsed[cfg.profile_name]["outputs"][cfg.target]["schema"] == "raw: staging"

    def test_yaml_special_chars_in_target_are_safe(self) -> None:
        """A target name containing YAML-special characters must not produce broken YAML."""
        cfg = _make_cfg(target="dev: local")
        content = render_profiles_yml(cfg)
        parsed = yaml.safe_load(content)
        assert parsed[cfg.profile_name]["target"] == "dev: local"

    def test_sp_valid_yaml(self) -> None:
        cfg = _make_cfg(dbt_auth=DbtAuthMode.SERVICE_PRINCIPAL)
        content = render_profiles_yml(cfg)
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)

    def test_contains_fabric_type(self) -> None:
        cfg = _make_cfg()
        content = render_profiles_yml(cfg)
        assert "type: fabric" in content

    def test_contains_driver(self) -> None:
        cfg = _make_cfg()
        content = render_profiles_yml(cfg)
        assert "ODBC Driver 18" in content

    def test_uses_profile_name_as_key(self) -> None:
        cfg = _make_cfg(profile_name="my_profile")
        content = render_profiles_yml(cfg)
        assert content.startswith("config:") or "my_profile:" in content

    def test_threads_in_output(self) -> None:
        cfg = _make_cfg(threads=8)
        content = render_profiles_yml(cfg)
        assert "threads: 8" in content


# ---------------------------------------------------------------------------
# render_dbt_project_yml
# ---------------------------------------------------------------------------


class TestRenderDbtProjectYml:
    def test_contains_project_name(self) -> None:
        cfg = _make_cfg()
        content = render_dbt_project_yml(cfg)
        assert cfg.project_name in content

    def test_contains_profile_name(self) -> None:
        cfg = _make_cfg(profile_name="my_profile")
        content = render_dbt_project_yml(cfg)
        assert "profile: my_profile" in content

    def test_valid_yaml(self) -> None:
        cfg = _make_cfg()
        content = render_dbt_project_yml(cfg)
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)

    def test_contains_model_paths(self) -> None:
        cfg = _make_cfg()
        content = render_dbt_project_yml(cfg)
        assert "model-paths" in content

    def test_contains_version(self) -> None:
        cfg = _make_cfg()
        content = render_dbt_project_yml(cfg)
        assert "version:" in content


# ---------------------------------------------------------------------------
# render_sources_yml
# ---------------------------------------------------------------------------


class TestRenderSourcesYml:
    def test_placeholder_when_no_sources(self) -> None:
        cfg = _make_cfg(with_sources=False)
        content = render_sources_yml(cfg)
        assert "placeholder" in content

    def test_placeholder_is_valid_yaml(self) -> None:
        cfg = _make_cfg(with_sources=False)
        content = render_sources_yml(cfg)
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)

    def test_with_sources_generates_schema_entries(self) -> None:
        schemas = [_make_schema("dbo"), _make_schema("finance")]
        tables = [_make_table("dbo", "orders"), _make_table("finance", "budget")]
        cfg = _make_cfg(with_sources=True, schemas=schemas, tables=tables)
        content = render_sources_yml(cfg)
        assert "dbo" in content
        assert "finance" in content

    def test_with_sources_includes_table_names(self) -> None:
        schemas = [_make_schema("dbo")]
        tables = [_make_table("dbo", "orders"), _make_table("dbo", "customers")]
        cfg = _make_cfg(with_sources=True, schemas=schemas, tables=tables)
        content = render_sources_yml(cfg)
        assert "orders" in content
        assert "customers" in content

    def test_with_sources_valid_yaml(self) -> None:
        schemas = [_make_schema("dbo")]
        tables = [_make_table("dbo", "orders")]
        cfg = _make_cfg(with_sources=True, schemas=schemas, tables=tables)
        content = render_sources_yml(cfg)
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)

    def test_with_sources_empty_schemas_falls_back_to_placeholder(self) -> None:
        cfg = _make_cfg(with_sources=True, schemas=[])
        content = render_sources_yml(cfg)
        assert "placeholder" in content

    def test_yaml_special_chars_in_schema_name_are_safe(self) -> None:
        """Schema names with YAML-special characters must survive a round-trip."""
        schemas = [_make_schema("finance: raw")]
        tables = [_make_table("finance: raw", "sales: orders")]
        cfg = _make_cfg(with_sources=True, schemas=schemas, tables=tables)
        content = render_sources_yml(cfg)
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)
        source = parsed["sources"][0]
        assert source["name"] == "finance: raw"
        assert source["tables"][0]["name"] == "sales: orders"

    def test_yaml_special_chars_in_database_are_safe_in_sources(self) -> None:
        """Database names with YAML-special characters must survive a round-trip."""
        schemas = [_make_schema("dbo")]
        tables = [_make_table("dbo", "orders")]
        cfg = _make_cfg(database="Sales: DWH #1", with_sources=True, schemas=schemas, tables=tables)
        content = render_sources_yml(cfg)
        parsed = yaml.safe_load(content)
        source = parsed["sources"][0]
        assert source["database"] == "Sales: DWH #1"

    def test_with_sources_includes_columns_per_table(self) -> None:
        """Each source table must have a ``columns:`` list with name + data_type."""
        schemas = [_make_schema("dbo")]
        tables = [_make_table("dbo", "orders")]
        columns: dict[tuple[str, str], list[dict[str, object]]] = {
            ("dbo", "orders"): [
                {
                    "ordinal": 1,
                    "name": "id",
                    "data_type": "INT",
                    "nullable": False,
                    "collation_name": None,
                    "is_identity": True,
                    "is_computed": False,
                },
                {
                    "ordinal": 2,
                    "name": "amount",
                    "data_type": "DECIMAL(18,2)",
                    "nullable": True,
                    "collation_name": None,
                    "is_identity": False,
                    "is_computed": False,
                },
            ]
        }
        cfg = _make_cfg(with_sources=True, schemas=schemas, tables=tables, columns=columns)
        content = render_sources_yml(cfg)
        parsed = yaml.safe_load(content)
        table_entry = parsed["sources"][0]["tables"][0]
        assert table_entry["name"] == "orders"
        assert "columns" in table_entry
        assert table_entry["columns"][0] == {"name": "id", "data_type": "INT"}
        assert table_entry["columns"][1] == {"name": "amount", "data_type": "DECIMAL(18,2)"}

    def test_with_sources_no_columns_when_dict_empty(self) -> None:
        """When the columns dict is empty, tables must NOT have a ``columns:`` key."""
        schemas = [_make_schema("dbo")]
        tables = [_make_table("dbo", "orders")]
        cfg = _make_cfg(with_sources=True, schemas=schemas, tables=tables, columns={})
        content = render_sources_yml(cfg)
        parsed = yaml.safe_load(content)
        table_entry = parsed["sources"][0]["tables"][0]
        assert "columns" not in table_entry

    def test_with_sources_columns_across_multiple_schemas(self) -> None:
        """Columns must be scoped correctly per (schema, table)."""
        schemas = [_make_schema("dbo"), _make_schema("finance")]
        tables = [_make_table("dbo", "customers"), _make_table("finance", "budget")]
        columns: dict[tuple[str, str], list[dict[str, object]]] = {
            ("dbo", "customers"): [
                {
                    "ordinal": 1,
                    "name": "customer_id",
                    "data_type": "INT",
                    "nullable": False,
                    "collation_name": None,
                    "is_identity": True,
                    "is_computed": False,
                },
            ],
            ("finance", "budget"): [
                {
                    "ordinal": 1,
                    "name": "dept",
                    "data_type": "NVARCHAR(50)",
                    "nullable": True,
                    "collation_name": None,
                    "is_identity": False,
                    "is_computed": False,
                },
            ],
        }
        cfg = _make_cfg(with_sources=True, schemas=schemas, tables=tables, columns=columns)
        content = render_sources_yml(cfg)
        parsed = yaml.safe_load(content)
        dbo_source = next(s for s in parsed["sources"] if s["name"] == "dbo")
        finance_source = next(s for s in parsed["sources"] if s["name"] == "finance")
        assert dbo_source["tables"][0]["columns"][0]["name"] == "customer_id"
        assert finance_source["tables"][0]["columns"][0]["name"] == "dept"

    def test_with_sources_columns_valid_yaml(self) -> None:
        """_sources.yml with columns must be valid YAML."""
        schemas = [_make_schema("dbo")]
        tables = [_make_table("dbo", "orders")]
        columns: dict[tuple[str, str], list[dict[str, object]]] = {
            ("dbo", "orders"): [
                {
                    "ordinal": 1,
                    "name": "id",
                    "data_type": "INT",
                    "nullable": False,
                    "collation_name": None,
                    "is_identity": False,
                    "is_computed": False,
                },
            ]
        }
        cfg = _make_cfg(with_sources=True, schemas=schemas, tables=tables, columns=columns)
        content = render_sources_yml(cfg)
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# scaffold (file writing)
# ---------------------------------------------------------------------------


class TestScaffold:
    def test_creates_folder_if_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "new_project"
        cfg = _make_cfg()
        scaffold(cfg, target)
        assert target.is_dir()

    def test_writes_dbt_project_yml(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        scaffold(cfg, tmp_path / "proj")
        assert (tmp_path / "proj" / "dbt_project.yml").is_file()

    def test_writes_profiles_yml(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        scaffold(cfg, tmp_path / "proj")
        assert (tmp_path / "proj" / "profiles.yml").is_file()

    def test_writes_requirements_txt(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        scaffold(cfg, tmp_path / "proj")
        req = tmp_path / "proj" / "requirements.txt"
        assert req.is_file()
        content = req.read_text()
        assert "dbt-core" in content
        assert "dbt-fabric" in content

    def test_writes_gitignore(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        scaffold(cfg, tmp_path / "proj")
        gi = tmp_path / "proj" / ".gitignore"
        assert gi.is_file()
        content = gi.read_text()
        assert "target/" in content
        assert "dbt_packages/" in content

    def test_creates_standard_dirs_with_gitkeep(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        scaffold(cfg, tmp_path / "proj")
        for dirname in _STANDARD_DIRS:
            d = tmp_path / "proj" / dirname
            assert d.is_dir(), f"Expected directory {dirname} to exist"
            assert (d / ".gitkeep").is_file(), f"Expected .gitkeep in {dirname}"

    def test_creates_sample_model(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        scaffold(cfg, tmp_path / "proj")
        model = tmp_path / "proj" / "models" / "my_first_model.sql"
        assert model.is_file()
        assert "hello from fabric-dw" in model.read_text()

    def test_sample_model_exact_content(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        scaffold(cfg, tmp_path / "proj")
        model = tmp_path / "proj" / "models" / "my_first_model.sql"
        content = model.read_text()
        assert content == _SAMPLE_MODEL_CONTENT

    def test_creates_sources_yml(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        scaffold(cfg, tmp_path / "proj")
        sources = tmp_path / "proj" / "models" / "staging" / "_sources.yml"
        assert sources.is_file()

    def test_creates_readme(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        scaffold(cfg, tmp_path / "proj")
        readme = tmp_path / "proj" / "README.md"
        assert readme.is_file()

    def test_refuses_non_empty_folder_without_force(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        (tmp_path / "existing.txt").write_text("hello")
        with pytest.raises(FileExistsError):
            scaffold(cfg, tmp_path)

    def test_force_allows_non_empty_folder(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        (tmp_path / "existing.txt").write_text("hello")
        scaffold(cfg, tmp_path, force=True)
        assert (tmp_path / "dbt_project.yml").is_file()

    def test_profiles_yml_contains_host(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        scaffold(cfg, tmp_path / "proj")
        content = (tmp_path / "proj" / "profiles.yml").read_text()
        assert cfg.host in content

    def test_profiles_yml_contains_database(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        scaffold(cfg, tmp_path / "proj")
        content = (tmp_path / "proj" / "profiles.yml").read_text()
        assert cfg.database in content

    def test_sp_profiles_yml_has_env_var_placeholders(self, tmp_path: Path) -> None:
        cfg = _make_cfg(dbt_auth=DbtAuthMode.SERVICE_PRINCIPAL)
        scaffold(cfg, tmp_path / "proj")
        content = (tmp_path / "proj" / "profiles.yml").read_text()
        parsed = yaml.safe_load(content)
        dev_output = parsed[cfg.profile_name]["outputs"][cfg.target]
        assert dev_output["tenant_id"] == "{{ env_var('AZURE_TENANT_ID') }}"
        assert dev_output["client_id"] == "{{ env_var('AZURE_CLIENT_ID') }}"
        assert dev_output["client_secret"] == "{{ env_var('AZURE_CLIENT_SECRET') }}"  # noqa: S105


# ---------------------------------------------------------------------------
# git init behaviour
# ---------------------------------------------------------------------------


class TestGitInit:
    def test_git_init_called_when_git_present(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        target = tmp_path / "proj"

        with (
            patch("shutil.which", return_value="/usr/bin/git"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            scaffold(cfg, target)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "git" in args[0]
        assert "init" in args

    def test_git_init_skipped_when_git_absent(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        target = tmp_path / "proj"

        with (
            patch("shutil.which", return_value=None),
            patch("subprocess.run") as mock_run,
        ):
            scaffold(cfg, target)

        mock_run.assert_not_called()

    def test_git_init_skipped_when_dot_git_exists(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        target = tmp_path / "proj"
        target.mkdir()
        (target / ".git").mkdir()

        with (
            patch("shutil.which", return_value="/usr/bin/git"),
            patch("subprocess.run") as mock_run,
        ):
            scaffold(cfg, target, force=True)

        mock_run.assert_not_called()

    def test_git_init_failure_does_not_abort_scaffold(self, tmp_path: Path) -> None:
        cfg = _make_cfg()
        target = tmp_path / "proj"

        with (
            patch("shutil.which", return_value="/usr/bin/git"),
            patch("subprocess.run", side_effect=subprocess.CalledProcessError(128, "git")),
        ):
            # Should NOT raise
            scaffold(cfg, target)

        assert (target / "dbt_project.yml").is_file()


# ---------------------------------------------------------------------------
# home profiles merging
# ---------------------------------------------------------------------------


_EXISTING_PROFILES_YML = "other_project:\n  target: dev\n  outputs:\n    dev:\n      type: other\n"


class TestHomeProfiles:
    def test_writes_to_home_dbt_folder(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch("pathlib.Path.home", return_value=fake_home):
            cfg = _make_cfg(profiles_dir=ProfilesDir.HOME)
            scaffold(cfg, tmp_path / "proj")

        profiles_path = fake_home / ".dbt" / "profiles.yml"
        assert profiles_path.is_file()

    def test_backs_up_existing_home_profiles(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        dbt_dir = fake_home / ".dbt"
        dbt_dir.mkdir()
        existing = dbt_dir / "profiles.yml"
        existing.write_text(_EXISTING_PROFILES_YML)

        with patch("pathlib.Path.home", return_value=fake_home):
            cfg = _make_cfg(profiles_dir=ProfilesDir.HOME)
            scaffold(cfg, tmp_path / "proj")

        backup = dbt_dir / "profiles.yml.bak"
        assert backup.is_file()
        assert "other_project" in backup.read_text()

    def test_merges_without_overwriting_other_profiles(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        dbt_dir = fake_home / ".dbt"
        dbt_dir.mkdir()
        existing = dbt_dir / "profiles.yml"
        existing.write_text(_EXISTING_PROFILES_YML)

        with patch("pathlib.Path.home", return_value=fake_home):
            cfg = _make_cfg(profiles_dir=ProfilesDir.HOME, project_name="new_project")
            scaffold(cfg, tmp_path / "proj")

        merged = (dbt_dir / "profiles.yml").read_text()
        assert "other_project" in merged
        assert "new_project" in merged

    def test_profile_already_present_does_not_clobber_file(self, tmp_path: Path) -> None:
        """When the profile already exists as a top-level key, the file must not be modified."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        dbt_dir = fake_home / ".dbt"
        dbt_dir.mkdir()
        profile_name = "my_project"
        existing_content = (
            f"{profile_name}:\n  target: dev\n  outputs:\n    dev:\n      type: fabric\n"
        )
        profiles_path = dbt_dir / "profiles.yml"
        profiles_path.write_text(existing_content)

        with patch("pathlib.Path.home", return_value=fake_home):
            cfg = _make_cfg(profiles_dir=ProfilesDir.HOME, project_name=profile_name)
            scaffold(cfg, tmp_path / "proj")

        # File must be unchanged.
        assert profiles_path.read_text() == existing_content

    def test_substring_match_does_not_suppress_merge(self, tmp_path: Path) -> None:
        """A longer profile key like ``my_project_old`` must not prevent merging ``my_project``."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        dbt_dir = fake_home / ".dbt"
        dbt_dir.mkdir()
        existing_content = (
            "my_project_old:\n  target: dev\n  outputs:\n    dev:\n      type: fabric\n"
        )
        profiles_path = dbt_dir / "profiles.yml"
        profiles_path.write_text(existing_content)

        with patch("pathlib.Path.home", return_value=fake_home):
            cfg = _make_cfg(profiles_dir=ProfilesDir.HOME, project_name="my_project")
            scaffold(cfg, tmp_path / "proj")

        merged = profiles_path.read_text()
        # Both profiles must be present.
        assert "my_project_old:" in merged
        assert "my_project:" in merged

    def test_backup_not_created_when_profile_already_present(self, tmp_path: Path) -> None:
        """No backup should be created when the profile already exists (no modification)."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        dbt_dir = fake_home / ".dbt"
        dbt_dir.mkdir()
        profile_name = "sales_warehouse"
        existing_content = (
            f"{profile_name}:\n  target: dev\n  outputs:\n    dev:\n      type: fabric\n"
        )
        profiles_path = dbt_dir / "profiles.yml"
        profiles_path.write_text(existing_content)

        with patch("pathlib.Path.home", return_value=fake_home):
            cfg = _make_cfg(profiles_dir=ProfilesDir.HOME, project_name=profile_name)
            scaffold(cfg, tmp_path / "proj")

        backup_path = dbt_dir / "profiles.yml.bak"
        assert not backup_path.exists(), "Backup must not be created when no modification occurs"

    def test_backup_created_only_when_profile_is_absent(self, tmp_path: Path) -> None:
        """Backup should be created exactly once when the profile is added."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        dbt_dir = fake_home / ".dbt"
        dbt_dir.mkdir()
        profiles_path = dbt_dir / "profiles.yml"
        profiles_path.write_text(_EXISTING_PROFILES_YML)

        with patch("pathlib.Path.home", return_value=fake_home):
            cfg = _make_cfg(profiles_dir=ProfilesDir.HOME, project_name="brand_new_project")
            scaffold(cfg, tmp_path / "proj")

        backup_path = dbt_dir / "profiles.yml.bak"
        assert backup_path.exists(), "Backup must be created when the profile is added"
        # Backup contains the original content.
        assert backup_path.read_text() == _EXISTING_PROFILES_YML


# ---------------------------------------------------------------------------
# requirements.txt content constants
# ---------------------------------------------------------------------------


class TestRequirementsContent:
    def test_contains_dbt_core(self) -> None:
        assert "dbt-core" in _REQUIREMENTS_CONTENT

    def test_contains_dbt_fabric(self) -> None:
        assert "dbt-fabric" in _REQUIREMENTS_CONTENT


# ---------------------------------------------------------------------------
# gitignore content constants
# ---------------------------------------------------------------------------


class TestGitignoreContent:
    def test_contains_target(self) -> None:
        assert "target/" in _GITIGNORE_CONTENT

    def test_contains_dbt_packages(self) -> None:
        assert "dbt_packages/" in _GITIGNORE_CONTENT

    def test_contains_logs(self) -> None:
        assert "logs/" in _GITIGNORE_CONTENT

    def test_contains_user_yml(self) -> None:
        assert ".user.yml" in _GITIGNORE_CONTENT
