---
title: Cache
---

# Cache

Manage the local name-to-UUID lookup cache. `fabric-dw` caches workspace and item name-to-GUID mappings to avoid repeated API round-trips. Use these commands if you rename items outside the CLI or need to force a fresh lookup.

**Targets:** Workspace (not item-specific)

## CLI

### cache clear

**Targets:** Workspace (not item-specific)

Clear all cached entries.

**Synopsis**

```
fdw cache clear
```

**Example**

```shell
fdw cache clear
```

```
Cache cleared.
```

## MCP tools

### clear_cache

**Targets:** Workspace (not item-specific)

Erase all cached workspace and item name-to-UUID mappings.

**Parameters:** None

**Returns:** `{ "cleared": true }`: confirmation.
