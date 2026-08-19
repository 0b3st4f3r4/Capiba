{{ config(materialized='table') }}

-- Amendment red flags per contract (PR-D-05, O2). Reference semantics:
-- src/capiba/detection/amendments.py — the bronze observation sequence
-- (publication crawls + update crawls) is ordered by ingestion date, the
-- LAST observation is sovereign, equality never fires and
-- missing/malformed fields are NULL (insufficient data).

with bronze_payloads as (
    select dt, payload_json from {{ source('lake_bronze', 'raw_pncp') }}
    union all
    select dt, payload_json from {{ source('lake_bronze', 'raw_pncp_contract_updates') }}
),

observations as (
    select
        b.dt as observed_on,
        json_extract_scalar(c, '$.numeroControlePNCP') as pncp_id,
        try(cast(json_extract_scalar(c, '$.valorInicial') as decimal(38, 4))) as initial_amount,
        try(cast(json_extract_scalar(c, '$.valorAcumulado') as decimal(38, 4))) as accumulated_amount,
        try(cast(substr(json_extract_scalar(c, '$.dataVigenciaFim'), 1, 10) as date)) as validity_end,
        try(cast(json_extract_scalar(c, '$.numeroRetificacao') as integer)) as rectifications
    from (
        select dt, cast(json_parse(payload_json) as array(json)) as contracts
        from bronze_payloads
    ) b
    cross join unnest(b.contracts) as t(c)
),

-- One row per contract: first positive valorInicial, last positive
-- valorAcumulado, first/last parseable validity end (all by ingestion dt).
per_contract as (
    select
        pncp_id,
        count(*) as observations,
        min_by(initial_amount, observed_on) filter (where initial_amount > 0) as first_initial,
        max_by(accumulated_amount, observed_on) filter (where accumulated_amount > 0) as last_accumulated,
        min_by(validity_end, observed_on) filter (where validity_end is not null) as first_validity_end,
        max_by(validity_end, observed_on) filter (where validity_end is not null) as last_validity_end,
        max(rectifications) as max_rectifications
    from observations
    where pncp_id is not null
    group by pncp_id
)

select
    s.id as contract_id,
    s.dt,
    coalesce(s.supplier.cnpj, s.supplier.cpf) as supplier_id,
    s.buyer.siafi_code as siafi_code,
    (p.pncp_id is not null) as bronze_matched,
    coalesce(p.observations, 0) as observations,
    case
        when p.first_initial is null or p.last_accumulated is null
            then cast(null as integer)
        when p.last_accumulated > p.first_initial then 1
        else 0
    end as f_value_amendment,
    case
        when p.first_validity_end is null
            then cast(null as integer)
        when p.last_validity_end > p.first_validity_end then 1
        else 0
    end as f_term_extension,
    p.max_rectifications,
    case
        when p.first_initial is null or p.last_accumulated is null
            then cast(null as double)
        else round(cast(p.last_accumulated / p.first_initial as double), 4)
    end as value_ratio
from {{ source('lake_silver', 'contracts') }} s
left join per_contract p on p.pncp_id = s.id
