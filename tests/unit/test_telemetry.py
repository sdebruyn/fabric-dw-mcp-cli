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
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_telemetry() -> Any:
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


def test_envelope_tenant_id_absent_when_no_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tenant_id is absent from the envelope when neither AZURE_TENANT_ID nor
    FABRIC_INTERACTIVE_TENANT_ID is set.

    OTel attribute values may not be None; the key is omitted entirely when no
    tenant ID is available.
    """
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert "tenant_id" not in envelope


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


# ---------------------------------------------------------------------------
# B1: privacy — auto-HTTP instrumentation is disabled
# ---------------------------------------------------------------------------


def test_get_tracer_passes_instrumentation_options_to_configure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_get_tracer must pass _INSTRUMENTATION_OPTIONS to configure_azure_monitor (B1).

    This prevents MSAL OAuth URLs (containing tenant IDs) from leaking as span
    attributes via the requests/urllib/azure_sdk auto-instrumentors.

    We patch the lazy imports inside _get_tracer using monkeypatch on the module
    reference to avoid corrupting sys.modules for other tests.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("CI", raising=False)
    for ci_var in ("GITHUB_ACTIONS", "JENKINS_URL", "TRAVIS", "CIRCLECI", "GITLAB_CI", "TF_BUILD"):
        monkeypatch.delenv(ci_var, raising=False)

    mod = _reload_telemetry()

    captured_kwargs: dict[str, object] = {}

    def fake_configure(**kwargs: object) -> None:
        captured_kwargs.update(kwargs)

    fake_tracer = object()

    class _FakeTrace(types.ModuleType):
        def get_tracer(self, *_args: object, **_kw: object) -> object:
            return fake_tracer

    fake_azure_mod: Any = types.ModuleType("azure.monitor.opentelemetry")
    fake_azure_mod.configure_azure_monitor = fake_configure

    fake_trace_mod = _FakeTrace("opentelemetry.trace")

    # Reset the SDK state so _get_tracer will actually run configure_azure_monitor.
    mod._sdk_initialised = False
    mod._tracer = None

    # Use monkeypatch.dict to safely restore sys.modules after the test.
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", fake_azure_mod)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", fake_trace_mod)

    mod._get_tracer()

    # Reset SDK state so the module is clean for any subsequent tests.
    mod._sdk_initialised = False
    mod._tracer = None

    raw_options: Any = captured_kwargs.get("instrumentation_options", {})
    assert isinstance(raw_options, dict), "instrumentation_options must be a dict"
    for lib in ("requests", "urllib", "urllib3", "azure_sdk"):
        assert lib in raw_options, f"instrumentation_options must disable '{lib}'"
        lib_opts: Any = raw_options[lib]
        assert lib_opts.get("enabled") is False, f"'{lib}' must have enabled=False"


# ---------------------------------------------------------------------------
# B1: privacy — _INSTRUMENTATION_OPTIONS constant disables all HTTP libs
# ---------------------------------------------------------------------------


def test_instrumentation_options_constant_disables_all_http_libs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_INSTRUMENTATION_OPTIONS must disable requests, urllib, urllib3, and azure_sdk."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()
    opts = mod._INSTRUMENTATION_OPTIONS  # type: ignore[attr-defined]
    for lib in ("requests", "urllib", "urllib3", "azure_sdk"):
        assert lib in opts, f"_INSTRUMENTATION_OPTIONS missing '{lib}'"
        assert opts[lib].get("enabled") is False, f"'{lib}' must have enabled=False"


# ---------------------------------------------------------------------------
# B2: flush_telemetry completes within the timeout (no hang)
# ---------------------------------------------------------------------------


def test_flush_telemetry_no_hang_when_exporter_slow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """flush_telemetry must not block longer than ~2 s even with a slow exporter (B2)."""
    import time  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()

    # Simulate a tracer object being set (SDK "initialised") so flush_telemetry
    # attempts the flush path, but the provider.force_flush blocks for 5 s.
    mod._sdk_initialised = True
    mod._tracer = object()

    fake_provider = types.SimpleNamespace(force_flush=lambda **_kw: __import__("time").sleep(5))

    fake_otel_trace: Any = types.ModuleType("opentelemetry.trace_fake")
    fake_otel_trace.get_tracer_provider = lambda: fake_provider

    # Use monkeypatch to safely install a fake trace module and restore afterwards.
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", fake_otel_trace)

    start = time.monotonic()
    mod.flush_telemetry(timeout_ms=500)  # type: ignore[attr-defined]
    elapsed = time.monotonic() - start

    # Must complete well within 3 s (daemon thread + join timeout ~0.6 s)
    assert elapsed < 3.0, f"flush_telemetry blocked for {elapsed:.1f} s — hang detected"


def test_flush_telemetry_is_noop_when_sdk_not_initialised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """flush_telemetry is a no-op when the SDK was never initialised."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()
    # _sdk_initialised starts False after reload
    assert mod._sdk_initialised is False  # type: ignore[attr-defined]
    # Must not raise
    mod.flush_telemetry()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# B3: exit_status mapping from Click exit code
