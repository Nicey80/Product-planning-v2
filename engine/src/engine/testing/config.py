"""YAML config loading for `python -m engine.testing.synthetic`'s CLI.

Not used by the library API itself: everywhere else in this package
(engine/tests/golden_synthetic_scenario.py, every engine/tests/
test_synthetic_*.py) builds a SyntheticParams directly in Python. This
module exists purely so a ground-truth parameter set can be handed to the
CLI as a text file instead of code -- see synthetic/base_portfolio.yaml
(repo root of the engine package) for a complete, annotated example, and
each loader function's docstring below for the exact shape it accepts.

Every probability/rate/weight value is read as a string and converted via
Decimal(str(...)) -- never float(...) -- so "0.1" in the YAML produces the
exact Decimal engine's identities need, not a binary-float approximation.
The one deliberate exception is the continuous cycle-time -> discrete
kernel derivation (_derive_closure_profile), which is inherently a Monte
Carlo approximation and uses float math throughout, same as this package's
existing _poisson/_negative_binomial samplers.

Scope: this loader implements the acquisition -> order-pipeline -> base
chain faithfully (calendar periods, named hierarchies with launch/retire,
regressors, the full acquisition process, continuous cycle-time -> kernel,
expiry-window churn/regrade hazards, contract extensions, migrations, and
the pathology-rate/self-check knobs). It deliberately does NOT implement
complexity_flags, calendar_effects (month-end raise surge, install
freeze), amendments, or capacity constraints -- seeing any of those
configured with real effect raises SchemaNotImplementedError rather than
silently ignoring config that would otherwise change the ground truth.
`break_timing` (early vs spread) is parsed but not behaviorally
implemented: breakage is always recognized at raise time (age 0),
matching engine.kernel's existing, unchanged convention -- changing that
would mean changing engine/kernel.py itself, which this module never does.

Scenarios (synthetic/scenarios.yaml, loaded via load_params_with_scenario
and the CLI's --scenario/--name) deep-merge a named, dotted-path patch
onto a base config before resolving it -- see apply_scenario_patch for the
merge semantics and scenarios.yaml's own header for which scenarios hit a
still-deferred section (and so still raise SchemaNotImplementedError) or
a richer-than-implemented shape (structural_break's target/changes,
bulk_closure_batch's period/volume/drawn_from_ages).
"""

from __future__ import annotations

import copy
import math
import random
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from engine.domain import ChannelId, NodeId, Period, TxnType
from engine.testing.calendar import MonthlyCalendar, month_abbreviation, parse_month
from engine.testing.regressors import (
    resolve_campaign_multiplier,
    resolve_series,
    resolve_working_days,
)
from engine.testing.synthetic import (
    EXTEND_SAME,
    AcquisitionProcess,
    ChannelHierarchy,
    ClosureProfile,
    HazardContext,
    HazardFn,
    MigrationSchedule,
    PathologyRates,
    ProductHierarchy,
    SelfCheckConfig,
    StructuralBreak,
    SyntheticParams,
)

_ZERO = Decimal(0)
_ONE = Decimal(1)
_Z90 = 1.2815515655446004  # standard normal quantile at p=0.9, for lognormal median/p90 -> mu/sigma

_DEFERRED_SECTIONS = (
    "complexity_flags",
    "calendar_effects",
    "amendments",
    "capacity",
)


class SchemaNotImplementedError(NotImplementedError):
    """Raised when a config enables a section this loader deliberately
    doesn't implement yet (see the module docstring's Scope paragraph),
    rather than silently ignoring config that would otherwise change the
    ground truth."""


def load_params(path: Path | str) -> SyntheticParams:
    raw = yaml.safe_load(Path(path).read_text())
    return params_from_dict(raw)


def load_params_with_scenario(
    base_path: Path | str, scenario_path: Path | str, scenario_name: str
) -> tuple[SyntheticParams, dict[str, Any]]:
    """Loads `base_path`'s config, applies the named scenario's patch from
    `scenario_path` (see apply_scenario_patch), and returns both the
    resolved SyntheticParams and the merged raw config dict -- the CLI
    writes the latter as params.yaml's provenance copy when a scenario is
    applied, since the untouched base file no longer describes what was
    actually run."""
    raw = yaml.safe_load(Path(base_path).read_text())
    patch = load_scenario_patch(scenario_path, scenario_name)
    merged = apply_scenario_patch(raw, patch)
    return params_from_dict(merged), merged


def load_scenario_patch(path: Path | str, name: str) -> dict[str, Any]:
    """Loads a scenarios.yaml file (see synthetic/scenarios.yaml) and
    returns the named scenario's `patch` mapping: dotted-path -> value,
    e.g. {"pipeline.max_order_age_periods": 10}."""
    raw = yaml.safe_load(Path(path).read_text())
    scenarios = raw.get("scenarios", {})
    if name not in scenarios:
        available = ", ".join(sorted(scenarios)) or "(none defined)"
        raise KeyError(f"no scenario named {name!r} in {path} -- available: {available}")
    return dict(scenarios[name].get("patch", {}))


