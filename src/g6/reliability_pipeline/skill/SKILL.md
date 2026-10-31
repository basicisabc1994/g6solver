---
name: g6-reliability-pipeline
description: Use when non-trivial work is about to start, to check that the task has measurable acceptance criteria, to hold a risky or destructive task until the request says its risk is accepted, to run the premortem checklist of how the work can fail, and to critique a plan for missing tests or rollback.
---

# G6 reliability pipeline

`g6.reliability_pipeline` is the front of G6's reliability pipeline: task intake (acceptance criteria and a
rule-based feasibility triage), the premortem checklist of thirteen failure modes, a structural plan critique, one
curated failure-mode skill and its bounded replay-check constructor. Its rules are deterministic and call no model;
an assessor you inject may only tighten them.

## When to use it

- You are about to start work and the request has no clear test of done, or only a placeholder ("TBD", "x").
- The task may delete, wipe, pay, deploy or touch secrets or live systems, and you want it held until the request
  carries `accepted_risky_intent` (a flag the caller sets; G6 records it as the caller's word, not a person's).
- You are writing a plan and want each of the thirteen premortem questions answered in it.
- You have a plan and want to know whether it names a goal, acceptance criteria, tests and a rollback.
- The objective talks about retries, duplicates or idempotency, and you want the replay question asked first.

## When not to use it

- Checking a specific risky action or a finished answer: use `coding_agent_gate` (`preflight`, `verify_answer`) or
  `action_gating` (`evaluate`).
- Freezing what counts as done for a coding change and checking the patch: use `g6.workforce`, which uses this
  package's replay constructor.
- Finding the user's intent in an open conversation: use `intent_engine`, which `run_intake` already calls.
- Judging whether a plan will work: a structural pass says only that the parts are there.

## Python API

- `run_intake`: stage 0. Reads `goal` (or `task`), `acceptance_criteria` (or `constraints`), `intent_answers`,
  `context_sources`, `accepted_risky_intent`, `declined_clarification`, `task_mode` and
  `success_criteria_structured` from the request; returns a `StageResult`.
- `StageResult`: `stage`, `state`, `reason` (a `G6_E_PIPELINE_*` code when blocked), `safe_next_action` and
  `metadata` (`task_contract`, `feasibility_report`, `intent_questions`, `components_used`, and `risk_acceptance`
  when a risky task proceeds on `accepted_risky_intent`).
- `StageState`: `advance`, `downgrade` or `block`.
- `Stage`: the pipeline's stage names, from intake to learning.
- `FeasibilityAssessor`: the rule-based triage (`assess(contract)`); returns a `FeasibilityReport`.
- `feasibility.FeasibilityReport`: `classification`, `assumptions`, `blockers`, `risk_level`, `signals` and the
  reduction-to-practice questions.
- `intake.IntakeContract`: the task, its criteria, mode, intent and `escalation_triggers`, carried in
  `metadata["task_contract"]`.
- `premortem.premortem_failure_mode_questions`: each premortem failure-mode code with its diagnostic question.
- `premortem.PREMORTEM_FAILURE_MODES`: the thirteen codes, in checklist order.
- `premortem.hypotheses`: those thirteen and six ways a code change can fail, each with what would test it, ranking
  none (`g6_premortem` returns them over MCP).
- `relevant`: the curated failure-mode skills whose trigger words appear in an objective.
- `SKILLS`: the curated skills (today one: `event-replay-semantics.v1`).
- `replay_question`: the one clarification question for a vague "make this event handler idempotent" request.
- `construct_replay_check`: builds a replay check from one fixed requirement sentence and an executed test, or
  returns `None`.
- `plan.Plan`: a plan's `goal`, `steps`, `acceptance_criteria`, `risks`, `tests` and `rollback`.
- `plan.critique`: the structural check plus a word tripwire; a `CritiqueReport` whose `verdict` is `pass`, `revise`
  or `block`.
