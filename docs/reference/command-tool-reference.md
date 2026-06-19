<!-- AUTO-GENERATED — do not edit by hand. Run `just gen-docs` to regenerate. -->

# CLI & MCP tool reference

This page is generated from the live code tree.  Every CLI command and MCP
tool is listed here, grouped by functional domain, with a one-line summary.

For full options, examples, and notes, see the per-domain pages under **Commands**.


## Workspaces

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw workspaces get` | Get details for WORKSPACE (name or GUID). |
| `fdw workspaces list` | List all workspaces the authenticated principal has access to. |
| `fdw workspaces set-collation` | Set the default Data Warehouse COLLATION for WORKSPACE (name or GUID). |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `get_workspace` | Return details for a single workspace (name or GUID). |
| `list_workspaces` | List all Fabric workspaces the caller has access to. |
| `set_workspace_collation` | Set the default Data Warehouse collation for a workspace. |


## Warehouses

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw warehouses create` | Create a new warehouse named NAME in the target workspace. |
| `fdw warehouses delete` | Delete WAREHOUSE (name or GUID) from the target workspace. |
| `fdw warehouses get` | Get details for WAREHOUSE (name or GUID) in the target workspace. |
| `fdw warehouses list` | List all warehouses in the target workspace. |
| `fdw warehouses permissions` | List principals with access to WAREHOUSE (name or GUID) in the target workspace. |
| `fdw warehouses rename` | Rename WAREHOUSE (name or GUID) to NEW_NAME in the target workspace. |
| `fdw warehouses takeover` | Take ownership of WAREHOUSE (name or GUID) in the target workspace. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `create_warehouse` | Create a new Warehouse in a workspace. |
| `delete_warehouse` | Delete a Warehouse. |
| `get_warehouse` | Return details for a single warehouse (name or GUID). |
| `get_warehouse_permissions` | Return principals with access to a Warehouse item. |
| `list_warehouses` | List all warehouses and SQL analytics endpoints in a workspace. |
| `rename_warehouse` | Rename a Warehouse (and optionally update its description). |
| `takeover_warehouse` | Take ownership of a Warehouse. |


## SQL Analytics Endpoints

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw sql-endpoints get` | Get details for ITEM (SQL analytics endpoint, name or GUID) in the target workspace. |
| `fdw sql-endpoints list` | List all SQL analytics endpoints in the target workspace. |
| `fdw sql-endpoints permissions` | List principals with access to ITEM (SQL endpoint, name or GUID) in the target workspace. |
| `fdw sql-endpoints refresh` | Refresh metadata for ITEM (SQL endpoint, name or GUID) in the target workspace. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `get_sql_endpoint` | Return details for a single SQL analytics endpoint (name or GUID). |
| `get_sql_endpoint_permissions` | Return principals with access to a SQL Analytics Endpoint item. |
| `list_sql_endpoints` | List all SQL analytics endpoints in a workspace. |
| `refresh_sql_endpoint_metadata` | Refresh metadata for a SQL analytics endpoint (sync from the underlying Lakehouse). |


## SQL execution

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw sql exec` | Execute a SQL statement against ITEM (warehouse or SQL endpoint). |
| `fdw sql plan` | Capture the estimated SHOWPLAN_XML for ITEM (warehouse or SQL endpoint). |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `execute_sql` | Execute an arbitrary SQL statement or batch against a warehouse or SQL Analytics |
| `get_query_plan` | Capture the estimated SHOWPLAN_XML execution plan for a SQL query without executing it. |


