# Goal tree proposals for G6

This extension of `g6.goal_engine` converts the attached prototype's
`GoalNode`, `GoalTree`, depth-first search, terminal detection, deepest-first
goal stack, and auto-decomposition into bounded, stable-ID, provider-neutral
building blocks.

`GoalTree` uses `root`, `root.0`, `root.0.1` identifiers, matching the
existing `PlanTree`, rather than goal text as dictionary keys (which breaks
when distinct nodes share the same goal text). Insertions are atomic, with
branching, depth and node budgets, and no silent truncation.

`propose_goal_tree(objective, proposer, ...)` expects a callback returning a
JSON list of candidate subgoals. The callback may call a model, but this
module has no model provider, API key, sleep or implicit network call.
Malformed outputs, empty proposals, and unexpanded nodes are reported.
It converts to a validated `GoalSpec` and calls existing `build_plan_tree`.

An optional caller-provided root `GoalSpec` preserves resource bounds,
guardrails, checkpoints, breakpoints, constraints and success criteria; these
are never invented for generated children. The result is
`qualified-draft` at best: a useful plan proposal is *not* a verified
solution, a checked dependency ordering, or an authorization to execute.
`generate_goal_stack()` is a postorder list, not an execution permission.

For execution, the executor must still call
`g6.goal_engine.evaluate_controls` with measured observations and obey
breakpoints and action gates. Proposed subtasks require review.

## Example

```python
from g6.goal_engine.decomposition import propose_goal_tree

def proposer(goal, max_children, depth_remaining):
    return '["add a failing regression test", "implement fix"]'

result = propose_goal_tree("fix crash", proposer, max_depth=1)
assert result.completion_state == "qualified-draft"
print(result.tree.visualize())
print(result.tree.generate_goal_stack())
```

Limitations: structural proposals are not enough to show that decomposition
is sound or complete; siblings do not encode dependencies; no persistent
runtime store and no provider-specific adapter are included.
