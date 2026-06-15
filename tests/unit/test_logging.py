"""Tests for fabric_dw.logging — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
import logging

from fabric_dw.logging import _JsonFormatter, redact_auth_header, setup_logging


class TestSetupLogging:
    def test_debug_level_sets_root_logger_to_debug(self) -> None:
        setup_logging(logging.DEBUG)
        assert logging.getLogger().level == logging.DEBUG

    def test_info_level_sets_root_logger_to_info(self) -> None:
        setup_logging(logging.INFO)
        assert logging.getLogger().level == logging.INFO

    def test_default_level_is_info(self) -> None:
        setup_logging()
        assert logging.getLogger().level == logging.INFO

    def test_output_is_valid_json(self) -> None:
        """The JSON formatter must produce parseable JSON with required keys."""
        formatter = _JsonFormatter()
        logger = logging.getLogger("fabric_dw.test_json")
        record = logger.makeRecord(
            name="fabric_dw.test_json",
            level=logging.DEBUG,
            fn="test_file.py",
            lno=1,
            msg="hello json",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        parsed = json.loads(formatted)
        assert "level" in parsed
        assert "msg" in parsed
        assert "name" in parsed
        assert "time" in parsed
        assert parsed["msg"] == "hello json"
        assert parsed["level"] == "DEBUG"
        assert parsed["name"] == "fabric_dw.test_json"


class TestRedactAuthHeader:
    def test_redacts_bearer_token(self) -> None:
        headers = {"Authorization": "Bearer abc123"}
        result = redact_auth_header(headers)
        assert result["Authorization"] == "Bearer ***"

    def test_leaves_other_headers_untouched(self) -> None:
        headers = {"Authorization": "Bearer abc123", "Content-Type": "application/json"}
        result = redact_auth_header(headers)
        assert result["Content-Type"] == "application/json"

    def test_no_authorization_header_unchanged(self) -> None:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        result = redact_auth_header(headers)
        assert result == headers

    def test_does_not_mutate_original_dict(self) -> None:
        headers = {"Authorization": "Bearer secret-token"}
        original = dict(headers)
        redact_auth_header(headers)
        assert headers == original

    def test_non_bearer_authorization_scheme_redacted_with_scheme_preserved(self) -> None:
        """Any Authorization scheme value is redacted; the scheme word is preserved."""
        headers = {"Authorization": "Basic dXNlcjpwYXNz"}
        result = redact_auth_header(headers)
        # C10: all Authorization values are redacted (not only Bearer); scheme preserved.
        assert result["Authorization"] == "Basic ***"

    def test_case_insensitive_authorization_redacted(self) -> None:
        """redact_auth_header must handle Authorization regardless of key casing."""
        headers = {"authorization": "bearer mytoken"}
        result = redact_auth_header(headers)
        assert result["authorization"] == "bearer ***"

    def test_proxy_authorization_redacted(self) -> None:
        """Proxy-Authorization is also a sensitive auth-bearing header."""
        headers = {"Proxy-Authorization": "Bearer proxytoken"}
        result = redact_auth_header(headers)
        assert result["Proxy-Authorization"] == "Bearer ***"

    def test_cookie_header_redacted(self) -> None:
        """Cookie header is sensitive and must be replaced wholesale."""
        headers = {"Cookie": "session=abc123; user=foo"}
        result = redact_auth_header(headers)
        assert result["Cookie"] == "***"

    def test_x_ms_authorization_auxiliary_redacted(self) -> None:
        """x-ms-authorization-auxiliary is an Azure auth header and must be redacted."""
        headers = {"X-Ms-Authorization-Auxiliary": "Bearer auxtoken"}
        result = redact_auth_header(headers)
        assert result["X-Ms-Authorization-Auxiliary"] == "***"


class TestJsonFormatterExtraFields:
    """Tests for extra= field propagation in _JsonFormatter."""

    def _make_record(self, msg: str = "hello", extra: dict | None = None) -> logging.LogRecord:
        logger = logging.getLogger("fabric_dw.test_extra")
        return logger.makeRecord(
            name="fabric_dw.test_extra",
            level=logging.DEBUG,
            fn="test_file.py",
            lno=1,
            msg=msg,
            args=(),
            exc_info=None,
            extra=extra,
        )

    def test_extra_string_field_included(self) -> None:
        """A string extra field must appear in the formatted JSON output."""
        formatter = _JsonFormatter()
        record = self._make_record(extra={"workspace_id": "ws-abc"})
        parsed = json.loads(formatter.format(record))
        assert "workspace_id" in parsed, f"workspace_id missing from {parsed}"
        assert parsed["workspace_id"] == "ws-abc"

    def test_extra_int_field_included(self) -> None:
        """An integer extra field must appear in the formatted JSON output."""
        formatter = _JsonFormatter()
        record = self._make_record(extra={"count": 42})
        parsed = json.loads(formatter.format(record))
        assert parsed.get("count") == 42

    def test_extra_non_standard_field_named_level_does_not_overwrite_core_key(self) -> None:
        """An extra field named 'level' must not overwrite the core 'level' payload key.

        'level' is NOT in _STANDARD_LOGRECORD_ATTRS (only 'levelname' is), so it
        reaches the extras merge loop.  The guard ``if key not in payload`` must
        prevent it from clobbering the real level value derived from levelname.
        """
        formatter = _JsonFormatter()
        # Inject a colliding 'level' key via extra — this is NOT a standard LogRecord
        # attribute, so the extras loop will encounter it.  The guard must block it.
        record = self._make_record(msg="real message", extra={"level": "EVIL"})
        parsed = json.loads(formatter.format(record))
        # Core key must retain the real level, not "EVIL"
        assert parsed["level"] == "DEBUG", (
            f"Guard failed: level was overwritten to {parsed['level']!r}"
        )
        assert parsed["msg"] == "real message"

    def test_non_serialisable_extra_coerced_to_str(self) -> None:
        """Non-JSON-serialisable extra values must be coerced to str, not raise."""
        formatter = _JsonFormatter()

        class Unserializable:
            def __repr__(self) -> str:
                return "Unserializable()"

        record = self._make_record(extra={"obj": Unserializable()})
        # Must not raise
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "obj" in parsed
        assert isinstance(parsed["obj"], str)

    def test_no_extra_fields_still_valid_json(self) -> None:
        """A record with no extra fields still produces valid JSON with core keys."""
        formatter = _JsonFormatter()
        record = self._make_record()
        parsed = json.loads(formatter.format(record))
        assert set(parsed.keys()) >= {"level", "name", "msg", "time"}

    def test_extra_request_id_lands_in_json(self) -> None:
        """The request_id extra used by http_client must appear in JSON output."""
        formatter = _JsonFormatter()
        record = self._make_record(extra={"request_id": "fabric-req-id-001"})
        parsed = json.loads(formatter.format(record))
        assert parsed.get("request_id") == "fabric-req-id-001"
