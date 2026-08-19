-- P7 (PR-D-04): every flag in {0, 1, NULL} and every CRI in [0, 1] or
-- NULL. NULL comparisons evaluate to NULL and are correctly not
-- returned; any returned row is a domain violation.
select *
from {{ ref('contract_red_flags') }}
where f_non_competitive not in (0, 1)
   or f_short_window not in (0, 1)
   or f_price_ratio not in (0, 1)
   or cri < 0
   or cri > 1
