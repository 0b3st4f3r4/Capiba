Feature: Batch fraud signal detection
  The detect task computes statistical fraud signals over the silver
  contracts table and materializes them in the gold fraud_signals table
  (score semantics: higher means more suspicious).

  Scenario: Supplier with Benford-deviating amounts is flagged
    Given 12 contracts of supplier "12345678000199" with leading digit 9 in all amounts
    When the fraud signals are computed
    Then a "benford_deviation" signal is emitted for supplier "12345678000199"
    And the "benford_deviation" signal score is above 0.5

  Scenario: Buyer dependent on a single supplier is flagged
    Given 3 contracts of buyer "26000" all won by the same supplier
    When the fraud signals are computed
    Then a "supplier_concentration" signal is emitted for buyer "26000"
    And the "supplier_concentration" signal score is exactly 1.0

  Scenario: Anomalous contract durations are flagged per supplier
    Given 12 contracts of supplier "12345678000199" where one lasts 10 years and the rest 1 month
    When the fraud signals are computed
    Then a "duration_outlier_share" signal is emitted for supplier "12345678000199"
