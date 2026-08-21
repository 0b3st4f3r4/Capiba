{{ config(materialized='table') }}

-- Amendment red flags per contract (PR-D-05). Reference semantics:
-- src/capiba/detection/amendments.py — the bronze observation sequence
-- (publication crawls + update crawls) is ordered by ingestion date, the
-- LAST observation is sovereign, equality never fires and
-- missing/malformed fields are NULL (insufficient data).

with bronze_payloads as (
    select dt, payload_json from {{ source('lake_bronze', 'raw_pncp') }}
    union all
    select dt, payload_json from {{ source('lake_bronze', 'raw_pncp_contract_updates') }}
),

-- Parse each payload straight into a typed row with only the fields this
-- model needs, exactly like contract_red_flags: casting to array(json)
-- and extracting per element materializes every full contract dict in
-- memory and OOMKilled Trino on real data (2026-08-20, exit 137).
observations as (
    select
        b.dt as observed_on,
        t.pncp_id,
        try(cast(t.initial_amount as decimal(38, 4))) as initial_amount,
        try(cast(t.accumulated_amount as decimal(38, 4))) as accumulated_amount,
        try(cast(substr(t.validity_end, 1, 10) as date)) as validity_end,
        try(cast(t.rectifications as integer)) as rectifications
    from (
        select
            dt,
            cast(json_parse(payload_json) as array(row(
                numeroControlePNCP varchar,
                valorInicial varchar,
                valorAcumulado varchar,
                dataVigenciaFim varchar,
                numeroRetificacao varchar
            ))) as contracts
        from bronze_payloads
    ) b
    cross join unnest(b.contracts) as t(
        pncp_id, initial_amount, accumulated_amount, validity_end,
        rectifications
    )
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
        -- Full precision: rounding to 4 decimals collapsed tiny positive
        -- ratios to 0.0 on real data (P7 domain violation, PR-D-05). Cast
        -- to double BEFORE dividing: decimal division at limited scale
        -- still rounds ratios < 1e-4 to zero (observed: 0.10/1,000,700).
        else cast(p.last_accumulated as double) / cast(p.first_initial as double)
    end as value_ratio
from {{ source('lake_silver', 'contracts') }} s
left join per_contract p on p.pncp_id = s.id
