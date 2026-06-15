"""Load data into a Fabric Data Warehouse table via ``COPY INTO``.

Supports two loading paths:

Local files
    1. Create a temporary staging Lakehouse in the target workspace.
    2. Upload the local file to the Lakehouse ``Files/`` area via the OneLake
       ADLS Gen2 DFS API (chunked create / append / flush).
    3. ``COPY INTO`` the target table from the staged OneLake URL using the
       caller's Entra identity (no ``CREDENTIAL`` needed for same-tenant).
    4. Drop the temporary staging Lakehouse in a ``finally`` block.

Remote URLs
    ``COPY INTO`` directly from the given URL.  For OneLake / public URLs no
    credential is emitted; for secured external URLs credential options are
    supported (SAS / Managed Identity / Service Principal / Account Key).

File formats
    ``CSV`` and ``Parquet`` are loaded directly (``FILE_TYPE='CSV'|'PARQUET'``).
    ``JSON`` is converted client-side to Parquet via ``pyarrow`` before staging.

SQL safety
    The target identifier is bracket-quoted; ``FROM`` URL and ``WITH`` option
    values are string literals with single-quotes escaped (``''``).  The
    ``FILE_TYPE`` is validated against an explicit allowlist.  Secrets are
    never logged.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Literal

import httpx
from azure.core.credentials_async import AsyncTokenCredential

from fabric_dw.auth import STORAGE_SCOPE
from fabric_dw.exceptions import FabricServerError, ItemKindError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.identifiers import quote_identifier, validate_identifier
from fabric_dw.models import CopyIntoResult, WarehouseKind
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "CopyIntoCredentialType",
    "CopyIntoCsvOptions",
    "copy_into_from_url",
    "create_staging_lakehouse",
    "delete_lakehouse",
    "load_local_file",
    "onelake_upload_file",
]

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: OneLake DFS endpoint used for ADLS Gen2 file operations.
_ONELAKE_DFS_BASE = "https://onelake.dfs.fabric.microsoft.com"

#: Supported FILE_TYPE values for COPY INTO (allowlist).
_VALID_FILE_TYPES: frozenset[str] = frozenset({"CSV", "PARQUET"})

#: Upload chunk size for DFS append (4 MiB).
_UPLOAD_CHUNK_SIZE: int = 4 * 1024 * 1024

# SQL Endpoint rejection message.
_SQL_ENDPOINT_READONLY_MSG = "SQL Endpoints are read-only; COPY INTO not supported"

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

CopyIntoCredentialType = Literal[
    "none", "sas", "managed-identity", "service-principal", "account-key"
]

FileFormat = Literal["csv", "json", "parquet"]


class CopyIntoCsvOptions:
    """Options for CSV-format ``COPY INTO`` loads.

    All fields are optional; omitted fields are not included in the WITH clause.
    """

    def __init__(
        self,
        *,
        delimiter: str | None = None,
        first_row: int | None = None,  # 1 = has header, 2 = skip header + start from row 2
        encoding: str | None = None,
        field_quote: str | None = None,
        row_terminator: str | None = None,
    ) -> None:
        self.delimiter = delimiter
        self.first_row = first_row
        self.encoding = encoding
        self.field_quote = field_quote
        self.row_terminator = row_terminator


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


def _assert_not_sql_endpoint(kind: WarehouseKind) -> None:
    if kind == WarehouseKind.SQL_ENDPOINT:
        raise ItemKindError(_SQL_ENDPOINT_READONLY_MSG)


# ---------------------------------------------------------------------------
# SQL builder helpers
# ---------------------------------------------------------------------------


def _sq(s: str) -> str:
    """Escape a string for embedding inside a SQL single-quoted literal."""
    return s.replace("'", "''")


def _build_credential_clause(
    credential_type: CopyIntoCredentialType,
    secret: str | None,
    identity: str | None,
) -> str | None:
    """Build the CREDENTIAL = (…) clause for COPY INTO.

    Returns ``None`` when no clause should be emitted (e.g. no secret provided).
    """
    if credential_type == "sas" and secret:
        return f"CREDENTIAL = (IDENTITY = 'Shared Access Signature', SECRET = '{_sq(secret)}')"
    if credential_type == "managed-identity":
        return "CREDENTIAL = (IDENTITY = 'Managed Identity')"
    if credential_type == "service-principal" and identity:
        return f"CREDENTIAL = (IDENTITY = '{_sq(identity)}', SECRET = '{_sq(secret or '')}')"
    if credential_type == "account-key" and secret:
        return f"CREDENTIAL = (IDENTITY = 'Storage Account Key', SECRET = '{_sq(secret)}')"
    return None


def _build_copy_into_sql(
    schema: str,
    table: str,
    url: str,
    file_type: str,
    *,
    credential_type: CopyIntoCredentialType = "none",
    secret: str | None = None,
    identity: str | None = None,
    csv_options: CopyIntoCsvOptions | None = None,
    max_errors: int | None = None,
    rejected_row_location: str | None = None,
) -> str:
    """Build a ``COPY INTO`` statement.

    Args:
        schema: Validated SQL schema name.
        table: Validated SQL table name.
        url: Source URL (OneLake or external).  Single-quotes are escaped.
        file_type: ``'CSV'`` or ``'PARQUET'``.  Must be in ``_VALID_FILE_TYPES``.
        credential_type: One of ``"none"``, ``"sas"``, ``"managed-identity"``,
            ``"service-principal"``, or ``"account-key"``.
        secret: Credential secret value (SAS token / client secret / account key).
            Never logged.
        identity: Identity value for managed-identity or service-principal.
        csv_options: Optional CSV loading options.
        max_errors: Maximum number of errors before aborting.
        rejected_row_location: URL for rejected-row output.

    Returns:
        A ``COPY INTO [schema].[table] FROM 'url' WITH (…)`` statement string.

    Raises:
        ValueError: If *file_type* is not in ``_VALID_FILE_TYPES``.
    """
    if file_type not in _VALID_FILE_TYPES:
        msg = f"Unsupported FILE_TYPE {file_type!r}; allowed: {sorted(_VALID_FILE_TYPES)}"
        raise ValueError(msg)

    target_q = f"{quote_identifier(schema)}.{quote_identifier(table)}"
    url_lit = f"'{_sq(url)}'"

    with_parts: list[str] = [f"FILE_TYPE = '{file_type}'"]

    # CSV-specific options.
    if file_type == "CSV" and csv_options is not None:
        if csv_options.delimiter is not None:
            with_parts.append(f"FIELDTERMINATOR = '{_sq(csv_options.delimiter)}'")
        if csv_options.first_row is not None:
            with_parts.append(f"FIRSTROW = {int(csv_options.first_row)}")
        if csv_options.encoding is not None:
            with_parts.append(f"ENCODING = '{_sq(csv_options.encoding)}'")
        if csv_options.field_quote is not None:
            with_parts.append(f"FIELDQUOTE = '{_sq(csv_options.field_quote)}'")
        if csv_options.row_terminator is not None:
            with_parts.append(f"ROWTERMINATOR = '{_sq(csv_options.row_terminator)}'")

    # Credential clause (only for secured external URLs).
    if credential_type != "none":
        cred = _build_credential_clause(credential_type, secret, identity)
        if cred:
            with_parts.append(cred)

    if max_errors is not None:
        with_parts.append(f"MAXERRORS = {int(max_errors)}")

    if rejected_row_location is not None:
        with_parts.append(f"REJECTED_ROW_LOCATION = '{_sq(rejected_row_location)}'")

    with_clause = ",\n    ".join(with_parts)
    return f"COPY INTO {target_q}\nFROM {url_lit}\nWITH (\n    {with_clause}\n);"


# ---------------------------------------------------------------------------
# JSON to Parquet conversion
# ---------------------------------------------------------------------------


def _json_to_parquet(local_path: Path) -> Path:
    """Convert a JSON file to Parquet using PyArrow.

    Args:
        local_path: Path to the input JSON file (newline-delimited or JSON array).

    Returns:
        Path to the temporary Parquet file (caller must delete it when done).

    Raises:
        ImportError: If PyArrow is not installed.
        Exception: If conversion fails.
    """
    import pyarrow.json as paj  # noqa: PLC0415
    import pyarrow.parquet as pap  # noqa: PLC0415

    suffix = f".{uuid.uuid4().hex[:8]}.parquet"
    out_path = local_path.with_suffix(suffix)
    table = paj.read_json(local_path)
    pap.write_table(table, out_path)
    _logger.debug("converted JSON %s -> Parquet %s (%d rows)", local_path, out_path, table.num_rows)
    return out_path


# ---------------------------------------------------------------------------
# Format inference
# ---------------------------------------------------------------------------


def infer_file_format(path: Path) -> FileFormat:
    """Infer the file format from *path*'s extension.

    Args:
        path: File path.

    Returns:
        One of ``"csv"``, ``"json"``, or ``"parquet"``.

    Raises:
        ValueError: If the extension is not recognised.
    """
    ext = path.suffix.lower()
    _ext_map: dict[str, FileFormat] = {
        ".csv": "csv",
        ".json": "json",
        ".parquet": "parquet",
        ".pq": "parquet",
    }
    fmt = _ext_map.get(ext)
    if fmt is None:
        msg = f"Cannot infer file format from extension {ext!r}; pass --format explicitly"
        raise ValueError(msg)
    return fmt


# ---------------------------------------------------------------------------
# OneLake staging helpers
# ---------------------------------------------------------------------------


async def create_staging_lakehouse(
    http: FabricHttpClient,
    workspace_id: uuid.UUID,
    name: str,
) -> str:
    """Create a staging Lakehouse and return its item ID as a string.

    Handles both the 202 LRO path and the synchronous 201 path.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: Workspace in which to create the Lakehouse.
        name: Display name for the new Lakehouse.

    Returns:
        The Lakehouse item ID as a string (UUID hex with dashes).

    Raises:
        FabricServerError: If the LRO completes without a usable item ID.
    """
    body: dict[str, object] = {
        "displayName": name,
        "description": "Temporary staging lakehouse — auto-deleted after COPY INTO",
    }
    _logger.debug(
        "create_staging_lakehouse: POST /workspaces/%s/lakehouses name=%r",
        workspace_id,
        name,
    )
    resp = await http.request(
        "POST", HttpBase.FABRIC, f"/workspaces/{workspace_id}/lakehouses", json=body
    )

    location = resp.headers.get("Location")
    if location:
        lro_result = await http.poll_operation(location)
        # Try resource-specific keys first (Path A)
        for key in ("resourceId", "createdItemId", "itemId"):
            raw = lro_result.get(key)
            if raw:
                return str(raw)
        # Path B: GET /operations/{id}/result
        op_id = location.rsplit("/", 1)[-1]
        result_resp = await http.request("GET", HttpBase.FABRIC, f"/operations/{op_id}/result")
        result_body = result_resp.json()
        item_id = result_body.get("id")
        if item_id:
            return str(item_id)
        msg = f"create_staging_lakehouse LRO completed but no item ID: {lro_result}"
        raise FabricServerError(msg)

    # 201 path: body contains the new item directly
    body_resp = resp.json()
    item_id = body_resp.get("id")
    if not item_id:
        msg = f"create_staging_lakehouse returned 201 but no id in body: {body_resp}"
        raise FabricServerError(msg)
    return str(item_id)


async def delete_lakehouse(
    http: FabricHttpClient,
    workspace_id: uuid.UUID,
    lakehouse_id: str,
) -> None:
    """Delete a Lakehouse by ID.

    Suppresses 404 errors (already deleted).

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: Workspace UUID.
        lakehouse_id: Lakehouse item UUID string.
    """
    from fabric_dw.exceptions import NotFoundError  # noqa: PLC0415 — avoid circular import

    _logger.debug(
        "delete_lakehouse: DELETE /workspaces/%s/lakehouses/%s",
        workspace_id,
        lakehouse_id,
    )
    try:
        await http.request(
            "DELETE", HttpBase.FABRIC, f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}"
        )
    except NotFoundError:
        _logger.debug("delete_lakehouse: already gone (404) for %s", lakehouse_id)


async def onelake_upload_file(
    credential: AsyncTokenCredential,
    workspace_id: uuid.UUID,
    lakehouse_id: str,
    dest_path: str,
    local_path: Path,
    *,
    chunk_size: int = _UPLOAD_CHUNK_SIZE,
) -> None:
    """Upload a local file to the OneLake DFS API using chunked create/append/flush.

    Uses the ADLS Gen2 DFS protocol:
    - PUT ``?resource=file``      — creates the file (0 bytes).
    - PATCH ``?action=append``    — appends data chunks.
    - PATCH ``?action=flush``     — commits the file at final position.

    Args:
        credential: An async Azure credential for the storage scope.
        workspace_id: Workspace UUID.
        lakehouse_id: Lakehouse item UUID string.
        dest_path: Destination path within the Lakehouse ``Files/`` area
            (e.g. ``"staging.parquet"``).
        local_path: Path to the local file to upload.
        chunk_size: Bytes per append chunk (default 4 MiB).

    Raises:
        httpx.HTTPStatusError: On non-2xx DFS responses.
    """
    # Fetch a storage-scoped token.
    token_obj = await credential.get_token(STORAGE_SCOPE)
    token = token_obj.token
    headers = {"Authorization": f"Bearer {token}"}

    dfs_base = f"{_ONELAKE_DFS_BASE}/{workspace_id}/{lakehouse_id}.Lakehouse/Files/{dest_path}"

    file_size = local_path.stat().st_size
    _logger.debug(
        "onelake_upload_file: %s -> %s (size=%d bytes, chunk=%d)",
        local_path,
        dfs_base,
        file_size,
        chunk_size,
    )

    async with httpx.AsyncClient(timeout=300.0) as client:
        # Step 1: create an empty file.
        create_resp = await client.put(
            dfs_base,
            params={"resource": "file"},
            headers=headers,
        )
        create_resp.raise_for_status()

        # Step 2: append chunks.
        offset = 0
        with local_path.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                append_resp = await client.patch(
                    dfs_base,
                    params={"action": "append", "position": offset},
                    content=chunk,
                    headers={**headers, "Content-Type": "application/octet-stream"},
                )
                append_resp.raise_for_status()
                offset += len(chunk)

        # Step 3: flush (commit).
        flush_resp = await client.patch(
            dfs_base,
            params={"action": "flush", "position": offset},
            headers=headers,
        )
        flush_resp.raise_for_status()

    _logger.debug("onelake_upload_file: upload complete, %d bytes written", offset)


# ---------------------------------------------------------------------------
# Core COPY INTO operation
# ---------------------------------------------------------------------------


async def copy_into_from_url(
    target: SqlTarget,
    schema: str,
    table: str,
    url: str,
    *,
    file_type: str,
    credential_type: CopyIntoCredentialType = "none",
    secret: str | None = None,
    identity: str | None = None,
    csv_options: CopyIntoCsvOptions | None = None,
    max_errors: int | None = None,
    rejected_row_location: str | None = None,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: object = None,
) -> CopyIntoResult:
    """Run ``COPY INTO`` from *url* into ``[schema].[table]``.

    This is the remote-URL path; no local staging is performed.

    Args:
        target: SQL connection target (warehouse connection string).
        schema: Validated SQL schema name.
        table: Validated SQL table name.
        url: Source URL.  For OneLake or public URLs pass ``credential_type="none"``.
        file_type: ``'CSV'`` or ``'PARQUET'``.
        credential_type: Credential kind for secured external URLs.
        secret: Secret value (SAS token / client secret / account key).
        identity: Identity for managed-identity or service-principal credentials.
        csv_options: Optional CSV loading options.
        max_errors: Maximum errors before abort.
        rejected_row_location: URL for rejected row output.
        kind: Warehouse item kind.  SQL Endpoint items are rejected.
        mode: Credential mode for SQL authentication.

    Returns:
        A :class:`~fabric_dw.models.CopyIntoResult`.

    Raises:
        ItemKindError: If *kind* is SQL_ENDPOINT.
        ValueError: If *file_type* is not supported.
    """
    from fabric_dw.auth import CredentialMode  # noqa: PLC0415

    _assert_not_sql_endpoint(kind)
    validate_identifier(schema)
    validate_identifier(table)

    sql = _build_copy_into_sql(
        schema,
        table,
        url,
        file_type,
        credential_type=credential_type,
        secret=secret,
        identity=identity,
        csv_options=csv_options,
        max_errors=max_errors,
        rejected_row_location=rejected_row_location,
    )

    _logger.debug("copy_into_from_url: schema=%s table=%s file_type=%s", schema, table, file_type)

    def _run() -> tuple[int, int]:
        # COPY INTO returns a result set with (rows_loaded, rows_rejected, …)

        _mode = mode if isinstance(mode, CredentialMode) else CredentialMode.DEFAULT
        cols, rows = run_query(target, sql, mode=_mode, commit=True)
        if rows:
            row = rows[0]
            # The result set columns vary slightly by Fabric version.
            # Columns: rows_loaded, rows_rejected [, rejected_file_location]
            try:
                col_map = {c.lower(): i for i, c in enumerate(cols)}
                loaded = int(row[col_map.get("rows_loaded", 0)] or 0)
                rejected_idx = col_map.get("rows_rejected")
                rejected = int(row[rejected_idx] or 0) if rejected_idx is not None else 0
            except (IndexError, TypeError, ValueError):
                loaded = int(row[0] or 0) if row else 0
                rejected = 0
            return loaded, rejected
        return 0, 0

    rows_loaded, rows_rejected = await asyncio.to_thread(_run)
    return CopyIntoResult(
        rows_loaded=rows_loaded,
        rows_rejected=rows_rejected,
        target=f"{schema}.{table}",
    )


# ---------------------------------------------------------------------------
# Local-file orchestration
# ---------------------------------------------------------------------------


async def load_local_file(
    http: FabricHttpClient,
    credential: AsyncTokenCredential,
    workspace_id: uuid.UUID,
    target: SqlTarget,
    schema: str,
    table: str,
    local_path: Path,
    *,
    file_format: FileFormat | None = None,
    staging_lakehouse_name: str | None = None,
    keep_staging: bool = False,
    csv_options: CopyIntoCsvOptions | None = None,
    max_errors: int | None = None,
    rejected_row_location: str | None = None,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: object = None,
) -> CopyIntoResult:
    """Load a local file into a Data Warehouse table via a temporary staging Lakehouse.

    Flow
    ----
    1. Infer the file format from the extension (unless *file_format* is given).
    2. If format is JSON, convert to Parquet client-side before upload.
    3. Create a temporary staging Lakehouse in *workspace_id*.
    4. Upload the (possibly converted) file to ``Files/`` on the Lakehouse
       using the OneLake DFS API with a storage-scoped token.
    5. Run ``COPY INTO`` from the staged OneLake URL.
    6. Drop the staging Lakehouse in a ``finally`` block (always, unless
       *keep_staging* is ``True``).

    Args:
        http: Authenticated Fabric HTTP client.
        credential: Azure credential (used to fetch a storage-scoped token).
        workspace_id: Workspace in which to stage and load.
        target: SQL connection target (warehouse connection string).
        schema: Validated SQL schema name.
        table: Validated SQL table name.
        local_path: Path to the local file on disk.
        file_format: Explicit format; inferred from extension when ``None``.
        staging_lakehouse_name: Explicit staging Lakehouse name; auto-generated
            when ``None`` (``staging_<uuid>``).
        keep_staging: When ``True``, do NOT drop the Lakehouse after loading.
            Use as an escape hatch for debugging.
        csv_options: CSV loading options (only used when file_format is ``"csv"``).
        max_errors: Maximum errors before aborting the load.
        rejected_row_location: URL for rejected-row output.
        kind: Warehouse item kind.  SQL Endpoint items are rejected.
        mode: Credential mode for SQL authentication.

    Returns:
        A :class:`~fabric_dw.models.CopyIntoResult`.

    Raises:
        ItemKindError: If *kind* is SQL_ENDPOINT.
        ValueError: If file format cannot be inferred or is unsupported.
        FileNotFoundError: If *local_path* does not exist.
    """
    _assert_not_sql_endpoint(kind)
    validate_identifier(schema)
    validate_identifier(table)

    if not local_path.exists():
        msg = f"Local file not found: {local_path}"
        raise FileNotFoundError(msg)

    # Step 1: determine format.
    fmt = file_format or infer_file_format(local_path)

    # Step 2: JSON → Parquet conversion.
    converted_path: Path | None = None
    upload_path = local_path
    file_type: str
    if fmt == "json":
        _logger.info("load_local_file: converting JSON to Parquet: %s", local_path)
        converted_path = await asyncio.to_thread(_json_to_parquet, local_path)
        upload_path = converted_path
        file_type = "PARQUET"
        dest_filename = upload_path.name
    elif fmt == "parquet":
        file_type = "PARQUET"
        dest_filename = local_path.name
    else:
        file_type = "CSV"
        dest_filename = local_path.name

    # Step 3: create staging Lakehouse.
    lh_name = staging_lakehouse_name or f"staging_{uuid.uuid4().hex[:12]}"
    _logger.info(
        "load_local_file: creating staging Lakehouse %r in workspace %s",
        lh_name,
        workspace_id,
    )
    lakehouse_id = await create_staging_lakehouse(http, workspace_id, lh_name)

    try:
        # Step 4: upload file.
        _logger.info(
            "load_local_file: uploading %s to OneLake Lakehouse %s", upload_path, lakehouse_id
        )
        await onelake_upload_file(
            credential,
            workspace_id,
            lakehouse_id,
            dest_filename,
            upload_path,
        )

        # Step 5: COPY INTO from the OneLake URL.
        # Same-tenant OneLake → caller's Entra identity; no CREDENTIAL needed.
        onelake_url = (
            f"https://onelake.dfs.fabric.microsoft.com"
            f"/{workspace_id}/{lakehouse_id}.Lakehouse/Files/{dest_filename}"
        )
        result = await copy_into_from_url(
            target,
            schema,
            table,
            onelake_url,
            file_type=file_type,
            credential_type="none",
            csv_options=csv_options if file_type == "CSV" else None,
            max_errors=max_errors,
            rejected_row_location=rejected_row_location,
            kind=kind,
            mode=mode,
        )
        _logger.info(
            "load_local_file: loaded %d rows into %s.%s (rejected=%d)",
            result.rows_loaded,
            schema,
            table,
            result.rows_rejected,
        )
        return result
    finally:
        # Step 6: cleanup — always drop the staging Lakehouse unless asked to keep it.
        if converted_path is not None:
            try:
                converted_path.unlink(missing_ok=True)
            except OSError as exc:
                _logger.warning(
                    "load_local_file: failed to delete converted file %s: %s",
                    converted_path,
                    exc,
                )

        if not keep_staging:
            _logger.info("load_local_file: deleting staging Lakehouse %s", lakehouse_id)
            await delete_lakehouse(http, workspace_id, lakehouse_id)
        else:
            _logger.info(
                "load_local_file: --keep-staging set; leaving Lakehouse %s (%s) in place",
                lh_name,
                lakehouse_id,
            )
