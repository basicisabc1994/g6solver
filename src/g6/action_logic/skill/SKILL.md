---
name: g6-action-logic
description: Use when an agent or workflow is about to take a large or hard-to-undo step and must check that it has a complete plan to observe, stop and roll it back, that it does not collide with other actions in flight, that no warning signal is being ignored, or that it is not still relying on a hypothesis an outcome has already disproved.
---

# G6 action logic

Deterministic controls for how complex decisions fail: act in small, checked steps, watch for warnings, and revise
on disconfirming evidence. Each op is a pure function over facts the caller declares. The four check ops answer in
the action gate's severity scale: `notice`, then `review`, then `block`.

## When to use it

- Before a material step (irreversible, external, real-world, or crossing several systems):
  `assess_material_action` with its verification contract, the actions already in flight and the world state.
- Several changes are scheduled on the same resource or budget: `detect_conflicts`, then `sequence_if_separable` for
  a one-at-a-time order.
- During an incident: `check_crisis_monitoring`, to see that monitoring has not dropped below its baseline.
- After an outcome, before the plan's assumption is used again: `update_on_outcome`, `apply_disconfirming_evidence`,
  `disconfirming_evidence_severity` and `reauthorisation_severity`.

## When not to use it

- Deciding whether one concrete agent action may go ahead: `action_gating.evaluate`. It runs the verification,
  coordination and weak-signal controls itself, works out materiality from the action, and adds impact, authority and
  an audit record.
- Run limits (tool calls, cost, wall time): `safety_engine`.
- Whether a claim is backed by evidence: `epistemic_integrity` or `grounding`.

## Ops

- `assess_material_action`: the verification-contract, coordination and weak-signal controls together for one action; the most restrictive severity wins.
- `detect_conflicts`: collisions between a `proposed` action and `active` ones: contradictory objectives, duplicate effects, resource contention, a combined budget breach, an invalidated assumption or a reused id.
- `sequence_if_separable`: a one-at-a-time order of action ids that removes the contention, or `null` when ordering cannot remove the collision.
- `check_crisis_monitoring`: `block` when monitoring in a crisis drops below the `baseline` in any dimension; `review` when the baseline records nothing.
- `update_on_outcome`: apply an observed outcome to a hypothesis: `confirmed` makes it `supported` (never proven), `disconfirmed` or `harmful` makes it `falsified`, and a falsified one stays falsified.
- `apply_disconfirming_evidence`: record evidence against a hypothesis, halve its confidence and mark it `weakened` (a falsified one only gains the note).
- `disconfirming_evidence_severity`: `review` when a hypothesis in use carries disconfirming evidence and is not yet falsified.
- `reauthorisation_severity`: `block` reliance on a falsified hypothesis unless a new, well-formed one with evidence of its own replaces it.

## Reading the result

The answer is under `result`. A check op gives `severity` (`null`, `notice`, `review` or `block`), `reason_codes`
and what is behind them, under `items` for `assess_material_action` and `item` for the other three. The codes are
`G6_E_BALLISTIC_ACTION_BLOCKED`, `G6_E_ACTION_COLLISION`,
`G6_E_NO_FUTURE_HORIZON`, `G6_E_UNADDRESSED_WEAK_SIGNAL`, `G6_E_THRESHOLD_ALREADY_BREACHED`,
`G6_E_CRISIS_MONITORING_DEGRADED`, `G6_E_CRISIS_MONITORING_UNKNOWN`, `G6_E_DISCONFIRMING_EVIDENCE_PENDING` or
`G6_E_FALSIFIED_HYPOTHESIS_REUSED`. `assess_material_action` also returns `materiality` as
`advisory_declared_materiality`: you declared `is_material`, so the answer is advice, not an authorisation. A
complete contract still reads `operationally_verified: false`.

A refusal names the field at fault: `G6_E_ACTION_LOGIC_FIELD_REQUIRED` when one is missing (for example
`hypothesis.hypothesis_id and hypothesis.statement are required`, or `proposed is required` for `detect_conflicts`),
`G6_E_ACTION_LOGIC_INVALID_FIELD` when a record has a field it does not take or a value it cannot read (an `outcome`
or `status` outside its list). A parameter the op does not take is refused with `G6_E_INPUT_VALIDATION`.

Field shapes, as JSON:

- `contract`: `expected_result`, `observation_window`, `leading_indicators`, `lagging_indicators`, `monitor`,
  `verification_time`, `stop_threshold`, `rollback_procedure`, `model_update_rule`. Complete means every one is
  filled in, with at least one leading and one lagging indicator.
- An action (`proposed`, each of `active` and `actions`): `action_id`, `target_resource`, `direction`,
  `shared_resources`, `costs` (`[["resource", amount]]`), `effect_signature`, `window`, `relies_on_assumptions`,
  `invalidates_assumptions`, `idempotency_key`. A `window` is an ISO 8601 interval `start/end` or a bucket token such
  as `nightly`; anything else counts as overlapping.
- `world`: `variables` (each `name`, `value`, `threshold`, `trend`, `is_resource`, `acknowledged`) and
  `horizons_evaluated`.
- A hypothesis: `hypothesis_id`, `statement`, `confidence`, `supporting_evidence`, `disconfirming_evidence`,
  `falsification_condition`, `status`.

