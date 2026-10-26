---
name: using-g6
description: Use when the G6 library or its g6 MCP server is available and the work changes code, makes claims that must be right, acts outside the conversation (deploys, sends, pays, promises), needs exact computation, or concerns an AI workflow that keeps making the same mistakes
---

# Using G6

## Overview

G6 is a failure-engineering harness. Each component closes one way AI work fails, and the components stack like slices
of Swiss cheese. The model proposes; G6 checks; a person decides what is irreversible. G6 is not one "review" call at
the end. Each point of the work has its own G6 step, and the table below is the map.

## The G6 step at each point of the work

| When | G6 step | How |
|---|---|---|
| Before non-trivial work | **Premortem:** name how this fails, and the guard for each failure | Python: `g6.reliability_pipeline.premortem.premortem_failure_mode_questions()` returns the premortem's questions, one per failure mode. Answer each one in your plan. Then `from g6.reliability_pipeline import relevant, run_intake`: `relevant(objective)` returns the curated failure-mode skills that match the task, each with the decision you must ask the person |
| Before a risky change (payments, auth or security, data migration, deploy, deletion, medical or legal logic, skipping a check) | **Preflight** | `g6_invoke("coding_agent_gate", "preflight", {"task", "proposed_action", "risk_tier", "constraints", "evidence"})` returns block, needs_review, needs_evidence or proceed_with_caution |
| Before an agent acts outside the conversation (sends, posts, pays, books, promises a refund) | **Action gate** | `g6_invoke("action_gating", "evaluate", ...)` returns go, ask a person, or block. Gate the action itself; a home-made keyword filter is not a gate |
| The answer must be exact | **Hand it to a checker, never to your head** | Logic → `formal` (`check_entailment`, `find_counterexample`)<br>Algebra and calculus → `algebra`<br>Schedules, routes, assignments → `optimisation` (`solve_*`)<br>Rules → `datalog`<br>Cause and effect → `causal`<br>State machines and protocols → `model_checking`<br>Proofs → `lean` |
| A claim must rest on a source | **Ground it** | `grounding` (`check_claims` against the passages); `epistemic_integrity` (`adjudicate`) for a high-stakes conclusion |
| Before saying "done" | **Verify the answer** | `g6_invoke("coding_agent_gate", "verify_answer", {"task", "answer", "task_contract", "evidence", "verifier", "claims"})` returns block, needs_review, revise or pass. For a standard of done a person confirms first: a `g6.workforce` mission (`create_coding_mission`, `confirm`, `submit_patch`, `certificate`) |
| A task you repeat keeps failing | **Tune it on held-out cases** | Declare the workflow in Python (`g6.metaharness`: `Workflow`, `Suite(dev=..., held_out=...)`, `adapt`; a person confirms it). Then use `metaharness_diagnose`, `metaharness_propose`, `metaharness_evaluate` and `metaharness_decide`.<br>**Hand the person the decision digest and stop.** They approve from their own session, from Python (`activate(decision, accepted_by=...)`) or from the terminal. `metaharness_approve` refuses the session that proposed the change.<br>Learned hints from failures: `g6.learning` |

## Calling G6

1. `g6_help()` once.
2. `g6_describe(component)` gives the ops and their exact fields. Copy the field names from it; never send
   placeholder parameters. The two shapes you will use most:
   - `evidence` is a list of `{"kind": "file|test|log|diff|review", "ref": "path or command", "output": "...", "exit_code": 0}`,
     with `"reviewer"` for kind `review`;
   - `verifier` is `{"status": "pass|fail|not_run", "exit_code": 0}`.
3. `g6_invoke(component, op, params_json)` runs one op.
4. `g6_find(query)` is for needs the table does not cover. An empty result means "not this query", not "skip G6".

Some components are reached from Python only, among them `workforce`, `metaharness` (beyond its MCP tools),
`reliability_pipeline`, `navigator` (`check_plan` before running a plan of G6 calls), `learning`, `guards` (prompt
injection) and `safety_engine` (run limits).

A sensitive op that is refused needs the person who runs the server to allow it (`G6_MCP_SENSITIVE`). Ask them. Never
edit the MCP configuration yourself.

## Reading what comes back

- `completion_state`:
  - `verified` only when an independent check G6 ran could have failed and passed;
  - `qualified-draft` means the check ran but your evidence or the method limits it;
  - `blocked-escalated` means stop. Tell the user the `warning_card` code and what unblocks it.
- **Report the label as given.** Never upgrade it. "All tests pass" without a run you can show is not a claim you can
  make.
- **What you send G6 counts as caller-declared.** Declare only what you ran or read. Your own test run supports a
  claim; it is not independent proof.
- **A refused meta-harness decision carries `recovery`:** why it was refused, and what to try next. Pass it on.

## Common mistakes

| Mistake | Instead |
|---|---|
| One G6 "second opinion" at the end, with guessed op names | The step at each point, with fields from `g6_describe` |
| Skipping G6 because `g6_find` returned nothing | Use the table first; it names the component |
| Writing your own filter for promises, payments or sends | `action_gating.evaluate` before the action, then keep your filter as an extra slice |
| Tuning a prompt by hand and judging it on cases you looked at | A declared workflow with a held-out suite; the meta-gate judges, a person approves |
| Guessing whether the sample is big enough | The meta-gate needs at least 30 held-out cases, 5 where base and candidate differ, and an exact sign test; `baseline()` gives a power estimate |
| Calling `metaharness_approve` from the session that proposed the change, or naming the approver | It is refused (`G6_E_META_APPROVER_IS_PROPOSER`). Give the person the decision digest; they approve elsewhere |

## For coding agents: the full lifecycle

**REQUIRED:** load `superpowers:using-superpowers` as well, and follow its skills. `references/lifecycle.md` pairs
each stage with its superpowers skill and its G6 step:

- brainstorm and intent;
- premortem and plan;
- worktree;
- test first;
- debug;
- verify;
- review;
- finish with a certificate;
- learn.
