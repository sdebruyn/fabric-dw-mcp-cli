"""Scaffold a dbt-fabric project linked to a Fabric Data Warehouse.

Public API
----------
- :func:`sanitize_project_name` — sanitize a name to a valid dbt identifier.
- :func:`auth_mode_to_dbt` — map :class:`~fabric_dw.auth.CredentialMode` to dbt auth string.
- :class:`DbtScaffoldConfig` — collected settings for a scaffold run.
- :func:`render_profiles_yml` — render ``profiles.yml`` content as a string.
- :func:`render_dbt_project_yml` — render ``dbt_project.yml`` content as a string.
- :func:`render_sources_yml` — render ``models/staging/_sources.yml`` content as a string.
- :func:`scaffold` — write all files into the target folder.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from fabric_dw.auth import CredentialMode
    from fabric_dw.models import Schema, Table

__all__ = [
    "DbtAuthMode",
    "DbtScaffoldConfig",
    "ProfilesDir",
    "auth_mode_to_dbt",
    "render_dbt_project_yml",
    "render_profiles_yml",
    "render_sources_yml",
    "sanitize_project_name",
    "scaffold",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: dbt authentication values supported by dbt-fabric.
class DbtAuthMode:
    AUTO = "auto"
    CLI = "CLI"
    SERVICE_PRINCIPAL = "ServicePrincipal"


class ProfilesDir:
    PROJECT = "project"
    HOME = "home"


#: Standard dbt project directories that must exist (each with .gitkeep).
_STANDARD_DIRS: tuple[str, ...] = (
    "models",
    "analyses",
    "macros",
    "seeds",
    "snapshots",
    "tests",
)

#: Standard .gitignore entries for a dbt project.
_GITIGNORE_CONTENT = """\
target/
dbt_packages/
logs/
.user.yml
"""

#: Sample first model content.
_SAMPLE_MODEL_CONTENT = "select 'hello from fabric-dw' as greeting\n"

#: requirements.txt content.
_REQUIREMENTS_CONTENT = """\
dbt-core
dbt-fabric
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sanitize_project_name(name: str) -> str:
    """Return *name* sanitized to a valid dbt project identifier.

    Rules:
    - Lowercase.
    - Replace any non-alphanumeric character (besides underscore) with ``_``.
    - Strip leading digits by prepending ``project_``.
    - Collapse consecutive underscores.
    - Strip leading/trailing underscores.

    Raises:
        ValueError: If *name* reduces to an empty string after sanitization.
    """
    lowered = name.lower()
    replaced = re.sub(r"[^a-z0-9_]", "_", lowered)
    collapsed = re.sub(r"_+", "_", replaced).strip("_")
    if not collapsed:
        raise ValueError(f"Cannot derive a valid dbt project name from {name!r}")
    # Strip leading digits (dbt identifiers must start with a letter or _)
    if collapsed and collapsed[0].isdigit():
        collapsed = f"project_{collapsed}"
    return collapsed


