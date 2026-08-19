Feature: Batch fraud signal detection
  The detect task computes fraud signals over the silver contracts table
  and materializes them in the gold fraud_signals table, using the canonical
  signal vocabulary of the API (score semantics: higher means more
  suspicious).

  Scenario: Supplier with Benford-deviating amounts is flagged
    Given 12 contracts of supplier "12345678000199" with leading digit 9 in all amounts
    When the fraud signals are computed
    Then a "anomalous_price" signal is emitted for supplier "12345678000199"
    And the "anomalous_price" signal score is above 0.5

  Scenario: Supplier winning via non-competitive modality is flagged
    Given 4 contracts of supplier "12345678000199" in modality "dispensa"
    When the fraud signals are computed
    Then a "single_bid" signal is emitted for supplier "12345678000199"
    And the "single_bid" signal score is exactly 1.0

  Scenario: Buyer dependent on a single supplier is flagged
    Given 3 contracts of buyer "26000" all won by the same supplier
    When the fraud signals are computed
    Then a "concentration" signal is emitted for buyer "26000"
    And the "concentration" signal score is exactly 1.0

  Scenario: Anomalous contract durations are flagged per supplier
    Given 12 contracts of supplier "12345678000199" where one lasts 10 years and the rest 1 month
    When the fraud signals are computed
    Then a "anomalous_duration" signal is emitted for supplier "12345678000199"