## Tables

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw tables clear` | Truncate QUALIFIED_NAME (schema.table) on ITEM. |
| `fdw tables clone` | Clone SOURCE table as a zero-copy clone named NAME on ITEM. |
| `fdw tables count` | Count rows in QUALIFIED_NAME (schema.table) on ITEM. |
| `fdw tables create` | Create a new table on ITEM. |
| `fdw tables delete` | Drop QUALIFIED_NAME (schema.table) from ITEM. |
| `fdw tables list` | List tables on ITEM (warehouse or SQL endpoint). |
| `fdw tables load` | Load data into QUALIFIED_NAME (schema.table) on ITEM via COPY INTO. |
| `fdw tables read` | Read up to COUNT rows from QUALIFIED_NAME (schema.table) on ITEM. |
| `fdw tables rename` | Rename QUALIFIED_NAME (schema.table) on ITEM to --new-name. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `clear_table` | Truncate a SQL table (remove all rows, keep structure). |
| `clone_table` | Create a zero-copy clone of a table using ``CREATE TABLE … AS CLONE OF …``. |
| `count_table_rows` | Return the total row count of a table via ``SELECT COUNT_BIG(*)``. |
| `create_empty_table` | Create an empty table from an explicit column spec (DDL only, no data). |
| `create_table` | Create a new SQL table via CTAS (CREATE TABLE AS SELECT). |
| `delete_table` | Drop a SQL table. |
| `import_table_from_url` | Load data into an existing Data Warehouse table via ``COPY INTO`` from a remote URL. |
| `list_tables` | List SQL tables on a warehouse or SQL Analytics Endpoint. |
| `load_table_from_url` | Load data into a Data Warehouse table via ``COPY INTO`` from a remote URL. |
| `read_table` | Return up to *count* rows from a table as JSON-serialisable columns + rows. |
| `rename_table` | Rename a SQL table via ``sp_rename`` (Data-Warehouse-only). |


## Views

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw views count` | Count rows in QUALIFIED_NAME (schema.view) on ITEM. |
| `fdw views create` | Create a new view QUALIFIED_NAME on ITEM. |
| `fdw views drop` | Drop QUALIFIED_NAME (schema.view) from ITEM. |
| `fdw views get` | Fetch the full definition of QUALIFIED_NAME (schema.view) on ITEM. |
| `fdw views list` | List views on ITEM (warehouse or SQL endpoint). |
| `fdw views read` | Read up to COUNT rows from QUALIFIED_NAME (schema.view) on ITEM. |
| `fdw views rename` | Rename QUALIFIED_NAME (schema.view) on ITEM to --new-name. |
| `fdw views update` | Redefine QUALIFIED_NAME (schema.view) on ITEM via CREATE OR ALTER VIEW. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `count_view_rows` | Return the total row count of a view via ``SELECT COUNT_BIG(*)``. |
| `create_view` | Create a new SQL view. |
| `drop_view` | Drop a SQL view. |
| `get_view` | Fetch the full definition of a view (schema.view). |
| `list_views` | List SQL views on a warehouse or SQL Analytics Endpoint. |
| `read_view` | Return up to *count* rows from a view as JSON-serialisable columns + rows. |
| `rename_view` | Rename a SQL view via sp_rename. |
| `update_view` | Redefine a SQL view via CREATE OR ALTER VIEW. |


## Stored procedures

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw procedures create` | Create a new stored procedure QUALIFIED_NAME on ITEM. |
| `fdw procedures drop` | Drop QUALIFIED_NAME (schema.proc) from ITEM. |
| `fdw procedures get` | Fetch the full definition of QUALIFIED_NAME (schema.proc) on ITEM. |
| `fdw procedures list` | List stored procedures on ITEM (warehouse or SQL endpoint). |
| `fdw procedures update` | Redefine QUALIFIED_NAME (schema.proc) on ITEM via CREATE OR ALTER PROCEDURE. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `create_procedure` | Create a new stored procedure. |
| `drop_procedure` | Drop a stored procedure. |
| `get_procedure` | Fetch the full definition of a stored procedure (schema.proc). |
| `list_procedures` | List stored procedures on a warehouse or SQL Analytics Endpoint. |
| `update_procedure` | Redefine a stored procedure via CREATE OR ALTER PROCEDURE. |


## Schemas

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw schemas create` | Create a new SQL schema NAME on ITEM. |
| `fdw schemas delete` | Drop schema NAME from ITEM. |
| `fdw schemas list` | List user-defined schemas on ITEM (warehouse). |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `create_schema` | Create a new SQL schema on a warehouse or SQL Analytics Endpoint. |
| `delete_schema` | Drop a SQL schema from a warehouse. |
| `list_schemas` | List user-defined SQL schemas on a warehouse or SQL Analytics Endpoint. |


