"""Service functions for Microsoft Fabric Warehouse Snapshot operations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fabric_dw.exceptions import PermissionDenied
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import WarehouseSnapshot
from fabric_dw.sql_client import FabricSqlClient, SqlTarget

__all__ = [
    "create",
    "delete",
    "list",
    "rename",
    "roll_timestamp",
]

# Characters / sequences that could enable SQL injection in a bracket-quoted name.
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


def _snapshot_from_detail(detail: dict[str, object]) -> WarehouseSnapshot:
    """Build a WarehouseSnapshot from a raw item-detail API response.

    The detail endpoint returns ``creationPayload.parentWarehouseId`` and
    ``creationPayload.snapshotDateTime`` nested under ``creationPayload``.
    We flatten them to match the ``WarehouseSnapshot`` model's field aliases.
    """
    creation_payload: dict[str, object] = detail.get("creationPayload") or {}  # type: ignore[assignment]
    flat: dict[str, object] = {
        "id": detail.get("id"),
        "displayName": detail.get("displayName"),
        "parentWarehouseId": creation_payload.get("parentWarehouseId"),
        "snapshotDateTime": creation_payload.get("snapshotDateTime"),
    }
    return WarehouseSnapshot.model_validate(flat)


async def list(  # noqa: A001
    http: FabricHttpClient,
    workspace_id: UUID,
    parent_warehouse_id: UUID,
) -> list[WarehouseSnapshot]:
    """Return all snapshots belonging to *parent_warehouse_id* in *workspace_id*.

    Pages through ``GET /workspaces/{ws}/items``, filters to items with
    ``type=WarehouseSnapshot``, fetches each item's detail to read
    ``creationPayload.parentWarehouseId``, and keeps only those that match
    *parent_warehouse_id*.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        parent_warehouse_id: Only snapshots whose parent matches this UUID are returned.

    Returns:
        A list of :class:`~fabric_dw.models.WarehouseSnapshot` instances.
    """
    snapshot_ids: list[UUID] = []

    async for item in http.iter_paginated(HttpBase.FABRIC, f"/workspaces/{workspace_id}/items"):
        if item.get("type") == "WarehouseSnapshot":
            raw_id = item.get("id")
            if raw_id:
                snapshot_ids.append(UUID(str(raw_id)))

    results: list[WarehouseSnapshot] = []
    for snap_id in snapshot_ids:
        resp = await http.request(
            "GET", HttpBase.FABRIC, f"/workspaces/{workspace_id}/items/{snap_id}"
        )
        detail: dict[str, object] = resp.json()
        creation_payload: dict[str, object] = detail.get("creationPayload") or {}  # type: ignore[assignment]
        raw_parent_id = creation_payload.get("parentWarehouseId")
        if raw_parent_id and UUID(str(raw_parent_id)) == parent_warehouse_id:
            results.append(_snapshot_from_detail(detail))

    return results


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
        "type": "WarehouseSnapshot",
        "displayName": name,
        "creationPayload": creation_payload,
    }
    if description is not None:
        body["description"] = description

    resp = await http.request(
        "POST",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/items",
        json=body,
    )

    location: str = resp.headers.get("Location", "")
    operation_result = await http.poll_operation(location)

    # Extract the new item's ID from the operation result or resourceId field
    resource_id_raw = operation_result.get("resourceId")
    if resource_id_raw:
        new_snap_id = UUID(str(resource_id_raw))
    else:
        # Fall back: parse from the Location header path
        # Location is like .../operations/op-id, but createdItemId may be in result
        created_item = operation_result.get("createdItemId") or operation_result.get("itemId")
        if created_item:
            new_snap_id = UUID(str(created_item))
        else:
            # Last resort: try to find the new snapshot in the items list
            # (This shouldn't happen in normal usage, but provides a safe fallback)
            msg = f"Cannot determine new snapshot ID from LRO result: {operation_result}"
            raise ValueError(msg)

    detail_resp = await http.request(
        "GET",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/items/{new_snap_id}",
    )
    return _snapshot_from_detail(detail_resp.json())


async def rename(
    http: FabricHttpClient,
    workspace_id: UUID,
    snapshot_id: UUID,
    *,
    new_name: str,
    description: str | None = None,
) -> WarehouseSnapshot:
    """Rename (and optionally re-describe) an existing warehouse snapshot.

    Microsoft Fabric requires re-sending ``creationPayload`` on rename.
    This function first GETs the snapshot to obtain the current
    ``parentWarehouseId``, then PATCHes with the full required body.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        snapshot_id: The UUID of the snapshot to rename.
        new_name: The new display name (non-empty trimmed string).
        description: Optional description to set on the snapshot.

    Returns:
        The updated :class:`~fabric_dw.models.WarehouseSnapshot`.

    Raises:
        ValueError: If *new_name* is empty or whitespace.
    """
    if not new_name or not new_name.strip():
        msg = "new_name must be a non-empty string"
        raise ValueError(msg)

    # Fetch the current snapshot to get parentWarehouseId
    detail_resp = await http.request(
        "GET",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/items/{snapshot_id}",
    )
    detail: dict[str, object] = detail_resp.json()
    creation_payload: dict[str, object] = detail.get("creationPayload") or {}  # type: ignore[assignment]
    parent_wh_id = creation_payload.get("parentWarehouseId")

    patch_body: dict[str, object] = {
        "type": "WarehouseSnapshot",
        "displayName": new_name,
        "creationPayload": {
            "parentWarehouseId": parent_wh_id,
        },
    }
    if description is not None:
        patch_body["description"] = description

    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/items/{snapshot_id}",
        json=patch_body,
    )

    # GET the updated snapshot to return fresh state
    updated_resp = await http.request(
        "GET",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/items/{snapshot_id}",
    )
    return _snapshot_from_detail(updated_resp.json())


async def delete(
    http: FabricHttpClient,
    workspace_id: UUID,
    snapshot_id: UUID,
) -> None:
    """Delete a warehouse snapshot.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        snapshot_id: The UUID of the snapshot to delete.

    Raises:
        NotFound: If the snapshot does not exist (HTTP 404).
    """
    await http.request(
        "DELETE",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/items/{snapshot_id}",
    )


async def roll_timestamp(
    sql: FabricSqlClient,
    parent_target: SqlTarget,
    snapshot_name: str,
    new_dt: datetime | None = None,
) -> None:
    """Advance or reset the timestamp of a warehouse snapshot via T-SQL.

    Executes ``ALTER DATABASE [{snapshot_name}] SET TIMESTAMP = …`` against
    *parent_target*.

    Args:
        sql: A :class:`~fabric_dw.sql_client.FabricSqlClient` instance.
        parent_target: The :class:`~fabric_dw.sql_client.SqlTarget` for the
            parent warehouse (the snapshot lives in the same SQL endpoint).
        snapshot_name: The name of the snapshot database. Must not contain
            ``]``, ``;``, ``\\``, ``'``, ``"``, ``--``, or newlines.
        new_dt: If supplied, the snapshot is rolled to this UTC datetime,
            formatted as ``YYYY-MM-DDTHH:MM:SS.SS``. If ``None``, the
            snapshot rolls forward to ``CURRENT_TIMESTAMP``.

    Raises:
        ValueError: If *snapshot_name* contains any forbidden character.
        PermissionDenied: If the driver reports a permission failure.
    """
    _validate_snapshot_name(snapshot_name, param="snapshot_name")

    if new_dt is None:
        sql_str = f"ALTER DATABASE [{snapshot_name}] SET TIMESTAMP = CURRENT_TIMESTAMP;"
    else:
        formatted = new_dt.strftime("%Y-%m-%dT%H:%M:%S.00")
        sql_str = f"ALTER DATABASE [{snapshot_name}] SET TIMESTAMP = '{formatted}';"

    try:
        await sql.execute_nonquery(parent_target, sql_str)
    except PermissionDenied:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if any(
            fragment in msg
            for fragment in ("permission", "access denied", "unauthorized", "403", "forbidden")
        ):
            raise PermissionDenied(str(exc)) from exc
        raise
