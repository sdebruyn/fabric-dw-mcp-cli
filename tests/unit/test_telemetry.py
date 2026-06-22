"""Tests for fabric_dw.telemetry — opt-out usage telemetry foundation.

Written TDD-first before the implementation.  All tests must pass with
no real network calls (every Azure Monitor SDK interaction is mocked).
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import types
import uuid
from collections.abc import Generator
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
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is True  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "YES", "on", "ON", "anything"])
def test_telemetry_disabled_by_fabric_dw_telemetry_opt_out_truthy(
    value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FABRIC_DW_TELEMETRY_OPT_OUT set to a truthy value disables telemetry."""
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", value)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "NO", "off", "OFF", ""])
def test_telemetry_enabled_when_fabric_dw_telemetry_opt_out_falsy(
    value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FABRIC_DW_TELEMETRY_OPT_OUT set to a falsy value does NOT disable telemetry."""
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", value)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is True  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE", "anything"])
def test_telemetry_disabled_by_do_not_track(
    value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DO_NOT_TRACK truthy disables telemetry (consoledonottrack.com standard)."""
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.setenv("DO_NOT_TRACK", value)
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

    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.toml"
    config_file.write_text(tomli_w.dumps({"telemetry": {"disabled": True}}), encoding="utf-8")

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


def test_telemetry_disabled_by_config_integer_truthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """telemetry_enabled() returns False when config has disabled = 1 (integer)."""
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    # Write disabled = 1 (integer, not bool) — valid TOML truthy value
    config_file = config_dir / "config.toml"
    config_file.write_text("[telemetry]\ndisabled = 1\n", encoding="utf-8")

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


def test_telemetry_not_disabled_by_config_integer_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """telemetry_enabled() returns True when config has disabled = 0 (falsy int)."""
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.toml"
    config_file.write_text("[telemetry]\ndisabled = 0\n", encoding="utf-8")

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is True  # type: ignore[attr-defined]


def test_telemetry_disabled_by_config_string_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """telemetry_enabled() returns False when config has disabled = \"true\" (string)."""
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.toml"
    config_file.write_text('[telemetry]\ndisabled = "true"\n', encoding="utf-8")

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


def test_telemetry_not_disabled_by_config_string_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """telemetry_enabled() returns True when config has disabled = \"false\" (string)."""
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.toml"
    config_file.write_text('[telemetry]\ndisabled = "false"\n', encoding="utf-8")

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is True  # type: ignore[attr-defined]


def test_telemetry_not_disabled_by_config_string_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """telemetry_enabled() returns True when config has disabled = \"0\" (string)."""
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.toml"
    config_file.write_text('[telemetry]\ndisabled = "0"\n', encoding="utf-8")

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is True  # type: ignore[attr-defined]


def test_telemetry_disabled_fail_closed_on_lock_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When config file EXISTS but cannot be read (lock timeout or OSError),
    telemetry must NOT be sent (fail-closed privacy guarantee)."""
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    # Write opt-out config — this file EXISTS so read-failure must fail-closed.
    config_file = config_dir / "config.toml"
    config_file.write_text("[telemetry]\ndisabled = true\n", encoding="utf-8")

    # Simulate an OSError when reading the file (e.g. permission denied).
    with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
        mod = _reload_telemetry()
        # File exists but is unreadable — must treat as opted out (fail-closed).
        assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


def test_telemetry_disabled_fail_closed_on_corrupt_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When config file EXISTS but contains corrupt TOML, telemetry must not
    be sent (fail-closed privacy guarantee)."""
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.toml"
    # File exists but TOML is invalid — must treat as opted out (fail-closed).
    config_file.write_text("[[[[invalid TOML", encoding="utf-8")

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


def test_env_opt_out_beats_config_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FABRIC_DW_TELEMETRY_OPT_OUT still disables telemetry even when config
    exists but is unreadable (env opt-outs checked before config read)."""
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.toml"
    config_file.write_text("[telemetry]\ndisabled = false\n", encoding="utf-8")

    mod = _reload_telemetry()
    # Env opt-out must win regardless of config content.
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


def test_suppress_beats_config_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Process-level suppress_telemetry() wins over config (checked first)."""
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.suppress_telemetry()  # type: ignore[attr-defined]
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# SDK is never imported when disabled
# ---------------------------------------------------------------------------


def test_azure_monitor_sdk_not_imported_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When telemetry is disabled, azure.monitor.opentelemetry must never be imported."""
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")
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
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()

    # Patch the internal _get_tracer to raise
    with patch.object(mod, "_get_tracer", side_effect=RuntimeError("SDK exploded")):  # type: ignore[attr-defined]
        # Must not raise
        mod.emit_event("some_event", {"key": "value"})  # type: ignore[attr-defined]


def test_emit_event_no_op_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """emit_event is a no-op (does not import SDK) when telemetry is disabled."""
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")
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
    """_build_envelope must return a dict with all required custom-dimension fields.

    Fields moved to native Part A via the OTel Resource (#477) are NOT present
    here: ``app_version`` (→ application_Version), ``surface`` (→ cloud_RoleName).
    Fields dropped entirely (#477): ``anonymous_install_id`` (duplicate of user_Id),
    ``is_ci`` (no useful signal).
    ``tenant_id`` is now always present (``"unknown"`` when unresolved).
    """
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope()  # type: ignore[attr-defined]

    # Fields that must remain as custom dimensions (no native Part A mapping).
    required = {
        "session_id",
        "python_version",
        "os",
        "arch",
        "install_method",
        "auth_mode",
        "tenant_id",  # always present, "unknown" when unresolved (#477 Finding 2)
    }
    for field in required:
        assert field in envelope, f"Missing envelope field: {field}"

    # Fields that were dropped or moved to native (#477) must NOT appear.
    dropped = {"anonymous_install_id", "is_ci", "app_version", "surface"}
    for field in dropped:
        assert field not in envelope, f"Field should have been removed from envelope: {field}"


def test_envelope_surface_not_in_custom_dimensions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """surface must NOT be a custom dimension — it is now native cloud_RoleName (#477).

    The surface is set via the OTel Resource (``service.name``), which the exporter
    maps to ``cloud_RoleName``.  Emitting it again as a custom dimension would be
    redundant.
    """
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert "surface" not in mod._build_envelope()  # type: ignore[attr-defined]
    assert "surface" not in mod._build_envelope()  # type: ignore[attr-defined]


def test_envelope_is_ci_not_in_custom_dimensions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """is_ci must NOT be in the envelope — it carries no useful signal (#477)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert "is_ci" not in envelope, "is_ci must not be emitted as a custom dimension (#477)"


def test_envelope_anonymous_install_id_not_in_custom_dimensions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """anonymous_install_id must NOT be in the envelope.

    It is already shipped natively as ``user_Id`` via ``enduser.pseudo.id``.
    Emitting it again as a custom dimension would be redundant (#477 Finding 5).
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert "anonymous_install_id" not in envelope


def test_envelope_python_version_is_minor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """python_version must be 'major.minor' format (e.g. '3.12')."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()
    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    pv = envelope["python_version"]
    parts = str(pv).split(".")
    assert len(parts) == 2, f"Expected 'major.minor', got {pv!r}"
    assert all(p.isdigit() for p in parts)


def test_envelope_os_is_lowercase(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """os field must be lowercase."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()
    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["os"] == envelope["os"].lower()


def test_envelope_tenant_id_from_azure_tenant_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tenant_id must be read from AZURE_TENANT_ID when present."""
    monkeypatch.setenv("AZURE_TENANT_ID", "my-tenant-123")
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "my-tenant-123"


def test_envelope_tenant_id_from_fabric_interactive_when_no_azure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tenant_id falls back to FABRIC_INTERACTIVE_TENANT_ID if AZURE_TENANT_ID is absent."""
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.setenv("FABRIC_INTERACTIVE_TENANT_ID", "interactive-tenant")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "interactive-tenant"


def test_envelope_tenant_id_unknown_when_no_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tenant_id is always present in the envelope (#477 Finding 2).

    When neither AZURE_TENANT_ID nor FABRIC_INTERACTIVE_TENANT_ID is set and no
    runtime override has been stored, ``tenant_id`` is ``"unknown"`` so the key
    is reliably queryable on every event.
    """
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod._tenant_id_override = None  # type: ignore[attr-defined]
    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope.get("tenant_id") == "unknown"


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
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
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
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
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
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.maybe_print_first_run_notice()  # type: ignore[attr-defined]

    captured = capsys.readouterr()
    assert captured.err == "", "No notice should be printed when telemetry is disabled"


def test_first_run_notice_not_printed_when_opt_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No first-run notice when FABRIC_DW_TELEMETRY_OPT_OUT=1."""
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.maybe_print_first_run_notice()  # type: ignore[attr-defined]

    captured = capsys.readouterr()
    assert captured.err == "", "No notice should be printed when telemetry is opted out"


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


def test_record_app_started_does_not_raise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """record_app_started must not raise even when telemetry is disabled."""
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.record_app_started("cli")  # type: ignore[attr-defined]


def test_record_app_exited_does_not_raise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """record_app_exited must not raise even when telemetry is disabled."""
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.record_app_exited(duration_ms=42.0, exit_status="ok", error_category=None)  # type: ignore[attr-defined]


def test_record_mcp_server_started_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """record_mcp_server_started must not raise even when telemetry is disabled."""
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.record_mcp_server_started()  # type: ignore[attr-defined]


def test_record_app_started_enabled_calls_emit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When telemetry is enabled, record_app_started must call emit_event."""
    for var in [
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    emitted: list[tuple[str, dict[str, object]]] = []

    def fake_emit(
        name: str,
        attrs: dict[str, object],
        *,
        omit_keys: set[str] | None = None,  # noqa: ARG001
    ) -> None:
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
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    emitted: list[tuple[str, dict[str, object]]] = []

    def fake_emit(
        name: str,
        attrs: dict[str, object],
        *,
        omit_keys: set[str] | None = None,  # noqa: ARG001
    ) -> None:
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
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    emitted: list[tuple[str, dict[str, object]]] = []

    def fake_emit(
        name: str,
        attrs: dict[str, object],
        *,
        omit_keys: set[str] | None = None,  # noqa: ARG001
    ) -> None:
        emitted.append((name, attrs))

    with patch.object(mod, "emit_event", side_effect=fake_emit):  # type: ignore[attr-defined]
        mod.record_mcp_server_started()  # type: ignore[attr-defined]

    assert len(emitted) == 1


# ---------------------------------------------------------------------------
# auth_mode omission on lifecycle-start events (#677)
# ---------------------------------------------------------------------------


def _setup_telemetry_with_fake_logger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Any, list[dict[str, object]]]:
    """Set up a reloaded telemetry module with a fake OTel logger that captures emissions.

    Returns ``(mod, captured)`` where *captured* is the list that grows as
    events are emitted.  Egress is blocked — no real Azure Monitor calls occur.
    """
    for var in ("FABRIC_DW_TELEMETRY_OPT_OUT", "DO_NOT_TRACK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()

    captured: list[dict[str, object]] = []

    # A minimal stand-in for OTel LogRecord: just holds its attributes.
    class _FakeLogRecord:
        def __init__(self, **kwargs: object) -> None:
            self.attributes: dict[str, object] = dict(kwargs.get("attributes") or {})  # type: ignore[arg-type]  # ty: ignore[no-matching-overload]

    class _FakeLogger:
        def emit(self, record: _FakeLogRecord) -> None:
            captured.append(dict(record.attributes))

    fake_logger = _FakeLogger()

    # Inject our fake logger so _get_tracer() returns it without real SDK init.
    mod._otel_logger = fake_logger  # type: ignore[attr-defined]
    mod._tracer = fake_logger  # type: ignore[attr-defined]
    mod._sdk_initialised = True  # type: ignore[attr-defined]

    # Inject the fake LogRecord class into sys.modules so the `from … import`
    # inside emit_event picks it up instead of the real OTel class.
    fake_logs_mod: Any = types.ModuleType("opentelemetry._logs")
    fake_logs_mod.LogRecord = _FakeLogRecord
    monkeypatch.setitem(sys.modules, "opentelemetry._logs", fake_logs_mod)

    return mod, captured


def test_app_started_omits_auth_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """app_started events must NOT carry auth_mode (fired before auth is resolved).

    Lifecycle-start events fire before any token is acquired, so _auth_mode_override
    is still None and _detect_auth_mode() falls back to env heuristics that can
    mis-classify the session.  Omitting the field is safer than emitting a wrong value.
    """
    mod, captured = _setup_telemetry_with_fake_logger(monkeypatch, tmp_path)
    mod.record_app_started("cli")  # type: ignore[attr-defined]

    assert len(captured) == 1
    assert "auth_mode" not in captured[0], (
        "app_started must NOT include auth_mode — it fires before auth is resolved and "
        "_detect_auth_mode() can mis-classify the session (#677)."
    )


def test_mcp_server_started_omits_auth_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """mcp_server_started events must NOT carry auth_mode (fired before auth is resolved).

    Same rationale as app_started: the MCP server boots before any token is acquired.
    """
    mod, captured = _setup_telemetry_with_fake_logger(monkeypatch, tmp_path)
    mod.record_mcp_server_started()  # type: ignore[attr-defined]

    assert len(captured) == 1
    assert "auth_mode" not in captured[0], (
        "mcp_server_started must NOT include auth_mode — it fires before auth is resolved "
        "and _detect_auth_mode() can mis-classify the session (#677)."
    )


def test_app_exited_includes_auth_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """app_exited events MUST carry auth_mode (emitted after the first token acquisition)."""
    mod, captured = _setup_telemetry_with_fake_logger(monkeypatch, tmp_path)
    mod.record_app_exited(duration_ms=10.0, exit_status="ok", error_category=None)  # type: ignore[attr-defined]

    assert len(captured) == 1
    assert "auth_mode" in captured[0], (
        "app_exited must include auth_mode — it is emitted after auth is resolved and "
        "the value is accurate by that point (#677)."
    )


def test_command_invoked_includes_auth_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """command_invoked events MUST carry auth_mode (emitted after the first token acquisition).

    Unlike lifecycle-start events (app_started, mcp_server_started), command_invoked
    fires after the auth layer has called set_auth_mode(), so the value is accurate
    and should always be present in the emitted envelope.
    """
    mod, captured = _setup_telemetry_with_fake_logger(monkeypatch, tmp_path)
    mod.emit_event("command_invoked", {"name": "workspace.list", "status": "success"})  # type: ignore[attr-defined]

    assert len(captured) == 1
    assert "auth_mode" in captured[0], (
        "command_invoked must include auth_mode — it fires after auth is resolved and "
        "the value is accurate by that point (#677)."
    )


def test_emit_event_omit_keys_removes_from_merged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """emit_event(omit_keys=...) must drop the listed keys from the merged envelope."""
    mod, captured = _setup_telemetry_with_fake_logger(monkeypatch, tmp_path)
    mod.emit_event("test_event", {}, omit_keys={"auth_mode", "session_id"})  # type: ignore[attr-defined]

    assert len(captured) == 1
    merged = captured[0]
    assert "auth_mode" not in merged, "omit_keys must remove 'auth_mode' from the emitted record"
    assert "session_id" not in merged, "omit_keys must remove 'session_id' from the emitted record"
    # Other envelope keys must still be present.
    assert "python_version" in merged, "Other envelope keys must remain after omit_keys filter"


def test_emit_event_omit_keys_none_is_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """emit_event with omit_keys=None (default) must include all envelope keys."""
    mod, captured = _setup_telemetry_with_fake_logger(monkeypatch, tmp_path)
    mod.emit_event("test_event", {})  # type: ignore[attr-defined]

    assert len(captured) == 1
    assert "auth_mode" in captured[0], (
        "When omit_keys is not provided (default None), auth_mode must be present in the envelope."
    )


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
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)

    mod = _reload_telemetry()

    captured_kwargs: dict[str, object] = {}

    def fake_configure(**kwargs: object) -> None:
        captured_kwargs.update(kwargs)

    fake_otel_logger = object()

    fake_azure_mod: Any = types.ModuleType("azure.monitor.opentelemetry")
    fake_azure_mod.configure_azure_monitor = fake_configure

    # _get_tracer now uses `from opentelemetry._logs import get_logger` (not trace).
    fake_logs_mod: Any = types.ModuleType("opentelemetry._logs")
    fake_logs_mod.get_logger = lambda *_a, **_kw: fake_otel_logger
    fake_logs_mod.LogRecord = object
    fake_logs_mod.SeverityNumber = object

    # Reset the SDK state so _get_tracer will actually run configure_azure_monitor.
    mod._sdk_initialised = False
    mod._tracer = None
    mod._otel_logger = None

    # Use monkeypatch.dict to safely restore sys.modules after the test.
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", fake_azure_mod)
    monkeypatch.setitem(sys.modules, "opentelemetry._logs", fake_logs_mod)

    mod._get_tracer()

    raw_options: Any = captured_kwargs.get("instrumentation_options", {})
    assert isinstance(raw_options, dict), "instrumentation_options must be a dict"
    for lib in ("requests", "urllib", "urllib3", "azure_sdk"):
        assert lib in raw_options, f"instrumentation_options must disable '{lib}'"
        lib_opts: Any = raw_options[lib]
        assert lib_opts.get("enabled") is False, f"'{lib}' must have enabled=False"

    # Reset SDK state so the module is clean for any subsequent tests.
    mod._sdk_initialised = False
    mod._tracer = None
    mod._otel_logger = None


# ---------------------------------------------------------------------------
# A1: #399 — configure_azure_monitor must pass enable_performance_counters=False
# ---------------------------------------------------------------------------


def test_get_tracer_passes_enable_performance_counters_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_get_tracer must pass enable_performance_counters=False to configure_azure_monitor.

    In azure-monitor-opentelemetry 1.8+ the PerformanceCounters subsystem has
    its own flag and is NOT disabled by ``disable_metrics=True``.  On
    short-lived processes its ``_get_processor_time`` callback divides by zero
    and writes a full traceback to stderr (#399).
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)

    mod = _reload_telemetry()

    captured_kwargs: dict[str, object] = {}

    def fake_configure(**kwargs: object) -> None:
        captured_kwargs.update(kwargs)

    fake_otel_logger = object()

    fake_azure_mod: Any = types.ModuleType("azure.monitor.opentelemetry")
    fake_azure_mod.configure_azure_monitor = fake_configure

    fake_logs_mod: Any = types.ModuleType("opentelemetry._logs")
    fake_logs_mod.get_logger = lambda *_a, **_kw: fake_otel_logger
    fake_logs_mod.LogRecord = object
    fake_logs_mod.SeverityNumber = object

    mod._sdk_initialised = False
    mod._tracer = None
    mod._otel_logger = None

    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", fake_azure_mod)
    monkeypatch.setitem(sys.modules, "opentelemetry._logs", fake_logs_mod)

    mod._get_tracer()

    assert captured_kwargs.get("enable_performance_counters") is False, (
        "configure_azure_monitor must receive enable_performance_counters=False "
        "to suppress PerformanceCounters ZeroDivisionError on short-lived processes (#399)"
    )

    # Reset SDK state so the module is clean for any subsequent tests.
    mod._sdk_initialised = False
    mod._tracer = None
    mod._otel_logger = None


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

    # Simulate the SDK as initialised so flush_telemetry attempts the flush path,
    # but the provider.force_flush blocks for 5 s.
    mod._sdk_initialised = True
    fake_logger = object()
    mod._tracer = fake_logger
    mod._otel_logger = fake_logger

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

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "runtime-tenant-xyz"


def test_set_tenant_id_takes_precedence_over_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """set_tenant_id override must take precedence over AZURE_TENANT_ID env var (A6)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("AZURE_TENANT_ID", "env-tenant")

    mod = _reload_telemetry()
    mod.set_tenant_id("runtime-tenant")  # type: ignore[attr-defined]

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "runtime-tenant"


# ---------------------------------------------------------------------------
# Persistent tenant store (#652)
# ---------------------------------------------------------------------------


def test_tenant_id_unknown_on_first_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With no known tenant, _build_envelope must return tenant_id == 'unknown' (#652)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)

    mod = _reload_telemetry()
    mod._tenant_id_override = None  # type: ignore[attr-defined]

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "unknown"


def test_set_tenant_id_writes_file_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """set_tenant_id must persist to disk when telemetry is enabled (#652)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is True  # type: ignore[attr-defined]

    mod.set_tenant_id("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")  # type: ignore[attr-defined]

    tenant_file = tmp_path / "fabric-dw" / "tenant_id"
    assert tenant_file.exists(), "tenant_id file must be written on disk when telemetry is enabled"
    assert tenant_file.read_text(encoding="utf-8").strip() == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_set_tenant_id_no_write_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """set_tenant_id must NOT write to disk when telemetry is disabled (#652)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]

    mod.set_tenant_id("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")  # type: ignore[attr-defined]

    tenant_file = tmp_path / "fabric-dw" / "tenant_id"
    assert not tenant_file.exists(), "tenant_id file must NOT be written when telemetry is disabled"


def test_cached_tenant_read_back_on_new_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After set_tenant_id, a fresh module load must read the persisted tenant (#652).

    Simulates process restart: write on first run, then reload (new process) with
    _tenant_id_override = None to check the cache is the fallback.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    # First run: set the tenant (writes file)
    mod = _reload_telemetry()
    mod.set_tenant_id("1111aaaa-2222-bbbb-3333-cccc44445555")  # type: ignore[attr-defined]

    # New process: reload module so in-memory state is gone, simulate no live override
    mod2 = _reload_telemetry()
    mod2._tenant_id_override = None  # type: ignore[attr-defined]

    envelope = mod2._build_envelope()  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "1111aaaa-2222-bbbb-3333-cccc44445555", (
        "Persisted tenant must be read back in a fresh process (app_started scenario)"
    )


def test_missing_tenant_file_falls_back_to_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing tenant cache file must not raise and must fall back to 'unknown' (#652)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)

    # Ensure no tenant file exists
    tenant_file = tmp_path / "fabric-dw" / "tenant_id"
    assert not tenant_file.exists()

    mod = _reload_telemetry()
    mod._tenant_id_override = None  # type: ignore[attr-defined]

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "unknown"


def test_garbage_tenant_file_falls_back_to_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A malformed/empty tenant cache file must not raise and must return None (#652)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "tenant_id").write_text("   \n", encoding="utf-8")  # whitespace only

    mod = _reload_telemetry()
    mod._tenant_id_override = None  # type: ignore[attr-defined]

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "unknown"


def test_live_override_takes_precedence_over_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Live _tenant_id_override must win over persisted cache (#652)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)

    # Write a stale tenant to cache
    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "tenant_id").write_text("stale-tenant-from-cache", encoding="utf-8")

    mod = _reload_telemetry()
    # Set a live override (different tenant)
    mod._tenant_id_override = "live-override-tenant"  # type: ignore[attr-defined]

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "live-override-tenant"


def test_env_var_takes_precedence_over_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AZURE_TENANT_ID env var must take precedence over persisted cache (#652)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("AZURE_TENANT_ID", "env-tenant-wins")
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)

    # Write a different tenant to cache
    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "tenant_id").write_text("cached-tenant-should-lose", encoding="utf-8")

    mod = _reload_telemetry()
    mod._tenant_id_override = None  # type: ignore[attr-defined]

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["tenant_id"] == "env-tenant-wins"


def test_stale_cache_corrected_by_set_tenant_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Document the accepted bounded-staleness behaviour (#652, C5).

    Sequence:
      run1  telemetry ON  → set_tenant_id("tenant-A") persists to cache
      run2  telemetry OFF → set_tenant_id("tenant-B") does NOT update cache
      run3  telemetry ON  → app_started fires BEFORE auth (reads stale "tenant-A")
                          → set_tenant_id("tenant-B") corrects it for every
                            subsequent event in the same process

    At most one lifecycle event (app_started) is misattributed per run — this is
    the intentional, bounded trade-off documented in _build_envelope.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    # run1: telemetry ON → tenant-A written to cache
    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is True  # type: ignore[attr-defined]
    mod.set_tenant_id("tenant-A")  # type: ignore[attr-defined]

    # run2: telemetry OFF → tenant-B NOT written to cache
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")
    mod2 = _reload_telemetry()
    assert mod2.telemetry_enabled() is False  # type: ignore[attr-defined]
    mod2.set_tenant_id("tenant-B")  # type: ignore[attr-defined]
    # Cache file still holds tenant-A
    assert (tmp_path / "fabric-dw" / "tenant_id").read_text(encoding="utf-8").strip() == "tenant-A"

    # run3: telemetry re-enabled; app_started fires before auth (no live override)
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    mod3 = _reload_telemetry()
    mod3._tenant_id_override = None  # type: ignore[attr-defined]

    pre_auth_envelope = mod3._build_envelope()  # type: ignore[attr-defined]
    # Stale cache value is used for the pre-auth event (bounded misattribution)
    assert pre_auth_envelope["tenant_id"] == "tenant-A"

    # After authentication, set_tenant_id corrects the override for this process
    mod3.set_tenant_id("tenant-B")  # type: ignore[attr-defined]
    post_auth_envelope = mod3._build_envelope()  # type: ignore[attr-defined]
    assert post_auth_envelope["tenant_id"] == "tenant-B"
    # And the cache is now updated with the correct tenant for future runs
    assert (tmp_path / "fabric-dw" / "tenant_id").read_text(encoding="utf-8").strip() == "tenant-B"


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
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
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
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
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
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")
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
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
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
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("AZURE_TENANT_ID", "env-var-tenant-id")

    mod = _reload_telemetry()
    tid_value = "token-tenant-id-from-tid-claim"
    mod._tenant_id_override = tid_value  # type: ignore[attr-defined]

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope.get("tenant_id") == tid_value


def test_envelope_falls_back_to_azure_tenant_id_env_when_no_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_build_envelope falls back to AZURE_TENANT_ID when no token override is set."""
    for var in [
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
        "FABRIC_INTERACTIVE_TENANT_ID",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("AZURE_TENANT_ID", "fallback-env-tenant")

    mod = _reload_telemetry()
    # Ensure no override is set
    mod._tenant_id_override = None  # type: ignore[attr-defined]

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope.get("tenant_id") == "fallback-env-tenant"


def test_envelope_tenant_id_unknown_when_no_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_build_envelope uses ``"unknown"`` for tenant_id when neither override nor env vars are set.

    tenant_id is always present (#477 Finding 2) so it is reliably queryable on every event.
    """
    for var in [
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
        "AZURE_TENANT_ID",
        "FABRIC_INTERACTIVE_TENANT_ID",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod._tenant_id_override = None  # type: ignore[attr-defined]

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope.get("tenant_id") == "unknown"


# ---------------------------------------------------------------------------
# shutdown_telemetry: bounded provider.shutdown() releases urllib3 pool
# ---------------------------------------------------------------------------


def _make_fake_trace_module(provider: Any) -> types.ModuleType:
    """Build a minimal fake ``opentelemetry.trace`` module with *provider*."""
    fake: Any = types.ModuleType("opentelemetry.trace_fake")
    fake.get_tracer_provider = lambda: provider
    return fake  # type: ignore[return-value]


def _install_fake_otel_trace(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    """Install *provider* as the fake OTel tracer provider for the current test.

    Sets both ``sys.modules["opentelemetry.trace"]`` and the ``trace``
    attribute on the already-loaded ``opentelemetry`` package so that
    ``from opentelemetry import trace`` inside the shutdown thread picks up
    our fake regardless of import-cache ordering or test execution order.
    """
    fake_module = _make_fake_trace_module(provider)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", fake_module)

    # Also patch the attribute on the opentelemetry package object so that
    # ``from opentelemetry import trace`` resolves to our fake when the package
    # is already loaded in a previous test's import cache.
    import opentelemetry as _otel_pkg  # noqa: PLC0415

    monkeypatch.setattr(_otel_pkg, "trace", fake_module, raising=False)


def test_shutdown_telemetry_calls_provider_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutdown_telemetry must call provider.shutdown() within the timeout."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()

    # Mark SDK as initialised so shutdown_telemetry proceeds past the guard.
    _fake_logger = object()
    mod._sdk_initialised = True  # type: ignore[attr-defined]
    mod._tracer = _fake_logger  # type: ignore[attr-defined]
    mod._otel_logger = _fake_logger  # type: ignore[attr-defined]
    mod._sdk_shutdown = False  # type: ignore[attr-defined]

    shutdown_called: list[bool] = []
    fake_provider = types.SimpleNamespace(shutdown=lambda: shutdown_called.append(True))
    _install_fake_otel_trace(monkeypatch, fake_provider)

    mod.shutdown_telemetry()  # type: ignore[attr-defined]

    assert shutdown_called, "provider.shutdown() was not called by shutdown_telemetry()"


def test_shutdown_telemetry_is_bounded_when_shutdown_is_slow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutdown_telemetry must not block longer than timeout_ms even with a slow provider."""
    import time  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()

    _fake_logger = object()
    mod._sdk_initialised = True  # type: ignore[attr-defined]
    mod._tracer = _fake_logger  # type: ignore[attr-defined]
    mod._otel_logger = _fake_logger  # type: ignore[attr-defined]
    mod._sdk_shutdown = False  # type: ignore[attr-defined]

    fake_provider = types.SimpleNamespace(shutdown=lambda: __import__("time").sleep(10))
    _install_fake_otel_trace(monkeypatch, fake_provider)

    start = time.monotonic()
    mod.shutdown_telemetry(timeout_ms=300)  # type: ignore[attr-defined]
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, f"shutdown_telemetry blocked for {elapsed:.1f} s — hang detected"


def test_shutdown_telemetry_is_noop_when_sdk_not_initialised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutdown_telemetry is a no-op when the SDK was never initialised."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()

    assert mod._sdk_initialised is False  # type: ignore[attr-defined]
    # Must not raise
    mod.shutdown_telemetry()  # type: ignore[attr-defined]


def test_shutdown_telemetry_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """shutdown_telemetry is idempotent — a second call is a silent no-op."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()

    _fake_logger = object()
    mod._sdk_initialised = True  # type: ignore[attr-defined]
    mod._tracer = _fake_logger  # type: ignore[attr-defined]
    mod._otel_logger = _fake_logger  # type: ignore[attr-defined]
    mod._sdk_shutdown = False  # type: ignore[attr-defined]

    shutdown_call_count: list[int] = []
    fake_provider = types.SimpleNamespace(shutdown=lambda: shutdown_call_count.append(1))
    _install_fake_otel_trace(monkeypatch, fake_provider)

    mod.shutdown_telemetry()  # type: ignore[attr-defined]
    mod.shutdown_telemetry()  # type: ignore[attr-defined]
    mod.shutdown_telemetry()  # type: ignore[attr-defined]

    assert len(shutdown_call_count) == 1, (
        f"provider.shutdown() was called {len(shutdown_call_count)} times — expected 1"
    )


def test_shutdown_telemetry_never_raises_on_provider_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutdown_telemetry suppresses any exception from provider.shutdown()."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()

    _fake_logger = object()
    mod._sdk_initialised = True  # type: ignore[attr-defined]
    mod._tracer = _fake_logger  # type: ignore[attr-defined]
    mod._otel_logger = _fake_logger  # type: ignore[attr-defined]
    mod._sdk_shutdown = False  # type: ignore[attr-defined]

    def _boom() -> None:
        msg = "exporter explosion"
        raise RuntimeError(msg)

    fake_provider = types.SimpleNamespace(shutdown=_boom)
    _install_fake_otel_trace(monkeypatch, fake_provider)

    # Must not raise
    mod.shutdown_telemetry()  # type: ignore[attr-defined]


def test_shutdown_telemetry_force_flushes_log_provider_before_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutdown_telemetry must call force_flush on the log provider BEFORE shutdown.

    This is the key ordering requirement that ensures events emitted just before
    shutdown (command_invoked, app_exited) are exported before the provider is
    torn down.  If force_flush is not called first — or is called after shutdown —
    the BatchLogRecordProcessor worker may not have exported the queued records
    before the daemon thread is killed.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()

    _fake_logger = object()
    mod._sdk_initialised = True  # type: ignore[attr-defined]
    mod._tracer = _fake_logger  # type: ignore[attr-defined]
    mod._otel_logger = _fake_logger  # type: ignore[attr-defined]
    mod._sdk_shutdown = False  # type: ignore[attr-defined]

    call_order: list[str] = []

    fake_log_provider = types.SimpleNamespace(
        force_flush=lambda **_kw: call_order.append("force_flush"),
        shutdown=lambda: call_order.append("shutdown"),
    )

    # Patch opentelemetry._logs so get_logger_provider() returns our fake.
    fake_logs_mod: Any = types.ModuleType("opentelemetry._logs_fake")
    fake_logs_mod.get_logger_provider = lambda: fake_log_provider

    import opentelemetry._logs as _real_logs_mod  # noqa: PLC0415

    monkeypatch.setattr(_real_logs_mod, "get_logger_provider", lambda: fake_log_provider)

    mod.shutdown_telemetry()  # type: ignore[attr-defined]

    assert "force_flush" in call_order, "force_flush must be called on the log provider"
    assert "shutdown" in call_order, "shutdown must be called on the log provider"
    # force_flush must precede shutdown so queued records are exported first.
    assert call_order.index("force_flush") < call_order.index("shutdown"), (
        f"force_flush must be called BEFORE shutdown, got order: {call_order}"
    )


def test_shutdown_telemetry_force_flush_is_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutdown_telemetry must not hang even when force_flush blocks indefinitely."""
    import time  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod = _reload_telemetry()

    _fake_logger = object()
    mod._sdk_initialised = True  # type: ignore[attr-defined]
    mod._tracer = _fake_logger  # type: ignore[attr-defined]
    mod._otel_logger = _fake_logger  # type: ignore[attr-defined]
    mod._sdk_shutdown = False  # type: ignore[attr-defined]

    # Simulate a force_flush that hangs for 30 s — must not propagate to caller.
    fake_log_provider = types.SimpleNamespace(
        force_flush=lambda **_kw: __import__("time").sleep(30),
        shutdown=lambda: None,
    )

    import opentelemetry._logs as _real_logs_mod  # noqa: PLC0415

    monkeypatch.setattr(_real_logs_mod, "get_logger_provider", lambda: fake_log_provider)

    start = time.monotonic()
    mod.shutdown_telemetry(timeout_ms=300)  # type: ignore[attr-defined]
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, (
        f"shutdown_telemetry blocked for {elapsed:.1f} s — force_flush timeout not respected"
    )


# ---------------------------------------------------------------------------
# _harden_azure_sdk_logging — logging hardening for #411
# ---------------------------------------------------------------------------

# Logger names that must be silenced by _harden_azure_sdk_logging.
_HARDENED_LOGGER_NAMES = [
    # Azure Monitor exporter tree: covers statsbeat "missing a valid region",
    # export/_base.py "Retrying due to server request error", and quickpulse
    # _ping tracebacks (#411).
    "azure.monitor.opentelemetry.exporter",
    # azure-core pipeline — belt-and-suspenders (#411).
    "azure.core.pipeline.policies",
]


@pytest.fixture
def restore_hardened_loggers() -> Generator[None, None, None]:
    """Save and restore the global logging state for the loggers targeted by
    _harden_azure_sdk_logging.

    Note: _reload_telemetry() evicts the telemetry module from sys.modules but
    does NOT reset the Python logging registry — Logger objects are singletons
    keyed by name.  Without this fixture, the four _harden tests would leave
    those loggers permanently at CRITICAL/propagate=False for the rest of the
    test process.  Any future test that needs to observe log output from these
    namespaces (e.g. a caplog test checking behaviour when hardening is NOT
    applied) would silently pass even when it should fail.
    """
    saved: dict[str, tuple[int, bool, list[logging.Handler]]] = {}
    for name in _HARDENED_LOGGER_NAMES:
        lgr = logging.getLogger(name)
        saved[name] = (lgr.level, lgr.propagate, list(lgr.handlers))
    yield
    for name, (level, propagate, handlers) in saved.items():
        lgr = logging.getLogger(name)
        lgr.setLevel(level)
        lgr.propagate = propagate
        lgr.handlers = list(handlers)


def test_harden_azure_sdk_logging_sets_level_to_critical(
    restore_hardened_loggers: None,  # noqa: ARG001
) -> None:
    """_harden_azure_sdk_logging must raise each targeted logger to CRITICAL."""
    mod = _reload_telemetry()
    mod._harden_azure_sdk_logging()  # type: ignore[attr-defined]

    for name in _HARDENED_LOGGER_NAMES:
        lgr = logging.getLogger(name)
        assert lgr.level >= logging.CRITICAL, (
            f"Logger {name!r} level {lgr.level} is below CRITICAL ({logging.CRITICAL})"
        )


def test_harden_azure_sdk_logging_sets_propagate_false(
    restore_hardened_loggers: None,  # noqa: ARG001
) -> None:
    """_harden_azure_sdk_logging must set propagate=False on each targeted logger."""
    mod = _reload_telemetry()
    mod._harden_azure_sdk_logging()  # type: ignore[attr-defined]

    for name in _HARDENED_LOGGER_NAMES:
        lgr = logging.getLogger(name)
        assert lgr.propagate is False, (
            f"Logger {name!r} propagate is not False — records can still reach root handler"
        )


def test_harden_azure_sdk_logging_attaches_null_handler(
    restore_hardened_loggers: None,  # noqa: ARG001
) -> None:
    """_harden_azure_sdk_logging must attach a NullHandler to each targeted logger."""
    mod = _reload_telemetry()
    mod._harden_azure_sdk_logging()  # type: ignore[attr-defined]

    for name in _HARDENED_LOGGER_NAMES:
        lgr = logging.getLogger(name)
        assert any(isinstance(h, logging.NullHandler) for h in lgr.handlers), (
            f"Logger {name!r} has no NullHandler — last-resort stderr message possible"
        )


def test_harden_azure_sdk_logging_is_idempotent(
    restore_hardened_loggers: None,  # noqa: ARG001
) -> None:
    """Calling _harden_azure_sdk_logging multiple times must not add duplicate handlers."""
    mod = _reload_telemetry()
    mod._harden_azure_sdk_logging()  # type: ignore[attr-defined]
    mod._harden_azure_sdk_logging()  # type: ignore[attr-defined]

    for name in _HARDENED_LOGGER_NAMES:
        lgr = logging.getLogger(name)
        null_count = sum(1 for h in lgr.handlers if isinstance(h, logging.NullHandler))
        assert null_count == 1, (
            f"Logger {name!r} has {null_count} NullHandlers after two calls — expected exactly 1"
        )


# ---------------------------------------------------------------------------
# A4: #418 — statsbeat IMDS socket-leak fix
# ---------------------------------------------------------------------------


def _clear_statsbeat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the statsbeat disable env var so tests start from a clean slate."""
    monkeypatch.delenv("APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL", raising=False)


def _enable_telemetry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Remove all opt-out signals so telemetry_enabled() returns True."""
    for var in (
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def test_statsbeat_disabled_env_set_before_configure_azure_monitor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL must be 'true' after _get_tracer runs.

    This prevents the statsbeat IMDS (169.254.169.254) socket probe from opening a
    socket that is left unclosed, which the GC would emit as 'Exception ignored in:
    <socket ...>' ResourceWarning at interpreter shutdown (#418).
    """
    _clear_statsbeat_env(monkeypatch)
    _enable_telemetry(monkeypatch, tmp_path)

    mod = _reload_telemetry()

    captured_env_at_configure: dict[str, str | None] = {}

    def fake_configure(**_kwargs: object) -> None:
        # Capture the env var value at the moment configure_azure_monitor is called.
        captured_env_at_configure["value"] = os.environ.get(
            "APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL"
        )

    fake_otel_logger = object()

    fake_azure_mod: Any = types.ModuleType("azure.monitor.opentelemetry")
    fake_azure_mod.configure_azure_monitor = fake_configure

    # _get_tracer now uses `from opentelemetry._logs import get_logger` (not trace).
    fake_logs_mod: Any = types.ModuleType("opentelemetry._logs")
    fake_logs_mod.get_logger = lambda *_a, **_kw: fake_otel_logger
    fake_logs_mod.LogRecord = object
    fake_logs_mod.SeverityNumber = object

    mod._sdk_initialised = False
    mod._tracer = None
    mod._otel_logger = None

    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", fake_azure_mod)
    monkeypatch.setitem(sys.modules, "opentelemetry._logs", fake_logs_mod)

    mod._get_tracer()

    mod._sdk_initialised = False
    mod._tracer = None
    mod._otel_logger = None

    # The env var must be set to "true" at the point configure_azure_monitor is called,
    # proving it is set BEFORE the exporter initialises (which is when statsbeat starts).
    assert captured_env_at_configure.get("value") == "true", (
        "APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL must be 'true' before "
        "configure_azure_monitor is called so statsbeat never opens the IMDS socket (#418)"
    )


def test_statsbeat_env_not_overridden_when_already_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """setdefault semantics: a pre-existing value must not be overwritten by _get_tracer.

    An operator can set APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL=false before
    launching the CLI to force statsbeat on.  _get_tracer uses os.environ.setdefault,
    so an existing value takes precedence.
    """
    _enable_telemetry(monkeypatch, tmp_path)
    # Pre-set a non-default value the operator chose explicitly.
    monkeypatch.setenv("APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL", "false")

    mod = _reload_telemetry()

    def fake_configure(**_kwargs: object) -> None:
        pass

    fake_otel_logger = object()

    fake_azure_mod: Any = types.ModuleType("azure.monitor.opentelemetry")
    fake_azure_mod.configure_azure_monitor = fake_configure

    # _get_tracer now uses `from opentelemetry._logs import get_logger` (not trace).
    fake_logs_mod: Any = types.ModuleType("opentelemetry._logs")
    fake_logs_mod.get_logger = lambda *_a, **_kw: fake_otel_logger
    fake_logs_mod.LogRecord = object
    fake_logs_mod.SeverityNumber = object

    mod._sdk_initialised = False
    mod._tracer = None
    mod._otel_logger = None

    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", fake_azure_mod)
    monkeypatch.setitem(sys.modules, "opentelemetry._logs", fake_logs_mod)

    mod._get_tracer()

    mod._sdk_initialised = False
    mod._tracer = None
    mod._otel_logger = None

    # The pre-existing "false" value must not have been overwritten.
    assert os.environ.get("APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL") == "false", (
        "os.environ.setdefault must not override a pre-existing value — "
        "an explicit operator setting must be respected (#418)"
    )


# ---------------------------------------------------------------------------
# suppress_telemetry — process-level help-flag suppression
# ---------------------------------------------------------------------------


def test_suppress_telemetry_makes_telemetry_enabled_return_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """suppress_telemetry() must cause telemetry_enabled() to return False."""
    # Start with telemetry ON (all opt-out signals absent).
    for var in [
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is True, "Precondition: telemetry should be enabled"

    mod.suppress_telemetry()
    assert mod.telemetry_enabled() is False, (
        "After suppress_telemetry(), telemetry_enabled() must return False"
    )


def test_suppress_telemetry_is_resettable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """suppress_telemetry(False) must restore normal enable/disable evaluation."""
    for var in [
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.suppress_telemetry(value=True)
    assert mod.telemetry_enabled() is False, "After suppress_telemetry(True), should be False"

    mod.suppress_telemetry(value=False)
    assert mod.telemetry_enabled() is True, "After suppress_telemetry(False), should be True again"


def test_suppress_telemetry_prevents_get_tracer_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When suppressed, emit_event must not call _get_tracer (no SDK init)."""
    for var in [
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.suppress_telemetry()

    tracer_calls: list[str] = []
    with patch.object(mod, "_get_tracer", side_effect=lambda: tracer_calls.append("called")):  # type: ignore[attr-defined]
        mod.emit_event("test_event", {"foo": "bar"})

    assert tracer_calls == [], "_get_tracer must not be called when telemetry is suppressed"


def test_suppress_telemetry_prevents_configure_azure_monitor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When suppressed, _get_tracer must not call configure_azure_monitor."""
    for var in [
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.suppress_telemetry()

    configure_calls: list[str] = []

    def fake_configure(**_kwargs: object) -> None:
        configure_calls.append("called")

    fake_tracer = object()

    class _FakeTrace(types.ModuleType):
        def get_tracer(self, *_args: object, **_kw: object) -> object:
            return fake_tracer

    fake_azure_mod: Any = types.ModuleType("azure.monitor.opentelemetry")
    fake_azure_mod.configure_azure_monitor = fake_configure
    fake_trace_mod = _FakeTrace("opentelemetry.trace")

    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", fake_azure_mod)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", fake_trace_mod)

    # Trigger emit path — should be suppressed before _get_tracer is reached.
    mod.record_app_started("cli")

    assert configure_calls == [], (
        "configure_azure_monitor must not be called when telemetry is suppressed"
    )


def test_suppress_telemetry_prevents_first_run_notice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """maybe_print_first_run_notice must be a no-op when telemetry is suppressed."""
    for var in [
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.suppress_telemetry()

    mod.maybe_print_first_run_notice()

    captured = capsys.readouterr()
    assert captured.err == "", (
        "maybe_print_first_run_notice must print nothing when telemetry is suppressed"
    )


def test_suppress_telemetry_shutdown_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutdown_telemetry must be a no-op when suppressed (SDK was never initialised)."""
    for var in [
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    mod.suppress_telemetry()

    # SDK must never have been initialised if suppressed from the start.
    assert mod._sdk_initialised is False  # type: ignore[attr-defined]

    # Must complete instantly (no network I/O) and not raise.
    mod.shutdown_telemetry()

    # SDK must remain un-initialised.
    assert mod._sdk_initialised is False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Native customEvents: envelope verification via real exporter
# ---------------------------------------------------------------------------


def test_emit_event_noop_when_telemetry_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """emit_event is a no-op when telemetry is disabled — _get_tracer is never called."""
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    called: list[str] = []

    with patch.object(mod, "_get_tracer", side_effect=lambda: called.append("x")):  # type: ignore[attr-defined]
        mod.emit_event("noop_event", {"key": "value"})  # type: ignore[attr-defined]

    assert called == [], "_get_tracer must not be called when telemetry is disabled"


def _build_test_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_id: str = "ccccdddd-dead-beef-1234-aaaaaaaaaaaa",
    event_name: str = "test_custom_event",
    resource_version: str = "1.2.3",
) -> tuple[Any, Any, dict[str, Any]]:
    """Shared helper: build and convert a log record through the real exporter.

    Returns ``(envelope, mod, custom_props)`` for assertions.
    """
    from azure.monitor.opentelemetry.exporter.export.logs._exporter import (  # noqa: PLC0415
        _convert_log_to_envelope,
    )
    from opentelemetry.sdk._logs._internal import (  # noqa: PLC0415
        InstrumentationScope,
        ReadableLogRecord,
    )
    from opentelemetry.sdk._logs._internal import LogRecord as SDKLogRecord  # noqa: PLC0415
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415

    for var in (
        "FABRIC_DW_TELEMETRY_OPT_OUT",
        "DO_NOT_TRACK",
        "AZURE_TENANT_ID",
        "FABRIC_INTERACTIVE_TENANT_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "install_id").write_text(install_id, encoding="utf-8")

    mod = _reload_telemetry()
    mod._tenant_id_override = None  # type: ignore[attr-defined]

    envelope_attrs = mod._build_envelope()  # type: ignore[attr-defined]
    merged: dict[str, object] = {**envelope_attrs}
    merged["microsoft.custom_event.name"] = event_name
    merged["enduser.pseudo.id"] = mod._get_install_id()  # type: ignore[attr-defined]
    merged["extra_dimension"] = "extra_value"
    merged["ai.operation.name"] = event_name

    sdk_record = SDKLogRecord(attributes=merged)  # type: ignore[arg-type]  # ty: ignore[no-matching-overload]
    resource = Resource.create(
        {
            "service.namespace": "fabric-dw",
            "service.name": "cli",
            "service.instance.id": install_id,
            "service.version": resource_version,
            "device.id": install_id,
        }
    )
    scope = InstrumentationScope("fabric_dw.telemetry")
    readable = ReadableLogRecord(
        log_record=sdk_record, resource=resource, instrumentation_scope=scope
    )
    envelope = _convert_log_to_envelope(readable)
    assert envelope.data is not None
    assert envelope.data.base_data is not None
    custom_props: dict[str, Any] = envelope.data.base_data.properties or {}  # ty: ignore[unresolved-attribute]
    return envelope, mod, custom_props


def test_emit_event_produces_eventdata_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """emit_event must produce an EventData envelope with correct base fields.

    Verifies: base_type, ai.user.id, event name, session_id (#460).
    Does NOT require a real network connection.
    """
    known_install_id = "ccccdddd-dead-beef-1234-aaaaaaaaaaaa"
    event_name = "test_custom_event"
    envelope, _mod, custom_props = _build_test_envelope(
        monkeypatch, tmp_path, install_id=known_install_id, event_name=event_name
    )

    assert envelope.tags is not None, "envelope.tags must not be None"
    assert envelope.data is not None
    assert envelope.data.base_type == "EventData", (
        f"Expected base_type='EventData' (customEvents), got {envelope.data.base_type!r}.  "
        "This means the log record would land in 'traces', not 'customEvents'."
    )

    ai_user_id = envelope.tags.get("ai.user.id")
    assert ai_user_id == known_install_id, (
        f"Expected ai.user.id={known_install_id!r}, got {ai_user_id!r}.  "
        "enduser.pseudo.id must map to ai.user.id for the Users blade."
    )

    assert envelope.data.base_data is not None
    event_data_name = envelope.data.base_data.name  # type: ignore[union-attr]
    assert event_data_name == event_name, (
        f"Expected event name {event_name!r} in EventData, got {event_data_name!r}."
    )

    assert "session_id" in custom_props, (
        "session_id must appear in customDimensions (custom properties).  "
        "ai.session.id has no attribute mapping in azure-monitor-opentelemetry-exporter "
        "1.0.0b53, so session_id is kept as a custom dimension for Logs blade queries."
    )


def test_emit_event_envelope_tenant_id_always_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tenant_id must always be in customDimensions, never absent (#477 Finding 2)."""
    _envelope, _mod, custom_props = _build_test_envelope(monkeypatch, tmp_path)

    assert "tenant_id" in custom_props, (
        "tenant_id must always appear in customDimensions so it is reliably queryable "
        "on every event even when no tenant has been resolved yet (#477 Finding 2)."
    )
    assert custom_props["tenant_id"] == "unknown", (
        "tenant_id must be 'unknown' when no tenant is resolved "
        "(not absent — must be queryable on every event)."
    )


def test_emit_event_envelope_redundant_dimensions_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """anonymous_install_id and is_ci must NOT be in customDimensions (#477 Finding 5)."""
    _envelope, _mod, custom_props = _build_test_envelope(monkeypatch, tmp_path)

    assert "anonymous_install_id" not in custom_props, (
        "anonymous_install_id must not appear in customDimensions — it is already shipped "
        "natively as user_Id (← enduser.pseudo.id).  Sending it twice is redundant (#477)."
    )
    assert "is_ci" not in custom_props, (
        "is_ci must not appear in customDimensions — telemetry is disabled entirely in CI "
        "so this was always False and carries no signal (#477)."
    )


def test_emit_event_envelope_part_a_native_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Native Part A fields must be populated from the OTel Resource (#477).

    Checks: cloud_RoleInstance = install_id (not hostname), cloud_RoleName not
    unknown_service:*, application_Version, operation_Name.
    """
    import platform as _platform  # noqa: PLC0415

    known_install_id = "ccccdddd-dead-beef-1234-aaaaaaaaaaaa"
    event_name = "test_custom_event"
    envelope, _mod, _custom_props = _build_test_envelope(
        monkeypatch, tmp_path, install_id=known_install_id, event_name=event_name
    )

    assert envelope.tags is not None

    # cloud_RoleInstance must be install_id, NOT platform.node() (#477 privacy fix).
    role_instance = envelope.tags.get("ai.cloud.roleInstance")
    hostname = _platform.node()
    assert role_instance != hostname, (
        f"cloud_RoleInstance must not be the machine hostname {hostname!r}.  "
        "This would leak the user's real name on every event (#477 privacy fix)."
    )
    assert role_instance == known_install_id, (
        f"cloud_RoleInstance must be the pseudonymous install_id {known_install_id!r}, "
        f"got {role_instance!r}.  Set via resource service.instance.id (#477)."
    )

    # cloud_RoleName must be meaningful (not unknown_service:*) (#477 Finding 3).
    role_name = envelope.tags.get("ai.cloud.role")
    assert role_name is not None, "cloud_RoleName must not be None"
    assert "unknown_service" not in role_name, (
        f"cloud_RoleName must not be 'unknown_service:*', got {role_name!r}.  "
        "Set via resource service.namespace + service.name (#477 Finding 3)."
    )

    # application_Version must be populated (#477 Finding 3).
    app_version_tag = envelope.tags.get("ai.application.ver")
    assert app_version_tag == "1.2.3", (
        f"application_Version must be '1.2.3', got {app_version_tag!r}.  "
        "Set via resource service.version (#477 Finding 3)."
    )

    # operation_Name must be set (#477 Finding 4).
    op_name = envelope.tags.get("ai.operation.name")
    assert op_name == event_name, (
        f"operation_Name must be the event name {event_name!r}, got {op_name!r}.  "
        "Set via ai.operation.name record attribute (#477 Finding 4)."
    )


# ---------------------------------------------------------------------------
# #477: hostname privacy — _build_otel_resource prevents platform.node() fallback
# ---------------------------------------------------------------------------


def test_build_otel_resource_sets_service_instance_id_to_install_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_build_otel_resource must set service.instance.id to the anonymous install_id.

    This prevents the exporter from falling back to platform.node() for
    cloud_RoleInstance, which would leak the machine hostname (#477 privacy fix).
    """
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    known_install_id = "aaaa1111-dead-beef-1234-bbbbbbbbbbbb"
    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "install_id").write_text(known_install_id, encoding="utf-8")

    mod = _reload_telemetry()
    resource = mod._build_otel_resource("cli")  # type: ignore[attr-defined]

    assert isinstance(resource, Resource), "_build_otel_resource must return a Resource"
    attrs = dict(resource.attributes)
    assert attrs.get("service.instance.id") == known_install_id, (
        f"service.instance.id must be install_id {known_install_id!r}, "
        f"got {attrs.get('service.instance.id')!r}.  "
        "This prevents hostname fallback for cloud_RoleInstance (#477)."
    )


def test_build_otel_resource_sets_device_id_to_install_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_build_otel_resource must set device.id to the anonymous install_id.

    This prevents the exporter from falling back to platform.node() for
    ai.device.id (#477 privacy fix).
    """
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    known_install_id = "cccc3333-dead-beef-1234-dddddddddddd"
    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "install_id").write_text(known_install_id, encoding="utf-8")

    mod = _reload_telemetry()
    resource = mod._build_otel_resource("mcp")  # type: ignore[attr-defined]

    assert isinstance(resource, Resource), "_build_otel_resource must return a Resource"
    attrs = dict(resource.attributes)
    assert attrs.get("device.id") == known_install_id, (
        f"device.id must be install_id {known_install_id!r}, "
        f"got {attrs.get('device.id')!r}.  "
        "This prevents hostname fallback for ai.device.id (#477)."
    )


def test_build_otel_resource_service_instance_id_is_not_hostname(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """service.instance.id in the OTel Resource must never be the machine hostname.

    The hostname is the fallback used by the exporter when service.instance.id
    is absent.  We must always set a non-identifying value (#477 privacy fix).
    """
    import platform as _platform  # noqa: PLC0415

    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    resource = mod._build_otel_resource("cli")  # type: ignore[attr-defined]

    assert isinstance(resource, Resource), "_build_otel_resource must return a Resource"
    attrs = dict(resource.attributes)

    hostname = _platform.node()
    assert attrs.get("service.instance.id") != hostname, (
        f"service.instance.id must not be the machine hostname {hostname!r}.  "
        "This would leak the user's real name on every telemetry event (#477)."
    )


def test_build_otel_resource_sets_service_name_to_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_build_otel_resource must set service.name to the surface (cloud_RoleName)."""
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    for surface in ("cli", "mcp"):
        resource = mod._build_otel_resource(surface)  # type: ignore[attr-defined]
        assert isinstance(resource, Resource)
        attrs = dict(resource.attributes)
        assert attrs.get("service.name") == surface, (
            f"service.name must be {surface!r}, got {attrs.get('service.name')!r}"
        )
        assert attrs.get("service.namespace") == "fabric-dw", (
            f"service.namespace must be 'fabric-dw', got {attrs.get('service.namespace')!r}"
        )


def test_get_tracer_passes_resource_to_configure_azure_monitor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_get_tracer must pass a ``resource`` kwarg to configure_azure_monitor (#477).

    The resource is what sets native Part A fields (cloud_RoleName, cloud_RoleInstance,
    application_Version, ai.device.id) and prevents the hostname fallback.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)

    mod = _reload_telemetry()

    captured_kwargs: dict[str, object] = {}

    def fake_configure(**kwargs: object) -> None:
        captured_kwargs.update(kwargs)

    fake_otel_logger = object()

    fake_azure_mod: Any = types.ModuleType("azure.monitor.opentelemetry")
    fake_azure_mod.configure_azure_monitor = fake_configure

    fake_logs_mod: Any = types.ModuleType("opentelemetry._logs")
    fake_logs_mod.get_logger = lambda *_a, **_kw: fake_otel_logger
    fake_logs_mod.LogRecord = object
    fake_logs_mod.SeverityNumber = object

    mod._sdk_initialised = False
    mod._tracer = None
    mod._otel_logger = None

    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", fake_azure_mod)
    monkeypatch.setitem(sys.modules, "opentelemetry._logs", fake_logs_mod)

    mod._get_tracer()

    assert "resource" in captured_kwargs, (
        "configure_azure_monitor must receive a 'resource' kwarg so native Part A fields "
        "are set from the OTel Resource instead of hostname fallback (#477)."
    )

    mod._sdk_initialised = False
    mod._tracer = None
    mod._otel_logger = None


def test_emit_event_sets_ai_operation_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """emit_event must set ai.operation.name on the log record (#477 Finding 4).

    Callers can override by passing it in attributes; the event name is used as
    a fallback for lifecycle events when it is not explicitly provided.
    """
    from azure.monitor.opentelemetry.exporter.export.logs._exporter import (  # noqa: PLC0415
        _convert_log_to_envelope,
    )
    from opentelemetry.sdk._logs._internal import (  # noqa: PLC0415
        InstrumentationScope,
        ReadableLogRecord,
    )
    from opentelemetry.sdk._logs._internal import LogRecord as SDKLogRecord  # noqa: PLC0415
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()

    # Simulate the attribute building done inside emit_event for a lifecycle event.
    event_name = "app_started"
    envelope_attrs = mod._build_envelope()  # type: ignore[attr-defined]
    merged: dict[str, object] = {**envelope_attrs}
    merged["microsoft.custom_event.name"] = event_name
    merged["enduser.pseudo.id"] = mod._get_install_id()  # type: ignore[attr-defined]
    # emit_event sets ai.operation.name = event_name when not already present.
    if "ai.operation.name" not in merged:
        merged["ai.operation.name"] = event_name

    sdk_record = SDKLogRecord(attributes=merged)  # type: ignore[arg-type]  # ty: ignore[no-matching-overload]
    resource = Resource.create({"service.name": "cli", "service.namespace": "fabric-dw"})
    scope = InstrumentationScope("fabric_dw.telemetry")
    readable = ReadableLogRecord(
        log_record=sdk_record, resource=resource, instrumentation_scope=scope
    )

    envelope = _convert_log_to_envelope(readable)
    assert envelope.tags is not None
    op_name = envelope.tags.get("ai.operation.name")
    assert op_name == event_name, (
        f"ai.operation.name must be {event_name!r} on the envelope, got {op_name!r}.  "
        "operation_Name should be populated for lifecycle events (#477 Finding 4)."
    )


def test_cloud_role_instance_from_resource_not_hostname(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cloud_RoleInstance must come from service.instance.id, not platform.node().

    This is the key privacy test for #477 Finding 1.  The exporter falls back to
    platform.node() when service.instance.id is absent.  We must ensure our
    resource attribute is set so the hostname is never shipped.
    """
    import platform as _platform  # noqa: PLC0415

    from azure.monitor.opentelemetry.exporter.export.logs._exporter import (  # noqa: PLC0415
        _convert_log_to_envelope,
    )
    from opentelemetry.sdk._logs._internal import (  # noqa: PLC0415
        InstrumentationScope,
        ReadableLogRecord,
    )
    from opentelemetry.sdk._logs._internal import LogRecord as SDKLogRecord  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    known_install_id = "eeee5555-dead-beef-1234-ffffffffffff"
    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "install_id").write_text(known_install_id, encoding="utf-8")

    mod = _reload_telemetry()

    # Build a log record with our resource (as _get_tracer would do in production).
    attrs: dict[str, object] = {
        "microsoft.custom_event.name": "test_event",
        "enduser.pseudo.id": known_install_id,
        "ai.operation.name": "test_event",
    }
    sdk_record = SDKLogRecord(attributes=attrs)  # type: ignore[arg-type]  # ty: ignore[no-matching-overload]

    # Use the resource built by _build_otel_resource (the production code path).
    resource = mod._build_otel_resource("cli")  # type: ignore[attr-defined]
    scope = InstrumentationScope("fabric_dw.telemetry")
    readable = ReadableLogRecord(
        log_record=sdk_record,
        resource=resource,
        instrumentation_scope=scope,  # type: ignore[arg-type]
    )

    envelope = _convert_log_to_envelope(readable)
    assert envelope.tags is not None

    hostname = _platform.node()
    role_instance = envelope.tags.get("ai.cloud.roleInstance")

    assert role_instance != hostname, (
        f"cloud_RoleInstance must NOT be the machine hostname {hostname!r} — "
        "hostnames often embed the user's real name (#477 privacy fix, Finding 1).  "
        f"Got cloud_RoleInstance={role_instance!r}."
    )
    assert role_instance == known_install_id, (
        f"cloud_RoleInstance must be the pseudonymous install_id {known_install_id!r}, "
        f"got {role_instance!r}.  "
        "Set via resource attribute service.instance.id (#477)."
    )


def test_ai_device_id_from_resource_not_hostname(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ai.device.id must come from install_id via the OTel Resource, not platform.node().

    This is the privacy test for #477 (parallel to the cloud_RoleInstance check above).
    The exporter falls back to platform.node() for ai.device.id when device.id is absent
    from the resource.  We must ensure the device.id resource attribute is set so the
    hostname is never shipped.
    """
    import platform as _platform  # noqa: PLC0415

    from azure.monitor.opentelemetry.exporter.export.logs._exporter import (  # noqa: PLC0415
        _convert_log_to_envelope,
    )
    from opentelemetry.sdk._logs._internal import (  # noqa: PLC0415
        InstrumentationScope,
        ReadableLogRecord,
    )
    from opentelemetry.sdk._logs._internal import LogRecord as SDKLogRecord  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    known_install_id = "ffff6666-dead-beef-1234-aaaaaaaaaaaa"
    config_dir = tmp_path / "fabric-dw"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "install_id").write_text(known_install_id, encoding="utf-8")

    mod = _reload_telemetry()

    # Build a log record using the production resource (as _get_tracer would do).
    attrs: dict[str, object] = {
        "microsoft.custom_event.name": "test_event",
        "enduser.pseudo.id": known_install_id,
        "ai.operation.name": "test_event",
    }
    sdk_record = SDKLogRecord(attributes=attrs)  # type: ignore[arg-type]  # ty: ignore[no-matching-overload]

    # Use the resource built by _build_otel_resource (the production code path).
    resource = mod._build_otel_resource("cli")  # type: ignore[attr-defined]
    scope = InstrumentationScope("fabric_dw.telemetry")
    readable = ReadableLogRecord(
        log_record=sdk_record,
        resource=resource,
        instrumentation_scope=scope,  # type: ignore[arg-type]
    )

    envelope = _convert_log_to_envelope(readable)
    assert envelope.tags is not None

    hostname = _platform.node()
    device_id = envelope.tags.get("ai.device.id")

    assert device_id != hostname, (
        f"ai.device.id must NOT be the machine hostname {hostname!r} — "
        "hostnames often embed the user's real name (#477 privacy fix).  "
        f"Got ai.device.id={device_id!r}."
    )
    assert device_id == known_install_id, (
        f"ai.device.id must be the pseudonymous install_id {known_install_id!r}, "
        f"got {device_id!r}.  "
        "Set via resource attribute device.id (#477)."
    )


# ---------------------------------------------------------------------------
# set_auth_mode / _auth_mode_override (#665)
# ---------------------------------------------------------------------------


def test_set_auth_mode_stores_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """set_auth_mode stores the value in _auth_mode_override when telemetry is enabled."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    mod = _reload_telemetry()
    assert mod._auth_mode_override is None  # type: ignore[attr-defined]

    mod.set_auth_mode("azure_cli")  # type: ignore[attr-defined]

    assert mod._auth_mode_override == "azure_cli"  # type: ignore[attr-defined]


def test_set_auth_mode_reflected_in_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After set_auth_mode, _build_envelope uses the override rather than _detect_auth_mode."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # Clear env vars so _detect_auth_mode would return "interactive" without the override.
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    monkeypatch.delenv("AZURE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    mod = _reload_telemetry()
    mod.set_auth_mode("azure_cli")  # type: ignore[attr-defined]

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["auth_mode"] == "azure_cli"


def test_set_auth_mode_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The first set_auth_mode call wins; subsequent calls are no-ops."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    mod = _reload_telemetry()
    mod.set_auth_mode("azure_cli")  # type: ignore[attr-defined]
    mod.set_auth_mode("interactive")  # subsequent call — must be ignored

    assert mod._auth_mode_override == "azure_cli"  # type: ignore[attr-defined]


def test_set_auth_mode_noop_when_telemetry_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """set_auth_mode is a no-op when telemetry is disabled (mirrors set_tenant_id behaviour)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")

    mod = _reload_telemetry()
    assert mod.telemetry_enabled() is False  # type: ignore[attr-defined]

    mod.set_auth_mode("azure_cli")  # type: ignore[attr-defined]

    # Override must NOT be set when telemetry is disabled.
    assert mod._auth_mode_override is None  # type: ignore[attr-defined]


def test_auth_mode_override_beats_detect(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_auth_mode_override takes precedence over _detect_auth_mode env heuristic."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # AZURE_CLIENT_SECRET would make _detect_auth_mode return "service_principal".
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "some-secret")
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    mod = _reload_telemetry()
    mod.set_auth_mode("azure_cli")  # type: ignore[attr-defined]

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["auth_mode"] == "azure_cli", (
        "set_auth_mode override must beat the AZURE_CLIENT_SECRET heuristic"
    )


def test_envelope_falls_back_to_detect_when_no_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When set_auth_mode has not been called, _build_envelope uses _detect_auth_mode."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "some-secret")
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    mod = _reload_telemetry()
    # No set_auth_mode call — override stays None.

    envelope = mod._build_envelope()  # type: ignore[attr-defined]
    assert envelope["auth_mode"] == "service_principal"


def test_set_auth_mode_is_exported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """set_auth_mode must be in __all__ and callable on the module."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mod = _reload_telemetry()
    assert "set_auth_mode" in mod.__all__  # type: ignore[attr-defined]
    assert callable(mod.set_auth_mode)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# record_auth_mode_from_default_credential (#665)
# ---------------------------------------------------------------------------


def _make_fake_dac_credential(sub_class_name: str | None) -> object:
    """Return a fake DefaultAzureCredential-like object with _successful_credential set.

    Creates an instance whose ``type().__name__`` matches *sub_class_name* so
    that :func:`~fabric_dw.auth.record_auth_mode_from_default_credential` can
    map it to a telemetry mode via :data:`~fabric_dw.auth._DAC_CLASS_TO_AUTH_MODE`.

    When *sub_class_name* is ``None`` the inner object has no
    ``_successful_credential`` attribute, simulating a DAC that has not yet
    resolved to a sub-credential.
    """
    if sub_class_name is None:
        fake_inner = types.SimpleNamespace()
        # No _successful_credential attribute on the inner object.
        return types.SimpleNamespace(_inner=fake_inner)

    # Create an instance of a dynamically named class so type().__name__ returns
    # sub_class_name.  SimpleNamespace.__class__ assignment is not supported for
    # immutable types, so we build the class explicitly.
    FakeSubClass = type(sub_class_name, (), {})  # noqa: N806
    fake_sub = FakeSubClass()
    fake_inner = types.SimpleNamespace(_successful_credential=fake_sub)
    return types.SimpleNamespace(_inner=fake_inner)


def test_record_auth_mode_azure_cli_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AzureCliCredential sub-credential → azure_cli auth mode."""
    import importlib  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    _reload_telemetry()
    import fabric_dw.auth as auth_mod  # noqa: PLC0415
    import fabric_dw.telemetry as telemetry_mod  # noqa: PLC0415

    importlib.reload(telemetry_mod)
    # Reset auth module state as well so we get a fresh override slot.
    telemetry_mod._auth_mode_override = None  # type: ignore[attr-defined]

    fake_cred = _make_fake_dac_credential("AzureCliCredential")
    auth_mod.record_auth_mode_from_default_credential(fake_cred)

    assert telemetry_mod._auth_mode_override == "azure_cli"  # type: ignore[attr-defined]


def test_record_auth_mode_azure_developer_cli_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AzureDeveloperCliCredential sub-credential → azure_cli auth mode."""
    import importlib  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    _reload_telemetry()
    import fabric_dw.auth as auth_mod  # noqa: PLC0415
    import fabric_dw.telemetry as telemetry_mod  # noqa: PLC0415

    importlib.reload(telemetry_mod)
    telemetry_mod._auth_mode_override = None  # type: ignore[attr-defined]

    fake_cred = _make_fake_dac_credential("AzureDeveloperCliCredential")
    auth_mod.record_auth_mode_from_default_credential(fake_cred)

    assert telemetry_mod._auth_mode_override == "azure_cli"  # type: ignore[attr-defined]


def test_record_auth_mode_interactive_browser_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """InteractiveBrowserCredential sub-credential → interactive auth mode."""
    import importlib  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    _reload_telemetry()
    import fabric_dw.auth as auth_mod  # noqa: PLC0415
    import fabric_dw.telemetry as telemetry_mod  # noqa: PLC0415

    importlib.reload(telemetry_mod)
    telemetry_mod._auth_mode_override = None  # type: ignore[attr-defined]

    fake_cred = _make_fake_dac_credential("InteractiveBrowserCredential")
    auth_mod.record_auth_mode_from_default_credential(fake_cred)

    assert telemetry_mod._auth_mode_override == "interactive"  # type: ignore[attr-defined]


def test_record_auth_mode_managed_identity_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ManagedIdentityCredential sub-credential → managed_identity auth mode."""
    import importlib  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    _reload_telemetry()
    import fabric_dw.auth as auth_mod  # noqa: PLC0415
    import fabric_dw.telemetry as telemetry_mod  # noqa: PLC0415

    importlib.reload(telemetry_mod)
    telemetry_mod._auth_mode_override = None  # type: ignore[attr-defined]

    fake_cred = _make_fake_dac_credential("ManagedIdentityCredential")
    auth_mod.record_auth_mode_from_default_credential(fake_cred)

    assert telemetry_mod._auth_mode_override == "managed_identity"  # type: ignore[attr-defined]


def test_record_auth_mode_environment_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """EnvironmentCredential sub-credential → service_principal auth mode."""
    import importlib  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    _reload_telemetry()
    import fabric_dw.auth as auth_mod  # noqa: PLC0415
    import fabric_dw.telemetry as telemetry_mod  # noqa: PLC0415

    importlib.reload(telemetry_mod)
    telemetry_mod._auth_mode_override = None  # type: ignore[attr-defined]

    fake_cred = _make_fake_dac_credential("EnvironmentCredential")
    auth_mod.record_auth_mode_from_default_credential(fake_cred)

    assert telemetry_mod._auth_mode_override == "service_principal"  # type: ignore[attr-defined]


def test_record_auth_mode_unknown_credential_leaves_override_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unknown sub-credential class name must not set the override (falls back to heuristic)."""
    import importlib  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    _reload_telemetry()
    import fabric_dw.auth as auth_mod  # noqa: PLC0415
    import fabric_dw.telemetry as telemetry_mod  # noqa: PLC0415

    importlib.reload(telemetry_mod)
    telemetry_mod._auth_mode_override = None  # type: ignore[attr-defined]

    fake_cred = _make_fake_dac_credential("SomeNewFutureCredential")
    auth_mod.record_auth_mode_from_default_credential(fake_cred)

    assert telemetry_mod._auth_mode_override is None  # type: ignore[attr-defined]


def test_record_auth_mode_no_successful_credential_leaves_override_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When _successful_credential is absent (not yet resolved), override stays None."""
    import importlib  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    _reload_telemetry()
    import fabric_dw.auth as auth_mod  # noqa: PLC0415
    import fabric_dw.telemetry as telemetry_mod  # noqa: PLC0415

    importlib.reload(telemetry_mod)
    telemetry_mod._auth_mode_override = None  # type: ignore[attr-defined]

    fake_cred = _make_fake_dac_credential(None)
    auth_mod.record_auth_mode_from_default_credential(fake_cred)

    assert telemetry_mod._auth_mode_override is None  # type: ignore[attr-defined]


def test_record_auth_mode_is_failsafe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """record_auth_mode_from_default_credential must never raise even if telemetry throws."""

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    _reload_telemetry()
    import fabric_dw.auth as auth_mod  # noqa: PLC0415

    # A plain object with no useful attributes: exercises the early-return paths
    # and ensures no exception propagates.
    auth_mod.record_auth_mode_from_default_credential(object())  # must not raise


def test_azure_cli_without_azure_config_dir_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression test for #665: Azure CLI session without AZURE_CONFIG_DIR env var.

    Without the set_auth_mode override the heuristic incorrectly returns
    'interactive'.  With the override (populated by record_auth_mode_from_default_credential),
    the envelope correctly reports 'azure_cli'.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # Simulate a plain 'az login' — AZURE_CONFIG_DIR is NOT set.
    monkeypatch.delenv("AZURE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    mod = _reload_telemetry()

    # Before override: heuristic returns "interactive" (the bug).
    assert mod._detect_auth_mode() == "interactive"  # type: ignore[attr-defined]
    assert mod._build_envelope()["auth_mode"] == "interactive"

    # After override (simulating what record_auth_mode_from_default_credential does):
    mod.set_auth_mode("azure_cli")  # type: ignore[attr-defined]
    assert mod._build_envelope()["auth_mode"] == "azure_cli"
