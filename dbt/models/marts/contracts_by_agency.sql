{{ config(materialized='table') }}

select
    buyer.siafi_code,
    buyer.name as agency_name,
    buyer.government_level,
    buyer.uf,
    count(*) as contracts,
    sum(amount) as total_amount,
    min(signature_date) as first_signature,
    max(signature_date) as last_signature
from {{ source('lake_silver', 'contracts') }}
group by buyer.siafi_code, buyer.name, buyer.government_level, buyer.uf
