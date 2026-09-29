"""Command-line entry point: `python -m engine.testing.synthetic --base
<config.yaml> --out <dir>`.

Generates a SyntheticPortfolio from a YAML ground-truth config
(engine.testing.config.load_params) and writes it into --out (created if
missing): the four raw_* tables, dim_product/dim_channel dimension tables,
a truth.json ground-truth summary (see engine.testing.serialize.
truth_summary for exactly what it covers and what it deliberately omits),
and a copy of the input config for provenance. All JSON (see
synthetic/base_portfolio.yaml's own `output.format: parquet` -- this CLI
writes JSON instead; see that module's docstring for why). See synthetic/
base_portfolio.yaml at the engine package's repo root for a complete,
annotated example config.

This is a thin shim over the library -- everything it does is directly
available in Python:

    from engine.testing.config import load_params
    from engine.testing.synthetic import generate_portfolio
    from engine.testing.serialize import serialize_portfolio

    portfolio = generate_portfolio(load_params("base_portfolio.yaml"))
    tables = serialize_portfolio(portfolio)
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from engine.testing.config import load_params
from engine.testing.serialize import (
    dim_channel_rows,
    dim_product_rows,
    serialize_portfolio,
    truth_summary,
)
from engine.testing.synthetic import generate_portfolio


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m engine.testing.synthetic",
        description="Generate a synthetic portfolio fixture from a YAML ground-truth config.",
    )
    parser.add_argument(
        "--base",
        required=True,
        type=Path,
        help="Path to a ground-truth params YAML file (see synthetic/base_portfolio.yaml).",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output directory. Created if missing; existing raw_*.json files are overwritten",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)

    params = load_params(args.base)
    portfolio = generate_portfolio(params)

    tables: dict[str, list[dict[str, object]] | dict[str, object]] = dict(
        serialize_portfolio(portfolio)
    )
    tables["dim_product"] = dim_product_rows(params)
    tables["dim_channel"] = dim_channel_rows(params)
    tables["truth"] = truth_summary(params)

    args.out.mkdir(parents=True, exist_ok=True)
    for table_name, contents in tables.items():
        (args.out / f"{table_name}.json").write_text(
            json.dumps(contents, indent=2, sort_keys=True) + "\n"
        )
    shutil.copy(args.base, args.out / "params.yaml")

    row_counts = sum(len(v) for v in tables.values() if isinstance(v, list))
    print(f"wrote {len(tables)} fixture files ({row_counts} total rows) to {args.out}/")


if __name__ == "__main__":
    main()