## Alone and composed

Alone, over MCP: `g6_invoke("action_logic", "detect_conflicts", params_json)`. From Python:
`from g6.action_logic import assess_material_action, detect_conflicts, VerificationContract, ActionIntent, WorldState`.

Composed:

- `action_gating` calls the verification-contract, coordination and weak-signal controls on the contexts an action
  declares, and the hypothesis controls only when `G6_ACTION_LOGIC_HYPOTHESIS_GATE` is on. Their severity raises the
  gate's decision, never lowers it.
- `check_crisis_monitoring` is called by no gate: call it yourself, and do not describe it as enforced.
- The hypothesis ops return new records and remember nothing: keep the latest record and pass it back next time.

## Failure modes it catches

- `unchecked_big_step` (a big move made without checking it first): a material step whose verification contract is
  missing or incomplete is `block`; a collision with an action in flight blocks a material step; and
  `sequence_if_separable` turns a contended set into one-at-a-time steps, or refuses when order cannot help.
- `ignored_warning` (a warning ignored on the way to an action): an unacknowledged deteriorating signal, an already
  breached threshold or no future horizon blocks a material step; disconfirming evidence on a hypothesis in use is
  `review`; reusing a falsified hypothesis is `block`; monitoring that drops in a crisis is `block`.

## Blind spots

- Everything is declared: materiality, the actions in flight, the world state, outcomes and evidence. A warning the
  caller leaves out is never seen.
- A complete contract is complete text: nothing checks that the monitor, stop threshold or rollback exists.
- Collisions are read from declared fields only; feedback loops and fine timing are folded into shared resources
  and window overlap.
- Confidence moves by fixed steps (+0.2, +0.1, ×0.7, ×0.5), not by calibration.
- A replacement hypothesis's evidence is free text: nothing ties it to an observation.
- It declares no failure mode of its own.

## What it needs

Nothing outside G6: pure Python over frozen dataclasses, with pydantic for the MCP input. No model.

## Examples

```g6-example op=assess_material_action
{"is_material": true, "contract": {"expected_result": "p95 latency under 200 ms", "observation_window": "30 minutes"}, "world": {"variables": [{"name": "disk_free_gb", "value": 40, "threshold": 10, "trend": "falling", "is_resource": true}], "horizons_evaluated": []}}
```

The reply is `block` with three reason codes: the contract lacks every element after its expected result and
observation window; no future horizon was evaluated; and free disk space is falling towards its floor
unacknowledged.

```g6-example op=detect_conflicts
{"proposed": {"action_id": "scale-down", "target_resource": "web-pool", "direction": "decrease", "window": "nightly"}, "active": [{"action_id": "scale-up", "target_resource": "web-pool", "direction": "increase", "window": "nightly"}]}
```

The reply lists one `contradictory_objectives` conflict with `scale-up`.

```python
from g6.action_logic import (ActionIntent, Hypothesis, HypothesisStatus, Outcome, VerificationContract,
                             assess_material_action, reauthorisation_severity, sequence_if_separable,
                             update_on_outcome)

partial = VerificationContract(expected_result="error rate under 1%", observation_window="30 minutes")
found = assess_material_action(is_material=True, contract=partial)
assert found.blocks() and "G6_E_BALLISTIC_ACTION_BLOCKED" in found.reason_codes

a = ActionIntent("reindex", shared_resources=("db",), window="w1")
b = ActionIntent("backfill", shared_resources=("db",), window="w1")
assert sequence_if_separable([a, b]) == ["reindex", "backfill"]  # contention: run them one at a time
up = ActionIntent("scale-up", target_resource="web", direction="increase", window="w1")
down = ActionIntent("scale-down", target_resource="web", direction="decrease", window="w1")
assert sequence_if_separable([up, down]) is None  # contradictory: no order helps

h = Hypothesis("h1", "the cache causes the slow page", falsification_condition="still slow with the cache off")
h = update_on_outcome(h, Outcome.DISCONFIRMED, evidence="still slow with the cache off")
assert h.status is HypothesisStatus.FALSIFIED
assert update_on_outcome(h, Outcome.CONFIRMED).status is HypothesisStatus.FALSIFIED  # it stays falsified
severity, codes, _ = reauthorisation_severity(relied_hypothesis=h)
assert severity == "block" and codes == ["G6_E_FALSIFIED_HYPOTHESIS_REUSED"]
```

## The four open problems

- **Learning signals**: Not yet: it writes nothing. Each answer is a labelled record (a severity, `G6_E_*` reason
  codes and the items behind them) a caller can store; inside `action_gating` the gate's audit trail keeps their
  reason codes, for the actions it audits. The hypothesis records are the nearest thing to learning, and the caller
  keeps them.
- **Evaluators the model cannot influence**: the controls are fixed code with no model call. The caller supplies
  every fact they read, so an agent that fills in the facts shapes the answer. Not yet: the code is not
  digest-pinned.
- **Transfer by failure mode**: the controls are bound to `unchecked_big_step` and `ignored_warning` and take no
  task-specific input, so they apply to any step described in their fields: an infrastructure change, a payment run,
  a data job. Not yet: their catch rate on unseen task families is not measured.
- **Enough held-out cases**: Not yet: their catch and false-alarm rates are not judged by G6's sequential test; the
  tests are fixed regressions.
