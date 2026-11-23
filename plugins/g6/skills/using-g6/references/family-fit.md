# Evaluate and improve applications.

<!-- route:fit -->
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

<!-- /route:fit -->

<!-- route:improve -->
- **improve.** G6's self-improvement loop on a folder. A person agrees in the client's dialog to enrol it
  (`g6_improve_enrol`), start it (`g6_improve_start`), lift a pause (`g6_improve_resume`) or take back a generation
  (`g6_improve_demote`). `g6_improve_status` only reads; `g6_improve_stop` needs no dialog and no permission.
<!-- /route:improve -->

Component families: `ml`.

Use `g6_describe("fit")` for registered operations and call templates. Read the named component with
`g6_describe` before choosing fields. Read `g6_describe("g6_invoke")` before invoking an operation.
The list is guidance, never admission to a sensitive operation or consent to share a source.

- [metaharness/skill/SKILL.md](../../../metaharness/skill/SKILL.md)

## Registered tools

- `mcp__g6__g6_improve_demote`: read its full guidance with g6_describe("g6_improve_demote").
- `mcp__g6__g6_improve_enrol`: read its full guidance with g6_describe("g6_improve_enrol").
- `mcp__g6__g6_improve_resume`: read its full guidance with g6_describe("g6_improve_resume").
- `mcp__g6__g6_improve_start`: read its full guidance with g6_describe("g6_improve_start").
- `mcp__g6__g6_improve_status`: read its full guidance with g6_describe("g6_improve_status").
- `mcp__g6__g6_improve_stop`: read its full guidance with g6_describe("g6_improve_stop").
- `mcp__g6__metaharness_approve`: read its full guidance with g6_describe("metaharness_approve").
- `mcp__g6__metaharness_confirm`: read its full guidance with g6_describe("metaharness_confirm").
- `mcp__g6__metaharness_decide`: read its full guidance with g6_describe("metaharness_decide").
- `mcp__g6__metaharness_diagnose`: read its full guidance with g6_describe("metaharness_diagnose").
- `mcp__g6__metaharness_evaluate`: read its full guidance with g6_describe("metaharness_evaluate").
- `mcp__g6__metaharness_evaluate_start`: read its full guidance with g6_describe("metaharness_evaluate_start").
- `mcp__g6__metaharness_inspect`: read its full guidance with g6_describe("metaharness_inspect").
- `mcp__g6__metaharness_job`: read its full guidance with g6_describe("metaharness_job").
- `mcp__g6__metaharness_propose`: read its full guidance with g6_describe("metaharness_propose").
- `mcp__g6__metaharness_propose_code`: read its full guidance with g6_describe("metaharness_propose_code").
- `mcp__g6__metaharness_rollback`: read its full guidance with g6_describe("metaharness_rollback").
- `mcp__g6__metaharness_run`: read its full guidance with g6_describe("metaharness_run").
- `mcp__g6__metaharness_start`: read its full guidance with g6_describe("metaharness_start").
