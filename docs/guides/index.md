---
title: Guides
---

# Guides

Task-oriented walkthroughs that thread several `fabric-dw` commands and MCP tools into a single end-to-end workflow. Each guide shows runnable CLI examples (`fdw …`) and names the equivalent MCP tool, so the same steps apply whether you drive from a terminal or an AI assistant. For the full option reference of any one command, see the per-domain [Commands](../commands/index.md) pages.

## Available guides

- **[dbt setup](dbt-setup.md)** — Stand up a working dbt environment on a Fabric Data Warehouse: provision, scaffold the `dbt-fabric` project, generate `_sources.yml` from the live warehouse, and verify with `dbt debug`/`dbt run`.
- **[Ingesting data](ingesting-data.md)** — Get CSV, Parquet, or JSON files into a warehouse table end to end: create the schema and table, stage and load via `COPY INTO`, verify, and refresh statistics.
- **[Query performance](query-performance.md)** — Find and fix slow queries with an investigate → diagnose → improve → verify loop across the `queries`, `sql`, `statistics`, `sql-pools`, and `settings` groups.
- **[Tables & views](tables-and-views.md)** — Build out a schema model from an empty schema through populated tables to reporting views, with the statistics that keep the optimizer honest.
- **[Warehouse performance](warehouse-performance.md)** — A monitor → diagnose → tune/scale playbook at the warehouse and capacity level for when the warehouse feels slow or throttled.
