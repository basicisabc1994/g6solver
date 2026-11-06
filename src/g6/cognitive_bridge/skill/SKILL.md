---
name: g6-cognitive-bridge
description: Use when an agent must choose which way to check a research or engineering claim (a rule graph, a causal model, a formal check, a predictive model or an optimisation), rank discriminating probes between competing hypotheses, or assemble the evidence for a claim against a pinned standard, with the choice and the evidence kept apart from the agent that made the claim.
---

# G6 cognitive bridge

Typed planning and evidence assembly for claims that need more than one kind of check. It ranks probes between
competing hypotheses by expected information gain and updates beliefs by Bayes' rule on the likelihoods you state;
it turns a claim's evidence needs into steps on G6's own solvers (`graph_reasoning`, `causal`, `formal`,
`adapt_sklearn`, `optimisation`) without running them; it ranks method variants for a problem signature from trials
an outside evaluator admitted; and, from Python, it assembles an assurance dossier and gates an adaptation on paired
held-out trials. Every result is `qualified-draft` or `blocked-escalated`: it never reports `verified`.

## When to use it

- Several hypotheses compete and you must pick the next probe: `rank`, then `update` with what you observed.
- A claim needs a rule graph, a causal model, a formal check, a predictive model or an optimisation: `plan_methods`
  lists the solver steps, and says which typed model is missing instead of guessing one.
- You must choose between ways of meeting one evidence need (an SMT encoding or a propositional one): `rank_methods`.

## When not to use it

- Checking one claim against source text: `grounding`. Grading the evidence behind a conclusion: `epistemic_integrity`.
- Deciding whether a harness change may go live: `metaharness` and `self_training`; this gate only recommends a
  shadow trial.
- Running a solver: call the solver itself. Nothing here runs one over MCP.

## Ops

- `rank`: the affordable probes by expected information gain (bits), best first, for priors that sum to one.
- `update`: the posterior after one observed probe outcome, under the probe's stated likelihood.
- `plan_methods`: the typed G6 solver steps for each claim's evidence needs; the steps are planned, not run.
- `rank_methods`: a cold-start ranking of method variants for a problem signature; no trial data over MCP. A
  context's `risk` is the number of its risk tier, an integer from 0 to 3 (`1` is R1; `true`, `1.0` or `"1"` is
  refused); at R2 and R3 no exploration bonus applies.
- `assurance_schema`: the documentary fields an assurance dossier must fill, and the two states it can carry.

## Reading the result

Every refusal is `blocked-escalated` with its code in `code` and in its `warning_card`, made in one place
(`codes.refusal`), which writes those fields itself and refuses a caller's value for them, for `verified`, and for
every field G6's door reads as a state (`codes.STATE_FIELDS`, taken from the door, `reliability_label` among
them). A
parameter an op does not take is `G6_E_INPUT_VALIDATION`; input the op cannot use (priors that
do not sum to one, a probe that does not predict every hypothesis, a plan with no claim) is
`G6_E_COGNITIVE_BRIDGE_INPUT`. A need with no typed model is `G6_E_COGNITIVE_MODEL_MISSING`; a mandatory method with no
affordable variant `G6_E_COGNITIVE_SELECTION_INCOMPLETE`; a required method no claim needs
`G6_E_COGNITIVE_OBLIGATION_NOT_PLANNED`.

