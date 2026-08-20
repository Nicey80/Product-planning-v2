#!/usr/bin/env python3
"""Deliberate, documented regeneration procedure for the synthetic golden
fixture (see the module docstring in ../golden_synthetic_scenario.py for
what these two files are and why they're split).

Run this ONLY when a change to engine/ is meant to change numeric output.
It is not run as part of CI -- test_golden_synthetic.py recomputes both
snapshots and diffs them against what's already committed; if they no
longer match, that test fails and tells you to come here.

Usage (from engine/):

    uv run python tests/golden/regenerate_synthetic_golden.py

Then:

    git diff tests/golden/synthetic_dataset_v1.json tests/golden/golden_forecast_run_v1.json

and confirm every changed number is explained by the change you just made
-- if anything moved that you can't explain, that's a real regression, not
a golden update. Commit the two regenerated files as their own reviewed
commit, separate from the code change that caused them to move.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # tests/ dir, for golden_synthetic_scenario

from golden_synthetic_scenario import build_dataset, run_forecast, serialize_dataset  # noqa: E402

_GOLDEN_DIR = Path(__file__).parent
_DATASET_PATH = _GOLDEN_DIR / "synthetic_dataset_v1.json"
_FORECAST_PATH = _GOLDEN_DIR / "golden_forecast_run_v1.json"


def main() -> None:
    portfolio = build_dataset()
    dataset = serialize_dataset(portfolio)
    forecast = run_forecast(portfolio)

    _DATASET_PATH.write_text(json.dumps(dataset, indent=2, sort_keys=True) + "\n")
    _FORECAST_PATH.write_text(json.dumps(forecast, indent=2, sort_keys=True) + "\n")

    print(f"wrote {_DATASET_PATH}")
    print(f"wrote {_FORECAST_PATH}")


if __name__ == "__main__":
    main()
