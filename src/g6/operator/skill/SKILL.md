---
name: g6-operator
description: Use when proposed tasks need ranking by explicitly based estimates, optional model decomposition into a review queue, or one budgeted task tick whose outward drafts await a person's decision.
---

# G6 operator

The operator holds proposed work and outward drafts in one approval queue. It sends nothing itself. Estimates,
model proposals and successful caller checks are `qualified-draft` at best.

## When to use it

Rank tasks by estimated value times likelihood divided by effort. Every estimate must name its basis.
Optionally ask an injected model caller to propose tasks, then let a person review the existing queue.
Use the existing `tick` to judge actual task output with acceptance checks.

## When not to use it

Do not use this package as a scheduler, an autonomous execution service, a model router or an authorisation
boundary against code already running in the process. It does not measure whether the estimates are accurate.

## Python API

This package has no block, registry component or MCP operations.

- `Estimate`: finite, non-negative `amount` and mandatory `basis`: `declared`, `model_estimate` or `template`.
- `TaskProposal`: task id, description, timezone-aware creation time, value, likelihood and effort estimates.
  Effort must be positive and likelihood must lie between zero and one. Schemas forbid extra fields.
- `rank`: compute value times likelihood over effort and return `ranked` entries containing each task and score.
  Ties go to the oldest creation time, then preserve input order. Only entirely declared estimates rank by default;
  `include_estimated=True` explicitly includes model and template estimates. Exclusions are named. Invalid tasks,
  duplicate ids, missing bases and non-finite scores refuse the entire ranking with `G6_E_OPERATOR_ESTIMATE`.
- `propose_tasks`: optional decomposition through an injected `g6.core.llm.LLMCaller`. No caller gives
  `G6_E_OPERATOR_NO_MODEL`; malformed output or a failed call gives `G6_E_OPERATOR_PROPOSAL`. Nothing substitutes
  fixed templates. Model estimates must say `model_estimate`; model claims of approval or acceptance are refused.
  Caller failures retain their type and recovery instructions, with credential-shaped values redacted.
  Valid proposals enter the existing queue as `proposed`, never as accepted or completed.
- `ActionQueue`: enqueue drafts, list them, and record a person's approve, reject or sent decisions. Internal
  `propose_tasks` inserts validated task proposals atomically. They cannot be marked sent.
- `OperatorStore`: the existing database and owner scope, venture records and budget ledger.
- `Task`: one task for a tick, with its reservation and harness checks.
- `ExecutionResult`: an executor's output, declared status, cost and proposed outward drafts.
- `SimulatedExecutor`: an explicit test/demo executor; it is not an actual model or execution service.
- `tick`: reserve budget, call the injected executor, record actual check outcomes and gate outward drafts.
- `route`: deterministic draft checks, with unknown measurements distinguished from passes.
- `Context`: contacts, price floor, secrets, caller checks and shadow guards for draft checking.
- `Guard`: a proposed learned check, ineffective until a person adopts it through the queue.

## Alone and composed

Use `rank` alone with typed proposals or dictionaries. To decompose, pass an existing queue and venture id plus
an explicitly configured caller to `propose_tasks`; the caller owns model-call authorisation. The operation is
listed in `__g6_sensitive_ops__`, but this Python-only package has no registry switch enforcing that declaration.
Task proposals use the existing queue and its decisions; they add no tenancy or budget mechanism.

Use harness `Check` values in `Task.checks` to assess actual output with `tick`. Outward drafts still go through
`g6.action_gating` and deterministic checks. A person must approve a draft before recording that they sent it.
A drafting actor cannot approve its own item. No task proposal executes itself after approval.

## Failure modes it catches

- `low_value_first`: calculated scores determine ordering, with age for ties. A missing basis or zero effort is
  refused, not replaced with a convenient default. Task description keywords do not affect ranking or choose a model.

## Blind spots

A named basis does not establish accuracy. Declared estimates can be mistaken, and model estimates remain guesses.
Task creation times are declared for direct Python inputs; model proposals use the queue's clock. Internal
proposals are not completed work. The queue can refuse the drafter's name but cannot establish that a different
reviewer string belongs to a person. Callables supplied to `tick` are trusted application integration code,
not sandboxed code. A model caller may have costs or network effects controlled by its own configuration.

## What it needs

Existing G6 dependencies only; no new extra, outside program, key or provider client. Deterministic ranking calls
no model. Optional decomposition requires an injected caller and observes the process-wide stop before the call
and before queue insertion (`G6_E_STOPPED`). State remains in the existing `operator.db` through core storage.

## Examples

```python
from g6.operator import rank

def task(name, value, date):
    return {"task_id": name, "description": name, "created_at": date,
            "value": {"amount": value, "basis": "declared"},
            "likelihood": {"amount": 0.5, "basis": "declared"},
            "effort": {"amount": 2, "basis": "declared"}}

report = rank([task("new", 8, "2026-10-02T00:00:00Z"), task("old", 8, "2026-10-01T00:00:00Z")])
assert [r["task"]["task_id"] for r in report["ranked"]] == ["old", "new"]
assert report["ranked"][0]["score"] == 2.0
assert report["completion_state"] == "qualified-draft"
```

```python
import json
from g6.core.llm import FakeLLM
from g6.operator import ActionQueue, OperatorStore, propose_tasks

store = OperatorStore(":memory:")
try:
    scope = store.for_owner("demo")
    venture = scope.create_venture("policy", caps={"model": 0, "cash": 0})["venture_id"]
    queue = ActionQueue(scope)
    response = {"tasks": [{"description": "Outline the policy",
                          "value": {"amount": 3, "basis": "model_estimate"},
                          "likelihood": {"amount": 0.5, "basis": "model_estimate"},
                          "effort": {"amount": 2, "basis": "model_estimate"}}]}
    report = propose_tasks(queue, venture, "Explain the policy", llm=FakeLLM(default=json.dumps(response)))
    assert report["completion_state"] == "qualified-draft"
    assert queue.list(venture)[0]["status"] == "proposed"
    assert queue.decisions(venture) == []
finally:
    store.close()
```

## The four open problems

- **Learning signals**: existing queue decisions and tick receipts persist; ranking creates no second ledger.
- **Evaluators the model cannot influence**: model text is schema-checked and remains a proposal. Tick completion
  comes from executed checks, not a non-empty reply.
- **Transfer by failure mode**: the numeric ordering works for any task with comparable units; no keyword list.
- **Enough held-out cases**: ranking estimates is not an evaluation. Measure task outcomes separately before
  interpreting an estimate as evidence of future performance.
