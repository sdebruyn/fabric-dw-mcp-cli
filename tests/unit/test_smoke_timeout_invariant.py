"""Guard test: the smoke-test subprocess timeout must exceed the CLI's connect-retry budget.

This test is intentionally placed in the unit suite so it runs on every ``pytest
tests/unit`` invocation without needing live Fabric credentials.  It imports the
real constants from both the production module and the integration smoke module and
asserts the relationship that prevents flaky cold-start timeouts.

Background
----------
The CLI's internal connect-retry loop runs for up to the *resolved* SQL retry
deadline (``_CONNECT_RETRY_TIMEOUT_S`` in the smoke module — runtime-resolved from
the ``FABRIC_SQL_RETRY_TIMEOUT_S`` env var if set, else the built-in default of
120 s) before surfacing a connection error.  The integration smoke test wraps the
CLI in a subprocess with its own wall-clock timeout.  If that subprocess timeout is
less than or equal to the resolved budget the subprocess is killed exactly when the
retry loop gives up — leaving zero room for process startup, authentication, and
query execution overhead paid *on top of* the retry budget.  The result is a flaky
``TimeoutExpired`` failure on cold Fabric capacities.

The required invariant is::

    _SQL_SMOKE_SUBPROCESS_TIMEOUT_S > _CONNECT_RETRY_TIMEOUT_S + minimum_margin

where ``minimum_margin`` is at least 60 s to cover real-world startup/auth costs.
"""

from __future__ import annotations

# Import the actual constants used by the smoke test module so any future edits
# to either side of the invariant are immediately caught by this test.
# _CONNECT_RETRY_TIMEOUT_S in the smoke module is the runtime-resolved retry budget
# (reads FABRIC_SQL_RETRY_TIMEOUT_S if set, so it scales with env configuration).
from tests.integration.test_cli_smoke import (
    _CONNECT_RETRY_TIMEOUT_S,
    _SQL_SMOKE_SUBPROCESS_TIMEOUT_S,
    _SQL_STARTUP_MARGIN_S,
)

# Minimum acceptable margin above the connect-retry budget.
# This is a separate floor so the test catches someone accidentally halving the
# margin constant (e.g. changing it from 120 to 10) even if the sum still exceeds
# _CONNECT_RETRY_TIMEOUT_S by a small amount.
_MINIMUM_MARGIN_S: int = 60


def test_sql_smoke_subprocess_timeout_exceeds_connect_retry_budget() -> None:
    """The smoke-test subprocess timeout must strictly exceed the CLI's connect-retry budget.

    INVARIANT: _SQL_SMOKE_SUBPROCESS_TIMEOUT_S > _CONNECT_RETRY_TIMEOUT_S

    Without this headroom, a cold-start Fabric capacity can legitimately exhaust the
    full retry budget, and the subprocess is killed before the CLI even gets a chance
    to surface an error — resulting in a spurious TimeoutExpired failure.

    NOTE: this test is NOT algebraically redundant with
    ``test_sql_smoke_startup_margin_is_generous``.  The margin test checks the
    *addend* (_SQL_STARTUP_MARGIN_S) in isolation; this test checks the *computed
    sum* (_SQL_SMOKE_SUBPROCESS_TIMEOUT_S) against the *live production constant*
    (_CONNECT_RETRY_TIMEOUT_S).  If someone changes the derivation formula (e.g.
    switches from ``math.ceil + margin`` to ``max(…)``) the margin test alone would
    not catch a resulting sum that still violated the invariant.  Keep both.
    """
    assert _SQL_SMOKE_SUBPROCESS_TIMEOUT_S > _CONNECT_RETRY_TIMEOUT_S, (
        f"Smoke-test subprocess timeout ({_SQL_SMOKE_SUBPROCESS_TIMEOUT_S}s) must be strictly "
        f"greater than the CLI connect-retry budget ({_CONNECT_RETRY_TIMEOUT_S}s). "
        "Raise _SQL_STARTUP_MARGIN_S in tests/integration/test_cli_smoke.py."
    )


def test_sql_smoke_startup_margin_is_generous() -> None:
    """The startup/auth/query margin added on top of the retry budget must be at least 60 s.

    A margin of 0-59 s is too small to absorb real-world subprocess startup, Azure AD
    token acquisition, and query execution overhead on a Fabric SQL endpoint.
    """
    assert _SQL_STARTUP_MARGIN_S >= _MINIMUM_MARGIN_S, (
        f"_SQL_STARTUP_MARGIN_S ({_SQL_STARTUP_MARGIN_S}s) is too small; "
        f"must be >= {_MINIMUM_MARGIN_S}s to provide meaningful cold-start headroom. "
        "Update _SQL_STARTUP_MARGIN_S in tests/integration/test_cli_smoke.py."
    )
