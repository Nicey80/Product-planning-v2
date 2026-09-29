"""Regressor time series -- explanatory variables (working days, price
index, marketing spend, headcount, campaign dummies) that the acquisition
process and hazard functions read from, resolved to a `dict[Period,
Decimal]` per key before generation starts.

Only the handful of concrete shapes engine/synthetic/base_portfolio.yaml
actually uses are implemented (a general "Regressor" class hierarchy
covering every conceivable series shape would be speculative generality
with a single call site). `resolve_series` is the one workhorse function;
`resolve_working_days` and `resolve_campaign_multiplier` cover the two
shapes that don't fit its step-function-plus-seasonality model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from engine.domain import Period
from engine.testing.calendar import MonthlyCalendar, month_abbreviation

_ZERO = Decimal(0)
_ONE = Decimal(1)


def resolve_working_days(
    calendar: MonthlyCalendar, periods: Sequence[Period]
) -> dict[Period, Decimal]:
    return {p: Decimal(calendar.working_days(p)) for p in periods}


def resolve_series(
    *,
    keys: Sequence[str],
    baseline: Decimal | Mapping[str, Decimal],
    periods: Sequence[Period],
    calendar: MonthlyCalendar,
    observed_cutoff: Period,
    key_field: str,
    value_field: str,
    seasonality: Mapping[str, Decimal] | None = None,
    events: Sequence[Mapping[str, Any]] = (),
    forward_assumption: str = "hold_last",
) -> dict[str, dict[Period, Decimal]]:
    """A per-key step function: starts at `baseline[key]` (or the scalar
    `baseline` shared by every key), steps to a new value at each dated
    event in `events` (an event with `key_field == "*"` applies to every
    key not overridden by a key-specific event at that same period),
    optionally multiplied by a repeating monthly `seasonality` factor
    (default 1.0). Periods after `observed_cutoff` ignore any dated
    events and instead follow `forward_assumption`:

    - "hold_last": repeat the value resolved at `observed_cutoff`.
    - "flat_at_last_12m_mean": repeat the mean of the 12 periods up to
      and including `observed_cutoff`.

    (There is no meaningful "forward" state for a series with no events
    and no seasonality trend beyond its baseline -- flat_at_last_12m_mean
    reduces to the same number `hold_last` would in that case, which is
    exactly marketing_spend's situation in the example config.)
    """
    if forward_assumption not in ("hold_last", "flat_at_last_12m_mean"):
        raise ValueError(f"unknown forward_assumption {forward_assumption!r}")

    events_by_key: dict[str, list[tuple[Period, Decimal]]] = {k: [] for k in keys}
    for event in events:
        event_key = str(event[key_field])
        period = calendar.period_of(event["period"])
        value = Decimal(str(event[value_field]))
        targets = keys if event_key == "*" else [event_key]
        for target in targets:
            if target in events_by_key:
                events_by_key[target].append((period, value))

    result: dict[str, dict[Period, Decimal]] = {}
    for key in keys:
        base = baseline.get(key, _ZERO) if isinstance(baseline, Mapping) else baseline
        # a key-specific event at a given period always wins over a
        # same-period wildcard one -- dedupe by period, preferring the
        # last entry appended (wildcards were appended before specifics
        # since `events` is walked in file order and specifics typically
        # follow wildcards in these configs, but to be robust we dedupe
        # explicitly rather than relying on that ordering)
        by_period: dict[Period, Decimal] = {}
        for period, value in sorted(events_by_key[key], key=lambda pv: int(pv[0])):
            by_period[period] = value
        step_periods = sorted(by_period)

        by_period_resolved: dict[Period, Decimal] = {}
        for t in periods:
            stepped = base
            for sp in step_periods:
                if int(sp) <= int(t):
                    stepped = by_period[sp]
                else:
                    break
            factor = _ONE
            if seasonality is not None:
                factor = seasonality.get(month_abbreviation(calendar.month_of_year(t)), _ONE)
            by_period_resolved[t] = stepped * factor

        cutoff_int = int(observed_cutoff)
        if forward_assumption == "hold_last":
            forward_value = by_period_resolved.get(observed_cutoff, base)
        else:
            trailing = [
                by_period_resolved[t] for t in periods if cutoff_int - 11 <= int(t) <= cutoff_int
            ]
            forward_value = (
                sum(trailing, start=_ZERO) / len(trailing)
                if trailing
                else by_period_resolved.get(observed_cutoff, base)
            )
        # An explicitly dated event beyond the cutoff (e.g. a "forward"
        # price change) still takes effect at and after its own date --
        # forward_assumption only fills the gap *before* the next such
        # event, it never overrides one that's already been specified.
        future_event_periods = sorted(sp for sp in step_periods if int(sp) > cutoff_int)
        for t in periods:
            if int(t) <= cutoff_int:
                continue
            if any(int(sp) <= int(t) for sp in future_event_periods):
                continue  # an explicit future event already applies here
            by_period_resolved[t] = forward_value

        result[key] = by_period_resolved
    return result


def resolve_campaign_multiplier(
    events: Sequence[Mapping[str, Any]],
    *,
    keys: Sequence[str],
    periods: Sequence[Period],
    calendar: MonthlyCalendar,
) -> dict[str, dict[Period, Decimal]]:
    """1.0 everywhere except (key, period) pairs named in an event's
    `channels` x `periods` cross product, which get that event's
    `raise_multiplier`."""
    result: dict[str, dict[Period, Decimal]] = {k: dict.fromkeys(periods, _ONE) for k in keys}
    for event in events:
        multiplier = Decimal(str(event["raise_multiplier"]))
        event_periods = {calendar.period_of(p) for p in event["periods"]}
        for key in event["channels"]:
            if key not in result:
                continue
            for period in event_periods:
                if period in result[key]:
                    result[key][period] = multiplier
    return result
