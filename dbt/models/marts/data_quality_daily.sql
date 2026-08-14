{{ config(materialized='table') }}

select
    dt,
    count(*) as rows_loaded,
    count(distinct id) as distinct_contracts,
    count(*) - count(distinct id) as duplicate_ids,
    count(*) filter (where amount is null or amount = 0) as zero_amount_rows,
    count(*) filter (where supplier.cnpj is null and supplier.cpf is null)
        as missing_supplier_rows
from {{ source('lake_silver', 'contracts') }}
group by dt
