# G6 Metacognitive Assurance Engine

**Technical specification — v0.2 reference prototype**  
**Date:** 2026-11-05  
**Status:** Draft architectural specification plus locally tested reference code; no production security assurance.

## 1. Scope, novelty and goals

A universal learning system needs an interface by which quantitative optimisers, heuristics, symbolic solvers, causal models, graph systems and LLM-based research strategies may be swapped without changing the surrounding task contract. G6's differentiator is not the presence of these solvers. It is a **compilation-and-assurance boundary** allowing their output to be independently checked against the task's *actual* acceptance obligations.

Novelty **hypothesis** (not an experimentally established claim): context-dependent choice of representation and computation, constrained by independently verified epistemic obligations, can outperform fixed reasoning pipelines on reliable progress per unit resource, without allowing the learned selector to weaken the standard that judges it. This remains to be validated against appropriate baselines.

The design has five separately accountable roles:

1. **Task owner / domain professional** — defines what correct work means, governing standards, success criteria, material risks and accountable decisions.
2. **Candidate/generator** — proposes language-to-model translations, hypotheses, reasoning steps, research probes, algorithms and code changes. Everything it writes is untrusted.
3. **Metacognitive selector** — proposes method/representation choices from validated historical measurements and explicit costs. It cannot grant trust, change obligations or act on the environment.
4. **Evaluator** — independently reads artifact bytes, owns frozen checks and expected outcomes, runs positive/negative controls, evaluates the submitted result and records discrepancies.
5. **Promotion authority** — independently replays and authenticates the evidence, considers professional/risk requirements and authorises shadow, deployment, release or an appropriate `verified` label. In current G6, this is subject to its own door promotion law and owner review; **not** supplied by this extension.

## 2. Formal objects

Define a task as

\[
\mathcal{T}=(I,G,S,J,R,B),
\]

where \(I\) is the user's intended outcome, \(G\) is the reviewable claim/constraint graph, \(S\) is an authoritative versioned set of standards, \(J\) is jurisdiction, \(R\) is material risk, and \(B\) is a computational/financial budget.

The candidate proposes an intermediate representation \(M\) and transformation \(\phi: I\rightarrow M\). **The fidelity of \(\phi\) is an additional obligation, not implied by proving anything about \(M\).**

Each method alternative is a tuple

\[
a=(\rho, e, o, p, c),
\]

where \(\rho\) is the representation (e.g. causal DAG, SMT, annotated graph, tabular data), \(e\) is the approved G6 component, \(o\) its allowed operation, \(p\) is a well-typed declarative payload, and \(c\) is an estimated resource cost. No `eval`, arbitrary tool path, code import or LLM-selected instruction ever becomes an executable operation.

A method's output is an **artifact** with a content digest \(h_A\), declared provenance, assumptions, and a bounded claim about what its evidence can establish. A criterion \(s_i\in S\) names a verifier \(v_i\) and expected evidence type. The evaluator-owned verifier runs against the original bytes and against planted wrong examples.

An evaluated episode \(z\) contains the task signature, chosen method, evaluator-owned split/cohort, outcome and cost, plus authenticated identity of the independent oracle. The selector cannot learn from self-graded episodes; without an admission authority its history is empty.

## 3. Metacognitive policy

### 3.1 Context and inference

A bounded context is \(x=(d,u,k,r,b,E)\): domain, uncertainty kind, claim type, risk, budget and available evidence. Training observations are matched by \((d,u,k,a)\); unrelated domain outcomes do not silently count toward success in the new domain. The approach intentionally avoids semantic embedding generalisation as evidence of cross-domain reliability.

For a particular representation/engine variant \(a\), with independently evaluated resolutions \(w\) across \(n\) matched episodes, the reference selector computes

\[
\hat p_a=\frac{w+1}{n+2},\qquad
U_a=\sqrt{\frac{2\ln(2+N)}{n+1}},
\]

