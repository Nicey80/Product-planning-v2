"""Auto-mark tests `unit` or `property` so CI can run them as separate
stages (per CLAUDE.md's "tests first for contracts": property tests are
the primary contract-enforcement mechanism and get their own gate)
without hand-tagging every test function.

A hypothesis `@given` test exposes a `.hypothesis` attribute on the
underlying function object -- that's enough to tell the two apart at
collection time.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        func = getattr(item, "obj", None)
        marker = "property" if hasattr(func, "hypothesis") else "unit"
        item.add_marker(getattr(pytest.mark, marker))
