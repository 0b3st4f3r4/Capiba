# Pre-Registration as Engineering Practice: An Epistemic Architecture for Fraud Detection in Open Government Data

**Draft — technical article. Working title.**

Authors: Capiba project contributors.
Status: draft for internal review; not submitted.

## Abstract

Data-driven detection of fraud and corruption in public procurement has
matured around a stable methodological core: contractual red flags and
composite risk indices, sanctions screening with entity resolution, and
network analysis over buyer–supplier graphs. What the field has not solved
is epistemic, not algorithmic: most indicators are unvalidated domain
proxies, ground truth is chronically scarce, and negative or inconvenient
results are rarely published. We describe Capiba, an open-source fraud
detection engine for Brazilian open data built in the service of community
data journalism, whose primary contribution is not a new indicator but an
**epistemic architecture**: falsifiable pre-registration applied to
detection engineering, negative results published with the same rigor as
confirmations, content-addressed reproducible evidence attached to every
signal, human editorial triage that doubles as a label factory for
supervised learning, and privacy (LGPD) enforced as a fail-closed build
invariant. We present the doctrine and one case study in full — battery
D-01, in which a pre-registered calibration experiment refuted a Benford's
Law chi-square operator that had been silently inert in production, and its
registered refinement D-01b closed the loop — and outline four further
episodes from the project's public record. We argue that this discipline is
a transferable, low-cost answer to the field's proxy-validation problem,
and that publishing refutations is itself a research contribution.

## 1. Introduction

The empirical study of corruption in public procurement has converged, over
the last decade, on a small set of methods that demonstrably work. The
backbone is the family of contractual red flags and composite corruption
risk indices developed by Fazekas and collaborators: single bidding,
non-competitive procedure types, short advertisement windows, and
price-ratio outliers, averaged per contract into an auditable risk score
[1]. This approach has been validated cross-nationally [1],
operationalized at scale across 33 jurisdictions by the DIGIWHIST project
and opentender.eu [2], replicated as a reference dataset by the QoG
Institute, and turned into policy tooling by the IMF and the IDB. It works
because it is deterministic, explainable, and auditable. A second pillar is
compliance-style screening: crossing suppliers against sanctions and PEP
lists, supported by a mature open-source ecosystem — OpenSanctions and
yente over the FollowTheMoney data model, industrial entity resolution via
Senzing — and by Open Ownership's demonstration that beneficial ownership,
procurement, and sanctions data combine naturally in a graph [3]. The
field's consensus is that names alone are insufficient: fuzzy matching
requires a documentary anchor and veto by contradictory evidence. A third
pillar applies network science to the buyer–supplier graph, where collusion
appears as topological pattern — co-occurrence, win concentration, dense
communities — consolidated in the systematic review of Lyra et al. [4] and
extended by Medina-Hernández, Kertész and Fazekas, who show that network
features add predictive power to contractual ones for detecting sanctioned
suppliers in Mexico [5]. Brazil has its own canonical civic case: Operação
Serenata de Amor and its Rosie robot applied machine learning, Benford
analysis, and concentration indices to millions of congressional expense
receipts, producing thousands of leads and hundreds of formal complaints
[6].

Yet the field's own literature is candid about three structural weaknesses.
First, most indicators are *domain proxies* whose detection power against
real fraud is rarely measured; "dark numbers" make judicial and
administrative statistics unreliable as ground truth. Second, ground truth
for supervised learning is chronically scarce — almost nobody closes the
loop from labels to training to production. Third, and least discussed,
publication bias applies with full force to engineering: pipelines ship
with operators whose statistical behavior has never been measured, and when
validation is done and fails, the failure is quietly patched rather than
published. The consequence is that the field accumulates indicators faster
than it accumulates evidence about them.

This article presents Capiba, an open-source engine for fraud detection in
Brazilian open data — procurement contracts from the national PNCP portal
and state transparency portals, federal revenue company registries,
electoral campaign finance, sanctions lists, and municipal official
gazettes — built explicitly in the service of community data journalism.
Methodologically, Capiba implements the validated core of the field: a
Fazekas-style corruption risk index, sanctions screening with documentary
veto calibrated against the OpenSanctions Pairs benchmark, a
FollowTheMoney ownership graph, and network collusion signals. We make no
claim that these indicators are novel. Our claim is about the architecture
around them. Capiba treats every empirical assertion about a detector as
governed by a lightweight pre-registration doctrine: a falsifiable numeric
prediction with success *and* refutation criteria is registered before any
execution; battery configurations are declarative files with seeds;
refutations are published in the same format and with the same dignity as
confirmations; and every refuted form is refined only through a new,
dated, justified registration. Around this doctrine sit three supporting
mechanisms: content-addressed evidence packages that make every emitted
signal reproducible by third parties; an editorial triage queue through
which every signal must pass before publication, which doubles as a
systematic label factory; and a fail-closed privacy allowlist in which any
new data mart lacking an explicit LGPD classification breaks the build.