def apply_scenario_patch(raw: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Applies a scenario's dotted-path patch onto a deep copy of `raw` (a
    config's parsed YAML). Each dotted path (e.g.
    "pipeline.defaults.cycle_time" or
    "product_hierarchy.0.products.1.variants.2.launch") navigates into
    nested dicts/lists -- a numeric segment indexes into a list, creating
    intermediate dicts if a dict segment is missing -- and REPLACES
    whatever sits at that exact leaf wholesale. This is "deep merge" only
    in the sense that every path *not* named in the patch is left
    untouched; a dict value given in the patch does not get merged into
    whatever dict was already at that leaf, it replaces it outright."""
    merged = copy.deepcopy(dict(raw))
    for dotted_path, value in patch.items():
        _set_patch_path(merged, dotted_path.split("."), value)
    return merged


def _set_patch_path(container: Any, segments: Sequence[str], value: Any) -> None:
    key = segments[0]
    if len(segments) == 1:
        if isinstance(container, list):
            container[int(key)] = value
        else:
            container[key] = value
        return
    child = container[int(key)] if isinstance(container, list) else container.setdefault(key, {})
    _set_patch_path(child, segments[1:], value)


def params_from_dict(raw: Mapping[str, Any]) -> SyntheticParams:
    meta = raw["meta"]
    calendar = MonthlyCalendar(epoch=parse_month(meta["history_start"]))
    observed_cutoff = calendar.period_of(meta["book_snapshot_date"])
    horizon = int(observed_cutoff) + 1 + int(meta["forecast_horizon_months"])
    periods = [Period(t) for t in range(horizon)]

    _check_deferred_sections(raw.get("pipeline", {}))

    product_hierarchy, node_launch_period, node_retire_period = _build_product_hierarchy(
        raw["product_hierarchy"], calendar
    )
    channel_hierarchy, channel_launch_period = _build_channel_hierarchy(
        raw["channel_hierarchy"], calendar
    )
    nodes = product_hierarchy.nodes
    channels = channel_hierarchy.sub_channels

    regressors = _resolve_regressors(
        raw.get("regressors", {}),
        nodes=nodes,
        channels=channels,
        periods=periods,
        calendar=calendar,
        observed_cutoff=observed_cutoff,
    )

    contract_terms = tuple(int(t) for t in raw["contract_terms"])
    contract_term_mix = _resolve_contract_term_mix(
        raw["acquisition"]["contract_term_mix"], channels
    )

    channel_mix = _resolve_acquisition_channel_mix(
        raw["acquisition"]["channel_mix"], nodes=nodes, channels=channels, periods=periods
    )
    campaign_multiplier = _campaign_multiplier(
        raw.get("regressors", {}), channels=channels, periods=periods, calendar=calendar
    )
    acquisition = _resolve_acquisition(
        raw["acquisition"],
        nodes=nodes,
        periods=periods,
        calendar=calendar,
        regressors=regressors,
        channel_mix=channel_mix,
        campaign_multiplier=campaign_multiplier,
    )

    rng_seed = int(meta["seed"])
    closure = _resolve_closure(
        raw["pipeline"],
        product_hierarchy=product_hierarchy,
        channels=channels,
        max_age_periods=int(raw["pipeline"]["max_order_age_periods"]),
        calendar=calendar,
        seed=rng_seed,
    )

    regrade_hazard = _build_hazard_fn(
        raw["regrade"]["hazard"], calendar=calendar, node_specific=False
    )
    churn_hazard = _build_hazard_fn(raw["churn"]["hazard"], calendar=calendar, node_specific=True)
    regrade_transition = _resolve_regrade_transition(raw["regrade"]["transitions"], nodes)
    regrade_order_channel_mix = _resolve_flat_compositional_mix(
        raw["regrade"]["order_channel_mix"], keys=channels, periods=periods
    )

    order_channel_weight = {
        c: Decimal(1) for c in channels
    }  # churn's order channel: flat/uniform -- the spec gives no churn order_channel section

    migrations = _resolve_migrations(raw.get("migrations", []), calendar=calendar)
    pathology_rates = _resolve_pathology_rates(raw.get("pathologies", {}))
    self_check = _resolve_self_check(raw.get("output", {}).get("self_check", {}))
    structural_break = _resolve_structural_break(
        raw.get("pathologies", {}).get("structural_break", {})
    )

    return SyntheticParams(
        product_hierarchy=product_hierarchy,
        channel_hierarchy=channel_hierarchy,
        horizon=horizon,
        seed=rng_seed,
        acquisition=acquisition,
        channel_mix=channel_mix,
        contract_terms=contract_terms,
        contract_term_mix=contract_term_mix,
        regrade_transition=regrade_transition,
        regrade_hazard=regrade_hazard,
        churn_hazard=churn_hazard,
        closure=closure,
        order_channel_weight=order_channel_weight,
        regrade_order_channel_mix=regrade_order_channel_mix,
        regrade_extension_with_move_rate=Decimal(
            str(raw["regrade"].get("extension_with_move_rate", "0"))
        ),
        migrations=migrations,
        pathology_rates=pathology_rates,
        self_check=self_check,
        channel_launch_period=channel_launch_period,
        node_launch_period=node_launch_period,
        node_retire_period=node_retire_period,
        structural_break=structural_break,
    )


def _check_deferred_sections(pipeline_cfg: Mapping[str, Any]) -> None:
    complexity_flags = pipeline_cfg.get("complexity_flags", {})
    for name, cfg in complexity_flags.items():
        if Decimal(str(cfg.get("probability", "0"))) > 0:
            raise SchemaNotImplementedError(
                f"pipeline.complexity_flags.{name} is not implemented yet (Stage 2) -- "
                "set its probability to 0 to generate without it"
            )

    calendar_effects = pipeline_cfg.get("calendar_effects", {})
    if calendar_effects:
        raise SchemaNotImplementedError(
            "pipeline.calendar_effects is not implemented yet (Stage 2) -- remove it to generate"
        )

    amendments = pipeline_cfg.get("amendments", {})
    if amendments.get("enabled", False):
        raise SchemaNotImplementedError(
            "pipeline.amendments is not implemented yet (Stage 2) -- set enabled: false to generate"
        )

    capacity = pipeline_cfg.get("capacity", {})
    if capacity.get("enabled", False):
        raise SchemaNotImplementedError(
            "pipeline.capacity is not implemented yet (Stage 2) -- set enabled: false to generate"
        )


# ---------------------------------------------------------------------------
# Hierarchies
# ---------------------------------------------------------------------------


def _build_product_hierarchy(
    raw: Sequence[Mapping[str, Any]], calendar: MonthlyCalendar
) -> tuple[ProductHierarchy, dict[NodeId, int], dict[NodeId, int]]:
    nodes: list[NodeId] = []
    product_of: dict[NodeId, str] = {}
    group_of_product: dict[str, str] = {}
    node_launch: dict[NodeId, int] = {}
    node_retire: dict[NodeId, int] = {}
    for group_cfg in raw:
        group = group_cfg["group"]
        for product_cfg in group_cfg["products"]:
            product = product_cfg["product"]
            group_of_product[product] = group
            for variant_cfg in product_cfg["variants"]:
                node = NodeId(variant_cfg["node"])
                nodes.append(node)
                product_of[node] = product
                if "launch" in variant_cfg:
                    node_launch[node] = int(calendar.period_of(variant_cfg["launch"]))
                if "retire" in variant_cfg:
                    node_retire[node] = int(calendar.period_of(variant_cfg["retire"]))
    return ProductHierarchy(tuple(nodes), product_of, group_of_product), node_launch, node_retire


def _build_channel_hierarchy(
    raw: Sequence[Mapping[str, Any]], calendar: MonthlyCalendar
) -> tuple[ChannelHierarchy, dict[ChannelId, int]]:
    """The spec's channel hierarchy is only 2 levels (group -> leaf
    channel), unlike ChannelHierarchy's 3-level shape (sub_channel ->
    channel -> channel_group). Each leaf is treated as its own "channel"
    parent (channel_of[leaf] = leaf) so roll_up's machinery still works
    unchanged rather than inventing a synthetic middle-tier name the
    source data doesn't have."""
    subs: list[ChannelId] = []
    channel_of: dict[ChannelId, str] = {}
    group_of_channel: dict[str, str] = {}
    launch: dict[ChannelId, int] = {}
    for group_cfg in raw:
        group = group_cfg["group"]
        for channel_cfg in group_cfg["channels"]:
            channel = ChannelId(channel_cfg["channel"])
            subs.append(channel)
            channel_of[channel] = channel
            group_of_channel[channel] = group
            if "launch" in channel_cfg:
                launch[channel] = int(calendar.period_of(channel_cfg["launch"]))
    return ChannelHierarchy(tuple(subs), channel_of, group_of_channel), launch


