---
title: Audit
---

# Audit

Manage SQL audit settings for Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

**Targets:** Data Warehouse · SQL Analytics Endpoint

---

## CLI

### audit add-group

**Targets:** Data Warehouse · SQL Analytics Endpoint

Add a single audit action group without overwriting the others. Idempotent - if the group is already present the command succeeds without modifying the configuration. Auditing must already be enabled.

**Synopsis**

```
fdw [-w WORKSPACE] audit add-group [WAREHOUSE] GROUP
```

**Example**

```shell
fdw -w MyWorkspace audit add-group SalesWH BATCH_COMPLETED_GROUP
```

---

### audit disable

**Targets:** Data Warehouse · SQL Analytics Endpoint

Disable SQL auditing on a warehouse or SQL Analytics Endpoint.

**Synopsis**

```
fdw [-w WORKSPACE] audit disable [WAREHOUSE]
```

**Example**

```shell
fdw -w MyWorkspace audit disable SalesWH
```

---

### audit enable

**Targets:** Data Warehouse · SQL Analytics Endpoint

Enable SQL auditing on a warehouse or SQL Analytics Endpoint.

**Synopsis**

```
fdw [-w WORKSPACE] audit enable [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--retention-days INTEGER` | Audit log retention in days (>= 1). Mutually exclusive with `--unlimited`. | - |
| `--unlimited` | Set unlimited audit log retention (service value 0). Mutually exclusive with `--retention-days`. | off |

Omitting both `--retention-days` and `--unlimited` defaults to unlimited retention. Passing `0` for `--retention-days` is rejected - use `--unlimited` for no-limit retention.

**Example**

```shell
# Retain logs for 90 days
fdw -w MyWorkspace audit enable --retention-days 90 SalesWH

# Unlimited retention
fdw -w MyWorkspace audit enable --unlimited SalesWH
```

---

### audit get

**Targets:** Data Warehouse · SQL Analytics Endpoint

Get the current audit settings for a warehouse or SQL Analytics Endpoint.

**Synopsis**

```
fdw [-w WORKSPACE] audit get [WAREHOUSE]
```

**Example**

```shell
fdw -w MyWorkspace audit get SalesWH
```

```
state            Enabled
retentionDays    7
actionGroups     BATCH_COMPLETED_GROUP
```

---

### audit remove-group

**Targets:** Data Warehouse · SQL Analytics Endpoint

Remove a single audit action group without overwriting the others. Idempotent - if the group is not present the command succeeds without modifying the configuration. Auditing must already be enabled.

**Synopsis**

```
fdw [-w WORKSPACE] audit remove-group [WAREHOUSE] GROUP
```

**Example**

```shell
fdw -w MyWorkspace audit remove-group SalesWH BATCH_COMPLETED_GROUP
```

---

### audit set-groups

**Targets:** Data Warehouse · SQL Analytics Endpoint

Set the audit action groups for a warehouse or SQL Analytics Endpoint. Pass `--group` / `-g` once per action group. This replaces the existing list of groups.

**Synopsis**

```
fdw [-w WORKSPACE] audit set-groups -g GROUP [-g GROUP ...] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `-g` / `--group TEXT` | Audit action group name. Repeat for multiple groups. (required) |

**Example**

```shell
fdw -w MyWorkspace audit set-groups \
  -g BATCH_COMPLETED_GROUP \
  -g SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP \
  SalesWH
```

---

### audit set-retention

**Targets:** Data Warehouse · SQL Analytics Endpoint

Update the audit log retention period without changing the audit enabled/disabled state. Audit must already be enabled; if it is disabled, run `audit enable` first.

**Synopsis**

```
fdw [-w WORKSPACE] audit set-retention --days INTEGER [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--days INTEGER` | Retention period in days (1–3650; 3650 ≈ 10 years). (required) |

**Example**

```shell
fdw -w MyWorkspace audit set-retention --days 90 SalesWH
```

---

## MCP tools

### add_audit_group

**Targets:** Data Warehouse · SQL Analytics Endpoint

Add a single audit action group without overwriting the others. Idempotent - if the group is already present the current settings are returned unchanged. Auditing must already be enabled.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `group` (`str`): action group name (e.g. `"BATCH_COMPLETED_GROUP"`).

**Returns:** `AuditSettings`: the updated audit settings.

---

### disable_audit

**Targets:** Data Warehouse · SQL Analytics Endpoint

Disable SQL auditing on a warehouse or SQL analytics endpoint.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse or SQL analytics endpoint name or GUID.

**Returns:** `AuditSettings`: the updated audit settings.

---

### enable_audit

**Targets:** Data Warehouse · SQL Analytics Endpoint

Enable SQL auditing on a warehouse or SQL analytics endpoint.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `retention_days` (`int`, default `0`): audit log retention in days; `0` means unlimited.

**Returns:** `AuditSettings`: the updated audit settings.

---

### get_audit_settings

**Targets:** Data Warehouse · SQL Analytics Endpoint

Fetch the current SQL audit settings for a warehouse or SQL analytics endpoint.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse or SQL analytics endpoint name or GUID.

**Returns:** `AuditSettings`: object with `state` (`Enabled` or `Disabled`), `retentionDays`, and `auditActionsAndGroups`.

---

### remove_audit_group

**Targets:** Data Warehouse · SQL Analytics Endpoint

Remove a single audit action group without overwriting the others. Idempotent - if the group is not present the current settings are returned unchanged. Auditing must already be enabled.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `group` (`str`): action group name (e.g. `"BATCH_COMPLETED_GROUP"`).

**Returns:** `AuditSettings`: the updated audit settings.

---

### set_audit_action_groups

**Targets:** Data Warehouse · SQL Analytics Endpoint

Replace the audited action groups for a warehouse or SQL analytics endpoint. This overwrites the existing list of groups.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `action_groups` (`list[str]`): list of audit action group names (e.g. `["BATCH_COMPLETED_GROUP"]`).

**Returns:** `AuditSettings`: the updated audit settings.

---

### set_audit_retention

**Targets:** Data Warehouse · SQL Analytics Endpoint

Update the audit log retention period without changing the audit enabled/disabled state. Audit must already be enabled; if it is disabled, enable it first with `enable_audit`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `days` (`int`): retention period in days (1–3650; 3650 ≈ 10 years).

**Returns:** `AuditSettings`: the updated audit settings.