We believe this epistemic architecture is the project's transferable
contribution. It directly answers the proxy-validation problem: an
indicator that has never been falsifiably tested is, in this framework, not
yet an indicator. And it answers the publication-bias problem structurally,
by making refutation a first-class, citable artifact. The remainder of the
article presents the doctrine (Section 2), one complete case study — the
refutation and correction of a Benford's Law operator that was silently
inert in production (Section 3) — and outlines four further episodes from
the project's record (Sections 4–7), the supporting evidence and governance
machinery (Sections 8–9), and a discussion of limitations and adoption
costs (Sections 10–11). All pre-registrations, configurations, raw outputs,
and result reports cited here are public in the project repository.

## 2. The doctrine: pre-registration for detection engineering

Capiba's pre-registration doctrine adapts the conventions of registered
reports to engineering-scale detection experiments. Five rules:

1. **No battery without a pre-registration.** A falsifiable numeric
   prediction, primary metrics, success *and* refutation criteria,
   controls, and seeds live in a `PR-D-NN.md` document before any
   execution. Amendments are dated and justified; history is never
   rewritten.
2. **Negative results are results.** A well-measured refutation is
   published in `R-D-NN.md` with the same rigor as a success, followed by a
   registered refinement (`PR-D-NNb.md`) when the corrected form is tested.
3. **Everything is declarative.** Battery configuration lives in a JSON
   file, seeds included; no parameter lives only in code or command lines.
4. **Exact anchors first.** Whenever a value is computable a priori — an
   HHI with known shares, the false-positive rate of a chi-square test —
   the prediction is the exact value with a declared tolerance, never a
   vague band.
5. **Mandatory limitation section.** Every registration declares the
   measured regime (synthetic vs. real, operator scope) and what the
   battery does *not* prove.

Stable identifiers tie the chain together — battery D-NN, pre-registration
PR-D-NN, result R-D-NN, refinements suffixed b, c — so that each empirical
claim in the system is traceable to its registered prediction and its
measured outcome. Two consequences of this design deserve emphasis.
Refinement after refutation is not ad hoc patching: the corrected form is
itself registered before execution, so the audit trail preserves both the
failure and the fix. And refuted predictions become permanent regression
tests, so a future change that re-breaks a refuted invariant fails CI.

## 3. Case study: D-01 → D-01b, or the chi-square that never fired

This section develops the doctrine's canonical episode in full, because it
is small enough to be checked by hand and because the bug it caught is one
we suspect is widespread in ad hoc pipelines.

### 3.1 The registered prediction

Battery D-01 asked a narrow question: do the three statistical signals in
Capiba's detection task — Benford's Law deviation, supplier concentration
(HHI), and duration outliers — behave as the theory of their operators
predicts, over synthetic contracts with planted ground truth? The
pre-registration (PR-D-01, dated 2026-08-17, before any execution)
specified a deterministic generator: 40 control suppliers with 20 contracts
each, values log-uniform over four orders of magnitude (Benford-conformant
by construction); 40 planted suppliers with 60% of values carrying leading
digit 9, the classic "just below the threshold" manipulation pattern; two
buyers with exactly computable HHI anchors (0.2500 and 0.5400); and one
supplier with a planted share of one-day contracts. Ten declared seeds; 800
Benford cells per run, 400 control and 400 planted.

Five falsifiable predictions were registered. The sharpest is P2, the
calibration anchor: under the control, the Benford score (1 − p) is uniform
by construction of the chi-square test, so the false-positive rate at
score ≥ 0.95 must be *exactly* 5% — total false positives in the 400
control cells inside the binomial 99.9% band [6, 34]. P3 registered the
power side: at least 380 of 400 planted cells must reach score ≥ 0.95
(expected χ² ≈ 150 per cell against a critical value of 15.5). The
registration also declared, in its mandatory limitation section, that with
n = 20 per cell the expected count for digit 9 under Benford is 0.92, below
the asymptotic approximation's comfort zone — so a small deviation of the
false-positive rate from nominal was anticipated and declared *in advance*
as acceptable, with its mechanism named.

