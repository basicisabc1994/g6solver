# G6 Evidence-Constrained Harness Adaptation (ECHA)

**Implementation specification for coding agents**  
**Version:** 0.1  
**Date:** 2026-10-09  
**Status:** Proposed; local research implementation only; not approved for production deployment  
**Parent theory:** G6 Adaptive Harness Intelligence (AHI), version 0.1  
**Owner decisions:** Standards-first verification, governed adaptation, three-state completion, no paid evaluation or production promotion without explicit authorisation

> **One-sentence mandate:** Implement a minimal, auditable failure-to-learning-to-transfer loop that discovers a narrow error using independently justified evidence, learns a reusable executable safeguard, and tests that safeguard on previously unseen tasks without allowing the learner to redefine the criteria for success.

## 0. Executive decision and the validation paradox

G6 faces a real validation paradox: if arbitrary answers cannot be reliably judged, how can G6 claim to learn from them? The engineering resolution is **not** to posit a universal correctness oracle. Instead:

1. Specify the scope of each task standard, failure claim, and oracle.
2. Exploit **partial falsification**: an independently established violation rejects an answer without requiring a full proof of overall correctness.
3. Acquire and validate **reusable executable safeguards** for such violations.
4. Measure the safeguard's **incremental effect on fresh tasks**, with fixed standards and independent adjudication.
5. Explicitly abstain, qualify, or escalate when reliable evidence is absent.

There is **no global correctness claim** and no general solution to the oracle problem in this milestone. A positive result supports a narrow, distribution- and oracle-scoped claim. A negative or inconclusive result is a valid research outcome and MUST remain in the ledger.

### Background evidence, not a promised result

The 8 October 2026 report, on an earlier run, reported 10 false accepts for a local-model baseline and 11 through G6 across 352 episodes per arm (22 underlying task templates); a narrower collateral-edit check showed promise. It also identified labels without discriminatory power and weak exploratory ranking signals. These are **past, small synthetic experiments**, not general evidence that G6 helps or harms. The coding agent MUST inspect the current repository rather than assume this historical snapshot is still current.

## 1. Required outcome, non-goals, and stage gates

### 1.1 Primary milestone: one verified learning event (VLE-1)

The delivered prototype MUST show all of the following, with machine-readable evidence:

- **Known defect**: an independently generated or externally confirmed example violates a frozen, explicit task requirement.
- **Discovery**: G6's executor or existing check fails to handle that example, and this is evidenced in a baseline replay (or else the experiment is not a demonstration of newly acquired value).
- **Diagnosis**: the failure is classified, with its causal hypothesis and alternative explanations recorded. The hypothesis is not treated as fact without support.
- **Candidate acquisition**: G6 proposes a bounded, executable safeguard/skill or routing improvement, rather than just storing the prose lesson.
- **Falsification**: independent positive and negative controls show the safeguard can detect the targeted violation and does not reject authorised behavior on the tested cases.
- **Transfer**: in a previously unseen task/repository, an approved-for-experiment version of the safeguard activates in response to its trigger and catches or prevents a relevant defect missed by the frozen control, with no evaluator or fixture leakage.
- **Counterfactual**: static G6 and adapted G6 are evaluated against the same independently specified requirement; differences in model, tools, budgets, or test conditions are made explicit.
- **Bounded claim**: the report states exactly what was established, what remains unknown, costs and regressions, and does not assert general reliability or learning superiority on the basis of one event.

A single transferred event is a **mechanism demonstration**, not statistical proof of broad uplift. Larger claims require a separate preregistered study.

### 1.2 Non-goals for v0.1

- Universal correctness checking, solving P versus NP, or proving general global learning convergence.
- Self-modification of governance, safety thresholds, task standards, or independent evaluators.
- Automatic production deployment, user-facing confidence scores, or unapproved changes to user-facing wording.
- Generalised agent self-training/fine-tuning or unrestricted autonomous software changes.
- Large new microservice framework, duplicate component registry, duplicate task-standard engine, or mandatory mutation runs on the workforce execution path.
- Treating multiple correlated model approvals as independent evidence.

### 1.3 Scope and permissions

Default feature state MUST be **OFF** for production and **experiment-only** locally. No paid model-API expenditure, database migration, network side effect, key/secrets change, public rollout, push, or deployment without authorisation. If a read-only repository investigation needs network access, respect existing access policies. The agent MAY create local test fixtures, research scripts, and feature-flagged adapters consistent with current architecture.

## 2. Core principles and normative terms

**MUST** indicates an acceptance requirement; **SHOULD** a strong preference requiring documented reasons for exception; **MAY** an optional extension.

### 2.1 Definitions

