---
title: Troubleshooting
---

# Troubleshooting

This page collects failure modes that real users have encountered, with the exact error message and the resolution.

## `az login` expired / no token

**Error you see:**

```
CredentialUnavailableError: Azure CLI not found on path.
```

or

```
CredentialUnavailableError: Please run 'az login' to set up an account.
```

**What happened:** `fabric-dw` authenticates through the [`DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential?WT.mc_id=MVP_310840) chain (used by `FABRIC_AUTH=default`). The chain walks several sources - environment variables, Workload Identity, Managed Identity, shared token cache, Azure CLI, Azure Developer CLI, Azure PowerShell - and stops at the first that returns a token. The error above means every source was exhausted without finding one, which usually means the Azure CLI session has expired or you have not run `az login` yet. See [Authentication](install.md#authentication) for the full credential chain.

**Resolution:**

```bash
az login
```

If your organisation uses multiple Entra tenants, specify the tenant explicitly so the cached token is scoped correctly:

```bash
az login --tenant <tenant-id-or-domain>
```

Then retry your `fabric-dw` command. The credential chain picks up the refreshed token automatically.

Or use any other source listed in [Authentication](authentication.md). Set `AZURE_LOG_LEVEL=debug` to see which source the chain tried.

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

## Capacity paused - cryptic 5xx or 404 errors

**Symptoms:** Commands fail even though the workspace and warehouse clearly exist. The Fabric portal may show the capacity as **Paused**.

**What happened:** Microsoft Fabric capacities can be paused to save cost. `sql exec` and other SQL-path commands, as well as REST/item commands that hit a plain HTTP 404 with error code `CapacityNotActive` (for example `snapshots create`), surface a clean `CapacityInactiveError` message: "The Fabric capacity for this workspace is paused or inactive. Resume it before running SQL, see [learn.microsoft.com/fabric/data-warehouse/pause-resume](https://learn.microsoft.com/fabric/data-warehouse/pause-resume?WT.mc_id=MVP_310840)".

Some REST error shapes are not yet mapped to that message and can still look cryptic: a non-retriable HTTP 5xx, or the HTTP 400 "Datamart server error" that `restore-points create` can return, which does not reliably mention the capacity at all. If you see either of those and the capacity is paused, treat it as the same root cause.

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

## mssql-python "authentication failed"

**Error you see:**

```
fabric_dw.exceptions.AuthError: authentication failed for server ...
```

or a raw driver message containing `Login failed` or `28000`.

**What happened:** The SQL driver is configured with `Authentication=ActiveDirectory{Default,ServicePrincipal,Interactive}` to match the `FABRIC_AUTH` mode you are using (see [`sql.py:_MODE_TO_AD_AUTH`](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/9a1436618112224b3e86085a9f2231b22e0c827b/src/fabric_dw/sql.py#L50)). In the default mode (`FABRIC_AUTH=default`) the driver runs its own [`ActiveDirectoryDefault`](https://learn.microsoft.com/sql/connect/odbc/using-azure-active-directory?WT.mc_id=MVP_310840) credential chain - environment variables → Managed Identity → Azure CLI → Visual Studio (Windows only) → Azure PowerShell → interactive browser - and is **not** limited to the Azure CLI cache. A token that was valid at connection-open time can still expire mid-session, causing the driver to fail on the next query. See [Authentication](authentication.md) for the full credential-chain reference.

**Resolution:**

Re-authenticate via whichever credential source your chain ended up using:

- **Azure CLI** (most common for `default` mode on a developer machine):

  ```bash
  az login --force
  ```

  If you have a stale MSAL token cache, clear it first:

  ```bash
  az account clear
  az login
  ```

- **Azure PowerShell** (if `AzurePowerShellCredential` was the winning source):

  ```powershell
  Connect-AzAccount
  ```

- **Service principal** (`FABRIC_AUTH=sp`): `FABRIC_AUTH=sp` uses `ActiveDirectoryServicePrincipal` (`ClientSecretCredential`) and bypasses the `ActiveDirectoryDefault` chain described in "What happened" above. Rotate or re-export `AZURE_CLIENT_SECRET` and restart the process:

  ```bash
  export AZURE_CLIENT_SECRET="<new-secret>"
  ```

- **Interactive browser** (`FABRIC_AUTH=interactive`): re-run your `fabric-dw` command; a new browser sign-in prompt will appear.

After re-authenticating, retry your command. `FabricSqlClient` opens a fresh connection and picks up the new token automatically. Set `AZURE_LOG_LEVEL=debug` if you are unsure which credential source the chain selected.

## 429 RateLimitedError

**Error you see:**

```
fabric_dw.exceptions.RateLimitedError: Received 429 10 consecutive times for https://api.fabric.microsoft.com/v1/...
```

**What happened:** The Fabric REST API enforces a rate limit. `fabric-dw` honours the `Retry-After` response header and automatically backs off, but if the API returns 429 more than 10 consecutive times the client raises `RateLimitedError` rather than waiting indefinitely. The internal rate limiter is set to **2 RPS**.

**Resolution:**

- If you hit this during a single command, simply retry - the capacity or the API may have been temporarily overloaded.
- If you are running `fabric-dw` commands in a loop or in parallel, reduce concurrency so that your effective request rate stays below the 2 RPS cap.
- Wait a few minutes before retrying if the API continues to throttle.

The client automatically retries on each 429 and waits exactly as long as the server requests, so transient throttling is usually transparent.

## Restore points not appearing

**Symptom:** `fdw snapshots list` returns an empty list, or user-defined restore points that you created are not visible.

**What happened:** Warehouse restore points are tied to capacity state:

- **User-defined restore points** can only be created while the capacity is in the **Active** state. If the capacity was paused at creation time, the restore point was not persisted.
- **System-created restore points** are generated automatically every **8 hours**, but only while the capacity is Active. Gaps in system points indicate the capacity was paused during that window.

**Resolution:**

1. Confirm the capacity is Active (see [Capacity paused](#capacity-paused-cryptic-5xx-or-404-errors) above).
2. Create a new user-defined restore point while the capacity is Active.
3. If you expected a system restore point from a period when the capacity was paused, that point does not exist - it was not created.

## MCP server doesn't show tools

**Symptom:** After adding `fabric-dw-mcp` to your AI tool's MCP config, the tool list is empty or the server does not appear.

**Steps to diagnose:**

1. **Verify the binary works locally:**

   ```bash
   fabric-dw-mcp --help
   ```

   If this fails with `command not found`, re-run `pip install fabric-dw` and make sure the install target's `bin/` directory is on your `PATH`.

2. **Check environment variables:** The MCP server requires the same Azure CLI credentials as the CLI. Make sure the process launched by your AI tool inherits the correct environment. See the [MCP server install](install.md#mcp) for the full list of required variables.

3. **Restart the MCP client:** Most AI tools (Claude Desktop, Cursor, VS Code) cache the tool list at startup. After updating the config or reinstalling the package, fully quit and reopen the application.

4. **Check the client logs:** Look for stderr output from the `fabric-dw-mcp` process in your AI tool's log folder - startup errors (missing env vars, import failures) are printed there.

## HTTP 413 from the HTTP transport

**Symptom:** A tool call over `--transport http` fails with HTTP 413 (payload too large). The same call over stdio succeeds.

**Cause:** The streamable-HTTP transport caps request bodies at 4 MiB. Only a very large payload reaches that: a multi-megabyte `execute_sql` script, or a procedure or view definition passed to `create_procedure` / `create_view`.

**Fix:** Split the statement, or load the script from storage with `load_table_from_url` / `import_table_from_url` rather than sending it inline. If neither fits, use the stdio transport, which has no body limit.

## HTTP 421 or 403 from the HTTP transport

**Symptom:** Every call over `--transport http` fails with HTTP 421 (`Invalid Host header`) or HTTP 403 (`Invalid Origin header`), even though the port is reachable.

**Cause:** Host and Origin validation is on and the request was addressed to a name that is not in the allowlist. This happens in two situations:

- You passed `--allowed-host` but the value does not match what the client sends. A reverse proxy on port 443 forwards `Host: mcp.example.com`, while a client talking straight to the port sends `Host: mcp.example.com:8000`. A value written without a port covers both; a value written with one covers only that port.
- You are on the default loopback bind, which the SDK protects with a loopback-only allowlist, but something in front of the server rewrites the header to a different name.

HTTP 403 specifically means the request carried an `Origin` header and no `--allowed-origin` matched it. Browser-based clients send that header, and so do Electron renderers, VS Code webviews and `fetch`-based Deno or Node clients. Note that an origin is matched exactly, so a different port counts as a different origin.

**Fix:** Pass `--allowed-host` with the exact name clients use, repeating the option per name, and `--allowed-origin` for a browser-based client. Both are described under [Hosting the MCP server](reference/hosting-mcp-server.md#host-and-origin-validation). To see what the server is actually enforcing, read its startup output: the allowlist it applied is logged at INFO level as `Host and Origin validation enabled`.

## `ping` logs a deprecation warning and I can't tell if it worked

**Symptom:** Calling `ping` (or the SDK's `send_ping()`) prints a deprecation warning. The warning appears every time, whether or not the call actually succeeded, so it gives you no signal either way.

**Cause:** MCP revision `2026-07-28` removed `ping` from the protocol. A client that negotiates that revision gets `-32601` (method not found) back; a client that negotiates the older `2025-11-25` revision still gets a normal response, since this server keeps serving both revisions from the same process. The SDK's own client emits the deprecation warning unconditionally in both cases, so the warning by itself does not tell you which one happened.

**Fix:** Ignore the warning and check the actual response or exception instead. If your code relies on `ping` returning normally, that only happens on `2025-11-25`; on `2026-07-28`, treat a `-32601` from `ping` as expected, not as a failure.
