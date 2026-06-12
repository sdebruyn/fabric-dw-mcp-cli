"""Service functions for Microsoft Fabric Warehouse Snapshot operations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fabric_dw.auth import CredentialMode
from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import WarehouseKind, WarehouseSnapshot, WarehouseSnapshotApiPayload, as_props
from fabric_dw.services._helpers import compact
from fabric_dw.services._lro import LRO_DETAIL_WAIT_S, LRO_MAX_DETAIL_RETRIES, resolve_lro_item_id
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "create",
    "delete",
    "list_snapshots",
    "rename",
    "roll_timestamp",
]

# Characters / sequences that could enable SQL injection in a bracket-quoted name.
# This is a strict blocklist: snapshot names are NOT passed through
# :func:`~fabric_dw.identifiers.validate_identifier` because they may contain
# spaces and mixed-case characters that the identifier regex would reject.
# The blocklist covers the characters that could break out of a bracket-quoted
# name or terminate a statement.
_FORBIDDEN_NAME_CHARS = ("]", ";", "\\", "'", '"', "--", "\n")


def _validate_snapshot_name(name: str, param: str = "name") -> None:
    """Raise ValueError if *name* is empty/whitespace or contains forbidden characters."""
    if not name or not name.strip():
        msg = f"{param} must be a non-empty string"
        raise ValueError(msg)
    for char in _FORBIDDEN_NAME_CHARS:
        if char in name:
            msg = f"snapshot_name contains forbidden character or sequence: {char!r}"
            raise ValueError(msg)


def _snapshot_from_typed_api(item: dict[str, object]) -> WarehouseSnapshot:
    """Build a WarehouseSnapshot from the type-specific API response.

    The ``GET /workspaces/{ws}/warehouseSnapshots`` and
    ``GET /workspaces/{ws}/warehouseSnapshots/{id}`` endpoints return
    ``parentWarehouseId`` and ``snapshotDateTime`` nested under ``properties``
    (not ``creationPayload``).
    """
    props = WarehouseSnapshotApiPayload.props_from_item(item)
    flat: dict[str, object] = {
        "id": item.get("id"),
        "displayName": item.get("displayName"),
        "parentWarehouseId": props.parent_warehouse_id,
        "snapshotDateTime": props.snapshot_date_time,
    }
    return WarehouseSnapshot.model_validate(flat)


async def list_snapshots(
    http: FabricHttpClient,
    workspace_id: UUID,
    parent_warehouse_id: UUID,
) -> list[WarehouseSnapshot]:
    """Return all snapshots belonging to *parent_warehouse_id* in *workspace_id*.

    Uses the type-specific ``GET /workspaces/{ws}/warehouseSnapshots`` endpoint,
    which returns ``properties.parentWarehouseId`` directly and avoids the
    multi-minute propagation lag that the generic items list has for newly
    created snapshots.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        parent_warehouse_id: Only snapshots whose parent matches this UUID are returned.

    Returns:
        A list of :class:`~fabric_dw.models.WarehouseSnapshot` instances.
    """
    out: list[WarehouseSnapshot] = []
    async for item in http.iter_paginated(
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/warehouseSnapshots",
    ):
        raw_parent_id = WarehouseSnapshotApiPayload.props_from_item(item).parent_warehouse_id
        if raw_parent_id and UUID(str(raw_parent_id)) == parent_warehouse_id:
            out.append(_snapshot_from_typed_api(item))
    return out


async def create(
    http: FabricHttpClient,
    workspace_id: UUID,
    parent_warehouse_id: UUID,
    name: str,
    *,
    description: str | None = None,
    snapshot_dt: datetime | None = None,
) -> WarehouseSnapshot:
    """Create a new warehouse snapshot via a long-running operation (LRO).

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        parent_warehouse_id: The UUID of the warehouse to snapshot.
        name: Display name for the new snapshot (non-empty trimmed string).
        description: Optional description for the snapshot.
        snapshot_dt: Optional point-in-time datetime for the snapshot. If
            ``None``, the service captures the current state.

    Returns:
        The newly-created :class:`~fabric_dw.models.WarehouseSnapshot`.

    Raises:
        ValueError: If *name* is empty or whitespace.
    """
    if not name or not name.strip():
        msg = "name must be a non-empty string"
        raise ValueError(msg)

    creation_payload: dict[str, object] = {
        "parentWarehouseId": str(parent_warehouse_id),
    }
    if snapshot_dt is not None:
        creation_payload["snapshotDateTime"] = snapshot_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    body: dict[str, object] = {
        **compact({"description": description}),
        "type": "WarehouseSnapshot",
        "displayName": name,
        "creationPayload": creation_payload,
    }

    resp = await http.request(
        "POST",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/items",
        json=body,
    )

    location: str = resp.headers.get("Location", "")
    operation_result = await http.poll_operation(location)

    # Extract the new item's ID using the shared LRO helper.
    # Fabric LRO status bodies only contain status metadata — the created item ID is NOT
    # included in the status body.  Per Microsoft docs, once Succeeded, the item is
    # available via GET /v1/operations/{op_id}/result.
    # The helper tries Path A (status body keys) then Path B (/result sub-endpoint).
    resource_id_str = await resolve_lro_item_id(
        http,
        operation_result=operation_result,
        location=location,
        result_id_keys=("resourceId", "createdItemId", "itemId", "id"),
    )
    if not resource_id_str:
        msg = f"Cannot determine new snapshot ID from LRO result: {operation_result}"
        raise ValueError(msg)
    new_snap_id = UUID(resource_id_str)

    # Use the type-specific endpoint to fetch the new snapshot's detail.
    # GET /warehouseSnapshots/{id} returns properties.parentWarehouseId directly.
    # Retry up to LRO_MAX_DETAIL_RETRIES times with LRO_DETAIL_WAIT_S back-off in
    # case provisioning hasn't finished yet.
    _typed_detail_path = f"/workspaces/{workspace_id}/warehouseSnapshots/{new_snap_id}"
    typed_body: dict[str, object] = {}
    for _attempt in range(LRO_MAX_DETAIL_RETRIES):
        typed_resp = await http.request("GET", HttpBase.FABRIC, _typed_detail_path)
        typed_body = typed_resp.json()
        if WarehouseSnapshotApiPayload.props_from_item(typed_body).parent_warehouse_id is not None:
            break
        if _attempt < LRO_MAX_DETAIL_RETRIES - 1:
            await asyncio.sleep(LRO_DETAIL_WAIT_S)
    else:
        # parentWarehouseId still absent after all retries — inject the value we sent.
        existing_props = as_props(typed_body.get("properties"))
        typed_body = dict(typed_body)
        typed_body["properties"] = {
            **existing_props,
            "parentWarehouseId": str(parent_warehouse_id),
        }

    return _snapshot_from_typed_api(typed_body)


async def rename(
    http: FabricHttpClient,
    workspace_id: UUID,
    snapshot_id: UUID,
    *,
    new_name: str,
    description: str | None = None,
    cache: LookupCache | None = None,
    old_name: str | None = None,
) -> WarehouseSnapshot:
    """Rename (and optionally re-describe) an existing warehouse snapshot.

    Microsoft Fabric requires re-sending ``creationPayload`` on rename.
    This function first GETs the snapshot to obtain the current
    ``parentWarehouseId``, then PATCHes with the full required body.

    After a successful rename the stale (workspace_id, old_name) cache entry is
    evicted and a new entry under (workspace_id, new_name) is populated.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        snapshot_id: The UUID of the snapshot to rename.
        new_name: The new display name (non-empty trimmed string).
        description: Optional description to set on the snapshot.
        cache: Optional :class:`~fabric_dw.cache.LookupCache` for stale-entry eviction.
        old_name: The current display name; used to evict the stale cache entry.

    Returns:
        The updated :class:`~fabric_dw.models.WarehouseSnapshot`.

    Raises:
        ValueError: If *new_name* is empty or whitespace.
    """
    if not new_name or not new_name.strip():
        msg = "new_name must be a non-empty string"
        raise ValueError(msg)

    # Fetch the current snapshot to read parentWarehouseId (required for the PATCH body).
    _typed_path = f"/workspaces/{workspace_id}/warehouseSnapshots/{snapshot_id}"
    current_resp = await http.request("GET", HttpBase.FABRIC, _typed_path)
    current: dict[str, object] = current_resp.json()
    parent_wh_id = WarehouseSnapshotApiPayload.props_from_item(current).parent_warehouse_id

    patch_body: dict[str, object] = {
        **compact({"description": description}),
        "type": "WarehouseSnapshot",
        "displayName": new_name,
        "creationPayload": {
            "parentWarehouseId": parent_wh_id,
        },
    }

    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/items/{snapshot_id}",
        json=patch_body,
    )

    # PATCH /items/{id} returns partial body for rename; re-fetch via typed endpoint.
    updated_resp = await http.request("GET", HttpBase.FABRIC, _typed_path)
    result = _snapshot_from_typed_api(updated_resp.json())

    if cache is not None:
        if old_name is not None:
            cache.evict_item(workspace_id, old_name)
        new_entry = ItemEntry(
            id=snapshot_id,
            kind=WarehouseKind.SNAPSHOT,
            connection_string=None,
            fetched_at=datetime.now(tz=UTC),
            display_name=new_name,
        )
        cache.put_item(workspace_id, new_name, new_entry)
        cache.put_item(workspace_id, str(snapshot_id), new_entry)

    return result


async def delete(
    http: FabricHttpClient,
    workspace_id: UUID,
    snapshot_id: UUID,
    *,
    cache: LookupCache | None = None,
    name: str | None = None,
) -> None:
    """Delete a warehouse snapshot.

    After a successful delete the (workspace_id, name) and
    (workspace_id, snapshot_id) cache entries are evicted.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        snapshot_id: The UUID of the snapshot to delete.
        cache: Optional :class:`~fabric_dw.cache.LookupCache` for stale-entry eviction.
        name: The display name of the snapshot; used to evict the name-keyed entry.

    Raises:
        NotFoundError: If the snapshot does not exist (HTTP 404).
    """
    await http.request(
        "DELETE",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/items/{snapshot_id}",
    )

    if cache is not None:
        if name is not None:
            cache.evict_item(workspace_id, name)
        cache.evict_item(workspace_id, str(snapshot_id))


async def roll_timestamp(
    parent_target: SqlTarget,
    snapshot_name: str,
    new_dt: datetime | None = None,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Advance or reset the timestamp of a warehouse snapshot via T-SQL.

    Executes ``ALTER DATABASE [{snapshot_name}] SET TIMESTAMP = …`` against
    *parent_target*.

    Args:
        parent_target: The :class:`~fabric_dw.sql.SqlTarget` for the parent
            warehouse (the snapshot lives in the same SQL endpoint).
        snapshot_name: The name of the snapshot database. Must not contain
            ``]``, ``;``, ``\\``, ``'``, ``"``, ``--``, or newlines.
        new_dt: If supplied, the snapshot is rolled to this UTC datetime,
            formatted as ``YYYY-MM-DDTHH:MM:SS.SS``. If ``None``, the
            snapshot rolls forward to ``CURRENT_TIMESTAMP``.
        mode: The credential mode for Entra authentication.

    Raises:
        ValueError: If *snapshot_name* contains any forbidden character.
        PermissionDeniedError: If the driver reports a permission failure.
    """
    _validate_snapshot_name(snapshot_name, param="snapshot_name")

    if new_dt is None:
        sql_str = f"ALTER DATABASE [{snapshot_name}] SET TIMESTAMP = CURRENT_TIMESTAMP;"
    else:
        formatted = new_dt.strftime("%Y-%m-%dT%H:%M:%S.00")
        sql_str = f"ALTER DATABASE [{snapshot_name}] SET TIMESTAMP = '{formatted}';"

    def _run() -> None:
        run_query(parent_target, sql_str, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)
