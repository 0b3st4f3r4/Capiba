Feature: FollowTheMoney vocabulary in the graph
  The ArangoDB graph follows the FtM vocabulary (O4): companies and
  persons as vertices, ownership/directorship as edges. The query
  "partners of the suppliers of a buying agency" is answered by a graph
  traversal, and the subgraph around a company exports as FtM JSON for
  interoperability with the investigative ecosystem (OpenSanctions,
  Aleph).

  Scenario: Partners of a buyer's suppliers come from a graph traversal
    Given the supplier "12345678000195" of buyer "900000" has partner "JOAO SILVA" via "ownership"
    When the partners of buyer "900000" are requested
    Then the partner list includes "JOAO SILVA" for supplier "12345678000195"

  Scenario: The company subgraph exports as FtM JSON
    Given the company "12345678" named "ACME LTDA" has partner "JOAO SILVA" via "ownership"
    When the FtM export of "12345678000195" is requested
    Then the FtM entities include a "Company" named "ACME LTDA"
    And the FtM entities include an "Ownership" from "person-p1" to "company-12345678"
