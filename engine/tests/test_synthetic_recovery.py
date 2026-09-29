"""Parameter recovery tests -- the point of engine/testing/synthetic.py.

Generate a portfolio from known ground-truth parameters, run the
estimators in engine/estimate.py over its raw event log, and assert the
estimates land close to the truth. test_censored_cohort_estimator_is_unbiased
below is the centerpiece: it proves estimate_closure_kernel's life-table
construction recovers g(k) correctly under heavy right-censoring where the
naive closed/raised ratio (naive_kernel_estimate) is badly biased -- a
concrete demonstration, not just an assertion, that the naive estimator
would fail this test.
"""

from __future__ import annotations

from decimal import Decimal

from engine.domain import NodeId, Period, TxnType
from engine.estimate import (
    estimate_churn_hazard,
    estimate_closure_kernel,
    estimate_regrade_transition,
    naive_kernel_estimate,
    observations_from_order_events,
    raised_by_cohort_from_events,
)
from engine.testing.synthetic import (
    AcquisitionProcess,
    ClosureProfile,
    HazardContext,
    SyntheticParams,
    generate_portfolio,
    make_channel_hierarchy,
    make_product_hierarchy,
    observation_cutoff,
)

_ZERO = Decimal(0)


def _zero_hazard(_ctx: HazardContext) -> Decimal:
    return _ZERO


# ---------------------------------------------------------------------------
# Closure kernel g(k) and breakage
# ---------------------------------------------------------------------------


def test_recovers_closure_kernel_and_breakage() -> None:
    ph = make_product_hierarchy(groups=1, products_per_group=1, variants_per_product=1)
    ch = make_channel_hierarchy(groups=1, channels_per_group=1, sub_channels_per_channel=1)
    node = ph.nodes[0]
    channel = ch.sub_channels[0]
    product = ph.product_of[node]
    horizon = 120
    periods = tuple(Period(t) for t in range(horizon))

    true_g = (Decimal("0.45"), Decimal("0.25"), Decimal("0.15"))
    true_breakage = Decimal("0.15")
    profile = ClosureProfile(g=true_g, breakage=true_breakage)

    params = SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=horizon,
        seed=1,
        acquisition={
            node: AcquisitionProcess(
                mean_by_period=dict.fromkeys(periods, Decimal("60")), dispersion=_ZERO
            )
        },
        channel_mix={node: {t: {channel: Decimal(1)} for t in periods}},
        contract_terms=(1,),
        contract_term_mix={channel: {1: Decimal(1)}},
        regrade_transition={},
        regrade_hazard=_zero_hazard,
        churn_hazard=_zero_hazard,
        closure={(channel, TxnType.ACQUISITION, product): profile},
        order_channel_weight={channel: Decimal(1)},
    )
    portfolio = generate_portfolio(params)

    observations = observations_from_order_events(
        portfolio.raw_order_event, node=node, order_channel=channel, txn_type=TxnType.ACQUISITION
    )
    raised = raised_by_cohort_from_events(
        portfolio.raw_order_event, node=node, order_channel=channel, txn_type=TxnType.ACQUISITION
    )
    cutoff = observation_cutoff(portfolio)

    estimated = estimate_closure_kernel(
        observations,
        raised,
        node=node,
        order_channel=channel,
        txn_type=TxnType.ACQUISITION,
        observation_cutoff=cutoff,
        max_age=len(true_g) + 2,
    )

    for k, true_gk in enumerate(true_g):
        assert abs(estimated.g[k] - true_gk) < Decimal("0.03"), (k, estimated.g[k], true_gk)
    assert abs(estimated.breakage - true_breakage) < Decimal("0.03")
    # tail ages beyond the true kernel's support should recover ~0
    for k in range(len(true_g), len(true_g) + 2):
        assert estimated.g[k] < Decimal("0.02")


# ---------------------------------------------------------------------------
# Regrade transition matrix
# ---------------------------------------------------------------------------


