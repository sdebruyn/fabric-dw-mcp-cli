"""Unit tests for the _sanitize_stderr() helper in test_cli_smoke."""

from __future__ import annotations

from tests.integration.test_cli_smoke import _sanitize_stderr

_IMDS_BLOCK = (
    "Failed to receive Azure VM metadata: timed out\n"
    "Traceback (most recent call last):\n"
    '  File "/path/to/azure/identity/_credentials/imds.py", line 42, in get_token\n'
    "    response = await self._client.get(request)\n"
    "TimeoutError: timed out\n"
)

_REAL_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "some_module.py", line 10, in do_thing\n'
    "    raise RuntimeError('boom')\n"
    "RuntimeError: boom\n"
)


def test_empty_string_returns_empty() -> None:
    assert _sanitize_stderr("") == ""


def test_imds_block_only_returns_empty() -> None:
    result = _sanitize_stderr(_IMDS_BLOCK)
    assert "Traceback" not in result
    assert "TimeoutError" not in result


def test_imds_block_with_real_traceback_preserves_real_traceback() -> None:
    combined = _IMDS_BLOCK + _REAL_TRACEBACK
    result = _sanitize_stderr(combined)
    assert "RuntimeError: boom" in result
    assert "Traceback (most recent call last):" in result
    assert "Failed to receive Azure VM metadata" not in result


def test_no_imds_block_returns_unchanged() -> None:
    stderr = "Some warning: something happened\nAnother line\n"
    assert _sanitize_stderr(stderr) == stderr
