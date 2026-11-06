# Changelog

## v0.2.6 — 2026-11-06: follow-up review 1 repairs

- Credit binds its subject. A dossier names the evidence it assessed by digest (`subject_sha256`, from `assurance.evidence_sha256`), and an assessment credits it only for the evidence submitted to that assessment; a reconciliation names its assertions the same way (`epistemic.assertions_sha256`) and is credited only for the assertions submitted, and only in the state its own findings give. A method selection is taken only when it is for the methods handed, is refused exactly when one is missing, meets each with one mandatory row, and each row is a variant given, as given (its cost included), eligible under the context given, with the rows together inside its budget.
- What a collaborator answers carries only its format's fields and claims no state (`codes.only_fields`): a dossier, a reconciliation report or a selection row with `reliability_label`, a `verified` flag outside a warning card, or a field its format does not define, is refused, so no assessment, compilation or plan republishes one. A dossier's state is the one its blockers give.
- An inventory test lists, from the code, every check a collaborator's answer goes through and plants each answer with those claims through the public call that takes it.

## v0.2.5 — 2026-11-06: review 4 repairs

- What a collaborator returns is checked where it is used: every collaborator (a model, proposer, selector, updater, predictor, tool, checker, evaluator, conflict gate, admission or review callback, black box) is handed only fresh copies (`snapshot.fresh`), and its answer is taken as a fresh copy the moment it returns and checked against what was checked (`snapshot.returned`) or taken as a verdict by identity (`is True`). The agent runs the validated candidate the selection names, refuses a posterior that breaks the rule priors follow, and uses beliefs only under the digest taken when they were checked. An inventory test lists every call of a collaborator from the code with what is done with its answer. Found by it: a black box could add held-out rows to the rows a surrogate is fitted on.
- `CognitiveAgent`'s history is written only from a tool step G6's door passed, each entry with that reply's `request_id`; a proposer is shown a copy, so nothing it writes enters the history.

## v0.2.4 — 2026-11-06: review 3 repairs

- Check-then-act removed at the root: one function (`g6.cognitive_bridge.snapshot`) takes a deep, immutable snapshot of what the bridge accepts; every record type snapshots its fields before its own checks; nothing handed back is a live view; an inventory test lists every store from the code. An assessment refuses a dossier whose standard is not the one compiled.
- `CognitiveAgent` runs each tool step through G6's checked door and credits it only when the door says `ok`; an inventory test allows no other call of a tool or step.
- The reserved fields include every field G6's door and envelope read as a state (`reliability_label`), taken from G6's own definitions.
- A path lookup error other than "no such file" refuses the path.
- Feature names are compared as the strings the tree reads them by; a collision is refused.

## v0.2.3 — 2026-11-06: review 2 repairs

- The adaptation gate admits a changed path only when it is one of the files the bridge declares adaptable (`adaptation.ADAPTABLE`), compared by file identity; every other path is refused, and any error resolving a path refuses it. The deny-list it replaces missed members in both reviews.
- `codes.refusal` and `codes.settled` write `completion_state`, `code` and `warning_card` themselves and refuse a caller's value for any of them or for `verified`; a supplied card must carry a registered code, so a step G6's door refused is `G6_E_COGNITIVE_STEP_FAILED` with the door's card under `door`.
- Freeze, then check, then act: the executor and the orchestrator copy the plan whole before checking it and run that copy; an artifact's content is immutable bytes from the moment it is made; the review scope and the criterion checks read one frozen set of artifacts.
- Credit binds what was evaluated: a paired trial names the candidate's and the baseline's digests, a control the frozen suite it ran on, and a run record the claim it executed; the gate counts the pairs it admitted, from one frozen copy.
- The conflict gate reads `adjudicate_claim`'s whole answer: a BLOCK or ASK disposition, or a triggered failure, refuses whatever the status says.
- A risk is an exact tier: an `int` from 0 to 3, never a `bool` or a float, wherever a standard or a context is made, and at the block's input.

## v0.2.2 — 2026-11-05: review 1 repairs

- One maker of refusals: every `blocked-escalated` result is made by `codes.refusal`, with a registered `G6_E_COGNITIVE_*` code (twenty in all) and a `warning_card`; `tests/cognitive_bridge/test_refusal_inventory.py` parses the package and fails on any other place that writes a state.
- Caller input decides nothing alone: an assessment credits only this orchestrator's own record of a run, step by step; a criterion only the checker its standard pins; a trial only the variant definition it names (`variant_digest`); a review only the evidence manifests it attested.
- Check, then act on what was checked: compiled models are deep copies pinned by digest, and the executor runs the step copies it validated.
- The adaptation gate protects the files of the code that judges a bridge result, listed from those functions, and compares a path as the file system resolves it.
- Duplicate probe ids are refused and a probe's cost is checked again where it runs; no component result carries a bare `verified` field; refutation-only evidence is `g6.epistemic_integrity`'s REFUTED; one variant's trials on one case count once whatever the oracle's revision.

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
- The selector can learn from a study's records: a `MethodVariant` may be any named method (`kind`: `engine`, the
  default, or a reviewer or an arm), repeated trials of one variant on one case are grouped by case instead of
  dropped, and `learn_from` names the splits it learns from (default held-out and shadow, as before).

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
