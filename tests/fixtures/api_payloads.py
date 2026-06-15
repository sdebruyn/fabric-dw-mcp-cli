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
  "id": "1726617378000",
  "displayName": "RestorePoint_20240315",
  "description": "Automated restore point before schema migration",
  "creationMode": "UserDefined",
  "creationDetails": {
    "eventDateTime": "2024-03-15T06:00:00Z",
    "eventInitiator": {
      "id": "f3052d1c-61a9-46fb-8df9-0d78916ae041",
      "displayName": "Jacob Hancock",
      "type": "User",
      "userDetails": {"userPrincipalName": "jacob@contoso.com"}
    }
  }
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

# First page of warehouse listing (with continuationUri for pagination tests)
WAREHOUSE_LIST_PAYLOAD = """{
  "value": [
    {
      "id": "d4e5f6a7-b8c9-0123-def0-123456789abc",
      "displayName": "SalesWarehouse",
      "description": "Data warehouse for sales analytics",
      "type": "Warehouse",
      "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "properties": {
        "connectionString": "saleswarehouse.datawarehouse.fabric.microsoft.com",
        "defaultCollation": "Latin1_General_100_BIN2_UTF8",
        "createdDate": "2024-03-15T10:30:00Z"
      }
    },
    {
      "id": "a7b8c9d0-e1f2-3456-a012-345678901234",
      "displayName": "FinanceWarehouse",
      "description": "Data warehouse for finance analytics",
      "type": "Warehouse",
      "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "properties": {
        "connectionString": "financewarehouse.datawarehouse.fabric.microsoft.com",
        "defaultCollation": "Latin1_General_100_CI_AS_KS_WS_SC_UTF8",
        "createdDate": "2024-04-01T09:00:00Z"
      }
    }
  ],
  "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces/a1b2c3d4-e5f6-7890-abcd-ef1234567890/warehouses?continuationToken=eyJ3aCI6InBhZ2UyIn0%3D"
}"""

# Second page of warehouse listing (no continuationUri → last page)
WAREHOUSE_LIST_PAGE2_PAYLOAD = """{
  "value": [
    {
      "id": "b8c9d0e1-f2a3-4567-b012-456789012345",
      "displayName": "HRWarehouse",
      "description": "Data warehouse for HR analytics",
      "type": "Warehouse",
      "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "properties": {
        "connectionString": "hrwarehouse.datawarehouse.fabric.microsoft.com",
        "defaultCollation": "Latin1_General_100_BIN2_UTF8",
        "createdDate": "2024-05-10T12:00:00Z"
      }
    }
  ]
}"""

# SQL analytics endpoints listing for a workspace
WAREHOUSE_SQL_ENDPOINTS_PAYLOAD = """{
  "value": [
    {
      "id": "e5f6a7b8-c9d0-1234-ef01-234567890abc",
      "displayName": "SalesLakehouse",
      "description": "SQL endpoint for sales lakehouse",
      "type": "SQLEndpoint",
      "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "properties": {
        "sqlEndpointProperties": {
          "connectionString": "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com",
          "id": "f6a7b8c9-d0e1-2345-f012-34567890abcd",
          "provisioningStatus": "Success"
        }
      }
    }
  ]
}"""

# 202 response body for warehouse create (LRO initiated)
WAREHOUSE_CREATE_202_PAYLOAD = """{
  "id": "d4e5f6a7-b8c9-0123-def0-123456789abc",
  "displayName": "SalesWarehouse",
  "type": "Warehouse",
  "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}"""

# SQL analytics endpoints first page with continuationUri (for pagination tests)
WAREHOUSE_SQL_ENDPOINTS_PAGE1_PAYLOAD = """{
  "value": [
    {
      "id": "e5f6a7b8-c9d0-1234-ef01-234567890abc",
      "displayName": "SalesLakehouse",
      "description": "SQL endpoint for sales lakehouse",
      "type": "SQLEndpoint",
      "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "properties": {
        "sqlEndpointProperties": {
          "connectionString": "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com",
          "id": "f6a7b8c9-d0e1-2345-f012-34567890abcd",
          "provisioningStatus": "Success"
        }
      }
    }
  ],
  "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces/a1b2c3d4-e5f6-7890-abcd-ef1234567890/sqlEndpoints?continuationToken=eyJzcWwiOiJwYWdlMiJ9"
}"""

