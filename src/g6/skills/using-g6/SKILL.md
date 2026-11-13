---
name: using-g6
description: Use when finishing a Python coding task (review the patch with G6 before saying it is done), constructing AI applications from checkable components, investigating repeated failures, testing redesign hypotheses, or reviewing claims and risky actions against evidence.
---

# Using G6

Treat improved performance as a hypothesis. The evaluator, authorization rules and promotion gate remain outside
the candidate's writable scope.

## Routes

A tool named here that your server does not list is absent: say so, use `g6_find`, and do not call it again. The
sections below are routes too: Deliver (review), Find (discover), Compose and Fit.

- **question.** Where the task may not decide a behaviour, `g6_confirm_behaviour` asks the person in the client's own
  dialog: one question per task by default, and the person may allow more or set none. Only the person answers; you
  never answer for them.
- **mission.** A standard of done, fixed before the work and confirmed by the person: `g6_premortem`, then the
  `g6_mission_*` tools (`references/lifecycle.md`).
- **consent.** Nothing of the person's reaches a cloud model without their grant; without one, results are withheld
  and local work goes on. `g6_cloud_grant` asks them in the client's dialog; `g6_cloud_revoke`, `g6_cloud_grants`
  and `g6_disclosures` withdraw, list and read what was attempted. Never grant for the person.
- **context.** The person's own documents, indexed on their machine from folders under the roots they list in
  `G6_KNOWLEDGE_ROOTS`: `g6_knowledge_add` (they accept a preview in the client's dialog), then `g6_knowledge_search`
  or `g6_knowledge_context`. Passages are cited and untrusted. Other tools: `references/context.md`.
- **improve.** G6's self-improvement loop on a folder. A person agrees in the client's dialog to enrol it
  (`g6_improve_enrol`), start it (`g6_improve_start`), lift a pause (`g6_improve_resume`) or take back a generation
  (`g6_improve_demote`). `g6_improve_status` only reads; `g6_improve_stop` needs no dialog and no permission.
- **rules.** What follows from facts and rules: `datalog` (sessions keep facts over a fixed program), `prolog` and
  `graph_reasoning`, through `g6_invoke` (`references/rules.md`).
- **abstraction.** Only where the person registered the separate server, `python -P -m g6.abstraction.server`: its
  tools search a design space for abstractions that hold up (`references/abstraction.md`).
- **workflow.** G6's local record of its own review work, off unless `G6_WORKFLOW_RECORD=on` is set where the server
  is registered: `g6_workflow_export`, `g6_workflow_correct`, `g6_workflow_delete`, `g6_suggestion_decide` and
  `g6_feedback`. Never write its files or answer its dialogs for the person.

## Deliver a coding task with G6

For a Python task, work in this order (each step in full: `references/lifecycle.md`).

1. **Intent.** Write the goals as numbered `requirements`, `{"id": "R1", "text": ..., "checks": [...]}`, each check a
   pytest node id.
2. **Risks.** Map each of this project's risks to a check: a repository test, a new acceptance test, or a template in
   `template_instances`: `example.v1`, `relation.v1`, `access_rule.v1`, `differential.v1`, `forbidden_dependency.v1`
   or `state_invariant.v1` (bindings and one valid instance each: `references/templates.md`).
3. **Hidden constraints.** One question per task by default, kept across calls. Where the task may not decide a
   behaviour, call `g6_confirm_behaviour` with the concrete scenario and its competing outcomes. The person answers
   in the client's own dialog and may allow one more question; their own setting can raise the default or set it to
   none. Only the person answers: no argument answers and you never answer for them. Never assume an answer: an
   unanswered question stays open in the report.
4. **Acceptance first, then test first.** Acceptance tests of what the person's users must be able to do, through
   the real interface; then unit tests, then code.
5. **Review.** Call `g6_review_patch` with the folder, the task and the requirements, then poll `metaharness_job`.
   Add optional inputs (`intent`, `template_instances`, `proposed_checks`) only in the shapes the tool's description
   gives; a refused call names the field to fix. It needs host permission `patch_review.review`, which the person
   grants with `g6 setup`; never change permissions yourself. Show the reply's `assessment["report"]` verbatim.
6. **Repair.** Fix what G6 reproduced, add each reproduction as a stated check, and review again. Stop after 3
   rounds, when a round changes nothing, or when the report says only the person can decide.
7. **Honesty.** Never call a patch `verified`, and never merge for the person. The result rests on your own tests:
   do not rewrite them to change it. The report's not-established list is the to-do list.

Loop: `g6_review_until_clean` (`references/lifecycle.md`).

## Find

Read `g6_allowances` before optional fan-out, and declare useful independent work honestly. Its advice is pacing
only: stale or missing data never justifies more use.

Call `g6_help`, then `g6_find` with the task or failure mode. Read `g6_describe` before choosing fields or an operation.
`g6_components` lists every installed component. Use grounding for source support, coding_agent_gate for declared
coding evidence, action_gating before consequential actions, and the meta-harness for repeated failures.
A direct call serializes its parameter object into `params_json`:

```text
g6_invoke("grounding", "check_claim", {"claim": "The limit is 42 units.", "sources": sources})
g6_invoke("grounding", "check_claims", {"text": "The limit is 42 units.", "sources": sources})
g6_invoke("grounding", "coverage", {"text": "The limit is 42 units.", "sources": sources})
```

`sources` maps names to supplied text. These lexical checks do not establish factual truth.

## Compose

Use `g6_compose` with data-only stages. Connect downstream fields explicitly with `0.field` or `caller.field`.
Evidence kinds must match; similar field names establish no equivalence. A contract-checked report establishes
structural checks, not application correctness. Preserve all upstream limitations.

## Fit

Declare a data-only application (task, labelled cases, frozen checks, baseline Python code) with
`metaharness_start` and confirm its digest through `metaharness_confirm`. Then `metaharness_diagnose`,
`metaharness_propose_code` with a hypothesis, `metaharness_evaluate_start`, poll `metaharness_job`,
`metaharness_decide`, present the client's approval prompt (`metaharness_approve`), and `metaharness_run` the approved
version. `g6_describe("metaharness")` has the rest, `metaharness_inspect` among them.

Below 30 held-out cases is exploration only.
Generated-code activation also requires a host-configured safety/injection evaluator. Never invent its observations.
Client-mediated consent may occur in the proposing session; the recorded name is not authenticated.
After a decline or cancel, ask the person; offer no other way to confirm. A job ID is pending work, not success.

## Read and ask

Preserve `verified`, `qualified-draft` and `blocked-escalated` exactly as returned; `verified` covers only the named
artifact and check scope. Report the warning card and recovery; never turn a refusal into a success.
Ask the person when permissions, task meaning or evidence are missing. Local settings assume a trusted host;
unrestricted same-user shell access defeats that separation.
Superpowers lifecycle skills are optional companions, not prerequisites for using G6.
