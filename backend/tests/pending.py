"""Marker for tests written before their implementation (CLAUDE.md §8, tests first).

A pending test is an expected failure **only while its module can't be imported**. Any other
error is a real failure. When the module lands and the test passes, strict mode turns the
unexpected pass into a failure, so the marker must be removed in the implementing commit.
"""

import pytest


def pending(commit: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(
        raises=ImportError, strict=True, reason=f"implementation arrives in {commit}"
    )