- **Task Standard Contract (TSC):** Frozen, versioned statement of accepted behavior, prohibited behavior, required evidence, resources, permissions, and escalation conditions. A TSC is a representation of the specified requirements, not a guarantee that the underlying human specification is complete.
- **Oracle:** Procedure or source that produces evidence about a stated predicate. It may be formal, executable, partial, heuristic, or absent. It is not presumed correct outside its documented scope.
- **Defect witness:** Independently validated instance of a violation of one task predicate; it need not solve the whole task.
- **Experience:** Versioned execution trace plus admissible outcome evidence, including failed attempts and negative learning results.
- **Learning candidate:** Proposed adaptation to a harness component, skill, test, or activation rule, with a preregistered target and falsifier.
- **Learned guard:** Versioned executable procedure with explicit activation conditions, a failure class, and tested limits. A guard's presence alone is not evidence of effective protection.
- **Promotion:** Separate, controlled permission to activate a candidate in an experimental or operational harness. For v0.1, **experimental-only**; no production promotion.
- **Independent evaluator:** Oracle/grading procedure whose specification, hidden cases, and authority are outside the adapting agent's writable scope. Independence is a property of actual information and control boundaries, not of the number of agents.
- **Failure escape:** A defect not discovered before a relevant decision or release gate; distinguish from **generation failure**, where no candidate is produced.

### 2.2 The evidence-constrained claim rule

Every accuracy, success, reliability, and learning claim MUST identify:

1. **Predicate** being checked.
2. **Task/distribution scope** and population.
3. **Evidence source and provenance.**
4. **Relevant assumptions** about the oracle and independence.
5. **Known limitations and inconclusive cases.**
6. **Version identifiers** of the task standard, harness, oracle, and learned component.

If a required predicate has not been independently established, do not infer it from a clean test log. **A lack of observed defects is not proof of correctness.**

### 2.3 Evidence levels (not universal truth rankings)

| Code | Evidence category | Permitted interpretation | Limits |
|---|---|---|---|
| `FORMAL_CHECK` | Proof/certificate checked by trusted kernel or equivalent | The formal proposition follows relative to the checker/specification/trust assumptions | Formalisation may not capture intended requirements |
| `INDEPENDENT_EXECUTABLE` | Independent executable oracle / known-answer case | Predicate holds/fails on covered cases | Coverage may be incomplete; reference may be defective |
| `PARTIAL_FALSIFIER` | Valid invariant, metamorphic relation, regression or fault-injection test | Demonstrated violation refutes the candidate on stated property | Passing does not establish full correctness |
| `HEURISTIC_REVIEW` | LLM/human judgement or proxy metric | Tentative evidence, disagreement, or investigation lead | Fallible, possibly correlated; cannot silently substitute for required oracle |
| `NO_DISCRIMINATING_EVIDENCE` | No relevant test or accessible observation | Unknown / evidence gap | Must not upgrade to verified correctness |

Store **oracle applicability, independence rationale, known defect classes, and calibration provenance separately**. A formal proof of the wrong requirement does not satisfy the true user requirement. Avoid simplistic “level A always outranks B” logic.

### 2.4 Mathematical limits that constrain implementation

- If two opposite-label task states produce identical distributions over **all admissible observations under the allowed policy**, no verifier using only those observations can distinguish them under equal priors beyond chance. Do **not** label failures of three exploratory signals as a proof of this premise.
- With a binary evaluator `V` and actual acceptance `S`, if an **independently justified** distribution-specific error bound exists, `P(V != S) <= epsilon`, then `|E[V] - E[S]| <= epsilon`. For a two-harness comparison, a measured population improvement `delta_V` supports only `delta_S >= delta_V - epsilon_old - epsilon_new`. Unknown `epsilon` MUST stay unknown; no fabricated bounds.
- Partial invariants and controlled fault-injection can supply reliable negative controls without general correctness labels. Claims remain scoped to the proven property and evaluated cases.

No confidence interval, error rate, or causal statement may be generated without documented sampling assumptions, sample provenance, and the relevant comparison.

## 3. Architecture: compose with existing G6, do not clone it

**First task is a code-and-documentation mapping.** Identify actual current implementations and ownership boundaries before proposing new files or names. The earlier theory mentioned `Step Generator`, `meta_router`, and `failure-mode engine` as conceptual modules; they are **not assumed to exist as named code modules**.

Logical responsibilities (reuse implementations and conventions already present):

