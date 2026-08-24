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
| `app_started` | Once per process | - (`auth_mode` omitted - see note below) |
| `mcp_server_started` | When the MCP server boots | - (`auth_mode` omitted - see note below) |
| `app_exited` | On process exit | `duration_ms`, `exit_status` (ok / user_error / api_error), `error_category` |

> **Note on `auth_mode` in lifecycle-start events:** `app_started` and `mcp_server_started` fire at process start, before any token is acquired. Emitting `auth_mode` at that point would produce a possibly-wrong value derived from environment-variable heuristics (e.g. `interactive` for a plain `az login`). The accurate value is only available after the first token acquisition and is emitted on `command_invoked` and `app_exited`.

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

### What is deliberately NOT collected

- SQL text, query results, or row counts
- Workspace, warehouse, schema, table, column, or snapshot names/IDs
- Connection strings or any credentials
- File paths or environment variable values
- Any other personally-identifiable information
- Distributed traces of any kind, including MCP protocol spans
- Metrics of any kind

### No spans or metrics are ever exported

`fabric-dw` emits telemetry as OpenTelemetry **log records**, never as spans. It
sets `OTEL_TRACES_EXPORTER=none` and `OTEL_METRICS_EXPORTER=none` before
initialising Application Insights, so no span exporter and no metric exporter is
built. Only the logs pipeline, which carries the `customEvents` above, is active.

This matters because the MCP Python SDK installs an OpenTelemetry middleware on
every server it creates, and that middleware emits one span per inbound protocol
message. Measured against the version this package pins, those spans carry the
method name (`tools/call`), the protocol revision, the JSON-RPC request ID, the
tool name, an `error.type` marker, and timings.

To be precise about what that is and is not: a failing tool call does **not** put
the exception text on the span. The MCP server converts a failing tool into an
error result before the middleware sees it, so the middleware records only
`error.type="tool_error"` with no message. SQL text, Fabric API error strings and
warehouse names are therefore not in the span payload. What would leak is
metadata: which tool ran, when, how long it took, and whether it failed. That is
still not on the collection list above, which is why the exporters are off.

Two notes for completeness. Both `disable_tracing=True` and `disable_metrics=True`
are also passed to the Azure Monitor configuration, but neither works: the library
overwrites caller-supplied values with its own defaults, which read only the
environment variables. The environment variables are the mechanism; the arguments
are a statement of intent. And a server that registered MCP *prompts* would leak
one more thing, the client-supplied prompt name, which the middleware copies into
the span name verbatim. `fabric-dw` registers no prompts and no resources, so this
does not arise here.

## Where telemetry data goes

Events are sent to a private Azure Application Insights resource operated by the `fabric-dw` maintainers, via a write-only connection string embedded in the package. The backing Log Analytics workspace has a daily ingestion cap to control costs.

## How to opt out

Any of the following fully disables telemetry - no events are emitted and the SDK is never imported:

| Method | How |
|---|---|
| Environment variable | Set `FABRIC_DW_TELEMETRY_OPT_OUT` to any value except the falsy set (`""`, `0`, `false`, `no`, `off`, case-insensitive). Setting it to `0` or `false` does **not** opt out. |
| Do Not Track | Set `DO_NOT_TRACK` to any value except the falsy set (same rules as above). |
| CLI command | Run `fdw config set telemetry disabled true`. To re-enable, run `fdw config set telemetry disabled false` or `fdw config unset telemetry disabled`. |
| Config file | Add `disabled = true` under a `[telemetry]` section in `$XDG_CONFIG_HOME/fabric-dw/config.toml` |
