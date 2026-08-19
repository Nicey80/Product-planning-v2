-- External regressor values (e.g. marketing spend) per node x
-- order_channel x period, for the engine's raise forecast to consume as
-- exogenous drivers (see docs/domain-model.md section 2.1: raise volume
-- is the controllable/plannable quantity). Sourced directly from a
-- reference-data seed -- regressors aren't part of the order/subscription
-- event pipeline, so there's no int_* step between source and mart.
select
    period,
    node,
    order_channel,
    regressor_name,
    regressor_value
from {{ ref('seed_regressor') }}
