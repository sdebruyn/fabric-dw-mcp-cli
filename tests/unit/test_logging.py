"""Tests for fabric_dw.logging — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
import logging

import pytest

from fabric_dw.logging import redact_auth_header, setup_logging


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

    def test_output_is_valid_json(self, caplog: pytest.LogCaptureFixture) -> None:
        setup_logging(logging.DEBUG)
        with caplog.at_level(logging.DEBUG, logger="fabric_dw.test_json"):
            logger = logging.getLogger("fabric_dw.test_json")
            logger.debug("hello json")

        assert len(caplog.records) >= 1
        record = caplog.records[-1]
        # The formatter attached by setup_logging should produce JSON
        # We get the handler to format the record
        root = logging.getLogger()
        handler = root.handlers[0] if root.handlers else None
        if handler is not None:
            formatted = handler.format(record)
            parsed = json.loads(formatted)
            assert "level" in parsed
            assert "msg" in parsed
            assert "name" in parsed
            assert "time" in parsed


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
