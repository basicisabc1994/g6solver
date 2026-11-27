---
name: using-g6
description: Use when finishing a Python coding task (review the patch with G6 before saying it is done), constructing AI applications from checkable components, investigating repeated failures, testing redesign hypotheses, or reviewing claims and risky actions against evidence.
---

# Using G6

Treat improved performance as a hypothesis. The evaluator, authorization rules and promotion gate remain outside
the candidate's writable scope.

Reach for G6 before finishing a Python task, when constructing AI applications from checkable components,
investigating repeated failures, testing redesign hypotheses, or reviewing claims and risky actions against evidence.
Open only the detail needed for the route you take.

A tool named here that your server does not list is absent: say so, use `g6_find`, and do not call it again.

When a G6 reply carries `repair`, follow its step; if `by` is person, tell the person and stop.

## Routes

- **select.** Unsure which step fits or repairing a refused call: `g6_select`; [read selection and repair steps](references/find.md).
- **question.** Task leaves behaviour undecided: `g6_confirm_behaviour`; [read before asking](references/question.md).
- **mission.** A standard of done, fixed before the work and confirmed by the person: `g6_premortem`, then the `g6_mission_*` tools (`references/lifecycle.md`). [Read the mission steps](references/mission.md).
- **consent.** Sharing or withdrawing sources: `g6_cloud_grant`, `g6_cloud_revoke`, `g6_cloud_grants`, `g6_disclosures`; [read the consent rules](references/consent.md).
- **context.** Index or cite the person's documents: `g6_knowledge_add`, `g6_knowledge_search`, `g6_knowledge_context`; [read context admission and replies](references/context.md).
- **improve.** Self-improvement on a folder: `g6_improve_enrol`, `g6_improve_start`, `g6_improve_resume`, `g6_improve_demote`, `g6_improve_status`, `g6_improve_stop`; [read before enrolling or running](references/improve.md).
- **rules.** Derive answers from facts: `datalog`, `prolog`, `graph_reasoning` through `g6_invoke`; [read engine selection and sessions](references/rules.md).
- **abstraction.** Only where the person registered the separate server, `python -P -m g6.abstraction.server`: its tools search a design space for abstractions that hold up (`references/abstraction.md`). [Read its tool order](references/abstraction.md).
- **workflow.** Local review record, on by default, off when `G6_WORKFLOW_RECORD=off` is set where the server is registered: `g6_workflow_export`, `g6_workflow_correct`, `g6_workflow_delete`, `g6_suggestion_decide`, `g6_feedback`; [read record controls](references/workflow.md).
- **learn.** To report a failure, inspect learned checking methods or ask G6 to adapt itself, read `references/learning.md`. Authority is measured and can be withdrawn; a detector's silence never passes a requirement. [Read the learning steps](references/learning.md).
- **review.** Deliver a Python task: `g6_review_patch`, `metaharness_job`, `g6_review_until_clean`; [read review, repair and proposed checks](references/deliver.md), [each lifecycle step](references/lifecycle.md), and [template bindings and instances](references/templates.md). A review's change map is a lead to check, never evidence or a decision.
- **discover.** Read `g6_allowances` before optional fan-out; `g6_help`, `g6_find`, `g6_describe`, `g6_components`, `g6_invoke`; [read discovery and withheld replies](references/find.md); for `g6_agentic_run`, [read substitution admission and labels](references/override.md).
- **compose.** Connect data-only stages with `g6_compose`; [read field mappings and assurance limits](references/compose.md).
- **fit.** Fit a declared application: `metaharness_start`, `metaharness_confirm`, `metaharness_diagnose`, `metaharness_propose_code`, `metaharness_evaluate_start`, `metaharness_job`, `metaharness_decide`, `metaharness_approve`, `metaharness_run`, `metaharness_inspect`; [read fitting and approval](references/fit.md).

## Read and ask

If host permission is missing, tell the person and stop; never change permissions yourself.
Never call a patch `verified`, and never merge for the person.
Only the person answers; you never answer for them. Never grant for the person.
Never write workflow record files or answer their dialogs for the person.

Preserve `verified`, `qualified-draft` and `blocked-escalated` exactly as returned; `verified` covers only the named
artifact and check scope. Report the warning card and recovery; never turn a refusal into a success.
Ask the person when permissions, task meaning or evidence are missing. Local settings assume a trusted host;
unrestricted same-user shell access defeats that separation.
Patch review and the completion loop need an operating-system boundary: Linux with bubblewrap, or Windows after
the one-time access grant; on macOS they refuse. On `G6_E_ISOLATION_UNAVAILABLE`, tell the person and stop.
On Windows, a check that runs git can fail inside the boundary until the person runs the grants `g6 doctor` prints:
tell the person, never run a grant yourself, and do not rewrite a correct patch.
Superpowers lifecycle skills are optional companions, not prerequisites for using G6.

Loop: in Claude Code with G6's plugin, its Stop hook runs the review-until-clean loop when you finish; elsewhere,
call `g6_review_until_clean` (`references/lifecycle.md`).
By default that hook runs a model-free patch review; the loop runs only after the person approves a reviewer in
their key file. Never add that line yourself.
