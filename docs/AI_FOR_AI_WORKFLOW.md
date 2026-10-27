# AI building AI applications with G6

The objective is an agent that constructs an application from components, identifies a failure,
proposes a redesign and measures it against a frozen baseline. The evaluator, authorization and
promotion rules remain host-controlled. Better performance is a hypothesis to test.

## Install and discover

Use Python 3.12 or 3.13. From the checkout, install `pip install ".[all]"`, run `g6 doctor`, then
`g6 setup --client codex` or `g6 setup --client claude`. Setup uses an absolute interpreter path
and installs the using-g6 skill. Existing skills are preserved. An existing MCP entry that the
client refuses to replace must be inspected with that client's `mcp get g6` command.

To explicitly use a host subscription, add `--model codex` or `--model claude_code`. Setup stores
the provider preference without changing other settings. This authorizes future model usage;
setup itself makes no model call. API-key configuration remains manual in `$G6_HOME/.env`.

Call `g6_help`, then `g6_find` by task or failure mode, and `g6_describe` before invoking an operation.
Python-only components remain available through their library API.

## Explicit composition

Pass this data to `g6_compose` as `composition_json`:

```json
{
  "stages": [{"component": "grounding", "op": "check_claims"}],
  "inputs": {"text": "The limit is 42 units.", "sources": {"manual": "The limit is 42 units."}}
}
```

Subsequent stages require explicit input mappings, such as `{"checks": "0.checks"}`;
caller fields use `caller.field`. Declared evidence types must match. Transformations requiring
code use reviewed Python adapter blocks, not executable strings in MCP arguments.

The report distinguishes `contract-checked` from `unvalidated`. It records contracts, source-module
digests, declared side effects, evidence provenance and checks actually performed. Empty integration-test
and application-validation fields mean no such evidence was supplied. Module digests do not pin transitive
dependencies. Plain Python `a >> b` remains an unchecked connection with completion-state enforcement.

## Construct and redesign a code application

Call `metaharness_start` with this structure in `specification_json`:

```json
{
  "name": "labels",
  "task": "Label {input}",
  "cases": [{"input": "red", "expected": "warm"}, {"input": "blue", "expected": "cool"}],
  "baseline_code": {"main.py": "def solve(task):\n    return 'unknown'\n"},
  "check": "exact",
  "seed": 20261027
}
```

This example deliberately has too few cases for activation. Supported checks are `exact`, `contains`
and `number` with a finite nonnegative `tolerance`. Files must have flat portable Python names.
The split uses a recorded seed; expected answers are read by the evaluator, not copied into the
candidate's execution directory. A proposing agent that supplied every answer has already seen those
answers: a random split alone does not establish independence from that agent. Use genuinely withheld
cases and an independent host evaluator before making a performance claim.

1. Confirm the returned workflow digest through `metaharness_confirm` and the client's prompt.
2. Inspect or diagnose the baseline. Propose a change with `metaharness_propose_code`, a JSON mapping
   of filenames to source in `files_json`, and a falsifiable `hypothesis`.
3. Call `metaharness_evaluate_start`; poll `metaharness_job` until a result or failure is reported.
4. Give the returned report and proposal digests to `metaharness_decide`. Preserve the recovery reasons.
5. Only a recommend decision can proceed to `metaharness_approve`. Same-session client-mediated consent
   is permitted. Missing elicitation, refusal or changed evidence never activates a version.
6. `metaharness_run` runs the approved version as a job. `metaharness_rollback` asks for consent to a
   specific previously approved profile; it cannot undo external effects of earlier runs.

Below 30 held-out cases the gate cannot recommend activation. Above that floor, significance, false
acceptance, cost and other existing gates still apply. Generated code additionally requires
`Workflow.patch_evaluator` to independently measure safety and injection controls. The data-only creator
currently does not configure that evaluator; use the Python workflow route for that advanced requirement.
Never supply invented observations to satisfy it.

## Reference application: review a Python patch

Register permission with `g6 setup --client codex --allow patch_review.review`. Call `g6_review_patch`
with the repository root, task, claim, source texts, selected test IDs, and a base ref (default `HEAD`).
Poll its job. The operation runs tests through workforce confinement, checks changed test integrity,
and connects grounding, an explicit adapter, coding-agent gate, an action adapter and action gating.

The report separates host-observed test receipts from caller-supplied source text. It binds the current
working-tree digest, base commit, test selection and execution environment. Passing tests cannot prove
the complete patch correct; source support cannot establish freshness. A stale supplied artifact digest,
weakened tests, changed runner files, failed tests or unconfined execution blocks recommendation.
The best result is `needs_review`; the application never merges code.

## Boundary and validation status

Linux requires a functioning bubblewrap installation; Windows uses AppContainer with the required access
grants. Other configurations refuse untrusted code unless the user explicitly permits unconfined execution.
An isolation record describes enforced restrictions, not complete security. Local preference files and
client consent assume a trusted host. An unrestricted same-user shell can change those files or inspect
held-out cases; use a separately protected host/account when that threat matters.

Automated API and FastMCP-client tests validate mechanisms. They do not establish fresh-user usability,
the real Claude/Codex clients' consent behavior, or application advantage over a baseline. Those are
separate release gates in [v0.1.1](releases/v0.1.1.md).
