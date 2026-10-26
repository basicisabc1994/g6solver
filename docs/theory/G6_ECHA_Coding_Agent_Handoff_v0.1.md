# G6 ECHA — coding-agent handoff

Read and implement the companion specification `G6_Evidence_Constrained_Harness_Adaptation_Spec_v0.1.md` as a research-only, feature-flagged extension to existing G6.

**Primary objective:** Demonstrate a complete and independently assessable learning event: established defect → failure diagnosis → bounded executable guard → negative/positive controls → held-out unseen transfer → comparison with frozen static and memory-only harnesses. Passing tests alone does not establish general correctness or transfer.

**Start by mapping the repository.** The previous report described an earlier run; confirm current state, source-of-truth documents, paths, and conflicts. Do not duplicate existing components or assume the theory's module names correspond to code.

**Security and scientific constraints:** The learner cannot read/change held-out graders, standards, promotion thresholds, reviewer transcripts, or trusted fixtures. Every claim carries evidence scope and provenance; unknown must remain unknown. Do not count a benchmark comparison that cannot fail on a delivered patch as active G6 test verification. Do not implement per-step local-stakes-only verification. Inherit downstream/mission standards. No default workforce mutation pass, no inapplicable code-coverage display for non-code tasks, and no user-facing numeric trust scores. Preserve existing three-state completion and approved helper-declaration semantics.

**First deliverables:** repository architecture map; standards/conflict ADR; preregistered task and baseline protocol; minimal evidence schema; one executable learned-guard candidate with negative and positive controls; independent held-out test plan; reproducible analysis with failure denominators, overhead and genuinely unknown outcomes. Research verdicts may be positive, negative or inconclusive.

**Stop conditions:** Any requested paid evaluation, deployment, product wording, policy exception, privileged evaluator access, or production activation requires owner approval. Do not invent missing evidence or claim the mechanism works before the independent evaluation supports it.
