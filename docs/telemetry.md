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

### MCP protocol spans

When you run the MCP server, `fabric-dw` also collects the OpenTelemetry spans
the MCP Python SDK produces: one span per inbound protocol message. This is
instrumentation the SDK maintains, so it covers every message type and keeps
covering new ones as the protocol grows, which hand-written events do not. Over
`command_invoked` it adds the non-tool protocol methods (`initialize`,
`tools/list`, `prompts/list`, ...), the negotiated protocol revision, exact
timings rather than buckets, and a per-message error classification.

The CLI produces no protocol spans and therefore installs no trace pipeline at
all.

Each exported span carries a fixed set of fields and nothing else:

| Field | Description |
|---|---|
| span name | The protocol method, e.g. `tools/call`. |
| span kind | Inbound message, or a request the server sent to the client. |
| start and end time | The message's duration. |
| `mcp.method.name` | The protocol method again, as a queryable field. |
| `mcp.protocol.version` | The negotiated protocol revision, e.g. `2025-06-18`. |
| `jsonrpc.request.id` | The request's numeric id, for correlating a request with its response. |
| `gen_ai.operation.name` | `execute_tool` on a `tools/call`, absent otherwise. |
| `error.type` | A JSON-RPC error code, `tool_error`, or the class name of a server-side exception. |
| `rpc.response.status_code` | The JSON-RPC error code, when the message failed. |
| trace and span IDs | Random identifiers, so a message's spans can be correlated with each other. |

Spans do not carry the event envelope listed at the top of this page. What they
do carry alongside the table above is the same resource description every event
carries: `anonymous_install_id`, `app_version`, and the surface (`mcp`).

#### What is stripped before a span is exported

Anything on a span that the client chose is removed. This is not hypothetical:
several such values land on a span as produced, and the values are as free-form
as whatever the sender typed.

- **The client-supplied name on a request.** A `prompts/get` or `tools/call`
  names what it wants, and the SDK copies that name into the span name and into
  a `gen_ai.prompt.name` / `gen_ai.tool.name` attribute. Registering no prompts
  is not protection: the resulting `Unknown prompt: <name>` error puts the same
  value into the span's status description as well. The span name is rebuilt
  from the method alone and both attributes are dropped. Which tool ran is
  already recorded, by name, on `command_invoked`.
- **Error messages and stack traces.** The status description is dropped and
  span events are dropped entirely, which is what removes the exception message
  and the full Python stack trace the SDK attaches on some failure paths. The
  `error.type` classification above is kept in their place.
- **A method name the protocol does not define.** The instrumentation covers
  the "method not found" path too, so a client calling a made-up method would
  otherwise have that string exported. Method names are checked against the
  protocol's own list and anything else is recorded as `<unknown>`, which still
  counts the call without quoting it.
- **A non-numeric JSON-RPC request id.** The id is the client's to choose and
  may be any string, so only an integer id is recorded.
- **Everything else.** Spans are rebuilt from the allowlist in the table above
  rather than filtered, so a field a future SDK release adds is dropped until it
  has been looked at.

#### Spans from anything other than the MCP SDK are never exported

The export path drops every span that did not come from the MCP SDK's own
tracer. All of OpenTelemetry's automatic instrumentation is switched off in this
package, so nothing else produces spans in the first place, but the drop is
unconditional so that stays true no matter what a future dependency starts
doing. HTTP client instrumentation in particular would attach request URLs, and
this project's URLs contain workspace and warehouse identifiers.

One qualifier, because it is about this package and not about your process: if
you embed `fabric-dw` in an application that has already set up its own
OpenTelemetry `TracerProvider`, that provider keeps collecting spans and sending
them wherever you configured, and `fabric-dw` leaves it alone rather than
replacing it. Its own pipeline then stands down entirely, so in that case the
MCP spans go to your destination and not to the maintainers.

### `fabric-dw` exports no metrics

`fabric-dw` forces `OTEL_METRICS_EXPORTER=none` around the Application Insights
setup call, so no metric exporter is built. `OTEL_TRACES_EXPORTER` is forced to
`none` there too, because the exporter that call would install ships spans
exactly as produced, including everything listed above as stripped; the trace
pipeline is built separately afterwards, with the filtering in front of it.

Those two variables are set unconditionally rather than deferring to a value you
may already have in your environment, and they are restored to whatever they were
immediately afterwards, so nothing else in your process is affected. The reason
they are not merely defaulted is that the Azure Monitor library reads them only
to check for the exact string `none`: any other value, including an empty string
or `otlp`, leaves the pipeline on and installs the **Azure Monitor** exporter
rather than the one you asked for. Deferring would therefore have handed you no
control while quietly turning an unfiltered export path back on.
`OTEL_LOGS_EXPORTER` is left alone: setting it to `none` yourself switches off
`fabric-dw`'s own events, which is a choice worth respecting.

Both `disable_tracing=True` and `disable_metrics=True` are also passed to the
Azure Monitor configuration, but neither works: the library overwrites
caller-supplied values with its own defaults, which read only the environment
variables. The environment variables are the mechanism; the arguments are a
statement of intent.

There is a second, separately gated pipeline that the exporter library starts on
its own: **customer sdkstats**, a delivery-statistics channel that reports how
many telemetry items succeeded, dropped, or were retried. Despite the name it is
not covered by the statsbeat switch, and it builds its own metric exporter, meter
provider, and a reader on a 15-minute timer, all pointed at the same connection
string. A CLI command finishes long before the first export cycle, but an MCP
server does not, and that is this project's main mode. `fabric-dw` therefore sets
`APPLICATIONINSIGHTS_SDKSTATS_DISABLED=true` unless you have already given that
variable a value of your own.

### What is deliberately NOT collected

- SQL text, query results, or row counts
- Workspace, warehouse, schema, table, column, or snapshot names/IDs
- Connection strings or any credentials
- File paths or environment variable values
- Any other personally-identifiable information
- Any value chosen by whoever composed an MCP request: prompt names, the name on
  a call for a tool that does not exist, unknown method names, or a string
  JSON-RPC request id (see above)
- Spans from anything other than the MCP SDK's protocol instrumentation
- Metrics of any kind, including the SDK's own delivery-statistics counters

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
