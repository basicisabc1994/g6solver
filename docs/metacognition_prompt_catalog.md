# G6 metacognition and critical-thinking prompt catalog

The static `g6.workspace.align_prompt_library.metacognition` catalog is an
optional collection of **candidate prompt templates**, not an alternative
prompt store. Install with `install_catalog(Library(path))` into the existing
append-only, SHA-256-addressed `align_prompt_library`, whose sensitive write
policy must still be respected by callers. Importing this module never writes.

## Provenance and adaptation

The attached code supplies these source behaviors:
- `assess_problem`: constraints, first principles, heuristics, self-assessment,
  satisficing strategy and bounded goal-tree parameters;
- `check_if_research_needed`, `evaluate_solution_critically`, execution
  synthesis and re-critique;
- `META_PROMPT_2` (new system prompt generation) and `META_PROMPT_1`
  (revision of an existing prompt).

These are adapted to G6's provider-neutral Python architecture and existing
`GoalSpec` / `PlanTree`, rather than hard-coding GPT-4o-mini, Sonar, Sonnet
or O3 adapters. The original POC's requests to display private chain-of-thought
are replaced with concise evidence and inspectable decision summaries.
All templates insist on stated uncertainties, refutation tests and clear
separation of claims from measurements.

## Named prompts

`mc-constraints`, `mc-first-principles`, `mc-heuristics`,
`mc-self-assess`, `mc-satisficing`, `mc-goal-decompose`,
`mc-research-needed`, `mc-result-synthesis`,
`mc-uncertainty-update`, `ct-critical-review`,
`ct-independent-test-plan`, `pe-generate-system-prompt`,
`pe-revise-system-prompt`.

All return structured JSON instructions and use strict
`{{placeholder}}` templates compatible with the existing library.

## Python example

```python
from g6.workspace.align_prompt_library import Library
from g6.workspace.align_prompt_library.metacognition import (
    get_prompt, render_prompt, install_catalog,
)
from g6.workspace.align_prompt_library.library import placeholders

entry = get_prompt("ct-critical-review")
values = {key: "example" for key in placeholders(entry.template)}
rendered = render_prompt(entry.name, values)
assert rendered["template_digest"] == entry.digest

library = Library("my-library.jsonl")
install_catalog(library)  # Explicit sensitive persistent write.
stored = library.get(entry.name)
assert stored["digest"] == entry.digest
```

## Evidence contract and limitations

A generated critique is only another model-generated proposal. It cannot earn
`verified`, alter a frozen evaluator, grant approvals or activate a revised
prompt. Prompt experiments must run through the G6 meta-harness's paired
held-out evaluations and promotion process with human approval.

The catalog has no measured prompt-improvement claims, benchmark outcomes or
automatic stage runner. It deliberately avoids duplicating the existing
workspace library, metaharness, goal engine and model router.