## Statistics

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw statistics create` | Create a statistic on --table (schema.table) on ITEM. |
| `fdw statistics delete` | Drop STAT_NAME on QUALIFIED_TABLE (schema.table) from ITEM. |
| `fdw statistics list` | List statistics on ITEM (warehouse or SQL endpoint). |
| `fdw statistics show` | Show details of STAT_NAME on QUALIFIED_TABLE (schema.table). |
| `fdw statistics update` | Update STAT_NAME on QUALIFIED_TABLE (schema.table) for ITEM. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `create_statistics` | Create a single-column statistic on a table. |
| `delete_statistics` | Drop a statistic via DROP STATISTICS. |
| `list_statistics` | List statistics on a warehouse or SQL Analytics Endpoint. |
| `show_statistics` | Show details of a statistic using DBCC SHOW_STATISTICS. |
| `update_statistics` | Update an existing statistic via UPDATE STATISTICS. |


## Functions

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw functions create` | Create a new T-SQL user-defined function QUALIFIED_NAME on ITEM. |
| `fdw functions drop` | Drop QUALIFIED_NAME (schema.fn) from ITEM. |
| `fdw functions get` | Fetch the full definition of QUALIFIED_NAME (schema.fn) on ITEM. |
| `fdw functions list` | List T-SQL user-defined functions on ITEM (warehouse or SQL endpoint). |
| `fdw functions rename` | Rename QUALIFIED_NAME (schema.fn) on ITEM to --new-name. |
| `fdw functions update` | Redefine QUALIFIED_NAME (schema.fn) on ITEM via CREATE OR ALTER FUNCTION. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `create_function` | Create a new T-SQL user-defined function. |
| `drop_function` | Drop a T-SQL user-defined function. |
| `get_function` | Fetch the full definition of a T-SQL user-defined function (schema.fn). |
| `list_functions` | List T-SQL user-defined functions on a warehouse or SQL Analytics Endpoint. |
| `rename_function` | Rename a T-SQL user-defined function via sp_rename. |
| `update_function` | Redefine a T-SQL user-defined function via CREATE OR ALTER FUNCTION. |


## Snapshots

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw snapshots create` | Create a new snapshot named NAME for ITEM (warehouse). |
| `fdw snapshots delete` | Delete SNAPSHOT (accepts name or GUID). |
| `fdw snapshots list` | List all snapshots for ITEM (warehouse). |
| `fdw snapshots rename` | Rename SNAPSHOT to NEW_NAME (snapshot accepts name or GUID). |
| `fdw snapshots roll` | Roll SNAPSHOT_NAME on ITEM (warehouse) to a new timestamp. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `create_snapshot` | Create a new warehouse snapshot. |
| `delete_snapshot` | Delete a warehouse snapshot. |
| `list_snapshots` | Return all snapshots belonging to a warehouse. |
| `rename_snapshot` | Rename a warehouse snapshot. |
| `roll_snapshot_timestamp` | Roll a snapshot's timestamp forward (or reset to current). |


## Restore points

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw restore-points create` | Create a restore point for ITEM (warehouse) at the current timestamp. |
| `fdw restore-points delete` | Delete RESTORE_POINT_ID on ITEM (warehouse). |
| `fdw restore-points get` | Get a restore point by RESTORE_POINT_ID for ITEM (warehouse). |
| `fdw restore-points list` | List all restore points for ITEM (warehouse). |
| `fdw restore-points rename` | Rename RESTORE_POINT_ID to NEW_NAME on ITEM (warehouse). |
| `fdw restore-points restore` | Restore ITEM (warehouse) in-place to RESTORE_POINT_ID. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `create_restore_point` | Create a restore point for a warehouse at the current timestamp. |
| `delete_restore_point` | Delete a user-defined restore point. |
| `get_restore_point` | Return a single restore point by ID. |
| `list_restore_points` | Return all restore points for a warehouse. |
| `restore_warehouse_in_place` | Restore a warehouse in-place to a restore point. |
| `update_restore_point` | Rename and/or update the description of a restore point. |


