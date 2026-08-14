{{ config(materialized='table', database='dwh') }}

-- Daily contract volumes per buying municipality/UF in the PostgreSQL DWH
-- (Trino catalog `dwh`). Aggregated from the silver contracts source
-- because the contracts_daily mart is aggregated by dt only and carries
-- no municipality dimension.
select
    dt,
    buyer.city as municipality,
    buyer.uf,
    count(*) as contracts,
    sum(amount) as total_amount,
    count(distinct buyer.siafi_code) as agencies,
    count(distinct coalesce(supplier.cnpj, supplier.cpf)) as suppliers
from {{ source('lake_silver', 'contracts') }}
where buyer.city is not null
group by dt, buyer.city, buyer.uf
