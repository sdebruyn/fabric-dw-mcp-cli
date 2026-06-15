"""Shared fixtures and policy documentation for unit tests of fabric_dw.services.

Testing policy — respx vs AsyncMock
-------------------------------------
**HTTP-boundary service tests use respx; AsyncMock only for non-HTTP collaborators.**

Rationale
^^^^^^^^^
:mod:`fabric_dw.http_client.FabricHttpClient` wraps ``httpx.AsyncClient``.  Using
``respx`` to mock at the HTTP layer has two advantages over ``AsyncMock`` against
the client directly:

1. **Wire serialisation** — the actual URL, method, headers, and JSON body that
   httpx sends are validated.  ``AsyncMock`` only validates Python call signatures,
   not the serialised request shape.
2. **Refactoring safety** — if a service helper changes how it builds a URL or
   constructs a query string, respx tests will catch it; AsyncMock tests will not.

Current status (June 2026)
^^^^^^^^^^^^^^^^^^^^^^^^^^
* Fully migrated to respx: ``test_workspaces.py``, ``test_audit.py``,
  ``test_restore.py``, ``test_snapshots.py``, ``test_warehouses.py``,
  ``test_sql_pools.py``, ``test_permissions.py``.
* ODBC/SQL services (``test_views.py``, ``test_schemas.py``, ``test_queries.py``,
  ``test_tables.py``, ``test_query_insights.py``): these services do NOT use
  FabricHttpClient.  Mocking ``open_connection`` / the cursor is the correct
  approach and is NOT a violation of this policy.
* ``test_sql_endpoints.py``: uses ``AsyncMock()`` as a *pass-through argument*
  to functions that are themselves patched.  The mock client is never called;
  no HTTP is exercised.  This is acceptable as-is; a full respx migration is
  a future improvement (no issue tracked — low priority since the client is
  never actually invoked in these tests).
* ``test_lro.py``: tests the LRO-polling helper which builds on FabricHttpClient.
  The tests use AsyncMock against the client.  Migrating to respx is a future
  improvement (no issue tracked — the polling logic is thin and the real risk
  is in the callers, which use respx already).

Rule of thumb
^^^^^^^^^^^^^
If your test calls ``FabricHttpClient.request()`` or any paginated helper on it,
**use respx**.  If your test only passes the client as an argument to a function
that is itself patched (so the client is never actually called), AsyncMock is
acceptable.  If your test is for an ODBC/SQL service, mock ``open_connection``.
"""
