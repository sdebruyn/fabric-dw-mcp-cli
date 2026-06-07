---
title: Troubleshooting
---

# Troubleshooting

This page collects failure modes that real users have encountered, with the exact error message and the resolution.

---

## `az login` expired / no token

**Error you see:**

```
CredentialUnavailableError: Azure CLI not found on path.
```

or

```
CredentialUnavailableError: Please run 'az login' to set up an account.
```

**What happened:** `fabric-dw` authenticates through the `DefaultAzureCredential` chain, which relies on the Azure CLI token cache. The cached token has expired or you have not logged in yet.

**Resolution:**

```bash
az login
```

If your organisation uses multiple Entra tenants, specify the tenant explicitly so the cached token is scoped correctly:

```bash
az login --tenant <tenant-id-or-domain>
```

Then retry your `fabric-dw` command. The credential chain picks up the refreshed token automatically.

---

## 403 PermissionDenied on a workspace call

**Error you see:**

```
fabric_dw.exceptions.PermissionDenied: Permission denied for https://api.fabric.microsoft.com/v1/workspaces/<id>/...: ...
```

or, from the SQL driver:

```
fabric_dw.exceptions.PermissionDenied: permission was denied on the object ...
```

**What happened:** Your account does not have the required role in the Fabric workspace. Workspace-level REST calls require at least the **Contributor** role; some write operations require **Member** or **Admin**.

**Resolution:** Ask the workspace owner to grant you Contributor (or Member) access in the Fabric portal under **Workspace settings → People and groups**.

---

## Capacity paused — cryptic 5xx or 404 errors

**Symptoms:** Commands fail with `FabricServerError` (HTTP 5xx) or `NotFound` (HTTP 404) even though the workspace and warehouse clearly exist. The Fabric portal may show the capacity as **Paused**.

**What happened:** Microsoft Fabric capacities can be paused to save cost. While paused, the Fabric REST API returns unreliable error codes instead of a clear "capacity is paused" message.

**Resolution:** Resume the capacity before running commands.

Using the Azure CLI (the same command the CI pipeline uses):

```bash
az resource invoke-action \
  --ids "<capacity-resource-id>" \
  --action resume
```

Wait for the capacity to reach the **Active** state:

```bash
az resource show \
  --ids "<capacity-resource-id>" \
  --query 'properties.state' -o tsv
```

Alternatively, resume from the [Fabric portal](https://app.fabric.microsoft.com) under **Capacity settings**.

---

## mssql-python "authentication failed"

**Error you see:**

```
fabric_dw.exceptions.AuthError: authentication failed for server ...
```

or a raw driver message containing `Login failed` or `28000`.

**What happened:** The SQL driver connects to the warehouse using an Entra (Azure AD) access token obtained from the Azure CLI cache. If the cached CLI token has expired since the process started, the driver cannot authenticate.

**Resolution:**

1. Re-run `az login` (or `az login --force` to force a fresh interactive login):

   ```bash
   az login --force
   ```

2. If you have a stale MSAL token cache, clear it:

   ```bash
   az account clear
   az login
   ```

3. Retry your command. The `FabricSqlClient` opens a new connection after a restart and will pick up the fresh token.

---

## 429 RateLimitedError

**Error you see:**

```
fabric_dw.exceptions.RateLimitedError: Received 429 5 consecutive times for https://api.fabric.microsoft.com/v1/...
```

**What happened:** The Fabric REST API enforces a rate limit. `fabric-dw` honours the `Retry-After` response header and automatically backs off, but if the API returns 429 more than 5 consecutive times the client raises `RateLimitedError` rather than waiting indefinitely. The internal rate limiter is set to **2 RPS**.

**Resolution:**

- If you hit this during a single command, simply retry — the capacity or the API may have been temporarily overloaded.
- If you are running `fabric-dw` commands in a loop or in parallel, reduce concurrency so that your effective request rate stays below the 2 RPS cap.
- Wait a few minutes before retrying if the API continues to throttle.

The client automatically retries on each 429 and waits exactly as long as the server requests, so transient throttling is usually transparent.

---

## Restore points not appearing

**Symptom:** `fabric-dw snapshot list` returns an empty list, or user-defined restore points that you created are not visible.

**What happened:** Warehouse restore points are tied to capacity state:

- **User-defined restore points** can only be created while the capacity is in the **Active** state. If the capacity was paused at creation time, the restore point was not persisted.
- **System-created restore points** are generated automatically every **8 hours**, but only while the capacity is Active. Gaps in system points indicate the capacity was paused during that window.

**Resolution:**

1. Confirm the capacity is Active (see [Capacity paused](#capacity-paused-cryptic-5xx-or-404-errors) above).
2. Create a new user-defined restore point while the capacity is Active.
3. If you expected a system restore point from a period when the capacity was paused, that point does not exist — it was not created.

---

## MCP server doesn't show tools

**Symptom:** After adding `fabric-dw-mcp` to your AI tool's MCP config, the tool list is empty or the server does not appear.

**Steps to diagnose:**

1. **Verify the binary works locally:**

   ```bash
   fabric-dw-mcp --help
   ```

   If this fails with `command not found`, re-run `pip install fabric-dw` and make sure the install target's `bin/` directory is on your `PATH`.

2. **Check environment variables:** The MCP server requires the same Azure CLI credentials as the CLI. Make sure the process launched by your AI tool inherits the correct environment. See the [MCP reference](mcp.md) for the full list of required variables.

3. **Restart the MCP client:** Most AI tools (Claude Desktop, Cursor, VS Code) cache the tool list at startup. After updating the config or reinstalling the package, fully quit and reopen the application.

4. **Check the client logs:** Look for stderr output from the `fabric-dw-mcp` process in your AI tool's log folder — startup errors (missing env vars, import failures) are printed there.
