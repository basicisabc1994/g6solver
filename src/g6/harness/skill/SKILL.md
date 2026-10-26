---
name: g6-harness
description: Use when a task or sequence of steps needs frozen acceptance checks, observed failures, readiness measurements, or a report of which failed criterion to address next.
---

# G6 harness

The harness runs a `Standard` through intake, checks, execution, adjudication and memory. Caller checks support a
`qualified-draft` at best. Failed checks block. Reports describe observations; they never certify a workflow.

## When to use it

Use it to check each step, count recurring failures, measure candidate and baseline outputs on reserved cases,
identify a failed critical criterion, or turn declarative JSON assertions into ordinary checks.

## When not to use it

Do not use it to execute untrusted code, certify a specialist judgement, estimate payment or economic value,
or prove that a model never saw a case during training. For governed workflow tuning use `g6.metaharness`.

## Python API

There is no block and no MCP op list for this package. These additions introduce no registry component or tool name.

- `Check`: predicate, callable, optional id, author, evidence kind, known-bad outputs and a known-good output. A check
  you supply is recorded as yours: an author of "g6" is recorded as "caller", and so are its controls.
- `Standard`: goal, checks, context sources, gates, family, optional plan and pipeline `SuccessCriterion` values.
- `Memory`: the existing `harness.db`; `:memory:` is useful for isolated examples. Close it when finished.
- `run`: run one standard; return the envelope, results, receipts, plan, gaps and next action.
- `run_steps`: run `(name, Standard, executor)` tuples in order, stopping at the first `blocked-escalated`.
  The `steps` list contains each attempted step's complete result. Empty steps are refused.
- `failure_counts`: failed-check occurrences grouped by `check` or `family`, filtered by positive `min_count`.
  Counts by family count check failures, so a run with two failed checks contributes two occurrences.
- `fail_rate`: blocked attempts divided by recorded attempts for one normalised family. Includes blocks before
  execution. With no recorded attempts, `rate=None` and `status="no runs"`. Legacy failure-only rows have no
  denominator and are excluded from this rate, while remaining in failure counts.
- `evaluate_cases`: freeze a standard, reserve distinct named string inputs, and call the injected executor with
  `(contract, case_input)` for each. An optional baseline executor sees the same inputs and checks. Inputs matching
  check controls are refused. Before copying or execution, refuse suites above `max_cases` (default 1,000) or
  `max_suite_bytes` (default 1,048,576), counting builtin data and container memory. Both limits are overridable.
  Store run references in the same database; return the readiness report. After both executors, rerun known-bad
  controls and compare source hashes; changed hashes or controls no longer failing invalidate frozen status.
- `readiness_report`: read the latest standard and paired evaluation from memory. Reports frozen checks, actual
  known-bad results, held-out pass rate, baseline comparison and observed blocks. A later ordinary run invalidates
  the earlier batch. Unknown rates and unmeasured blocking counts are `None`. Unavailable executions or unknown
  check outcomes leave comparison unknown; `comparison_unavailable` explains why, including executor diagnostics.
- `value_report`: read a recorded run id; name the `highest` and `next` failed checks. Critical criteria precede
  non-critical criteria; ties follow standard order. Criticality comes from `SuccessCriterion.critical`, matched by
  exact predicate text. Duplicate predicate text is refused before execution, naming the ambiguous check ids.
  Unknown outcomes and missing criticality remain separate gaps.
- `check_field`: build a `Check` from a dotted JSON field, assertion op and expected value. Ops are `eq`, `equals`,
  `ne`, `not_equals`, `gt`, `gte`, `lt`, `lte`, `truthy`, `falsey`, `contains`, `not_contains`, `len_gte`.
- `resolve_field`: traverse dictionary keys, list indices and list `count`; no attributes, methods or imports.
- `confirm_suite`: combine built-in checks with declarative manifest entries. Every replacement needs literal
  `override: true`, even if it looks stronger. Omitted built-ins remain. Extra fields, duplicate ids and empty
  suites are refused. This constructs a suite; it grants no approval or stronger reliability label.

The readiness ladder is descriptive: `none` without tested controls, `prototype` with them, `reusable` only when
all reserved candidate cases pass without blocks, and `publishable` only with that plus a strictly beaten paired
baseline. Every tier remains `qualified-draft`; the report is not publication permission or a sample-size claim.

## Alone and composed