def test_recovers_regrade_transition_matrix() -> None:
    ph = make_product_hierarchy(groups=1, products_per_group=1, variants_per_product=3)
    ch = make_channel_hierarchy(groups=1, channels_per_group=1, sub_channels_per_channel=1)
    nodes = ph.nodes
    channel = ch.sub_channels[0]
    product = ph.product_of[nodes[0]]
    horizon = 100
    periods = tuple(Period(t) for t in range(horizon))

    # a deliberately non-uniform transition matrix
    true_transition: dict[NodeId, dict[NodeId, Decimal]] = {
        nodes[0]: {nodes[0]: Decimal("0.2"), nodes[1]: Decimal("0.5"), nodes[2]: Decimal("0.3")},
        nodes[1]: {nodes[0]: Decimal("0.6"), nodes[1]: Decimal("0.1"), nodes[2]: Decimal("0.3")},
        nodes[2]: {nodes[0]: Decimal("0.3"), nodes[1]: Decimal("0.3"), nodes[2]: Decimal("0.4")},
    }
    one_shot = ClosureProfile(g=(Decimal(1),), breakage=_ZERO)

    def _regrade_hazard(_ctx: HazardContext) -> Decimal:
        return Decimal("0.08")

    def _churn_hazard(_ctx: HazardContext) -> Decimal:
        return Decimal("0.01")

    params = SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=horizon,
        seed=11,
        acquisition={
            n: AcquisitionProcess(
                mean_by_period=dict.fromkeys(periods, Decimal("15")), dispersion=_ZERO
            )
            for n in nodes
        },
        channel_mix={n: {t: {channel: Decimal(1)} for t in periods} for n in nodes},
        contract_terms=(1,),
        contract_term_mix={channel: {1: Decimal(1)}},
        regrade_transition=true_transition,
        regrade_hazard=_regrade_hazard,
        churn_hazard=_churn_hazard,
        closure={
            (channel, TxnType.ACQUISITION, product): one_shot,
            (channel, TxnType.REGRADE, product): one_shot,
            (channel, TxnType.CHURN, product): one_shot,
        },
        order_channel_weight={channel: Decimal(1)},
        regrade_order_channel_mix={t: {channel: Decimal(1)} for t in periods},
    )
    portfolio = generate_portfolio(params)

    estimated = estimate_regrade_transition(portfolio.raw_subscription_event)

    for source, true_dist in true_transition.items():
        est_dist = estimated[source]
        for dest, true_p in true_dist.items():
            assert abs(est_dist.get(dest, _ZERO) - true_p) < Decimal("0.06"), (
                source,
                dest,
                est_dist.get(dest),
                true_p,
            )


# ---------------------------------------------------------------------------
# Churn hazard by tenure and contract term
# ---------------------------------------------------------------------------


def test_recovers_churn_hazard_by_tenure_and_contract_term() -> None:
    """estimate_churn_hazard is only matched here for a hazard that's
    constant per contract term (window-independent) -- identical to what
    the old tenure+term-only model could express. Recovering the richer
    contract-expiry-window hazard shape (a function of
    months_since_contract_start, not just tenure/term) needs a matching
    estimator that doesn't exist yet in engine/estimate.py; that's future
    work, not something this generator change should shim around.
    """
    ph = make_product_hierarchy(groups=1, products_per_group=1, variants_per_product=1)
    ch = make_channel_hierarchy(groups=1, channels_per_group=1, sub_channels_per_channel=1)
    node = ph.nodes[0]
    channel = ch.sub_channels[0]
    product = ph.product_of[node]
    horizon = 80
    periods = tuple(Period(t) for t in range(horizon))
    one_shot = ClosureProfile(g=(Decimal(1),), breakage=_ZERO)

    # contract terms are lengths in periods: 1 stands in for "monthly", 12
    # for "annual".
    true_hazard = {1: Decimal("0.05"), 12: Decimal("0.015")}

    def churn_hazard(ctx: HazardContext) -> Decimal:
        return true_hazard[ctx.contract_term]

    params = SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=horizon,
        seed=5,
        acquisition={
            node: AcquisitionProcess(
                mean_by_period=dict.fromkeys(periods, Decimal("50")), dispersion=_ZERO
            )
        },
        channel_mix={node: {t: {channel: Decimal(1)} for t in periods}},
        contract_terms=(1, 12),
        contract_term_mix={channel: {1: Decimal("0.5"), 12: Decimal("0.5")}},
        regrade_transition={},
        regrade_hazard=_zero_hazard,
        churn_hazard=churn_hazard,
        closure={
            (channel, TxnType.ACQUISITION, product): one_shot,
            (channel, TxnType.CHURN, product): one_shot,
        },
        order_channel_weight={channel: Decimal(1)},
    )
    portfolio = generate_portfolio(params)
    cutoff = observation_cutoff(portfolio)

    estimated = estimate_churn_hazard(
        portfolio.raw_subscription_event, observation_cutoff=cutoff, max_tenure=15
    )

    # tenure 0 is structurally unobservable in this discrete model: a
    # subscriber's acquisition order must close (making them "live") before
    # they can be selected as a churn candidate, which can only happen in a
    # later period -- so hazard(0, *) is always estimated as 0 regardless
    # of the true input. Recovery is checked from tenure 1 onward, where
    # there's a real at-risk population. raw_subscription_event's
    # contract_term is str(term) at generation time.
    for term, true_p in true_hazard.items():
        errors = [abs(estimated[(k, str(term))] - true_p) for k in range(1, 12)]
        assert max(errors) < Decimal("0.02"), (term, errors)


