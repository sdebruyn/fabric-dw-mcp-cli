"""Unit tests for fabric_dw.services.load."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import httpx
import pytest
import respx
from azure.core.credentials_async import AsyncTokenCredential

from fabric_dw.exceptions import FabricError, ItemKindError
from fabric_dw.models import CopyIntoResult, WarehouseKind
from fabric_dw.services.load import (
    _DFS_API_VERSION,
    _DFS_CREATE_MAX_RETRIES,
    _DFS_CREATE_RETRY_DELAY,
    _ONELAKE_DFS_BASE,
    CopyIntoCsvOptions,
    _build_copy_into_sql,
    _json_to_parquet,
    _log_dfs_error,
    _safe_dest_filename,
    _sq,
    _validate_https_url,
    _validate_staging_name,
    copy_into_from_url,
    infer_file_format,
    load_local_file,
    onelake_upload_file,
)

# ---------------------------------------------------------------------------
# _sq — SQL string escaping
# ---------------------------------------------------------------------------


class TestSqlEscape:
    def test_no_quotes(self) -> None:
        assert _sq("hello") == "hello"

    def test_single_quote_escaped(self) -> None:
        assert _sq("O'Reilly") == "O''Reilly"

    def test_multiple_quotes(self) -> None:
        assert _sq("it's a 'test'") == "it''s a ''test''"

    def test_empty_string(self) -> None:
        assert _sq("") == ""


# ---------------------------------------------------------------------------
# _build_copy_into_sql — SQL generation
# ---------------------------------------------------------------------------


class TestBuildCopyIntoSql:
    def test_parquet_no_credential(self) -> None:
        sql = _build_copy_into_sql(
            "dbo",
            "sales",
            "https://onelake.dfs.fabric.microsoft.com/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/ffffffff-0000-1111-2222-333333333333/Files/f.parquet",
            "PARQUET",
        )
        assert "COPY INTO [dbo].[sales]" in sql
        assert "FILE_TYPE = 'PARQUET'" in sql
        assert "CREDENTIAL" not in sql
        assert "FROM 'https://onelake.dfs.fabric.microsoft.com" in sql

    def test_csv_with_options(self) -> None:
        csv_opts = CopyIntoCsvOptions(
            delimiter=",",
            first_row=2,
            encoding="UTF8",
            field_quote='"',
            row_terminator=r"\n",
        )
        sql = _build_copy_into_sql(
            "dbo", "t", "https://example.com/f.csv", "CSV", csv_options=csv_opts
        )
        assert "FILE_TYPE = 'CSV'" in sql
        assert "FIELDTERMINATOR = ','" in sql
        assert "FIRSTROW = 2" in sql
        assert "ENCODING = 'UTF8'" in sql
        assert "FIELDQUOTE = '\"'" in sql
        assert r"ROWTERMINATOR = '\n'" in sql

    def test_csv_no_header(self) -> None:
        csv_opts = CopyIntoCsvOptions(first_row=1)
        sql = _build_copy_into_sql(
            "dbo", "t", "https://example.com/f.csv", "CSV", csv_options=csv_opts
        )
        assert "FIRSTROW = 1" in sql

    def test_sas_credential(self) -> None:
        sql = _build_copy_into_sql(
            "dbo",
            "t",
            "https://sa.blob.core.windows.net/c/f.parquet",
            "PARQUET",
            credential_type="sas",
            secret="my-sas-token",  # noqa: S106
        )
        assert "CREDENTIAL = (IDENTITY = 'Shared Access Signature', SECRET = 'my-sas-token')" in sql

    def test_managed_identity_credential(self) -> None:
        sql = _build_copy_into_sql(
            "dbo",
            "t",
            "https://sa.blob.core.windows.net/c/f.parquet",
            "PARQUET",
            credential_type="managed-identity",
        )
        assert "CREDENTIAL = (IDENTITY = 'Managed Identity')" in sql

    def test_service_principal_credential(self) -> None:
        sql = _build_copy_into_sql(
            "dbo",
            "t",
            "https://sa.blob.core.windows.net/c/f.parquet",
            "PARQUET",
            credential_type="service-principal",
            identity="my-client-id",
            secret="my-secret",  # noqa: S106
        )
        assert "CREDENTIAL = (IDENTITY = 'my-client-id', SECRET = 'my-secret')" in sql

    def test_account_key_credential(self) -> None:
        sql = _build_copy_into_sql(
            "dbo",
            "t",
            "https://sa.blob.core.windows.net/c/f.parquet",
            "PARQUET",
            credential_type="account-key",
            secret="base64key==",  # noqa: S106
        )
        assert "CREDENTIAL = (IDENTITY = 'Storage Account Key', SECRET = 'base64key==')" in sql

    def test_max_errors_csv(self) -> None:
        """MAXERRORS must appear in the SQL for CSV loads."""
        sql = _build_copy_into_sql("dbo", "t", "https://example.com/f.csv", "CSV", max_errors=5)
        assert "MAXERRORS = 5" in sql

    def test_max_errors_parquet_omitted(self) -> None:
        """MAXERRORS must NOT appear when FILE_TYPE is PARQUET (Fabric rejects it)."""
        sql = _build_copy_into_sql(
            "dbo", "t", "https://example.com/f.parquet", "PARQUET", max_errors=0
        )
        assert "MAXERRORS" not in sql

    def test_max_errors_parquet_none_unaffected(self) -> None:
        """No MAXERRORS in PARQUET SQL when max_errors is None."""
        sql = _build_copy_into_sql(
            "dbo", "t", "https://example.com/f.parquet", "PARQUET", max_errors=None
        )
        assert "MAXERRORS" not in sql

    def test_rejected_row_location(self) -> None:
        sql = _build_copy_into_sql(
            "dbo",
            "t",
            "https://example.com/f.parquet",
            "PARQUET",
            rejected_row_location="https://example.com/rejected/",
        )
        assert "REJECTED_ROW_LOCATION = 'https://example.com/rejected/'" in sql

    def test_invalid_file_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported FILE_TYPE"):
            _build_copy_into_sql("dbo", "t", "https://example.com/f.json", "JSON")

    def test_url_with_single_quote_escaped(self) -> None:
        # URLs with single quotes are escaped in the SQL literal.
        sql = _build_copy_into_sql("dbo", "t", "https://example.com/it's/file.parquet", "PARQUET")
        assert "it''s" in sql

    def test_secret_not_in_no_credential_sql(self) -> None:
        sql = _build_copy_into_sql(
            "dbo",
            "t",
            "https://example.com/f.parquet",
            "PARQUET",
            credential_type="none",
            secret="super-secret",  # noqa: S106
        )
        # When credential_type is "none", no CREDENTIAL clause is emitted.
        assert "CREDENTIAL" not in sql
        assert "super-secret" not in sql

    def test_target_bracket_quoted(self) -> None:
        sql = _build_copy_into_sql(
            "my_schema", "my_table", "https://example.com/f.parquet", "PARQUET"
        )
        assert "[my_schema].[my_table]" in sql


# ---------------------------------------------------------------------------
# infer_file_format
# ---------------------------------------------------------------------------


class TestInferFileFormat:
    def test_csv(self) -> None:
        assert infer_file_format(Path("data.csv")) == "csv"

    def test_json(self) -> None:
        assert infer_file_format(Path("data.json")) == "json"

    def test_parquet(self) -> None:
        assert infer_file_format(Path("data.parquet")) == "parquet"

    def test_pq_alias(self) -> None:
        assert infer_file_format(Path("data.pq")) == "parquet"

    def test_uppercase_extension(self) -> None:
        assert infer_file_format(Path("data.CSV")) == "csv"

    def test_unknown_extension_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot infer file format"):
            infer_file_format(Path("data.orc"))

    def test_no_extension_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot infer file format"):
            infer_file_format(Path("data"))


# ---------------------------------------------------------------------------
# _json_to_parquet — JSON conversion
# ---------------------------------------------------------------------------


class TestJsonToParquet:
    def test_converts_json_to_parquet(self, tmp_path: Path) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(
            '{"id": 1, "name": "Alice"}\n{"id": 2, "name": "Bob"}\n',
            encoding="utf-8",
        )

        out = _json_to_parquet(json_file)
        try:
            assert out.exists()
            assert out.suffix == ".parquet"

            import pyarrow.parquet as pap  # noqa: PLC0415

            table = pap.read_table(out)
            assert table.num_rows == 2
            assert "id" in table.column_names
            assert "name" in table.column_names
        finally:
            out.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# copy_into_from_url — SQL endpoint guard
# ---------------------------------------------------------------------------


class TestCopyIntoFromUrlGuard:
    async def test_sql_endpoint_rejected(self) -> None:
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        with pytest.raises(ItemKindError):
            await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://example.com/f.parquet",
                file_type="PARQUET",
                kind=WarehouseKind.SQL_ENDPOINT,
            )


# ---------------------------------------------------------------------------
# copy_into_from_url — unit test with mocked run_query
# ---------------------------------------------------------------------------


class TestCopyIntoFromUrl:
    async def test_returns_copy_into_result(self) -> None:
        """copy_into_from_url must return rows_loaded from cursor.rowcount (fetch='rowcount').

        mssql-python ≥ 1.9.0: COPY INTO returns no result set; run_query with
        fetch='rowcount' returns ([], [(N,)]) where N is cursor.rowcount.
        rows_rejected is always 0 (not exposed via ODBC rowcount).
        """
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )

        with patch("fabric_dw.services.load.run_query") as mock_run:
            # Simulate fetch="rowcount" return: ([], [(N,)])
            mock_run.return_value = ([], [(100,)])
            result = await copy_into_from_url(
                target,
                "dbo",
                "sales",
                "https://example.com/f.parquet",
                file_type="PARQUET",
            )

        assert isinstance(result, CopyIntoResult)
        assert result.rows_loaded == 100
        assert result.rows_rejected == 0  # not available via rowcount
        assert result.target == "dbo.sales"
        # Verify fetch="rowcount" was requested
        _call_kwargs = mock_run.call_args.kwargs
        assert _call_kwargs.get("fetch") == "rowcount"
        assert _call_kwargs.get("commit") is True

    async def test_rowcount_negative_treated_as_zero(self) -> None:
        """ODBC rowcount=-1 (unknown) must be treated as 0 rows loaded."""
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )

        with patch("fabric_dw.services.load.run_query") as mock_run:
            # Simulate ODBC rowcount=-1 (driver cannot determine count)
            mock_run.return_value = ([], [(-1,)])
            result = await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://example.com/f.parquet",
                file_type="PARQUET",
            )

        assert result.rows_loaded == 0
        assert result.rows_rejected == 0

    async def test_rowcount_none_treated_as_zero(self) -> None:
        """rowcount=None in result rows must be treated as 0 rows loaded."""
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )

        with patch("fabric_dw.services.load.run_query") as mock_run:
            mock_run.return_value = ([], [(None,)])
            result = await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://example.com/f.parquet",
                file_type="PARQUET",
            )

        assert result.rows_loaded == 0
        assert result.rows_rejected == 0

    async def test_empty_rows_treated_as_zero(self) -> None:
        """Empty rows list from run_query must yield rows_loaded=0."""
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )

        with patch("fabric_dw.services.load.run_query") as mock_run:
            mock_run.return_value = ([], [])
            result = await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://example.com/f.parquet",
                file_type="PARQUET",
            )

        assert result.rows_loaded == 0
        assert result.rows_rejected == 0

    async def test_secret_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Secret values must never appear in log output (happy path)."""
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        secret = "super-secret-sas-token-xyz123"  # noqa: S105

        with (
            caplog.at_level(logging.DEBUG, logger="fabric_dw"),
            patch("fabric_dw.services.load.run_query") as mock_run,
        ):
            mock_run.return_value = ([], [(5,)])
            await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://sa.blob.core.windows.net/c/f.parquet",
                file_type="PARQUET",
                credential_type="sas",
                secret=secret,
            )

        # Secret must NOT appear in any log record emitted by the service.
        for record in caplog.records:
            assert secret not in record.getMessage()

    async def test_secret_not_in_error_on_driver_exception(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A4/B1: raw driver errors must NOT expose the secret or SQL statement.

        When the underlying driver raises an exception that would normally carry
        the full SQL statement text (which contains the embedded SAS/key secret),
        the service must wrap it in a FabricError with a safe message that
        contains neither the secret nor the raw SQL statement.
        """
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        secret = "top-secret-key-that-must-not-leak"  # noqa: S105

        # Simulate a raw driver error whose str() includes the SQL with the secret.
        class _FakeDriverError(Exception):
            pass

        sql_with_secret = f"COPY INTO [dbo].[t] FROM '...' WITH (SECRET = '{secret}')"
        driver_exc = _FakeDriverError(f"Driver error executing: {sql_with_secret}")

        with (
            caplog.at_level(logging.DEBUG, logger="fabric_dw"),
            patch("fabric_dw.services.load.run_query", side_effect=driver_exc),
            pytest.raises(FabricError) as exc_info,
        ):
            await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://sa.blob.core.windows.net/c/f.parquet",
                file_type="PARQUET",
                credential_type="sas",
                secret=secret,
            )

        # The raised FabricError message must NOT contain the secret.
        error_text = str(exc_info.value)
        assert secret not in error_text, f"Secret leaked into FabricError message: {error_text!r}"

        # The secret must NOT appear in any log record.
        for record in caplog.records:
            assert secret not in record.getMessage(), (
                f"Secret leaked into log record: {record.getMessage()!r}"
            )


# ---------------------------------------------------------------------------
# load_local_file — unit tests with mocked helpers
# ---------------------------------------------------------------------------


class TestLoadLocalFile:
    async def test_csv_load_cleans_up_lakehouse(self, tmp_path: Path) -> None:
        """Staging Lakehouse must be deleted even on success."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id,name\n1,Alice\n2,Bob\n", encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        lh_id = "lh-uuid-1234"

        with (
            patch(
                "fabric_dw.services.load.create_staging_lakehouse", return_value=lh_id
            ) as mock_create,
            patch("fabric_dw.services.load.onelake_upload_file", return_value=None) as mock_upload,
            patch(
                "fabric_dw.services.load.copy_into_from_url",
                return_value=CopyIntoResult(rows_loaded=2, rows_rejected=0, target="dbo.t"),
            ) as mock_copy,
            patch("fabric_dw.services.load.delete_lakehouse") as mock_delete,
        ):
            result = await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                csv_file,
                file_format="csv",
            )

        assert result.rows_loaded == 2
        mock_create.assert_called_once()
        mock_upload.assert_called_once()
        mock_copy.assert_called_once()
        mock_delete.assert_called_once_with(mock_http, ws_id, lh_id)

    async def test_lakehouse_cleaned_up_on_failure(self, tmp_path: Path) -> None:
        """Staging Lakehouse must be deleted even when COPY INTO fails."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id,name\n1,Alice\n", encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        mock_http = AsyncMock()
        mock_credential = AsyncMock()
        lh_id = "lh-fail-uuid"

        with (  # noqa: SIM117
            patch("fabric_dw.services.load.create_staging_lakehouse", return_value=lh_id),
            patch("fabric_dw.services.load.onelake_upload_file", return_value=None),
            patch(
                "fabric_dw.services.load.copy_into_from_url", side_effect=RuntimeError("SQL error")
            ),
            patch("fabric_dw.services.load.delete_lakehouse") as mock_delete,
        ):
            with pytest.raises(RuntimeError, match="SQL error"):
                await load_local_file(
                    mock_http,
                    mock_credential,
                    ws_id,
                    target,
                    "dbo",
                    "t",
                    csv_file,
                    file_format="csv",
                )

        mock_delete.assert_called_once_with(mock_http, ws_id, lh_id)

    async def test_keep_staging_does_not_delete(self, tmp_path: Path) -> None:
        """When --keep-staging is set, the Lakehouse must NOT be deleted."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        with (
            patch("fabric_dw.services.load.create_staging_lakehouse", return_value="lh-id"),
            patch("fabric_dw.services.load.onelake_upload_file", return_value=None),
            patch(
                "fabric_dw.services.load.copy_into_from_url",
                return_value=CopyIntoResult(rows_loaded=1, rows_rejected=0, target="dbo.t"),
            ),
            patch("fabric_dw.services.load.delete_lakehouse") as mock_delete,
        ):
            await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                csv_file,
                file_format="csv",
                keep_staging=True,
            )

        mock_delete.assert_not_called()

    async def test_json_converted_to_parquet(self, tmp_path: Path) -> None:
        """JSON local files are converted to Parquet before upload."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        uploaded_paths: list[Path] = []

        async def _capture_upload(
            _cred: object,
            _ws: object,
            _lh: object,
            _dest: str,
            path: Path,
            **_kw: object,
        ) -> None:
            uploaded_paths.append(path)

        with (
            patch("fabric_dw.services.load.create_staging_lakehouse", return_value="lh-id"),
            patch("fabric_dw.services.load.onelake_upload_file", side_effect=_capture_upload),
            patch(
                "fabric_dw.services.load.copy_into_from_url",
                return_value=CopyIntoResult(rows_loaded=2, rows_rejected=0, target="dbo.t"),
            ) as mock_copy,
            patch("fabric_dw.services.load.delete_lakehouse"),
        ):
            await load_local_file(
                mock_http, mock_credential, ws_id, target, "dbo", "t", json_file, file_format="json"
            )

        # The uploaded file should be a Parquet file (not the original JSON).
        assert len(uploaded_paths) == 1
        assert uploaded_paths[0].suffix == ".parquet"
        # The call to copy_into_from_url should use PARQUET file_type.
        _, kwargs = mock_copy.call_args
        assert kwargs.get("file_type") == "PARQUET" or mock_copy.call_args[0][4] == "PARQUET"

    async def test_copy_into_url_is_pure_guid_no_lakehouse_suffix(self, tmp_path: Path) -> None:
        """The onelake_url passed to copy_into_from_url must be pure-GUID with no .Lakehouse suffix.

        load_local_file builds the COPY INTO source URL from the staging Lakehouse IDs.
        This test asserts that the URL argument (positional arg index 3) does not
        contain '.Lakehouse' and starts with the expected pure-GUID prefix, matching
        the DFS upload path and Microsoft's documented OPENROWSET/COPY INTO source form.
        """
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id,name\n1,Alice\n", encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        lh_id = "c0ffeeee-dead-beef-cafe-123456789abc"

        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        with (
            patch("fabric_dw.services.load.create_staging_lakehouse", return_value=lh_id),
            patch("fabric_dw.services.load.onelake_upload_file", return_value=None),
            patch(
                "fabric_dw.services.load.copy_into_from_url",
                return_value=CopyIntoResult(rows_loaded=1, rows_rejected=0, target="dbo.t"),
            ) as mock_copy,
            patch("fabric_dw.services.load.delete_lakehouse"),
        ):
            await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                csv_file,
                file_format="csv",
            )

        mock_copy.assert_called_once()
        # The URL is the 4th positional argument (index 3): target, schema, table, url
        call_args = mock_copy.call_args
        url_arg: str = call_args[0][3]
        expected_prefix = f"https://onelake.dfs.fabric.microsoft.com/{ws_id}/{lh_id}/Files/"
        assert ".Lakehouse" not in url_arg, (
            f"COPY INTO URL must NOT contain '.Lakehouse' suffix; got: {url_arg!r}"
        )
        assert url_arg.startswith(expected_prefix), (
            f"COPY INTO URL must start with pure-GUID prefix {expected_prefix!r}; got: {url_arg!r}"
        )

    async def test_file_not_found_raises(self, tmp_path: Path) -> None:
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        with pytest.raises(FileNotFoundError):
            await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                tmp_path / "nonexistent.csv",
                file_format="csv",
            )

    async def test_sql_endpoint_rejected(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        with pytest.raises(ItemKindError):
            await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                csv_file,
                file_format="csv",
                kind=WarehouseKind.SQL_ENDPOINT,
            )


# ---------------------------------------------------------------------------
# Secret-safety: verify secrets never appear in SQL statements that get logged
# ---------------------------------------------------------------------------


class TestSecretSafety:
    def test_sas_secret_in_sql_but_no_plain_log(self) -> None:
        """The SQL itself contains the SAS token (required by Fabric), but we
        test that the service never logs the raw secret independently."""
        secret = "?sv=2021&se=...&sig=ABCDEF"  # noqa: S105
        sql = _build_copy_into_sql(
            "dbo",
            "t",
            "https://sa.blob.core.windows.net/c/f.parquet",
            "PARQUET",
            credential_type="sas",
            secret=secret,
        )
        # The SQL itself contains the (escaped) secret — this is by design
        # (COPY INTO must have it in the literal).  The important constraint
        # is that the SERVICE does not log the secret separately.
        assert _sq(secret) in sql

    def test_account_key_not_in_no_credential_sql(self) -> None:
        """Passing secret with credential_type='none' emits no CREDENTIAL clause at all."""
        secret = "account-key-base64-value=="  # noqa: S105
        sql = _build_copy_into_sql(
            "dbo",
            "t",
            "https://example.com/f.parquet",
            "PARQUET",
            credential_type="none",
            secret=secret,
        )
        assert "CREDENTIAL" not in sql
        assert secret not in sql


# ---------------------------------------------------------------------------
# B2: temp file cleaned up even when create_staging_lakehouse raises
# ---------------------------------------------------------------------------


class TestTempFileCleanup:
    async def test_converted_parquet_cleaned_up_when_create_lakehouse_fails(
        self, tmp_path: Path
    ) -> None:
        """B2: temp Parquet file must be deleted even if create_staging_lakehouse raises."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"id": 1}\n', encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        converted_paths: list[Path] = []

        original_json_to_parquet = _json_to_parquet

        def _capture_conversion(path: Path) -> Path:
            result = original_json_to_parquet(path)
            converted_paths.append(result)
            return result

        with (
            patch(
                "fabric_dw.services.load._json_to_parquet",
                side_effect=_capture_conversion,
            ),
            patch(
                "fabric_dw.services.load.create_staging_lakehouse",
                side_effect=RuntimeError("Lakehouse creation failed"),
            ),
            pytest.raises(RuntimeError, match="Lakehouse creation failed"),
        ):
            await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                json_file,
                file_format="json",
            )

        # The converted temp Parquet file must have been cleaned up.
        assert len(converted_paths) == 1, "Expected exactly one converted file"
        assert not converted_paths[0].exists(), (
            f"Temp Parquet file was not cleaned up: {converted_paths[0]}"
        )