| Logical capability | Responsibility | Forbidden shortcut |
|---|---|---|
| Task-standard adapter | Read/freeze TSC predicates, mandatory checks, permissions, inherited mission risks | Infer unrestricted permissions from free-form prose |
| Oracle/evidence registry | Represent oracle type, version, scope, outcome, source, caveats, and independence | Treat author-controlled tests as independent validators |
| Episode recorder | Store ordered events, pre-outcome forecasts, tool invocation, costs, failure/outcome records | Persist only final “passed” flags or silently drop failures |
| Failure diagnosis | Classify observed issue; capture witness, missing capability and competing hypotheses | Interpret every failure as a model-generation failure |
| Guard candidate builder | Make a bounded test/skill/routing patch tied to explicit hypothesis and falsifier | Change evaluator, task standard, or release threshold |
| Component/skill registry | Version and resolve candidate/approved guards; record present/reachable/activated/effective | Equate installed with executed or effective |
| Independent evaluation adapter | Run hidden tests under evaluator-only authority and return bounded reports | Expose hidden tests/transcripts to builder |
| Promotion guardian | Reject/approve experimental activation using immutable policy and evidence | Promote from LLM self-certification or from a repeatedly tuned holdout |
| Metrics/claims ledger | Produce reproducible comparisons, uncertainty, counts, costs, and unknowns | Emit generalised trust percentages or unsupported claims |

Keep operational executor, adapter/policy updater, evaluator, and promotion authority logically distinct and, where supported, separately permissioned. A different LLM subagent with shared writable fixtures and visible reviewer transcripts does **not** constitute adequate independence.

## 4. Minimal typed data contracts

Prefer existing dataclass/Pydantic/TypeScript types, persistence, event log, and JSON conventions. The following is a **conceptual contract**; names and paths are illustrative, not an instruction to create duplicate services.

### 4.1 TaskStandardContract

Required fields:

```yaml
schema_version: echa.v1
task_id: api-compat-001
standard_id: spec-public-api-7
standard_sha256: '<computed-content-hash>'
source_provenance: '<authoritative task document and approval>'
mandatory_predicates:
  - id: preserves-existing-public-contract
    description: 'Unauthorised public API behavior changes are prohibited'
    evidence_required: ['independent_contract_test']
    severity: high
prohibited_actions: ['alter-oracle', 'weaken-tests', 'unauthorised-helper-change']
authorised_changes:
  declared_helper_names: []
  declaration_confirmed_by_owner: false
risk:
  local: moderate
  downstream: high
  mission: high
resource_limit: '<existing task policy>'
completion_policy: '<existing three-state release rules>'
```

Do not introduce this exact YAML to end users unless the existing G6 product supports it. TSC changes require a different approval path than normal learning.

### 4.2 EpisodeRecord

Minimum fields:

- `episode_id`, `task_id`, `task_family`, `repository_or_group_id`, `split_id`.
- `standard_hash`, `executor_version`, `harness_version`, `oracle_version`, `component_versions`.
- `model_id`, `model_parameters_or_configuration`, `seed_if_supported`, `cost_budget`.
- Ordered append-only events: plan, chosen action, activation attempt, tool result, evidence received, repair, adjudication.
- **Prospective** risk prediction or `not_available` recorded *before outcome*; do not backfill forecasts.
- `candidate_artifact_hash`, `outcome_state`, `evidence_refs`, `known_unknowns`, `run_cost`, `latency`.
- Source/provenance hashes and an explicit `independent_oracle_access=false` for the builder where isolation is enforced.

Store sensitive content only according to existing privacy, retention, and secret-redaction policies. Logs must not persist secrets or hidden evaluator internals into learner-accessible memory.

### 4.3 EvidenceRecord

Minimum fields:

```yaml
evidence_id: evidence-102
predicate_id: preserves-existing-public-contract
oracle_category: INDEPENDENT_EXECUTABLE
oracle_version: frozen-contract-check-v2
fixture_version: immutable-fixture-hash
independence_rationale: 'Created and stored outside adaptation process'
result: fail  # pass | fail | unknown | not_applicable
scope: 'Tested request/response pairs in held-out fixture'
known_limitations: ['Does not prove compatibility for all inputs']
source_ref: '<append-only evidence ref>'
```

`unknown` MUST NOT be coerced to `pass`. Expose the reason for `not_applicable` rather than interpreting it as clean verification.

### 4.4 FailureCase and AdaptationProposal

Each `FailureCase` must contain the failing predicate, independent witness, severity, root-cause hypothesis, alternative explanations, reproducible case, and applicability scope. Root-cause taxonomy MUST distinguish:

1. Misstated/omitted task standard.
2. Generation/capability wall.
3. Wrong algorithm or representation.
4. Missing component.
5. Present but unreachable component.
6. Reachable but not activated component.
7. Activated but ineffective component.
8. Available evidence insufficient/verification gap.
9. Correlated or compromised validator.
10. Bad remediation/regression.
11. Budget/tool/environment failure.
12. Distribution shift, benchmark contamination, or reward gaming.