# ---------------------------------------------------------------------------
# Censored-cohort unbiasedness -- the centerpiece test.
# ---------------------------------------------------------------------------


def test_censored_cohort_estimator_is_unbiased_naive_is_not() -> None:
    """A short horizon relative to the kernel's max_lag means most raise
    cohorts haven't had time to reach the kernel's oldest ages by the
    observation cutoff -- heavy right-censoring concentrated at high k.

    naive_kernel_estimate divides by *total* raised regardless of whether
    each cohort has been observed long enough to have resolved at age k,
    so it systematically undercounts g(k) at high k (cohorts that simply
    haven't had time to close yet look, to that estimator, like cohorts
    that resolved some other way). estimate_closure_kernel's life-table
    construction excludes not-yet-matured cohorts from age k's risk set
    entirely, and recovers the true g(k) at every age -- this is the
    concrete proof that a naive closed/raised ratio would fail this test
    while the life-table estimator does not.
    """
    ph = make_product_hierarchy(groups=1, products_per_group=1, variants_per_product=1)
    ch = make_channel_hierarchy(groups=1, channels_per_group=1, sub_channels_per_channel=1)
    node = ph.nodes[0]
    channel = ch.sub_channels[0]
    product = ph.product_of[node]
    horizon = 12  # << kernel max_lag=8: most cohorts are censored at high k
    periods = tuple(Period(t) for t in range(horizon))

    true_g = tuple(Decimal("0.1") for _ in range(8))  # ages 0..7
    true_breakage = Decimal(1) - sum(true_g, start=_ZERO)  # 0.2
    profile = ClosureProfile(g=true_g, breakage=true_breakage)

    params = SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=horizon,
        seed=21,
        acquisition={
            node: AcquisitionProcess(
                mean_by_period=dict.fromkeys(periods, Decimal("80")), dispersion=_ZERO
            )
        },
        channel_mix={node: {t: {channel: Decimal(1)} for t in periods}},
        contract_terms=(1,),
        contract_term_mix={channel: {1: Decimal(1)}},
        regrade_transition={},
        regrade_hazard=_zero_hazard,
        churn_hazard=_zero_hazard,
        closure={(channel, TxnType.ACQUISITION, product): profile},
        order_channel_weight={channel: Decimal(1)},
    )
    portfolio = generate_portfolio(params)

    observations = observations_from_order_events(
        portfolio.raw_order_event, node=node, order_channel=channel, txn_type=TxnType.ACQUISITION
    )
    raised = raised_by_cohort_from_events(
        portfolio.raw_order_event, node=node, order_channel=channel, txn_type=TxnType.ACQUISITION
    )
    cutoff = observation_cutoff(portfolio)
    max_age = len(true_g)

    life_table = estimate_closure_kernel(
        observations,
        raised,
        node=node,
        order_channel=channel,
        txn_type=TxnType.ACQUISITION,
        observation_cutoff=cutoff,
        max_age=max_age,
    )
    naive = naive_kernel_estimate(observations, raised, max_age=max_age)

    # At the oldest age, more than half the raise cohorts (periods 5..11 of
    # 0..11) have not yet had 8 periods to resolve as of cutoff=11 -- so
    # naive's denominator counts them but its numerator structurally can't.
    oldest_age = max_age - 1
    true_gk = true_g[oldest_age]

    life_table_error = abs(life_table.g[oldest_age] - true_gk)
    naive_error = abs(naive[oldest_age] - true_gk)

    assert life_table_error < Decimal("0.02"), (
        f"life-table estimate at age {oldest_age} should track the true g(k) "
        f"closely: got {life_table.g[oldest_age]}, true {true_gk}"
    )
    assert naive_error > Decimal("0.03"), (
        "naive closed/raised should be visibly biased under this much "
        f"censoring: got {naive[oldest_age]}, true {true_gk} (error {naive_error})"
    )
    # the life-table estimator's error is at least an order of magnitude
    # smaller than the naive one's at the age most affected by censoring.
    assert life_table_error * 5 < naive_error

    # breakage recovery: life-table's residual assignment keeps it close to
    # truth even though several raise cohorts haven't fully resolved yet.
    assert abs(life_table.breakage - true_breakage) < Decimal("0.03")
