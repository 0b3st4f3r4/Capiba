{{ config(materialized='table') }}

-- Grouped by supplier id only: the legal name can vary between records of
-- the same CNPJ/CPF (typing differences between sources).
select
    coalesce(supplier.cnpj, supplier.cpf) as supplier_id,
    arbitrary(supplier.legal_name) as supplier_name,
    count(*) as contracts,
    sum(amount) as total_amount,
    count(distinct buyer.siafi_code) as agencies,
    min(dt) as first_seen,
    max(dt) as last_seen
from {{ source('lake_silver', 'contracts') }}
group by coalesce(supplier.cnpj, supplier.cpf)
