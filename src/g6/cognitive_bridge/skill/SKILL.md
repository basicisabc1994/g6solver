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
- `rank_methods`: a cold-start ranking of method variants for a problem signature; no trial data over MCP.
- `assurance_schema`: the documentary fields an assurance dossier must fill, and the two states it can carry.

## Alone and composed

Alone, over MCP: `g6_invoke("cognitive_bridge", "rank_methods", params_json)`. From Python:
`from g6.cognitive_bridge import MetacognitiveMethodSelector, MethodRouter, MixedMethodExecutor`.

Composed (Python only):

- `MixedMethodExecutor` runs an approved plan's steps on G6's solvers. It re-derives the plan first and refuses an
  edited one (`G6_E_COGNITIVE_PLAN_TAMPERED`), and runs nothing without approval (`G6_E_COGNITIVE_APPROVAL_REQUIRED`).
- `CognitiveAssuranceOrchestrator` joins the selector, the router, the executor and the assurance checker: compile,
  execute with approval, then assess the produced bytes against the standard's criteria.

## Failure modes it catches

- `ad_hoc_choice`: a method or probe is chosen by a stated score (information gain, a smoothed success rate from
  admitted trials, a cost), never by the agent's preference; every mandatory evidence need must be covered within
  budget, or the plan blocks.
- `false_confidence`: no result is `verified`; a dossier blocks on a missing or failed critical check, a checker that
  misses its planted negative control, a missing documentary section, or an unauthenticated review at R2 and R3.
- `repetition_as_corroboration`: the conflict gate counts one source repeated as one source, and the selector
  counts one variant's trials on one case as one case.

## Blind spots

- `unevaluated_model_shipped`: a predictor handed to `MLAcquisitionSelector`, or a surrogate tree, is used as given;
  its fidelity is measured only on the held-out rows you supply.
- The likelihoods, priors, standards and admission hooks are the caller's. A permissive admission hook admits
  anything, and the assurance checker runs in this Python process: it neither isolates the checks it runs nor
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

- **Learning signals**: the selector learns only from trials an outside evaluator's hook admits; over MCP it has
  none, so `rank_methods` is a cold start. Not yet: no trial store ships with it.
- **Evaluators the model cannot influence**: the frozen checkers, controls, standards and admission hooks are passed
  in by the evaluator, not written by the candidate. Not yet: they run in the caller's own process.
- **Transfer by failure mode**: a dossier's failed criteria become failure-mode findings with the codes the standard
  names, so a guard can be tied to a failure seen elsewhere. Not yet: transfer across domains is not measured.
- **Enough held-out cases**: the adaptation gate needs enough held-out pairs and discordant pairs, a win by the exact
  sign test at alpha divided by the number of evaluations, and no critical regression. Not yet: no study has run.
