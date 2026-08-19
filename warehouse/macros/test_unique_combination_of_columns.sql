{% test unique_combination_of_columns(model, combination_of_columns) %}
-- Generic test: fails (returns rows) if `combination_of_columns` is not
-- unique per row of `model`. A minimal, dependency-free stand-in for
-- dbt_utils.unique_combination_of_columns -- this project intentionally
-- has zero external package dependencies (see warehouse/pyproject.toml).

with grouped as (
    select
        {{ combination_of_columns | join(', ') }},
        count(*) as row_count
    from {{ model }}
    group by {{ combination_of_columns | join(', ') }}
)

select *
from grouped
where row_count > 1

{% endtest %}
