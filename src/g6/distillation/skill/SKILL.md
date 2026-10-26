---
name: g6-distillation
description: Use when a small generated or distilled Python function (a solve(x) meant to replace a model call or a hand-written rule) is about to be relied on, and you need to know whether it holds on cases it was not made from, run out of process under a time and memory bound.
---

# G6 distillation

`g6.distillation` runs a small distilled function, `solve(x)`, in a bounded child process and tests it on held-out
cases the evaluator owns, with a known-wrong negative control that must fail. Its report is evidence for a decision,
`qualified-draft` at best. It serves nothing and promotes nothing.

## When to use it

- You or a model wrote a small `solve(x)` from examples, and you are about to use it instead of a model call or a rule.
- You want to know whether it works on cases it was not fitted to, not only on the examples it came from.
- You are about to `exec` generated Python in your own process: run it through the pre-filter and a bounded child
  instead.
- You want a paired comparison with what you do today (a `baseline`) on the same cases.

## When not to use it

- The function belongs to a declared meta-harness workflow: use `MetaHarness.admit_distillate` in `g6.metaharness`,
  which runs this module's `evaluate` on the workflow's held-out split and records the admission.
- The code is a change to a repository with its own tests: use a `g6.workforce` coding mission, or `coding_agent_gate`
  (`verify_answer`) before you report it done.
- You need an operating-system sandbox for hostile code: this is a resource bound and a pre-filter, and the child runs
  as you.
- The answer must be exact mathematics or logic: hand it to `algebra` or `formal` instead of a fitted function.

## Python API

- `Candidate`: a frozen distilled function: `source` that defines `solve(x)`, plus `origin_episode_ids` and
  `training_case_ids`; `digest` is the sha256 of the source.
- `HoldoutCase`: an evaluator-owned case (`case_id`, `input`, `expected`); the candidate never receives `expected`.
- `evaluate`: runs a candidate on held-out cases with a required `negative_control`, an optional `positive_control`
  and `baseline`, and `timeout_s` / `memory_mb` limits; returns a `HoldoutReport`.
- `HoldoutReport`: `verdict` (`passed`, `failed`, `inconclusive` or `refused`), `reason`, `passed`, `failed`,
  `outcomes`, `negative_control_failed` (the cases it answered wrongly), `positive_control_failed`, `baseline`, `code`,
  `completion_state` and `to_dict()`.
- `check_source`: the AST pre-filter and size bound; returns why a source may not run, empty when it may.
- `run_sandboxed`: `solve(value)` once in a bounded child; returns a `Run`.
- `run_batch`: `solve(value)` for each input in one bounded child; one `Run` per input, in order.
- `Run`: one input's outcome: `status` (`ok`, `error`, `timeout` or `refused`), `value`, `reason` and `ok`.
- `same`: the comparison `evaluate` uses: JSON equality, with floats equal to within 1e-9.

## Alone and composed

Alone, from Python only (it has no block, so there is no `g6_invoke` op):
`from g6.distillation import Candidate, HoldoutCase, evaluate`.

Composed:

- `g6.metaharness`: `MetaHarness.admit_distillate(source, step=...)` turns the workflow's held-out cases into
  `HoldoutCase`s and calls `evaluate` with a negative control that answers `None`. Only a `passed` report stores the
  distillate by digest; a profile may then name it (`Profile.distillates`), and that profile still goes through the
  meta-gate and a person.
- `g6.self_training`: whether a candidate is served is the caller's governance, for example
  `govern_patch_promotion`, with this report as evidence the caller turns into its `PatchEvalReport`.

## Failure modes it catches

- `brittle_shortcut` (a shortcut that breaks on cases it has not seen). Before anything runs, `evaluate` refuses a
  held-out case whose id is one of the candidate's `training_case_ids`, an empty holdout, two cases with one id, and a
  source the pre-filter refuses. It then requires the negative control to return a wrong answer on at least one
  case: an error, a timeout or a refusal does not count. A negative control that answered no case (no `solve`,
  refused by the pre-filter, raising or timing out on every case) makes the report `refused` with
  `G6_E_DISTILLATION_NEGATIVE_CONTROL_DID_NOT_RUN`, before the candidate runs; one that answered but never wrongly
  means the cases cannot tell right from wrong, and the report is `inconclusive`. A positive control that fails a
  case also makes it `inconclusive`, because the cases or their answers are wrong.