where \(N\) counts evaluator-admitted historical episodes, not generated transcripts. This is a **heuristic Bayesian-smoothed proxy** and UCB-like uncertainty bonus, not a calibrated probability bound for real-world truth. A ranking score uses

\[
\mathrm{score}(a\mid x)=
\frac{\hat p_a+\lambda U_a\mathbf 1[r<2]-\mathrm{criticalRegressionRate}_a}
{1+\widehat{\mathrm{cost}}_a}.
\]

All input costs must be finite and nonnegative. Alternative representations can use independent prior models or different allowed G6 operations. The same task should be run with paired alternative methods on a preregistered representative sample to estimate comparative gains; observational method allocation is confounded, and the selector openly discloses this.

**Critical constraint:** standards identify a set of required capabilities \(C_S\). The selector seeks a budget-feasible portfolio covering all of \(C_S\); optional alternatives cannot satisfy a different obligation merely because their score is higher. If budget, required typed model or capability is missing, the plan is blocked. Up to five obligations and bounded alternatives are searched for a viable portfolio.

### 3.2 Exploration and difficult cases

The system may explore alternatives on low-risk tasks, within a hard cost limit and frozen evaluation. For R2/R3, it disables the exploration bonus: no speculative method may displace a mandatory professional verifier. Unknown or unsupported capability => refuse, not pretend the LLM understands how to execute it.

The selector is **not a judge**. Historical reward is downstream of the evaluator's measured resolution of a frozen criterion. Pure novelty and LLM confidence must never count as positive validation. Penalties should include regressions, cost overruns, failed negative controls, source/standard mismatch, distribution shift, unsupported claims, and refusal to reproduce on independently held-out cases.

### 3.3 Self-improvement as an experiment

The adaptive method selector itself is subject to paired trials against a frozen baseline: same cases, same resources, blind scoring, planned sample size, multiplicity adjustment, stratification by difficulty/domain and risk, negative controls. Promote only on an external recommendation; retain both successes and failures in the public evidence trail where privacy/licensing permit. No training on frozen holdout answers.

## 4. Metacognitive primitives

The LLM-side critic proposes bounded JSON objects for these functions:

| Primitive | Candidate output | Independent adjudication obligation |
|---|---|---|
| Premortem | Anticipated failure modes and scope | Planted failures / execution traces that make each mode observable |
| Blind-spot detection | Unexamined assumptions and missing evidence | Independent review against domain acceptance profile |
| Critical challenge | Alternative explanation and falsifying question | Distinguishing observation or countermodel |
| Representation shift | Proposed graph, causal DAG, formal model or quantitative model | Typed parse and semantic-faithfulness check against user intent |
| Epistemic calibration | Candidate confidence reason | Reference outcomes, calibration plots and withheld cases; self-rating excluded |
| Theory revision | New hypothesis or guard candidate | Freeze revision; paired replay of previous failures and controls |
| Resource metareasoning | Value-of-information and cost estimates | Compare expected gains with measured gains using external scores |
| Compositional diagnosis | Links failed criteria to possible root causes | Reproduce a failure, show a discriminating fix and falsify alternatives |

Critical distinction: observed failure \(\neq\) established root cause \(\neq\) demonstrated mitigation. The failure graph stores separately typed edges for each.

## 5. Evidence types and admissible inference

A method can only establish what its model and checker support:

| Method | Admissible inference | Unresolved by default |
|---|---|---|
| Graph reasoning | Rule derivation from declared facts and graph semantics | Factual validity of facts and edges; real-world causality |
| Causal identification | Identifiability conditional on given DAG and supported criterion; estimates from supplied data | Correctness of the DAG, all unmeasured confounders, causal transportability |
| Formal | Satisfiability, counterexample, scoped validity of the supplied formula | Whether formalisation is faithful, complete and legally/clinically correct |
| Predictive ML | Held-out discrimination/error on the sampled population | Cause, shift robustness, policy fairness, population validity |
| Optimisation | Feasibility and optimality when adequately certified for a given mathematical objective | Whether the objective represents the intended social/economic outcome |
| LLM critique | Candidate hypotheses and counterexamples | Any empirical or logical claim without external checks |