def auth_mode_to_dbt(mode: CredentialMode) -> str:
    """Map a :class:`~fabric_dw.auth.CredentialMode` to a dbt-fabric authentication string.

    Mapping:
    - ``default`` → ``auto``  (DefaultAzureCredential chain)
    - ``interactive`` → ``CLI``  (ActiveDirectoryInteractive)
    - ``sp`` → ``ServicePrincipal``

    Args:
        mode: The CLI credential mode.

    Returns:
        The dbt-fabric authentication string.
    """
    from fabric_dw.auth import CredentialMode  # noqa: PLC0415

    auth_map = {
        CredentialMode.DEFAULT: DbtAuthMode.AUTO,
        CredentialMode.INTERACTIVE: DbtAuthMode.CLI,
        CredentialMode.SERVICE_PRINCIPAL: DbtAuthMode.SERVICE_PRINCIPAL,
    }
    return auth_map.get(mode, DbtAuthMode.AUTO)


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class DbtScaffoldConfig:
    """All settings collected for a single dbt scaffold run.

    Attributes:
        host: The SQL analytics endpoint host (from warehouse connection string).
        database: The warehouse display name (used as the dbt database).
        project_name: The dbt project identifier.
        profile_name: The dbt profile name (defaults to *project_name*).
        schema: The default schema (default ``dbo``).
        target: The output target name (default ``dev``).
        threads: Number of dbt threads (default 4).
        dbt_auth: The dbt-fabric authentication string (``auto``, ``CLI``, ``ServicePrincipal``).
        profiles_dir: Where to write profiles.yml — ``project`` or ``home``.
        with_sources: Whether to generate a real ``_sources.yml`` from the DW.
        schemas: Pre-fetched schema list (used when *with_sources* is True).
        tables: Pre-fetched table list (used when *with_sources* is True).
    """

    host: str
    database: str
    project_name: str
    profile_name: str = ""
    schema: str = "dbo"
    target: str = "dev"
    threads: int = 4
    dbt_auth: str = DbtAuthMode.AUTO
    profiles_dir: str = ProfilesDir.PROJECT
    with_sources: bool = False
    schemas: list[Schema] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.profile_name:
            self.profile_name = self.project_name


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_profiles_yml(cfg: DbtScaffoldConfig) -> str:
    """Render the ``profiles.yml`` content for the given config.

    For ServicePrincipal auth, credentials are emitted as ``env_var(...)``
    placeholders — never as literal secrets.

    Uses :func:`yaml.safe_dump` to serialise the document so that any
    special characters in ``database``, ``schema``, ``target``, or
    ``profile_name`` (colons, hashes, etc.) are automatically quoted.

    Args:
        cfg: The scaffold configuration.

    Returns:
        The ``profiles.yml`` content as a YAML string.
    """
    output: dict[str, object] = {
        "type": "fabric",
        "driver": "ODBC Driver 18 for SQL Server",
        "host": cfg.host,
        "database": cfg.database,
        "schema": cfg.schema,
        "threads": cfg.threads,
        "authentication": cfg.dbt_auth,
    }

    if cfg.dbt_auth == DbtAuthMode.SERVICE_PRINCIPAL:
        output["tenant_id"] = "{{ env_var('AZURE_TENANT_ID') }}"
        output["client_id"] = "{{ env_var('AZURE_CLIENT_ID') }}"
        output["client_secret"] = "{{ env_var('AZURE_CLIENT_SECRET') }}"  # noqa: S105

    doc: dict[str, object] = {
        "config": {"partial_parse": True},
        cfg.profile_name: {
            "target": cfg.target,
            "outputs": {cfg.target: output},
        },
    }

    return yaml.safe_dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)


def render_dbt_project_yml(cfg: DbtScaffoldConfig) -> str:
    """Render the ``dbt_project.yml`` content for the given config.

    Args:
        cfg: The scaffold configuration.

    Returns:
        The ``dbt_project.yml`` content as a YAML string.
    """
    return (
        f"name: {cfg.project_name}\n"
        "version: '1.0.0'\n"
        "config-version: 2\n"
        f"profile: {cfg.profile_name}\n"
        "\n"
        "model-paths: ['models']\n"
        "analysis-paths: ['analyses']\n"
        "test-paths: ['tests']\n"
        "seed-paths: ['seeds']\n"
        "macro-paths: ['macros']\n"
        "snapshot-paths: ['snapshots']\n"
        "\n"
        "models:\n"
        f"  {cfg.project_name}:\n"
        "    +materialized: table\n"
    )