# ---------------------------------------------------------------------------
# Regressors
# ---------------------------------------------------------------------------


def _resolve_regressors(
    raw: Mapping[str, Any],
    *,
    nodes: Sequence[NodeId],
    channels: Sequence[ChannelId],
    periods: Sequence[Period],
    calendar: MonthlyCalendar,
    observed_cutoff: Period,
) -> dict[str, dict[str, dict[Period, Decimal]]]:
    """Resolves every regressor section present, keyed by regressor name
    then by node/channel id. `contracts_expiring` (derived_from_cohorts)
    and `campaign_flags` (handled separately, it's a multiplier not a
    level) are not included here."""
    resolved: dict[str, dict[str, dict[Period, Decimal]]] = {}

    if "working_days" in raw:
        days = resolve_working_days(calendar, periods)
        resolved["working_days"] = {"*": days}

    if "relative_price_index" in raw:
        cfg = raw["relative_price_index"]
        events = [*cfg.get("events", []), *cfg.get("forward", [])]
        resolved["relative_price_index"] = resolve_series(
            keys=list(nodes),
            baseline=Decimal(str(cfg.get("baseline", "1.0"))),
            periods=periods,
            calendar=calendar,
            observed_cutoff=observed_cutoff,
            key_field="node",
            value_field="multiplier",
            events=events,
            forward_assumption="hold_last",
        )

    if "marketing_spend" in raw:
        cfg = raw["marketing_spend"]
        seasonality = {k: Decimal(str(v)) for k, v in cfg.get("seasonality", {}).items()}
        resolved["marketing_spend"] = resolve_series(
            keys=list(channels),
            baseline={k: Decimal(str(v)) for k, v in cfg["baseline"].items()},
            periods=periods,
            calendar=calendar,
            observed_cutoff=observed_cutoff,
            key_field="channel",
            value_field="multiplier",
            seasonality=seasonality,
            forward_assumption=cfg.get("forward_assumption", "flat_at_last_12m_mean"),
        )

    if "sales_headcount" in raw:
        cfg = raw["sales_headcount"]
        resolved["sales_headcount"] = resolve_series(
            keys=list(channels),
            baseline={k: Decimal(str(v)) for k, v in cfg["baseline"].items()},
            periods=periods,
            calendar=calendar,
            observed_cutoff=observed_cutoff,
            key_field="channel",
            value_field="level",
            events=cfg.get("events", []),
            forward_assumption=cfg.get("forward_assumption", "hold_last"),
        )

    return resolved