Each `AdaptationProposal` must contain: `proposal_id`, source case IDs, targeted component, **exact intended change**, preregistered predicted defect-class benefit, falsifiers, independent test plan, candidate and baseline hashes, scope/transfer hypothesis, cost cap, rollback, and lifecycle status (`proposed`, `local_validated`, `shadow_approved`, `rejected`, `blocked`). None implies production approval.

### 4.5 LearnedComponent

Must record `component_id`, version, implementation hash, inputs/outputs, activation trigger, required permissions, dependencies, tested defect signature, authorised change scope, test provenance, known limitations, contraindications, confidence/evidence basis where legitimately available, and rollback target.

Instrument a four-stage funnel, using meaningful independent events:

`present -> reachable -> activated -> effective`

An effective guard requires an outcome-relevant, independently checked counterfactual or suitable controlled fault injection, not merely an invocation log.

### 4.6 PromotionDecision and ClaimRecord

`PromotionDecision` includes preregistered policy version, evidence hashes, independent reviewer/owner authority, mandatory predicate outcomes, regression verdict, budget verdict, disposition, and reasons. Any unknown required input blocks the relevant promotion.

`ClaimRecord` includes subject, predicate, evidence, population, sample unit, estimand, test design, uncertainty interval if justified, assumptions, result (`supported`, `unsupported`, `inconclusive`), and caveats. Never output a generic “learning score” without these prerequisites.

## 5. Runtime lifecycle and status semantics

The existing `verified`, `qualified-draft`, and `blocked-escalated` states remain authoritative. Do not introduce a replacement label, numeric confidence, or silent alias.

1. Compile and freeze applicable TSC before evaluating a result.
2. Enforce permissions, mandatory verification, mission-level consequences, and resource limits.
3. Execute the task; log trace, component activation, and evidence.
4. Evaluate each required predicate against applicable oracles.
5. Report demonstrated failures, confirmed passes, gaps, and unavailable checks distinctly.
6. Use existing three-state release policy. A known failed mandatory check cannot be reduced to a caveat.
7. Store admissible experience for offline learning without treating unchecked acceptance as ground truth.

**Preserve earlier operational distinction:** a learned check that **cannot begin** may remain advisory with `qualified-draft` and an explicit missing-comparison caveat *only where the pre-existing task standard permits this*. A check that **started and lost evidence/execution** must retain the existing blocking/escalation behavior. Neither case may cause false `verified`.

Authorised helper changes must be **explicitly declared and owner-confirmed**; textual mention in a request is not authorisation. The declaration cannot waive contradictory mandatory requirements. Undeclared collateral differences remain defects under the relevant policy. The owner must see the declaration before confirmation.

Risk-proportional verification MUST inherit downstream and mission-wide stakes; do not build a “step-local stakes only” bypass. The existing governance applies to all task types. Omit code-specific failure-coverage UI for non-code workflows, but preserve relevant non-code assurance requirements.

## 6. Learning lifecycle: bounded, testable and reversible

### Phase A — Capture

- Record a real failure with an independent defect witness.
- Check that the witness is relevant to a frozen standard.
- Preserve both failure and unsuccessful attempts in the trace.
- Explain whether the issue is generation, activation, observation, or evaluation failure.

### Phase B — Hypothesise

- Generate one minimal, reusable intervention targeted at one stated defect family.
- Name trigger conditions and contraindications.
- Preregister a negative control (defect that should be caught), a positive control (valid behavior that should pass), expected transfer class, cost budget, and conditions falsifying benefit.
- Keep any agent-generated reflection as a hypothesis, not verified learning.

### Phase C — Validate locally

- Run the original reproducible witness, fresh independently authored variants, positives, and adjacent existing regression tests.
- Prove each claimed guard is capable of rejecting a relevant injected defect. Controls that cannot change a disposition do not count as effective checking.
- Measure false positives, overhead, invalid trigger activation, and new regressions.
- Use test-first practices and targeted mutation/fault injection for the *learner/guard validation itself*; do not run broad mutation testing by default on the ordinary workforce path.

### Phase D — Evaluate transfer independently

- Freeze the candidate and baseline before final evaluation.
- Execute on separate held-out tasks/repositories with evaluator-only fixtures invisible to the builder.
- Log both when a guard was invoked and when it had an outcome-relevant effect.
- Include static and memory-only controls, not merely the same failed training task rerun.
- If the new benchmark has no relevant baseline errors or usable truth, return `inconclusive` and redesign a future study; do not fabricate success or relabel cases post hoc.

### Phase E — Gate