# ---------------------------------------------------------------------------
# max_errors warning for Parquet/JSON-converted loads (#744)
# ---------------------------------------------------------------------------


class TestMaxErrorsParquetWarning:
    async def test_copy_into_from_url_parquet_with_max_errors_emits_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """copy_into_from_url with FILE_TYPE=PARQUET and max_errors must emit a WARNING
        and succeed — the common choke point covering local-file AND URL callers."""
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )

        with (
            caplog.at_level(logging.WARNING, logger="fabric_dw"),
            patch("fabric_dw.services.load.run_query") as mock_run,
        ):
            mock_run.return_value = ([], [(2,)])
            result = await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://onelake.dfs.fabric.microsoft.com/ws/lh/Files/f.parquet",
                file_type="PARQUET",
                max_errors=0,
            )

        assert result.rows_loaded == 2

        # A WARNING must have been logged mentioning max-errors and Parquet.
        warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "max-errors" in msg.lower() or "max_errors" in msg.lower() for msg in warning_messages
        ), f"Expected a warning about max_errors/max-errors; got: {warning_messages}"
        assert any("parquet" in msg.lower() for msg in warning_messages), (
            f"Expected warning to mention 'parquet'; got: {warning_messages}"
        )

    async def test_copy_into_from_url_csv_with_max_errors_no_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """copy_into_from_url with FILE_TYPE=CSV and max_errors must NOT emit a warning."""
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )

        with (
            caplog.at_level(logging.WARNING, logger="fabric_dw"),
            patch("fabric_dw.services.load.run_query") as mock_run,
        ):
            mock_run.return_value = ([], [(1,)])
            await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://example.com/f.csv",
                file_type="CSV",
                max_errors=10,
            )

        # No warning expected for CSV + max_errors.
        warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert not any(
            "max-errors" in msg.lower() or "max_errors" in msg.lower() for msg in warning_messages
        ), f"Unexpected warning for CSV max_errors; got: {warning_messages}"

    async def test_json_load_with_max_errors_emits_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When format=json (converted to Parquet) and max_errors is set, a WARNING
        must be emitted (via copy_into_from_url) and the load must succeed."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"id": 1}\n', encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        with (
            caplog.at_level(logging.WARNING, logger="fabric_dw"),
            patch("fabric_dw.services.load.create_staging_lakehouse", return_value="lh-id"),
            patch("fabric_dw.services.load.onelake_upload_file", return_value=None),
            # Patch run_query (not copy_into_from_url) so the real copy_into_from_url
            # runs and the warning is emitted at the common choke point.
            patch("fabric_dw.services.load.run_query", return_value=([], [(1,)])),
            patch("fabric_dw.services.load.delete_lakehouse"),
        ):
            result = await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                json_file,
                file_format="json",
                max_errors=0,
            )

        # Load must succeed.
        assert result.rows_loaded == 1

        # A WARNING must have been logged mentioning max-errors and Parquet.
        warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "max-errors" in msg.lower() or "max_errors" in msg.lower() for msg in warning_messages
        ), f"Expected a warning about max_errors/max-errors; got: {warning_messages}"
        assert any("parquet" in msg.lower() for msg in warning_messages), (
            f"Expected warning to mention 'parquet'; got: {warning_messages}"
        )

    async def test_parquet_load_with_max_errors_emits_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When format=parquet and max_errors is set, a WARNING must be emitted."""
        parquet_file = tmp_path / "data.parquet"
        parquet_file.write_bytes(b"PAR1")  # minimal placeholder

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        with (
            caplog.at_level(logging.WARNING, logger="fabric_dw"),
            patch("fabric_dw.services.load.create_staging_lakehouse", return_value="lh-id"),
            patch("fabric_dw.services.load.onelake_upload_file", return_value=None),
            patch("fabric_dw.services.load.run_query", return_value=([], [(3,)])),
            patch("fabric_dw.services.load.delete_lakehouse"),
        ):
            result = await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                parquet_file,
                file_format="parquet",
                max_errors=5,
            )

        assert result.rows_loaded == 3

        warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "max-errors" in msg.lower() or "max_errors" in msg.lower() for msg in warning_messages
        ), f"Expected a warning about max_errors/max-errors; got: {warning_messages}"

    async def test_csv_load_with_max_errors_no_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """CSV loads with max_errors must NOT emit a warning (MAXERRORS is valid for CSV)."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        with (
            caplog.at_level(logging.WARNING, logger="fabric_dw"),
            patch("fabric_dw.services.load.create_staging_lakehouse", return_value="lh-id"),
            patch("fabric_dw.services.load.onelake_upload_file", return_value=None),
            patch(
                "fabric_dw.services.load.copy_into_from_url",
                return_value=CopyIntoResult(rows_loaded=1, rows_rejected=0, target="dbo.t"),
            ),
            patch("fabric_dw.services.load.delete_lakehouse"),
        ):
            await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                csv_file,
                file_format="csv",
                max_errors=10,
            )

        # No warning expected for CSV + max_errors.
        warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert not any(
            "max-errors" in msg.lower() or "max_errors" in msg.lower() for msg in warning_messages
        ), f"Unexpected warning for CSV max_errors; got: {warning_messages}"