- `plan.merge_reports`: merges another critique (a reviewer's or a model's) into the floor; it can only tighten.
- `distill_run`: stores a finished run's experience in an `ExperienceStore` you pass, only for a run it knows.
- `reconsume`: recalls stored experiences for a task signature, each cited by its run; disclosure only.
- `ExperienceStore`: the protocol a caller's experience store implements (`has_run`, `add`, `similar`).
- `Experience`: one stored run: `run_id`, `signature`, `outcome`, `reward`, `trace`.

## Alone and composed

Alone, from Python only (no block, so no `g6_invoke` op):
`from g6.reliability_pipeline import run_intake, relevant` and
`from g6.reliability_pipeline.premortem import premortem_failure_mode_questions`.

Composed:

- `intent_engine`: `run_intake` runs its `IntentElicitor` on the goal; the questions it returns become the
  clarification block, and its contract is what feasibility reads.
- `g6.workforce`: a coding mission calls `construct_replay_check` when an objective states the replay requirement,
  and shows the constructed check to the person who confirms the standard.
- `coding_agent_gate`: after intake advances, `preflight` checks the concrete risky action; after the work, pass the
  acceptance criteria intake recorded as `verify_answer`'s `task_contract`.
- `plan.merge_reports`: a reviewer's or a model's `CritiqueReport` joins the structural floor and can only make the
  verdict stricter.

## Failure modes it catches

Each code has its question in the premortem checklist, under the same code. Where the code does more than ask, it
says so here.

- `unclear_success` (work started with no measurable test of success): intake blocks with
  `G6_E_PIPELINE_NO_ACCEPTANCE` when no criterion is given; a placeholder ("TBD", "x") is not a criterion, whether it
  comes in `acceptance_criteria`, `constraints` or `intent_answers["success_criteria"]`. The plan critique reports
  `missing_success_criteria`.
- `context_loss` (facts or decisions lost between the steps of one task): a request that names context it does not
  carry is blocked as `blocked_missing_context`; attaching `context_sources` resolves that and nothing else.
- `unsafe_autonomous_action` (an agent taking a harmful or irreversible action nobody approved): the tripwire marks a
  task `risky` when it names an access term (`password`, `credential`, `private key`), a high-stakes word (`delete`,
  `payment`, `production`, `medical`, `legal`, `safety critical`), a destructive one (`wipe`, `drop table`,
  `destroy`, `purge`, `rm -rf`, `force push`), an absolute guarantee, or two risk axes together (irreversible, live
  system, external or sensitive), and intake blocks it with `G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED`
  until the request carries `accepted_risky_intent`. It then proceeds as a `downgrade`, never an `advance`, and
  `metadata["risk_acceptance"]` says the acceptance is the caller's word (`accepted_by: caller`,
  `authenticated: false`).
- `unverified_reliability` (completion claimed with nothing named that would prove it): the plan critique reports
  `missing_evaluation` when no test is named; a criterion marked critical in `success_criteria_structured` with no
  available verifier becomes an escalation trigger.
