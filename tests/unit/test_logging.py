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