def _campaign_multiplier(
    regressors_cfg: Mapping[str, Any],
    *,
    channels: Sequence[ChannelId],
    periods: Sequence[Period],
    calendar: MonthlyCalendar,
) -> dict[ChannelId, dict[Period, Decimal]]:
    cfg = regressors_cfg.get("campaign_flags")
    if cfg is None:
        return {c: dict.fromkeys(periods, _ONE) for c in channels}
    result = resolve_campaign_multiplier(
        cfg["events"], keys=list(channels), periods=periods, calendar=calendar
    )
    return {ChannelId(k): v for k, v in result.items()}


# ---------------------------------------------------------------------------
# Compositional (log-ratio drift) mixes -- acquisition.channel_mix and
# regrade.order_channel_mix both use this shape.
# ---------------------------------------------------------------------------


def _resolve_compositional_mix(
    base_shares: Mapping[str, tuple[Decimal, Decimal]], *, periods: Sequence[Period]
) -> dict[Period, dict[str, Decimal]]:
    """Log-ratio compositional drift: log_share[k](t) = log(share_k) + t *
    log(1 + drift_k_pct_per_month / 100), renormalized (softmax-style) so
    every period's shares sum to exactly 1. A zero-share key stays exactly
    0 at every period -- a true zero can't be reached by log-ratio drift,
    which matches the source config's intent (retention/renewal channels
    that "raise almost no acquisitions").
    """
    nonzero_keys = [k for k, (share, _drift) in base_shares.items() if share > 0]
    zero_keys = [k for k in base_shares if k not in nonzero_keys]
    log_share0 = {k: math.log(float(base_shares[k][0])) for k in nonzero_keys}
    log_drift = {k: math.log(1.0 + float(base_shares[k][1]) / 100.0) for k in nonzero_keys}

    result: dict[Period, dict[str, Decimal]] = {}
    for t in periods:
        raw_values = {k: math.exp(log_share0[k] + int(t) * log_drift[k]) for k in nonzero_keys}
        total = sum(raw_values.values())
        shares = {k: Decimal(str(raw_values[k] / total)) for k in nonzero_keys} if total > 0 else {}
        residual = _ONE - sum(shares.values(), start=_ZERO)
        if shares:
            largest = max(shares, key=lambda k: shares[k])
            shares[largest] += residual
        for k in zero_keys:
            shares[k] = _ZERO
        result[t] = shares
    return result


def _resolve_acquisition_channel_mix(
    raw: Mapping[str, Any],
    *,
    nodes: Sequence[NodeId],
    channels: Sequence[ChannelId],
    periods: Sequence[Period],
) -> dict[NodeId, dict[Period, dict[ChannelId, Decimal]]]:
    """acquisition.channel_mix: a `defaults` share/drift per channel, with
    optional per-node `overrides` that replace (not merge into) specific
    channels' shares for that node -- the override + untouched-default
    shares are then renormalized together each period by
    _resolve_compositional_mix so the result always sums to 1 (the raw
    override entries in the example config, e.g. fttp_1000's 0.06 + 0.38 +
    0.31 = 0.75, don't sum to 1 on their own -- renormalization is what
    makes that a valid distribution)."""
    defaults = raw["defaults"]
    overrides = raw.get("overrides", {})
    result: dict[NodeId, dict[Period, dict[ChannelId, Decimal]]] = {}
    for node in nodes:
        node_override = overrides.get(node, {})
        base_shares: dict[str, tuple[Decimal, Decimal]] = {}
        for channel in channels:
            cfg = node_override.get(channel, defaults.get(channel, {}))
            share = Decimal(str(cfg.get("share", "0")))
            drift = Decimal(str(cfg.get("drift_pct_per_month", "0")))
            base_shares[channel] = (share, drift)
        resolved = _resolve_compositional_mix(base_shares, periods=periods)
        result[node] = {
            t: {ChannelId(k): v for k, v in shares.items()} for t, shares in resolved.items()
        }
    return result


