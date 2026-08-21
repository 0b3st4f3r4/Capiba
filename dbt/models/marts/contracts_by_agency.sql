{{ config(materialized='table') }}

-- One row per buyer (SIAFI code). The silver carries spelling variants of
-- the agency name/level/UF under the same SIAFI code (605 duplicate
-- siafi_code groups on real data, 2026-08-20), so aggregate first per
-- observed variant and keep the attributes of the most frequent one.
with per_variant as (
    select
        buyer.siafi_code as siafi_code,
        buyer.name as agency_name,
        buyer.government_level as government_level,
        buyer.uf as uf,
        count(*) as n,
        sum(amount) as total_amount,
        min(signature_date) as first_signature,
        max(signature_date) as last_signature
    from {{ source('lake_silver', 'contracts') }}
    group by buyer.siafi_code, buyer.name, buyer.government_level, buyer.uf
)

select
    siafi_code,
    max_by(agency_name, n) as agency_name,
    max_by(government_level, n) as government_level,
    max_by(uf, n) as uf,
    sum(n) as contracts,
    sum(total_amount) as total_amount,
    min(first_signature) as first_signature,
    max(last_signature) as last_signature
from per_variant
group by siafi_code
