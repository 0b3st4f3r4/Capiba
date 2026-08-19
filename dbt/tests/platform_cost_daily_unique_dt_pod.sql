-- Composite uniqueness of (dt, pod) in platform_cost_daily.
-- Singular data test (dbt core) — the project has no dbt_utils dependency
-- (no dbt/packages.yml), so dbt_utils.unique_combination_of_columns is not
-- available; this native test fails when any rows are returned.
select
    dt,
    pod,
    count(*) as rows_per_key
from {{ ref('platform_cost_daily') }}
group by dt, pod
having count(*) > 1
