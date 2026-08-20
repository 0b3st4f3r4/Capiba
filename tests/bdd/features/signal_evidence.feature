Feature: Reproducible evidence package per signal
  Every detected signal carries an evidence package: the operator that
  produced it, the run window, the code version and the source rows with a
  SHA-256 hash — so a third party re-executes the package and obtains the
  same result before publication (and has a defense after it).

  Scenario: A third party reproduces a signal from its package
    Given 4 contracts of supplier "12345678000199" in modality "dispensa"
    When the fraud signals are computed
    And the evidence packages are stored
    Then the signal "supplier:12345678000199:single_bid" has a stored manifest
    And reproducing "supplier:12345678000199:single_bid" from the batch package matches the stored score

  Scenario: A tampered source row breaks reproduction
    Given 4 contracts of supplier "12345678000199" in modality "dispensa"
    When the fraud signals are computed
    And the evidence packages are stored
    And a source row of the batch package is tampered with
    Then reproducing "supplier:12345678000199:single_bid" from the batch package does not match the stored score

  Scenario: Graph-derived signals are reproducible with the eligibility snapshot
    Given a computed collusion signal "supplier:91000000000001+91000000000002:collusion_network"
    And an eligibility snapshot with min_wins 3 for the collusion pair
    When the evidence packages are stored
    Then the manifest of "supplier:91000000000001+91000000000002:collusion_network" is marked reproducible
    And reproducing "supplier:91000000000001+91000000000002:collusion_network" from the graph batch package matches the stored score

  Scenario: Graph-derived signals without snapshot stay non-reproducible
    Given a computed collusion signal "supplier:91000000000001+91000000000002:collusion_network"
    When the evidence packages are stored
    Then the manifest of "supplier:91000000000001+91000000000002:collusion_network" is marked non-reproducible

  Scenario: Graph-derived signals reproduce under the co-occurrence refinement (PR-D-03b)
    Given a computed collusion signal "supplier:91000000000001+91000000000002:collusion_network"
    And an eligibility snapshot with min_wins 3 and min_buyers 2 for the collusion pair
    When the evidence packages are stored
    Then the graph batch package records min_buyers 2
    And reproducing "supplier:91000000000001+91000000000002:collusion_network" from the graph batch package matches the stored score