## Audit

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw audit add-group` | Add GROUP to the audit action groups for ITEM (warehouse). |
| `fdw audit disable` | Disable SQL auditing on ITEM (warehouse). |
| `fdw audit enable` | Enable SQL auditing on ITEM (warehouse). |
| `fdw audit get` | Get the current audit settings for ITEM (warehouse). |
| `fdw audit remove-group` | Remove GROUP from the audit action groups for ITEM (warehouse). |
| `fdw audit set-groups` | Set audit action groups for ITEM (warehouse). |
| `fdw audit set-retention` | Update the audit log retention period for ITEM (warehouse). |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `add_audit_group` | Add a single audit action group without overwriting the others. |
| `disable_audit` | Disable SQL auditing on a warehouse. |
| `enable_audit` | Enable SQL auditing on a warehouse. |
| `get_audit_settings` | Fetch the current SQL audit settings for a warehouse. |
| `remove_audit_group` | Remove a single audit action group without overwriting the others. |
| `set_audit_action_groups` | Replace the audited action groups for a warehouse. |
| `set_audit_retention` | Update the audit log retention period without changing the audit enabled/disabled state. |


## Queries

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw queries connections` | List active SQL connections on ITEM (warehouse or endpoint). |
| `fdw queries frequent` | List frequently-run queries from queryinsights.frequently_run_queries. |
| `fdw queries history` | List completed SQL requests from queryinsights.exec_requests_history. |
| `fdw queries kill` | Kill the session SESSION_ID on ITEM (warehouse or endpoint). |
| `fdw queries long-running` | List long-running queries from queryinsights.long_running_queries. |
| `fdw queries running` | List currently running queries on ITEM (warehouse or endpoint). |
| `fdw queries sessions` | List completed sessions from queryinsights.exec_sessions_history. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `kill_session` | Terminate a session on a warehouse by session_id. |
| `list_connections` | Return all active SQL connections on a warehouse or SQL Analytics Endpoint. |
| `list_frequent_queries` | Return frequently-run queries from queryinsights.frequently_run_queries. |
| `list_long_running_queries` | Return long-running queries from queryinsights.long_running_queries. |
| `list_request_history` | Return completed SQL requests from queryinsights.exec_requests_history. |
| `list_running_queries` | Return all currently-executing queries on a warehouse or SQL Analytics Endpoint. |
| `list_session_history` | Return completed sessions from queryinsights.exec_sessions_history. |


## SQL Pools

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw sql-pools create` | Add a new SQL pool to the workspace. |
| `fdw sql-pools delete` | Remove an SQL pool from the workspace. |
| `fdw sql-pools disable` | Disable custom SQL Pools for the workspace (preserves pool configuration). |
| `fdw sql-pools enable` | Enable custom SQL Pools for the workspace (preserves pool configuration). |
| `fdw sql-pools get` | Fetch the SQL Pools configuration for the workspace. |
| `fdw sql-pools insights` | List SQL pool insights from queryinsights.sql_pool_insights. |
| `fdw sql-pools list` | List all SQL pools in the workspace. |
| `fdw sql-pools show` | Show details for a single SQL pool in the workspace. |
| `fdw sql-pools update` | Update an existing SQL pool in the workspace. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `create_sql_pool` | Add a new custom SQL pool to a workspace. |
| `delete_sql_pool` | Delete an SQL pool from a workspace. |
| `disable_sql_pools` | Disable custom SQL Pools for a workspace, preserving pool configuration. |
| `enable_sql_pools` | Enable custom SQL Pools for a workspace without modifying pool definitions. |
| `get_sql_pool` | Return details for a single SQL pool by name. |
| `get_sql_pools_configuration` | Fetch the full SQL Pools configuration (enabled flag + pool list) for a workspace. |
| `list_sql_pool_insights` | Return SQL pool insight events from queryinsights.sql_pool_insights. |
| `list_sql_pools` | Return the list of custom SQL pools for a workspace. |
| `update_sql_pool` | Update an existing SQL pool.  Only the parameters you supply are changed. |


## dbt integration

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw dbt init` | Scaffold a new dbt-fabric project in FOLDER linked to ITEM. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `generate_dbt_profile` | Generate dbt-fabric project file contents for a Fabric Data Warehouse. |


## Cache

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw cache clear` | Clear all cached entries. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `clear_cache` | Erase cached workspace and item name-to-UUID mappings. |


## Configuration

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw config clear` | Wipe all configuration defaults. |
| `fdw config set warehouse` | Set the default WAREHOUSE / SQL Analytics Endpoint (name or GUID). |
| `fdw config set workspace` | Set the default WORKSPACE (name or GUID). |
| `fdw config show` | Show the current configuration defaults. |
| `fdw config unset warehouse` | Clear the default warehouse. |
| `fdw config unset workspace` | Clear the default workspace. |


## Shell completion

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw completion install` | Generate and optionally install the completion script for SHELL. |


## Server-side settings

### CLI commands

| Command | Summary |
| ------- | ------- |
| `fdw settings result-set-caching` | Enable or disable result-set caching on ITEM. |
| `fdw settings retention` | Set the time-travel retention period on ITEM. |
| `fdw settings show` | Show all server-side settings for ITEM. |

### MCP tools

| Tool | Summary |
| ---- | ------- |
| `get_warehouse_settings` | Return the current server-side database settings for a warehouse. |
| `set_result_set_caching` | Enable or disable result-set caching on a warehouse. |
| `set_time_travel_retention` | Set the time-travel retention period on a warehouse. |

