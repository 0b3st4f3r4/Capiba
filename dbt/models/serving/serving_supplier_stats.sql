{{ config(materialized='table', database='dwh') }}

-- Serving copy of the gold supplier_stats mart in the PostgreSQL DWH
-- (Trino catalog `dwh`), for low-latency consumption outside the lake.
-- Cross-catalog materialization: dbt-trino renders the fully-qualified
-- target relation, and Trino executes the CTAS reading from the gold
-- Iceberg catalog and writing to the PostgreSQL catalog.
select * from {{ ref('supplier_stats') }}
