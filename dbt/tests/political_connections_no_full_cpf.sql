-- LGPD invariant (PR-D-08): no full 11-digit CPF may leave the
-- political_connections mart. Any returned row is a masking violation.
select *
from {{ ref('political_connections') }}
where donor_document_type = 'PF'
  and position('*' in donor_document_masked) = 0
