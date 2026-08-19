Feature: Collusion network signal in the detect task
  Besides the statistical signals over the silver contracts, the detect task
  queries the ArangoDB graph for pairs of suppliers alternating wins for the
  same buyer (detect_collusion) and emits one collusion_network signal per
  pair, with a binary score placeholder validated by battery D-02. Graph
  failures are best-effort: they never fail the task.

  Scenario: Suppliers eligible for the same buyer are flagged as a pair
    Given the graph has eligible suppliers "91000000000001" and "91000000000002" for buyer "26000"
    When the detect task runs
    Then a "collusion_network" signal is written for the pair "91000000000001+91000000000002"

  Scenario: ArangoDB unavailable does not fail the detect task
    Given the graph database is unavailable
    When the detect task runs
    Then no "collusion_network" signal is written