def _resolve_flat_compositional_mix(
    raw: Mapping[str, Any], *, keys: Sequence[ChannelId], periods: Sequence[Period]
) -> dict[Period, dict[ChannelId, Decimal]]:
    """regrade.order_channel_mix: same share/drift shape as
    channel_mix.defaults, but flat (no per-node dimension)."""
    base_shares = {
        key: (
            Decimal(str(cfg.get("share", "0"))),
            Decimal(str(cfg.get("drift_pct_per_month", "0"))),
        )
        for key, cfg in raw.items()
    }
    resolved = _resolve_compositional_mix(base_shares, periods=periods)
    return {t: {ChannelId(k): v for k, v in shares.items()} for t, shares in resolved.items()}


def _resolve_contract_term_mix(
    raw: Mapping[str, Any], channels: Sequence[ChannelId]
) -> dict[ChannelId, dict[int, Decimal]]:
    """acquisition.contract_term_mix: a channel's `by_channel` entry, if
    present, fully replaces `defaults` for that channel (not merged)."""
    defaults = {int(k): Decimal(str(v)) for k, v in raw["defaults"].items()}
    by_channel = raw.get("by_channel", {})
    result: dict[ChannelId, dict[int, Decimal]] = {}
    for channel in channels:
        if channel in by_channel:
            result[channel] = {int(k): Decimal(str(v)) for k, v in by_channel[channel].items()}
        else:
            result[channel] = dict(defaults)
    return result


# ---------------------------------------------------------------------------
# Acquisition process
# ---------------------------------------------------------------------------


def _resolve_acquisition(
    raw: Mapping[str, Any],
    *,
    nodes: Sequence[NodeId],
    periods: Sequence[Period],
    calendar: MonthlyCalendar,
    regressors: Mapping[str, Mapping[str, Mapping[Period, Decimal]]],
    channel_mix: Mapping[NodeId, Mapping[Period, Mapping[ChannelId, Decimal]]],
    campaign_multiplier: Mapping[ChannelId, Mapping[Period, Decimal]],
) -> dict[NodeId, AcquisitionProcess]:
    """acquisition.defaults + acquisition.nodes[node] resolve a
    negative-binomial process per node:

        mean(t) = level
            * (1 + trend_pct_per_month/100)^t
            * seasonality[month(t)]
            * (working_days(t) / working_days(0)) ^ working_day_elasticity
            * relative_price_index(node, t) ^ price_elasticity
            * (channel-mix-weighted marketing_spend(node, t)
               / its own period-0 value) ^ marketing_elasticity
            * (channel-mix-weighted sales_headcount(node, t)
               / its own period-0 value) ^ headcount_elasticity
            * channel-mix-weighted campaign multiplier(node, t)

    marketing_spend and sales_headcount are channel-level regressors, but
    their elasticities are specified at the node-level acquisition
    process; there is no single unambiguous way to apply a channel-level
    driver to a node-level process, so each node's *current* channel_mix
    is used as weights to get a representative per-node value each
    period, normalized to its own period-0 value (unlike
    relative_price_index, these regressors aren't already expressed as a
    ratio to a baseline). This is a modeling simplification, not a literal
    spec requirement -- documented here because it's a genuine judgment
    call, not an unambiguous reading of the source config.
    """
    defaults = raw["defaults"]
    seasonality = {k: Decimal(str(v)) for k, v in defaults.get("seasonality", {}).items()}
    working_day_elasticity = float(defaults.get("working_day_elasticity", 0))
    price_elasticity = float(defaults.get("price_elasticity", 0))
    marketing_elasticity = float(defaults.get("marketing_elasticity", 0))
    headcount_elasticity = float(defaults.get("headcount_elasticity", 0))

    working_days = regressors.get("working_days", {}).get("*", {})
    working_days_0 = float(working_days.get(Period(0), _ONE)) or 1.0
    price_index = regressors.get("relative_price_index", {})
    marketing = regressors.get("marketing_spend", {})
    headcount = regressors.get("sales_headcount", {})

    def channel_weighted(
        series: Mapping[str, Mapping[Period, Decimal]], node: NodeId, t: Period
    ) -> float:
        mix = channel_mix.get(node, {}).get(t, {})
        return float(
            sum((mix.get(c, _ZERO) * series.get(c, {}).get(t, _ZERO) for c in mix), start=_ZERO)
        )

    result: dict[NodeId, AcquisitionProcess] = {}
    for node in nodes:
        node_cfg = raw.get("nodes", {}).get(node, {})
        level = float(node_cfg["level"])
        trend = float(node_cfg.get("trend_pct_per_month", defaults.get("trend_pct_per_month", 0)))
        dispersion = Decimal(str(node_cfg.get("dispersion", defaults.get("dispersion", "0"))))

        marketing_0 = channel_weighted(marketing, node, Period(0)) or 1.0
        headcount_0 = channel_weighted(headcount, node, Period(0)) or 1.0

        mean_by_period: dict[Period, Decimal] = {}
        for t in periods:
            trend_factor = (1.0 + trend / 100.0) ** int(t)
            season_factor = float(
                seasonality.get(month_abbreviation(calendar.month_of_year(t)), _ONE)
            )
            wd_factor = (
                float(working_days.get(t, Decimal(str(working_days_0)))) / working_days_0
            ) ** working_day_elasticity
            price_factor = float(price_index.get(node, {}).get(t, _ONE)) ** price_elasticity

            marketing_t = channel_weighted(marketing, node, t)
            marketing_factor = (
                (marketing_t / marketing_0) ** marketing_elasticity if marketing_t > 0 else 1.0
            )
            headcount_t = channel_weighted(headcount, node, t)
            headcount_factor = (
                (headcount_t / headcount_0) ** headcount_elasticity if headcount_t > 0 else 1.0
            )

            mix = channel_mix.get(node, {}).get(t, {})
            campaign_factor = (
                float(
                    sum(
                        (
                            mix.get(c, _ZERO) * campaign_multiplier.get(c, {}).get(t, _ONE)
                            for c in mix
                        ),
                        start=_ZERO,
                    )
                )
                or 1.0
            )

            mean = (
                level
                * trend_factor
                * season_factor
                * wd_factor
                * price_factor
                * marketing_factor
                * headcount_factor
                * campaign_factor
            )
            mean_by_period[t] = Decimal(str(max(mean, 0.0)))
        result[node] = AcquisitionProcess(mean_by_period=mean_by_period, dispersion=dispersion)
    return result


