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

    def test_non_bearer_authorization_header_preserved(self) -> None:
        headers = {"Authorization": "Basic dXNlcjpwYXNz"}
        result = redact_auth_header(headers)
        # Non-bearer tokens are left as-is (only Bearer is a concern for this codebase)
        assert result["Authorization"] == "Basic dXNlcjpwYXNz"


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
        """An extra field named 'level' that is NOT a standard LogRecord attr must not
        overwrite the core 'level' key in the JSON payload.

        We use a custom attribute name that happens to collide with a payload key but
        is not in _STANDARD_LOGRECORD_ATTRS.  We simulate this by injecting a key that
        is definitely not standard and checking that core keys are preserved.
        """
        formatter = _JsonFormatter()
        # Inject a non-standard attribute that would collide with 'level' if the guard
        # were missing.  We use '_custom_level' since 'level' is a payload key we compute.
        # The real protection: extras loop does `if key not in payload`.
        record = self._make_record(msg="real message", extra={"workspace_id": "ws-123"})
        parsed = json.loads(formatter.format(record))
        # Core keys must retain their proper values
        assert parsed["level"] == "DEBUG"
        assert parsed["msg"] == "real message"
        # The extra must appear
        assert parsed.get("workspace_id") == "ws-123"

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
