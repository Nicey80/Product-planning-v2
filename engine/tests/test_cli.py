"""Tests for the `python -m engine.testing.synthetic --base <yaml> --out
<dir>` CLI: the YAML config loader (engine.testing.config) and the CLI
shim itself (engine.testing.cli).

Uses tests/fixtures/small_portfolio.yaml, a minimal config that exercises
every required schema section -- not synthetic/base_portfolio.yaml, the
real-scale example, which takes on the order of minutes to generate and is
deliberately excluded from the automated suite (see its own header
comment).
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from engine.domain import NodeId, Period, TxnType
from engine.testing.cli import main
from engine.testing.config import (
    SchemaNotImplementedError,
    apply_scenario_patch,
    load_params,
    load_params_with_scenario,
    load_scenario_patch,
    params_from_dict,
)
from engine.testing.synthetic import generate_portfolio

_SAMPLE_CONFIG = Path(__file__).parent / "fixtures" / "small_portfolio.yaml"
_SAMPLE_SCENARIOS = Path(__file__).parent / "fixtures" / "small_scenarios.yaml"


def _raw_config() -> dict[str, Any]:
    """A fresh, mutable copy of the sample config's parsed YAML, for tests
    that need to tweak a single section without hand-authoring a second
    full config."""
    return copy.deepcopy(yaml.safe_load(_SAMPLE_CONFIG.read_text()))


def test_sample_config_exists() -> None:
    assert _SAMPLE_CONFIG.is_file(), f"{_SAMPLE_CONFIG} is missing"


def test_load_params_from_sample_yaml() -> None:
    params = load_params(_SAMPLE_CONFIG)

    assert params.horizon == 8
    assert params.seed == 42

    nodes = params.product_hierarchy.nodes
    channels = params.channel_hierarchy.sub_channels
    assert set(nodes) == {"n0", "n1"}
    assert set(channels) == {"c0", "c1"}

    assert params.contract_terms == (1, 12)
    for channel in channels:
        assert sum(params.contract_term_mix[channel].values(), start=Decimal(0)) == Decimal(1)

    # regrade_transition: fully specified, sums to 1 per source
    for _source, dist in params.regrade_transition.items():
        if dist:
            assert sum(dist.values(), start=Decimal(0)) == Decimal(1)

    # channel_mix: compositional, sums to 1 per node per period
    for node in nodes:
        for period, shares in params.channel_mix[node].items():
            assert sum(shares.values(), start=Decimal(0)) == Decimal(1), (node, period)

    product = params.product_hierarchy.product_of[nodes[0]]
    acquisition_profile = params.closure[(channels[0], TxnType.ACQUISITION, product)]
    assert sum(acquisition_profile.g, start=Decimal(0)) + acquisition_profile.breakage == Decimal(1)

    assert params.node_launch_period == {}
    assert params.channel_launch_period == {}
    assert params.structural_break is None
    assert params.migrations == ()


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


def test_acquisition_missing_node_level_raises() -> None:
    """Every real node needs an explicit acquisition.nodes[node].level --
    there is no portfolio-wide default level to silently fall back to."""
    raw = _raw_config()
    raw["acquisition"]["nodes"] = {"does-not-exist": {"level": "5"}}

    with pytest.raises(KeyError, match="level"):
        params_from_dict(raw)


def test_deferred_complexity_flag_raises() -> None:
    raw = _raw_config()
    raw["pipeline"]["complexity_flags"] = {"new_site": {"probability": 0.5}}

    with pytest.raises(SchemaNotImplementedError, match="complexity_flags"):
        params_from_dict(raw)


def test_deferred_calendar_effects_raises() -> None:
    raw = _raw_config()
    raw["pipeline"]["calendar_effects"] = {"month_end_surge": {}}

    with pytest.raises(SchemaNotImplementedError, match="calendar_effects"):
        params_from_dict(raw)


def test_deferred_amendments_raises() -> None:
    raw = _raw_config()
    raw["pipeline"]["amendments"] = {"enabled": True}

    with pytest.raises(SchemaNotImplementedError, match="amendments"):
        params_from_dict(raw)


def test_deferred_capacity_raises() -> None:
    raw = _raw_config()
    raw["pipeline"]["capacity"] = {"enabled": True}

    with pytest.raises(SchemaNotImplementedError, match="capacity"):
        params_from_dict(raw)


def test_cli_end_to_end_writes_fixture_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "fixtures" / "small_portfolio"
    main(["--base", str(_SAMPLE_CONFIG), "--out", str(out_dir)])

    expected_files = {
        "raw_order_event.json",
        "raw_subscription_event.json",
        "raw_base_snapshot.json",
        "raw_order_book_snapshot.json",
        "dim_product.json",
        "dim_channel.json",
        "truth.json",
        "params.yaml",
    }
    assert {p.name for p in out_dir.iterdir()} == expected_files

    order_events = json.loads((out_dir / "raw_order_event.json").read_text())
    assert isinstance(order_events, list)
    assert order_events
    assert {"node", "order_channel", "txn_type", "raise_period", "event_type", "count"} <= set(
        order_events[0]
    )

    dim_product = json.loads((out_dir / "dim_product.json").read_text())
    assert {row["node"] for row in dim_product} == {"n0", "n1"}

    truth = json.loads((out_dir / "truth.json").read_text())
    assert "closure_kernel_g_k" in truth
    assert "regrade_transition_matrix" in truth

    assert (out_dir / "params.yaml").read_text() == _SAMPLE_CONFIG.read_text()


def test_cli_is_deterministic_given_same_seed(tmp_path: Path) -> None:
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    main(["--base", str(_SAMPLE_CONFIG), "--out", str(out_a)])
    main(["--base", str(_SAMPLE_CONFIG), "--out", str(out_b)])

    for name in ("raw_order_event.json", "raw_subscription_event.json"):
        assert (out_a / name).read_text() == (out_b / name).read_text()


# ---------------------------------------------------------------------------
# --scenario / --name: deep-merging a named scenarios.yaml patch onto --base
# ---------------------------------------------------------------------------


def test_load_scenario_patch_returns_named_patch() -> None:
    patch = load_scenario_patch(_SAMPLE_SCENARIOS, "bump_n0")
    assert patch["acquisition.nodes.n0.level"] == 35
    assert patch["pipeline.max_order_age_periods"] == 6


def test_load_scenario_patch_unknown_name_raises() -> None:
    with pytest.raises(KeyError, match="bump_n0"):
        load_scenario_patch(_SAMPLE_SCENARIOS, "does-not-exist")


def test_apply_scenario_patch_replaces_leaf_without_disturbing_siblings() -> None:
    raw = _raw_config()
    patch = load_scenario_patch(_SAMPLE_SCENARIOS, "bump_n0")
    merged = apply_scenario_patch(raw, patch)

    assert merged["acquisition"]["nodes"]["n0"]["level"] == 35
    assert merged["pipeline"]["max_order_age_periods"] == 6
    # untouched siblings/sections are preserved exactly
    assert merged["acquisition"]["nodes"]["n1"] == raw["acquisition"]["nodes"]["n1"]
    assert merged["pipeline"]["defaults"] == raw["pipeline"]["defaults"]
    # the original dict passed in is never mutated
    assert raw["acquisition"]["nodes"]["n0"]["level"] == 20


def test_apply_scenario_patch_indexes_into_lists() -> None:
    raw = _raw_config()
    patch = load_scenario_patch(_SAMPLE_SCENARIOS, "late_launch_n1")
    merged = apply_scenario_patch(raw, patch)

    variants = merged["product_hierarchy"][0]["products"][0]["variants"]
    assert variants[0] == {"node": "n0"}  # untouched sibling variant
    assert variants[1] == {"node": "n1", "launch": "2024-05"}


def test_load_params_with_scenario_resolves_patched_params() -> None:
    params, merged = load_params_with_scenario(_SAMPLE_CONFIG, _SAMPLE_SCENARIOS, "bump_n0")

    assert params.horizon == 8  # untouched
    periods = sorted(params.acquisition[NodeId("n0")].mean_by_period)
    assert params.acquisition[NodeId("n0")].mean_by_period[periods[0]] == Decimal("35")
    assert merged["meta"]["name"] == "bump_n0"


def test_load_params_with_scenario_late_launch_sets_node_launch_period() -> None:
    params, _merged = load_params_with_scenario(_SAMPLE_CONFIG, _SAMPLE_SCENARIOS, "late_launch_n1")
    assert params.node_launch_period[NodeId("n1")] == int(
        Period(4)
    )  # 2024-05, 4 months after 2024-01


def test_load_params_with_scenario_deferred_section_raises() -> None:
    with pytest.raises(SchemaNotImplementedError, match="capacity"):
        load_params_with_scenario(_SAMPLE_CONFIG, _SAMPLE_SCENARIOS, "turn_on_capacity")


def test_cli_scenario_and_name_must_be_given_together(tmp_path: Path) -> None:
    out_dir = str(tmp_path / "out")
    with pytest.raises(SystemExit):
        main(
            ["--base", str(_SAMPLE_CONFIG), "--scenario", str(_SAMPLE_SCENARIOS), "--out", out_dir]
        )
    with pytest.raises(SystemExit):
        main(["--base", str(_SAMPLE_CONFIG), "--name", "bump_n0", "--out", out_dir])


def test_cli_unknown_scenario_name_exits_cleanly(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "--base",
                str(_SAMPLE_CONFIG),
                "--scenario",
                str(_SAMPLE_SCENARIOS),
                "--name",
                "does-not-exist",
                "--out",
                str(tmp_path / "out"),
            ]
        )


def test_cli_with_scenario_writes_merged_params_yaml(tmp_path: Path) -> None:
    out_dir = tmp_path / "bump_n0"
    main(
        [
            "--base",
            str(_SAMPLE_CONFIG),
            "--scenario",
            str(_SAMPLE_SCENARIOS),
            "--name",
            "bump_n0",
            "--out",
            str(out_dir),
        ]
    )

    written = yaml.safe_load((out_dir / "params.yaml").read_text())
    assert written["meta"]["name"] == "bump_n0"
    assert written["acquisition"]["nodes"]["n0"]["level"] == 35
    # the on-disk base config itself is untouched
    assert yaml.safe_load(_SAMPLE_CONFIG.read_text())["acquisition"]["nodes"]["n0"]["level"] == 20

    dim_product = json.loads((out_dir / "dim_product.json").read_text())
    assert {row["node"] for row in dim_product} == {"n0", "n1"}
