{{ config(materialized='table') }}

-- Amendment red flags aggregated by buying agency (PR-D-05 § 3: share of
-- contracts with amendments, means and counts; the shares average over
-- the contracts where the flag is not null).
select
    siafi_code,
    count(*) as contracts,
    count_if(bronze_matched) as contracts_with_observations,
    round(avg(cast(f_value_amendment as double)), 4) as share_value_amendment,
    round(avg(cast(f_term_extension as double)), 4) as share_term_extension,
    round(avg(value_ratio), 4) as mean_value_ratio
from {{ ref('contract_amendments') }}
where siafi_code is not null and siafi_code != ''
group by siafi_code
