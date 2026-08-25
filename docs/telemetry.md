# Telemetry

`fabric-dw` collects **opt-out usage telemetry** to understand how the tool is used and to prioritise improvements.

## What is collected

Every telemetry event includes a shared envelope of standard fields:

| Field | Description |
|---|---|
| `anonymous_install_id` | Random UUID generated once and stored in the config directory. Used to count unique installations without identifying the user. |
| `session_id` | Random UUID per process run. Used to group events within a single invocation. |
| `app_version` | The installed version of `fabric-dw`. |
| `python_version` | Python major.minor (e.g. `3.12`). |
| `os` | Operating system (e.g. `linux`, `darwin`, `windows`). |
| `arch` | CPU architecture (e.g. `arm64`, `x86_64`). |
| `install_method` | Best-effort detection: `pip`, `uv`, `pipx`, or `source`. |
| `surface` | `cli` or `mcp`: which interface was used. |
| `auth_mode` | Categorical authentication mode: `service_principal`, `github_oidc`, `azure_cli`, `interactive`, or `managed_identity`. **Never credentials.** |
| `tenant_id` | Your Azure (Entra) tenant ID. |

### Lifecycle events

| Event | When | Extra fields |
|---|---|---|
| `app_started` | Once per process | - |
| `mcp_server_started` | When the MCP server boots | - |
| `mcp_client_connected` | First time each distinct MCP client identifies itself, for the first 8 distinct names a server process sees. After that they all count as one client, `other`. | `mcp_client` |
| `app_exited` | On process exit | `duration_ms`, `exit_status` (ok / user_error / api_error), `error_category` |

> `auth_mode` is omitted from the three start events: they fire before any sign-in, so there is nothing accurate to report yet. It appears on `command_invoked` and `app_exited`.

### `command_invoked`: per-command usage

One `command_invoked` event is emitted after every CLI command and every MCP tool call completes (success or failure).

| Field | Description |
|---|---|
| `name` | Command name. CLI: `<group>.<subcommand>` (e.g. `warehouses.list`). MCP: tool name (e.g. `create_table`). Never SQL text or identifiers. |
| `domain` | Rolled-up feature area (see table below). |
| `surface` | `cli` or `mcp`. |
| `status` | `success`, `user_error` (validation/usage problems), or `api_error` (HTTP/driver/unexpected). |
| `duration_ms_bucket` | Bucketed wall-clock duration: `<100ms`, `<1s`, `<10s`, or `>10s`. |
| `destructive_op` | `true` only for permanently-destructive MCP tools (delete, clear, restore in-place). Omitted otherwise. |
| `mcp_client` | The name the connected MCP client reports for itself (e.g. `claude-ai`), capped at 64 characters. MCP only; `unknown` when the client gives no name, and `other` past the first 8 distinct names a server process sees. |

#### Domain rollup

| Domain | CLI group(s) | Representative MCP tools |
|---|---|---|
| `workspaces` | `workspaces` | `list_workspaces`, `get_workspace`, `set_workspace_collation` |
| `warehouses` | `warehouses` | `list_warehouses`, `create_warehouse`, `delete_warehouse`, … |
| `sql_endpoints` | `sql-endpoints` | `list_sql_endpoints`, `get_sql_endpoint`, … |
| `sql` | `sql` | `execute_sql` |
| `tables` | `tables` | `list_tables`, `create_table`, `delete_table`, … |
| `views` | `views` | `list_views`, `create_view`, `drop_view`, … |
| `procedures` | `procedures` | `list_procedures`, `create_procedure`, `drop_procedure`, … |
| `functions` | `functions` | `list_functions`, `create_function`, `drop_function`, … |
| `schemas` | `schemas` | `list_schemas`, `create_schema`, `delete_schema` |
| `statistics` | `statistics` | `list_statistics`, `create_statistics`, `delete_statistics` |
| `snapshots` | `snapshots` | `list_snapshots`, `create_snapshot`, `delete_snapshot`, … |
| `restore_points` | `restore-points` | `list_restore_points`, `create_restore_point`, `restore_warehouse_in_place`, … |
| `audit` | `audit` | `get_audit_settings`, `enable_audit`, `disable_audit`, … |
| `queries` | `queries` | `list_running_queries`, `kill_session`, `list_request_history`, … |
| `sql_pools` | `sql-pools` | `list_sql_pools`, `create_sql_pool`, `delete_sql_pool`, … |
| `dbt` | `dbt` | `generate_dbt_profile` |
| `cache` | `cache` | `clear_cache` |
| `config` | `config` | - |
| `completion` | `completion` | - |

### MCP protocol messages

Running the MCP server also records one entry per protocol message it handles, which covers the messages that are not tool calls (`initialize`, `tools/list`, and so on).

| Field | Description |
|---|---|
| method | The protocol method, e.g. `tools/call`. A method the MCP protocol does not define is recorded as `<unknown>`, never as the text that was sent. |
| duration | How long the message took to handle. |
| outcome | Whether it succeeded, and if not, the error code or the name of the error type. |
| protocol version | The MCP revision the client and the server agreed on, e.g. `2025-06-18`. |
| parent entry | Which other entry from this same server caused this one, so a sequence reads as one operation. Only ever an entry this server created itself; a parent a client puts in a request is dropped. |

Anything you choose the content of is removed from these entries first: prompt and tool names, error messages, stack traces, and request IDs that are not plain numbers. A trace ID a client puts in a request is replaced by a hash of it, keyed with a random value this process never stores or sends, so entries from one trace still group together but the exported ID is not one you chose or can recover.

### What is deliberately NOT collected

- SQL text, query results, or row counts
- Workspace, warehouse, schema, table, column, or snapshot names/IDs
- Connection strings or any credentials
- File paths or environment variable values
- Any other personally-identifiable information
- Names you put in an MCP request: prompt names, and the tool name on a call for a tool that does not exist
- Error messages and stack traces, and trace IDs taken off the wire (replaced by the keyed hash described above)
- Metrics of any kind

## Where telemetry data goes

Events are sent to a private Azure Application Insights resource operated by the `fabric-dw` maintainers, via a write-only connection string embedded in the package. The backing Log Analytics workspace has a daily ingestion cap to control costs. The events above are the only thing sent there: the Azure SDK's background channels are all switched off, so it reports nothing about itself, does not poll Microsoft for settings that would change how this installation behaves, and does not probe the machine it runs on.

## How to opt out

Any of the following fully disables telemetry - no events are emitted and the SDK is never imported:

| Method | How |
|---|---|
| Environment variable | Set `FABRIC_DW_TELEMETRY_OPT_OUT` to any value except the falsy set (`""`, `0`, `false`, `no`, `off`, case-insensitive). Setting it to `0` or `false` does **not** opt out. |
| Do Not Track | Set `DO_NOT_TRACK` to any value except the falsy set (same rules as above). |
| CLI command | Run `fdw config set telemetry disabled true`. To re-enable, run `fdw config set telemetry disabled false` or `fdw config unset telemetry disabled`. |
| Config file | Add `disabled = true` under a `[telemetry]` section in `$XDG_CONFIG_HOME/fabric-dw/config.toml` |
