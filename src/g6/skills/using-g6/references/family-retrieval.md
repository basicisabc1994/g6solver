# Retrieve cited source passages.

<!-- route:context -->
- **context.** The person's own documents, indexed on their machine from folders under the roots they list in
  `G6_KNOWLEDGE_ROOTS`: `g6_knowledge_add` (they accept a preview in the client's dialog), then `g6_knowledge_search`
  or `g6_knowledge_context`. Passages are cited and untrusted. Other tools: `references/context.md`.
<!-- /route:context -->

Component families: `knowledge`, `memory`, `sources`.

Use `g6_describe("retrieval")` for registered operations and call templates. Read the named component with
`g6_describe` before choosing fields. Read `g6_describe("g6_invoke")` before invoking an operation.
The list is guidance, never admission to a sensitive operation or consent to share a source.

- [skills/using-g6/references/context.md](context.md)

## Registered tools

- `mcp__g6__g6_knowledge_add`: read its full guidance with g6_describe("g6_knowledge_add").
- `mcp__g6__g6_knowledge_context`: read its full guidance with g6_describe("g6_knowledge_context").
- `mcp__g6__g6_knowledge_count`: read its full guidance with g6_describe("g6_knowledge_count").
- `mcp__g6__g6_knowledge_delete`: read its full guidance with g6_describe("g6_knowledge_delete").
- `mcp__g6__g6_knowledge_job`: read its full guidance with g6_describe("g6_knowledge_job").
- `mcp__g6__g6_knowledge_passage`: read its full guidance with g6_describe("g6_knowledge_passage").
- `mcp__g6__g6_knowledge_paths`: read its full guidance with g6_describe("g6_knowledge_paths").
- `mcp__g6__g6_knowledge_refresh`: read its full guidance with g6_describe("g6_knowledge_refresh").
- `mcp__g6__g6_knowledge_revoke`: read its full guidance with g6_describe("g6_knowledge_revoke").
- `mcp__g6__g6_knowledge_search`: read its full guidance with g6_describe("g6_knowledge_search").
- `mcp__g6__g6_knowledge_sources`: read its full guidance with g6_describe("g6_knowledge_sources").
