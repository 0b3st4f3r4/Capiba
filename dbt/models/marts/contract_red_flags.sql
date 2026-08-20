{{ config(materialized='table') }}

-- Red flags per contract (Fazekas & Kocsis CRI, deterministic variant).
-- Reference semantics: docs/preregistrations/PR-D-04.md § 3 (as amended)
-- and src/capiba/detection/red_flags.py; flags are 1 (suspect), 0 (not
-- suspect) or NULL (insufficient data), CRI is the mean of the non-null
-- flags rounded to 4 decimals. The 7-day window threshold is the
-- declared placeholder (calibration requires a PR-D-04b).

-- Parse each payload straight into a typed row with only the fields this
-- model needs. Casting to array(json) and extracting per element
-- materializes every full contract dict in memory, which blew past the
-- Trino per-node memory limit (EXCEEDED_LOCAL_MEMORY_LIMIT, UnnestOperator
-- >600MB over ~1.9GB of payloads); the typed cast keeps the unnested
-- blocks small.
with bronze_contracts as (
    select
        b.dt,
        t.pncp_id,
        t.proposal_opened_at,
        t.proposal_closed_at,
        t.estimated_amount,
        t.homologated_amount
    from (
        select
            dt,
            cast(json_parse(payload_json) as array(row(
                numeroControlePNCP varchar,
                dataAberturaProposta varchar,
                dataEncerramentoProposta varchar,
                valorInicialCompra varchar,
                valorTotalHomologado varchar
            ))) as contracts
        from {{ source('lake_bronze', 'raw_pncp') }}
    ) b
    cross join unnest(b.contracts) as t(
        pncp_id, proposal_opened_at, proposal_closed_at,
        estimated_amount, homologated_amount
    )
    -- Only silver contracts need flags; skip the rest before the dedup
    -- window (a plain left join would discard them anyway).
    where t.pncp_id in (select id from {{ source('lake_silver', 'contracts') }})
),

-- The same contract may arrive in overlapping crawl windows; keep one
-- row per contract (the flag fields are source data, stable per id).
bronze_dedup as (
    select pncp_id, proposal_opened_at, proposal_closed_at,
           estimated_amount, homologated_amount
    from (
        select *,
               row_number() over (partition by pncp_id order by dt desc) as rn
        from bronze_contracts
        where pncp_id is not null
    )
    where rn = 1
),

parsed as (
    select
        pncp_id,
        try(cast(substr(proposal_opened_at, 1, 10) as date)) as opened_on,
        try(cast(substr(proposal_closed_at, 1, 10) as date)) as closed_on,
        try(cast(estimated_amount as decimal(38, 4))) as estimated,
        try(cast(homologated_amount as decimal(38, 4))) as homologated
    from bronze_dedup
),

flags as (
    select
        s.id as contract_id,
        s.dt,
        coalesce(s.supplier.cnpj, s.supplier.cpf) as supplier_id,
        s.buyer.siafi_code as siafi_code,
        s.modality,
        (p.pncp_id is not null) as bronze_matched,
        case
            when s.modality is null
                 or trim(lower(s.modality)) in ('', 'not_informed')
                then cast(null as integer)
            when lower(s.modality) like '%dispensa%'
                 or lower(s.modality) like '%inexigibilidade%'
                then 1
            else 0
        end as f_non_competitive,
        case
            when p.opened_on is null or p.closed_on is null
                then cast(null as integer)
            when date_diff('day', p.opened_on, p.closed_on) < 7 then 1
            else 0
        end as f_short_window,
        case
            when p.estimated is null or p.homologated is null
                 or p.estimated <= 0 or p.homologated <= 0
                then cast(null as integer)
            when p.homologated > p.estimated then 1
            else 0
        end as f_price_ratio
    from {{ source('lake_silver', 'contracts') }} s
    left join parsed p on p.pncp_id = s.id
)

select
    *,
    round(
        cast(
            coalesce(f_non_competitive, 0)
            + coalesce(f_short_window, 0)
            + coalesce(f_price_ratio, 0) as double
        ) / nullif(
            (case when f_non_competitive is not null then 1 else 0 end)
            + (case when f_short_window is not null then 1 else 0 end)
            + (case when f_price_ratio is not null then 1 else 0 end),
            0
        ),
        4
    ) as cri
from flags
