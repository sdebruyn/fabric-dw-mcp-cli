"""Shared stderr sanitization utilities used by both unit and integration tests."""

from __future__ import annotations

import re

# Matches the benign Azure Identity IMDS timeout block that appears when
# the Instance Metadata Service endpoint is unreachable (e.g. outside Azure).
# The trailing newline is optional because the block may be the last content
# in stderr without a final newline.
_IMDS_BLOCK_RE = re.compile(
    r"Failed to receive Azure VM metadata: timed out\n"
    r"Traceback \(most recent call last\):.*?TimeoutError: timed out\n?",
    re.DOTALL,
)


def sanitize_stderr(stderr: str) -> str:
    """Strip known-benign Azure Identity IMDS timeout tracebacks from stderr.

    When the Azure Identity library probes the Instance Metadata Service (IMDS)
    and the endpoint is unreachable (e.g. outside Azure), it logs a Python
    traceback to stderr. This is harmless but causes the forbidden-pattern check
    for 'Traceback (most recent call last)' to fire. Strip these blocks so that
    real unexpected tracebacks are still caught.
    """
    return _IMDS_BLOCK_RE.sub("", stderr)
