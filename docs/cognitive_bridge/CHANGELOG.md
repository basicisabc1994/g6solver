# Changelog

## v0.2.1 — 2026-11-05: joined to G6's core harness

- Registered with every surface that lists components: a typed block with a `help` op, a skill, the site's
  catalogue and FAQ, the extras table (`g6[ml]` for `xai`), the import-linter layer contract and the generated
  catalog. The four scikit-learn tests skip where `g6[ml]` is not installed.
- Stable codes in `codes.py`, each carried in `code` and in a `warning_card`, pinned by
  `tests/cognitive_bridge/test_block_refusal_codes.py`. An assessment carries `completion_state`, `warning_card`,
  `evidence`, `request_id` and `run_id`, and no second state field.
- `risk` is named as the number of G6's `risk_tier`; the bridge's own nouns are defined in TECHNICAL_SPEC.md.
- The executor runs each step through G6's checked door (`g6.core.door.invoke_checked`), so a sensitive op needs the
  person's allow and a component's own `verified` is held at `qualified-draft`.
- One rule per fact: the adaptation gate's sign test is `g6.reasoning.sign_test_p` (the meta-harness gate's), and the
  conflict gate reads stance and lineage by `g6.epistemic_integrity`'s rules.
- `IndependentAssuranceEvaluator` is renamed `InProcessAssuranceEvaluator`: it runs in this process and attests no
  isolation.

## v0.2 — 2026-11-05

- Added contextual metacognitive method selection across representation × G6 engine/operation, with outside-evaluator admission of historical trials, duplicate-case filtering, conservative risk-based exploration, and a budget-feasible mandatory-method portfolio.
- Extended G6 method routing with safe allowlisted operation variants: formal `check_propositional`, predictive `rank`, graph `query` and optimisation `solve_mip`, alongside the v0.1 defaults. The existing mixed-method executor still re-derives the canonical route to reject tampering.
- Added LLM metacognitive critical reasoning proposals with explicitly falsifiable assumptions and evidence requirements, never a verifier.
- Added scope/standard/edition/jurisdiction/pinned artifact model; positive/negative control replay; strictly scoped candidate assurance dossiers; risk-based external reviewer authentication hooks; no automatic promotion.
- Added cross-method epistemic contradiction detection, external assertion admission, and source-dependence collapse.
- Added compositional failure modes and a statistical held-out adaptation gate with protected evaluator paths, no critical regressions, and a recommendation limited to isolated shadow trials.
- Added orchestrator for the selected variant model → canonical G6 plan → approved execution → evaluator-owned evidence package.
- Added a finite Boolean truth-table worked example and illustrative candidate dossier.
- Retained all v0.1 cognitive and XAI features. No modified G6 core files and no GitHub commit.

**Evidence status:** 62 standalone tests passed in the supplied environment using G6 API shims; no production G6 integration or external trust-boundary certification. See `docs/INTEGRATION_GATES.md`.
