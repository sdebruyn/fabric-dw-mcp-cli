---
title: Authentication
---

# Authentication

## Quick path — local development

1. `az login`
2. Run the CLI / start the MCP server. Done.

## The full picture

With `FABRIC_AUTH=default` the package uses [`DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential?WT.mc_id=MVP_310840). It walks this chain and the first source that returns a token wins:

### 1. Environment variables — service principal or user

`AZURE_CLIENT_ID` + `AZURE_TENANT_ID` + `AZURE_CLIENT_SECRET` for a service principal. See [`EnvironmentCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.environmentcredential?WT.mc_id=MVP_310840).

### 2. Workload Identity — federated, no secret

For GitHub Actions OIDC, AKS federated workloads, etc. See [`WorkloadIdentityCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.workloadidentitycredential?WT.mc_id=MVP_310840).

### 3. Managed Identity — on Azure

Azure VMs, App Service, Functions, Container Apps, AKS pod identities. See [`ManagedIdentityCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.managedidentitycredential?WT.mc_id=MVP_310840).

### 4. Shared MSAL cache

A token written by VS, VS Code, another Azure SDK app on the same machine. See [`SharedTokenCacheCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.sharedtokencachecredential?WT.mc_id=MVP_310840).

### 5. Azure CLI

`az login`. See [`AzureCliCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.azureclicredential?WT.mc_id=MVP_310840).

### 6. Azure Developer CLI

`azd auth login`. See [`AzureDeveloperCliCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.azuredeveloperclicredential?WT.mc_id=MVP_310840).

### 7. Azure PowerShell

`Connect-AzAccount`. See [`AzurePowerShellCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.azurepowershellcredential?WT.mc_id=MVP_310840).

### 8. Interactive browser — last-resort fallback

If everything above failed and you're on a workstation with a browser, you'll be prompted to sign in. See [`InteractiveBrowserCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.interactivebrowsercredential?WT.mc_id=MVP_310840).

## Two explicit modes

- `FABRIC_AUTH=sp` — [`ClientSecretCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.clientsecretcredential?WT.mc_id=MVP_310840) only. For CI / unattended.
- `FABRIC_AUTH=interactive` — [`InteractiveBrowserCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.interactivebrowsercredential?WT.mc_id=MVP_310840) only.

## Debugging

`AZURE_LOG_LEVEL=debug` makes azure-identity log which credential in the chain it tried and why each failed.
