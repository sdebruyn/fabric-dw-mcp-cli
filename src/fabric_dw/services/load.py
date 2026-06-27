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
import re
import urllib.parse
import uuid
from pathlib import Path
from typing import Literal

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import ClientAuthenticationError

from fabric_dw.auth import STORAGE_SCOPE
from fabric_dw.exceptions import (
    FabricError,
    FabricServerError,
    auth_error_from_credential_exc,
)
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.identifiers import quote_identifier, validate_identifier
from fabric_dw.models import CopyIntoResult, WarehouseKind
from fabric_dw.services._helpers import _assert_not_sql_endpoint
from fabric_dw.sql import SqlTarget, run_query, run_statements

__all__ = [
    "CopyIntoCredentialType",
    "CopyIntoCsvOptions",
    "IfExistsPolicy",
    "copy_into_from_url",
    "create_and_load",
    "create_staging_lakehouse",
    "delete_lakehouse",
    "infer_file_format",
    "load_local_file",
    "onelake_upload_file",
    "table_exists",
    "truncate_table",
]

IfExistsPolicy = Literal["fail", "append", "truncate", "replace"]

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

#: Maximum local file size before staging upload is rejected (2 GiB).
_MAX_STAGING_FILE_BYTES: int = 2 * 1024 * 1024 * 1024

#: ADLS Gen2 / OneLake DFS API version sent on every request.
#:
#: The OneLake / ADLS Gen2 DFS Path API documentation shows this value in sample responses.
#: Sending an explicit version header pins the server to a known, supported protocol version
#: and avoids the ``UnsupportedRestVersion`` 400 that can occur when the service falls back
#: to a very old default.  This value was chosen because it appears in the OneLake
#: documentation examples and is consistent with the ADLS Gen2 service version that supports
#: the create/append/flush workflow used here.
_DFS_API_VERSION = "2021-06-08"

#: Maximum number of retries for the DFS create-file PUT on transient failures.
#: A newly-provisioned Lakehouse may briefly return 400 before its OneLake storage backend
#: is fully ready, even after the Fabric LRO has completed.
_DFS_CREATE_MAX_RETRIES: int = 3

#: Base delay (seconds) between DFS create-file retries.  Each subsequent attempt waits
#: an additional ``_DFS_CREATE_RETRY_DELAY`` seconds (i.e. 2 s, 4 s, 6 s …) — an
#: arithmetic ramp, not a flat delay.
_DFS_CREATE_RETRY_DELAY: float = 2.0

#: HTTP status codes that are considered transient and safe to retry on the create PUT.
#: Only these statuses trigger a retry; all others (including 401/403/404/409) surface
#: immediately so the caller gets actionable feedback rather than silent retries.
#: - 400: may indicate a provisioning-lag ``InvalidUri`` / ``UnsupportedRestVersion``.
#: - 408: Request Timeout.
#: - 429: Too Many Requests (throttling).
#: - 5xx: server-side errors (502/503/504 are common transient gateway failures).
_DFS_CREATE_RETRYABLE_STATUSES: frozenset[int] = frozenset({400, 408, 429, 500, 502, 503, 504})

#: Staging lakehouse name: same alphabet as SQL identifier but allow hyphens.
_STAGING_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,127}$")

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


def _validate_staging_name(name: str) -> str:
    """Validate a staging Lakehouse display name.

    Accepts letters, digits, underscores, and hyphens (max 128 chars).
    Rejects newlines, null bytes, and other control characters to prevent
    log injection.

    Args:
        name: The raw staging Lakehouse name supplied by the caller.

    Returns:
        *name* unchanged if valid.

    Raises:
        ValueError: If *name* contains forbidden characters or is empty.
    """
    if not name:
        msg = "staging_lakehouse_name must not be empty"
        raise ValueError(msg)
    if any(c in name for c in ("\n", "\r", "\0", "\t")):
        msg = f"staging_lakehouse_name {name!r}: control characters (newlines etc.) are not allowed"
        raise ValueError(msg)
    if not _STAGING_NAME_RE.match(name):
        msg = (
            f"staging_lakehouse_name {name!r}: must start with a letter or underscore "
            "and contain only letters, digits, underscores, or hyphens (max 128 chars)"
        )
        raise ValueError(msg)
    return name


