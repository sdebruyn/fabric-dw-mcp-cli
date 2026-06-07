"""Realistic JSON payload constants adapted from Microsoft Learn documentation shapes."""

WORKSPACE_LIST_PAYLOAD = """{
  "value": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "displayName": "AnalyticsWorkspace",
      "description": "Primary analytics workspace for data engineering",
      "type": "Workspace",
      "capacityId": "cafebabe-dead-beef-cafe-babe12345678"
    },
    {
      "id": "b2c3d4e5-f6a7-8901-bcde-f01234567891",
      "displayName": "DataScienceWorkspace",
      "description": null,
      "type": "Workspace",
      "capacityId": null
    }
  ],
  "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces?continuationToken=eyJ0b2tlbiI6InRlc3QifQ%3D%3D"
}"""

WAREHOUSE_GET_PAYLOAD = """{
  "id": "d4e5f6a7-b8c9-0123-def0-123456789abc",
  "displayName": "SalesWarehouse",
  "description": "Data warehouse for sales analytics",
  "type": "Warehouse",
  "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "properties": {
    "connectionString": "saleswarehouse.datawarehouse.fabric.microsoft.com",
    "defaultCollation": "Latin1_General_100_BIN2_UTF8",
    "createdDate": "2024-03-15T10:30:00Z",
    "oneLakeFilesPath": "https://onelake.dfs.fabric.microsoft.com/a1b2c3d4-e5f6-7890-abcd-ef1234567890/d4e5f6a7-b8c9-0123-def0-123456789abc/Files",
    "oneLakeTablesPath": "https://onelake.dfs.fabric.microsoft.com/a1b2c3d4-e5f6-7890-abcd-ef1234567890/d4e5f6a7-b8c9-0123-def0-123456789abc/Tables",
    "sqlEndpointProperties": {
      "connectionString": "warehouse-sql-ep.datawarehouse.fabric.microsoft.com",
      "id": "e5f6a7b8-c9d0-1234-ef01-234567890abc",
      "provisioningStatus": "Success"
    }
  }
}"""

LAKEHOUSE_GET_PAYLOAD = """{
  "id": "e5f6a7b8-c9d0-1234-ef01-234567890abc",
  "displayName": "SalesLakehouse",
  "description": "Lakehouse for raw sales data",
  "type": "Lakehouse",
  "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "properties": {
    "oneLakeFilesPath": "https://onelake.dfs.fabric.microsoft.com/a1b2c3d4-e5f6-7890-abcd-ef1234567890/e5f6a7b8-c9d0-1234-ef01-234567890abc/Files",
    "oneLakeTablesPath": "https://onelake.dfs.fabric.microsoft.com/a1b2c3d4-e5f6-7890-abcd-ef1234567890/e5f6a7b8-c9d0-1234-ef01-234567890abc/Tables",
    "sqlEndpointProperties": {
      "connectionString": "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com",
      "id": "f6a7b8c9-d0e1-2345-f012-34567890abcd",
      "provisioningStatus": "Success"
    }
  }
}"""

AUDIT_SETTINGS_PAYLOAD = """{
  "state": "Enabled",
  "retentionDays": 30,
  "auditActionsAndGroups": ["BATCH_COMPLETED_GROUP", "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"]
}"""

WAREHOUSE_SNAPSHOT_PAYLOAD = """{
  "id": "f6a7b8c9-d0e1-2345-f012-34567890abcd",
  "displayName": "SalesWarehouse_Snapshot_20240315",
  "parentWarehouseId": "d4e5f6a7-b8c9-0123-def0-123456789abc",
  "snapshotDateTime": "2024-03-15T08:00:00Z"
}"""

RESTORE_POINT_PAYLOAD = """{
  "id": "07a8b9c0-d1e2-3456-0123-456789abcdef",
  "name": "RestorePoint_20240315",
  "description": "Automated restore point before schema migration",
  "createdAt": "2024-03-15T06:00:00Z",
  "isSystemCreated": false
}"""

# Second page of workspace listing (no continuationUri → last page)
WORKSPACE_LIST_PAGE2_PAYLOAD = """{
  "value": [
    {
      "id": "c3d4e5f6-a7b8-9012-cdef-012345678901",
      "displayName": "MLWorkspace",
      "description": "Machine learning workspace",
      "type": "Workspace",
      "capacityId": "cafebabe-dead-beef-cafe-babe12345678"
    }
  ]
}"""

# Single workspace GET response (no collation-related field)
WORKSPACE_GET_PAYLOAD = """{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "displayName": "AnalyticsWorkspace",
  "description": "Primary analytics workspace for data engineering",
  "type": "Workspace",
  "capacityId": "cafebabe-dead-beef-cafe-babe12345678"
}"""

# Items list page containing two WarehouseSnapshot items + one non-snapshot item.
# Used in snapshot service tests to verify pagination and type-filtering.
ITEMS_LIST_WITH_SNAPSHOTS_PAYLOAD = """{
  "value": [
    {
      "id": "f6a7b8c9-d0e1-2345-f012-34567890abcd",
      "displayName": "SalesWarehouse_Snapshot_20240315",
      "type": "WarehouseSnapshot",
      "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    },
    {
      "id": "22222222-3333-4444-5555-666666666666",
      "displayName": "OtherWarehouse_Snapshot",
      "type": "WarehouseSnapshot",
      "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    },
    {
      "id": "99999999-9999-9999-9999-999999999999",
      "displayName": "SomeLakehouse",
      "type": "Lakehouse",
      "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
  ]
}"""

# LRO polling response for a WarehouseSnapshot creation operation (Succeeded).
WAREHOUSE_SNAPSHOT_CREATE_OPERATION_PAYLOAD = """{
  "status": "Succeeded",
  "createdTimeUtc": "2024-03-15T10:00:00Z",
  "lastUpdatedTimeUtc": "2024-03-15T10:01:00Z",
  "percentComplete": 100,
  "error": null
}"""
