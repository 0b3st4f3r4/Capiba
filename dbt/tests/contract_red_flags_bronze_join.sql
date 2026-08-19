-- P8 (PR-D-04): less than 1% of the silver contracts without a matching
-- bronze payload. Fails when the unmatched share reaches 1% (lineage
-- loss to investigate before trusting the flags). An empty mart yields
-- a NULL share and passes.
select
    count_if(not bronze_matched) as unmatched,
    count(*) as total,
    count_if(not bronze_matched) / cast(count(*) as double) as unmatched_share
from {{ ref('contract_red_flags') }}
having count_if(not bronze_matched) / cast(count(*) as double) >= 0.01
