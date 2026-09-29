"""Tests for the `python -m engine.testing.synthetic --base <yaml> --out
<dir>` CLI: the YAML config loader (engine.testing.config) and the CLI
shim itself (engine.testing.cli), end to end against the committed
example config in synthetic/base_portfolio.yaml.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from engine.domain import TxnType
from engine.testing.cli import main
from engine.testing.config import load_params
from engine.testing.synthetic import generate_portfolio

_SAMPLE_CONFIG = Path(__file__).parents[1] / "synthetic" / "base_portfolio.yaml"


def test_sample_config_exists() -> None:
    assert _SAMPLE_CONFIG.is_file(), (
        f"{_SAMPLE_CONFIG} is missing -- `python -m engine.testing.synthetic "
        "--base synthetic/base_portfolio.yaml --out ...` depends on it existing"
    )


def test_load_params_from_sample_yaml() -> None:
    params = load_params(_SAMPLE_CONFIG)

    assert params.horizon == 24
    assert params.seed == 20260101

    nodes = params.product_hierarchy.nodes
    channels = params.channel_hierarchy.sub_channels
    assert len(nodes) == 2
    assert len(channels) == 2

    # acquisition_rate: default applied to every (node, channel) pair
    for node in nodes:
        for channel in channels:
            assert params.acquisition_rate[(node, channel)] == Decimal("6")

    assert params.contract_terms == ("monthly", "annual")
    assert params.contract_term_weights == (Decimal("0.7"), Decimal("0.3"))

    # regrade_transition: "uniform" shorthand sums to 1 per source
    for _source, dist in params.regrade_transition.items():
        assert sum(dist.values(), start=Decimal(0)) == Decimal(1)
        assert set(dist) == set(nodes)

    # regrade_hazard: scalar shorthand form, constant across tenure
    assert params.regrade_hazard(0, "monthly") == Decimal("0.02")
    assert params.regrade_hazard(10, "annual") == Decimal("0.02")

    # churn_hazard: table form with a tenure-0 override
    assert params.churn_hazard(0, "monthly") == Decimal("0.0")
    assert params.churn_hazard(1, "monthly") == Decimal("0.05")
    assert params.churn_hazard(0, "annual") == Decimal("0.0")
    assert params.churn_hazard(5, "annual") == Decimal("0.02")

    acquisition_profile = params.closure[(channels[0], TxnType.ACQUISITION)]
    assert acquisition_profile.g == (Decimal("0.5"), Decimal("0.3"), Decimal("0.1"))
    assert acquisition_profile.breakage == Decimal("0.1")
    # same profile object applied to every channel
    for channel in channels:
        assert params.closure[(channel, TxnType.ACQUISITION)] == acquisition_profile

    assert params.order_channel_weight == dict.fromkeys(channels, Decimal(1))
    assert params.initial_base == {}
    assert params.channel_launch_period == {}
    assert params.structural_break is None


def test_generate_portfolio_from_yaml_config_is_valid() -> None:
    params = load_params(_SAMPLE_CONFIG)
    portfolio = generate_portfolio(params)

    assert portfolio.raw_order_event
    assert portfolio.raw_subscription_event
    for book_row in portfolio.raw_order_book_snapshot:
        assert book_row.open_orders >= Decimal(0)
    for base_row in portfolio.raw_base_snapshot:
        assert base_row.opening_base >= Decimal(0)
        assert base_row.closing_base >= Decimal(0)


def test_acquisition_rate_override_rejects_unknown_node() -> None:
    raw = {
        "horizon": 4,
        "seed": 1,
        "product_hierarchy": {"groups": 1, "products_per_group": 1, "variants_per_product": 1},
        "channel_hierarchy": {"groups": 1, "channels_per_group": 1, "sub_channels_per_channel": 1},
        "acquisition_rate": {
            "default": "1",
            "overrides": [{"node": "does-not-exist", "channel": "cg0-c0-s0", "rate": "5"}],
        },
        "contract_terms": [{"term": "monthly", "weight": "1"}],
        "churn_hazard": {"monthly": "0.01"},
        "closure": {
            "acquisition": {"g": ["1"], "breakage": "0"},
            "regrade": {"g": ["1"], "breakage": "0"},
            "churn": {"g": ["1"], "breakage": "0"},
        },
    }
    from engine.testing.config import params_from_dict

    with pytest.raises(ValueError, match="unknown node"):
        params_from_dict(raw)


def test_cli_end_to_end_writes_fixture_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "fixtures" / "base_portfolio"
    main(["--base", str(_SAMPLE_CONFIG), "--out", str(out_dir)])

    expected_files = {
        "raw_order_event.json",
        "raw_subscription_event.json",
        "raw_base_snapshot.json",
        "raw_order_book_snapshot.json",
        "params.yaml",
    }
    assert {p.name for p in out_dir.iterdir()} == expected_files

    order_events = json.loads((out_dir / "raw_order_event.json").read_text())
    assert isinstance(order_events, list)
    assert order_events
    assert {"node", "order_channel", "txn_type", "raise_period", "event_type", "count"} <= set(
        order_events[0]
    )

    assert (out_dir / "params.yaml").read_text() == _SAMPLE_CONFIG.read_text()


def test_cli_is_deterministic_given_same_seed(tmp_path: Path) -> None:
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    main(["--base", str(_SAMPLE_CONFIG), "--out", str(out_a)])
    main(["--base", str(_SAMPLE_CONFIG), "--out", str(out_b)])

    for name in ("raw_order_event.json", "raw_subscription_event.json"):
        assert (out_a / name).read_text() == (out_b / name).read_text()
