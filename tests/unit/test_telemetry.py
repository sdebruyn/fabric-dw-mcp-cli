"""Tests for fabric_dw.telemetry — opt-out usage telemetry foundation.

Written TDD-first before the implementation.  All tests must pass with
no real network calls (every Azure Monitor SDK interaction is mocked).
"""

from __future__ import annotations

import importlib
import sys
import types
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_telemetry() -> types.ModuleType:
    """Force-reload the telemetry module so module-level state resets."""
    if "fabric_dw.telemetry" in sys.modules:
        del sys.modules["fabric_dw.telemetry"]
    return importlib.import_module("fabric_dw.telemetry")


# ---------------------------------------------------------------------------
# Opt-out: telemetry_enabled()
# ---------------------------------------------------------------------------


def test_telemetry_enabled_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Telemetry is ON by default when no opt-out signal is present."""
    monkeypatch.delenv("FABRIC_TELEMETRY", raising=False)
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("JENKINS_URL", raising=False)
    monkeypatch.delenv("TRAVIS", raising=False)
    monkeypatch.delenv("CIRCLECI", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("TF_BUILD", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is True  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "NO", "off", "OFF"])
def test_telemetry_disabled_by_fabric_telemetry_falsy(
    value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FABRIC_TELEMETRY in {0, false, no, off} disables telemetry."""
    monkeypatch.setenv("FABRIC_TELEMETRY", value)
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


def test_telemetry_not_disabled_by_fabric_telemetry_truthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FABRIC_TELEMETRY=1 does NOT disable telemetry (opt-in is a no-op; it is on by default)."""
    monkeypatch.setenv("FABRIC_TELEMETRY", "1")
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("JENKINS_URL", raising=False)
    monkeypatch.delenv("TRAVIS", raising=False)
    monkeypatch.delenv("CIRCLECI", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("TF_BUILD", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is True  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE"])
def test_telemetry_disabled_by_fabric_disable_telemetry(
    value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FABRIC_DISABLE_TELEMETRY set to a truthy value disables telemetry."""
    monkeypatch.delenv("FABRIC_TELEMETRY", raising=False)
    monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", value)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE"])