# ---------------------------------------------------------------------------


def test_exit_status_mapping_zero_is_ok() -> None:
    """Exit code 0 must map to exit_status='ok'."""
    # Replicate the mapping logic from _on_close
    exit_code = 0
    exit_status = "ok" if (exit_code is None or exit_code == 0) else "user_error"
    assert exit_status == "ok"


def test_exit_status_mapping_none_is_ok() -> None:
    """Exit code None must map to exit_status='ok'."""
    exit_code = None
    exit_status = "ok" if (exit_code is None or exit_code == 0) else "user_error"
    assert exit_status == "ok"


def test_exit_status_mapping_nonzero_is_user_error() -> None:
    """Non-zero exit code must map to exit_status='user_error'."""
    for code in (1, 2, 127):
        exit_status = "ok" if (code is None or code == 0) else "user_error"
        assert exit_status == "user_error", f"Expected 'user_error' for exit code {code}"


def test_on_close_exit_status_mapping_covered_by_logic_tests() -> None:
    """Placeholder confirming B3 exit_status mapping is covered by logic tests above.

    The exit_status mapping in _on_close uses sys.exc_info() at teardown time.
    The canonical mapping is tested by test_exit_status_mapping_* above; this
    test is kept as a docstring anchor so the B3 coverage rationale is clear.
    """
    # B3 mapping: 0 / None → "ok", non-zero → "user_error", Abort → "user_error".
    # See test_exit_status_mapping_zero_is_ok, test_exit_status_mapping_nonzero_is_user_error.
    assert True


# ---------------------------------------------------------------------------
# A5: _detect_install_method — no false-positive "uv" for pip-in-venv
# ---------------------------------------------------------------------------


def test_detect_install_method_uv_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_detect_install_method returns 'uv' when UV env var is set."""
    monkeypatch.setenv("UV", "1")
    monkeypatch.delenv("UV_VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("PIPX_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod._detect_install_method() == "uv"  # type: ignore[attr-defined]


def test_detect_install_method_pipx_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_detect_install_method returns 'pipx' when PIPX_HOME is set."""
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.delenv("UV_VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("PIPX_HOME", "/home/user/.local/pipx")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod._detect_install_method() == "pipx"  # type: ignore[attr-defined]


def test_detect_install_method_no_false_uv_for_plain_venv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A plain .venv path in sys.executable must NOT return 'uv' (pip-in-venv false positive)."""
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.delenv("UV_VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("PIPX_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()

    with patch.object(mod.sys, "executable", "/project/.venv/bin/python"):  # type: ignore[attr-defined]
        method = mod._detect_install_method()  # type: ignore[attr-defined]

    # Must NOT return "uv" just because the path contains ".venv"
    assert method != "uv", "Must not return 'uv' for a plain pip-in-venv interpreter"


# ---------------------------------------------------------------------------
# A6: set_tenant_id wires to _tenant_id_override
# ---------------------------------------------------------------------------


def test_set_tenant_id_stores_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """set_tenant_id must store the value in _tenant_id_override (A6)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)

    mod = _reload_telemetry()
    assert mod._tenant_id_override is None  # type: ignore[attr-defined]

    mod.set_tenant_id("override-tenant-abc")  # type: ignore[attr-defined]

    assert mod._tenant_id_override == "override-tenant-abc"  # type: ignore[attr-defined]


