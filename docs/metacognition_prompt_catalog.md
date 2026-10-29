# G6 metacognition and critical-thinking prompt catalog

The static `g6.workspace.align_prompt_library.metacognition` entries are
candidate prompt templates in G6's one prompt catalogue
(`g6.workspace.align_prompt_library.catalogue`), not an alternative prompt store.
Each keeps its original stage and is tied to a work mode and a pipeline step.
Installing the catalogue into the append-only, SHA-256-addressed
`align_prompt_library` is the sensitive op `install_catalog`: over MCP it is refused
unless the person who runs the server allows it. Importing either module never writes.

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
from g6.workspace.align_prompt_library import catalogue
from g6.workspace.align_prompt_library.library import Library, placeholders

entry = catalogue.entry("ct-critical-review")
values = {key: "example" for key in placeholders(entry.template)}
rendered = catalogue.render(entry.name, values)
assert rendered["template_digest"] == entry.digest

catalogue.install(path="my-library.jsonl")  # explicit persistent write; install_catalog over MCP
stored = Library("my-library.jsonl").get(entry.name)
assert stored["digest"] == entry.digest
```
## Evidence contract and limitations

A generated critique is only another model-generated proposal. It cannot raise
a result's state, alter a frozen evaluator, grant approvals or activate a revised
prompt. Prompt experiments must run through the G6 meta-harness's paired
held-out evaluations and promotion process with human approval.

The catalog has no measured prompt-improvement claims, benchmark outcomes or
automatic stage runner. It deliberately avoids duplicating the existing
workspace library, metaharness, goal engine and model router.
