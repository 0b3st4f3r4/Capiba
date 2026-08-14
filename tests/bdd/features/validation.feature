Feature: Contract validation
  The validate step computes a quality report (checksum, duplicates,
  normalization errors) over the normalized contracts before persistence.

  Scenario: Batch with duplicate contract ids is rejected
    Given 3 normalized contracts where 2 share the same id
    When the contracts are validated
    Then the report marks the batch as invalid
    And the report counts 1 duplicated id

  Scenario: Batch with unique contract ids passes
    Given 3 normalized contracts with unique ids
    When the contracts are validated
    Then the report marks the batch as valid
    And the report counts 0 duplicated ids

  Scenario: Normalization errors are surfaced in the report
    Given 3 normalized contracts with unique ids
    And 2 normalization errors from the previous step
    When the contracts are validated
    Then the report counts 2 normalization errors
