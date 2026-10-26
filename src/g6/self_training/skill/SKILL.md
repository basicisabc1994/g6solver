---
name: g6-self-training
description: Use when a proposed agent or harness change needs risk classification, promotion rules, regression comparison, or rollback after a measured regression.
---

# G6 self-training

`g6.self_training` governs self-modification. `govern_patch_promotion` is the deterministic gate for a proposed
harness change; `run_improvement_cycle` runs one sense, propose, evaluate, gate, apply, remeasure and
rollback cycle with callables you inject; the scorecard scores component outputs against shape rubrics. A decision
is `qualified-draft` at best, because every input to the gate is declared by its caller.

## When to use it

- An agent, a learner or the meta-harness has proposed a change to a harness (a prompt, examples, a routing rule, a
  rubric, a skill file, an evaluation suite) and someone is about to apply it.
- A proposed change touches paths, and you need to know whether it may touch them at all (deletions, test
  configuration, another component, protected paths).
- You have a test run before and after a change and need to know whether it failed, dropped or skipped a test that ran
  before.
- You want one improvement cycle that never applies a change it will not measure again, and rolls it back on a
  regression.

## When not to use it

- Tuning a repeated task end to end (models, prompts, hints, pipeline steps) on held-out cases: use `g6.metaharness`,
  which calls this gate for code-level changes.
- Checking a coding agent's own change to a user's repository: use a `g6.workforce` mission or `coding_agent_gate`.
- Measuring whether a hint helps: use `g6.learning`; whether a distilled function holds: `g6.distillation`.
- You want evidence a change works: this gate reads scores you supply; it does not measure them.

## Python API

- `govern_patch_promotion`: the promotion decision for a `HarnessPatch` and its `PatchEvalReport`; returns a
  `PromotionDecision`. `autonomous_low_risk=False` by default, so every promotion needs a person until the caller
  passes `True`.
- `HarnessPatch`: the change as data: `agent_id`, `target_surfaces`, effect flags (`permission_change`,
  `safety_policy_change`, `external_action_impact`, `removes_human_review`, `reversible`, `high_impact_domain`,
  `prohibited`), `touched_paths` and `diff`.
- `PatchEvalReport`: the caller's evaluation: `targeted_failure_fixed`, `regression_pass_rate`,
  `safety_violations`, `target_metric_improvement`, `evaluator_confidence`, `independent_eval` and `evaluator_id`.
- `PromotionDecision`: `decision`, `risk_tier`, `reliability_label`, `requires_human`, `auto_promotable`, `reasons`
  and `patch_digest`.
- `classify_patch_risk`: the risk tier (`R0` to `R5`) from the surfaces and effect flags; an unknown surface is `R3`.
- `decision_matches`: whether a decision was made on exactly this patch (by a digest of every field); check it
  before applying.
- `SAFE_SURFACES`: surfaces available for proposals; risk rules decide whether promotion needs a person.
- `OFF_LIMITS_SURFACES`: surfaces never promoted without a person.
- `classify_change_scope`: whether a `ChangeScope` may touch its paths: `allow`, `hitl` or `block`, with reasons.
- `ChangeScope`: the target component and its changed and deleted paths.
- `regression_delta`: `passed`, `failed` or `unevaluable` for a change's `RunSummary` against a baseline.
- `BenchmarkCase`: a unique case name, input data and equality target; explicit `None` is a target.
- `run_cases`: a Python function (no op reaches it from MCP) calling trusted application code on asserted cases.
  Stops before or between cases, missing assertions, empty suites, and executor `OSError` or `ImportError` subclasses make runs
  unevaluable; other exceptions fail cases. Duplicate ids and non-callables are refused. Before copying or execution,
  limits `max_cases` (default 1,000) and `max_suite_bytes` (1,048,576) refuse excess with `G6_E_RESOURCE_LIMIT`.
  Both are overridable. Bytes count builtin data and container memory; opaque objects are refused.
- `RunSummary`: failing, collected and skipped ids, evaluability, and `diagnostics` retaining stop reasons or
  environment exception types and messages.
