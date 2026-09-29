"""YAML config loading for `python -m engine.testing.synthetic`'s CLI.

Not used by the library API itself: everywhere else in this package
(engine/tests/golden_synthetic_scenario.py, every engine/tests/test_synthetic_*.py)
builds a SyntheticParams directly in Python. This module exists purely so
a ground-truth parameter set can be handed to the CLI as a text file
instead of code -- see synthetic/base_portfolio.yaml (repo root of the
engine package) for a complete, annotated example, and the section
docstrings below for what each key means and accepts.

Every probability/rate/weight value is read as a string and converted via
Decimal(str(...)) -- never float(...) -- so "0.1" in the YAML produces the
exact Decimal engine's identities need, not a binary-float approximation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from engine.domain import ChannelId, NodeId, TxnType
from engine.testing.synthetic import (
    ClosureProfile,
    HazardFn,
    StructuralBreak,
    SyntheticParams,
    make_channel_hierarchy,
    make_product_hierarchy,
)

_TXN_TYPE_KEYS = (
    ("acquisition", TxnType.ACQUISITION),
    ("regrade", TxnType.REGRADE),
    ("churn", TxnType.CHURN),
)


def load_params(path: Path | str) -> SyntheticParams:
    """Parse a YAML ground-truth config into a SyntheticParams. Raises
    KeyError/ValueError with the offending key on a missing or malformed
    section -- see synthetic/base_portfolio.yaml for the expected shape."""
    raw = yaml.safe_load(Path(path).read_text())
    return params_from_dict(raw)


def params_from_dict(raw: Mapping[str, Any]) -> SyntheticParams:
    product_hierarchy = make_product_hierarchy(
        groups=raw["product_hierarchy"]["groups"],
        products_per_group=raw["product_hierarchy"]["products_per_group"],
        variants_per_product=raw["product_hierarchy"]["variants_per_product"],
    )
    channel_hierarchy = make_channel_hierarchy(
        groups=raw["channel_hierarchy"]["groups"],
        channels_per_group=raw["channel_hierarchy"]["channels_per_group"],
        sub_channels_per_channel=raw["channel_hierarchy"]["sub_channels_per_channel"],
    )
    nodes = product_hierarchy.nodes
    channels = channel_hierarchy.sub_channels

    contract_terms, contract_term_weights = _load_contract_terms(raw["contract_terms"])

    return SyntheticParams(
        product_hierarchy=product_hierarchy,
        channel_hierarchy=channel_hierarchy,
        horizon=raw["horizon"],
        seed=raw["seed"],
        acquisition_rate=_load_acquisition_rate(
            raw["acquisition_rate"], nodes=nodes, channels=channels
        ),
        contract_terms=contract_terms,
        contract_term_weights=contract_term_weights,
        regrade_transition=_load_regrade_transition(
            raw.get("regrade_transition", "uniform"), nodes=nodes
        ),
        regrade_hazard=_load_hazard_fn(
            raw.get("regrade_hazard", {}), contract_terms=contract_terms
        ),
        churn_hazard=_load_hazard_fn(raw["churn_hazard"], contract_terms=contract_terms),
        closure=_load_closure(raw["closure"], channels=channels),
        order_channel_weight=_load_channel_weight(
            raw.get("order_channel_weight", "uniform"), channels=channels
        ),
        initial_base={NodeId(k): Decimal(str(v)) for k, v in raw.get("initial_base", {}).items()},
        channel_launch_period={
            ChannelId(k): int(v) for k, v in raw.get("channel_launch_period", {}).items()
        },
        structural_break=_load_structural_break(raw.get("structural_break")),
    )


def _load_acquisition_rate(
    cfg: Mapping[str, Any], *, nodes: Sequence[NodeId], channels: Sequence[ChannelId]
) -> dict[tuple[NodeId, ChannelId], Decimal]:
    """
    acquisition_rate:
      default: "6"           # applied to every (node, channel) pair
      overrides:              # optional, specific pairs (node/channel ids
        - node: pg0-p0-v0     # are only known once product_hierarchy /
          channel: cg0-c0-s0  # channel_hierarchy above have been sized --
          rate: "10"          # see the generated ids by running the CLI
    """
    default = Decimal(str(cfg.get("default", "0")))
    rates = {(n, c): default for n in nodes for c in channels}
    for override in cfg.get("overrides", []):
        node = NodeId(override["node"])
        channel = ChannelId(override["channel"])
        if node not in nodes:
            raise ValueError(f"acquisition_rate override references unknown node {node!r}")
        if channel not in channels:
            raise ValueError(f"acquisition_rate override references unknown channel {channel!r}")
        rates[(node, channel)] = Decimal(str(override["rate"]))
    return rates


def _load_contract_terms(
    cfg: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, ...], tuple[Decimal, ...]]:
    """
    contract_terms:
      - term: monthly
        weight: "0.7"
      - term: annual
        weight: "0.3"
    """
    terms = tuple(str(entry["term"]) for entry in cfg)
    weights = tuple(Decimal(str(entry["weight"])) for entry in cfg)
    return terms, weights


def _load_regrade_transition(
    cfg: Any, *, nodes: Sequence[NodeId]
) -> dict[NodeId, dict[NodeId, Decimal]]:
    """
    regrade_transition: uniform    # every node regrades to every node
                                    # (including itself) with equal weight
    # or, explicit per-source destination distributions:
    # regrade_transition:
    #   pg0-p0-v0: {pg0-p0-v0: "0.2", pg0-p0-v1: "0.8"}
    #   pg0-p0-v1: {pg0-p0-v0: "0.6", pg0-p0-v1: "0.4"}
    """
    if cfg == "uniform":
        weight = Decimal(1) / len(nodes)
        return {n: dict.fromkeys(nodes, weight) for n in nodes}
    result: dict[NodeId, dict[NodeId, Decimal]] = {}
    for source, dist in cfg.items():
        src = NodeId(source)
        if src not in nodes:
            raise ValueError(f"regrade_transition references unknown source node {src!r}")
        result[src] = {}
        for dest, weight in dist.items():
            d = NodeId(dest)
            if d not in nodes:
                raise ValueError(f"regrade_transition references unknown destination node {d!r}")
            result[src][d] = Decimal(str(weight))
    return result


def _load_hazard_fn(cfg: Mapping[str, Any], *, contract_terms: Sequence[str]) -> HazardFn:
    """
    churn_hazard:                  # (or regrade_hazard, same shape)
      monthly: "0.05"              # shorthand: a single constant rate
      annual:                      # or a tenure-dependent table:
        default: "0.02"            # rate for any tenure not listed below
        by_tenure:
          0: "0.0"                 # can't churn in their acquisition period
          1: "0.01"
    Every contract_term must have an entry; missing terms default to 0.
    """
    tables: dict[str, tuple[Decimal, dict[int, Decimal]]] = {}
    for term in contract_terms:
        term_cfg = cfg.get(term, {})
        if isinstance(term_cfg, str | int | float):
            tables[term] = (Decimal(str(term_cfg)), {})
        else:
            default = Decimal(str(term_cfg.get("default", "0")))
            table = {int(k): Decimal(str(v)) for k, v in term_cfg.get("by_tenure", {}).items()}
            tables[term] = (default, table)

    def hazard(tenure: int, term: str) -> Decimal:
        default, table = tables[term]
        return table.get(tenure, default)

    return hazard


def _load_closure(
    cfg: Mapping[str, Mapping[str, Any]], *, channels: Sequence[ChannelId]
) -> dict[tuple[ChannelId, TxnType], ClosureProfile]:
    """
    closure:
      acquisition: {g: ["0.5", "0.3", "0.1"], breakage: "0.1"}
      regrade:     {g: ["0.6", "0.2"],        breakage: "0.2"}
      churn:       {g: ["0.8"],               breakage: "0.2"}
    One profile per txn_type, applied to every channel -- per-channel
    closure differences aren't expressible in this schema yet; build
    SyntheticParams directly in Python if you need that.
    """
    result: dict[tuple[ChannelId, TxnType], ClosureProfile] = {}
    for key, txn_type in _TXN_TYPE_KEYS:
        profile_cfg = cfg[key]
        profile = ClosureProfile(
            g=tuple(Decimal(str(v)) for v in profile_cfg["g"]),
            breakage=Decimal(str(profile_cfg["breakage"])),
        )
        for channel in channels:
            result[(channel, txn_type)] = profile
    return result


def _load_channel_weight(cfg: Any, *, channels: Sequence[ChannelId]) -> dict[ChannelId, Decimal]:
    """
    order_channel_weight: uniform   # equal weight for every channel
    # or explicit: {cg0-c0-s0: "2", cg0-c0-s1: "1"}
    """
    if cfg == "uniform":
        return {c: Decimal(1) for c in channels}
    return {ChannelId(k): Decimal(str(v)) for k, v in cfg.items()}


def _load_structural_break(cfg: Mapping[str, Any] | None) -> StructuralBreak | None:
    """
    structural_break:                    # optional; omit for none
      break_period: 12
      acquisition_multiplier: "2"        # default "1" (no change)
      churn_multiplier: "1.5"            # default "1" (no change)
    """
    if not cfg:
        return None
    return StructuralBreak(
        break_period=cfg["break_period"],
        acquisition_multiplier=Decimal(str(cfg.get("acquisition_multiplier", "1"))),
        churn_multiplier=Decimal(str(cfg.get("churn_multiplier", "1"))),
    )