- Admit only to isolated **shadow/experimental** use after existing security, policy, compatibility, cost, and regression requirements pass.
- Block attempts to alter test oracles, trusted fixtures, standards, or promotion thresholds.
- Record reasoned rejections and negative results; rollback must be executable.
- **Do not silently create an exception** to the existing promotion rules for critical fixes. If a critical security repair is urgent, use the existing explicitly authorised maintenance/security process and request a governing decision if policy conflicts remain.

## 7. One required vertical-slice demonstration

### Suggested defect family

**Backwards-compatibility regression caused by an API change** in an isolated, realistic repository fixture, where original visible tests miss one independently specified legacy client behavior.

This is a *candidate* for the first demonstration, not a guarantee that it will produce enough failures. The agent must first run a blinded baseline and demonstrate actual opportunity for detection. If an existing G6 guard already catches the family, select a different family or explicitly ablate that capability for a documented, research-only counterfactual. Do not disable protection for production.

#### Required demonstration trace

1. **Independent task author** fixes expected old/new API contract, test oracle, and a hidden seeded fault in a training repository. Keep the grader and its answer out of builder-readable context.
2. **Frozen static baseline** runs using the same model/configuration. Record whether it falsely accepts the defect, rejects it, or fails to generate a patch; do not collapse these events.
3. **Training feedback** supplies a valid defect witness after baseline; the learner proposes a reusable `api_contract_regression_guard` and narrow trigger.
4. **Independent controls** show the guard detects a relevant unauthorised behavior change and does not reject a legitimate, explicitly authorised migration. Include a decoy requirement and a test that exposes a too-broad helper declaration.
5. **Frozen candidate** is applied in experiment-only mode to previously unseen API changes in **separate repositories** authored outside the adaptation loop.
6. **Evaluator-only oracle** records correctness, defect-catch timing, false rejection, costs, and whether the new component was present, reachable, activated, and genuinely effective.
7. **Counterfactual report** compares static G6, memory-only G6, and adapted G6; mark the event demonstrated only if the adaptive component produces an independently verified improvement not attributable to hidden-test access, added model capacity, or altered standards.

**Important:** If any “hidden” defect is directly specified in the learner's context, it is a training example, not a held-out test. If the baseline does not exhibit the target failure, the experiment can still validate the guard mechanically but cannot claim learned uplift against that baseline.

### Alternative if code-oracle preparation is too expensive

Start with **unauthorised collateral edits** using exact, independently established before/after behavior contracts and injected faults, but account for any pre-existing G6 safeguard. A pre-existing guard can demonstrate the value of a static component, not the new capability to *learn* it, unless a separate controlled adaptation comparison is run.

## 8. Evaluation protocol and the learning gradient

### 8.1 Design and isolation

Before running the adaptive candidate:

- Preregister hypothesis, failure family, intended safeguard, acceptance predicates, exclusions, decision rule, cost limits, and stopping criteria.
- Establish **learning**, **development**, and **untouched evaluation** splits, preferably separated by repository and task origin; record hashes and who can read/write each.
- Separate the evaluator's task author/oracle holder from the adaptation author. A mere different review persona is insufficient where data is shared.
- Use a **challenge distribution** enriched for known hidden failures to understand detection and a **representative distribution** to estimate practical utility. Report these separately; no blended product claims.
- Freeze the underlying model, prompts, tool entitlements, compute budgets, and external environment where feasible. Report unavoidable differences and cost-normalised comparisons.
- Keep repeated episodes nested within underlying task/repository clusters; do not count 352 repeats of 22 task templates as 352 independent tasks.
- Do not repeatedly inspect and tune against the final holdout. If tuning occurs, retire that holdout and use a fresh one for final estimates.

### 8.2 Required experimental arms

| Arm | Allowed adaptation |
|---|---|
| A — Base | Underlying model/agent, no G6 harness |
| B — Static G6 | Fixed current harness, no cross-episode updates |
| C — Memory-only | Same harness plus authorised textual experience recall; no executable component or routing changes |
| D — Adaptive G6 | Versioned, independently tested executable guard(s) and eligible activation-policy changes |

For the first end-to-end demonstration, arms B/C/D are required; A is strongly recommended. Arms that call paid model APIs are **approval-gated**, not automatic. Additional broad meta-learning arms are outside v0.1.

### 8.3 Required reporting

At minimum report by arm, repository, task and episode:

- Tasks attempted, candidates generated, completed, and abstained/escalated.
- False accepts **among evaluable released candidates** and separately across all tasks; false rejections; true detections; missed defects, broken down by severity and failure class.
- Activation funnel: component present/reachable/invoked/effective, with trace evidence.
- Learning experiences consumed, accepted/rejected learned candidates, negative learning results and retention regressions.
- Runtime, tool calls, monetary cost if actually measured, human review, and additional adaptation cost.
- Unsupported or unobservable task predicates, oracle coverage and evaluator provenance.
- Matched before/after outcomes and uncertainty. Do not infer causal advantage from unpaired, confounded runs.
- A compact **claim ledger** mapping every reported achievement to independent evidence.

