# Integrated experimental protocol — proposal, not executed

## Design and comparator structure

There are **three independent estimands**, with distinct experiments:

**Static composition effect**: improvement from typed/evidence-aware mission graphs at the same model and tools, measured against a direct response and against an outside agent invoking the tools itself. Use the *already preregistered* five-arm mission comparison procedure; do not retroactively change the task set, endpoint or significance threshold. Its current planned model is `gpt-oss:20b`; the experiment is built but has not yet been run on a model.

**Evidence-bound reliability effect**: change in false `verified`/false acceptance under independently established task standards when a typed evidence policy prevents the system from substituting model consensus, process completion or authored tests for ground truth. Count true coverage and refused/inconclusive tasks, not just false acceptance among released candidates. In real medico-legal tasks, one wrong clinical claim may require expert adjudication and cannot be labelled “passed” by another LLM.

**Adaptive learning effect**: comparison of a frozen static harness with the same harness plus textual memory, a frozen executable learned skill/guard, and its structural learned router under independent held-out cases. The skill's author has no evaluator authority, and an 'always-on skill' arm distinguishes routing intelligence from owning the skill.

## Dataset construction

Use ordinary software patches and synthetic medico-legal / engineering missions for the first controlled tests. Training/dev/final evaluations must be disjoint by repository/author/family where practical. If independently verified real examples are lacking, generate *synthetic* seeded claims whose correct answers are determined by an external simulator, formally specified predicate or hidden tested fixture. Report synthetic vs historical vs real outcomes separately. Never mix user patient records or confidential legal documents into the study.

## Task standard and oracle hierarchy

Freeze mission predicates *before* the agent sees the task. Associate each predicate with an independently chosen applicable test or declare it unknown. Record a valid negative control (the check must reject a material violation); also an authorised positive control. Formal proof requires a real kernel checker on a specific statement; independent executable checks have strictly scoped coverage; partial falsifiers can reject a candidate but cannot certify arbitrary correctness; heuristic reviewers are leads and cannot become independent by being numerous.

## Per-task recorded data

Identifiers: task, family, source, task-standard hash, model/backend/version, oracle version, candidate version, trace ID, vault source; choice stages: candidates, feasibility, authorised dependencies, all cost estimates, planned/selected/invoked components, evidence received, refusals; outcomes: generated answer, accepted/rejected/abstained, true or oracle-scoped success, detected defect, false accept, false rejection, unsupported claim, scope of proof, unverified claims, near-miss safety violations. Resource data: wall clock, CPU/GPU where measured, tokens, monetary charge, review time, retries and extra interventions. Unknown values remain unknown.

## Hypotheses and prespecified falsifiers

H1 typed plans prevent at least one declared compositional defect (effect bypass, type mismatch, unbounded cost, missing reviewer) not prevented by an untyped baseline at the same resources. Falsified by a valid adversarial plan admitted to release or by baseline achieving identical detection with less complexity.

H2 the evidence type system lowers unsupported `verified` claims on evaluation cases with independent labels. Falsified by persistent false promotion or greater false negatives that swamp practical value at budget cap.

H3 explicit structural diagnosis improves selection on held-out task families. Falsified by no difference against a competent baseline after matched costs and equal information.

H4 governed adaptive capability acquisition transfers to unseen families and remains better than memory-only or always-on after matched model and tool budget. Falsified by absence of independent baseline errors, holdout contamination, or no meaningful incremental effect.

## Independence and statistics

Task or repository is the sampling unit. Repeated generations are repeated measures inside one cluster. Freeze experiment hashes and holdout after development, use paired comparisons and cluster-aware uncertainty. The threshold for early pilot feasibility is not interchangeable with the threshold for a population-level improvement claim. If data are scarce, publish case-level results and declare uncertainty rather than a p-value. NIST has documented evaluation-gaming failure modes; actively inspect traces for shortcutting and grader influence. Correctness unidentifiable under unknown oracle error remains explicitly unidentifiable.

## Stop conditions and permissions

No paid API use, production changes, broader ECHA Stage B, real clinical/regulated deployment or unapproved self-promotion is authorised by this research protocol. Existing owner approvals and current product authority must be respected. Any finding that the executor can mutate a frozen evaluator or reclassify self-authored tests as independent is a hard research blocker, not a 'warning' that can be traded against a higher average score.