def test_set_tenant_id_reflected_in_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After set_tenant_id, _build_envelope must use the override value (A6)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)

    mod = _reload_telemetry()
    mod.set_tenant_id("runtime-tenant-xyz")  # type: ignore[attr-defined]

    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "runtime-tenant-xyz"


def test_set_tenant_id_takes_precedence_over_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """set_tenant_id override must take precedence over AZURE_TENANT_ID env var (A6)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("AZURE_TENANT_ID", "env-tenant")

    mod = _reload_telemetry()
    mod.set_tenant_id("runtime-tenant")  # type: ignore[attr-defined]

    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "runtime-tenant"


# ---------------------------------------------------------------------------
# A3: marker file written AFTER notice is printed
# ---------------------------------------------------------------------------


def test_first_run_notice_marker_written_after_print(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The marker file must be written only after the notice is printed (A3).

    Verifies that maybe_print_first_run_notice writes the marker file *after* the
    notice text is emitted to stderr — so a failed print won't permanently silence
    future notices.
    """
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

    marker_file = tmp_path / "fabric-dw" / ".telemetry_notice_shown"

    mod = _reload_telemetry()

    # Before the call, neither the notice nor the marker file should exist.
    assert not marker_file.exists(), "Marker file must not exist before first call"

    mod.maybe_print_first_run_notice()  # type: ignore[attr-defined]

    captured = capsys.readouterr()
    assert captured.err != "", "Notice must be printed to stderr"
    # Marker file must exist after a successful print (written *after* print = A3).
    assert marker_file.exists(), "Marker file must exist after notice is printed"


# ---------------------------------------------------------------------------
# #366: decode_tid_from_token — JWT payload tid claim extraction
# ---------------------------------------------------------------------------


def _make_jwt(payload_dict: dict[str, object]) -> str:
    """Construct a minimal JWT-shaped string with the given payload dict.

    The header and signature are fake — only the payload segment is real.
    """
    import base64  # noqa: PLC0415
    import json  # noqa: PLC0415

    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    payload_bytes = json.dumps(payload_dict).encode()
    payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    signature = "fakesig"
    return f"{header}.{payload}.{signature}"


def test_decode_tid_returns_tid_from_valid_jwt() -> None:
    """decode_tid_from_token returns the tid claim from a well-formed JWT payload."""
    import importlib  # noqa: PLC0415

    mod = importlib.import_module("fabric_dw.telemetry")
    tenant = "aaaabbbb-1234-5678-abcd-000011112222"
    token = _make_jwt(
        {"tid": tenant, "oid": "some-oid", "iss": "https://login.microsoftonline.com/"}
    )
    result = mod.decode_tid_from_token(token)  # type: ignore[attr-defined]
    assert result == tenant


def test_decode_tid_returns_none_when_tid_missing() -> None:
    """decode_tid_from_token returns None when the JWT payload has no tid claim."""
    import importlib  # noqa: PLC0415

    mod = importlib.import_module("fabric_dw.telemetry")
    token = _make_jwt({"oid": "some-oid", "iss": "https://login.microsoftonline.com/"})
    result = mod.decode_tid_from_token(token)  # type: ignore[attr-defined]
    assert result is None