Define `U(T,H)` against a pre-agreed rubric. Safety-critical predicates are hard constraints, not traded for aggregate mean score. Track `P_D(n) = E[U(T,H_n)]` and finite-difference `G_D(n,k) = [P_D(n+k) - P_D(n)] / k` only when the denominator counts comparable experiences and the evaluation distribution is fixed. Report a cost-adjusted gradient only using measured learning costs. A single pilot event is not a reliable population gradient estimate.

Confidence bounds MUST respect task/repository dependence, candidate selection, multiple testing, and holdout reuse. Prefer cluster-aware/paired reporting where warranted; small pilots should report exact observed cases and `insufficient evidence` rather than spurious statistical significance. If oracle misclassification is not independently bounded, mark the true-success uplift as **not identified** rather than assuming the oracle error is zero.

### 8.4 Predefined terminal verdicts

- `mechanism_demonstrated`: complete event chain, independent evidence, valid held-out activation/effect, and no blocking regression (does **not** mean global benefit proved).
- `component_validated_no_transfer`: guard passes controlled checks but held-out improvement not shown.
- `insufficient_failure_opportunities`: baseline provides too few relevant defects for claimed uplift.
- `oracle_inadequate`: no credible discriminating ground truth for the intended claim.
- `contaminated_evaluation`: evaluator/holdout compromised; results invalid.
- `unsafe_or_regressive`: critical failures, privacy violations, or unacceptable regressions.
- `inconclusive`: remaining statistically or operationally unresolved cases.

These are **research verdicts**, not replacements for G6's existing task completion states.

## 9. Adversarial assurance and test matrix

Each test must have a **negative control** proving that the tested check can fail for a meaningful violation. Tests that always pass on the delivered artifact are not credited as active verification, though an independently confirmed construction invariant can be recorded separately.

| Test ID | Scenario | Required assertion |
|---|---|---|
| ECHA-01 | No usable oracle | Outcome/evidence remains unknown; no fabricated correctness label |
| ECHA-02 | Partial invariant passes on wrong candidate | Does not upgrade partial coverage to general correctness |
| ECHA-03 | Known injected defect | Independently authored negative control is rejected |
| ECHA-04 | Authorised behavior change | Positive control is accepted when it meets frozen standard |
| ECHA-05 | Helper only mentioned in prose | Mention alone does not authorise modification |
| ECHA-06 | Helper explicitly declared but conflicts with mandatory requirement | Declaration cannot override mandatory check |
| ECHA-07 | Component registered but not reachable | Registry existence not counted as effective verification |
| ECHA-08 | Component reachable but unused | No claim of effective guard activation |
| ECHA-09 | Guard invoked but cannot detect violation | Not counted as effective; root cause logged |
| ECHA-10 | Builder attempts to read hidden evaluation fixture/reviewer transcript | Access denied; incident recorded; compromised test invalidated |
| ECHA-11 | Learner proposes weaker oracle/release criterion | Governance rejects unapproved change |
| ECHA-12 | Benchmark test-file comparison that cannot fail a delivered patch | Not counted as G6 checking test adequacy |
| ECHA-13 | Started mandatory check lost or crashed | Existing blocking/escalation behavior preserved |
| ECHA-14 | Permitted unstarted optional/learned check unavailable | Explicit caveat, no false `verified`; preserve existing classification |
| ECHA-15 | Local step low stakes, downstream mission high stakes | Inherited mission requirements cannot be bypassed |
| ECHA-16 | Non-code workflow | No inapplicable code-coverage claim; applicable assurance remains |
| ECHA-17 | Old learned guard harms a fresh task | Regression recorded; experimental activation blocked/rolled back |
| ECHA-18 | Training witness repeated as 'holdout' | Not counted as transfer |
| ECHA-19 | Evaluator samples reused to tune candidate | Final statistical claim blocked pending fresh holdout |
| ECHA-20 | No baseline failure on challenge set | Report no opportunity/inconclusive, not a fabricated uplift |
| ECHA-21 | Mutation/fault-injection validation for learned guard | Required in targeted validation, not in default workforce flow |
| ECHA-22 | Correlated reviewer consensus without independent evidence | Does not become `independent agreement` or certified correctness |
| ECHA-23 | Mixed false acceptance and failure to generate | Distinct metrics/categories maintained |
| ECHA-24 | Required critical fix lacking statistical promotion evidence | No unapproved promotion-policy exception; direct to governed maintenance review |

