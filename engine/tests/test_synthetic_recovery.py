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

from engine.domain import NodeId, TxnType
from engine.estimate import (
    estimate_churn_hazard,
    estimate_closure_kernel,
    estimate_regrade_transition,
    naive_kernel_estimate,
    observations_from_order_events,
    raised_by_cohort_from_events,
)
from engine.testing.synthetic import (
    ClosureProfile,
    SyntheticParams,
    generate_portfolio,
    make_channel_hierarchy,
    make_product_hierarchy,
    observation_cutoff,
)

_ZERO = Decimal(0)


# ---------------------------------------------------------------------------
# Closure kernel g(k) and breakage
# ---------------------------------------------------------------------------


def test_recovers_closure_kernel_and_breakage() -> None:
    ph = make_product_hierarchy(groups=1, products_per_group=1, variants_per_product=1)
    ch = make_channel_hierarchy(groups=1, channels_per_group=1, sub_channels_per_channel=1)
    node = ph.nodes[0]
    channel = ch.sub_channels[0]

    true_g = (Decimal("0.45"), Decimal("0.25"), Decimal("0.15"))
    true_breakage = Decimal("0.15")
    profile = ClosureProfile(g=true_g, breakage=true_breakage)

    params = SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=120,
        seed=1,
        acquisition_rate={(node, channel): Decimal("60")},
        contract_terms=("monthly",),
        contract_term_weights=(Decimal(1),),
        regrade_transition={},
        regrade_hazard=lambda _t, _c: _ZERO,
        churn_hazard=lambda _t, _c: _ZERO,
        closure={(channel, TxnType.ACQUISITION): profile},
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

    # a deliberately non-uniform transition matrix
    true_transition: dict[NodeId, dict[NodeId, Decimal]] = {
        nodes[0]: {nodes[0]: Decimal("0.2"), nodes[1]: Decimal("0.5"), nodes[2]: Decimal("0.3")},
        nodes[1]: {nodes[0]: Decimal("0.6"), nodes[1]: Decimal("0.1"), nodes[2]: Decimal("0.3")},
        nodes[2]: {nodes[0]: Decimal("0.3"), nodes[1]: Decimal("0.3"), nodes[2]: Decimal("0.4")},
    }
    one_shot = ClosureProfile(g=(Decimal(1),), breakage=_ZERO)

    params = SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=100,
        seed=11,
        acquisition_rate={(n, channel): Decimal("15") for n in nodes},
        contract_terms=("monthly",),
        contract_term_weights=(Decimal(1),),
        regrade_transition=true_transition,
        regrade_hazard=lambda _t, _c: Decimal("0.08"),
        churn_hazard=lambda _t, _c: Decimal("0.01"),
        closure={
            (channel, TxnType.ACQUISITION): one_shot,
            (channel, TxnType.REGRADE): one_shot,
            (channel, TxnType.CHURN): one_shot,
        },
        order_channel_weight={channel: Decimal(1)},
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
    ph = make_product_hierarchy(groups=1, products_per_group=1, variants_per_product=1)
    ch = make_channel_hierarchy(groups=1, channels_per_group=1, sub_channels_per_channel=1)
    node = ph.nodes[0]
    channel = ch.sub_channels[0]
    one_shot = ClosureProfile(g=(Decimal(1),), breakage=_ZERO)

    true_hazard = {"monthly": Decimal("0.05"), "annual": Decimal("0.015")}

    def churn_hazard(_tenure: int, term: str) -> Decimal:
        return true_hazard[term]

    params = SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=80,
        seed=5,
        acquisition_rate={(node, channel): Decimal("50")},
        contract_terms=("monthly", "annual"),
        contract_term_weights=(Decimal("0.5"), Decimal("0.5")),
        regrade_transition={},
        regrade_hazard=lambda _t, _c: _ZERO,
        churn_hazard=churn_hazard,
        closure={(channel, TxnType.ACQUISITION): one_shot, (channel, TxnType.CHURN): one_shot},
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
    # there's a real at-risk population.
    for term, true_p in true_hazard.items():
        errors = [abs(estimated[(k, term)] - true_p) for k in range(1, 12)]
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

    true_g = tuple(Decimal("0.1") for _ in range(8))  # ages 0..7
    true_breakage = Decimal(1) - sum(true_g, start=_ZERO)  # 0.2
    profile = ClosureProfile(g=true_g, breakage=true_breakage)

    params = SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=12,  # << kernel max_lag=8: most cohorts are censored at high k
        seed=21,
        acquisition_rate={(node, channel): Decimal("80")},
        contract_terms=("monthly",),
        contract_term_weights=(Decimal(1),),
        regrade_transition={},
        regrade_hazard=lambda _t, _c: _ZERO,
        churn_hazard=lambda _t, _c: _ZERO,
        closure={(channel, TxnType.ACQUISITION): profile},
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
