-- P7 (PR-D-05): every flag in {0, 1, NULL} and every value_ratio > 0
-- when present. NULL comparisons evaluate to NULL and are correctly not
-- returned; any returned row is a domain violation.
select *
from {{ ref('contract_amendments') }}
where f_value_amendment not in (0, 1)
   or f_term_extension not in (0, 1)
   or value_ratio <= 0