def render_sources_yml(
    cfg: DbtScaffoldConfig,
) -> str:
    """Render the ``models/staging/_sources.yml`` content.

    When ``cfg.with_sources`` is ``True`` and schemas/tables have been pre-fetched,
    generates a real source definition per schema.  Otherwise generates a minimal
    placeholder.

    Uses :func:`yaml.safe_dump` to serialise the document so that any
    special characters in schema/table names (colons, hashes, etc.) are
    automatically quoted and cannot break the YAML structure.

    Args:
        cfg: The scaffold configuration (must have ``schemas`` and ``tables`` populated
            when ``with_sources`` is ``True``).

    Returns:
        The ``_sources.yml`` content as a YAML string.
    """
    if not cfg.with_sources or not cfg.schemas:
        doc: dict[str, object] = {
            "version": 2,
            "sources": [
                {
                    "name": "placeholder",
                    "description": "Replace with your source definitions.",
                    "database": "{{ env_var('DBT_DATABASE', '" + cfg.database + "') }}",
                    "schema": "dbo",
                    "tables": [],
                }
            ],
        }
        return yaml.safe_dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # Group tables by schema name for easier lookup.
    tables_by_schema: dict[str, list[str]] = {}
    for tbl in cfg.tables:
        tables_by_schema.setdefault(tbl.schema_name, []).append(tbl.name)

    sources: list[dict[str, object]] = []
    for schema in cfg.schemas:
        schema_tables = sorted(tables_by_schema.get(schema.name, []))
        source: dict[str, object] = {
            "name": schema.name,
            "database": cfg.database,
            "schema": schema.name,
            "tables": [{"name": t} for t in schema_tables],
        }
        sources.append(source)

    doc = {
        "version": 2,
        "sources": sources,
    }
    return yaml.safe_dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _render_readme(cfg: DbtScaffoldConfig, profiles_dir: str) -> str:
    """Render the README.md content for the scaffolded project."""
    if profiles_dir == ProfilesDir.HOME:
        profiles_note = (
            "The `profiles.yml` has been merged into `~/.dbt/profiles.yml`. "
            "Run dbt from any directory."
        )
    else:
        profiles_note = (
            "The `profiles.yml` is in this project folder. "
            "Run `dbt` from this directory, or set the `DBT_PROFILES_DIR` "
            "environment variable to this folder's path."
        )

    sp_note = ""
    if cfg.dbt_auth == DbtAuthMode.SERVICE_PRINCIPAL:
        sp_note = (
            "\n"
            "### Service Principal environment variables\n"
            "\n"
            "Set the following before running dbt:\n"
            "\n"
            "```bash\n"
            "export AZURE_TENANT_ID=<your-tenant-id>\n"
            "export AZURE_CLIENT_ID=<your-client-id>\n"
            "export AZURE_CLIENT_SECRET=<your-client-secret>\n"
            "```\n"
        )

    return (
        f"# {cfg.project_name}\n"
        "\n"
        "A dbt project scaffolded by [fabric-dw](https://github.com/sdebruyn/fabric-dw-mcp-cli),\n"
        f"targeting the **{cfg.database}** Fabric Data Warehouse.\n"
        "\n"
        "## Prerequisites\n"
        "\n"
        "1. Python 3.11+\n"
        "2. Install dbt-fabric and its dependencies:\n"
        "\n"
        "   ```bash\n"
        "   pip install -r requirements.txt\n"
        "   ```\n"
        "\n"
        "3. Install **ODBC Driver 18 for SQL Server**:\n"
        "   - Windows: [Download from Microsoft](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)\n"
        "   - macOS: `brew install microsoft/mssql-release/msodbcsql18`\n"
        "   - Linux: see [Microsoft docs](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server)\n"
        + sp_note
        + "\n"
        "## Getting started\n"
        "\n"
        f"{profiles_note}\n"
        "\n"
        "```bash\n"
        "# Verify the connection\n"
        "dbt debug\n"
        "\n"
        "# Run the sample model\n"
        "dbt run\n"
        "```\n"
        "\n"
        "## References\n"
        "\n"
        "- [Set up dbt for Fabric DW](https://learn.microsoft.com/en-us/fabric/data-warehouse/tutorial-setup-dbt)\n"
        "- [dbt-fabric adapter](https://docs.getdbt.com/docs/core/connect-data-platform/fabric-setup)\n"
    )


# ---------------------------------------------------------------------------
# Main scaffold function
# ---------------------------------------------------------------------------