- `failing_node_ids`: the node ids pytest's summary reports `FAILED` or `ERROR`.
- `run_improvement_cycle`: one governed cycle; off unless `enabled=True`; never raises. To apply it needs an
  `apply_fn`, a `rollback_fn`, a `remeasure_fn` and a `regressed_fn`, checked before anything runs.
- `ImprovementCycleResult`: what the cycle did (`stage`, `code`, `applied`, `rolled_back`, `rollback_failed`,
  `patch_in_effect`); `to_dict()` is the library envelope.
- `make_no_eval_report`: the stand-in evaluation when nothing evaluated a patch; the gate rejects it.
- `evaluate_rubric`: the weighted share of shape assertions an output meets.
- `audit_output`: scores one invocation you already ran; returns an `AuditResult` with a tier.
- `ComponentRubric`: shape assertions for one component's op, with `RubricProvenance` (who reviewed it).
- `RubricAssertion`: one shape check (`exists`, `not_empty`, `eq`, `gt`, `type_is`, `in_set` and others).
- `ScorecardStore`: an append-only audit log and a scorecard in a directory you name; `flag`, `resolve_flag`,
  `promote`.
- `diagnose_audit`: a fixed-rule classification of why an audit fell short, naming the rule that fired.

## What a person must approve

- **Every decision with `requires_human=True`**: the decision is the strictest review any rule asks for, and never
  weaker than its risk tier's own: `R2` needs `require_approval`, `R3` `require_security_approval`, `R4`
  `require_expert_review`. On top of the tier, an off-limits surface asks for at least `require_approval`
  (`security_boundary` and `tool_permission` for security approval, `kill_switch` for expert review), so `governance`
  or `billing` (`R3`) gets security approval and `promotion_gate` or `evaluator` (`R4`) expert review. A change with
  no independent evaluation (`independent_eval=False`, a missing `agent_id` or `evaluator_id`, or the same id for
  both), an `R0` or `R1` change with no `diff`, and every `R0` or `R1` change in the default manual-only mode ask for
  at least `require_approval`. The cycle applies none of these (`G6_E_SELF_TRAINING_REVIEW_REQUIRED`); nothing in
  this package records an approval, so the person applies the change themselves.
- **What runs without a person**: `promote_auto` (`R0`) and `canary` (`R1`) exist only when the caller passes
  `autonomous_low_risk=True`, and a cycle applies them only with `enabled=True`, `apply_enabled=True`, an `apply_fn`,
  a `rollback_fn`, a `remeasure_fn` and a `regressed_fn`. These are arguments, so an agent that calls the cycle can
  pass them itself. Without a `rollback_fn` nothing runs (`G6_E_SELF_TRAINING_NO_ROLLBACK`).
- **A scorecard tier**: `ScorecardStore.promote` is a decision someone makes. Raising the tier is refused while a
  component is flagged, and `T5` or above is refused unless the latest audit passed on a reviewed rubric (one whose
  `provenance.reviewed_by` is not blank). `resolve_flag` needs a non-blank note of what resolved it (the caller's
  word; nothing checks it).
- **No approval lifts a block**: expanded tool permissions, a weakened safety policy, removed human review, untrusted
  content treated as instructions, no rollback, a protected path in `touched_paths` (this package, `metaharness/`,
  `tests/`, `core/stop`, `invariants/`, `safety_engine/`, `harness/`, `evidence/` and others), or a prohibited patch.

## Alone and composed

Alone, from Python only (no block, so no `g6_invoke` op):
`from g6.self_training import HarnessPatch, PatchEvalReport, govern_patch_promotion`.

Composed:

- `g6.metaharness`: checks changed paths and calls promotion with `autonomous_low_risk=False`; a block, rejection
  or `R3`+ tier refuses the change. Its declared `skill_file` surface yields `R2`.
- `regression_delta`: `RegressionVerdict.to_eval_report()` turns the before-and-after runs into a `PatchEvalReport`
  with `independent_eval=False`, so the gate never auto-promotes on the change's own suite.
- `g6.distillation` and `g6.learning`: a caller can fill the evaluation fields from their held-out reports; the gate
  does not read those reports itself.

## Failure modes it catches

