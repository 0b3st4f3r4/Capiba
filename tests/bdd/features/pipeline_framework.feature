Feature: Declarative pipeline framework
  Ingestion pipelines are declared in YAML files and executed by the
  declarative runner, with sources, formulas, validations and destinations
  resolved by registries — no new Python code per pipeline.

  Scenario: A YAML-declared pipeline runs without new Python code
    Given a YAML spec declaring a pipeline with mock sources
    When the pipeline runs for the date "2026-01-15"
    Then the run report is successful
    And the report records the crawl, normalize and validate steps
    And the normalized contracts reach the silver layer

  Scenario: A spec with an unknown source fails with a clear error
    Given a YAML spec declaring the source "fonte_inexistente"
    When the spec is loaded
    Then the error states that the source "fonte_inexistente" is unknown
