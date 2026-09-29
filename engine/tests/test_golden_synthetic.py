"""CI's synthetic golden-fixture stage.

Two independent comparisons, matching golden_synthetic_scenario.py's two
snapshots:

1. Regenerate the dataset from PINNED_PARAMS and diff against
   golden/synthetic_dataset_v1.json byte-for-byte -- proves
   engine.testing.synthetic.generate_portfolio is still exactly
   reproducible from (params, seed).
2. Recompute the forecast from the *committed* dataset (not a freshly
   generated one -- see note below) and diff against
   golden/golden_forecast_run_v1.json -- proves engine's estimate ->
   kernel -> simulate -> hierarchy chain is still exactly reproducible
   from a fixed input.

Step 2 deliberately re-derives its input portfolio from the committed
JSON rather than reusing generate_portfolio's in-memory output, so this
test would also fail (correctly) if the *serialization* of the dataset
silently dropped or reordered something the forecast depends on.

A mismatch means something in this PR changed engine's numeric behavior.
If that's deliberate, see golden_synthetic_scenario.py's module docstring
for the regeneration procedure -- do not hand-edit the golden files.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from engine.testing.serialize import deserialize_portfolio
from golden_synthetic_scenario import PINNED_PARAMS, build_dataset, run_forecast, serialize_dataset

_GOLDEN_DIR = Path(__file__).parent / "golden"
_DATASET_PATH = _GOLDEN_DIR / "synthetic_dataset_v1.json"
_FORECAST_PATH = _GOLDEN_DIR / "golden_forecast_run_v1.json"


def _to_decimals(value: Any) -> Any:
    """Recursively convert every JSON string that looks like a plain
    decimal into a Decimal, so the comparison isn't sensitive to
    formatting (trailing zeros, "1" vs "1.0") -- only to actual value."""
    if isinstance(value, dict):
        return {key: _to_decimals(v) for key, v in value.items()}
    if isinstance(value, list):
        return [_to_decimals(v) for v in value]
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            return value
    return value


def test_synthetic_dataset_matches_committed_snapshot() -> None:
    actual = serialize_dataset(build_dataset())
    expected = json.loads(_DATASET_PATH.read_text())

    assert _to_decimals(actual) == _to_decimals(expected), (
        "generate_portfolio(PINNED_PARAMS) no longer reproduces "
        "golden/synthetic_dataset_v1.json exactly -- if this change to "
        "engine/testing/synthetic.py is deliberate, regenerate the golden "
        "fixture (see golden_synthetic_scenario.py's module docstring) and "
        "commit it as its own reviewed diff"
    )


def test_forecast_matches_committed_snapshot() -> None:
    committed_dataset = json.loads(_DATASET_PATH.read_text())
    portfolio = deserialize_portfolio(committed_dataset, params=PINNED_PARAMS)

    actual = run_forecast(portfolio)
    expected = json.loads(_FORECAST_PATH.read_text())

    assert _to_decimals(actual) == _to_decimals(expected), (
        "run_forecast() over the committed golden dataset no longer "
        "reproduces golden/golden_forecast_run_v1.json exactly -- if this "
        "change to engine/estimate.py, kernel.py, simulate.py, or "
        "hierarchy.py is deliberate, regenerate the golden fixture (see "
        "golden_synthetic_scenario.py's module docstring) and commit it "
        "as its own reviewed diff"
    )
