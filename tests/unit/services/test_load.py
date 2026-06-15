"""Unit tests for fabric_dw.services.load."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from fabric_dw.exceptions import ItemKindError
from fabric_dw.models import CopyIntoResult, WarehouseKind
from fabric_dw.services.load import (
    CopyIntoCsvOptions,
    _build_copy_into_sql,
    _json_to_parquet,
    _sq,
    copy_into_from_url,
    infer_file_format,
    load_local_file,
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
            "https://onelake.dfs.fabric.microsoft.com/ws/lh.Lakehouse/Files/f.parquet",
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

    def test_max_errors_option(self) -> None:
        sql = _build_copy_into_sql(
            "dbo", "t", "https://example.com/f.parquet", "PARQUET", max_errors=10
        )
        assert "MAXERRORS = 10" in sql

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
        pytest.importorskip("pyarrow")

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
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )

        with patch("fabric_dw.services.load.run_query") as mock_run:
            mock_run.return_value = (
                ["rows_loaded", "rows_rejected"],
                [(100, 2)],
            )
            result = await copy_into_from_url(
                target,
                "dbo",
                "sales",
                "https://example.com/f.parquet",
                file_type="PARQUET",
            )

        assert isinstance(result, CopyIntoResult)
        assert result.rows_loaded == 100
        assert result.rows_rejected == 2
        assert result.target == "dbo.sales"

    async def test_secret_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Secret values must never appear in log output."""
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws-id", database="db", connection_string="server=x;database=y"
        )
        secret = "super-secret-sas-token-xyz123"  # noqa: S105

        with (
            caplog.at_level(logging.DEBUG, logger="fabric_dw"),
            patch("fabric_dw.services.load.run_query") as mock_run,
        ):
            mock_run.return_value = (["rows_loaded"], [(5,)])
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
        pytest.importorskip("pyarrow")

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