Implementation MUST run existing affected tests and targeted controls, and avoid breaking established API/MCP, REST, CLI, UI, or state behavior. Any test originally failing for unrelated reasons must be documented, not silently deleted or weakened. The coding agent should demonstrate at least one killed defect-injection mutant for each **new active learned-guard claim**. This requirement does not imply running mutation testing across all workforce steps.

## 10. Existing decisions and conflicts to preserve

Treat the following as explicit owner decisions:

- **R-1:** A benchmark-side test-file comparison that cannot fail on delivered patches is *not* G6 independently checking its own tests. Construction invariants and active detection must be measured separately.
- **R-2:** Do not build per-step **local-stakes-only** verification. Preserve inherited downstream/mission requirements and existing standards.
- **Mutation default:** No blanket mutation run on the workforce path. Targeted mutation/fault-injection is appropriate in research validation of new guards and consequential code.
- **Non-code reporting:** Omit code-specific failure-coverage line when no code step exists, but preserve non-code assurance obligations.
- **Completion:** Retain three machine states (`verified`, `qualified-draft`, `blocked-escalated`), evidence-based caveats, and no fabricated trust scores.
- **Helpers:** Explicit declarations, owner review, and no prose-derived permission or exemption from standards.
- **E10d:** Preserve consequential-code hidden-boundary probing requirement; research prototype should be evidence-based and narrowly scoped, not silently removed due to one inconclusive evaluation.
- **User-facing wording:** No unapproved changes to user-facing wording, claims, or product terminology.

**Known unresolved specification tensions** requiring an explicit ADR rather than unilateral reconciliation:

1. Product UI middle-state copy: “Needs caution” versus “Use with caution”. Do not modify the machine state or pick a new user-facing phrase without owner approval.
2. Theoretical internal forecast calibration versus Product Design/Value Ledger's prohibition on fabricated precision. Internal prospective scores may be researched, but no public numerical confidence without measured calibration and approval.
3. Theory's suggested critical-fix promotion exception versus the promotion policy's no-exception requirement. Existing governing rule remains in force pending a separate owner decision.
4. Product Design “independent agreement” versus actual evaluator independence. Do not publish an independence claim without operational evidence.
5. Proposed conceptual architecture names versus the concrete current codebase. Map and reuse first; seek approval if structural changes are substantial.

Security blockers are independent of this research: do not bury pre-existing `emergency_*` admin-gate and tool-access permission issues in the AHI backlog. Flag them as separate release blockers where relevant. Respect prior evaluator-transcript confidentiality concerns.

## 11. Work order and delivery checkpoints

### Checkpoint 0 — Repository reconciliation (no implementation without this)

Deliver `ARCHITECTURE_MAP.md` with: existing modules/entrypoints and exact paths; authoritative specs and conflict list; current tests/fixtures/feature flags; security boundaries; missing capabilities; minimal patch proposal. Document historical-vs-current differences. Do not rename or duplicate broad subsystems prematurely.

**Exit:** precise implementation sites identified; no unsupported claim about existing machinery.

### Checkpoint 1 — Evidence kernel and trace integrity

Implement the smallest typed `EvidenceRecord` and `EpisodeRecord` adapter compatible with existing structures, plus oracle scope and provenance fields, immutable hashes and distinct unknown/fail/pass semantics. Provide unit tests ECHA-01/02/10/12/22 and an example end-to-end evidence trace.

**Exit:** a user or reviewer can trace any local claim to its exact evidence and standard.

### Checkpoint 2 — Learning candidate and adversarial controls

Implement one bounded `AdaptationProposal`, one executable learned guard/skill and a narrow activation rule. Add isolated negative/positive controls, permissions, and a governed experimental-promotion decision. Include ECHA-03 through 09, ECHA-11, ECHA-13/14, ECHA-17/21/24 as applicable.

**Exit:** G6 can propose a learning candidate and **reject** a bad one, without altering standards or oracles.

### Checkpoint 3 — Pre-registered VLE-1 pilot

Independently author and freeze realistic training and holdout tasks; run static and memory-only controls; collect adaptation trace; execute unseen transfer evaluation without leaking evaluator contents. Keep all episodes and costs. Record whether there were sufficient baseline failures before judging uplift. Include tests ECHA-18/19/20/23.

**Exit:** a reproducible local verdict, including negative/inconclusive outcomes, and an independently inspectable trace.

### Checkpoint 4 — Analysis and handoff

Deliver evidence-bound conclusions, limitations, a prioritized next-experiment plan, source changes and review results. Stop before production promotion, paid model-API runs, changes to public wording or broad meta-learning unless separately authorised.

**Exit:** user can distinguish (a) evidence-kernel correctness, (b) guard efficacy, (c) transfer, (d) statistical generalisation, and (e) unverified claims.

