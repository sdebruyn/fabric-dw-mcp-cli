"""Tests for fabric_dw.logging — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
import logging

from fabric_dw.logging import _JsonFormatter, redact_auth_header, redact_sql, setup_logging


class TestSetupLogging:
    def test_debug_level_sets_fabric_dw_logger_to_debug(self) -> None:
        setup_logging(logging.DEBUG)
        assert logging.getLogger("fabric_dw").level == logging.DEBUG

    def test_info_level_sets_fabric_dw_logger_to_info(self) -> None:
        setup_logging(logging.INFO)
        assert logging.getLogger("fabric_dw").level == logging.INFO

    def test_default_level_is_info(self) -> None:
        setup_logging()
        assert logging.getLogger("fabric_dw").level == logging.INFO

    def test_does_not_mutate_root_logger(self) -> None:
        """setup_logging must scope to fabric_dw, not the root logger (C11)."""
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        setup_logging(logging.DEBUG)
        assert root.level == original_level
        assert root.handlers == original_handlers

    def test_fabric_dw_logger_does_not_propagate(self) -> None:
        """fabric_dw logger must not propagate to root to avoid third-party handler leaks."""
        setup_logging()
        assert logging.getLogger("fabric_dw").propagate is False

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

    def test_extra_colliding_core_key_preserved_with_prefix(self) -> None:
        """An extra field named 'level' must not overwrite the core key but must not be lost.

        C12: instead of silently dropping the colliding extra, the formatter
        must store it under the prefix ``extra_<name>`` so both the core value
        and the extra value are visible in the structured log output.
        """
        formatter = _JsonFormatter()
        record = self._make_record(msg="real message", extra={"level": "EVIL"})
        parsed = json.loads(formatter.format(record))
        # Core key retains the real log level.
        assert parsed["level"] == "DEBUG", f"Core level was overwritten to {parsed['level']!r}"
        assert parsed["msg"] == "real message"
        # The colliding extra must survive under the prefixed key — not be lost.
        assert parsed.get("extra_level") == "EVIL", (
            f"Colliding extra was not preserved under 'extra_level'; got {parsed!r}"
        )

    def test_extra_colliding_time_key_preserved_with_prefix(self) -> None:
        """An extra field named 'time' must be stored as 'extra_time' (not lost).

        'time' is a core payload key added by _JsonFormatter itself (not by
        logging internals), so it is NOT a standard LogRecord attr and CAN be
        injected via ``extra=``.
        """
        formatter = _JsonFormatter()
        record = self._make_record(extra={"time": "2099-01-01T00:00:00Z"})
        parsed = json.loads(formatter.format(record))
        # Core 'time' key (from formatTime) must be present and is a real timestamp.
        assert "time" in parsed
        # The injected 'time' extra must survive under the prefixed key.
        assert parsed.get("extra_time") == "2099-01-01T00:00:00Z"

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


class TestRedactSql:
    """Tests for redact_sql — SQL secret redaction helper."""

    def test_secret_clause_value_is_replaced(self) -> None:
        """SECRET = '<token>' must become SECRET = '***'."""
        sql = (
            "COPY INTO [dbo].[t] FROM 'https://x.blob.core.windows.net/c/f.parquet'"
            " WITH (CREDENTIAL = (IDENTITY = 'Shared Access Signature',"
            " SECRET = 'sv=2024&sig=ABC123xyz'))"
        )
        result = redact_sql(sql)
        assert "ABC123xyz" not in result
        assert "SECRET = '***'" in result

    def test_secret_clause_case_insensitive(self) -> None:
        """secret = and Secret = must also be redacted."""
        sql = (
            "COPY INTO [t] FROM 'x' WITH (CREDENTIAL = (IDENTITY = 'y', secret = 'mysecrettoken'))"
        )
        result = redact_sql(sql)
        assert "mysecrettoken" not in result
        assert "***" in result

    def test_secret_with_doubled_quotes_redacted(self) -> None:
        """Values that contain '' (doubled single-quote escaping via _sq) must be redacted."""
        sql = (
            "COPY INTO [t] FROM 'x'"
            " WITH (CREDENTIAL = (IDENTITY = 'y', SECRET = 'token''with''quotes'))"
        )
        result = redact_sql(sql)
        assert "token''with''quotes" not in result
        assert "***" in result

    def test_sas_url_query_string_redacted(self) -> None:
        """https://... URLs with query strings must have ?... replaced with ?***."""
        sql = (
            "COPY INTO [t] FROM"
            " 'https://myaccount.blob.core.windows.net/c/f.parquet"
            "?sv=2024-01-01&sig=ABCSECRET&se=2025&sp=r'"
        )
        result = redact_sql(sql)
        assert "ABCSECRET" not in result
        assert "https://myaccount.blob.core.windows.net/c/f.parquet?***" in result

    def test_copy_into_with_secret_and_sas_url_both_redacted(self) -> None:
        """A realistic COPY INTO with both SECRET and a SAS URL must have both redacted."""
        sql = (
            "COPY INTO [dbo].[target] "
            "FROM 'https://storage.dfs.core.windows.net/container/data.parquet"
            "?sv=2022&sig=TOKEN&sp=r' "
            "WITH (CREDENTIAL = (IDENTITY = 'Shared Access Signature',"
            " SECRET = 'sv=2022&sig=TOKEN'))"
        )
        result = redact_sql(sql)
        assert "TOKEN" not in result
        assert "SECRET = '***'" in result
        assert "?***" in result

    def test_sql_without_secrets_unchanged(self) -> None:
        """A plain SELECT without secrets is returned unchanged."""
        sql = "SELECT TOP 10 * FROM [dbo].[mytable]"
        result = redact_sql(sql)
        assert result == sql

    def test_original_string_not_mutated(self) -> None:
        """redact_sql must not mutate the input string."""
        sql = "COPY INTO [t] FROM 'x' WITH (CREDENTIAL = (IDENTITY = 'y', SECRET = 'tok'))"
        original = sql
        redact_sql(sql)
        assert sql == original

    def test_url_without_query_string_unchanged(self) -> None:
        """An https:// URL without a query string must not be modified."""
        sql = "COPY INTO [t] FROM 'https://myaccount.blob.core.windows.net/c/f.parquet'"
        result = redact_sql(sql)
        assert result == sql

    def test_abfss_url_query_string_redacted(self) -> None:
        """abfss:// Azure Data Lake Storage URLs with query strings must be redacted."""
        sql = (
            "COPY INTO [t] FROM"
            " 'abfss://container@account.dfs.core.windows.net/data.parquet"
            "?sv=2024-01-01&sig=ABCSECRET&se=2025&sp=r'"
        )
        result = redact_sql(sql)
        assert "ABCSECRET" not in result
        assert "abfss://container@account.dfs.core.windows.net/data.parquet?***" in result

    def test_wasbs_url_query_string_redacted(self) -> None:
        """wasbs:// Azure Blob Storage URLs with query strings must be redacted."""
        sql = (
            "COPY INTO [t] FROM"
            " 'wasbs://container@account.blob.core.windows.net/data.parquet"
            "?sv=2024&sig=WASB_SECRET'"
        )
        result = redact_sql(sql)
        assert "WASB_SECRET" not in result
        assert "?***" in result
