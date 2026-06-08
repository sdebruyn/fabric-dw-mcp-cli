---
title: Install
---

# Install

## Prerequisites

- Python 3.11 or later
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli?WT.mc_id=MVP_310840) — used for authentication (`az login`)

## Authentication {#authentication}

`fabric-dw` selects a credential source via the `FABRIC_AUTH` environment variable:

| `FABRIC_AUTH` value | What it uses |
| --- | --- |
| `default` (default) | [`azure-identity` `DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential?WT.mc_id=MVP_310840) — see chain below |
| `sp` | Service-principal (`AZURE_CLIENT_SECRET`) |
| `interactive` | Browser pop-up |

### `FABRIC_AUTH=default` — DefaultAzureCredential chain

When `FABRIC_AUTH` is `default` (or unset), the package delegates to [`azure-identity`'s `DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential?WT.mc_id=MVP_310840). It walks the following sources **in order** and stops at the first one that returns a usable token:

1. **Environment variables** — `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` / `AZURE_CLIENT_CERTIFICATE_PATH`, `AZURE_TENANT_ID`
2. **Workload Identity** — injected in Kubernetes / AKS workloads
3. **Managed Identity** — Azure VMs, App Service, Container Apps, etc.
4. **Shared token cache** — the MSAL cache shared between Azure tools
5. **Azure CLI** — token from `az login`
6. **Azure Developer CLI** — token from `azd auth login`
7. **Azure PowerShell** — token from `Connect-AzAccount`

!!! note "Interactive browser excluded"
    `fabric-dw` sets `exclude_interactive_browser_credential=True`, so the browser pop-up is **never** triggered by the `default` mode. Use `FABRIC_AUTH=interactive` if you want an interactive login.

In practice, the most common source is the Azure CLI. Run `az login` (or `az login --tenant <tenant-id>`) before using `fabric-dw` and the credential chain picks up your session automatically.

## Install

```bash
pip install fabric-dw
```

!!! note
    The package is not yet published to PyPI. Installation instructions will be updated on the first release. In the meantime, you can install directly from the repository source.

## Verify

After installation, confirm the CLI is available:

```bash
fabric-dw --help
```

You should see the top-level help output listing available commands.

---

See also the [Troubleshooting](troubleshooting.md) page for common failure modes such as expired `az login` tokens and 403 permission errors.
