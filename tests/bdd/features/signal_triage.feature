Feature: Editorial triage of fraud signals
  Every detected signal enters an editorial queue (pending_review) and only
  leaves as confirmed/rejected/published through a human review — rejection
  requires a reason. Human labels feed the per-operator precision report
  (and, later, the supervised ML training dataset).

  Scenario: Newly detected signals enter triage as pending review
    Given a computed signal "single_bid" for supplier "12345678000199" with score 0.8
    When the signals are registered for triage
    Then the triage entry for "single_bid" on "12345678000199" has status "pending_review"

  Scenario: Recomputation does not overwrite a reviewed signal
    Given a computed signal "single_bid" for supplier "12345678000199" with score 0.8
    And the signal was confirmed by reviewer "ana"
    When a computed signal "single_bid" for supplier "12345678000199" with score 0.9 is registered again
    Then the triage entry for "single_bid" on "12345678000199" has status "confirmed"
    And the triage entry score is 0.9

  Scenario: Rejection requires a reason
    Given a computed signal "concentration" for buyer "26000" with score 0.7
    When reviewer "ana" rejects the signal without a reason
    Then the triage entry for "concentration" on "26000" has status "pending_review"

  Scenario: Published signals are final
    Given a computed signal "single_bid" for supplier "12345678000199" with score 0.8
    And the signal was confirmed by reviewer "ana"
    And the signal was published by reviewer "ana"
    When reviewer "bruno" tries to reject the signal with reason "falso positivo"
    Then the triage entry for "single_bid" on "12345678000199" has status "published"

  Scenario: Precision report aggregates human labels per operator
    Given a computed signal "single_bid" for supplier "12345678000199" with score 0.8
    And a computed signal "single_bid" for supplier "98765432000196" with score 0.6
    And the signal "single_bid" on "12345678000199" was confirmed by reviewer "ana"
    And the signal "single_bid" on "98765432000196" was rejected by reviewer "ana" with reason "falso positivo"
    When the precision report is computed
    Then the operator "single_bid" has precision 0.5