Call these Python functions directly. `run_steps` calls `run`; all reports use the same `Memory`. Pass
`confirm_suite`'s tuple to `Standard`. Pass pipeline success criteria through `Standard.criteria`; every supplied
criterion must match a check. Executors are trusted application adapters; untrusted source belongs in `core.sandbox`.
`run`, `run_steps` and `evaluate_cases` write local observations and are declared sensitive Python operations;
there is no registry dispatch or automatic authorisation for them.

## Failure modes it catches

- `unchecked_step`: the first blocked step stops the sequence; each attempted step carries its checks' results.
- `learning_gap`: persistent failures are counted, and recorded attempts supply rate denominators.
- `untested_workflow`: readiness requires actual frozen-check and control observations, not supplied booleans.
- `weak_link`: the failed critical check is prioritised from the observed deliverable, not a declared checklist.
- `silent_behaviour_change`: declarative assertions detect changed outputs; manifests cannot silently replace checks.

## Blind spots

`runner_in_process`: injected callables run with this process's permissions. They are trusted integration code,
not a sandbox. An in-process callable has no enforceable deadline; a hung callback can prevent return.
Suite limits bound input data, not callback allocations or output size. Checks and controls remain caller-authored.
Source hashes and rerun controls cannot establish that all mutable closure or global behaviour stayed unchanged.
Local hold-out reservation cannot establish training-data isolation or population
coverage. An editable local database is not an adversarial trust boundary. A pass rate counts checks passing,
not business correctness; a zero block rate does not mean all checks passed. Criticality is declared context.

## What it needs

Existing G6 dependencies only. No extra, outside program, network fetch, YAML parser or model is required.
State uses the existing storage helper and `harness.db`.

Refusal codes include `G6_E_HARNESS_STEPS`, `G6_E_HARNESS_STANDARD`, `G6_E_HARNESS_CRITERIA`,
`G6_E_HARNESS_CHECK_FAILED`, `G6_E_HARNESS_BROKEN_CHECK`, `G6_E_HARNESS_NO_EXECUTOR`,
`G6_E_HARNESS_EXECUTOR_FAILED`, and `G6_E_STOPPED`. Invalid analytics, cases, run ids, assertions, suites and
replacements raise `ValueError` with `G6_E_HARNESS_ANALYTICS`, `G6_E_HARNESS_CASES`, `G6_E_HARNESS_RUN`,
`G6_E_HARNESS_ASSERTION`, `G6_E_HARNESS_SUITE` or `G6_E_HARNESS_OVERRIDE`; manifest schema errors use Pydantic.
Suite limits raise `ValueError` with `G6_E_RESOURCE_LIMIT`.

## Examples

```python
from g6.harness import (Memory, Standard, check_field, confirm_suite, run_steps, fail_rate,
                        failure_counts, evaluate_cases, readiness_report, value_report)
from g6.reliability_pipeline.task_contract import SuccessCriterion

memory = Memory(":memory:")
check = check_field("total", "eq", 42, check_id="total", known_bad=('{"total":41}',))
standard = Standard("Summarise the refund policy", confirm_suite([check], []), family="policy",
                    criteria=(SuccessCriterion(check.predicate, critical=True),))
try:
    steps = run_steps([("summary", standard, lambda c: '{"total":41}')], memory=memory)
    assert steps["completion_state"] == "blocked-escalated"
    report = value_report(steps["steps"][0]["result"]["run_id"], memory=memory)
    assert report["highest"]["check"] == "total"
    assert failure_counts(memory=memory)["counts"] == {"total": 1}
    assert fail_rate("policy", memory=memory)["rate"] == 1.0
    evaluate_cases(standard, {"new-policy": "different input"}, lambda c, case: '{"total":42}',
                   baseline_executor=lambda c, case: '{"total":41}', memory=memory)
    assert readiness_report("policy", memory=memory)["held_out_pass_rate"] == 1.0
finally:
    memory.close()
```

## The four open problems

- **Learning signals**: failed checks and observed run receipts remain in the one harness database.
- **Evaluators the model cannot influence**: checks are frozen before execution and tested on known-bad outputs;
  caller authorship and mutable callable state remain limitations.
- **Transfer by failure mode**: predicates and declarative paths are caller-extensible, with no keyword scenarios.
- **Enough held-out cases**: rates include the number of reserved cases. No minimum statistical sample size or
  independent dataset provenance is established.
