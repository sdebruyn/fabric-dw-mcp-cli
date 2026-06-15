# Telemetry

`fabric-dw` collects **anonymous, opt-out usage telemetry** to understand how the tool is used and to prioritise improvements.

## What is collected

Every telemetry event includes a shared envelope of anonymous fields:

| Field | Description |
|---|---|
| `anonymous_install_id` | Random UUID generated once and stored in the config directory. Used to count unique installations without identifying the user. |
| `session_id` | Random UUID per process run. Used to group events within a single invocation. |
| `app_version` | The installed version of `fabric-dw`. |
| `python_version` | Python major.minor (e.g. `3.12`). |
| `os` | Operating system (e.g. `linux`, `darwin`, `windows`). |
| `arch` | CPU architecture (e.g. `arm64`, `x86_64`). |
| `install_method` | Best-effort detection: `pip`, `uv`, `pipx`, or `source`. |
| `surface` | `cli` or `mcp` — which interface was used. |
| `is_ci` | Whether a CI environment was detected. |
| `auth_mode` | Categorical authentication mode: `service_principal`, `github_oidc`, `azure_cli`, or `interactive`. **Never credentials.** |
| `tenant_id` | Your Azure tenant ID, read from `AZURE_TENANT_ID` or `FABRIC_INTERACTIVE_TENANT_ID` **only when telemetry is enabled**. This identifies the organisation (not an individual). |

### Lifecycle events

| Event | When | Extra fields |
|---|---|---|
| `app_started` | Once per process | — |
| `mcp_server_started` | When the MCP server boots | — |
| `app_exited` | On process exit | `duration_ms`, `exit_status` (ok / user_error / api_error), `error_category` |

> **Note:** Per-command usage tracking (`command_invoked`) is a planned follow-up (#367) and is **not** included in this release.

### What is deliberately NOT collected

- SQL text, query results, or row counts
- Workspace, warehouse, schema, table, column, or snapshot names/IDs
- Connection strings or any credentials
- File paths or environment variable values
- Any other personally-identifiable information

`tenant_id` is the only organisation-identifying field and is only emitted when telemetry is enabled (it is omitted when you opt out).

## Where telemetry data goes

Events are sent to **Azure Application Insights** (`appi-fabric-dw-mcp-cli`, `westeurope`), owned and operated by the `fabric-dw` maintainers. Data is ingested via a write-only connection string embedded in the package. The backing Log Analytics workspace has a daily ingestion cap to control costs.

## How to opt out

Any of the following fully disables telemetry — no events are emitted and the SDK is never imported:

| Method | How |
|---|---|
| Environment variable | `FABRIC_DISABLE_TELEMETRY=1` |
| Environment variable | `FABRIC_TELEMETRY=0` (also accepts `false`, `no`, `off`) |
| Console Do Not Track | `DO_NOT_TRACK=1` ([consoledonottrack.com](https://consoledonottrack.com)) |
| CI detection | Automatic when `CI`, `GITHUB_ACTIONS`, `TRAVIS`, `CIRCLECI`, `GITLAB_CI`, `JENKINS_URL`, or `TF_BUILD` is set |
| Config file | Add `[telemetry]\ndisabled = true` to `$XDG_CONFIG_HOME/fabric-dw/config.toml` |

Telemetry is **always off in CI environments** — no action needed for automated pipelines.
