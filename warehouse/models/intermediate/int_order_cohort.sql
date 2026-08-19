-- The survival triangle: for every (raise_period, age, node, order_channel,
-- txn_type), how much of that raise cohort has resolved to each
-- outcome ('closed', 'broken') by that age, and how much is still 'open'.
-- age = the resolution/observation period minus raise_period.
--
-- Built incremental, not full-refresh: each run only emits new triangle
-- cells for cohorts that had raise/close/break activity in the periods
-- not yet processed (tracked via the max raise_period + age already
-- stored). A cohort with zero resolutions in a given period is not
-- re-emitted that period -- its last recorded 'open' cell remains the
-- most recent known value until a later period supersedes it. This keeps
-- the incremental batch bounded by activity, not by the full cohort x age
-- grid, which is what "incremental" means for a triangle that otherwise
-- grows one age-column per period for every still-open cohort.
{{
    config(
        materialized='incremental',
        unique_key=['raise_period', 'age', 'node', 'order_channel', 'txn_type', 'outcome'],
        incremental_strategy='merge'
    )
}}

with events as (
    select *
    from {{ ref('stg_order_event') }}
    {% if is_incremental() %}
    where event_period > coalesce((select max(raise_period + age) from {{ this }}), -1)
    {% endif %}
),

-- cohorts touched by an event in this batch, and the latest period each
-- was observed at (the age we're reporting a triangle cell for)
cohort_periods as (
    select
        node,
        order_channel,
        txn_type,
        raise_period,
        max(event_period) as as_of_period
    from events
    group by node, order_channel, txn_type, raise_period
),

raised_totals as (
    select
        node,
        order_channel,
        txn_type,
        raise_period,
        sum(order_count) as raised
    from {{ ref('stg_order_event') }}
    where event_type = 'raised'
    group by node, order_channel, txn_type, raise_period
),

-- cumulative resolutions as of each cohort's as_of_period -- scanning full
-- history for the (small) set of cohorts active this batch, not the whole
-- table, which is what keeps this incremental rather than full-refresh.
cumulative_resolutions as (
    select
        cp.node,
        cp.order_channel,
        cp.txn_type,
        cp.raise_period,
        cp.as_of_period,
        coalesce(sum(case when e.event_type = 'closed' then e.order_count end), 0) as cum_closed,
        coalesce(sum(case when e.event_type = 'broken' then e.order_count end), 0) as cum_broken
    from cohort_periods cp
    left join {{ ref('stg_order_event') }} e
        on e.node = cp.node
       and e.order_channel = cp.order_channel
       and e.txn_type = cp.txn_type
       and e.raise_period = cp.raise_period
       and e.event_type in ('closed', 'broken')
       and e.event_period <= cp.as_of_period
    group by cp.node, cp.order_channel, cp.txn_type, cp.raise_period, cp.as_of_period
),

period_resolutions as (
    select
        node,
        order_channel,
        txn_type,
        raise_period,
        event_period as as_of_period,
        sum(case when event_type = 'closed' then order_count else 0 end) as closed_this_period,
        sum(case when event_type = 'broken' then order_count else 0 end) as broken_this_period
    from events
    where event_type in ('closed', 'broken')
    group by node, order_channel, txn_type, raise_period, event_period
),

triangle as (
    select
        cr.raise_period,
        cr.as_of_period - cr.raise_period as age,
        cr.node,
        cr.order_channel,
        cr.txn_type,
        'open' as outcome,
        rt.raised - cr.cum_closed - cr.cum_broken as count
    from cumulative_resolutions cr
    inner join raised_totals rt
        on rt.node = cr.node
       and rt.order_channel = cr.order_channel
       and rt.txn_type = cr.txn_type
       and rt.raise_period = cr.raise_period

    union all

    select
        pr.raise_period,
        pr.as_of_period - pr.raise_period as age,
        pr.node,
        pr.order_channel,
        pr.txn_type,
        'closed' as outcome,
        pr.closed_this_period as count
    from period_resolutions pr
    where pr.closed_this_period != 0

    union all

    select
        pr.raise_period,
        pr.as_of_period - pr.raise_period as age,
        pr.node,
        pr.order_channel,
        pr.txn_type,
        'broken' as outcome,
        pr.broken_this_period as count
    from period_resolutions pr
    where pr.broken_this_period != 0
)

select * from triangle