# ---------------------------------------------------------------------------
# Closure kernel: continuous cycle-time -> discrete per-segment ClosureProfile
# ---------------------------------------------------------------------------


def _resolve_pipeline_segment(
    pipeline_cfg: Mapping[str, Any], *, txn_type: TxnType, channel: ChannelId, product: str
) -> tuple[Decimal, Decimal, Decimal]:
    """Cascading override: pipeline.defaults, then by_order_type[txn_type]
    (partial override -- only cycle_time and/or breakage_rate keys present
    replace the corresponding default), then by_channel[channel] (same,
    applied on top), then by_product[product]'s cycle_time_multiplier /
    breakage_multiplier scale whatever resulted from the first three
    stages. Returns (median_days, p90_days, breakage_rate)."""
    defaults = pipeline_cfg["defaults"]
    median_days = Decimal(str(defaults["cycle_time"]["median_days"]))
    p90_days = Decimal(str(defaults["cycle_time"]["p90_days"]))
    breakage_rate = Decimal(str(defaults["breakage_rate"]))

    order_type_cfg = pipeline_cfg.get("by_order_type", {}).get(txn_type.value, {})
    if "cycle_time" in order_type_cfg:
        median_days = Decimal(str(order_type_cfg["cycle_time"]["median_days"]))
        p90_days = Decimal(str(order_type_cfg["cycle_time"]["p90_days"]))
    if "breakage_rate" in order_type_cfg:
        breakage_rate = Decimal(str(order_type_cfg["breakage_rate"]))

    channel_cfg = pipeline_cfg.get("by_channel", {}).get(channel, {})
    if "cycle_time" in channel_cfg:
        median_days = Decimal(str(channel_cfg["cycle_time"]["median_days"]))
        p90_days = Decimal(str(channel_cfg["cycle_time"]["p90_days"]))
    if "breakage_rate" in channel_cfg:
        breakage_rate = Decimal(str(channel_cfg["breakage_rate"]))

    product_cfg = pipeline_cfg.get("by_product", {}).get(product, {})
    cycle_multiplier = Decimal(str(product_cfg.get("cycle_time_multiplier", "1")))
    breakage_multiplier = Decimal(str(product_cfg.get("breakage_multiplier", "1")))
    median_days *= cycle_multiplier
    p90_days *= cycle_multiplier
    breakage_rate = min(breakage_rate * breakage_multiplier, _ONE)

    return median_days, p90_days, breakage_rate


def _derive_closure_profile(
    rng: random.Random,
    *,
    calendar: MonthlyCalendar,
    median_days: Decimal,
    p90_days: Decimal,
    breakage_rate: Decimal,
    max_age_periods: int,
    n_samples: int = 20_000,
) -> ClosureProfile:
    """Monte Carlo discretizes a lognormal(median_days, p90_days) cycle-
    time distribution into a per-segment ClosureProfile.

    mu/sigma come from the lognormal's closed-form median/p90 relation
    (median = exp(mu), p90 = exp(mu + sigma*z_0.9)). Each of n_samples
    draws picks a uniform raise-day within a representative month (period
    0 -- a fixed reference so the derived kernel is a single static
    profile, not itself period-varying) and a cycle-time-in-days draw,
    discretized to an integer age via MonthlyCalendar.day_offset_to_period
    -- this is what naturally produces realistic clustering at age 0 for
    short cycle times, and realistic spread from month-length variation,
    without needing a fixed "days per period" approximation.

    breakage_rate draws are separate (never cycle-time sampled at all);
    any close that would land at or beyond max_age_periods is folded into
    breakage too, matching the spec's "orders older than K_max are
    force-broken" (zombie orders never accumulate unboundedly in the
    order book). breakage is finally taken as the residual `1 - sum(g)`
    so ClosureProfile's exact-sum-to-one contract holds despite Monte
    Carlo noise, per the same pattern engine/estimate.py's estimators use.
    """
    mu = math.log(float(median_days))
    sigma = (math.log(float(p90_days)) - mu) / _Z90

    raise_period = Period(0)
    days_in_month = calendar.days_in_month(raise_period)
    breakage_samples = round(n_samples * float(breakage_rate))
    survival_samples = n_samples - breakage_samples

    age_counts = [0] * max_age_periods
    forced_break_count = 0
    for _ in range(survival_samples):
        day_offset = rng.randrange(days_in_month)
        cycle_days = rng.lognormvariate(mu, sigma)
        close_period = calendar.day_offset_to_period(raise_period, day_offset, cycle_days)
        age = max(int(close_period) - int(raise_period), 0)
        if age < max_age_periods:
            age_counts[age] += 1
        else:
            forced_break_count += 1

    total = n_samples
    g = tuple(Decimal(count) / Decimal(total) for count in age_counts)
    breakage = _ONE - sum(g, start=_ZERO)
    return ClosureProfile(g=g, breakage=breakage)


