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
