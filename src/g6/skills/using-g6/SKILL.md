---
name: using-g6
description: Use when finishing a Python coding task (review the patch with G6 before saying it is done), constructing AI applications from checkable components, investigating repeated failures, testing redesign hypotheses, or reviewing claims and risky actions against evidence.
---

# Using G6

G6 is a library of primitives for AI building AI. An agent can construct an application, propose a redesign,
and measure it against frozen checks. Treat improved performance as a hypothesis, never a consequence of composition.
The evaluator, authorization rules and promotion gate remain outside the candidate's writable scope.

## Before you say a coding task is done

For a Python patch, call `g6_review_patch` before you tell the person the task is done. It needs host permission
`patch_review.review`, which the person grants with `g6 setup`; never change permissions yourself.

1. Turn the task into numbered requirements, `{"id": "R1", "text": ..., "checks": [...]}`, each with the pytest
   node ids that check it where such a test exists. A requirement with no check stays not established; say so.
2. Call `g6_review_patch` with the repository's folder, the task and the requirements, then poll `metaharness_job`
   until the review ends.
3. Show the person the reply's `assessment["report"]` verbatim. Do not summarise it into a better result.

Then the repair loop:

- Fix what G6 reproduced: each failed check, each test of the changed code that now fails, each counterexample.
- On the next call, add each earlier reproduction as a stated check of its requirement (a proposed check's
  reproduction is a test file: add it to the repository's tests and list its node id), so the person can see the fix
  passes the original counterexample.
- Review again, and show the new report.
- Stop after 3 rounds, when a round changes nothing, or when the report says only the person can decide.

The outcome is one of: test integrity compromised, defect detected, insufficient evidence, or supported within scope
(every stated requirement passed its stated checks; never proof the patch is complete). Never call a patch verified
or correct, and never merge it: merging is the person's decision, made separately.

## Find

Call `g6_help`, then `g6_find` with the task or failure mode. Read `g6_describe` before choosing fields or an operation.
Use grounding for source support, coding_agent_gate for declared coding evidence, action_gating before consequential
actions, and the meta-harness for repeated failures. Python-only components require their library APIs.

For a direct component call, these examples show the parameter object; serialize it into `params_json`:

```text
g6_invoke("grounding", "check_claim", {"claim": "The limit is 42 units.", "sources": sources})
g6_invoke("grounding", "check_claims", {"text": "The limit is 42 units.", "sources": sources})
g6_invoke("grounding", "coverage", {"text": "The limit is 42 units.", "sources": sources})
```

Here `sources` maps source names to supplied text. These are lexical source checks, not proof of factual truth.

## Compose

Use `g6_compose` with data-only stages. Connect downstream fields explicitly with `0.field` or `caller.field`.
Evidence kinds must match; similar field names do not establish equivalent meaning. Code transformations require
reviewed Python adapters. A contract-checked report establishes structural checks, not application correctness.
Plain `>>` does not check contracts. Read the assurance gaps and preserve all upstream limitations.

## Fit

Use `metaharness_start` to create a data-only application with a task, labelled cases, frozen checks and baseline
Python code. Confirm the workflow digest through `metaharness_confirm`. Then diagnose, propose_code with a hypothesis,
evaluate_start, poll `metaharness_job`, decide, present the client's approval prompt, and run the approved version.
The full tool names begin `metaharness_`. This is Propose -> Test -> Gate -> Approve, with explicit recovery after failure.

Below 30 held-out cases is exploration only. A split of answers the proposer already saw is not independent evidence.
Generated-code activation also requires a host-configured safety/injection evaluator. Never invent its observations.
Client-mediated consent may occur in the proposing session; the recorded name is not authenticated. If elicitation
is unavailable, relay the terminal command. A job ID is pending work, not success; restart loses process-local handles.

## Read and ask

Preserve `verified`, `qualified-draft` and `blocked-escalated` exactly as returned. Verification covers only the named
artifact and check scope. Report the warning card and recovery; never turn a refusal into a successful tuning result.

Ask the user when permissions, task meaning or evidence are missing. Local settings assume a trusted host;
unrestricted same-user shell access defeats that separation. Do not change permissions to make a test pass.
Superpowers lifecycle skills are optional companions, not prerequisites for using G6.
