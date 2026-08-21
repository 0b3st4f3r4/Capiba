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
-- Documentless suppliers (e.g. foreign companies with neither CNPJ nor CPF
-- in the PNCP payload) cannot be keyed or screened — exclude them.
where coalesce(supplier.cnpj, supplier.cpf) is not null
group by coalesce(supplier.cnpj, supplier.cpf)