The G6 grades `model`, `instrument`, and `kernel` are about **method and provenance**, not the fluency of the answer or a model's word. The public promotion law requires G6's own trusted door to *re-run* the relevant independent check, bound to the artifact and caller's original criterion. This bridge produces only `qualified-draft` or `blocked-escalated`, even when every in-process check passed.

## 6. Candidate verification dossier contract

A candidate dossier contains:

- Format identifier, claim/artifact IDs, producer and evaluator identities.
- Normative standard: edition, jurisdiction, authority, source SHA-256, a per-criterion verifier and risk classification.
- Exact artifact bytes hashed individually, evidence source references, source provenance and intended claim scope.
- Frozen checker ID, version digest, positive/negative control digests and actual control results. A verifier throwing an exception on a planted wrong result does **not** count as detecting it.
- Every requirement, checked or not; failed/missing/unsupported checks as blockers. No vacuous success when a critical criterion is absent.
- Documentary sections: scope, methodology, standards, provenance, assumptions, uncertainties, counterexamples, reproduction instructions, exceptions.
- Explicit limitation of what each method and the combined result establish.
- For high-risk R2/R3 work, an independently authenticated professional attestation of the **exact** standard, artifact set, verifier manifest, documentary content and evaluator/producer identities.
- Aggregate metrics with definitions: declared critical obligations, critical passed, controls actually detected, advisory exceptions, missing sections. No invented universal confidence score.
- An unkeyed content digest for accidental-integrity checking; **not** a signature or a trust certificate.
- External promotion instructions and a statement that a G6 door must independently reproduce before minting `verified`.

A perfect completion cannot be asserted simply because the checklist was filled. Profile quality, domain standards, judgment competence, independence and custody are requirements of the surrounding professional process. Text such as “reviewed” is not an authenticated attestation.

**Documentary conformance rule:** even if every mathematical checker passes, a gap in the documentary sections or required professional review makes the dossier `blocked-escalated`.

**Absence of uncertainty is suspicious, not exemplary.** The record must describe excluded hypotheses, untested assumptions and limits on the work's conclusions.

## 7. Governed adaptation: monotonicity within evidence scope

Given a candidate change \(\Delta\) and frozen set \(D_{\mathrm{holdout}}\), define paired outcomes \(Y_i^0\) (baseline) and \(Y_i^1\) (adapted). Let

\[
W=\sum_i\mathbf1[Y_i^1=1\land Y_i^0=0],\quad
L=\sum_i\mathbf1[Y_i^1=0\land Y_i^0=1].
\]

A one-sided exact sign test computes

\[
p_{\mathrm{sign}}=2^{-(W+L)}\sum_{k=W}^{W+L}\binom{W+L}{k}.
\]

A preregistered conservative multiple-testing threshold is \(\alpha/m\), where \(m\) is the maximum number of adaptation candidates evaluated. The reference gate additionally requires minimum numbers of untouched pairs and disagreements, positive and negative controls, authentic evaluator receipts, bound code/suite digests, no overlap with development cases, a net positive advantage and **zero regression on critical held-out obligations**. Passing means at most *recommend isolated shadow*. Owner authorisation is still required.

This **does not** prove monotonic improvement in the deployment distribution. The guarantee is a *bounded nonregression observation on the frozen test set* plus an observed positive paired effect under specific assumptions. Maintain ongoing monitoring, canary cohorts, distribution-shift detection, recall/rollback, fresh tests and independent reviews after changes. Do not repeatedly inspect the same holdout until it stops being held out.

A proposed guard affecting a protected path (tests, frozen validators, verification, certificates, approvals, ledger, G6 core) must never be promoted through this extension. Core changes require the repository's existing certificate-reissuance process and human authorisation.

## 8. Threat model and limits