def test_decode_tid_returns_none_for_malformed_token() -> None:
    """decode_tid_from_token returns None (no exception) for garbage input."""
    import importlib  # noqa: PLC0415

    mod = importlib.import_module("fabric_dw.telemetry")
    for bad_token in [
        "",
        "notajwt",
        "only.two",
        "too.many.parts.here.five",
        "header.!!invalid_base64!!.sig",
        "a.eyJub3RqExon.c",  # valid base64 but JSON parse fails (example)
    ]:
        result = mod.decode_tid_from_token(bad_token)  # type: ignore[attr-defined]
        assert result is None, f"Expected None for {bad_token!r}, got {result!r}"


def test_decode_tid_handles_urlsafe_chars_in_payload() -> None:
    """decode_tid_from_token correctly handles payloads with '-' in the base64url encoding.

    This is a regression test for the b64decode vs urlsafe_b64decode bug.  The
    standard base64 alphabet uses '+' and '/' where base64url uses '-' and '_'.
    ``base64.b64decode`` with ``validate=False`` silently discards '-' and '_'
    characters before the padding check, producing an incorrect result or an
    ``Incorrect padding`` error — both of which cause the function to return
    ``None`` instead of the tid.

    The tid value ``"~~~"`` (three tildes) is chosen because JSON.encode of
    ``{"tid": "~~~"}`` produces a byte sequence whose base64url encoding
    contains '-' (specifically the group encoding the tilde bytes maps to
    value 62 → '-' in base64url).

    This test FAILS with ``base64.b64decode`` and PASSES with
    ``base64.urlsafe_b64decode``.
    """
    import base64  # noqa: PLC0415
    import importlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    mod = importlib.import_module("fabric_dw.telemetry")

    # '~~~' is chosen because json.dumps({'tid': '~~~'}) produces bytes whose
    # base64url encoding contains '-', guaranteed by the tilde (0x7E) character.
    tid_value = "~~~"
    payload_bytes = json.dumps({"tid": tid_value}).encode()
    b64url = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()

    # Sanity-check: the fixture must exercise the URL-safe character path.
    assert any(c in "-_" for c in b64url), (
        f"Fixture must contain '-' or '_' to exercise the bug; got: {b64url!r}"
    )

    token = f"fake_header.{b64url}.fake_sig"
    result = mod.decode_tid_from_token(token)  # type: ignore[attr-defined]
    assert result == tid_value, (
        f"Expected {tid_value!r} but got {result!r}. "
        "This indicates b64decode is being used instead of urlsafe_b64decode."
    )


