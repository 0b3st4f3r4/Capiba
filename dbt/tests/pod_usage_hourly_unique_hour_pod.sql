-- Composite uniqueness of (hour, pod) in pod_usage_hourly.
-- Singular data test (dbt core) — the project has no dbt_utils dependency
-- (no dbt/packages.yml), so dbt_utils.unique_combination_of_columns is not
-- available; this native test fails when any rows are returned.
select
    hour,
    pod,
    count(*) as rows_per_key
from {{ ref('pod_usage_hourly') }}
group by hour, pod
having count(*) > 1
