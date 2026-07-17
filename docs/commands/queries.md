---
title: Queries
---

# Queries

Inspect and manage running queries on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

**Targets:** Data Warehouse / SQL Analytics Endpoint

## CLI

### queries connections

**Targets:** Data Warehouse / SQL Analytics Endpoint

List all active SQL connections on a warehouse or SQL Analytics Endpoint. This queries `sys.dm_exec_connections` and shows lower-level connection info (including idle connections) that is not visible in `queries running`.

**Synopsis**

```
fdw [-w WORKSPACE] queries connections [WAREHOUSE]
```

**Example**

```shell
fdw -w MyWorkspace queries connections SalesWH
```

```
 session_id  connect_time          client_net_address  auth_scheme  encrypt_option  net_transport  most_recent_session_id
 ----------  --------------------  ------------------  -----------  --------------  -------------  ----------------------
 10          2026-06-08T10:00:00Z  192.168.1.100       NTLM         TRUE            TCP            10
 20          2026-06-08T10:01:00Z  192.168.1.101       KERBEROS     FALSE           TCP            20
```

### queries frequent

**Targets:** Data Warehouse / SQL Analytics Endpoint

List frequently-run queries from `queryinsights.frequently_run_queries`.

> **Note:** Elapsed-time fields (e.g. `avg_total_elapsed_time_ms`, `min_run_total_elapsed_time_ms`, `max_run_total_elapsed_time_ms`, `last_run_total_elapsed_time_ms`) are typed as `float` (`number` in JSON) because Fabric returns fractional millisecond values. Count fields (`number_of_runs`, `number_of_successful_runs`, `number_of_failed_runs`, `number_of_canceled_runs`) remain `int`.

**Synopsis**

```
fdw [-w WORKSPACE] queries frequent [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--limit INTEGER` | Maximum rows to return (1–10 000). | `100` |
| `--since ISO8601` | Return rows with last_run_start_time >= this value. Mutually exclusive with `--ago`. | - |
| `--until ISO8601` | Return rows with last_run_start_time <= this value. | - |
| `--ago DURATION` | Relative alternative to `--since`: rows newer than now minus this duration (e.g. `1h`, `90m`, `3600s`, `2d`). Mutually exclusive with `--since`. | - |

**Example**

```shell
fdw -w MyWorkspace queries frequent SalesWH --limit 20
fdw -w MyWorkspace queries frequent SalesWH --ago 1h
```

### queries history

**Targets:** Data Warehouse / SQL Analytics Endpoint

List completed SQL requests from `queryinsights.exec_requests_history`. Supports optional time-range filtering with `--since` and `--until` (ISO-8601 strings). The `--limit` option caps the number of rows returned (default: 100, max: 10 000).

> **Note:** Elapsed-time and CPU-time fields (e.g. `total_elapsed_time_ms`, `allocated_cpu_time_ms`) are typed as `float` (`number` in JSON) because Fabric returns fractional millisecond values. Count fields (e.g. `row_count`) remain `int`.

**Synopsis**

```
fdw [-w WORKSPACE] queries history [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--limit INTEGER` | Maximum rows to return (1–10 000). | `100` |
| `--since ISO8601` | Return rows with timestamp >= this value. Mutually exclusive with `--ago`. | - |
| `--until ISO8601` | Return rows with timestamp <= this value. | - |
| `--ago DURATION` | Relative alternative to `--since`: rows newer than now minus this duration (e.g. `1h`, `90m`, `3600s`, `2d`). Mutually exclusive with `--since`. | - |

**Example**

```shell
fdw -w MyWorkspace queries history SalesWH --limit 50 --since 2026-06-01T00:00:00
fdw -w MyWorkspace queries history SalesWH --ago 1h
```

### queries kill

**Targets:** Data Warehouse / SQL Analytics Endpoint

