"""Synthetic data generation for testing engine/ against known parameters.

Not part of engine's production surface (backend/frontend must never import
`engine.testing`). Submodules:

- synthetic.py -- the portfolio simulator (SyntheticParams, generate_portfolio,
  the four raw_* dataclasses).
- pathologies.py -- named, injectable data-quality/modeling edge cases built
  on top of synthetic.py.
- serialize.py -- JSON (de)serialization for the four raw_* tables.
- config.py -- YAML config loading for the CLI below.
- cli.py -- `python -m engine.testing.synthetic --base <config.yaml> --out
  <dir>`.
- engine/estimate.py (one level up) -- the estimators this whole package
  exists to validate.

Import directly from the submodule you need (e.g. `from
engine.testing.synthetic import generate_portfolio`) rather than from this
package's `__init__` -- deliberately kept import-free here so `python -m
engine.testing.synthetic` doesn't re-execute synthetic.py a second time
under a different module identity (a well-known quirk of `python -m
pkg.mod` when `pkg/__init__.py` eagerly imports from `pkg.mod`).
"""