def scaffold(
    cfg: DbtScaffoldConfig,
    folder: Path,
    *,
    force: bool = False,
) -> list[Path]:
    """Write all dbt project files into *folder*.

    Args:
        cfg: The scaffold configuration.
        folder: The target directory.  Created if it does not exist.
        force: When ``True``, allow writing into a non-empty folder.

    Returns:
        A list of :class:`~pathlib.Path` objects for every file/directory written.

    Raises:
        FileExistsError: When *folder* is non-empty and *force* is ``False``.
    """
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)

    # Refuse to scaffold into a non-empty folder unless --force.
    existing = list(folder.iterdir())
    if existing and not force:
        raise FileExistsError(
            f"Target folder {folder} is not empty "
            "(use --force to scaffold into a non-empty folder)."
        )

    written: list[Path] = []

    # --- dbt_project.yml ---
    dbt_project_path = folder / "dbt_project.yml"
    dbt_project_path.write_text(render_dbt_project_yml(cfg), encoding="utf-8")
    written.append(dbt_project_path)

    # --- profiles.yml ---
    if cfg.profiles_dir == ProfilesDir.HOME:
        written.extend(_write_home_profiles(cfg))
    else:
        profiles_path = folder / "profiles.yml"
        profiles_path.write_text(render_profiles_yml(cfg), encoding="utf-8")
        written.append(profiles_path)

    # --- requirements.txt ---
    req_path = folder / "requirements.txt"
    req_path.write_text(_REQUIREMENTS_CONTENT, encoding="utf-8")
    written.append(req_path)

    # --- .gitignore ---
    gitignore_path = folder / ".gitignore"
    gitignore_path.write_text(_GITIGNORE_CONTENT, encoding="utf-8")
    written.append(gitignore_path)

    # --- Standard dbt directories with .gitkeep ---
    for dirname in _STANDARD_DIRS:
        dir_path = folder / dirname
        dir_path.mkdir(exist_ok=True)
        gitkeep = dir_path / ".gitkeep"
        gitkeep.write_text("", encoding="utf-8")
        written.append(dir_path)
        written.append(gitkeep)

    # --- Sample model ---
    models_dir = folder / "models"
    sample_model = models_dir / "my_first_model.sql"
    sample_model.write_text(_SAMPLE_MODEL_CONTENT, encoding="utf-8")
    written.append(sample_model)

    # --- Staging directory + _sources.yml ---
    staging_dir = models_dir / "staging"
    staging_dir.mkdir(exist_ok=True)
    written.append(staging_dir)

    sources_path = staging_dir / "_sources.yml"
    sources_path.write_text(render_sources_yml(cfg), encoding="utf-8")
    written.append(sources_path)

    # --- README.md ---
    readme_path = folder / "README.md"
    readme_path.write_text(_render_readme(cfg, cfg.profiles_dir), encoding="utf-8")
    written.append(readme_path)

    # --- git init (if git is on PATH and folder has no .git) ---
    _maybe_git_init(folder)

    return written


def _write_home_profiles(cfg: DbtScaffoldConfig) -> list[Path]:
    """Merge the profile into ``~/.dbt/profiles.yml``, backing up any existing file.

    The backup is only created when the profile is actually absent and the file
    will be modified — re-running when the profile is already present leaves the
    backup from the first run intact.

    Returns:
        A list of written paths (backup path if created, plus the profiles.yml).
    """
    home_dbt = Path.home() / ".dbt"
    home_dbt.mkdir(parents=True, exist_ok=True)
    profiles_path = home_dbt / "profiles.yml"
    written: list[Path] = []

    new_content = render_profiles_yml(cfg)

    if profiles_path.exists():
        existing = profiles_path.read_text(encoding="utf-8")
        # Use an anchored regex so that `my_project` does not match
        # `my_project_old:` or `my_project_backup:` as a top-level key.
        profile_key_re = re.compile(r"^" + re.escape(cfg.profile_name) + r"\s*:", re.MULTILINE)
        if not profile_key_re.search(existing):
            # Profile is genuinely absent — back up before modifying.
            backup_path = profiles_path.with_suffix(".yml.bak")
            shutil.copy2(profiles_path, backup_path)
            written.append(backup_path)
            # Strip the global config block (starts with "config:") from new_content
            # and append just the profile section.
            profile_section = _extract_profile_section(new_content, cfg.profile_name)
            merged = existing.rstrip("\n") + "\n\n" + profile_section + "\n"
            profiles_path.write_text(merged, encoding="utf-8")
        # else: profile already present — leave the file untouched.
    else:
        profiles_path.write_text(new_content, encoding="utf-8")

    written.append(profiles_path)
    return written


def _extract_profile_section(profiles_yml: str, profile_name: str) -> str:
    """Extract the profile-name block from a profiles.yml string (without the top-level config:).

    Used when merging into an existing ~/.dbt/profiles.yml to avoid duplicating
    the ``config:`` stanza.
    """
    lines = profiles_yml.splitlines(keepends=True)
    in_profile = False
    result: list[str] = []
    for line in lines:
        if line.startswith(f"{profile_name}:"):
            in_profile = True
        if in_profile:
            result.append(line)
    return "".join(result)


def _maybe_git_init(folder: Path) -> None:
    """Run ``git init`` in *folder* if git is on PATH and the folder has no ``.git``.

    Skips silently when git is not found on PATH.

    Args:
        folder: The target project directory.
    """
    if (folder / ".git").exists():
        return
    git_path = shutil.which("git")
    if git_path is None:
        return
    import contextlib  # noqa: PLC0415

    with contextlib.suppress(subprocess.CalledProcessError):
        subprocess.run(  # noqa: S603
            [git_path, "init", str(folder)],
            check=True,
            capture_output=True,
        )