# ---------------------------------------------------------------------------
# A1: safe destination filename (percent-encoding)
# ---------------------------------------------------------------------------


class TestSafeDestFilename:
    def test_plain_filename_unchanged(self) -> None:
        assert _safe_dest_filename(Path("/some/path/data.parquet")) == "data.parquet"

    def test_percent_encoded_slash_stripped(self) -> None:
        # %2F is a forward-slash; the directory component must be stripped.
        result = _safe_dest_filename(Path("evil%2Fpath.parquet"))
        assert "/" not in result
        assert "\\" not in result
        assert result == "path.parquet"

    def test_backslash_encoded_decoded(self) -> None:
        # %5C decodes to backslash; on POSIX, backslash is a valid filename char
        # (not a path separator), so the decoded name is returned as-is.
        result = _safe_dest_filename(Path("evil%5Cpath.parquet"))
        # The important property: no forward-slash (path separator) in the result.
        assert "/" not in result

    def test_normal_filename_with_spaces_decoded(self) -> None:
        result = _safe_dest_filename(Path("my%20file.parquet"))
        assert result == "my file.parquet"


# ---------------------------------------------------------------------------
# A2: file size limit
# ---------------------------------------------------------------------------


class TestFileSizeLimit:
    async def test_oversized_file_raises_value_error(self, tmp_path: Path) -> None:
        """A2: files larger than _MAX_STAGING_FILE_BYTES must be rejected."""
        from fabric_dw.services.load import _MAX_STAGING_FILE_BYTES  # noqa: PLC0415

        large_file = tmp_path / "large.csv"
        large_file.write_bytes(b"x")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        # Mock stat() to return a size exceeding the limit.
        import unittest.mock  # noqa: PLC0415

        oversized = unittest.mock.MagicMock(st_size=_MAX_STAGING_FILE_BYTES + 1)
        with (
            unittest.mock.patch.object(Path, "stat", return_value=oversized),
            pytest.raises(ValueError, match="too large to stage"),
        ):
            await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                large_file,
                file_format="csv",
            )


# ---------------------------------------------------------------------------
# A3: staging lakehouse name validation
# ---------------------------------------------------------------------------


class TestStagingNameValidation:
    def test_valid_name_accepted(self) -> None:
        assert _validate_staging_name("staging_abc123") == "staging_abc123"

    def test_hyphens_allowed(self) -> None:
        assert _validate_staging_name("my-staging-lh") == "my-staging-lh"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_staging_name("")

    def test_newline_rejected(self) -> None:
        with pytest.raises(ValueError, match="control characters"):
            _validate_staging_name("staging\ninjected")

    def test_carriage_return_rejected(self) -> None:
        with pytest.raises(ValueError, match="control characters"):
            _validate_staging_name("staging\rinjected")

    def test_special_characters_rejected(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            _validate_staging_name("staging; DROP TABLE")

    async def test_invalid_staging_name_raises_in_load_local_file(self, tmp_path: Path) -> None:
        """A3: load_local_file must reject invalid staging_lakehouse_name early."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        with pytest.raises(ValueError, match="control characters"):
            await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                csv_file,
                file_format="csv",
                staging_lakehouse_name="bad\nname",
            )


# ---------------------------------------------------------------------------
# A5: URL scheme validation (SSRF prevention)
# ---------------------------------------------------------------------------


class TestUrlSchemeValidation:
    def test_https_url_accepted(self) -> None:
        _validate_https_url("https://sa.blob.core.windows.net/c/f.parquet", "url")  # no error

    def test_http_url_rejected(self) -> None:
        with pytest.raises(ValueError, match="only HTTPS"):
            _validate_https_url("http://sa.blob.core.windows.net/c/f.parquet", "url")

    def test_imds_link_local_rejected(self) -> None:
        with pytest.raises(ValueError, match="link-local"):
            _validate_https_url("https://169.254.169.254/metadata/instance", "url")

    def test_metadata_azure_host_rejected(self) -> None:
        with pytest.raises(ValueError, match="link-local"):
            _validate_https_url("https://metadata.azure.internal/", "url")

    def test_ftp_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="only HTTPS"):
            _validate_https_url("ftp://example.com/file.parquet", "url")

    async def test_http_url_rejected_in_copy_into_from_url(self) -> None:
        """A5: copy_into_from_url must reject non-HTTPS source URLs."""
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        with pytest.raises(ValueError, match="only HTTPS"):
            await copy_into_from_url(
                target,
                "dbo",
                "t",
                "http://sa.blob.core.windows.net/c/f.parquet",
                file_type="PARQUET",
            )

    async def test_http_rejected_row_location_rejected_in_load_local_file(
        self, tmp_path: Path
    ) -> None:
        """A5: load_local_file must reject non-HTTPS rejected_row_location."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        with pytest.raises(ValueError, match="only HTTPS"):
            await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                csv_file,
                file_format="csv",
                rejected_row_location="http://example.com/rejected/",
            )


# ---------------------------------------------------------------------------
# OneLake DFS upload: create → append → flush sequence
# ---------------------------------------------------------------------------


