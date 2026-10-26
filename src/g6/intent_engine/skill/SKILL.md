---
name: g6-intent-engine
description: Use when an agent is about to start on a request whose purpose, standard of success, unacceptable outcomes, scope or risk tolerance it has not been told, and should ask the person instead of reading the request its own way; or when it needs the intent contract a mission or intake is held to.
---

# G6 intent engine

`g6.intent_engine` is the intent contract a task is held to (`IntentContract`) and a deterministic elicitor that fills
it: it merges the person's answers into the contract and lists the checklist fields still missing, as questions. It is
a field-completeness checklist, not an understanding of the request, and calls no model.

## When to use it

- The request is vague ("make my app better", "build export") and you are about to choose a reading yourself.
- You are about to start work with no observable success criteria, no stated unacceptable outcome or no scope
  boundary.
- A person's answer changes the goal itself, and you want that recorded rather than silently swapped in.
- A person declined to clarify, and you want the contract to say so.

## When not to use it

- A frozen standard of done that a person confirms, checked against the agent's patch: a `workforce` mission (it holds
  missions to this contract).
- The premortem and task intake: `reliability_pipeline` (`run_intake` uses this elicitor).
- Splitting a goal into subtasks with guardrails and approvals: `goal_engine`.
- Checking that a finished answer meets the contract: `coding_agent_gate` (`verify_answer`) or a `workforce`
  certificate. Typed checks here cover only explicitly mapped textual criteria.

## Python API

- `Check`: typed `contains`, `absent`, `regex`, or `word_count` with `min_words`/`max_words`; extra fields refused.
  Attach in `IntentContract.success_checks` or `failure_checks`, keyed by the exact criterion text. Old criteria
  remain strings and old contracts still load. A matching failure check means the unacceptable outcome occurred.
- `check_output`: rows with criterion, kind, index, `pass`/`fail`/`unchecked` and matching span. Unmapped prose stays
  unchecked, never converted to a literal absence test. Nothing checked is no evidence of completion.
- `harness_checks`: freeze these mappings into existing harness checks; unchecked becomes `unknown`, visible as
  `value_report.unmeasured` in the existing ledger. A failure criterion's check reads `avoids: <criterion>`, so its
  pass means the outcome did not occur. A person must review mappings before freezing them.

- `interpret`: `interpret(request, contract, llm=None)` computes the missing-field and declared-risk floor:
  `endorsed < clarify < block`. A request that is not the contract's goal (case and spacing aside) stays at
  `clarify`, unless the elicitor recorded the goal answer replacing it. Optional injected `LLMCaller` advice may only
  raise the floor and propose untrusted questions; it never fills fields. The contract is returned unchanged; persist
  it yourself, including preferences in constraints.
- `apply_intent_floor`: stricter of two known postures; unknown postures block.

- `IntentElicitor`: `elicit(initial_request, answers=..., prior=..., user_declined=...)` returns an `IntentSession`;
  `max_questions` (5) and `confidence_threshold` (0.82) are set on construction.
- `IntentSession`: `session_id` (covers the request, answers, prior and decline), `questions`, `contract` and
  `ended_by`: `confidence`, `incomplete`, `question_budget` or `declined`.
- `IntentContract`: the goal with `why`, `success_criteria`, `failure_criteria`, `non_goals`, `risk_tolerance`,
  `user_expertise_level`, `cost_constraint`, `deadline`, `assumptions`, `declined_clarification`, and `confidence`
  with `confidence_method="field_completeness_heuristic"`.
- `IntentQuestion`: `field`, `question` and `reason`.
- `Assumption`: `statement`, `confidence` in [0, 1] and `source` (`explicit`, `inferred` or `fallback`).
- `GoalInput`: the goal schema the contract extends (context, constraints, resource bounds, guardrails, checkpoints,
  breakpoints, subtasks, success criteria), carried as data only.
- `CheckpointSpec`: one declared soft check (name, predicate, params), data only.

## Reading the result

The checklist has five fields, asked in this order: `why`, `success_criteria`, `failure_criteria`, `non_goals`,
`risk_tolerance`. A field counts only when it says something: "TBD", "n/a", "x" or "..." are kept in the contract but
not counted, so their questions stay open. `confidence` is the share of the five fields given, less 0.08 for each
assumption that is not explicit; it is a completeness heuristic, never a semantic or verification score. `ended_by` is
`confidence` only when the threshold was reached. A goal answer that replaces the request, and a free-text risk answer
that names no single level ("anything but high"), are kept as explicit assumptions rather than guessed.

## Alone and composed