def test_telemetry_disabled_by_do_not_track(
    value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DO_NOT_TRACK truthy disables telemetry (consoledonottrack.com standard)."""
    monkeypatch.delenv("FABRIC_TELEMETRY", raising=False)
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.setenv("DO_NOT_TRACK", value)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


def test_telemetry_disabled_when_ci_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CI=true disables telemetry."""
    monkeypatch.delenv("FABRIC_TELEMETRY", raising=False)
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "var",
    ["GITHUB_ACTIONS", "JENKINS_URL", "TRAVIS", "CIRCLECI", "GITLAB_CI", "TF_BUILD"],
)
def test_telemetry_disabled_by_ci_marker(
    var: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Common CI marker env vars disable telemetry."""
    monkeypatch.delenv("FABRIC_TELEMETRY", raising=False)
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    # Clear other CI markers
    for other in ["GITHUB_ACTIONS", "JENKINS_URL", "TRAVIS", "CIRCLECI", "GITLAB_CI", "TF_BUILD"]:
        if other != var:
            monkeypatch.delenv(other, raising=False)
    monkeypatch.setenv(var, "true")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Opt-out via config setting
# ---------------------------------------------------------------------------


def test_telemetry_disabled_by_config_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """telemetry_enabled() returns False when config file has telemetry_disabled=true."""
    import tomli_w  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_TELEMETRY", raising=False)
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("JENKINS_URL", raising=False)
    monkeypatch.delenv("TRAVIS", raising=False)
    monkeypatch.delenv("CIRCLECI", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("TF_BUILD", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.toml"
    config_file.write_text(tomli_w.dumps({"telemetry": {"disabled": True}}), encoding="utf-8")

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# SDK is never imported when disabled
# ---------------------------------------------------------------------------


def test_azure_monitor_sdk_not_imported_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When telemetry is disabled, azure.monitor.opentelemetry must never be imported."""
    monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    # Remove SDK from sys.modules to detect a fresh import
    for key in list(sys.modules):
        if "azure.monitor" in key or "opentelemetry" in key:
            del sys.modules[key]

    mod = _reload_telemetry()
    mod.emit_event("test_event", {})  # type: ignore[attr-defined]

    # SDK must not have been imported
    assert not any("azure.monitor.opentelemetry" in k for k in sys.modules)


# ---------------------------------------------------------------------------
# emit_event: never raises even when SDK errors
# ---------------------------------------------------------------------------


def test_emit_event_never_raises_on_sdk_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """emit_event must swallow all exceptions and never propagate them."""
    monkeypatch.delenv("FABRIC_TELEMETRY", raising=False)
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("JENKINS_URL", raising=False)
    monkeypatch.delenv("TRAVIS", raising=False)
    monkeypatch.delenv("CIRCLECI", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("TF_BUILD", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()

    # Patch the internal _get_tracer to raise
    with patch.object(mod, "_get_tracer", side_effect=RuntimeError("SDK exploded")):  # type: ignore[attr-defined]
        # Must not raise
        mod.emit_event("some_event", {"key": "value"})  # type: ignore[attr-defined]


def test_emit_event_no_op_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """emit_event is a no-op (does not import SDK) when telemetry is disabled."""
    monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()

    called: list[str] = []

    with patch.object(mod, "_get_tracer", side_effect=lambda: called.append("called")):  # type: ignore[attr-defined]
        mod.emit_event("test", {})  # type: ignore[attr-defined]

    assert called == [], "_get_tracer must not be called when disabled"


# ---------------------------------------------------------------------------
# Envelope fields
# ---------------------------------------------------------------------------


def test_envelope_contains_required_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_build_envelope must return a dict with all required fields."""
    monkeypatch.delenv("FABRIC_TELEMETRY", raising=False)
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("JENKINS_URL", raising=False)
    monkeypatch.delenv("TRAVIS", raising=False)
    monkeypatch.delenv("CIRCLECI", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("TF_BUILD", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]

    required = {
        "anonymous_install_id",
        "session_id",
        "app_version",
        "python_version",
        "os",
        "arch",
        "install_method",
        "surface",
        "is_ci",
        "auth_mode",
    }
    for field in required:
        assert field in envelope, f"Missing envelope field: {field}"


def test_envelope_surface_field(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Surface field must match the argument passed to _build_envelope."""
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("JENKINS_URL", raising=False)
    monkeypatch.delenv("TRAVIS", raising=False)
    monkeypatch.delenv("CIRCLECI", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("TF_BUILD", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod._build_envelope("cli")["surface"] == "cli"  # type: ignore[attr-defined]
    assert mod._build_envelope("mcp")["surface"] == "mcp"  # type: ignore[attr-defined]


def test_envelope_is_ci_true_when_ci_env_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """is_ci must be True when CI env var is set."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert envelope["is_ci"] is True


def test_envelope_is_ci_false_when_no_ci_markers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """is_ci must be False when no CI env vars are present."""
    ci_vars = ["CI", "GITHUB_ACTIONS", "JENKINS_URL", "TRAVIS", "CIRCLECI", "GITLAB_CI", "TF_BUILD"]
    for var in ci_vars:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert envelope["is_ci"] is False


def test_envelope_python_version_is_minor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """python_version must be 'major.minor' format (e.g. '3.12')."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()
    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    pv = envelope["python_version"]
    parts = str(pv).split(".")
    assert len(parts) == 2, f"Expected 'major.minor', got {pv!r}"
    assert all(p.isdigit() for p in parts)


def test_envelope_os_is_lowercase(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """os field must be lowercase."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()
    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert envelope["os"] == envelope["os"].lower()


def test_envelope_tenant_id_from_azure_tenant_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tenant_id must be read from AZURE_TENANT_ID when present."""
    monkeypatch.setenv("AZURE_TENANT_ID", "my-tenant-123")
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "my-tenant-123"


def test_envelope_tenant_id_from_fabric_interactive_when_no_azure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tenant_id falls back to FABRIC_INTERACTIVE_TENANT_ID if AZURE_TENANT_ID is absent."""
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.setenv("FABRIC_INTERACTIVE_TENANT_ID", "interactive-tenant")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "interactive-tenant"


def test_envelope_tenant_id_none_when_no_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tenant_id is None when neither AZURE_TENANT_ID nor FABRIC_INTERACTIVE_TENANT_ID is set."""
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert envelope["tenant_id"] is None


# ---------------------------------------------------------------------------
# auth_mode derivation
# ---------------------------------------------------------------------------


def test_auth_mode_service_principal_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """auth_mode is 'service_principal' when AZURE_CLIENT_SECRET is set."""
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret")
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod._detect_auth_mode() == "service_principal"  # type: ignore[attr-defined]


def test_auth_mode_github_oidc_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """auth_mode is 'github_oidc' when ACTIONS_ID_TOKEN_REQUEST_URL is set."""
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.com/")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod._detect_auth_mode() == "github_oidc"  # type: ignore[attr-defined]


def test_auth_mode_azure_cli_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """auth_mode is 'azure_cli' when AZURE_CONFIG_DIR is set and no SP/OIDC."""
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    monkeypatch.setenv("AZURE_CONFIG_DIR", "/home/user/.azure")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod._detect_auth_mode() == "azure_cli"  # type: ignore[attr-defined]


def test_auth_mode_interactive_as_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """auth_mode falls back to 'interactive' when no specific env vars are set."""
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    monkeypatch.delenv("AZURE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod._detect_auth_mode() == "interactive"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# anonymous_install_id: generated-once-and-persisted
# ---------------------------------------------------------------------------


def test_install_id_is_valid_uuid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """anonymous_install_id must be a valid UUID string."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    install_id = mod._get_install_id()  # type: ignore[attr-defined]
    # Must be parseable as a UUID
    parsed = uuid.UUID(install_id)
    assert str(parsed) == install_id


def test_install_id_persisted_across_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_get_install_id must return the same UUID across multiple calls."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    id1 = mod._get_install_id()  # type: ignore[attr-defined]
    id2 = mod._get_install_id()  # type: ignore[attr-defined]
    assert id1 == id2


def test_install_id_persisted_across_reloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_get_install_id must return the same UUID after module reload (reads from disk)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod1 = _reload_telemetry()
    id1 = mod1._get_install_id()  # type: ignore[attr-defined]

    mod2 = _reload_telemetry()
    id2 = mod2._get_install_id()  # type: ignore[attr-defined]

    assert id1 == id2


def test_install_id_stored_in_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """install_id marker file must be stored in the config directory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod._get_install_id()  # type: ignore[attr-defined]

    # The config dir is tmp_path/fabric-dw/; the marker file should exist somewhere in it
    config_dir = tmp_path / "fabric-dw"
    assert config_dir.exists(), "Config directory was not created"
    files = list(config_dir.iterdir())
    assert len(files) >= 1, "No files created in config dir"


# ---------------------------------------------------------------------------
# session_id: per-process  # noqa: ERA001
# ---------------------------------------------------------------------------


def test_session_id_is_valid_uuid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """session_id must be a valid UUID."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    session_id = mod._SESSION_ID  # type: ignore[attr-defined]
    parsed = uuid.UUID(session_id)
    assert str(parsed) == session_id


def test_session_id_stable_within_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """session_id must be the same UUID throughout a single module load."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    id1 = mod._SESSION_ID  # type: ignore[attr-defined]
    id2 = mod._SESSION_ID  # type: ignore[attr-defined]
    assert id1 == id2


def test_session_id_differs_across_reloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A fresh module reload (simulating a new process) generates a new session_id."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod1 = _reload_telemetry()
    id1 = mod1._SESSION_ID  # type: ignore[attr-defined]

    mod2 = _reload_telemetry()
    id2 = mod2._SESSION_ID  # type: ignore[attr-defined]

    assert id1 != id2, "New module load should produce a different session_id"


# ---------------------------------------------------------------------------
# First-run notice
# ---------------------------------------------------------------------------


def test_first_run_notice_printed_to_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """First-run notice must be printed to stderr the first time."""
    for var in [
        "FABRIC_TELEMETRY",
        "FABRIC_DISABLE_TELEMETRY",
        "DO_NOT_TRACK",
        "CI",
        "GITHUB_ACTIONS",
        "JENKINS_URL",
        "TRAVIS",
        "CIRCLECI",
        "GITLAB_CI",
        "TF_BUILD",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.maybe_print_first_run_notice()  # type: ignore[attr-defined]

    captured = capsys.readouterr()
    assert captured.err != "", "First-run notice should be printed to stderr"
    assert "telemetry" in captured.err.lower() or "opt" in captured.err.lower()


def test_first_run_notice_printed_only_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """First-run notice must not be repeated after the marker file is written."""
    for var in [
        "FABRIC_TELEMETRY",
        "FABRIC_DISABLE_TELEMETRY",
        "DO_NOT_TRACK",
        "CI",
        "GITHUB_ACTIONS",
        "JENKINS_URL",
        "TRAVIS",
        "CIRCLECI",
        "GITLAB_CI",
        "TF_BUILD",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.maybe_print_first_run_notice()  # type: ignore[attr-defined]
    mod.maybe_print_first_run_notice()  # second call

    captured = capsys.readouterr()
    # Count lines that look like a telemetry notice
    notice_lines = [line for line in captured.err.splitlines() if line.strip()]
    assert len(notice_lines) == 1, "Notice must appear exactly once, not repeated"


def test_first_run_notice_not_printed_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No first-run notice when telemetry is disabled."""
    monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.maybe_print_first_run_notice()  # type: ignore[attr-defined]

    captured = capsys.readouterr()
    assert captured.err == "", "No notice should be printed when telemetry is disabled"


def test_first_run_notice_not_printed_in_ci(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No first-run notice when running in CI."""
    for var in ["FABRIC_TELEMETRY", "FABRIC_DISABLE_TELEMETRY", "DO_NOT_TRACK"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.maybe_print_first_run_notice()  # type: ignore[attr-defined]

    captured = capsys.readouterr()
    assert captured.err == "", "No notice should be printed in CI"


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


def test_record_app_started_does_not_raise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """record_app_started must not raise even when telemetry is disabled."""
    monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.record_app_started("cli")  # type: ignore[attr-defined]


def test_record_app_exited_does_not_raise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """record_app_exited must not raise even when telemetry is disabled."""
    monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.record_app_exited(duration_ms=42.0, exit_status="ok", error_category=None)  # type: ignore[attr-defined]


def test_record_mcp_server_started_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """record_mcp_server_started must not raise even when telemetry is disabled."""
    monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.record_mcp_server_started()  # type: ignore[attr-defined]


def test_record_app_started_enabled_calls_emit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When telemetry is enabled, record_app_started must call emit_event."""
    for var in [
        "FABRIC_TELEMETRY",
        "FABRIC_DISABLE_TELEMETRY",
        "DO_NOT_TRACK",
        "CI",
        "GITHUB_ACTIONS",
        "JENKINS_URL",
        "TRAVIS",
        "CIRCLECI",
        "GITLAB_CI",
        "TF_BUILD",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    emitted: list[tuple[str, dict[str, object]]] = []

    def fake_emit(name: str, attrs: dict[str, object]) -> None:
        emitted.append((name, attrs))

    with patch.object(mod, "emit_event", side_effect=fake_emit):  # type: ignore[attr-defined]
        mod.record_app_started("cli")  # type: ignore[attr-defined]

    assert len(emitted) == 1
    event_name, _ = emitted[0]
    assert "started" in event_name


def test_record_app_exited_enabled_calls_emit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When telemetry is enabled, record_app_exited must call emit_event."""
    for var in [
        "FABRIC_TELEMETRY",
        "FABRIC_DISABLE_TELEMETRY",
        "DO_NOT_TRACK",
        "CI",
        "GITHUB_ACTIONS",
        "JENKINS_URL",
        "TRAVIS",
        "CIRCLECI",
        "GITLAB_CI",
        "TF_BUILD",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    emitted: list[tuple[str, dict[str, object]]] = []

    def fake_emit(name: str, attrs: dict[str, object]) -> None:
        emitted.append((name, attrs))

    with patch.object(mod, "emit_event", side_effect=fake_emit):  # type: ignore[attr-defined]
        mod.record_app_exited(duration_ms=100.0, exit_status="ok", error_category=None)  # type: ignore[attr-defined]

    assert len(emitted) == 1
    event_name, attrs = emitted[0]
    assert "exited" in event_name
    assert attrs.get("exit_status") == "ok"


def test_record_mcp_server_started_enabled_calls_emit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When telemetry is enabled, record_mcp_server_started must call emit_event."""
    for var in [
        "FABRIC_TELEMETRY",
        "FABRIC_DISABLE_TELEMETRY",
        "DO_NOT_TRACK",
        "CI",
        "GITHUB_ACTIONS",
        "JENKINS_URL",
        "TRAVIS",
        "CIRCLECI",
        "GITLAB_CI",
        "TF_BUILD",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    emitted: list[tuple[str, dict[str, object]]] = []

    def fake_emit(name: str, attrs: dict[str, object]) -> None:
        emitted.append((name, attrs))

    with patch.object(mod, "emit_event", side_effect=fake_emit):  # type: ignore[attr-defined]
        mod.record_mcp_server_started()  # type: ignore[attr-defined]

    assert len(emitted) == 1


# ---------------------------------------------------------------------------
# set_tenant_id stub
# ---------------------------------------------------------------------------


def test_set_tenant_id_exists_and_is_callable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """set_tenant_id must exist and be callable (stub for #366)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert callable(mod.set_tenant_id)  # type: ignore[attr-defined]
    # Must not raise
    mod.set_tenant_id("some-tenant-id")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# install_method detection
# ---------------------------------------------------------------------------


def test_install_method_returns_string(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_detect_install_method must return a non-empty string."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    method = mod._detect_install_method()  # type: ignore[attr-defined]
    assert isinstance(method, str)
    assert method in {"pip", "uv", "pipx", "source", "unknown"}


# ---------------------------------------------------------------------------
# Connection string: default is embedded
# ---------------------------------------------------------------------------


def test_default_connection_string_is_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_DEFAULT_CONNECTION_STRING must be a non-empty string."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert isinstance(mod._DEFAULT_CONNECTION_STRING, str)  # type: ignore[attr-defined]
    assert "InstrumentationKey" in mod._DEFAULT_CONNECTION_STRING  # type: ignore[attr-defined]


def test_connection_string_overridable_via_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FABRIC_TELEMETRY_CONNECTION_STRING must override the default."""
    monkeypatch.setenv("FABRIC_TELEMETRY_CONNECTION_STRING", "InstrumentationKey=custom-key")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    conn_str = mod._get_connection_string()  # type: ignore[attr-defined]
    assert conn_str == "InstrumentationKey=custom-key"


def test_connection_string_default_when_env_not_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_get_connection_string returns the default when env var is absent."""
    monkeypatch.delenv("FABRIC_TELEMETRY_CONNECTION_STRING", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    conn_str = mod._get_connection_string()  # type: ignore[attr-defined]
    assert conn_str == mod._DEFAULT_CONNECTION_STRING  # type: ignore[attr-defined]
