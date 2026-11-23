# Plan and build changes.

<!-- route:mission -->
- **mission.** A standard of done, fixed before the work and confirmed by the person: `g6_premortem`, then the
  `g6_mission_*` tools (`references/lifecycle.md`).
<!-- /route:mission -->

<!-- route:question -->
- **question.** Where the task may not decide a behaviour, `g6_confirm_behaviour` asks the person in the client's own
  dialog: one question per task by default, and the person may allow more or set none. Only the person answers; you
  never answer for them.
<!-- /route:question -->

Treat improved performance as a hypothesis. The evaluator, authorization rules and promotion gate remain outside
the candidate's writable scope.

Component families: `code`, `workspace`.

Use `g6_describe("engineering")` for registered operations and call templates. Read the named component with
`g6_describe` before choosing fields. Read `g6_describe("g6_invoke")` before invoking an operation.
The list is guidance, never admission to a sensitive operation or consent to share a source.

- [skills/using-g6/references/lifecycle.md](lifecycle.md)

## Registered tools

- `mcp__g6__g6_confirm_behaviour`: read its full guidance with g6_describe("g6_confirm_behaviour").
- `mcp__g6__g6_mission_certificate`: read its full guidance with g6_describe("g6_mission_certificate").
- `mcp__g6__g6_mission_clarify`: read its full guidance with g6_describe("g6_mission_clarify").
- `mcp__g6__g6_mission_confirm`: read its full guidance with g6_describe("g6_mission_confirm").
- `mcp__g6__g6_mission_create`: read its full guidance with g6_describe("g6_mission_create").
- `mcp__g6__g6_mission_status`: read its full guidance with g6_describe("g6_mission_status").
- `mcp__g6__g6_mission_submit`: read its full guidance with g6_describe("g6_mission_submit").
- `mcp__g6__g6_premortem`: read its full guidance with g6_describe("g6_premortem").