def _resolve_closure(
    pipeline_cfg: Mapping[str, Any],
    *,
    product_hierarchy: ProductHierarchy,
    channels: Sequence[ChannelId],
    max_age_periods: int,
    calendar: MonthlyCalendar,
    seed: int,
) -> dict[tuple[ChannelId, TxnType, str], ClosureProfile]:
    rng = random.Random(f"closure-kernel-derivation:{seed}")
    products = sorted(set(product_hierarchy.product_of.values()))
    result: dict[tuple[ChannelId, TxnType, str], ClosureProfile] = {}
    for channel in channels:
        for txn_type in (TxnType.ACQUISITION, TxnType.REGRADE, TxnType.CHURN):
            for product in products:
                median_days, p90_days, breakage_rate = _resolve_pipeline_segment(
                    pipeline_cfg, txn_type=txn_type, channel=channel, product=product
                )
                result[(channel, txn_type, product)] = _derive_closure_profile(
                    rng,
                    calendar=calendar,
                    median_days=median_days,
                    p90_days=p90_days,
                    breakage_rate=breakage_rate,
                    max_age_periods=max_age_periods,
                )
    return result


# ---------------------------------------------------------------------------
# Regrade/churn hazard: contract-expiry-window structure
# ---------------------------------------------------------------------------


def _build_hazard_fn(
    raw: Mapping[str, Any], *, calendar: MonthlyCalendar, node_specific: bool
) -> HazardFn:
    """base_monthly_rate/in_contract_monthly, expiry_window_months (an
    explicit SET of integer offsets relative to contract end -- not
    necessarily contiguous, see the example config's regrade window
    [-2, 0, 1, 2] which skips -1), expiry_window_rate, out_of_contract_
    rate, seasonality, by_acquisition_channel, and (churn only) by_node
    and early_life_multiplier.

    Tiering: `relative = months_since_contract_start - contract_term`.
    - relative in expiry_window_months -> expiry_window_rate
    - relative < 0 (and not in the window) -> in-contract rate
    - relative >= 0 (and not in the window) -> out-of-contract rate
    (Using the sign of `relative` for the fallback, not a min/max window
    bound, is what correctly handles a non-contiguous window like
    [-2, 0, 1, 2]: relative=-1 falls through to in-contract, not
    out-of-contract, even though -1 isn't in the window and isn't less
    than the window's minimum either.)

    early_life_multiplier keys off `tenure` (periods since acquisition --
    a settling-in effect tied to the subscriber's original sign-up, not
    reset by later contract extensions); the expiry-window tiers key off
    `months_since_contract_start` (which *does* reset on an extension).
    """
    in_contract_rate = Decimal(
        str(raw.get("base_monthly_rate", raw.get("in_contract_monthly", "0")))
    )
    expiry_window_months = frozenset(int(m) for m in raw.get("expiry_window_months", []))
    expiry_window_rate = Decimal(
        str(raw.get("expiry_window_rate", raw.get("expiry_window_monthly", "0")))
    )
    out_of_contract_rate = Decimal(
        str(raw.get("out_of_contract_rate", raw.get("out_of_contract_monthly", "0")))
    )
    seasonality = {k: Decimal(str(v)) for k, v in raw.get("seasonality", {}).items()}
    by_acquisition_channel = {
        ChannelId(k): Decimal(str(v)) for k, v in raw.get("by_acquisition_channel", {}).items()
    }
    by_node = (
        {NodeId(k): Decimal(str(v)) for k, v in raw.get("by_node", {}).items()}
        if node_specific
        else {}
    )
    early_life_multiplier = (
        {int(k): Decimal(str(v)) for k, v in raw.get("early_life_multiplier", {}).items()}
        if node_specific
        else {}
    )

    def hazard(ctx: HazardContext) -> Decimal:
        relative = ctx.months_since_contract_start - ctx.contract_term
        if relative in expiry_window_months:
            rate = expiry_window_rate
        elif relative < 0:
            rate = in_contract_rate
        else:
            rate = out_of_contract_rate

        rate *= seasonality.get(month_abbreviation(calendar.month_of_year(ctx.period)), _ONE)
        rate *= by_acquisition_channel.get(ctx.acquisition_channel, _ONE)
        rate *= by_node.get(ctx.node, _ONE)
        rate *= early_life_multiplier.get(ctx.tenure, _ONE)
        return min(rate, _ONE)

    return hazard