- `missing_domain_standard` (work in a specialist field started without that field's evidence standard): the
  tripwire's words `medical` and `legal` make a task `risky`; otherwise the question only.
- `wrong_risk_tier` (harm or irreversibility underestimated): tripwire signals and stated figures raise the risk band
  (at least `medium` with a time bound); an injected assessor may tighten the verdict, never relax it.
- `budget_overrun` (a task overrunning its token, cost or time budget): a stated time bound or cost constraint is
  recorded as the request's own, unchecked assumption; sweeping scope gives `too_large`.
- `unavailable_tool` (a plan that relies on a tool that is not there): the question; only an injected assessor can
  answer `blocked_missing_access`.
- `hidden_assumption` (an unstated assumption that would change the answer if false): the report lists each
  assumption with who stated it.
- `source_gap` (claims that need sources and have none): the question only.
- `verification_gap` (output that no independent check looks at): the question, and the escalation trigger for a
  structured critical criterion without a verifier.
- `handoff_gap` (a result handed over without saying how far to rely on it): every block carries its reason and a
  `safe_next_action`; the escalation triggers say when to stop at `qualified-draft`.
- `learning_gap` (a failure never recorded, so nothing learns from it): the question; `distill_run` records a run
  when you call it.

## Blind spots

- The tripwires are word lists. Their silence never means a task is harmless: a recorded test shows the feasibility
  tripwire misses high-stakes tasks reworded to avoid its words, and it holds harmless tasks that use them.
- The clarify gate is on unless `G6_PIPELINE_CLARIFICATION` is `0`, `false`, `no` or `off`; a clear task advances
  even when the intent is incomplete, and the elicitor's questions are not returned on that path.
- `accepted_risky_intent` is a boolean on the request: G6 cannot tell whether a person set it or the agent did. It
  records it as the caller's word and holds the task at `qualified-draft` at most; hand the decision to the person.
- `IntakeContract.mode`, its structured criteria and its escalation triggers are carried for the caller; nothing in
  this package enforces them later.
- The plan critique checks presence and placeholders, not whether the plan works; its best state is
  `qualified-draft`.
- The replay constructor is two fixed templates, each matched by one requirement sentence (or a relation a person
  confirmed); a reworded requirement gets no check, and what it builds can refute a change, never support it.
- `relevant` matches whole trigger words; there is one curated skill, and its performance is recorded as
  `unmeasured`.

## What it needs

Nothing outside G6: deterministic Python rules, with `intent_engine` from G6 itself.

## Examples

Intake advances a clear task with a criterion, blocks a placeholder and holds a destructive task; the premortem has
its thirteen questions; the plan critique passes a complete plan and stops an empty one.

```python
from types import SimpleNamespace

from g6.reliability_pipeline import StageState, relevant, run_intake
from g6.reliability_pipeline.plan import Plan, critique
from g6.reliability_pipeline.premortem import premortem_failure_mode_questions

ok = run_intake(SimpleNamespace(goal="add CSV export", intent_answers={
    "why": "users need reports", "success_criteria": ["selected rows download"]}))
assert ok.state == StageState.ADVANCE and ok.metadata["feasibility_report"].classification == "clear"

vague = run_intake(SimpleNamespace(goal="ship feature", acceptance_criteria=["TBD"]))
assert (vague.state, vague.reason) == (StageState.BLOCK, "G6_E_PIPELINE_NO_ACCEPTANCE")

risky = run_intake(SimpleNamespace(goal="delete production payment records", intent_answers={
    "why": "cleanup", "success_criteria": ["records removed"]}))
assert risky.reason == "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED"

questions = premortem_failure_mode_questions()
assert len(questions) == 13 and "unclear_success" in questions
assert [s.failure_id for s in relevant("make the webhook handler idempotent under retries")] == [
    "event-replay-semantics.v1"]

plan = Plan(goal="Add a timeout setting to the HTTP client", steps=["add the field"],
            acceptance_criteria=["the loader reads the new key"], tests=["tests/test_config.py passes"],
            rollback="git revert the commit")
assert critique(plan).completion_state == "qualified-draft"
assert critique(Plan(goal="Fix it")).completion_state == "blocked-escalated"
```

## The four open problems

- **Learning signals**: the `learning_gap` question asks for it, and `distill_run` stores a finished run's outcome
  in a store you pass, only for a run that store knows; `reconsume` gives them back as disclosure only. Not yet:
  intake, the premortem and the plan critique write nothing to a ledger.
- **Evaluators the model cannot influence**: every verdict is G6's own deterministic rules; no model is called. An
  injected assessor or a merged critique can only tighten (`at_least`, `merge_reports`).
- **Transfer by failure mode**: the checklist is keyed by failure mode, not by task, so its questions apply to any
  task. Not yet: no catch rate measured on task families the rules were not written for.
- **Enough held-out cases**: Not yet: nothing here measures its own catch or false-alarm rate on held-out cases;
  the tripwire's misses are recorded in a test, not judged by a sequential test.