From Python: running a plan without approval is `G6_E_COGNITIVE_APPROVAL_REQUIRED`, an edited plan or a compiled model
that no longer matches its pin `G6_E_COGNITIVE_PLAN_TAMPERED`, an orchestrator plan that is not approved, not fully
specified or has no registry `G6_E_COGNITIVE_EXECUTION_NOT_APPROVED`, and an incomplete compiled plan
`G6_E_COGNITIVE_COMPILE_BLOCKED`, each before anything runs. A step whose prerequisite failed is
`G6_E_COGNITIVE_PREREQUISITE_UNMET`, one whose call raised or that G6's door refused or did not pass
`G6_E_COGNITIVE_STEP_FAILED` (the door's own card is kept under `door`), and the run is then
`G6_E_COGNITIVE_EXECUTION_INCOMPLETE`. A dossier with a blocker is
`G6_E_COGNITIVE_DOSSIER_BLOCKED`; a refused guard change `G6_E_COGNITIVE_ADAPTATION_REFUSED`; a proposition
`G6_E_COGNITIVE_EVIDENCE_CONTESTED`, `G6_E_COGNITIVE_EVIDENCE_REFUTED` or `G6_E_COGNITIVE_EVIDENCE_UNDETERMINED`, and the
reconciliation `G6_E_COGNITIVE_EVIDENCE_NOT_RECONCILED`; an agent with no executable probe `G6_E_COGNITIVE_NO_PROBE`, and one whose
tool step G6's door did not pass `G6_E_COGNITIVE_STEP_FAILED`.
An assessment carries `warning_card`, `evidence`, `request_id` and `run_id`: `G6_E_COGNITIVE_ASSURANCE_BLOCKED`
names what failed, and `G6_E_COGNITIVE_NOT_PROMOTED` says the checks passed in this process and nothing more. It
credits only a run this orchestrator executed, from its own record, for the claim that run executed, step by step.

## Alone and composed

Alone, over MCP: `g6_invoke("cognitive_bridge", "rank_methods", params_json)`. From Python:
`from g6.cognitive_bridge import MetacognitiveMethodSelector, MethodRouter, MixedMethodExecutor`.

Composed (Python only):

- `MixedMethodExecutor` runs an approved plan's steps through G6's checked door (`g6.core.door.invoke_checked`, the
  call `g6.invoke` makes): a sensitive op runs only when the person allowed it (`allow=` or `G6_MCP_SENSITIVE`), and
  a component's own `verified` is held at `qualified-draft` with the door's card. A step whose answer is not plain
  JSON data, or carries a `reliability_label`, a `verified` flag outside a warning card or a nested result outside the
  bridge's format, is `G6_E_COGNITIVE_STEP_FAILED`, the reason naming each place.
  It checks the plan against the canonical routing, refuses an edited one (`G6_E_COGNITIVE_PLAN_TAMPERED`) and runs nothing unless `approved=True`
  (`G6_E_COGNITIVE_APPROVAL_REQUIRED`; `"false"`, `1` or any other value is not approval).
- `CognitiveAgent` makes each observation a tool step through the same door, credited only when the door's reply
  says `ok`; `allow=` is the person's allow, as the executor takes it.
- Everything a caller or a collaborator hands the bridge is taken once, where it enters, as plain JSON data
  (`g6.cognitive_bridge.snapshot.plain`, a round trip through its canonical encoder): a set, a subclass, an object, a
  key that is not text or a number that is not finite is refused under `G6_E_COGNITIVE_BRIDGE_INPUT` with its path,
  and an identity is text. Every record is immutable all the way down and shares nothing with what the caller keeps.
  An assessment reads the standard's digest again after the evaluator used it.
- `CognitiveAssuranceOrchestrator` joins the selector, the router, the executor and `InProcessAssuranceEvaluator`:
  compile, execute with approval, then assess the produced bytes against the standard's criteria. A compilation's
  state is its selection's verdict: a blocked selection never runs or is credited, whatever its `refused` field says.
  Execution and assessment take only a compilation this orchestrator issued, and credit only a dossier or a
  reconciliation the evaluator or gate they called issued (the digest of every field, held in memory): one built by
  hand, edited or made by another instance is refused.
- The cross-method conflict gate reads stance and lineage by `epistemic_integrity`'s rules: one source repeated is
  one lineage, and each proposition's status (contested, refuted, undetermined) is `adjudicate_claim`'s; a BLOCK or
  ASK disposition, or a failure it triggered, refuses whatever the status says.
- The adaptation gate's paired sign test is `reasoning.sign_test_p`, the statistic the meta-harness gate decides by,
  at alpha divided by the number of evaluations. A change may write only the files the bridge declares adaptable
  (`adaptation.ADAPTABLE`), compared by file identity however the path is spelled or linked, and never one
  `g6.core.adaptation_gate` protects; every other path is refused. A paired trial credits only the change whose
  candidate and baseline digests it names, and a control only the frozen suite it names.

## Failure modes it catches

- `ad_hoc_choice`: a method or probe is chosen by a stated score (information gain, a smoothed success rate from
  admitted trials, a cost), never by the agent's preference; every mandatory evidence need must be covered within
  budget, or the plan blocks.
- `false_confidence`: no result is `verified`; a dossier blocks on a missing or failed critical check, a checker that
  misses its planted negative control, a missing documentary section, or an unauthenticated review at R2 and R3.
- `repetition_as_corroboration`: the conflict gate counts one source repeated as one lineage, and the selector
  groups one variant's repeated trials on one case into one case, with the share that resolved, whatever the
  oracle's revision. A trial credits only the variant definition it names (`variant_digest`), not a reused id.

## Blind spots

- `unevaluated_model_shipped`: a predictor handed to `MLAcquisitionSelector`, or a surrogate tree, is used as given;
  its fidelity is measured only on the held-out rows you supply.
- The likelihoods, priors, standards and admission hooks are the caller's. A permissive admission hook admits
  anything, and `InProcessAssuranceEvaluator` runs in this Python process: it neither isolates the checks it runs nor
  attests that they were isolated.
- A planned step checks the typed model you gave, not whether that model says what the claim means.

## What it needs

Nothing outside G6 for its ops. The surrogate and tree export in `g6.cognitive_bridge.xai` need scikit-learn:
`pip install "g6[ml] @ git+https://github.com/basicisabc1994/g6solver"` (g6[ml]).

## Examples

```g6-example op=rank
{"budget": 1, "hypotheses": [{"id": "A", "statement": "the cache is stale", "prior": 0.5}, {"id": "B", "statement": "the index is corrupt", "prior": 0.5}], "probes": [{"id": "p1", "question": "Does a cold restart fix it?", "tool": "read", "positive_if": {"A": true, "B": false}, "assumed_match_probability": 0.9}]}
```

```g6-example op=update
{"hypotheses": [{"id": "A", "statement": "the cache is stale", "prior": 0.5}, {"id": "B", "statement": "the index is corrupt", "prior": 0.5}], "probes": [{"id": "p1", "question": "Does a cold restart fix it?", "tool": "read", "positive_if": {"A": true, "B": false}, "assumed_match_probability": 0.9}], "probe_id": "p1", "observed_positive": true}
```

```g6-example op=plan_methods
{"claims": [{"id": "c1", "statement": "A and A implies B entail B", "evidence_needs": ["formal"], "models": {"formal": {"formula": "B", "premises": ["(=> A B)", "A"], "sorts": {"A": "Bool", "B": "Bool"}}}}]}
```

```g6-example op=rank_methods
{"context": {"domain": "software_engineering", "uncertainty": "formal_specification", "claim_kind": "invariant", "risk": 1, "budget": 4}, "required_methods": ["formal"], "variants": [{"id": "smt", "method": "formal", "representation": "SMT", "engine": "formal", "op": "check_validity", "estimated_cost": 2}]}
```

```g6-example op=assurance_schema
{}
```

```python
from g6.cognitive_bridge import MetacognitiveMethodSelector, MethodVariant, ProblemSignature

context = ProblemSignature("software_engineering", "formal_specification", "invariant", 1, 3)
variants = [MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2.0),
            MethodVariant("dpll", "formal", "propositional", "formal", "check_propositional", 1.0)]
decision = MetacognitiveMethodSelector().select(context, variants, required_methods=["formal"])
assert decision.completion_state == "qualified-draft" and decision.admitted_trials == 0  # no admission hook
```

## The four open problems

- **Learning signals**: the selector learns only from trials an outside evaluator's hook admits, from the splits
  `learn_from` names (held-out and shadow by default); a variant may be a solver op, a reviewer or a study arm
  (`MethodVariant(..., kind="arm")`), so a study's records can train it. Over MCP it has none, so `rank_methods` is a
  cold start. Not yet: no trial store ships with it.
- **Evaluators the model cannot influence**: the frozen checkers, controls, standards and admission hooks are passed
  in by the evaluator, not written by the candidate. Not yet: they run in the caller's own process.
- **Transfer by failure mode**: a dossier's failed criteria become failure-mode findings with the codes the standard
  names, so a guard can be tied to a failure seen elsewhere. Not yet: transfer across domains is not measured.
- **Enough held-out cases**: the adaptation gate needs enough held-out pairs and discordant pairs, a win by the exact
  sign test at alpha divided by the number of evaluations, and no critical regression. Not yet: no study has run.