def _resolve_regrade_transition(
    raw: Mapping[str, Mapping[str, Any]], nodes: Sequence[NodeId]
) -> dict[NodeId, dict[NodeId, Decimal]]:
    """A destination of "_extend_same" maps to EXTEND_SAME (see its
    docstring in synthetic.py); every other key is a real node id."""
    result: dict[NodeId, dict[NodeId, Decimal]] = {}
    for source, dist in raw.items():
        result[NodeId(source)] = {
            (EXTEND_SAME if dest == "_extend_same" else NodeId(dest)): Decimal(str(weight))
            for dest, weight in dist.items()
        }
    for node in nodes:
        result.setdefault(NodeId(node), {})
    return result


# ---------------------------------------------------------------------------
# Migrations, pathology rates, self_check, structural break
# ---------------------------------------------------------------------------


def _resolve_migrations(
    raw: Sequence[Mapping[str, Any]], *, calendar: MonthlyCalendar
) -> tuple[MigrationSchedule, ...]:
    schedules = []
    for entry in raw:
        schedule = {
            calendar.period_of(period): Decimal(str(count))
            for period, count in entry["schedule"].items()
        }
        schedules.append(
            MigrationSchedule(
                name=entry["name"],
                from_node=NodeId(entry["from_node"]),
                to_node=NodeId(entry["to_node"]) if entry.get("to_node") is not None else None,
                schedule=schedule,
            )
        )
    return tuple(schedules)


def _resolve_pathology_rates(raw: Mapping[str, Any]) -> PathologyRates:
    orphan = raw.get("orphan_orders", {})
    backdated = raw.get("backdated_events", {})
    status_flapping = raw.get("status_flapping", {})
    bulk_closure = raw.get("bulk_closure_batch", {})
    zombie = raw.get("zombie_orders", {})
    duplicate = raw.get("duplicate_orders", {})
    return PathologyRates(
        orphan_closed_with_no_base_movement_rate=Decimal(
            str(orphan.get("closed_with_no_base_movement_rate", "0"))
        ),
        orphan_base_movement_with_no_order_rate=Decimal(
            str(orphan.get("base_movement_with_no_order_rate", "0"))
        ),
        backdated_rate=Decimal(str(backdated.get("rate", "0"))),
        backdated_max_periods_back=int(backdated.get("max_periods_back", 0)),
        status_flapping_rate=Decimal(str(status_flapping.get("rate", "0"))),
        bulk_closure_batch_enabled=_resolve_bulk_closure_batch_enabled(bulk_closure),
        zombie_orders_rate=Decimal(str(zombie.get("rate", "0"))),
        duplicate_orders_rate=Decimal(str(duplicate.get("rate", "0"))),
    )


def _resolve_bulk_closure_batch_enabled(raw: Mapping[str, Any]) -> bool:
    """pathologies.bulk_closure_batch always targets the oldest still-open
    acquisition cohort, closed at the last observed period (see
    apply_pathology_rates in pathologies.py) -- period/volume/
    drawn_from_ages overrides of that aren't implemented. Rather than
    silently ignoring them while still running the batch with the
    hardcoded target, raise if any are set."""
    enabled = bool(raw.get("enabled", False))
    unsupported = sorted(set(raw) - {"enabled"})
    if enabled and unsupported:
        raise SchemaNotImplementedError(
            f"pathologies.bulk_closure_batch.{unsupported} not implemented -- "
            "bulk_closure_batch always closes the oldest still-open acquisition cohort "
            "at the last observed period; remove these keys to run with that behavior"
        )
    return enabled


def _resolve_self_check(raw: Mapping[str, Any]) -> SelfCheckConfig:
    defaults = SelfCheckConfig()
    return SelfCheckConfig(
        assert_order_book_identity=bool(
            raw.get("assert_order_book_identity", defaults.assert_order_book_identity)
        ),
        assert_base_identity=bool(raw.get("assert_base_identity", defaults.assert_base_identity)),
        assert_resign_conservation=bool(
            raw.get("assert_resign_conservation", defaults.assert_resign_conservation)
        ),
        assert_kernel_sums_to_one=bool(
            raw.get("assert_kernel_sums_to_one", defaults.assert_kernel_sums_to_one)
        ),
        assert_non_negative_base=bool(
            raw.get("assert_non_negative_base", defaults.assert_non_negative_base)
        ),
        assert_migrations_net_zero_when_paired=bool(
            raw.get(
                "assert_migrations_net_zero_when_paired",
                defaults.assert_migrations_net_zero_when_paired,
            )
        ),
    )


def _resolve_structural_break(raw: Mapping[str, Any]) -> StructuralBreak | None:
    if not raw.get("enabled", False):
        return None
    if "target" in raw or "changes" in raw:
        raise SchemaNotImplementedError(
            "pathologies.structural_break with a 'target'/'changes' shape (per-channel or "
            "per-node breakage/acquisition-level overrides) is not implemented yet -- only "
            "the flat break_period/acquisition_multiplier/churn_multiplier shape is"
        )
    return StructuralBreak(
        break_period=int(raw["break_period"]),
        acquisition_multiplier=Decimal(str(raw.get("acquisition_multiplier", "1"))),
        churn_multiplier=Decimal(str(raw.get("churn_multiplier", "1"))),
    )
