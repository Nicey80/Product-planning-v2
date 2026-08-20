"""CI's golden-run-snapshot stage: recompute the fixed scenario in
golden_scenario.py and diff it against the committed snapshot.

A mismatch means some change in this PR altered engine/'s numeric
behavior. That's sometimes intentional -- if so, regenerate the snapshot
deliberately (see the module docstring below) and commit it as its own
reviewable diff, not as a side effect of an unrelated change.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from golden_scenario import run_golden_scenario

SNAPSHOT_PATH = Path(__file__).parent / "golden" / "golden_run_v1.json"


def _to_decimals(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _to_decimals(v) for key, v in value.items()}
    if isinstance(value, str):
        return Decimal(value)
    return value


def test_golden_run_matches_committed_snapshot() -> None:
    actual = run_golden_scenario()
    expected = json.loads(SNAPSHOT_PATH.read_text())

    assert _to_decimals(actual) == _to_decimals(expected), (
        "golden run output no longer matches golden/golden_run_v1.json -- "
        "if this change to engine/ is deliberate, regenerate the snapshot "
        "(see golden_scenario.run_golden_scenario) and commit it as its "
        "own reviewed diff"
    )
