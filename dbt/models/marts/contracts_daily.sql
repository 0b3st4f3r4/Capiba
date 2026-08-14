{{ config(materialized='table') }}

select
    dt,
    count(*) as contracts,
    sum(amount) as total_amount,
    count(distinct buyer.siafi_code) as agencies,
    count(distinct coalesce(supplier.cnpj, supplier.cpf)) as suppliers
from {{ source('lake_silver', 'contracts') }}
group by dt
