---
title: Install
---

# Install

## Prerequisites

- Python 3.11 or later
- An Azure credential. The package picks one up automatically — see [Authentication](authentication.md).

## Authentication {#authentication}

`fabric-dw` selects a credential source via the `FABRIC_AUTH` environment variable:

| `FABRIC_AUTH` value | What it uses |
| --- | --- |
| `default` (default) | [`azure-identity` `DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential?WT.mc_id=MVP_310840) — see [Authentication](authentication.md) for the full chain |
| `sp` | Service-principal (`AZURE_CLIENT_SECRET`) |
| `interactive` | Browser pop-up |

See [Authentication](authentication.md) for the full credential chain, all supported sources, and debugging tips.

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

See also the [Authentication](authentication.md) page for the full credential chain and the [Troubleshooting](troubleshooting.md) page for common failure modes such as expired tokens and 403 permission errors.
