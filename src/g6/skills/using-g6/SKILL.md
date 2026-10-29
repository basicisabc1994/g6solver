---
name: using-g6
description: Use when finishing a Python coding task (review the patch with G6 before saying it is done), constructing AI applications from checkable components, investigating repeated failures, testing redesign hypotheses, or reviewing claims and risky actions against evidence.
---

# Using G6

G6 is a library of primitives for AI building AI. An agent can construct an application, propose a redesign,
and measure it against frozen checks. Treat improved performance as a hypothesis, never a consequence of composition.
The evaluator, authorization rules and promotion gate remain outside the candidate's writable scope.

## Deliver a coding task with G6

For a Python task, work in this order, lean in words.

1. **Intent.** Read the task, the project's docs and the code. Write the person's most likely intended design and
   goals in a few lines. Pass them as `intent`, or as numbered `requirements`, `{"id": "R1", "text": ..., "checks":
   [...]}`, each check a pytest node id.
2. **This project's risks**, a premortem, not a generic list: edge cases, behaviour that must stay the same, who may do
   what, data or money that must never be lost, state that must never go wrong, tests too weak to catch a defect, a
   misleading environment, and G6 misreading the intent. Map each risk to a check: a repository test, a new
   acceptance test, or one of G6's templates in `template_instances`: `example.v1` (a stated input gives a stated
   result), `relation.v1` (round trip, idempotence, invariance or monotonicity), `access_rule.v1` (who may do what;
   with `steps`, grant, share and revoke), `differential.v1` (nothing changes outside the change's scope),
   `forbidden_dependency.v1` (layer rules) or `state_invariant.v1` (critical state).
3. **Hidden constraints.** Ask the person at most 3 questions, each a concrete scenario with the competing outcomes:
   "for this input the original does A and the change would do B; which do you intend?" Probe the code for the rest.
   Never assume an answer: an unanswered question stays open in the report.
4. **Acceptance first, then test first.** Write acceptance tests for what the person's own users must be able to do,
   through the product's real interface (its API, command line or handlers), and for usability and fitness for purpose
   wherever a test can show it. They become the numbered requirements, each with its stated check. Then unit tests,
   then the code.
5. **Critical parts, formally.** Model them with `state_invariant.v1` (its `model` states the variables, actions and
   invariants; its `actions` bind them to the code's operations; its `observe` reads each variable from the real
   state). G6 searches the model and replays each trace it finds on the real code. A result about the model is about the model only; only a reproduced trace is a finding
   about the code. A model never proves the code correct.
6. **Review.** Call `g6_review_patch` with the requirements and their stated checks, the template instances and any
   `proposed_checks` of your own, then poll `metaharness_job`. It needs host permission `patch_review.review`, which
   the person grants with `g6 setup`; never change permissions yourself. Show the reply's `assessment["report"]`
   verbatim.
7. **Repair.** Fix what G6 reproduced. Add each earlier reproduction as a stated check (a proposed check's
   reproduction is a test file: add it to the repository's tests and list its node id), so the person sees the fix
   pass the original counterexample. Review again. Stop after 3 rounds, when a round changes nothing, or when the
   report says only the person can decide.
8. **Honesty.** Never call a patch verified, and never merge for the person. G6 cannot judge usability by itself;
   acceptance tests and the person's confirmation do. The report's not-established list is the to-do list. For
   specialised needs, find more components with `g6_find` (formal tools, grounding of claims).

The outcome is one of: test integrity compromised, defect detected, insufficient evidence, or supported within scope
(every stated requirement passed its stated checks; never proof the patch is complete).

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
