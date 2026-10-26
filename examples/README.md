# Demonstrations

Four scripts, each runnable from a clean clone after `pip install .` (demo 3 also needs `git`). Each runs offline and
needs no AI model. What each one printed when it was last run is in [`output/`](output/); `tests/examples` runs all
four so they cannot drift from the code.

| Script | What it shows |
|---|---|
| [`demo_1_persistent_state.py`](demo_1_persistent_state.py) | **State across sessions.** A check that failed in one process makes the next process refuse a standard that dropped it, until the check is back or a person waives it. A control run with an empty state folder shows the memory made the difference. |
| [`demo_2_evidence_revision.py`](demo_2_evidence_revision.py) | **Error detection and evidence-based revision.** Through the same reply an MCP client gets: unbacked sentences are `blocked-escalated` with what is missing named; once the source is supplied the draft is a `qualified-draft`; a source with a different figure is a conflict. |
| [`demo_3_governed_change.py`](demo_3_governed_change.py) | **Governed harness modification.** A failing task, its diagnosis, two proposed template changes, each verified in its own git worktree against the project's tests and a held-out suite, and decided by `govern_patch_promotion` bound to the exact diff. The bad change is rejected and rolled back with its record kept; the good one is merged and used by a later task. |
| [`demo_4_agent_discovery.py`](demo_4_agent_discovery.py) | **A coding agent discovering G6.** The `g6_find` → `g6_describe` → `g6_invoke` calls an agent makes to check an email before sending it: claims against documents, then the action gate, which blocks until the agent supplies a verification plan and then asks for a person. |

Run one:

```bash
python examples/demo_1_persistent_state.py
```

What the demos do not show: that G6 makes an agent better at real work. That is what the studies in
[`docs/validation/VALIDATION_PLAN.md`](../docs/validation/VALIDATION_PLAN.md) are for. Demo 4's acceptance test is a fresh
Claude Code or Codex session doing the same task from the README alone; the script only shows the path.