### 3.2 The double refutation

The measured outcome refuted both Benford predictions simultaneously, in
opposite directions: **0** false positives in 400 control cells (below the
band), and **1** hit in 400 planted cells (power ≈ 0.25%). The other three
predictions — signal conservation counts and both exact anchors —
reproduced to the digit in all ten seeds, ruling out the harness. The root
cause was structural: the operator fed `scipy.stats.chisquare` with
normalized frequencies (proportions summing to 1) in both `f_obs` and
`f_exp`. The chi-square goodness-of-fit test requires absolute counts; with
proportions, the statistic is divided by n (measured χ² ≈ 0.43 where the
correct value is ≈ 8.6 in a typical control cell), the p-value sticks at
~1.0, and the signal can never fire — neither on controls nor on the most
brazen manipulation. The declared small-sample limitation was explicitly
considered and excluded as the cause: it would shift the false-positive
rate by a few points, not to 0/400; the measured displacement was 20× in
the statistic.

The consequence is worth stating plainly: the Benford signal had been
**inert in production** since its introduction. No supplier would ever have
been flagged by it, under any manipulation, and nothing in the test suite
or in observed outputs would have revealed this — an inert statistical test
is silent. The battery did its job; the refutation was published as R-D-01
with the same structure and prominence as a success, and the raw outputs
remain versioned intact.

### 3.3 The registered refinement and the closed loop

Following the doctrine, the corrected form was pre-registered as PR-D-01b
before re-execution: a single change to the operator under test (absolute
counts into `chisquare`), with identical grid, seeds, bands, and
predictions — plus one fine anchor registered a priori: in the typical
control cell measured in R-D-01, the corrected χ² should be ≈ 8.64 (df = 8,
p ≈ 0.37). Re-executed, the refinement confirmed all five predictions:
24 false positives in 400 control cells (6.0% against the 5% nominal,
inside the registered band, the small excess consistent with the declared
n = 20 asymptotic limitation) and full power (400/400) on the planted
regime. The five predictions were then converted into a permanent
regression test that runs over the D-01b configuration in CI: any future
change to the statistical operators or the detection task that violates the
registered bands breaks the build.

Three lessons generalize. First, *exact anchors make refutation cheap to
localize*: because the HHI and duration anchors passed to the digit while
both Benford predictions failed in opposite directions, the harness, the
generator, and the small-sample regime were all excluded within one
battery. Second, *the loop is the unit of knowledge*: the contribution is
not the one-line fix (which any code review could in principle catch, and
ours did not) but the registered chain prediction → refutation → published
cause → registered refinement → confirmation → permanent guard. Third,
*this bug class is invisible without measurement*: a chi-square on
proportions is syntactically valid, returns plausible p-values, and fails
only by never firing. The Serenata/CEAP tradition popularized Benford
screening in Brazilian civic tech [6]; we suspect, but cannot prove, that
variants of this operator error are common in ad hoc implementations,
precisely because nothing forces the measurement. The doctrine exists to
force it.

## 4. Inconclusive as a verdict: D-03 and D-03b (collusion at real scale)

*[Skeleton — to be developed.]*

- Setup: `collusion_network` signal — all pairs C(k,2) of suppliers with ≥
  `min_wins` wins at the same buyer — validated only in synthetic regime
  (D-02); threshold a placeholder. D-03 pre-registered a calibration over
  the real accumulated graph (~152k `won` edges, ~98k buyer–supplier
  pairs) with an explicit *editorial triage budget*: backlog ≤ 500 pairs,
  increment ≤ 20/day, and a registered decision rule over candidate
  thresholds {3,4,5,6,8,10}.
- Key design move: nothing real is predicted (unknowable a priori, hence
  unfalsifiable); what is registered is the *decision rule*, structural
  invariants (double counting AQL vs. Python, monotonicity, coverage
  ≥ 90%, non-materialization above budget), and exact synthetic anchors.
- Result: P1–P7 confirmed exactly (sweep in 24 s, double counting exact,
  100% coverage) — and P8 **inconclusive**: no candidate fits the budget
  (627,592 pairs at w=3; 15,107 at w=10). Refutation is informative: the
  problem is the semantics, not the threshold; large buyers with dozens of
  habitual suppliers generate thousands of pairs with no coordinated
  alternation. The production default stays off — the battery enables a
  human decision, does not make it.