It has no block, so over MCP `g6_invoke("intent_engine", ...)` answers `G6_E_NO_BLOCK`;
`g6_describe("intent_engine")` serves this skill. From Python: `from g6.intent_engine import IntentElicitor`.

Composed:

- `reliability_pipeline.run_intake` runs this elicitor on the task, blocks with `G6_E_PIPELINE_NO_ACCEPTANCE` when
  there are still no success criteria, and passes the contract to its feasibility check.
- `workforce` missions hold an `IntentContract`; its acceptance step uses `IntentElicitor`.
- `metaharness` asks the elicitor's open questions about the goal while a workflow is being built.
- `goal_engine.GoalSpec` keeps `GoalInput`'s field names, so a declared goal carries over; `goal_engine` is what reads
  its guardrails, checkpoints and breakpoints against an executor's reports.

## Failure modes it catches

- `missing_user_constraint`: check each mapped success/failure criterion against actual output. Unmapped prose
  has `G6_E_CRITERIA_UNVERIFIABLE`; failed dependencies or unsupported patterns have `G6_E_CRITERION_CHECK_FAILED`.

- `wrong_problem` (solving the wrong problem): it asks for the five things that pin down what the person meant (why,
  observable success, the unacceptable outcome, what is out of scope, the risk accepted), refuses to count a
  placeholder as an answer, and records a goal that replaced the request as an explicit assumption, so the change is
  visible to whoever reads the contract; `interpret` never endorses a request the contract's goal does not match.

## Blind spots

- Typed checks are syntactic, case-sensitive and only as good as their mapping. No semantic privacy, clinical or
  security guarantee is inferred. Regex is limited to literals, classes, anchors and fixed repetitions `{n}` up to
  64; groups, alternatives, backreferences and variable repetition are refused. Patterns cap at 256 characters,
  outputs at 100,000. Plain-language proposals are for a person to map, never automatically activated.

- `interpret` cannot discover every unstated risk. `endorsed` means checklist complete, not permission to act. It
  compares the request with the goal word for word, so a reworded request for the same work also asks to clarify.
  Model or checklist failures block with `G6_E_INTENT_REVIEW_FAILED` or `G6_E_INTENT_FLOOR_FAILED`; no fallback guesses.

- Any answer that "says something" counts, however vague or wrong it is: completeness is not understanding.
- The questions are fixed; nothing follows up on what an answer says.
- The answers must come from the person. An agent that answers its own questions fills the contract and defeats it.
- Risk tolerance and expertise are mapped from free text by whole-word keywords.
- `GoalInput`'s guardrails, resource bounds and breakpoints are data here; nothing in this package enforces them.
- The threshold (0.82) and the penalty (0.08) are not calibrated on outcomes.

## What it needs

Python on pydantic, which installs with g6. Only optional interpret advice uses an injected model caller.

## Examples

Five open questions for a bare request; a complete set of answers closes them; placeholders do not; and a goal answer
that replaces the request is recorded:

```python
from g6.intent_engine import IntentElicitor

elicitor = IntentElicitor()
first = elicitor.elicit("build export")
assert [q.field for q in first.questions] == ["why", "success_criteria", "failure_criteria", "non_goals",
                                              "risk_tolerance"]
assert first.ended_by == "question_budget"

answers = {"why": "customers need reports", "success_criteria": ["CSV downloads with the selected rows"],
           "failure_criteria": ["wrong rows exported"], "non_goals": ["PDF export"], "risk_tolerance": "low"}
done = elicitor.elicit("build export", answers=answers)
assert done.questions == [] and done.ended_by == "confidence"
assert done.contract.confidence_method == "field_completeness_heuristic"

junk = elicitor.elicit("build export", answers={**answers, "why": "TBD", "non_goals": ["x"]})
assert [q.field for q in junk.questions] == ["why", "non_goals"] and junk.ended_by == "incomplete"

swapped = elicitor.elicit("delete the prod db", answers={"goal": "write a haiku"})
assert swapped.contract.goal == "write a haiku"
assert any(a.source == "explicit" and "delete the prod db" in a.statement for a in swapped.contract.assumptions)
```

## The four open problems

- **Learning signals**: Not yet: it writes nothing to a run ledger. The open questions and the explicit assumptions in
  a session are what a caller can record when a task later turns out to have solved the wrong problem.
- **Evaluators the model cannot influence**: the elicitor is deterministic code and calls no model; but the answers it
  counts are whatever the caller passes, so it is only as independent as the person answering.
- **Transfer by failure mode**: the five-field checklist is bound to the failure mode, not to a task: it is the same
  for every request. Not yet: whether it lowers wrong-problem failures has not been measured on any task family.
- **Enough held-out cases**: Not yet: the threshold and the penalty were set by hand, not judged on held-out cases.