def _safe_dest_filename(local_path: Path) -> str:
    """Return a safe DFS destination filename from *local_path*.

    Percent-decodes the filename (handles URL-encoded slashes / path separators)
    and then strips any directory component to guarantee only a bare filename is
    embedded in the DFS path and the SQL statement.

    Args:
        local_path: The original local file path.

    Returns:
        A bare filename with no path separators and percent-encoding decoded.
    """
    raw_name = local_path.name
    decoded = urllib.parse.unquote(raw_name)
    # Strip any directory separators that might have been encoded as %2F / %5C.
    return Path(decoded).name or raw_name


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

    # MAXERRORS is only valid for CSV; Fabric rejects it for PARQUET with
    # "Option 'MAXERRORS' is not supported for specified format 'PARQUET'".
    if max_errors is not None and file_type == "CSV":
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


def _log_dfs_error(resp: httpx.Response, context: str) -> None:
    """Log the DFS error code and body from a non-2xx response.

    Extracts the ``x-ms-error-code`` response header and the JSON error body
    (which contains the ADLS Gen2 error code and message) and logs them at
    ERROR level.  The Authorization token is never read or logged — only the
    response data is used.

    Args:
        resp: The non-2xx :class:`httpx.Response`.
        context: A short label for the log message (e.g. ``"DFS create"``).
    """
    error_code = resp.headers.get("x-ms-error-code", "<none>")
    request_id = resp.headers.get("x-ms-request-id", "<none>")
    try:
        body_text = resp.text
    except Exception as exc:
        _logger.warning("_log_dfs_error: could not read response body: %s", exc)
        body_text = "<unreadable>"

    _logger.error(
        "%s failed: HTTP %s  x-ms-error-code=%s  x-ms-request-id=%s  body=%s",
        context,
        resp.status_code,
        error_code,
        request_id,
        body_text,
    )


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
    - PUT  ``?resource=file``              — creates the file (0 bytes).
    - PATCH ``?action=append&position=N`` — appends data chunks.
    - PATCH ``?action=flush&position=N``  — commits the file at the final offset.

    All requests include ``x-ms-version: 2021-06-08`` (the earliest version
    confirmed to work with OneLake DFS) and ``Content-Length`` as required by
    the ADLS Gen2 Path API.  Non-2xx responses log the ``x-ms-error-code``
    header and the JSON error body *before* raising, so the next integration
    run will surface the exact server-side error code without exposing auth
    tokens.

    Args:
        credential: An async Azure credential for the storage scope.
        workspace_id: Workspace UUID.
        lakehouse_id: Lakehouse item UUID string.
        dest_path: Destination path within the Lakehouse ``Files/`` area
            (e.g. ``"staging.parquet"``).
        local_path: Path to the local file to upload.
        chunk_size: Bytes per append chunk (default 4 MiB).

    Raises:
        httpx.HTTPStatusError: On non-2xx DFS responses (after logging the
            error code and body).
    """
    # Fetch a storage-scoped token.
    try:
        token_obj = await credential.get_token(STORAGE_SCOPE)
    except ClientAuthenticationError as exc:
        raise auth_error_from_credential_exc(exc) from exc
    token = token_obj.token

    # Base headers sent with every DFS request.
    # x-ms-version: the ADLS Gen2 / OneLake DFS API version.  The OneLake
    # documentation shows 2021-06-08 in response headers as the minimum
    # supported version; omitting it can cause the service to fall back to an
    # unexpectedly old default that may reject the request.
    base_headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "x-ms-version": _DFS_API_VERSION,
    }

    # Pure-GUID OneLake DFS path: workspace GUID + item GUID, NO `.Lakehouse`
    # type suffix.  The friendly-name form (`<guid>.Lakehouse`) is rejected with
    # 400 FriendlyNameSupportDisabled on tenants where that feature is disabled.
    # The pure-GUID form works regardless of whether friendly-name support is
    # enabled, so it is unconditionally safer.
    dfs_url = f"{_ONELAKE_DFS_BASE}/{workspace_id}/{lakehouse_id}/Files/{dest_path}"

    file_size = local_path.stat().st_size
    _logger.debug(
        "onelake_upload_file: %s -> %s (size=%d bytes, chunk=%d)",
        local_path,
        dfs_url,
        file_size,
        chunk_size,
    )

    async with httpx.AsyncClient(timeout=300.0) as client:
        # ------------------------------------------------------------------
        # Step 1: create an empty file.
        #
        # The ADLS Gen2 DFS Path - Create API requires Content-Length: 0 on
        # the PUT ?resource=file call.  Omitting it or sending a non-zero
        # value results in a 400 ContentLengthMustBeZero error.
        #
        # A freshly provisioned Lakehouse can briefly return 400 before its
        # OneLake storage backend is fully ready even though the Fabric LRO
        # has already signalled completion.  We retry with a short backoff to
        # tolerate this transient provisioning delay.
        # ------------------------------------------------------------------
        create_headers = {**base_headers, "Content-Length": "0"}
        create_resp: httpx.Response | None = None
        for attempt in range(_DFS_CREATE_MAX_RETRIES):
            create_resp = await client.put(
                dfs_url,
                params={"resource": "file"},
                headers=create_headers,
                content=b"",
            )
            if create_resp.is_success:
                break
            attempt_label = f"attempt {attempt + 1}/{_DFS_CREATE_MAX_RETRIES}"
            _log_dfs_error(create_resp, f"DFS create ({attempt_label})")
            # Only retry on transient statuses.  Hard failures (401/403/404) and
            # 409 Conflict (file already exists from a prior partial attempt) must
            # surface immediately so the caller gets actionable feedback.
            if create_resp.status_code not in _DFS_CREATE_RETRYABLE_STATUSES:
                break
            if attempt < _DFS_CREATE_MAX_RETRIES - 1:
                await asyncio.sleep(_DFS_CREATE_RETRY_DELAY * (attempt + 1))
        if create_resp is None:  # pragma: no cover — loop always sets this
            msg = "DFS create: no response received"
            raise RuntimeError(msg)
        create_resp.raise_for_status()

        # ------------------------------------------------------------------
        # Step 2: append chunks.
        # ------------------------------------------------------------------
        offset = 0
        with local_path.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                append_resp = await client.patch(
                    dfs_url,
                    params={"action": "append", "position": offset},
                    content=chunk,
                    headers={
                        **base_headers,
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(chunk)),
                    },
                )
                if not append_resp.is_success:
                    _log_dfs_error(append_resp, f"DFS append (offset={offset})")
                append_resp.raise_for_status()
                offset += len(chunk)

        # ------------------------------------------------------------------
        # Step 3: flush (commit).
        # Content-Length must be 0 for flush (no request body).
        # ------------------------------------------------------------------
        flush_resp = await client.patch(
            dfs_url,
            params={"action": "flush", "position": offset},
            headers={**base_headers, "Content-Length": "0"},
            content=b"",
        )
        if not flush_resp.is_success:
            _log_dfs_error(flush_resp, f"DFS flush (position={offset})")
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
    truncate_first: bool = False,
) -> CopyIntoResult:
    """Run ``COPY INTO`` from *url* into ``[schema].[table]``.

    This is the remote-URL path; no local staging is performed.

    Atomic truncate-and-load (#863)
    -------------------------------
    When *truncate_first* is ``True``, a ``TRUNCATE TABLE`` and the ``COPY INTO``
    run inside a **single explicit transaction on one connection** (no commit
    between them).  If the ``COPY INTO`` fails, the ``TRUNCATE`` is rolled back
    and the table keeps its existing rows — there is no window in which the
    table is left empty by a failed load.  Fabric Data Warehouse supports both
    ``TRUNCATE TABLE`` and ``COPY INTO`` (a DML statement) inside an explicit
    transaction; any statement failure rolls back the whole transaction.

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
        max_errors: Maximum errors before abort.  Only valid for ``FILE_TYPE='CSV'``;
            Fabric rejects ``MAXERRORS`` for Parquet.  When *file_type* is
            ``'PARQUET'`` and *max_errors* is not ``None``, a warning is emitted
            and the value is cleared before building the SQL statement.
        rejected_row_location: URL for rejected row output.
        kind: Warehouse item kind.  SQL Endpoint items are rejected.
        mode: Credential mode for SQL authentication.
        truncate_first: When ``True``, ``TRUNCATE TABLE [schema].[table]`` runs in
            the same transaction as the ``COPY INTO`` so a failed load rolls the
            truncate back (atomic replace-the-rows semantics).  The caller is
            responsible for any destructive-operation authorization gate.

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

    # MAXERRORS is only valid for CSV; Fabric rejects it for PARQUET with
    # "Option 'MAXERRORS' is not supported for specified format 'PARQUET'".
    # Clear the value here (defence-in-depth alongside the gate in
    # _build_copy_into_sql) so every caller — local-file and URL — is covered.
    if max_errors is not None and file_type == "PARQUET":
        _logger.warning(
            "copy_into_from_url: --max-errors is not supported for Parquet COPY INTO "
            "(Fabric rejects MAXERRORS for FILE_TYPE='PARQUET'); ignoring max_errors=%d",
            max_errors,
        )
        max_errors = None

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
        # mssql-python ≥ 1.9.0: COPY INTO produces NO result set.
        # cursor.description is None and fetchall() raises
        # ProgrammingError: Invalid cursor state.
        # cursor.rowcount correctly equals the number of rows loaded.
        # rows_rejected is not exposed via the ODBC rowcount API; it is
        # always reported as 0 here (known limitation).

        _mode = mode if isinstance(mode, CredentialMode) else CredentialMode.DEFAULT
        try:
            if truncate_first:
                # Atomic replace (#863): TRUNCATE + COPY INTO in ONE transaction
                # on ONE connection.  commit_per_statement=False defers the single
                # commit until both statements succeed, so a COPY failure rolls
                # back the TRUNCATE and the table keeps its existing rows.
                truncate_sql = (
                    f"TRUNCATE TABLE {quote_identifier(schema)}.{quote_identifier(table)}"
                )
                raw_rowcount = run_statements(
                    target,
                    [truncate_sql, sql],
                    mode=_mode,
                    commit_per_statement=False,
                    fetch_last_rowcount=True,
                )
            else:
                # mssql-python rowcount path: run_query(fetch="rowcount") returns
                # ([], [(N,)]) where N is cursor.rowcount.
                _cols, rows = run_query(target, sql, mode=_mode, commit=True, fetch="rowcount")
                raw_rowcount = rows[0][0] if rows and rows[0] else None
        except FabricServerError as exc:
            # run_query now wraps unmapped driver SQL errors (e.g. a column-type
            # mismatch in the COPY INTO target) as FabricServerError.  Re-raise
            # with the COPY INTO context prepended so the user knows which table
            # failed, while the cleaned SQL Server message is preserved.
            safe_msg = f"COPY INTO [{schema}].[{table}] failed: {exc}"
            raise FabricServerError(safe_msg, is_retriable=False) from exc
        except FabricError:
            # Other already-mapped high-level errors (NotFoundError,
            # PermissionDeniedError, AuthError) are re-raised as-is; they carry
            # their own actionable messages and do not contain SQL statement text.
            raise
        except Exception as exc:
            # Truly unmapped exceptions that carry no ddbc_error attribute (e.g.
            # network errors whose str() may embed the raw SQL statement with
            # embedded secrets).  Surface only the safe ddbc_detail when present,
            # otherwise suppress the detail entirely to protect credentials.
            ddbc_detail = getattr(exc, "ddbc_error", None)
            _logger.debug(
                "copy_into_from_url: driver error executing COPY INTO (schema=%s table=%s): %s",
                schema,
                table,
                type(exc).__name__,
                # Intentionally NOT logging str(exc) — may contain SQL with secrets.
            )
            if ddbc_detail:
                safe_msg = (
                    f"COPY INTO [{schema}].[{table}] failed: {type(exc).__name__}: {ddbc_detail}"
                )
            else:
                safe_msg = (
                    f"COPY INTO [{schema}].[{table}] failed: "
                    f"{type(exc).__name__} (details suppressed to protect credentials)"
                )
            raise FabricError(safe_msg) from exc
        # raw_rowcount is cursor.rowcount from the COPY INTO (rows loaded).
        # ODBC rowcount is -1 when the driver cannot determine the count;
        # treat any negative or missing value as 0.
        rows_loaded = int(raw_rowcount) if raw_rowcount is not None and raw_rowcount >= 0 else 0
        rows_rejected = 0
        return rows_loaded, rows_rejected

    rows_loaded, rows_rejected = await asyncio.to_thread(_run)
    return CopyIntoResult(
        rows_loaded=rows_loaded,
        rows_rejected=rows_rejected,
        target=f"{schema}.{table}",
    )


# ---------------------------------------------------------------------------
# Local-file orchestration
# ---------------------------------------------------------------------------


async def load_local_file(  # noqa: PLR0915
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

    # A3: validate staging_lakehouse_name if explicitly provided.
    if staging_lakehouse_name is not None:
        _validate_staging_name(staging_lakehouse_name)

    if not local_path.exists():
        msg = f"Local file not found: {local_path}"
        raise FileNotFoundError(msg)

    # A2: enforce file size limit before attempting to stage.
    file_size = local_path.stat().st_size
    if file_size > _MAX_STAGING_FILE_BYTES:
        msg = (
            f"Local file {local_path.name!r} is too large to stage "
            f"({file_size:,} bytes > {_MAX_STAGING_FILE_BYTES:,} byte limit). "
            "Split the file or upload directly to OneLake and use --url instead."
        )
        raise ValueError(msg)

    # Step 1: determine format.
    fmt = file_format or infer_file_format(local_path)

    # Step 2: JSON → Parquet conversion.
    # B2 FIX: register converted_path for cleanup BEFORE any API call so that
    # a failure in create_staging_lakehouse cannot leave the temp file behind.
    converted_path: Path | None = None
    upload_path = local_path
    file_type: str
    if fmt == "json":
        _logger.info("load_local_file: converting JSON to Parquet: %s", local_path)
        converted_path = await asyncio.to_thread(_json_to_parquet, local_path)
        upload_path = converted_path
        file_type = "PARQUET"
    elif fmt == "parquet":
        file_type = "PARQUET"
    else:
        file_type = "CSV"

    # A1: normalise the destination filename — decode percent-encoded characters
    # (e.g. %2F / %5C that could be interpreted as path separators) and strip
    # any directory component so only a bare filename is embedded in the DFS URL.
    dest_filename = _safe_dest_filename(upload_path)

    # B2 FIX: wrap everything from conversion onward in a try/finally so that
    # the temp Parquet file is always cleaned up, even if create_staging_lakehouse
    # or any subsequent step fails before we reach the original finally block.
    try:
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
                "load_local_file: uploading %s to OneLake Lakehouse %s",
                upload_path,
                lakehouse_id,
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
            # Pure-GUID path (no .Lakehouse suffix): the canonical OneLake form,
            # consistent with the DFS upload path above and Microsoft's documented
            # OPENROWSET/COPY INTO source form.  Works on all tenants regardless of
            # whether the friendly-name feature is enabled.
            onelake_url = (
                f"https://onelake.dfs.fabric.microsoft.com"
                f"/{workspace_id}/{lakehouse_id}/Files/{dest_filename}"
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
            if not keep_staging:
                _logger.info("load_local_file: deleting staging Lakehouse %s", lakehouse_id)
                await delete_lakehouse(http, workspace_id, lakehouse_id)
            else:
                _logger.info(
                    "load_local_file: --keep-staging set; leaving Lakehouse %s (%s) in place",
                    lh_name,
                    lakehouse_id,
                )
    finally:
        # Always clean up the converted temp file (B2 fix: covers create_staging_lakehouse failure).
        if converted_path is not None:
            try:
                converted_path.unlink(missing_ok=True)
            except OSError as exc:
                _logger.warning(
                    "load_local_file: failed to delete converted file %s: %s",
                    converted_path,
                    exc,
                )


# ---------------------------------------------------------------------------
# Table existence check
# ---------------------------------------------------------------------------


async def table_exists(
    target: SqlTarget,
    schema: str,
    table: str,
    mode: object = None,
) -> bool:
    """Return ``True`` when ``[schema].[table]`` exists in ``sys.tables``."""
    return await _table_exists(target, schema, table, mode=mode)


async def truncate_table(
    target: SqlTarget,
    schema: str,
    table: str,
    mode: object = None,
) -> None:
    """Issue ``TRUNCATE TABLE [schema].[table]``."""
    await _truncate_table_sql(target, schema, table, mode=mode)


async def _table_exists(
    target: SqlTarget,
    schema: str,
    table: str,
    mode: object = None,
) -> bool:
    """Return True when ``[schema].[table]`` exists in ``sys.tables``."""
    from fabric_dw.auth import CredentialMode  # noqa: PLC0415

    _mode = mode if isinstance(mode, CredentialMode) else CredentialMode.DEFAULT

    _sql = (
        "SELECT 1 FROM sys.tables t "
        "JOIN sys.schemas s ON s.schema_id = t.schema_id "
        "WHERE s.name = ? AND t.name = ?;"
    )

    def _run() -> bool:
        _cols, rows = run_query(target, _sql, params=[schema, table], mode=_mode)
        return bool(rows)

    return await asyncio.to_thread(_run)


async def _drop_table_sql(
    target: SqlTarget,
    schema: str,
    table: str,
    mode: object = None,
) -> None:
    """Issue ``DROP TABLE [schema].[table]``."""
    from fabric_dw.auth import CredentialMode  # noqa: PLC0415

    _mode = mode if isinstance(mode, CredentialMode) else CredentialMode.DEFAULT
    ddl = f"DROP TABLE {quote_identifier(schema)}.{quote_identifier(table)}"

    def _run() -> None:
        run_query(target, ddl, mode=_mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)


async def _truncate_table_sql(
    target: SqlTarget,
    schema: str,
    table: str,
    mode: object = None,
) -> None:
    """Issue ``TRUNCATE TABLE [schema].[table]``."""
    from fabric_dw.auth import CredentialMode  # noqa: PLC0415

    _mode = mode if isinstance(mode, CredentialMode) else CredentialMode.DEFAULT
    ddl = f"TRUNCATE TABLE {quote_identifier(schema)}.{quote_identifier(table)}"

    def _run() -> None:
        run_query(target, ddl, mode=_mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# Schema inference for local files (reuse #308 helpers)
# ---------------------------------------------------------------------------


async def _infer_columns_from_local(
    local_path: Path,
    fmt: FileFormat,
    *,
    all_varchar: bool = False,
    varchar_length: int = 8000,
    sample_rows: int = 1000,
    csv_delimiter: str = ",",
    csv_encoding: str = "utf-8-sig",
) -> list:
    """Infer :class:`~fabric_dw.models.ColumnSpec` list from a local file.

    Delegates to the public helpers in :mod:`fabric_dw.services.tables`:

    - Parquet: :func:`~fabric_dw.services.tables.infer_columns_from_parquet`
      (reads the Parquet footer only — no data rows).
    - CSV: :func:`~fabric_dw.services.tables.infer_columns_from_csv`
      (header + bounded sample via ``pyarrow.csv``).
    - JSON: converts to Parquet via :func:`_json_to_parquet`, then delegates
      to the Parquet path above.

    Returns a list of :class:`~fabric_dw.models.ColumnSpec`.
    """
    from fabric_dw.services.tables import (  # noqa: PLC0415
        infer_columns_from_csv,
        infer_columns_from_parquet,
    )

    if fmt == "parquet":
        return await infer_columns_from_parquet(local_path, varchar_length=varchar_length)

    if fmt == "json":
        # JSON → Parquet (same path used by load_local_file); schema comes for free.
        converted: Path | None = None
        try:
            converted = await asyncio.to_thread(_json_to_parquet, local_path)
            return await infer_columns_from_parquet(converted, varchar_length=varchar_length)
        finally:
            if converted is not None:
                import contextlib as _cl  # noqa: PLC0415

                with _cl.suppress(OSError):
                    converted.unlink(missing_ok=True)

    # CSV
    return await infer_columns_from_csv(
        local_path,
        all_varchar=all_varchar,
        varchar_length=varchar_length,
        sample_rows=sample_rows,
        delimiter=csv_delimiter,
        encoding=csv_encoding,
    )


# ---------------------------------------------------------------------------
# create_and_load — public API
# ---------------------------------------------------------------------------


async def create_and_load(
    http: FabricHttpClient,
    credential: AsyncTokenCredential,
    workspace_id: uuid.UUID,
    target: SqlTarget,
    schema: str,
    table: str,
    local_path: Path,
    *,
    if_exists: IfExistsPolicy = "fail",
    file_format: FileFormat | None = None,
    staging_lakehouse_name: str | None = None,
    keep_staging: bool = False,
    csv_options: CopyIntoCsvOptions | None = None,
    max_errors: int | None = None,
    rejected_row_location: str | None = None,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: object = None,
    cleanup_on_failure: bool = False,
    # Schema-inference options
    all_varchar: bool = False,
    varchar_length: int = 8000,
    sample_rows: int = 1000,
    csv_delimiter: str = ",",
    csv_encoding: str = "utf-8-sig",
    cluster_by: list[str] | None = None,
) -> CopyIntoResult:
    """Create the target table from the source schema and load data into it.

    Combines schema inference (#308) with ``COPY INTO`` (#309) in a single call.

    Flow
    ----
    1. Guard: SQL Analytics Endpoints are rejected (CREATE TABLE + COPY INTO are
       Data Warehouse-only).
    2. Infer columns from *local_path* (Parquet footer / CSV header+sample /
       JSON→Parquet; reuses #308 helpers).
    3. Check whether ``[schema].[table]`` already exists.
    4. Apply the *if_exists* policy:

       - ``"fail"`` — raise :class:`ValueError` when the table already exists.
       - ``"append"`` — skip CREATE, load into the existing table.
       - ``"truncate"`` — TRUNCATE the existing table, then load.
       - ``"replace"`` — DROP + recreate from inferred schema, then load.

    5. Create the table when needed (via #308 ``create_empty_table``).
    6. Load via #309 ``load_local_file``.
    7. If we created the table and *cleanup_on_failure* is ``True``, drop the
       table on load failure — but **never** drop a pre-existing table.

    Args:
        http: Authenticated Fabric HTTP client.
        credential: Azure credential for storage-scoped token (DFS upload).
        workspace_id: Workspace in which to stage and load.
        target: SQL connection target (warehouse connection string).
        schema: Validated SQL schema name.
        table: Validated SQL table name.
        local_path: Path to the local file (CSV, Parquet, or JSON).
        if_exists: Policy when the table already exists.  One of ``"fail"``,
            ``"append"``, ``"truncate"``, or ``"replace"``.
        file_format: Explicit format; inferred from extension when ``None``.
        staging_lakehouse_name: Explicit staging Lakehouse name; auto-generated
            when ``None``.
        keep_staging: Keep the staging Lakehouse after loading (debugging).
        csv_options: CSV loading options (used when file_format is ``"csv"``).
        max_errors: Maximum errors before aborting the load.
        rejected_row_location: URL for rejected-row output.
        kind: Warehouse item kind.  SQL Analytics Endpoints are rejected.
        mode: Credential mode for SQL authentication.
        cleanup_on_failure: Drop the table if WE created it and the load fails.
            Pre-existing tables are never dropped.
        all_varchar: Force all CSV columns to ``VARCHAR(*varchar_length*)``.
        varchar_length: Default VARCHAR/VARBINARY length for inferred columns.
        sample_rows: Maximum rows to sample for CSV type inference.
        csv_delimiter: CSV field delimiter (used for schema inference only).
        csv_encoding: CSV encoding for schema inference.
        cluster_by: Optional list of column names for the ``CLUSTER BY`` clause
            (up to 4).  Passed through to :func:`~fabric_dw.services.tables.create_empty_table`
            when a table is created.  Each name must appear in the inferred schema.

    Returns:
        A :class:`~fabric_dw.models.CopyIntoResult`.

    Raises:
        ItemKindError: If *kind* is SQL_ENDPOINT.
        ValueError: If the table exists and *if_exists* is ``"fail"``, or if
            the file format is unrecognised or unsupported, or if a *cluster_by*
            column is not in the inferred schema, or if more than 4 *cluster_by*
            columns are supplied.
        FileNotFoundError: If *local_path* does not exist.
    """
    _assert_not_sql_endpoint(kind)
    validate_identifier(schema)
    validate_identifier(table)

    if not local_path.exists():
        msg = f"Local file not found: {local_path}"
        raise FileNotFoundError(msg)

    fmt = file_format or infer_file_format(local_path)

    # Step 1: infer schema from the source file.
    _logger.debug("create_and_load: inferring schema from %s (format=%s)", local_path.name, fmt)
    columns = await _infer_columns_from_local(
        local_path,
        fmt,
        all_varchar=all_varchar,
        varchar_length=varchar_length,
        sample_rows=sample_rows,
        csv_delimiter=csv_delimiter,
        csv_encoding=csv_encoding,
    )

    # Step 2: check existence.
    exists = await _table_exists(target, schema, table, mode=mode)

    # Track whether WE created the table so cleanup_on_failure is scoped.
    we_created = False

    if exists:
        if if_exists == "fail":
            msg = (
                f"Table [{schema}].[{table}] already exists. "
                "Use --if-exists append/truncate/replace to handle existing tables."
            )
            raise ValueError(msg)
        # Guard: CLUSTER BY can only be applied when a table is (re)created.
        # truncate keeps the existing schema; append leaves the table as-is.
        # Neither recreates the table, so cluster_by would be silently ignored.
        if cluster_by and if_exists in ("truncate", "append"):
            msg = (
                "CLUSTER BY can only be applied when the table is created; "
                f"it cannot be combined with --if-exists {if_exists!r} on an existing table. "
                "Use --if-exists replace (or drop the table first)."
            )
            raise ValueError(msg)
        if if_exists == "truncate":
            _logger.info("create_and_load: TRUNCATE TABLE [%s].[%s]", schema, table)
            await _truncate_table_sql(target, schema, table, mode=mode)
        elif if_exists == "replace":
            _logger.info("create_and_load: DROP + recreate TABLE [%s].[%s]", schema, table)
            await _drop_table_sql(target, schema, table, mode=mode)
            await _create_table_from_columns(
                target, schema, table, columns, kind=kind, mode=mode, cluster_by=cluster_by
            )
            we_created = True
        # "append": do nothing — COPY INTO will add rows to the existing table.
    else:
        # Table does not exist — create it (for "fail", "replace", "append" with no table).
        _logger.info("create_and_load: CREATE TABLE [%s].[%s]", schema, table)
        await _create_table_from_columns(
            target, schema, table, columns, kind=kind, mode=mode, cluster_by=cluster_by
        )
        we_created = True

    # Step 3: load data.
    try:
        result = await load_local_file(
            http,
            credential,
            workspace_id,
            target,
            schema,
            table,
            local_path,
            file_format=fmt,
            staging_lakehouse_name=staging_lakehouse_name,
            keep_staging=keep_staging,
            csv_options=csv_options,
            max_errors=max_errors,
            rejected_row_location=rejected_row_location,
            kind=kind,
            mode=mode,
        )
    except Exception:
        if cleanup_on_failure and we_created:
            _logger.info("create_and_load: cleanup_on_failure — dropping [%s].[%s]", schema, table)
            try:
                await _drop_table_sql(target, schema, table, mode=mode)
            except Exception as drop_exc:
                _logger.warning("create_and_load: cleanup_on_failure drop failed: %s", drop_exc)
        raise

    _logger.info(
        "create_and_load: loaded %d rows into [%s].[%s] (rejected=%d)",
        result.rows_loaded,
        schema,
        table,
        result.rows_rejected,
    )
    return result


async def _create_table_from_columns(
    target: SqlTarget,
    schema: str,
    table: str,
    columns: list,
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: object = None,
    cluster_by: list[str] | None = None,
) -> None:
    """Create an empty table from an inferred column list.

    Thin wrapper delegating to
    :func:`~fabric_dw.services.tables.create_empty_table`.
    """
    from fabric_dw.auth import CredentialMode  # noqa: PLC0415
    from fabric_dw.services.tables import create_empty_table  # noqa: PLC0415

    _mode = mode if isinstance(mode, CredentialMode) else CredentialMode.DEFAULT
    await create_empty_table(
        target, schema, table, columns, cluster_by=cluster_by, kind=kind, mode=_mode
    )
