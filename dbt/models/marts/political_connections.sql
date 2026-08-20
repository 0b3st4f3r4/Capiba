{{ config(materialized='table') }}

-- Publishable view of the political_connection signals (PR-D-08):
-- campaign donors of elected mayors who became suppliers of the
-- municipality inside the mandate window, enriched with the silver TSE
-- tables and the UE <-> SIAFI crosswalk seed.
--
-- LGPD classification (PR-D-08 slice 3 decision): the full CPF/CNPJ never
-- leaves this mart. CPFs (11 digits, personal data) are masked CEAF-style
-- ('***' + middle 6 digits + '**'); CNPJs (14 digits) identify companies,
-- whose donation records are public in the TSE and RFB publications, and
-- are kept. The silver keeps the complete documents for the deterministic
-- match (decision recorded in PR-D-08 §2). The stable key is exposed only
-- as a SHA-256 hash (signal_id), matching the editorial triage key.

with signals as (
    -- fraud_signals is appended per detect run; keep the latest row per
    -- (donor document, elected candidate, election year).
    select *
    from (
        select
            dt,
            entity_id,
            score,
            details,
            row_number() over (
                partition by
                    entity_id,
                    json_extract_scalar(details, '$.candidate.sequential'),
                    json_extract_scalar(details, '$.election_year')
                order by dt desc
            ) as rn
        from {{ source('lake_gold', 'fraud_signals') }}
        where signal_type = 'political_connection'
    )
    where rn = 1
),

parsed as (
    select
        dt as signal_dt,
        cast(json_extract_scalar(details, '$.election_year') as integer)
            as election_year,
        json_extract_scalar(details, '$.candidate.sequential')
            as candidate_sequential,
        entity_id as donor_document,
        cast(json_extract_scalar(details, '$.donation_total_brl') as double)
            as donation_total_brl,
        cast(json_extract_scalar(details, '$.contracts_total_brl') as double)
            as contracts_total_brl,
        cast(json_extract_scalar(details, '$.buyer_total_brl') as double)
            as buyer_total_brl,
        cast(json_extract_scalar(details, '$.share') as double) as share,
        score,
        cast(json_extract_scalar(details, '$.contracts') as integer)
            as contracts,
        json_extract_scalar(details, '$.buyer.name') as buyer_name,
        json_extract_scalar(details, '$.buyer.city') as buyer_city,
        json_extract_scalar(details, '$.buyer.uf') as buyer_uf,
        json_extract_scalar(details, '$.buyer.siafi_code') as buyer_siafi_code,
        json_extract_scalar(details, '$.candidate.candidate_name')
            as candidate_name,
        json_extract_scalar(details, '$.candidate.party') as party,
        cast(json_extract_scalar(details, '$.mandate_start') as date)
            as mandate_start,
        cast(json_extract_scalar(details, '$.mandate_end') as date)
            as mandate_end
    from signals
),

-- The silver TSE tables are monthly snapshots (full dump per run
-- partition); keep only the latest partition of each.
candidacies_latest as (
    select election_year, candidate_sequential, office, ue_code, ue_name, uf
    from {{ source('lake_silver', 'candidacies') }}
    where dt = (select max(dt) from {{ source('lake_silver', 'candidacies') }})
),

-- Donation profile per effective donor (origin donor has priority, as in
-- the signal) from the silver, so the mart carries publishable donation
-- detail beyond the aggregate embedded in the signal details.
donations as (
    select
        election_year,
        candidate_sequential,
        coalesce(donor_origin_document, donor_document) as donor_document,
        count(*) as donations,
        min(donation_date) as first_donation,
        max(donation_date) as last_donation
    from {{ source('lake_silver', 'campaign_donations') }}
    where dt = (
        select max(dt) from {{ source('lake_silver', 'campaign_donations') }}
    )
    group by 1, 2, 3
),

enriched as (
    select
        p.*,
        c.office,
        c.ue_code,
        c.ue_name,
        x.siafi_code as crosswalk_siafi_code,
        x.ibge_code,
        d.donations,
        d.first_donation,
        d.last_donation
    from parsed p
    left join candidacies_latest c
        on c.election_year = p.election_year
        and c.candidate_sequential = p.candidate_sequential
    left join {{ ref('ue_siafi_crosswalk') }} x
        on x.ue_code = c.ue_code
    left join donations d
        on d.election_year = p.election_year
        and d.candidate_sequential = p.candidate_sequential
        and d.donor_document = p.donor_document
)

select
    to_hex(sha256(to_utf8(concat(
        coalesce(cast(election_year as varchar), ''),
        '|',
        coalesce(candidate_sequential, ''),
        '|',
        coalesce(donor_document, '')
    )))) as signal_id,
    signal_dt,
    election_year,
    case
        when length(donor_document) = 11
            then '***' || substr(donor_document, 4, 6) || '**'
        else donor_document
    end as donor_document_masked,
    case when length(donor_document) = 11 then 'PF' else 'PJ' end
        as donor_document_type,
    candidate_name,
    party,
    office,
    ue_code,
    coalesce(ue_name, buyer_city) as municipality,
    uf,
    coalesce(crosswalk_siafi_code, buyer_siafi_code) as siafi_code,
    ibge_code,
    buyer_name,
    contracts,
    donations,
    first_donation,
    last_donation,
    donation_total_brl,
    contracts_total_brl,
    buyer_total_brl,
    share,
    score,
    mandate_start,
    mandate_end
from enriched