- D-03b: registered refinement to cross-buyer co-occurrence ("itinerant
  cartel" semantics, `min_buyers ≥ 2`). Synthetic anchors exact (including
  a degenerate control proving the refinement reduces bit-for-bit to the
  old semantics at `min_buyers=1`); real grid reduces volume ~28× (22,173
  pairs at (3,2)), but the smallest backlog (1,397 at (5,3)) is still ~2.8×
  over budget → **inconclusive** again. Diagnosis sharpened: the binding
  constraint is the backlog, not the increment; next refinement (D-03c)
  registered as pair dominance or top-k ranking.
- Argument to develop: two consecutive inconclusive batteries are a
  research output, not a failure — they map the boundary between an
  algorithmic-scale problem and a signal problem, and they prevented a
  placeholder threshold from flooding the human triage queue in production.
  Connect to Lyra et al. [4] and Medina-Hernández et al. [5]: network
  signals' literature rarely reports operational volumes at editorial
  scale.
- Honest operational detail worth keeping: first D-03b sweep discarded
  because concurrent writes violated the registered freeze window
  (double-counting invariant refuted mid-run) — the invariants caught a
  measurement-protocol violation, exactly as designed.

## 5. Sparse fields and refuted assumptions: D-05 (amendment red flags)

*[Skeleton — to be developed.]*

- Synthetic phase clean (5/5, exact flag vectors, boundary and null
  discipline); battery caught a bug in the *generator*, not the operator —
  doctrine working as intended.
- Real phase (205,349 contracts, via registered dbt data tests): P7 refuted
  by a two-layer rounding bug (round-to-4 collapsing ratios < 5×10⁻⁵; then
  Trino DECIMAL(38,4) scale) — corrected; P8 **refuted**: only 23.54% of
  bronze payloads carry both fields needed for the value-amendment flag,
  against a pre-registered ≥ 50% viability threshold.
- Argument: the most valuable output was a *coverage measurement*, not a
  detector. A red flag computable for less than a quarter of contracts is
  editorially near-mute, and the refutation triggered a registered plan B
  (per-contract amendment-terms endpoint) instead of silent degradation.
  Generalizes the field's proxy problem: indicators inherit the
  availability distribution of their source fields, and that distribution
  is almost never measured.

## 6. Calibrating against the field's benchmark: D-07 → D-07b (entity resolution)

*[Skeleton — to be developed.]*

- Setup: entity resolution for ownership-graph dedupe (name 0.6 + masked
  document 0.3 + age band 0.1, threshold 0.85), validated on synthetic
  cases and on the OpenSanctions Pairs benchmark (755,540 labeled pairs;
  stratified reservoir samples).
- D-07: P1–P6 confirmed — precision **1.00** on 1,000 negatives including
  pure homonyms — and P7 **refuted**: recall 0.025 against a registered
  band of 0.30–0.70. Root cause structural, not a bug: only 4.8% of
  benchmark positives carry bilateral identifiers; the matcher is
  conservative *by registered design* (missing feature = zero, name-only
  capped at 0.6 < threshold), so ~95% of benchmark positives are
  unreachable by construction.
- The registered conclusion inverted the naive reading: the *band* was
  miscalibrated, not the matcher. D-07b recalibrated the expectation
  (recall band [0.00, 0.10]) on three fresh seeds, kept the matcher
  untouched, confirmed 7/7 — precision 1.00 across 3,000 accumulated
  negatives, recall ≈ bilateral-document rate × conditional merge rate,
  stable across samples.
- Argument: measuring against the field's own public benchmark [3] is what
  makes the trade-off legible — recall 0.025 is the known, measured price
  of homonym discipline, which is the right regime when `same_as` edges
  feed an investigative ownership graph where one wrong merge contaminates
  chains of control. Contrast with the common practice of tuning to the
  benchmark's aggregate numbers.

## 7. Related aligned results in brief

*[Skeleton — one paragraph each.]*

- D-06b: fuzzy sanctions screening with documentary veto validated on
  OpenSanctions Pairs, precision 0.925 (7/7) — the field's consensus design
  (documentary anchor + veto) confirmed on its own benchmark.
- D-04/real-volume step: Fazekas-style CRI over 205,349 real contracts
  found **zero** contracts with CRI ≥ 0.5 — a published negative that says
  something real about index calibration transfer to Brazilian data
  (connect to [1], [2]).
- D-02: collusion semantics validated synthetically; threshold explicitly
  labeled placeholder pending D-03 — registered humility about regime
  transfer.

## 8. Reproducible evidence as architecture

*[Skeleton — to be developed.]*

