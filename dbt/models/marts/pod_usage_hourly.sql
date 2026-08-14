{{ config(materialized='table') }}

-- Hourly per-pod CPU/memory aggregates from the bronze raw_pod_usage
-- snapshots (hourly_pod_usage pipeline). payload_json is a JSON array of
-- per-container records, parsed with Trino json functions and unnested.
with raw as (
    select
        ingested_at,
        cast(json_parse(payload_json) as array(json)) as samples
    from {{ source('lake_bronze', 'raw_pod_usage') }}
),
flat as (
    select
        date_trunc('hour', raw.ingested_at) as hour,
        json_extract_scalar(sample, '$.pod') as pod,
        cast(json_extract_scalar(sample, '$.cpu_millicores') as double) as cpu_millicores,
        cast(json_extract_scalar(sample, '$.memory_bytes') as double) as memory_bytes
    from raw
    cross join unnest(raw.samples) as t(sample)
    where json_extract_scalar(sample, '$.pod') is not null
)
select
    hour,
    pod,
    avg(cpu_millicores) as avg_cpu_millicores,
    max(cpu_millicores) as max_cpu_millicores,
    avg(memory_bytes) as avg_memory_bytes,
    max(memory_bytes) as max_memory_bytes,
    count(*) as samples
from flat
group by hour, pod
