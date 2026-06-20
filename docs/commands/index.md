---
title: Commands
---

# Commands

`fabric-dw` exposes every operation as both a CLI command and an MCP tool. The two surfaces share the same authentication, connection, and business logic — a fix or new feature lands in both at once.

Each page below covers one command domain: CLI synopsis, options, and examples alongside the corresponding MCP tool parameters and return values.

## Domains

- **[Audit](audit.md)** — Manage SQL audit settings for Data Warehouses and SQL Analytics Endpoints.
- **[Cache](cache.md)** — Manage the local name-to-UUID lookup cache.
- **[Completion](completion.md)** — Install and manage shell completion scripts.
- **[Configuration & defaults](config.md)** — Store a default workspace and/or warehouse to avoid repeating them on every invocation.
- **[dbt](dbt.md)** — Scaffold a dbt project pre-wired to a Fabric Data Warehouse.
- **[Functions](functions.md)** — Manage T-SQL user-defined functions.
- **[Queries](queries.md)** — Inspect and manage running queries.
- **[Restore Points](restore-points.md)** — Create, list, rename, and delete warehouse restore points.
- **[Running SQL](sql.md)** — Execute SQL statements and capture estimated execution plans.
- **[Schemas](schemas.md)** — Manage SQL schemas.
- **[Settings](settings.md)** — Manage server-side database settings.
- **[Snapshots](snapshots.md)** — Manage Data Warehouse snapshots.
- **[SQL Analytics Endpoints](sql-endpoints.md)** — Manage SQL Analytics Endpoints.
- **[SQL Pools](sql-pools.md)** — Manage custom SQL Pools at the workspace level.
- **[Statistics](statistics.md)** — Manage user-defined statistics.
- **[Stored procedures](procedures.md)** — Manage stored procedures.
- **[Tables](tables.md)** — Manage SQL tables, including CTAS, clone, load, and clustering.
- **[Views](views.md)** — Manage SQL views.
- **[Warehouses](warehouses.md)** — Manage Data Warehouses and SQL Analytics Endpoints.
- **[Workspaces](workspaces.md)** — List and inspect workspaces.
