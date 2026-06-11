# Security

We use [GitHub Private Vulnerability Reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability?WT.mc_id=MVP_310840) for security disclosures.

**To report a vulnerability**: [open a private advisory](https://github.com/sdebruyn/fabric-dw-mcp-cli/security/advisories/new). GitHub will deliver it directly and privately to the maintainers — no public issue or email needed.

We aim to acknowledge reports within **5 business days** and to publish a fix or advisory within **90 days** of confirmation.

## Supported versions

This project is in **pre-1.0 / alpha**. Only the **latest published release** on PyPI receives security fixes. No backports are made to older releases.

If you are running anything other than the latest published version, upgrade first before reporting.

## Scope

Bugs that put data, credentials, or workspace state at risk are in scope. Out of scope: bugs in third-party SDKs we depend on (file those upstream), denial-of-service against the Fabric API (file with Microsoft).

## Interactive sign-in Entra application

The interactive browser sign-in path (and the fallback interactive browser step inside `DefaultAzureCredential`) uses a **shared multi-tenant Entra application** by default:

- **Application (client) ID**: `f666e5ee-2149-4c6a-87eb-13c9e1fdc70d`

This application requests delegated (user-context) permissions for the following scope:

- `https://analysis.windows.net/powerbi/api/.default` — Power BI / Fabric REST API

> **Note**: The `SQL_SCOPE` constant (`https://database.windows.net/.default`) is defined in `auth.py` for future use when direct Fabric SQL Analytics Endpoint connections are implemented, but it is not yet requested at runtime.

### Implications

Because the app is **multi-tenant and shared across all users of this tool**:

1. **Upstream-app trust**: your tenant admin must consent to the app (or the user must do per-user consent if your tenant policy allows it). The audit trail in your Azure AD / Entra audit log will show the *upstream app ID* (`f666e5ee-...`), not a tenant-specific registration — this reduces per-tenant audit granularity.
2. **Supply-chain risk**: if the shared application is compromised, suspended, or deleted by Microsoft, all users on the default flow will be impacted without a per-tenant fallback.
3. **No tenant-specific conditional-access policies**: because the app is not registered in your tenant, you cannot target it with tenant-specific conditional-access rules.

### Registering your own Entra application

Tenants that require full audit-trail control or wish to apply custom conditional-access policies should register their own **public-client** Entra application and set the override environment variable:

```bash
export FABRIC_INTERACTIVE_CLIENT_ID="<your-app-client-id>"
```

The app must be configured as a public client (mobile & desktop) with the following delegated API permissions:

- **Power BI Service** → `Tenant.Read.All` (or the narrower scopes your workflows require)
- **Azure SQL Database** → `user_impersonation`

Once registered, set `FABRIC_INTERACTIVE_CLIENT_ID` to your app's client ID. The tool will use it for both the `interactive` and `default` credential modes instead of the shared application.