| Threat | Protection in reference code | Remaining required protection |
|---|---|---|
| LLM chooses arbitrary code/tool | Fixed operation allowlist and typed declarative payloads; executor recomputes canonical plan | G6's real execution sandbox and tool permission boundary |
| LLM scores its own work | History blocked without independent admission hook | Separate evaluator process with keyed authenticity and withheld gold data |
| Fabricated claim/causal model | Translation and model marked unreviewed | Expert source/semantic-faithfulness adjudication |
| Good-looking but fake results | Actual artifact bytes submitted to distinct checker, negative controls required | Independent custody and deployment of the executable oracle |
| Replacing an artifact | SHA-256 content digest | Authenticated publication and secure artifact store |
| Forged certificate | Candidate label only; never emits `verified` | G6 door replay, source-bound promotion witness, verified signatures |
| Weakening tests in an adaptation | Protected path denylist, controls, frozen suite | G6 adaptation gate and code/isolation review outside candidate scope |
| Selection biased toward easy cases | Mandatory obligations retained, trial matching, holdout admission, disclosed proxy score | Randomised paired evaluations, stratification, confidence calibration |
| Repeated experiments / p-hacking | Bonferroni max evaluations, external samples and split tracking | Preregistration, experiment tracking and prevention of repeated holdout peeking |
| “Formal proof” of wrong task | All model translations candidate and incomplete by default | Independent semantic-specification review / user intent binding |
| Claimed professional authority | Standard edition/jurisdiction, reviewer authentication hook | Actual verified regulations, credentials, authority, professional supervision |
| Hidden dependent errors | Failure-mode trace and compositional guard hypotheses | Causal root-cause evaluation and independent falsification |
| Supply chain / compromised evaluator | None in same-process Python prototype | Isolated evaluator, sealed binaries, reproducible build and secure key storage |

The `admit_trial`, `admit_pair`, `admit_control`, and `reviewer_authenticator` callables are **trust-boundary interfaces**, not built-in verification of the callers' trustworthiness. A malicious candidate can pass a permissive lambda in its own process. Production must make these capabilities inaccessible to candidate-generated code. The bundle tests are local model/unit tests; they do not prove that outside-process separation holds in a full G6 deployment.

## 9. Scientific and professional evaluation design

Primary north-star endpoint (proposed): **number of materially correct, independently validated acceptance obligations satisfied per fixed resource cost and wall-clock budget**, with strict safety/risk floors. Supplementary metrics: oracle false-accept rate, planted failure detection rate, task-coverage completeness, time to counterexample, unsupported claim rate, failure-mode recurrence, correction cost, and reproducibility on a separate machine.

A study should compare:

- Fixed LLM + prompt-only critique.
- Fixed G6 mixed-method routing without a learned selector.
- Meta-selector trained only on evaluator-admitted outcomes.
- Meta-selector plus LLM-proposed critical questions, but the same independent acceptors.
- Full compositional failure-mode adaptation in shadow, with the same resource limits.

All arms must share the same frozen task set and evaluator at evaluation time. Separate software domains, codebase scale, novelty categories, source constraints and task risk. Record all refusals, overruns, exclusions and versions. Select on a **development** split; evaluate once on unseen held-out tasks; for repeated releases use fresh untouched cohorts. Confidence intervals and power/sample planning belong to the preregistered protocol, not selected retrospectively after viewing outcomes.

## 10. Implementation status

Implemented: typed canonical routing and real alternative G6 operations; a context-specific conservative selector with outside-evaluator admission hook; bounded LLM critic; documentary/frozen-check candidate assurance; evidence-linked failure findings; guarded adaptation recommendations; a worked finite Boolean check with a negative control. **The actual outside authority has not been established.**

Not implemented: complete semantic parsing of natural language into the correct formal model, learning policy calibration against independent production datasets, universal cross-domain inference, authenticated evidence stores/signature validation, independent external evaluator process, G6's official certificate issuance, safe live autonomous activation. These are separate work packages; they must be measured before any production promise.