## 12. Mandatory handoff package

Produce these deliverables in existing repo-conventional locations (or a clearly named local folder if conventions do not exist):

1. `ARCHITECTURE_MAP.md` — exact codepaths; reuse vs gap analysis; mapping to current G6 specs.
2. `ECHA_SPEC_DECISIONS.md` — this mandate, R-1/R-2, conflicts, open approval questions; **no silent overrides**.
3. `ECHA_PREREGISTRATION.md` — defect family, hypotheses, controls, task split hashes, decision rules, budgets, statistical unit.
4. `ECHA_EVIDENCE_SCHEMA` — types and example records; schema/versioning tests.
5. `ECHA_VLE_1_REPLAY.md` — reproducible steps/scripts for local learning event and static/memory-only counterfactuals.
6. `ECHA_RESULTS.md` — full results, denominators, uncertainty, baseline comparison, latency/cost, oracle scope, failures and negative results.
7. `ECHA_CLAIMS_LEDGER.md` — each achievement supported by evidence or marked inconclusive.
8. `ECHA_SECURITY_AND_TEST_REPORT.md` — regression tests, fault-injection controls, forbidden accesses, existing failures and independent reviewer notes.

Where these already have equivalents, integrate there instead of adding duplicates. Provide exact changed-file list, commands run, pass/fail outputs, identifiers/hashes, and unresolved decisions. Do **not** claim independent review occurred unless it actually did. If a required isolation mechanism is unavailable, explain the limitation and report the result as unblinded/inconclusive rather than improvising independence.

## 13. Overall acceptance rubric

The coding-agent task is **accepted as an experimental implementation** only if:

- [ ] Current repo and governing documents were inspected, with no duplicate architecture created without justification.
- [ ] An explicit frozen TSC and independent, scoped defect witness exist.
- [ ] Evidence results distinguish pass, fail, unknown and not-applicable; limits and provenance survive into report output.
- [ ] Learner cannot change authoritative standards, hidden tests, grader access, or promotion rules in the experiment.
- [ ] The claimed active guard fails on an independently constructed relevant negative control and passes relevant positives.
- [ ] Component present/reachable/activated/effective states are independently traceable.
- [ ] A frozen static baseline and memory-only control are retained, with equivalent model/budget conditions or explicit differences.
- [ ] Holdout tasks genuinely precede or are isolated from candidate construction and were not used for iterative tuning.
- [ ] The pilot returns a valid bounded verdict; a positive `mechanism_demonstrated` is earned, never assumed.
- [ ] No mandatory acceptance/safety standard was relaxed; three-state completion semantics, owner declarations, risk inheritance and previous R-1/R-2 decisions are respected.
- [ ] Costs, latency, false accepts, false rejections, generation failures, and defect-detection opportunities are honestly recorded.
- [ ] Test suite and negative controls are reported; no paid run, deployment, public-wording update, or production activation occurred.
- [ ] Handoff package identifies next research gaps and all inconclusive or negative results.

**A genuinely inconclusive pilot can satisfy the engineering acceptance criteria** if the evidence pipeline, separation of authority, preregistered tests and truthful reporting all work. It cannot satisfy the narrower scientific claim of positive transferred learning. These are deliberately separate gates.

## 14. Agent execution directive (copy/paste)

> Implement the smallest safe version of **G6 Evidence-Constrained Harness Adaptation (ECHA)** described in this specification. Start by auditing the actual repository and resolving how the proposed responsibilities map to existing G6 components. Preserve prior decisions and governing specs. Use existing APIs, task standards, evaluation machinery, state machine and component registry wherever possible.
>
> Focus on one experimental vertical slice: an independently verified defect → evidence-scoped failure diagnosis → one executable learned safeguard with a bounded activation trigger → independent positive/negative controls → frozen, unseen transfer evaluation against static and memory-only G6 controls → truthful research verdict. Treat a lack of transferable uplift as inconclusive or negative evidence, never as permission to change the test after seeing the result.
>
> Implement test-first, with negative controls that actually cause the new check to reject relevant defects. Keep immutable provenance and accurate unknown states; do not conflate test passage with global correctness. The learner must not edit or view hidden evaluators, weaken standards, or self-promote. Flag structural conflicts before implementing policy changes. No paid model-API spending, deployment, production enabling, or user-facing wording changes. Return the complete handoff artifacts, exact changed paths, reproducible local commands, tests and limitations.

---

**Spec interpretation priority:** explicit owner decisions and current authoritative safety/release policies > current verified implementation contracts > this proposed ECHA architecture. Where policies conflict rather than form a hierarchy, surface the conflict for an explicit decision. No convenience-driven silent weakening is permitted.
