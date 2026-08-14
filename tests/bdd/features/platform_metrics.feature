Feature: Platform metrics of the declarative pipelines
  Every pipeline run publishes per-step metrics (duration, rows in/out,
  validation errors) to the gold platform_metrics table — the datasource
  of the ingestion observability dashboards.

  Scenario: A pipeline run publishes duration and volume metrics
    Given a YAML spec declaring a pipeline with mock sources
    When the pipeline runs for the date "2026-01-15"
    Then the gold platform_metrics table has one row per step of the run
    And each metrics row records the pipeline name, duration and row counts
