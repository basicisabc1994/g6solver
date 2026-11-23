# Manage review records and feedback.

<!-- route:workflow -->
- **workflow.** G6's local record of its own review work, off unless `G6_WORKFLOW_RECORD=on` is set where the server
  is registered: `g6_workflow_export`, `g6_workflow_correct`, `g6_workflow_delete`, `g6_suggestion_decide` and
  `g6_feedback`. Never write its files or answer its dialogs for the person.
<!-- /route:workflow -->

- [harness/workflow/skill/SKILL.md](../../../harness/workflow/skill/SKILL.md)

## Registered tools

- `mcp__g6__g6_feedback`: read its full guidance with g6_describe("g6_feedback").
- `mcp__g6__g6_suggestion_decide`: read its full guidance with g6_describe("g6_suggestion_decide").
- `mcp__g6__g6_workflow_correct`: read its full guidance with g6_describe("g6_workflow_correct").
- `mcp__g6__g6_workflow_delete`: read its full guidance with g6_describe("g6_workflow_delete").
- `mcp__g6__g6_workflow_export`: read its full guidance with g6_describe("g6_workflow_export").