## Reading the result

- `passed` is `qualified-draft`: every case passed and the controls behaved. The cases are yours, so the report is as
  good as they are.
- `failed`, `inconclusive` and `refused` are `blocked-escalated`; `reason` says why, and `outcomes` gives each case.
  `code` is `G6_E_DISTILLATION_NEGATIVE_CONTROL_DID_NOT_RUN` when the negative control answered no case, else empty.
- With a `baseline`: `baseline_passed`, `wins`, `losses` and `p_one_sided`, a one-sided exact sign test on the pairs.

## Blind spots

- Overlap with training is checked by case id only: a training case held out under a new id, or with the same input,
  is not caught.
- There is no minimum number of cases: three cases can pass. With a baseline, the sign test shows how weak a small
  sample is; without one, nothing does.
- One wrong answer satisfies the negative control, so a control wrong only on an easy case shows little; give one
  that makes the mistake you fear.
- It is a resource bound, not an operating-system sandbox. The child runs as the same user, and connections off this
  machine are refused by a guard on Python's socket class, not by the operating system (loopback is allowed). Memory is capped (a POSIX limit or a
  Windows Job Object); CPU and file-size limits apply on POSIX only.
- The pre-filter allows no import and only a small table of builtins, so any function that needs a module is
  refused, not run.
- Inputs and outputs must be JSON, and comparison is exact apart from floats: a right answer in another shape fails.
- The `baseline` callable runs in your own process, outside the bound.

## What it needs

Nothing outside G6: the candidate runs under this Python interpreter (or the one `G6_PYTHON` names) in a child
process started by `g6.core.sandbox`, with the standard library only.

## Examples

A right candidate passes with a failing negative control; a training case offered as held out is refused; cases a
wrong function also passes are inconclusive.

```python
from g6.distillation import Candidate, HoldoutCase, check_source, evaluate, run_sandboxed

reverse = Candidate("def solve(x):\n    return x[::-1]\n", training_case_ids=("t1", "t2"))
identity = Candidate("def solve(x):\n    return x\n")          # known wrong: the negative control
holdout = [HoldoutCase("h1", "abc", "cba"), HoldoutCase("h2", "ab", "ba"), HoldoutCase("h3", "", "")]

report = evaluate(reverse, holdout, negative_control=identity, baseline=lambda x: x)
assert (report.verdict, report.completion_state) == ("passed", "qualified-draft")
assert report.negative_control_failed == ("h1", "h2")
assert (report.baseline["wins"], report.baseline["losses"]) == (2, 0)

leaked = evaluate(reverse, [*holdout, HoldoutCase("t1", "xy", "yx")], negative_control=identity)
assert (leaked.verdict, leaked.completion_state) == ("refused", "blocked-escalated")

palindromes = [HoldoutCase("p1", "aba", "aba"), HoldoutCase("p2", "", "")]
assert evaluate(reverse, palindromes, negative_control=identity).verdict == "inconclusive"

assert check_source("import os\ndef solve(x):\n    return x\n")      # refused before it runs
assert run_sandboxed("def solve(x):\n    return 10 // x\n", 0).status == "error"
```

## The four open problems

- **Learning signals**: Not yet on its own: the report goes back to the caller and nothing is written to a ledger.
  Through `MetaHarness.admit_distillate`, each admission is written to the workflow's run ledger as an evaluation row.
- **Evaluators the model cannot influence**: the held-out cases belong to the evaluator and the candidate never
  receives `expected`; it runs out of process; the comparison and the controls are G6's own code. The cases and their
  answers are still the caller's, so the best state is `qualified-draft`.
- **Transfer by failure mode**: Not yet: a report is about one candidate on one set of cases, and nothing carries
  what it found to another task.
- **Enough held-out cases**: partly: with a `baseline`, a one-sided exact sign test over the paired wins and losses.
  Not yet: no minimum sample size and no estimate of how many more cases a decision needs.
