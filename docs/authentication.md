---
title: Authentication
---

# Authentication

## TL;DR

If you are already signed in via [Azure CLI](https://learn.microsoft.com/cli/azure/reference-index?view=azure-cli-latest&WT.mc_id=MVP_310840#az-login) or [Azure PowerShell](https://learn.microsoft.com/powershell/module/az.accounts/connect-azaccount?WT.mc_id=MVP_310840), you don't need to configure anything — `fabric-dw` picks up your session automatically.

```bash
az login          # or: az login --tenant <tenant-id>
fdw -w "Sales Workspace" warehouses list
```

```powershell
Connect-AzAccount
fdw -w "Sales Workspace" warehouses list
```

If neither of those works for you, read on for the alternatives.

---

`fabric-dw` selects a credential source via a 3-layer resolution stack:

| Layer | Mechanism | Description |
| --- | --- | --- |
| 1 | `FABRIC_AUTH` env var | Wins when non-empty and non-whitespace. An empty/whitespace value is treated as absent (falls through). An unrecognised non-empty value raises an error. |
| 2 | `[defaults] auth_mode` in `config.toml` | Set with `fdw config set auth-mode MODE`. Invalid values are discarded (treated as unset) with a warning. |
| 3 | Built-in default | `default` (DefaultAzureCredential chain) |

Valid values: `default`, `interactive`, `sp` (case-insensitive).

| Value | What it uses |
| --- | --- |
| `default` | [`azure-identity` `DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential?WT.mc_id=MVP_310840) — see [credential chain](#fabric_authdefault-defaultazurecredential-chain) below |
| `interactive` | Browser pop-up — see [interactive sign-in](#interactive-browser-sign-in-zero-setup) below |
| `sp` | Service-principal — see [service principal](#fabric_authsp-service-principal) below |

!!! note "Empty-value semantics"
    An empty or whitespace-only `FABRIC_AUTH` (e.g. `FABRIC_AUTH=`) is treated as absent and falls through to `config.toml` / the built-in default. An unrecognised non-empty value (e.g. `FABRIC_AUTH=typo`) raises a configuration error immediately at server start so the credential is never silently wrong.

---

## Interactive browser sign-in (zero setup)

`FABRIC_AUTH=interactive` (and the default-mode browser fallback) uses a shared multi-tenant app — no registration needed:

| | |
| --- | --- |
| Display name | `fabric-dw` |
| Client ID | `f666e5ee-2149-4c6a-87eb-13c9e1fdc70d` |
| Sign-in audience | Multi-tenant (`AzureADMultipleOrgs`) |
| Redirect URI | `http://localhost` |

On first sign-in:

- **Non-admin users** — the consent prompt asks for the delegated scopes the app needs (Workspace, Item, Tenant.Read, SQL user_impersonation). If your tenant policy requires admin consent for any of them, sign-in will fail until an admin grants it.
- **Admins** — choose "Consent on behalf of your organization" once; subsequent sign-ins from anyone in the tenant just work.

Pre-consent admin URL:

```
https://login.microsoftonline.com/<YOUR-TENANT-ID>/adminconsent?client_id=f666e5ee-2149-4c6a-87eb-13c9e1fdc70d
```

### Bring your own app (advanced)

Set `FABRIC_INTERACTIVE_CLIENT_ID` (and optionally `FABRIC_INTERACTIVE_TENANT_ID`) to override the shared default. You then need to register an Entra app in your tenant:

```bash
az ad app create \
  --display-name "fabric-dw" \
  --sign-in-audience AzureADMyOrg \
  --is-fallback-public-client true \
  --public-client-redirect-uris http://localhost
```

Then grant the same delegated permissions as the shared app:

| API | Permission | Resource app ID |
| --- | --- | --- |
| Power BI Service | `Workspace.ReadWrite.All` | `00000009-0000-0000-c000-000000000000` |
| Power BI Service | `Item.ReadWrite.All` | `00000009-0000-0000-c000-000000000000` |
| Power BI Service | `Tenant.Read.All` | `00000009-0000-0000-c000-000000000000` |
| Azure SQL Database | `user_impersonation` | `022907d3-0f1b-48f7-badc-1ba6abab6d66` |

!!! note "Tenant pinning"
    When `FABRIC_INTERACTIVE_TENANT_ID` is set, `FABRIC_AUTH=interactive` passes it as `tenant_id` to [`InteractiveBrowserCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.interactivebrowsercredential?WT.mc_id=MVP_310840) and the default-mode browser fallback also receives it as `interactive_browser_tenant_id`. Useful when your tenant policy requires a specific tenant context at sign-in time.

---

## `FABRIC_AUTH=default` — DefaultAzureCredential chain

When `FABRIC_AUTH` is `default` (or unset), the package delegates to [`azure-identity`'s `DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential?WT.mc_id=MVP_310840). It walks the following sources **in order** and stops at the first one that returns a usable token:

1. **Environment variables** — `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` / `AZURE_CLIENT_CERTIFICATE_PATH`, `AZURE_TENANT_ID` — see [`EnvironmentCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.environmentcredential?WT.mc_id=MVP_310840)
2. **Workload Identity** — injected in Kubernetes / AKS workloads — see [`WorkloadIdentityCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.workloadidentitycredential?WT.mc_id=MVP_310840)
3. **Managed Identity** — Azure VMs, App Service, Container Apps, etc. — see [`ManagedIdentityCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.managedidentitycredential?WT.mc_id=MVP_310840)
4. **Shared token cache** — the MSAL cache shared between Azure tools — see [`SharedTokenCacheCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.sharedtokencachecredential?WT.mc_id=MVP_310840)
5. **Azure CLI** — token from `az login` — see [`AzureCliCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.azureclicredential?WT.mc_id=MVP_310840)
6. **Azure Developer CLI** — token from `azd auth login` — see [`AzureDeveloperCliCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.azuredeveloperclicredential?WT.mc_id=MVP_310840)
7. **Azure PowerShell** — token from `Connect-AzAccount` — see [`AzurePowerShellCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.azurepowershellcredential?WT.mc_id=MVP_310840)
8. **Interactive browser** — falls back to browser sign-in using the [shared app](#interactive-browser-sign-in-zero-setup) (or your override via `FABRIC_INTERACTIVE_CLIENT_ID`) — see [`InteractiveBrowserCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.interactivebrowsercredential?WT.mc_id=MVP_310840)

---

## `FABRIC_AUTH=sp` — Service principal

Set the following environment variables:

| Variable | Description |
| --- | --- |
| `AZURE_TENANT_ID` | Your Entra tenant ID |
| `AZURE_CLIENT_ID` | Application (client) ID of your registered app |
| `AZURE_CLIENT_SECRET` | A client secret for the app |

The package uses [`ClientSecretCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.clientsecretcredential?WT.mc_id=MVP_310840) with these values. The shared `fabric-dw` app is **not** used in SP mode — you must supply your own app registration and secret.

---

## Environment variable reference

| Variable | Default | Description |
| --- | --- | --- |
| `FABRIC_AUTH` | _(unset — falls through to config / built-in default)_ | Credential mode: `default`, `interactive`, or `sp`. Empty/whitespace falls through; unrecognised non-empty value raises an error at startup. |
| `FABRIC_INTERACTIVE_CLIENT_ID` | `f666e5ee-2149-4c6a-87eb-13c9e1fdc70d` | Override the shared app client ID for browser sign-in |
| `FABRIC_INTERACTIVE_TENANT_ID` | _(unset)_ | Pin a specific Entra tenant for browser sign-in |
| `AZURE_TENANT_ID` | _(unset)_ | Required for `FABRIC_AUTH=sp` |
| `AZURE_CLIENT_ID` | _(unset)_ | Required for `FABRIC_AUTH=sp` |
| `AZURE_CLIENT_SECRET` | _(unset)_ | Required for `FABRIC_AUTH=sp` |

To persist the credential mode for the **MCP server** across restarts without setting an environment variable:

```shell
fdw config set auth-mode interactive   # persist 'interactive' in config.toml
fdw config unset auth-mode             # revert to built-in default
```

!!! note "MCP server only"
    `[defaults] auth_mode` is consumed by the MCP server (`fdw mcp`) only.  The `fdw` CLI credential is controlled by the `--auth` / `-a` flag and does not fall back to this config key.  A follow-up issue will unify the CLI credential path with the config default.

---

## Debugging

Set `AZURE_LOG_LEVEL=debug` to make `azure-identity` log which credential in the chain it tried and why each failed.