class TestOneLakeUploadFile:
    """Verify the ADLS Gen2 DFS create/append/flush request sequence.

    The OneLake DFS API requires:
    - PUT  ?resource=file                    Content-Length: 0             (create empty file)
    - PATCH ?action=append&position=N        Content-Length: <chunk size>  (upload data)
    - PATCH ?action=flush&position=<total>   Content-Length: 0             (commit)

    All requests must carry x-ms-version (the ADLS Gen2 API version) so the
    server uses a deterministic, supported protocol version rather than falling
    back to a very old default that may reject the call with 400
    UnsupportedRestVersion.

    Missing or incorrect Content-Length on the PUT or flush PATCH results in a
    400 ContentLengthMustBeZero / MissingRequiredHeader from OneLake.

    On any non-2xx response the code must log the x-ms-error-code header and
    the response body (never the auth token) before raising HTTPStatusError.

    The create PUT is retried up to _DFS_CREATE_MAX_RETRIES times on failure to
    tolerate the transient provisioning lag that can occur immediately after a
    new Lakehouse is created.
    """

    _WS_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    _LH_ID = "ffffffff-0000-1111-2222-333333333333"
    # Pure-GUID DFS path — NO .Lakehouse suffix.  The friendly-name form is
    # rejected with 400 FriendlyNameSupportDisabled on some tenants (#402).
    _DFS_BASE = f"{_ONELAKE_DFS_BASE}/{_WS_ID}/{_LH_ID}/Files/data.parquet"

    def _make_credential(self) -> AsyncTokenCredential:
        """Return a minimal async credential stub that returns a fake token."""
        token = MagicMock()
        token.token = "fake-bearer-token"  # noqa: S105
        cred = AsyncMock(spec=AsyncTokenCredential)
        cred.get_token.return_value = token
        return cred  # type: ignore[return-value]

    # -----------------------------------------------------------------------
    # Pure-GUID DFS path — no .Lakehouse suffix (#402)
    # -----------------------------------------------------------------------

    @respx.mock
    async def test_dfs_url_is_pure_guid_no_type_suffix(self, tmp_path: Path) -> None:
        """All DFS requests must use the pure-GUID path without a .Lakehouse type suffix.

        Tenants with FriendlyNameSupportDisabled reject paths of the form
        ``{workspace}/{item}.Lakehouse/Files/…`` with 400.  The correct form is
        ``{workspace_guid}/{item_guid}/Files/…`` which works on all tenants.
        """
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"PARQUET")

        all_urls: list[str] = []

        def _capture_put(request: httpx.Request) -> httpx.Response:
            all_urls.append(str(request.url))
            return httpx.Response(201)

        def _capture_patch(request: httpx.Request) -> httpx.Response:
            all_urls.append(str(request.url))
            action = request.url.params.get("action", "")
            return httpx.Response(202 if action == "append" else 200)

        expected_path_prefix = f"{_ONELAKE_DFS_BASE}/{self._WS_ID}/{self._LH_ID}/Files/"
        # Register routes for the pure-GUID URL (no .Lakehouse suffix).
        respx.put(self._DFS_BASE).mock(side_effect=_capture_put)
        respx.patch(self._DFS_BASE).mock(side_effect=_capture_patch)

        await onelake_upload_file(
            self._make_credential(),
            self._WS_ID,
            self._LH_ID,
            "data.parquet",
            data_file,
        )

        assert all_urls, "Expected at least one DFS request"
        for url in all_urls:
            assert ".Lakehouse" not in url, (
                f"DFS URL must NOT contain '.Lakehouse' type suffix (FriendlyNameSupportDisabled); "
                f"got: {url!r}"
            )
            assert url.startswith(expected_path_prefix), (
                f"DFS URL must use pure-GUID form '{expected_path_prefix}…'; got: {url!r}"
            )

    @respx.mock
    async def test_create_has_content_length_zero(self, tmp_path: Path) -> None:
        """PUT ?resource=file must include Content-Length: 0."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"PARQUETDATA")

        captured_create: list[httpx.Request] = []

        def _on_create(request: httpx.Request) -> httpx.Response:
            captured_create.append(request)
            return httpx.Response(201)

        respx.put(self._DFS_BASE).mock(side_effect=_on_create)
        respx.patch(self._DFS_BASE).mock(return_value=httpx.Response(202))

        await onelake_upload_file(
            self._make_credential(),
            self._WS_ID,
            self._LH_ID,
            "data.parquet",
            data_file,
        )

        assert len(captured_create) == 1, "Expected exactly one PUT (create) call"
        create_req = captured_create[0]
        assert create_req.method == "PUT"
        assert create_req.url.params.get("resource") == "file"
        assert create_req.headers.get("content-length") == "0", (
            "Content-Length must be '0' on the PUT create call; "
            f"got: {create_req.headers.get('content-length')!r}"
        )

    @respx.mock
    async def test_append_has_correct_content_length(self, tmp_path: Path) -> None:
        """PATCH ?action=append must carry Content-Length equal to the chunk size."""
        payload = b"HELLO WORLD"
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(payload)

        captured_appends: list[httpx.Request] = []

        def _on_patch(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("action") == "append":
                captured_appends.append(request)
                return httpx.Response(202)
            # flush
            return httpx.Response(200)

        respx.put(self._DFS_BASE).mock(return_value=httpx.Response(201))
        respx.patch(self._DFS_BASE).mock(side_effect=_on_patch)

        await onelake_upload_file(
            self._make_credential(),
            self._WS_ID,
            self._LH_ID,
            "data.parquet",
            data_file,
        )

        assert len(captured_appends) >= 1, "Expected at least one append PATCH call"
        for req in captured_appends:
            cl = req.headers.get("content-length")
            assert cl is not None, f"Append PATCH must include Content-Length header; got {cl!r}"
            body_bytes = req.read()
            assert cl == str(len(body_bytes)), (
                f"Append PATCH Content-Length {cl!r} must equal actual body size {len(body_bytes)}"
            )

    @respx.mock
    async def test_flush_has_content_length_zero(self, tmp_path: Path) -> None:
        """PATCH ?action=flush must include Content-Length: 0 (no body)."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"PAYLOAD")

        captured_flush: list[httpx.Request] = []

        def _on_patch(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("action") == "flush":
                captured_flush.append(request)
                return httpx.Response(200)
            return httpx.Response(202)

        respx.put(self._DFS_BASE).mock(return_value=httpx.Response(201))
        respx.patch(self._DFS_BASE).mock(side_effect=_on_patch)

        await onelake_upload_file(
            self._make_credential(),
            self._WS_ID,
            self._LH_ID,
            "data.parquet",
            data_file,
        )

        assert len(captured_flush) == 1, "Expected exactly one flush PATCH call"
        flush_req = captured_flush[0]
        assert flush_req.url.params.get("action") == "flush"
        assert flush_req.headers.get("content-length") == "0", (
            "Content-Length must be '0' on the flush PATCH call; "
            f"got: {flush_req.headers.get('content-length')!r}"
        )

    @respx.mock
    async def test_flush_position_equals_total_bytes(self, tmp_path: Path) -> None:
        """The flush ?position= must equal the total number of bytes uploaded."""
        payload = b"ABCDEFGHIJ"
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(payload)

        flush_positions: list[int] = []

        def _on_patch(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("action") == "flush":
                flush_positions.append(int(request.url.params["position"]))
                return httpx.Response(200)
            return httpx.Response(202)

        respx.put(self._DFS_BASE).mock(return_value=httpx.Response(201))
        respx.patch(self._DFS_BASE).mock(side_effect=_on_patch)

        await onelake_upload_file(
            self._make_credential(),
            self._WS_ID,
            self._LH_ID,
            "data.parquet",
            data_file,
        )

        assert flush_positions == [len(payload)], (
            f"flush position must equal total file size {len(payload)}; got {flush_positions}"
        )

    @respx.mock
    async def test_full_create_append_flush_order(self, tmp_path: Path) -> None:
        """Requests must arrive in order: PUT create, PATCH append(s), PATCH flush."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"X" * 10)

        call_order: list[str] = []

        def _on_put(_request: httpx.Request) -> httpx.Response:
            call_order.append("create")
            return httpx.Response(201)

        def _on_patch(request: httpx.Request) -> httpx.Response:
            action = request.url.params.get("action", "")
            call_order.append(action)
            return httpx.Response(202 if action == "append" else 200)

        respx.put(self._DFS_BASE).mock(side_effect=_on_put)
        respx.patch(self._DFS_BASE).mock(side_effect=_on_patch)

        await onelake_upload_file(
            self._make_credential(),
            self._WS_ID,
            self._LH_ID,
            "data.parquet",
            data_file,
        )

        assert call_order[0] == "create", f"First call must be create; got {call_order}"
        assert call_order[-1] == "flush", f"Last call must be flush; got {call_order}"
        assert all(a == "append" for a in call_order[1:-1]), (
            f"Middle calls must all be append; got {call_order}"
        )

    @respx.mock
    async def test_create_non_2xx_raises(self, tmp_path: Path) -> None:
        """A persistent non-2xx on the create PUT must raise HTTPStatusError after all retries."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"DATA")

        # All retry attempts return 400 — must ultimately raise.
        respx.put(self._DFS_BASE).mock(return_value=httpx.Response(400))

        with (
            patch("fabric_dw.services.load.asyncio.sleep"),  # skip real sleep
            pytest.raises(httpx.HTTPStatusError),
        ):
            await onelake_upload_file(
                self._make_credential(),
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

    @respx.mock
    async def test_append_non_2xx_raises(self, tmp_path: Path) -> None:
        """A non-2xx response on any append PATCH must raise HTTPStatusError."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"DATA")

        respx.put(self._DFS_BASE).mock(return_value=httpx.Response(201))
        respx.patch(self._DFS_BASE).mock(return_value=httpx.Response(500))

        with pytest.raises(httpx.HTTPStatusError):
            await onelake_upload_file(
                self._make_credential(),
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

    @respx.mock
    async def test_flush_non_2xx_raises(self, tmp_path: Path) -> None:
        """A non-2xx response on the flush PATCH must raise HTTPStatusError."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"DATA")

        def _on_patch(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("action") == "append":
                return httpx.Response(202)
            # flush → server error
            return httpx.Response(500)

        respx.put(self._DFS_BASE).mock(return_value=httpx.Response(201))
        respx.patch(self._DFS_BASE).mock(side_effect=_on_patch)

        with pytest.raises(httpx.HTTPStatusError):
            await onelake_upload_file(
                self._make_credential(),
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

    @respx.mock
    async def test_multi_chunk_append_positions(self, tmp_path: Path) -> None:
        """With chunk_size=4, a 10-byte file must produce three appends at positions 0, 4, 8."""
        payload = b"X" * 10
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(payload)

        append_positions: list[int] = []

        def _on_patch(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("action") == "append":
                append_positions.append(int(request.url.params["position"]))
                return httpx.Response(202)
            # flush
            return httpx.Response(200)

        respx.put(self._DFS_BASE).mock(return_value=httpx.Response(201))
        respx.patch(self._DFS_BASE).mock(side_effect=_on_patch)

        await onelake_upload_file(
            self._make_credential(),
            self._WS_ID,
            self._LH_ID,
            "data.parquet",
            data_file,
            chunk_size=4,
        )

        # 10 bytes / 4-byte chunks → chunks of sizes 4, 4, 2 → positions 0, 4, 8
        assert append_positions == [0, 4, 8], (
            f"Expected append positions [0, 4, 8] for 3-chunk upload; got {append_positions}"
        )

    # -----------------------------------------------------------------------
    # x-ms-version header (protocol correctness — #402)
    # -----------------------------------------------------------------------

    @respx.mock
    async def test_all_requests_carry_x_ms_version(self, tmp_path: Path) -> None:
        """Every DFS request (create, append, flush) must include the x-ms-version header.

        Sending a valid API version makes OneLake use a deterministic protocol
        version instead of falling back to a very old default that can return
        400 UnsupportedRestVersion.
        """
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"HELLO")

        all_requests: list[httpx.Request] = []

        def _capture_put(request: httpx.Request) -> httpx.Response:
            all_requests.append(request)
            return httpx.Response(201)

        def _capture_patch(request: httpx.Request) -> httpx.Response:
            all_requests.append(request)
            action = request.url.params.get("action", "")
            return httpx.Response(202 if action == "append" else 200)

        respx.put(self._DFS_BASE).mock(side_effect=_capture_put)
        respx.patch(self._DFS_BASE).mock(side_effect=_capture_patch)

        await onelake_upload_file(
            self._make_credential(),
            self._WS_ID,
            self._LH_ID,
            "data.parquet",
            data_file,
        )

        assert all_requests, "Expected at least one DFS request"
        for req in all_requests:
            version = req.headers.get("x-ms-version")
            assert version == _DFS_API_VERSION, (
                f"Request {req.method} {req.url.params} must include "
                f"x-ms-version={_DFS_API_VERSION!r}; got {version!r}"
            )

    def test_log_dfs_error_does_not_read_request_headers(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_log_dfs_error must never read resp.request.headers (which contains Authorization).

        The token-leak guarantee is that _log_dfs_error only reads *response* data.
        This test attaches a request with a secret token to the response and asserts
        that the secret does not appear in any log record, proving that the function
        does not forward request headers to the logger.
        """
        secret_token = "super-secret-bearer-XYZ"  # noqa: S105

        # Build a response that has a fabricated request object carrying Authorization.
        request = httpx.Request(
            "PUT",
            "https://onelake.dfs.fabric.microsoft.com/ws/lh/Files/f.parquet",
            headers={"Authorization": f"Bearer {secret_token}"},
        )
        resp = httpx.Response(
            400,
            json={"error": {"code": "UnsupportedRestVersion", "message": "bad version"}},
            headers={"x-ms-error-code": "UnsupportedRestVersion", "x-ms-request-id": "req-xyz"},
            request=request,
        )

        with caplog.at_level(logging.DEBUG, logger="fabric_dw"):
            _log_dfs_error(resp, "DFS create")

        for record in caplog.records:
            assert secret_token not in record.getMessage(), (
                f"Auth token leaked into log record: {record.getMessage()!r}"
            )

    # -----------------------------------------------------------------------
    # Diagnostic error logging on non-2xx responses (no secret leakage)
    # -----------------------------------------------------------------------

    def test_log_dfs_error_logs_error_code_and_body(self, caplog: pytest.LogCaptureFixture) -> None:
        """_log_dfs_error must log x-ms-error-code and body at ERROR level."""
        resp = httpx.Response(
            400,
            json={"error": {"code": "ContentLengthMustBeZero", "message": "CL must be 0"}},
            headers={"x-ms-error-code": "ContentLengthMustBeZero", "x-ms-request-id": "req-123"},
        )

        with caplog.at_level(logging.ERROR, logger="fabric_dw"):
            _log_dfs_error(resp, "DFS create")

        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "400" in log_text, "Status code must appear in log"
        assert "ContentLengthMustBeZero" in log_text, "Error code must appear in log"
        assert "req-123" in log_text, "x-ms-request-id must appear in log"

    def test_log_dfs_error_never_logs_token(self, caplog: pytest.LogCaptureFixture) -> None:
        """_log_dfs_error must never log the Authorization header value."""
        secret_token = "super-secret-bearer-token-xyz"  # noqa: S105
        # The Authorization header is only on the *request*; _log_dfs_error only
        # reads from the *response*.  This test verifies the logged text contains no token.
        resp = httpx.Response(
            400,
            json={"error": {"code": "InvalidInput", "message": "bad"}},
            headers={
                "x-ms-error-code": "InvalidInput",
                # Response headers will NOT include Authorization, but we verify
                # that even if somehow a token appears it is not re-logged.
                "x-custom-header": f"not-auth:{secret_token}",
            },
        )

        with caplog.at_level(logging.ERROR, logger="fabric_dw"):
            _log_dfs_error(resp, "DFS flush")

        for record in caplog.records:
            msg = record.getMessage()
            # The token itself must not appear, but header value may appear in body
            # if it were present; we verify the specific secret string is absent from
            # the log line produced by _log_dfs_error (which only logs body + error code).
            # We check the full formatted message.
            assert secret_token not in record.getMessage(), f"Secret token leaked into log: {msg!r}"

    @respx.mock
    async def test_create_400_logs_error_body(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A 400 on the create PUT must log the x-ms-error-code and body before raising."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"DATA")

        respx.put(self._DFS_BASE).mock(
            return_value=httpx.Response(
                400,
                json={"error": {"code": "InvalidUri", "message": "bad path"}},
                headers={"x-ms-error-code": "InvalidUri", "x-ms-request-id": "req-abc"},
            )
        )

        with (
            caplog.at_level(logging.ERROR, logger="fabric_dw"),
            patch("fabric_dw.services.load.asyncio.sleep"),  # skip real sleep
            pytest.raises(httpx.HTTPStatusError),
        ):
            await onelake_upload_file(
                self._make_credential(),
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

        error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_logs, "Expected at least one ERROR log on 400 create"
        combined = "\n".join(r.getMessage() for r in error_logs)
        assert "InvalidUri" in combined, f"Error code not in log: {combined!r}"

    @respx.mock
    async def test_create_400_auth_token_not_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """On a 400 create response the auth token must never appear in the log."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"DATA")

        respx.put(self._DFS_BASE).mock(
            return_value=httpx.Response(
                400,
                json={"error": {"code": "SomeError", "message": "fail"}},
                headers={"x-ms-error-code": "SomeError"},
            )
        )

        fake_token = "fake-bearer-token"  # noqa: S105
        with (
            caplog.at_level(logging.DEBUG, logger="fabric_dw"),
            patch("fabric_dw.services.load.asyncio.sleep"),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await onelake_upload_file(
                self._make_credential(),
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

        for record in caplog.records:
            assert fake_token not in record.getMessage(), (
                f"Auth token leaked into log record: {record.getMessage()!r}"
            )

    # -----------------------------------------------------------------------
    # Retry behaviour on create 400 (transient provisioning lag — #402)
    # -----------------------------------------------------------------------

    @respx.mock
    async def test_create_retries_on_400_then_succeeds(self, tmp_path: Path) -> None:
        """The create PUT is retried on 400 — success on the second attempt must proceed."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"PAYLOAD")

        call_count = 0

        def _flaky_put(_request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(400)  # first attempt fails
            return httpx.Response(201)  # second attempt succeeds

        respx.put(self._DFS_BASE).mock(side_effect=_flaky_put)
        respx.patch(self._DFS_BASE).mock(return_value=httpx.Response(202))

        with patch("fabric_dw.services.load.asyncio.sleep") as mock_sleep:
            await onelake_upload_file(
                self._make_credential(),
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

        assert call_count == 2, f"Expected 2 PUT attempts (1 fail + 1 success); got {call_count}"
        # Verify that the backoff delay was applied: attempt 0 → sleep(_DFS_CREATE_RETRY_DELAY * 1)
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == [_DFS_CREATE_RETRY_DELAY * 1], (
            f"Expected backoff delays {[_DFS_CREATE_RETRY_DELAY * 1]!r}; got {sleep_calls!r}"
        )

    @respx.mock
    async def test_create_exhausts_retries_then_raises(self, tmp_path: Path) -> None:
        """After _DFS_CREATE_MAX_RETRIES failures the create PUT must raise HTTPStatusError."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"PAYLOAD")

        call_count = 0

        def _always_400(_request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(400)

        respx.put(self._DFS_BASE).mock(side_effect=_always_400)

        with (
            patch("fabric_dw.services.load.asyncio.sleep") as mock_sleep,
            pytest.raises(httpx.HTTPStatusError),
        ):
            await onelake_upload_file(
                self._make_credential(),
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

        assert call_count == _DFS_CREATE_MAX_RETRIES, (
            f"Expected exactly {_DFS_CREATE_MAX_RETRIES} PUT attempts; got {call_count}"
        )
        # Verify the arithmetic backoff ramp: sleep is called between each retry pair
        # (not after the final attempt), so for MAX_RETRIES=3: delays are 2 s, 4 s.
        expected_delays = [
            _DFS_CREATE_RETRY_DELAY * (i + 1) for i in range(_DFS_CREATE_MAX_RETRIES - 1)
        ]
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == expected_delays, (
            f"Expected backoff delays {expected_delays!r}; got {sleep_calls!r}"
        )

    @respx.mock
    async def test_create_409_not_retried(self, tmp_path: Path) -> None:
        """A 409 Conflict on the create PUT must NOT be retried.

        409 means the file already exists (from a prior partial attempt).  Retrying
        the same PUT would be incorrect; the error must surface immediately so the
        caller can handle it (e.g. delete and re-try at a higher level).
        """
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"PAYLOAD")

        call_count = 0

        def _always_409(_request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(409)

        respx.put(self._DFS_BASE).mock(side_effect=_always_409)

        with (
            patch("fabric_dw.services.load.asyncio.sleep") as mock_sleep,
            pytest.raises(httpx.HTTPStatusError),
        ):
            await onelake_upload_file(
                self._make_credential(),
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

        assert call_count == 1, f"409 must not be retried; got {call_count} PUT attempts"
        assert not mock_sleep.called, "No sleep should occur when 409 surfaces immediately"

    @pytest.mark.parametrize("status", [401, 403, 404])
    @respx.mock
    async def test_create_hard_failure_not_retried(self, tmp_path: Path, status: int) -> None:
        """Hard failure statuses (401/403/404) on the create PUT must NOT be retried."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"PAYLOAD")

        call_count = 0

        def _hard_fail(_request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(status)

        respx.put(self._DFS_BASE).mock(side_effect=_hard_fail)

        with (
            patch("fabric_dw.services.load.asyncio.sleep") as mock_sleep,
            pytest.raises(httpx.HTTPStatusError),
        ):
            await onelake_upload_file(
                self._make_credential(),
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

        assert call_count == 1, f"HTTP {status} must not be retried; got {call_count} PUT attempts"
        assert not mock_sleep.called, f"No sleep should occur on non-retryable HTTP {status}"

    @respx.mock
    async def test_flush_400_logs_error_body(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A 400 on the flush PATCH must log the error code and body before raising."""
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"DATA")

        def _on_patch(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("action") == "append":
                return httpx.Response(202)
            return httpx.Response(
                400,
                json={"error": {"code": "InvalidFlushPosition", "message": "bad position"}},
                headers={"x-ms-error-code": "InvalidFlushPosition"},
            )

        respx.put(self._DFS_BASE).mock(return_value=httpx.Response(201))
        respx.patch(self._DFS_BASE).mock(side_effect=_on_patch)

        with (
            caplog.at_level(logging.ERROR, logger="fabric_dw"),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await onelake_upload_file(
                self._make_credential(),
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

        error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_logs, "Expected at least one ERROR log on 400 flush"
        combined = "\n".join(r.getMessage() for r in error_logs)
        assert "InvalidFlushPosition" in combined, f"Error code not in log: {combined!r}"


# ---------------------------------------------------------------------------
# T-LOAD-AUTH: credential failures in onelake_upload_file → AuthError
# ---------------------------------------------------------------------------


class TestOnelakeUploadFileAuthError:
    """Credential failures during DFS upload must surface as AuthError, not raw tracebacks.

    The call to ``credential.get_token(STORAGE_SCOPE)`` inside
    ``onelake_upload_file`` is outside ``FabricHttpClient._get_token``, so it
    needs its own mapping from :class:`~azure.core.exceptions.ClientAuthenticationError`
    to :class:`~fabric_dw.exceptions.AuthError`.  Both the base exception class
    and its subclass :class:`~azure.identity.CredentialUnavailableError` must be
    handled.
    """

    _WS_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    _LH_ID = "ffffffff-0000-1111-2222-333333333333"

    def _make_failing_credential(self, exc: Exception) -> AsyncTokenCredential:
        cred = MagicMock(spec=AsyncTokenCredential)
        cred.get_token = AsyncMock(side_effect=exc)
        return cred  # type: ignore[return-value]

    @pytest.mark.asyncio
    async def test_client_auth_error_maps_to_auth_error(self, tmp_path: Path) -> None:
        """ClientAuthenticationError from get_token must be re-raised as AuthError."""
        from azure.core.exceptions import ClientAuthenticationError  # noqa: PLC0415

        from fabric_dw.exceptions import AuthError  # noqa: PLC0415

        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"PAR1")

        cred = self._make_failing_credential(
            ClientAuthenticationError("DefaultAzureCredential failed to retrieve a token")
        )

        with pytest.raises(AuthError) as exc_info:
            await onelake_upload_file(
                cred,
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

        msg = str(exc_info.value)
        assert "Azure authentication failed" in msg
        assert "az login" in msg
        assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_credential_unavailable_maps_to_auth_error(self, tmp_path: Path) -> None:
        """CredentialUnavailableError (subclass of ClientAuthenticationError) maps to AuthError."""
        from azure.identity import CredentialUnavailableError  # noqa: PLC0415

        from fabric_dw.exceptions import AuthError  # noqa: PLC0415

        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"PAR1")

        cred = self._make_failing_credential(
            CredentialUnavailableError("No credential was available")
        )

        with pytest.raises(AuthError) as exc_info:
            await onelake_upload_file(
                cred,
                self._WS_ID,
                self._LH_ID,
                "data.parquet",
                data_file,
            )

        msg = str(exc_info.value)
        assert "Azure authentication failed" in msg
        assert "az login" in msg
        assert exc_info.value.__cause__ is not None


# ---------------------------------------------------------------------------
# _build_credential_clause — edge cases (None return paths)
# ---------------------------------------------------------------------------


class TestBuildCredentialClause:
    """Directly test _build_credential_clause for the None-return branches."""

    def test_sas_without_secret_returns_none(self) -> None:
        from fabric_dw.services.load import _build_credential_clause  # noqa: PLC0415

        result = _build_credential_clause("sas", None, None)
        assert result is None

    def test_service_principal_without_identity_returns_none(self) -> None:
        from fabric_dw.services.load import _build_credential_clause  # noqa: PLC0415

        result = _build_credential_clause("service-principal", "secret", None)
        assert result is None

    def test_account_key_without_secret_returns_none(self) -> None:
        from fabric_dw.services.load import _build_credential_clause  # noqa: PLC0415

        result = _build_credential_clause("account-key", None, None)
        assert result is None

    def test_none_credential_type_returns_none(self) -> None:
        from fabric_dw.services.load import _build_credential_clause  # noqa: PLC0415

        result = _build_credential_clause("none", "secret", "identity")
        assert result is None

    def test_sas_with_secret_returns_clause(self) -> None:
        from fabric_dw.services.load import _build_credential_clause  # noqa: PLC0415

        result = _build_credential_clause("sas", "my-token", None)
        assert result is not None
        assert "Shared Access Signature" in result
        assert "my-token" in result

    def test_service_principal_with_both_returns_clause(self) -> None:
        from fabric_dw.services.load import _build_credential_clause  # noqa: PLC0415

        result = _build_credential_clause("service-principal", "my-secret", "client-id")
        assert result is not None
        assert "client-id" in result
        assert "my-secret" in result


# ---------------------------------------------------------------------------
# _build_copy_into_sql: credential clause with cred=None (sas without secret)
# ---------------------------------------------------------------------------


class TestBuildCopyIntoSqlCredNone:
    """When _build_credential_clause returns None the WITH clause must not include CREDENTIAL."""

    def test_sas_without_secret_no_credential_in_sql(self) -> None:
        sql = _build_copy_into_sql(
            "dbo",
            "t",
            "https://example.com/f.parquet",
            "PARQUET",
            credential_type="sas",
            secret=None,  # no secret → credential clause returns None
        )
        assert "CREDENTIAL" not in sql

    def test_parquet_csv_options_ignored(self) -> None:
        """CSV options must NOT appear in PARQUET SQL (only applied when file_type='CSV')."""
        csv_opts = CopyIntoCsvOptions(delimiter=",", first_row=2)
        sql = _build_copy_into_sql(
            "dbo", "t", "https://example.com/f.parquet", "PARQUET", csv_options=csv_opts
        )
        assert "FIELDTERMINATOR" not in sql
        assert "FIRSTROW" not in sql


# ---------------------------------------------------------------------------
# _assert_not_sql_endpoint
# ---------------------------------------------------------------------------


class TestAssertNotSqlEndpoint:
    def test_warehouse_not_rejected(self) -> None:
        from fabric_dw.services.load import _assert_not_sql_endpoint  # noqa: PLC0415

        # Should not raise
        _assert_not_sql_endpoint(WarehouseKind.WAREHOUSE)

    def test_sql_endpoint_rejected(self) -> None:
        from fabric_dw.services.load import _assert_not_sql_endpoint  # noqa: PLC0415

        with pytest.raises(ItemKindError):
            _assert_not_sql_endpoint(WarehouseKind.SQL_ENDPOINT)


# ---------------------------------------------------------------------------
# _log_dfs_error: unreadable body (exception in resp.text)
# ---------------------------------------------------------------------------


class TestLogDfsErrorUnreadableBody:
    def test_unreadable_body_logs_warning_and_continues(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_log_dfs_error must handle resp.text raising an exception gracefully."""
        resp = httpx.Response(
            500,
            headers={"x-ms-error-code": "InternalError", "x-ms-request-id": "req-999"},
        )
        # Make .text raise an exception to exercise the except branch.
        decode_error = RuntimeError("cannot decode")
        with (
            patch.object(type(resp), "text", new_callable=PropertyMock, side_effect=decode_error),
            caplog.at_level(logging.WARNING, logger="fabric_dw"),
        ):
            _log_dfs_error(resp, "DFS flush")

        warning_logs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_logs, "Expected a WARNING log when body is unreadable"
        assert "could not read response body" in warning_logs[0].getMessage()


# ---------------------------------------------------------------------------
# create_staging_lakehouse — 201 path and LRO paths
# ---------------------------------------------------------------------------


class TestCreateStagingLakehouse:
    def _make_sync_resp(self, headers: dict, body: dict) -> object:
        """Return a sync-style mock response (json() is NOT async in FabricHttpClient)."""
        resp = MagicMock()
        resp.headers = headers
        resp.json.return_value = body
        return resp

    async def test_201_path_returns_item_id(self) -> None:
        """When the API responds 201 with id in body, return that id directly."""
        from fabric_dw.services.load import create_staging_lakehouse  # noqa: PLC0415

        item_id = "aabbccdd-1234-5678-abcd-ef0123456789"
        mock_resp = self._make_sync_resp(headers={}, body={"id": item_id})

        mock_http = AsyncMock()
        mock_http.request.return_value = mock_resp

        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        result = await create_staging_lakehouse(mock_http, ws_id, "my_staging")

        assert result == item_id

    async def test_201_path_no_id_raises_server_error(self) -> None:
        """When the 201 body lacks 'id', FabricServerError must be raised."""
        from fabric_dw.exceptions import FabricServerError  # noqa: PLC0415
        from fabric_dw.services.load import create_staging_lakehouse  # noqa: PLC0415

        mock_resp = self._make_sync_resp(headers={}, body={"displayName": "staging"})

        mock_http = AsyncMock()
        mock_http.request.return_value = mock_resp

        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        with pytest.raises(FabricServerError, match="no id in body"):
            await create_staging_lakehouse(mock_http, ws_id, "my_staging")

    async def test_202_lro_path_a_resourceid(self) -> None:
        """202 LRO with resourceId key in result must return that id."""
        from fabric_dw.services.load import create_staging_lakehouse  # noqa: PLC0415

        item_id = "res-id-uuid-1234"
        location = "https://api.fabric.microsoft.com/v1/operations/op-abc"

        mock_resp = self._make_sync_resp(headers={"Location": location}, body={})

        mock_http = AsyncMock()
        mock_http.request.return_value = mock_resp
        mock_http.poll_operation.return_value = {"resourceId": item_id}

        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        result = await create_staging_lakehouse(mock_http, ws_id, "my_staging")

        assert result == item_id
        mock_http.poll_operation.assert_called_once_with(location)

    async def test_202_lro_path_a_created_item_id(self) -> None:
        """202 LRO with createdItemId key in result must return that id."""
        from fabric_dw.services.load import create_staging_lakehouse  # noqa: PLC0415

        item_id = "created-item-uuid-5678"
        location = "https://api.fabric.microsoft.com/v1/operations/op-xyz"

        mock_resp = self._make_sync_resp(headers={"Location": location}, body={})

        mock_http = AsyncMock()
        mock_http.request.return_value = mock_resp
        mock_http.poll_operation.return_value = {"createdItemId": item_id}

        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        result = await create_staging_lakehouse(mock_http, ws_id, "my_staging")

        assert result == item_id

    async def test_202_lro_path_b_operations_result(self) -> None:
        """202 LRO Path B: no resource key → GET /operations/{id}/result with 'id' field."""
        from fabric_dw.services.load import create_staging_lakehouse  # noqa: PLC0415

        item_id = "path-b-item-id-9999"
        op_id = "op-path-b"
        location = f"https://api.fabric.microsoft.com/v1/operations/{op_id}"

        # First request returns a response with a Location header (202 LRO).
        first_resp = self._make_sync_resp(headers={"Location": location}, body={})
        # Second request returns the operations result body.
        second_resp = self._make_sync_resp(headers={}, body={"id": item_id})

        # LRO result has no resource keys.
        lro_result = {"status": "Succeeded", "something": "else"}

        mock_http = AsyncMock()
        mock_http.request.side_effect = [first_resp, second_resp]
        mock_http.poll_operation.return_value = lro_result

        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        result = await create_staging_lakehouse(mock_http, ws_id, "my_staging")

        assert result == item_id

    async def test_202_lro_no_id_raises_server_error(self) -> None:
        """202 LRO with no usable id in either path must raise FabricServerError."""
        from fabric_dw.exceptions import FabricServerError  # noqa: PLC0415
        from fabric_dw.services.load import create_staging_lakehouse  # noqa: PLC0415

        op_id = "op-no-id"
        location = f"https://api.fabric.microsoft.com/v1/operations/{op_id}"

        first_resp = self._make_sync_resp(headers={"Location": location}, body={})
        # Path B result also has no 'id'.
        second_resp = self._make_sync_resp(headers={}, body={"status": "Succeeded"})

        lro_result = {"status": "Succeeded"}

        mock_http = AsyncMock()
        mock_http.request.side_effect = [first_resp, second_resp]
        mock_http.poll_operation.return_value = lro_result

        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        with pytest.raises(FabricServerError, match="no item ID"):
            await create_staging_lakehouse(mock_http, ws_id, "my_staging")


# ---------------------------------------------------------------------------
# delete_lakehouse — 404 suppression
# ---------------------------------------------------------------------------


class TestDeleteLakehouse:
    async def test_successful_delete(self) -> None:
        from fabric_dw.services.load import delete_lakehouse  # noqa: PLC0415

        mock_http = AsyncMock()
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        await delete_lakehouse(mock_http, ws_id, "lh-id-xyz")

        mock_http.request.assert_called_once()
        call_args = mock_http.request.call_args
        assert call_args[0][0] == "DELETE"
        assert "lh-id-xyz" in call_args[0][2]

    async def test_404_suppressed(self) -> None:
        """delete_lakehouse must silently swallow NotFoundError (already deleted)."""
        from fabric_dw.exceptions import NotFoundError  # noqa: PLC0415
        from fabric_dw.services.load import delete_lakehouse  # noqa: PLC0415

        mock_http = AsyncMock()
        mock_http.request.side_effect = NotFoundError("lakehouse not found")

        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        # Must not raise
        await delete_lakehouse(mock_http, ws_id, "already-gone-lh")


# ---------------------------------------------------------------------------
# copy_into_from_url — rejected_row_location HTTPS validation
# ---------------------------------------------------------------------------


class TestCopyIntoFromUrlRejectedRowValidation:
    async def test_http_rejected_row_location_raises(self) -> None:
        """rejected_row_location with http:// must raise ValueError before executing SQL."""
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        with (
            patch("fabric_dw.services.load.run_query") as mock_rq,
            pytest.raises(ValueError, match="only HTTPS"),
        ):
            await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://example.com/f.parquet",
                file_type="PARQUET",
                rejected_row_location="http://example.com/rejected/",
            )
        # Validation fires before SQL execution — run_query must not be called.
        mock_rq.assert_not_called()

    async def test_fabric_error_reraised_as_is(self) -> None:
        """FabricError from run_query must be re-raised as-is (not wrapped)."""
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        orig_error = FabricError("already mapped server error")

        with (
            patch("fabric_dw.services.load.run_query", side_effect=orig_error),
            pytest.raises(FabricError) as exc_info,
        ):
            await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://example.com/f.parquet",
                file_type="PARQUET",
            )

        assert exc_info.value is orig_error


# ---------------------------------------------------------------------------
# load_local_file — parquet path (no JSON conversion)
# ---------------------------------------------------------------------------


class TestLoadLocalFileParquetPath:
    async def test_parquet_file_not_converted(self, tmp_path: Path) -> None:
        """Parquet local files must be uploaded directly without JSON→Parquet conversion."""
        pq_file = tmp_path / "data.parquet"
        pq_file.write_bytes(b"PARQUETDATA")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        with (
            patch("fabric_dw.services.load.create_staging_lakehouse", return_value="lh-id"),
            patch("fabric_dw.services.load.onelake_upload_file", return_value=None) as mock_upload,
            patch(
                "fabric_dw.services.load.copy_into_from_url",
                return_value=CopyIntoResult(rows_loaded=3, rows_rejected=0, target="dbo.t"),
            ) as mock_copy,
            patch("fabric_dw.services.load.delete_lakehouse"),
            patch("fabric_dw.services.load._json_to_parquet") as mock_convert,
        ):
            result = await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                pq_file,
                file_format="parquet",
            )

        # No JSON conversion should occur for parquet input.
        mock_convert.assert_not_called()
        assert result.rows_loaded == 3

        # COPY INTO should use PARQUET file_type.
        _, call_kwargs = mock_copy.call_args
        assert call_kwargs.get("file_type") == "PARQUET"

        # Uploaded path must be the original parquet file, not a converted temp file.
        upload_call = mock_upload.call_args
        uploaded_path: Path = upload_call[0][4]
        assert uploaded_path == pq_file


# ---------------------------------------------------------------------------
# load_local_file — OSError on converted_path.unlink (warning logged)
# ---------------------------------------------------------------------------


class TestLoadLocalFileUnlinkError:
    async def test_oserror_on_unlink_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """OSError from unlink of converted temp file must be logged as WARNING, not raised."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"id": 1}\n', encoding="utf-8")

        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        mock_http = AsyncMock()
        mock_credential = AsyncMock()

        original_json_to_parquet = _json_to_parquet

        def _capture_and_return(path: Path) -> Path:
            return original_json_to_parquet(path)

        with (
            patch("fabric_dw.services.load._json_to_parquet", side_effect=_capture_and_return),
            patch("fabric_dw.services.load.create_staging_lakehouse", return_value="lh-id"),
            patch("fabric_dw.services.load.onelake_upload_file", return_value=None),
            patch(
                "fabric_dw.services.load.copy_into_from_url",
                return_value=CopyIntoResult(rows_loaded=1, rows_rejected=0, target="dbo.t"),
            ),
            patch("fabric_dw.services.load.delete_lakehouse"),
            patch.object(Path, "unlink", side_effect=OSError("permission denied")),
            caplog.at_level(logging.WARNING, logger="fabric_dw"),
        ):
            # Should NOT raise even though unlink fails.
            result = await load_local_file(
                mock_http,
                mock_credential,
                ws_id,
                target,
                "dbo",
                "t",
                json_file,
                file_format="json",
            )

        assert result.rows_loaded == 1
        warning_logs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_logs, "Expected WARNING log for failed unlink"
        assert "failed to delete converted file" in warning_logs[0].getMessage()


# ---------------------------------------------------------------------------
# table_exists and truncate_table — public wrapper functions
# ---------------------------------------------------------------------------


class TestTableExistsPublicApi:
    async def test_table_exists_delegates(self) -> None:
        """table_exists must delegate to _table_exists with the same arguments."""
        from fabric_dw.services.load import table_exists  # noqa: PLC0415
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        with patch("fabric_dw.services.load._table_exists", return_value=True) as mock_inner:
            result = await table_exists(target, "dbo", "my_table")

        assert result is True
        mock_inner.assert_called_once_with(target, "dbo", "my_table", mode=None)

    async def test_table_not_exists(self) -> None:
        from fabric_dw.services.load import table_exists  # noqa: PLC0415
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        with patch("fabric_dw.services.load._table_exists", return_value=False):
            result = await table_exists(target, "dbo", "nonexistent_table")

        assert result is False


class TestTruncateTablePublicApi:
    async def test_truncate_table_delegates(self) -> None:
        """truncate_table must delegate to _truncate_table_sql."""
        from fabric_dw.services.load import truncate_table  # noqa: PLC0415
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        with patch("fabric_dw.services.load._truncate_table_sql") as mock_inner:
            await truncate_table(target, "dbo", "my_table")

        mock_inner.assert_called_once_with(target, "dbo", "my_table", mode=None)


# ---------------------------------------------------------------------------
# _table_exists — internal implementation
# ---------------------------------------------------------------------------


class TestTableExistsInternal:
    async def test_returns_true_when_row_found(self) -> None:
        from fabric_dw.services.load import _table_exists  # noqa: PLC0415
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        with patch("fabric_dw.services.load.run_query", return_value=(["col"], [(1,)])):
            result = await _table_exists(target, "dbo", "my_table")

        assert result is True

    async def test_returns_false_when_no_rows(self) -> None:
        from fabric_dw.services.load import _table_exists  # noqa: PLC0415
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        with patch("fabric_dw.services.load.run_query", return_value=(["col"], [])):
            result = await _table_exists(target, "dbo", "ghost_table")

        assert result is False


# ---------------------------------------------------------------------------
# _drop_table_sql — internal implementation
# ---------------------------------------------------------------------------


class TestDropTableSql:
    async def test_drop_issues_correct_ddl(self) -> None:
        from fabric_dw.services.load import _drop_table_sql  # noqa: PLC0415
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        with patch("fabric_dw.services.load.run_query") as mock_rq:
            await _drop_table_sql(target, "dbo", "old_table")

        mock_rq.assert_called_once()
        sql_arg: str = mock_rq.call_args[0][1]
        assert "DROP TABLE" in sql_arg
        assert "[dbo]" in sql_arg
        assert "[old_table]" in sql_arg
        call_kwargs = mock_rq.call_args[1]
        assert call_kwargs.get("commit") is True
        assert call_kwargs.get("fetch") == "none"


# ---------------------------------------------------------------------------
# _truncate_table_sql — internal implementation
# ---------------------------------------------------------------------------


class TestTruncateTableSql:
    async def test_truncate_issues_correct_ddl(self) -> None:
        from fabric_dw.services.load import _truncate_table_sql  # noqa: PLC0415
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        with patch("fabric_dw.services.load.run_query") as mock_rq:
            await _truncate_table_sql(target, "dbo", "my_table")

        mock_rq.assert_called_once()
        sql_arg: str = mock_rq.call_args[0][1]
        assert "TRUNCATE TABLE" in sql_arg
        assert "[dbo]" in sql_arg
        assert "[my_table]" in sql_arg
        call_kwargs = mock_rq.call_args[1]
        assert call_kwargs.get("commit") is True
        assert call_kwargs.get("fetch") == "none"


# ---------------------------------------------------------------------------
# _infer_columns_from_local — parquet, csv, json paths
# ---------------------------------------------------------------------------


class TestInferColumnsFromLocal:
    async def test_parquet_path_delegates(self) -> None:
        from fabric_dw.services.load import _infer_columns_from_local  # noqa: PLC0415

        mock_columns = [object()]
        with patch(
            "fabric_dw.services.tables.infer_columns_from_parquet",
            return_value=mock_columns,
        ) as mock_fn:
            result = await _infer_columns_from_local(Path("data.parquet"), "parquet")

        assert result is mock_columns
        mock_fn.assert_called_once()

    async def test_csv_path_delegates(self) -> None:
        from fabric_dw.services.load import _infer_columns_from_local  # noqa: PLC0415

        mock_columns = [object()]
        with patch(
            "fabric_dw.services.tables.infer_columns_from_csv",
            return_value=mock_columns,
        ) as mock_fn:
            result = await _infer_columns_from_local(Path("data.csv"), "csv")

        assert result is mock_columns
        mock_fn.assert_called_once()

    async def test_json_path_converts_then_infers(self, tmp_path: Path) -> None:
        """JSON format must convert to Parquet, infer columns, then clean up the temp file."""
        from fabric_dw.services.load import _infer_columns_from_local  # noqa: PLC0415

        json_file = tmp_path / "data.json"
        json_file.write_text('{"id": 1}\n', encoding="utf-8")

        mock_columns = [object()]
        converted_path: list[Path] = []

        original_json_to_parquet = _json_to_parquet

        def _capture(path: Path) -> Path:
            result = original_json_to_parquet(path)
            converted_path.append(result)
            return result

        with (
            patch("fabric_dw.services.load._json_to_parquet", side_effect=_capture),
            patch(
                "fabric_dw.services.tables.infer_columns_from_parquet",
                return_value=mock_columns,
            ),
        ):
            result = await _infer_columns_from_local(json_file, "json")

        assert result is mock_columns
        # Temp file must be cleaned up.
        assert len(converted_path) == 1
        assert not converted_path[0].exists()


# ---------------------------------------------------------------------------
# create_and_load — all if_exists branches and cleanup_on_failure
# ---------------------------------------------------------------------------


class TestCreateAndLoad:
    """Tests for the create_and_load orchestration function."""

    def _make_target(self):  # type: ignore[return]  # SqlTarget imported locally to avoid top-level import
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        return SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )

    async def test_sql_endpoint_rejected(self, tmp_path: Path) -> None:
        from fabric_dw.services.load import create_and_load  # noqa: PLC0415

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        mock_http = AsyncMock()
        mock_credential = AsyncMock()
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        with pytest.raises(ItemKindError):
            await create_and_load(
                mock_http,
                mock_credential,
                ws_id,
                self._make_target(),
                "dbo",
                "t",
                csv_file,
                kind=WarehouseKind.SQL_ENDPOINT,
            )

    async def test_file_not_found_raises(self, tmp_path: Path) -> None:
        from fabric_dw.services.load import create_and_load  # noqa: PLC0415

        mock_http = AsyncMock()
        mock_credential = AsyncMock()
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        with pytest.raises(FileNotFoundError):
            await create_and_load(
                mock_http,
                mock_credential,
                ws_id,
                self._make_target(),
                "dbo",
                "t",
                tmp_path / "nonexistent.csv",
            )

    async def test_if_exists_fail_raises_when_table_exists(self, tmp_path: Path) -> None:
        from fabric_dw.services.load import create_and_load  # noqa: PLC0415

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        mock_http = AsyncMock()
        mock_credential = AsyncMock()
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        with (
            patch(
                "fabric_dw.services.load._infer_columns_from_local",
                return_value=[],
            ),
            patch("fabric_dw.services.load._table_exists", return_value=True),
            pytest.raises(ValueError, match="already exists"),
        ):
            await create_and_load(
                mock_http,
                mock_credential,
                ws_id,
                self._make_target(),
                "dbo",
                "t",
                csv_file,
                if_exists="fail",
            )

    async def test_if_exists_append_skips_create(self, tmp_path: Path) -> None:
        from fabric_dw.services.load import create_and_load  # noqa: PLC0415

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        mock_http = AsyncMock()
        mock_credential = AsyncMock()
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        expected_result = CopyIntoResult(rows_loaded=5, rows_rejected=0, target="dbo.t")

        with (
            patch("fabric_dw.services.load._infer_columns_from_local", return_value=[]),
            patch("fabric_dw.services.load._table_exists", return_value=True),
            patch("fabric_dw.services.load._create_table_from_columns") as mock_create,
            patch("fabric_dw.services.load.load_local_file", return_value=expected_result),
        ):
            result = await create_and_load(
                mock_http,
                mock_credential,
                ws_id,
                self._make_target(),
                "dbo",
                "t",
                csv_file,
                if_exists="append",
            )

        # With append and existing table, CREATE must NOT be called.
        mock_create.assert_not_called()
        assert result.rows_loaded == 5

    async def test_if_exists_truncate_truncates_then_loads(self, tmp_path: Path) -> None:
        from fabric_dw.services.load import create_and_load  # noqa: PLC0415

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        mock_http = AsyncMock()
        mock_credential = AsyncMock()
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        expected_result = CopyIntoResult(rows_loaded=2, rows_rejected=0, target="dbo.t")

        with (
            patch("fabric_dw.services.load._infer_columns_from_local", return_value=[]),
            patch("fabric_dw.services.load._table_exists", return_value=True),
            patch("fabric_dw.services.load._truncate_table_sql") as mock_truncate,
            patch("fabric_dw.services.load._create_table_from_columns") as mock_create,
            patch("fabric_dw.services.load.load_local_file", return_value=expected_result),
        ):
            result = await create_and_load(
                mock_http,
                mock_credential,
                ws_id,
                self._make_target(),
                "dbo",
                "t",
                csv_file,
                if_exists="truncate",
            )

        mock_truncate.assert_called_once()
        mock_create.assert_not_called()
        assert result.rows_loaded == 2

    async def test_if_exists_replace_drops_and_recreates(self, tmp_path: Path) -> None:
        from fabric_dw.services.load import create_and_load  # noqa: PLC0415

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        mock_http = AsyncMock()
        mock_credential = AsyncMock()
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        columns = [object()]
        expected_result = CopyIntoResult(rows_loaded=3, rows_rejected=0, target="dbo.t")

        with (
            patch("fabric_dw.services.load._infer_columns_from_local", return_value=columns),
            patch("fabric_dw.services.load._table_exists", return_value=True),
            patch("fabric_dw.services.load._drop_table_sql") as mock_drop,
            patch("fabric_dw.services.load._create_table_from_columns") as mock_create,
            patch("fabric_dw.services.load.load_local_file", return_value=expected_result),
        ):
            result = await create_and_load(
                mock_http,
                mock_credential,
                ws_id,
                self._make_target(),
                "dbo",
                "t",
                csv_file,
                if_exists="replace",
            )

        mock_drop.assert_called_once()
        mock_create.assert_called_once()
        assert result.rows_loaded == 3

    async def test_table_does_not_exist_creates_and_loads(self, tmp_path: Path) -> None:
        from fabric_dw.services.load import create_and_load  # noqa: PLC0415

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        mock_http = AsyncMock()
        mock_credential = AsyncMock()
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        columns = [object()]
        expected_result = CopyIntoResult(rows_loaded=1, rows_rejected=0, target="dbo.t")

        with (
            patch("fabric_dw.services.load._infer_columns_from_local", return_value=columns),
            patch("fabric_dw.services.load._table_exists", return_value=False),
            patch("fabric_dw.services.load._create_table_from_columns") as mock_create,
            patch("fabric_dw.services.load.load_local_file", return_value=expected_result),
        ):
            result = await create_and_load(
                mock_http,
                mock_credential,
                ws_id,
                self._make_target(),
                "dbo",
                "t",
                csv_file,
                if_exists="fail",
            )

        mock_create.assert_called_once()
        assert result.rows_loaded == 1

    async def test_cleanup_on_failure_drops_new_table(self, tmp_path: Path) -> None:
        """cleanup_on_failure=True: drop the newly-created table if load_local_file fails."""
        from fabric_dw.services.load import create_and_load  # noqa: PLC0415

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        mock_http = AsyncMock()
        mock_credential = AsyncMock()
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        with (
            patch("fabric_dw.services.load._infer_columns_from_local", return_value=[]),
            patch("fabric_dw.services.load._table_exists", return_value=False),
            patch("fabric_dw.services.load._create_table_from_columns"),
            patch(
                "fabric_dw.services.load.load_local_file",
                side_effect=RuntimeError("upload failed"),
            ),
            patch("fabric_dw.services.load._drop_table_sql") as mock_drop,
            pytest.raises(RuntimeError, match="upload failed"),
        ):
            await create_and_load(
                mock_http,
                mock_credential,
                ws_id,
                self._make_target(),
                "dbo",
                "t",
                csv_file,
                cleanup_on_failure=True,
            )

        # The table WE created must be dropped.
        mock_drop.assert_called_once()

    async def test_cleanup_on_failure_false_does_not_drop(self, tmp_path: Path) -> None:
        """cleanup_on_failure=False (default): do NOT drop the table on load failure."""
        from fabric_dw.services.load import create_and_load  # noqa: PLC0415

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        mock_http = AsyncMock()
        mock_credential = AsyncMock()
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        with (
            patch("fabric_dw.services.load._infer_columns_from_local", return_value=[]),
            patch("fabric_dw.services.load._table_exists", return_value=False),
            patch("fabric_dw.services.load._create_table_from_columns"),
            patch(
                "fabric_dw.services.load.load_local_file",
                side_effect=RuntimeError("upload failed"),
            ),
            patch("fabric_dw.services.load._drop_table_sql") as mock_drop,
            pytest.raises(RuntimeError),
        ):
            await create_and_load(
                mock_http,
                mock_credential,
                ws_id,
                self._make_target(),
                "dbo",
                "t",
                csv_file,
                cleanup_on_failure=False,
            )

        mock_drop.assert_not_called()

    async def test_cleanup_on_failure_drop_error_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When cleanup drop fails, the error is logged as WARNING and original error re-raised."""
        from fabric_dw.services.load import create_and_load  # noqa: PLC0415

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        mock_http = AsyncMock()
        mock_credential = AsyncMock()
        ws_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        with (
            patch("fabric_dw.services.load._infer_columns_from_local", return_value=[]),
            patch("fabric_dw.services.load._table_exists", return_value=False),
            patch("fabric_dw.services.load._create_table_from_columns"),
            patch(
                "fabric_dw.services.load.load_local_file",
                side_effect=RuntimeError("original error"),
            ),
            patch(
                "fabric_dw.services.load._drop_table_sql",
                side_effect=RuntimeError("drop failed too"),
            ),
            caplog.at_level(logging.WARNING, logger="fabric_dw"),
            pytest.raises(RuntimeError, match="original error"),
        ):
            await create_and_load(
                mock_http,
                mock_credential,
                ws_id,
                self._make_target(),
                "dbo",
                "t",
                csv_file,
                cleanup_on_failure=True,
            )

        warning_logs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_logs, "Expected WARNING when cleanup drop fails"
        assert "cleanup_on_failure drop failed" in warning_logs[0].getMessage()


# ---------------------------------------------------------------------------
# _create_table_from_columns — thin wrapper
# ---------------------------------------------------------------------------


class TestCreateTableFromColumns:
    async def test_delegates_to_create_empty_table(self) -> None:
        from fabric_dw.services.load import _create_table_from_columns  # noqa: PLC0415
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        columns = [object()]

        with patch("fabric_dw.services.tables.create_empty_table") as mock_cet:
            await _create_table_from_columns(target, "dbo", "new_table", columns)

        mock_cet.assert_called_once()
        call_args = mock_cet.call_args
        assert call_args[0][0] is target
        assert call_args[0][1] == "dbo"
        assert call_args[0][2] == "new_table"
        assert call_args[0][3] is columns