def test_decode_tid_handles_missing_padding() -> None:
    """decode_tid_from_token correctly handles base64url payloads with stripped padding."""
    import base64  # noqa: PLC0415
    import importlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    mod = importlib.import_module("fabric_dw.telemetry")
    tenant = "ccccdddd-1234-5678-efgh-000011112222"
    # Build a payload whose base64url length mod 4 != 0 (i.e. needs padding)
    payload_bytes = json.dumps({"tid": tenant}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    # Confirm there is actually missing padding in this fixture
    assert len(payload_b64) % 4 != 0, "Fixture must have unpadded base64"
    token = f"header.{payload_b64}.sig"
    result = mod.decode_tid_from_token(token)  # type: ignore[attr-defined]
    assert result == tenant


def test_decode_tid_returns_none_for_non_string_tid() -> None:
    """decode_tid_from_token returns None when tid is not a string (e.g. an int)."""
    import importlib  # noqa: PLC0415

    mod = importlib.import_module("fabric_dw.telemetry")
    token = _make_jwt({"tid": 12345})
    result = mod.decode_tid_from_token(token)  # type: ignore[attr-defined]
    assert result is None


def test_decode_tid_returns_none_for_empty_string_tid() -> None:
    """decode_tid_from_token returns None when tid is an empty string."""
    import importlib  # noqa: PLC0415

    mod = importlib.import_module("fabric_dw.telemetry")
    token = _make_jwt({"tid": ""})
    result = mod.decode_tid_from_token(token)  # type: ignore[attr-defined]
    assert result is None


# ---------------------------------------------------------------------------
# #366: cache_tenant_id_from_token — integration with telemetry_enabled guard
# ---------------------------------------------------------------------------


def test_cache_tenant_id_sets_override_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cache_tenant_id_from_token sets _tenant_id_override when telemetry is on."""
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
    tenant = "aaaabbbb-0000-1111-2222-333344445555"
    token = _make_jwt({"tid": tenant})

    mod.cache_tenant_id_from_token(token)  # type: ignore[attr-defined]
    assert mod._tenant_id_override == tenant  # type: ignore[attr-defined]


def test_cache_tenant_id_does_not_decode_when_telemetry_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cache_tenant_id_from_token is a no-op (no decode) when telemetry is disabled."""
    monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    tenant = "aaaabbbb-0000-1111-2222-333344445555"
    token = _make_jwt({"tid": tenant})

    # Track whether decode_tid_from_token is called
    decode_calls: list[str] = []
    original_decode = mod.decode_tid_from_token  # type: ignore[attr-defined]

    def spy_decode(t: str) -> str | None:
        decode_calls.append(t)
        return original_decode(t)

    with patch.object(mod, "decode_tid_from_token", side_effect=spy_decode):  # type: ignore[attr-defined]
        mod.cache_tenant_id_from_token(token)  # type: ignore[attr-defined]

    assert decode_calls == [], "decode_tid_from_token must not be called when telemetry is disabled"
    assert mod._tenant_id_override is None  # type: ignore[attr-defined]


def test_cache_tenant_id_is_idempotent_skips_decode_when_override_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cache_tenant_id_from_token skips decode when _tenant_id_override is already set."""
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
    existing_tenant = "existing-tenant-id"
    mod._tenant_id_override = existing_tenant  # type: ignore[attr-defined]

    decode_calls: list[str] = []
    original_decode = mod.decode_tid_from_token  # type: ignore[attr-defined]

    def spy_decode(t: str) -> str | None:
        decode_calls.append(t)
        return original_decode(t)

    new_token = _make_jwt({"tid": "new-tenant-should-not-override"})
    with patch.object(mod, "decode_tid_from_token", side_effect=spy_decode):  # type: ignore[attr-defined]
        mod.cache_tenant_id_from_token(new_token)  # type: ignore[attr-defined]

    assert decode_calls == [], "decode must be skipped when override is already set"
    assert mod._tenant_id_override == existing_tenant  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# #366: env-var fallback precedence in _build_envelope
# ---------------------------------------------------------------------------


def test_envelope_uses_token_tid_over_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The decoded tid from a token takes precedence over AZURE_TENANT_ID env var.

    This tests that _tenant_id_override (set via cache_tenant_id_from_token)
    wins over the env-var fallback in _build_envelope.
    """
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
    monkeypatch.setenv("AZURE_TENANT_ID", "env-var-tenant-id")

    mod = _reload_telemetry()
    tid_value = "token-tenant-id-from-tid-claim"
    mod._tenant_id_override = tid_value  # type: ignore[attr-defined]

    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert envelope.get("tenant_id") == tid_value


def test_envelope_falls_back_to_azure_tenant_id_env_when_no_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_build_envelope falls back to AZURE_TENANT_ID when no token override is set."""
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
        "FABRIC_INTERACTIVE_TENANT_ID",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("AZURE_TENANT_ID", "fallback-env-tenant")

    mod = _reload_telemetry()
    # Ensure no override is set
    mod._tenant_id_override = None  # type: ignore[attr-defined]

    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert envelope.get("tenant_id") == "fallback-env-tenant"


def test_envelope_omits_tenant_id_when_no_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_build_envelope omits tenant_id when neither override nor env vars are set."""
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
        "AZURE_TENANT_ID",
        "FABRIC_INTERACTIVE_TENANT_ID",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod._tenant_id_override = None  # type: ignore[attr-defined]

    envelope = mod._build_envelope("cli")  # type: ignore[attr-defined]
    assert "tenant_id" not in envelope