Kill a specific session on a warehouse or SQL Analytics Endpoint. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] queries kill [WAREHOUSE] SESSION_ID
```

**Example**

```shell
fdw -w MyWorkspace --yes queries kill SalesWH 42
```

### queries locks

**Targets:** Data Warehouse / SQL Analytics Endpoint

List active locks from `sys.dm_tran_locks`, joined with `sys.dm_exec_requests` to show blocking info. DATABASE-scoped rows are excluded by default to reduce noise from idle connections.

**Synopsis**

```
fdw [-w WORKSPACE] queries locks [OPTIONS] [ITEM]
```

| Option | Description | Default |
| --- | --- | --- |
| `--limit INTEGER` | Maximum rows to return (1-10 000). | `100` |
| `--waiting-only` | Only show locks with `request_status = WAIT`. | off |
| `--blocked-only` | Only show sessions blocked by another session. | off |
| `--include-database` | Include DATABASE-scoped lock rows. | off |

**Example**

```shell
fdw -w MyWorkspace queries locks SalesWH
fdw -w MyWorkspace queries locks SalesWH --waiting-only
fdw -w MyWorkspace queries locks SalesWH --blocked-only --limit 50
```

### queries long-running

**Targets:** Data Warehouse / SQL Analytics Endpoint

List long-running queries from `queryinsights.long_running_queries`.

> **Note:** `median_total_elapsed_time_ms` and `last_run_total_elapsed_time_ms` are typed as `float` (`number` in JSON) because Fabric returns fractional millisecond values. `number_of_runs` remains `int`.

**Synopsis**

```
fdw [-w WORKSPACE] queries long-running [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--limit INTEGER` | Maximum rows to return (1–10 000). | `100` |
| `--since ISO8601` | Return rows with last_run_start_time >= this value. Mutually exclusive with `--ago`. | - |
| `--until ISO8601` | Return rows with last_run_start_time <= this value. | - |
| `--ago DURATION` | Relative alternative to `--since`: rows newer than now minus this duration (e.g. `1h`, `90m`, `3600s`, `2d`). Mutually exclusive with `--since`. | - |

**Example**

```shell
fdw -w MyWorkspace queries long-running SalesWH
fdw -w MyWorkspace queries long-running SalesWH --ago 2d
```

### queries running

**Targets:** Data Warehouse / SQL Analytics Endpoint

List all currently running queries on a warehouse or SQL Analytics Endpoint.

**Synopsis**

```
fdw [-w WORKSPACE] queries running [WAREHOUSE]
```

**Example**

```shell
fdw -w MyWorkspace queries running SalesWH
```

```
 sessionId   loginName   startTime             commandText
 ----------- ----------- --------------------- -------------------------
 42          user@co.io  2026-06-08T10:01:00Z  SELECT * FROM sales ...
```

### queries sessions

**Targets:** Data Warehouse / SQL Analytics Endpoint

List completed sessions from `queryinsights.exec_sessions_history`.

> **Note:** `total_query_elapsed_time_ms` is typed as `float` (`number` in JSON) because Fabric returns fractional millisecond values.

**Synopsis**

```
fdw [-w WORKSPACE] queries sessions [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--limit INTEGER` | Maximum rows to return (1–10 000). | `100` |
| `--since ISO8601` | Return rows with session_start_time >= this value. Mutually exclusive with `--ago`. | - |
| `--until ISO8601` | Return rows with session_start_time <= this value. | - |
| `--ago DURATION` | Relative alternative to `--since`: rows newer than now minus this duration (e.g. `1h`, `90m`, `3600s`, `2d`). Mutually exclusive with `--since`. | - |

**Example**

```shell
fdw -w MyWorkspace queries sessions SalesWH
fdw -w MyWorkspace queries sessions SalesWH --ago 90m
```

## MCP tools

The following four tools query the `queryinsights` schema DMVs via TDS. They share the same parameter shape - `workspace`, `warehouse`, optional `limit`, optional `since`, and optional `until`.

### kill_session

**Targets:** Data Warehouse / SQL Analytics Endpoint

Terminate a session on a warehouse.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.
- `session_id` (`int`): the session ID to terminate.

**Returns:** `{ "killed": true, "session_id": int }`: confirmation with the terminated session ID.

### list_connections

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return all active SQL connections on a warehouse or SQL Analytics Endpoint. Queries `sys.dm_exec_connections`, which includes idle connections not visible via `list_running_queries`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.

**Returns:** `list[Connection]`: array of connection objects, each with `session_id`, `connect_time`, `client_net_address`, `auth_scheme`, `encrypt_option`, and `net_transport`.

### list_frequent_queries

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return frequently-run queries from `queryinsights.frequently_run_queries`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse or SQL Analytics Endpoint name or GUID.
- `limit` (`int`, default `100`): maximum rows to return (1–10 000).
- `since` (`str | null`, optional): ISO-8601 lower bound on `last_run_start_time`.
- `until` (`str | null`, optional): ISO-8601 upper bound on `last_run_start_time`.

**Returns:** `list[dict]`: array of frequently-run query row objects. Elapsed-time fields (e.g. `avg_total_elapsed_time_ms`, `min_run_total_elapsed_time_ms`, `max_run_total_elapsed_time_ms`, `last_run_total_elapsed_time_ms`) are JSON `number` (float); count fields remain `integer`.

### list_locks

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return active lock rows from `sys.dm_tran_locks` joined with `sys.dm_exec_requests`. DATABASE-scoped rows are excluded by default.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL Analytics Endpoint name or GUID.
- `limit` (`int`, default `100`): maximum rows to return (1-10 000).
- `waiting_only` (`bool`, default `false`): restrict to locks with `request_status = 'WAIT'`.
- `blocked_only` (`bool`, default `false`): restrict to sessions blocked by another session.
- `include_database` (`bool`, default `false`): include DATABASE-scoped lock rows.

**Returns:** `list[dict]`: array of lock row objects, each with `session_id`, `resource_type`, `request_mode`, `request_status`, `schema_name`, `object_name`, `blocking_session_id`, `wait_type`, `wait_time` (ms), and `command`.

### list_long_running_queries

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return long-running queries from `queryinsights.long_running_queries`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse or SQL Analytics Endpoint name or GUID.
- `limit` (`int`, default `100`): maximum rows to return (1–10 000).
- `since` (`str | null`, optional): ISO-8601 lower bound on `last_run_start_time`.
- `until` (`str | null`, optional): ISO-8601 upper bound on `last_run_start_time`.

**Returns:** `list[dict]`: array of long-running query row objects. `median_total_elapsed_time_ms` and `last_run_total_elapsed_time_ms` are JSON `number` (float); `number_of_runs` remains `integer`.

### list_request_history

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return completed SQL requests from `queryinsights.exec_requests_history`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse or SQL Analytics Endpoint name or GUID.
- `limit` (`int`, default `100`): maximum rows to return (1–10 000).
- `since` (`str | null`, optional): ISO-8601 lower bound on `submit_time`.
- `until` (`str | null`, optional): ISO-8601 upper bound on `submit_time`.

**Returns:** `list[dict]`: array of request-history row objects. Elapsed-time and CPU-time fields (e.g. `total_elapsed_time_ms`, `allocated_cpu_time_ms`) are JSON `number` (float) because Fabric returns fractional millisecond values.

### list_running_queries

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return all currently-executing queries on a warehouse.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.

**Returns:** `list[RunningQuery]`: array of query objects, each with `session_id`, `request_id`, `status`, `start_time`, `total_elapsed_time` (ms), `login_name`, `command`, and `query_text`.

### list_session_history

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return completed sessions from `queryinsights.exec_sessions_history`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse or SQL Analytics Endpoint name or GUID.
- `limit` (`int`, default `100`): maximum rows to return (1–10 000).
- `since` (`str | null`, optional): ISO-8601 lower bound on `session_start_time`.
- `until` (`str | null`, optional): ISO-8601 upper bound on `session_start_time`.

**Returns:** `list[dict]`: array of session-history row objects. `total_query_elapsed_time_ms` is JSON `number` (float) because Fabric returns fractional millisecond values.
