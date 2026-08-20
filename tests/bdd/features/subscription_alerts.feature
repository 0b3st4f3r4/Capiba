Feature: Municipal alert subscriptions (O12)
  Community journalists subscribe to the published fraud signals of a
  municipality by its 7-digit IBGE code. Only signals that complete the
  editorial triage (O10) as "published" trigger an alert e-mail linking
  the reproducible evidence package (O9); confirmed or rejected signals
  never dispatch.

  Scenario: Journalist subscribes and confirms via the e-mail token
    When "ana@example.org" subscribes to municipality "2611606"
    Then the subscription of "ana@example.org" to "2611606" is "pending"
    When the management token is used to confirm
    Then the subscription of "ana@example.org" to "2611606" is "confirmed"

  Scenario: Unsubscribe via the same management token
    Given "ana@example.org" has a confirmed subscription to "2611606"
    When the management token is used to unsubscribe
    Then the subscription of "ana@example.org" to "2611606" is "unsubscribed"

  Scenario: Published signal alerts only the confirmed subscribers of the municipality
    Given "ana@example.org" has a confirmed subscription to "2611606"
    And "bruno@example.org" has a pending subscription to "2611606"
    When a "single_bid" signal of supplier "12345678000199" for "Recife"/"PE" is published
    Then 1 alert e-mail is sent
    And the alert e-mail links the evidence package of the signal

  Scenario: Published signal without a resolvable municipality alerts nobody
    Given "ana@example.org" has a confirmed subscription to "2611606"
    When a "single_bid" signal of supplier "12345678000199" without municipality is published
    Then no alert e-mail is sent
