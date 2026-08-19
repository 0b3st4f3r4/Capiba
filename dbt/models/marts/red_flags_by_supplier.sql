{{ config(materialized='table') }}

-- Red flags aggregated by supplier (PR-D-04 § 3: means and counts; the
-- shares average over the contracts where the flag is not null).
select
    supplier_id,
    count(*) as contracts,
    count(cri) as contracts_with_cri,
    round(avg(cri), 4) as mean_cri,
    round(avg(cast(f_non_competitive as double)), 4) as share_non_competitive,
    round(avg(cast(f_short_window as double)), 4) as share_short_window,
    round(avg(cast(f_price_ratio as double)), 4) as share_price_ratio
from {{ ref('contract_red_flags') }}
where supplier_id is not null
group by supplier_id
