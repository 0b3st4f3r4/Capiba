-- P6 (PR-D-05): exactly one mart row per silver contract. The unique +
-- not_null column tests on contract_id cover duplicates; this singular
-- test fails when the row counts diverge (missing contracts).
select
    mart_rows,
    silver_rows
from (
    select
        (select count(*) from {{ ref('contract_amendments') }}) as mart_rows,
        (select count(*) from {{ source('lake_silver', 'contracts') }}) as silver_rows
) counts
where mart_rows != silver_rows