- Every emitted signal carries a content-addressed evidence package:
  batch package (silver rows + `source_rows_sha256` + window + code
  version), per-signal manifest keyed to the triage key; `reproduce_signal`
  re-executes detection over the package and checks the score; tampering
  with one row breaks integrity *and* match (tested as exact anchors in
  D-03/D-03b, `graph_batch` kind for graph signals).
- Motivation: a fraud signal destined for journalism or legal use must be
  verifiable by third parties; the field treats this as an afterthought —
  here it is part of the signal's type.
- Link to doctrine: evidence reproduction is itself pre-registered and
  battery-tested (P4/Q5 in D-03/D-03b), not asserted.

## 9. Governance in the loop: triage as label factory, privacy as build invariant

*[Skeleton — to be developed.]*

- Editorial triage: every signal enters as `pending_review`; a named human
  reviewer moves it to `confirmed`/`rejected`/`published` (terminal);
  subscriber alerts fire *only* on the transition to `published`.
  Human-in-the-loop as accountability mechanism, not decoration.
- Label factory: triage decisions are stored per stable signal key with
  reviewer identity, yielding per-operator precision reports — a
  systematic answer to the field's ground-truth scarcity, feeding the
  supervised-ML roadmap with registered hypotheses (including a
  pre-registered refutation hypothesis: "gradient boosting will not beat
  the deterministic CRI on balanced accuracy").
- LGPD fail-closed: public export allowlist is declarative and fail-closed
  — a new mart without explicit classification fails a test, i.e., privacy
  review is a build invariant, not a later legal review; CPF masked at the
  source (CEAF pattern) in the political-connections mart. Position against
  the common "publish first, redact later" practice.

## 10. Limitations

*[Skeleton — to be developed.]*

- The doctrine measures operator fidelity to its own theory and operational
  viability; it does not, by itself, establish detection power against
  *real* fraud — that still requires labels (Section 9) and remains the
  project's open frontier, as it is the field's.
- Real-regime batteries are descriptive + decision-rule registrations, not
  predictions of real-world counts — deliberately, because unfalsifiable.
- Costs are real but modest: registration overhead per battery (hours, not
  weeks); the discipline occasionally surfaces inconvenient results the
  team must then act on (D-05 plan B, collusion default off).
- Single-project evidence: the doctrine has been run inside one project
  with one team's culture; transfer claims are hypotheses, and this article
  is partly an invitation to adversarial replication.
- Non-stationarity: real sweeps depend on ingestion state (backfill in
  progress moved the graph +56% within one day during D-03b); calibrations
  must be re-measured, and the reports say so.

## 11. Conclusion

*[Skeleton — short.]*

- Restate: the contribution is not an indicator but an epistemic
  architecture — falsifiable pre-registration, published refutations,
  content-addressed evidence, triage-as-labels, fail-closed privacy — that
  makes an existing, validated detection stack *accountable*.
- The D-01 → D-01b loop shows the machinery catching a production-inert
  operator; D-03/D-03b show "inconclusive" functioning as a verdict that
  protects the human queue; D-05 and D-07 show coverage and benchmark
  structure treated as first-class measurements.
- Invitation: the pre-registrations, configs, raw outputs, and result
  reports are public; external review of the doctrine and of individual
  batteries is actively sought.

## References

[1] M. Fazekas, "Uncovering High-Level Corruption: Cross-National
Objective Corruption Risk Indicators Using Public Procurement Data,"
*British Journal of Political Science*, 50(1), 2020.

[2] DIGIWHIST / opentender.eu, "Tender-Based Indicators and Political
Connections Index, 33 jurisdictions," Government Transparency Institute.

[3] OpenSanctions project and Open Ownership, "Spotting risks by combining
beneficial ownership, public procurement and sanctions data"; OpenSanctions
Pairs benchmark.

[4] P. Lyra et al., "Fraud, corruption and collusion in public procurement
activities: a systematic literature review on data-driven methods,"
*Applied Network Science*, 2022.

[5] J. Medina-Hernández, J. Kertész, M. Fazekas, "Learning from sanctioned
government suppliers: a machine learning and network science approach to
detecting fraud and corruption in Mexico," *Scientific Reports*, 2026.

[6] Operação Serenata de Amor / Rosie, case study, Rights CoLab; CEAP
Playbook.

*Note: full bibliographic details and URLs to be completed at submission;
all primary artifacts (PR-D-*, R-D-*, configs, raw outputs) are in the
project repository under `docs/preregistrations/`, `docs/results/`, and
`results/detect/`.*
