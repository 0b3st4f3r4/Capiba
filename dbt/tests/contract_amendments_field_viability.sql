-- P8 (PR-D-05): among silver contracts WITH a bronze PNCP payload, at
-- least 50% must report positive valorInicial/valorAcumulado (i.e. a
-- computable f_value_amendment). Fails when the computable share drops
-- below 50% — the field is too sparse and the design changes (plan B:
-- the per-contract terms endpoint). Zero matched contracts yields a
-- NULL share and passes.
select
    count_if(bronze_matched) as matched,
    count_if(bronze_matched and f_value_amendment is not null) as computable,
    count_if(bronze_matched and f_value_amendment is not null)
        / cast(count_if(bronze_matched) as double) as computable_share
from {{ ref('contract_amendments') }}
having count_if(bronze_matched and f_value_amendment is not null)
    / cast(count_if(bronze_matched) as double) < 0.5
