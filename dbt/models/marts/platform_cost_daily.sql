{{ config(materialized='table') }}

-- Daily per-pod real usage vs the declared requests (seed `requests`,
-- generated from the chart values.yaml). Pods are matched to components
-- by the pod_pattern LIKE expression of the seed; pods without a match
-- keep NULL requests (hook jobs, ad-hoc pods).
with usage as (
    select
        cast(hour as date) as dt,
        pod,
        avg(avg_cpu_millicores) as avg_cpu_millicores,
        max(max_cpu_millicores) as max_cpu_millicores,
        avg(avg_memory_bytes) as avg_memory_bytes,
        max(max_memory_bytes) as max_memory_bytes
    from {{ ref('pod_usage_hourly') }}
    group by cast(hour as date), pod
)
select
    usage.dt,
    usage.pod,
    requests.component,
    usage.avg_cpu_millicores,
    requests.request_cpu_millicores,
    usage.max_cpu_millicores,
    usage.avg_memory_bytes,
    requests.request_memory_bytes,
    usage.max_memory_bytes,
    -- idleness: fraction of the request left unused on average
    1 - usage.avg_cpu_millicores / nullif(requests.request_cpu_millicores, 0) as cpu_idle_ratio,
    1 - usage.avg_memory_bytes / nullif(requests.request_memory_bytes, 0) as memory_idle_ratio
from usage
left join {{ ref('requests') }} as requests
    on usage.pod like requests.pod_pattern