- `unchecked_self_change` (a self-change that slips past its own checks): the gate is model-free; scores that are
  not finite and in range fail; a patch whose `touched_paths` name the gate, its evaluators, the stop, the tests or the
  meta-harness is blocked whatever surfaces it declares; the generator may not be its own sole judge; no rule can
  lower the review a risk tier needs; a decision is bound to a digest of every field of the patch (its diff, paths,
  surfaces, effect flags and `agent_id`), and a patch changed after the decision, by a `snapshot_fn` or anyone else,
  is not applied (`G6_E_SELF_TRAINING_DECISION_MISMATCH`). The cycle reads the process-wide stop before sensing,
  proposing, evaluating and applying, never applies a patch it has no `rollback_fn` for, and rolls back on an
  `apply_fn` that raises (the patch may be partly applied), a regression, a failed remeasure or an answer that is not
  `True` or `False`; a rollback that raises is reported (`G6_E_SELF_TRAINING_ROLLBACK_FAILED`).

- `broke_existing_functionality`: `run_cases` supplies actual per-case outcomes to `regression_delta`, so a new
  failure cannot hide behind an unchanged aggregate rate. Keep the same case ids across versions.

## Blind spots

- `runner_in_process`: trusted callables have no enforceable in-process deadline; a hung callback can prevent
  return. Suite limits bound input data, not callback allocations or output size. Use `core.sandbox` for untrusted
  execution. Expected outputs are caller assertions; case identity does not detect an edited expected value.

- Every score, flag and identity in `HarnessPatch` and `PatchEvalReport` is the caller's word: once a caller turns
  autonomy on (`autonomous_low_risk=True`), declaring a passing independent evaluation gets `canary` for a prompt
  change.
- Surfaces are declared, not inferred from the diff; only `touched_paths` are checked against the protected list.
- `canary` has no slice: the cycle applies it in full, like `promote_auto`.
- The digest binds the patch, not the evaluation: a `PatchEvalReport` edited after the decision is not detected.
- A rollback that raises leaves the patch possibly in effect (`rollback_failed`); restore it from your snapshot.
- Rubrics check shape (a field exists, has a type, a bound), not quality; `diagnose` is a floor of fixed patterns.

## What it needs

Nothing outside G6: pure Python rules; the scorecard writes two plain files in the directory you name.

## Examples

A person decides promotion by default. Per-case measurements catch regressions without issuing a reliability label.

```python
from g6.self_training import (BenchmarkCase, HarnessPatch, PatchEvalReport, govern_patch_promotion,
                              regression_delta, run_cases)

patch = HarnessPatch(agent_id="proposer", target_surfaces=["prompt"], diff="+ Give the unit.",
                     touched_paths=["prompts/answer.txt"])
assert govern_patch_promotion(patch, PatchEvalReport()).reliability_label != "verified"
cases = [BenchmarkCase("double", 21, 42), BenchmarkCase("zero", 0, 0)]
baseline = run_cases(lambda value: value * 2, cases)
changed = run_cases(lambda value: value * 2 + 1, cases)
assert regression_delta(changed, baseline).new_failures == {"double", "zero"}
assert regression_delta(baseline, baseline).to_eval_report().independent_eval is False
assert run_cases(lambda value: {"success": True}, [BenchmarkCase("unasserted", {})]).evaluable is False
```

## The four open problems

- **Learning signals**: the scorecard's audit log is append-only, and a flag holds a component's tier until it is
  resolved with a note; every cycle result carries its decision and remeasure as evidence, and its rollback state in
  its fields. Not yet: a cycle's result is returned, not written to a shared run ledger.
- **Evaluators the model cannot influence**: the gate's rules are fixed code, and the paths that judge a change (this
  package, the tests, the meta-harness's evaluator and gate, the stop) are blocked for any patch whose
  `touched_paths` name them. The evaluation scores themselves are the caller's.
- **Transfer by failure mode**: the gate's rules are bound to surfaces, effects and paths, not to a task, so they
  apply to any harness change. Not yet: no catch rate measured across kinds of change.
- **Enough held-out cases**: Not yet: the gate reads thresholds (a regression pass rate of at least 0.98, an
  improvement of at least 0.03, an evaluator confidence of at least 0.75) on numbers you supply, and checks no sample
  size.