# Second page of SQL endpoints listing (no continuationUri → last page)
WAREHOUSE_SQL_ENDPOINTS_PAGE2_PAYLOAD = """{
  "value": [
    {
      "id": "a1b2c3d4-0000-1111-2222-ef1234567890",
      "displayName": "HRLakehouse",
      "description": "SQL endpoint for HR lakehouse",
      "type": "SQLEndpoint",
      "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "properties": {
        "sqlEndpointProperties": {
          "connectionString": "hr-sql-ep.datawarehouse.fabric.microsoft.com",
          "id": "b2c3d4e5-f6a7-8901-bcde-f01234567891",
          "provisioningStatus": "Success"
        }
      }
    }
  ]
}"""

# LRO poll result when the operation has succeeded
WAREHOUSE_OPERATION_SUCCEEDED_PAYLOAD = """{
  "status": "Succeeded",
  "createdTimeUtc": "2024-03-15T10:29:50Z",
  "lastUpdatedTimeUtc": "2024-03-15T10:30:00Z",
  "percentComplete": 100,
  "error": null,
  "resourceLocation": "https://api.fabric.microsoft.com/v1/workspaces/a1b2c3d4-e5f6-7890-abcd-ef1234567890/warehouses/d4e5f6a7-b8c9-0123-def0-123456789abc"
}"""

# LRO poll result when resourceLocation is null (missing)
WAREHOUSE_OPERATION_SUCCEEDED_NO_LOCATION_PAYLOAD = """{
  "status": "Succeeded",
  "createdTimeUtc": "2024-03-15T10:29:50Z",
  "lastUpdatedTimeUtc": "2024-03-15T10:30:00Z",
  "percentComplete": 100,
  "error": null,
  "resourceLocation": null
}"""

# GET /v1/operations/{id}/result body for a completed warehouse creation LRO
WAREHOUSE_OPERATION_RESULT_PAYLOAD = """{
  "id": "d4e5f6a7-b8c9-0123-def0-123456789abc",
  "type": "Warehouse",
  "displayName": "SalesWarehouse",
  "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}"""

# Item access details — single-page response with User, Group, ServicePrincipal entries
ITEM_ACCESS_DETAILS_PAYLOAD = """{
  "accessDetails": [
    {
      "principal": {
        "id": "f3052d1c-61a9-46fb-8df9-0d78916ae041",
        "displayName": "Jacob Hancock",
        "type": "User",
        "userDetails": {
          "userPrincipalName": "jacob@example.com"
        }
      },
      "itemAccessDetails": {
        "type": "Warehouse",
        "permissions": ["Read", "Write"],
        "additionalPermissions": ["ReadAll"]
      }
    },
    {
      "principal": {
        "id": "c7db8e03-c8cb-4d4c-9f64-1dcd327c9d3c",
        "displayName": "TestSecurityGroup",
        "type": "Group",
        "groupDetails": {
          "groupType": "SecurityGroup"
        }
      },
      "itemAccessDetails": {
        "type": "Warehouse",
        "permissions": ["Read"],
        "additionalPermissions": []
      }
    },
    {
      "principal": {
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "displayName": "MyServicePrincipal",
        "type": "ServicePrincipal",
        "servicePrincipalDetails": {
          "aadAppId": "b2c3d4e5-f6a7-8901-bcde-f01234567891"
        }
      },
      "itemAccessDetails": {
        "type": "Warehouse",
        "permissions": ["Read", "Reshare"],
        "additionalPermissions": []
      }
    }
  ]
}"""

# Item access details — page 1 of 2 (with continuationUri)
ITEM_ACCESS_DETAILS_PAGE1_PAYLOAD = """{
  "accessDetails": [
    {
      "principal": {
        "id": "f3052d1c-61a9-46fb-8df9-0d78916ae041",
        "displayName": "Jacob Hancock",
        "type": "User",
        "userDetails": {
          "userPrincipalName": "jacob@example.com"
        }
      },
      "itemAccessDetails": {
        "type": "Warehouse",
        "permissions": ["Read"],
        "additionalPermissions": []
      }
    }
  ],
  "continuationUri": "https://api.fabric.microsoft.com/v1/admin/workspaces/a1b2c3d4-e5f6-7890-abcd-ef1234567890/items/d4e5f6a7-b8c9-0123-def0-123456789abc/users?continuationToken=page2token"
}"""

# Item access details — page 2 of 2 (no continuationUri)
ITEM_ACCESS_DETAILS_PAGE2_PAYLOAD = """{
  "accessDetails": [
    {
      "principal": {
        "id": "c7db8e03-c8cb-4d4c-9f64-1dcd327c9d3c",
        "displayName": "Eric Solomon",
        "type": "User",
        "userDetails": {
          "userPrincipalName": "eric@example.com"
        }
      },
      "itemAccessDetails": {
        "type": "Warehouse",
        "permissions": ["Read", "Reshare"],
        "additionalPermissions": ["ReadAll"]
      }
    }
  ]
}"""
