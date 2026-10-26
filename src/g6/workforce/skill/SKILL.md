---
name: g6-workforce
description: Use when a coding agent is about to fix a failing test or change a function in a local git repository and what counts as done must be frozen and confirmed by a person before the patch is written, so the patch is checked against that standard rather than against tests the agent can change.
---

# G6 workforce

`g6.workforce` runs a local coding mission under a frozen standard: G6 freezes what counts as done (the baseline
snapshot, the required tests, the permitted file, the risk tier, the budget and the attempt limit), a person confirms
it, G6 reproduces the failure on the baseline, the caller's agent submits a patch, and G6 runs the checks and issues a
certificate that says, test by test, what the evidence supports.

## When to use it

- A test fails in a local repository and an agent is about to change code to make it pass.
- You are worried the agent will "fix" the problem by editing or skipping the tests, or by changing the goal.
- You want the failure reproduced before anyone writes a fix.
- You want to know whether a change to a test file weakened it (`verify.weakened_tests`), or whether the tests would
  notice a small deliberate change to the code (`mutation_check.mutants` makes the changes; `mutation_check.check`
  runs your tests on each).

## When not to use it

- A quick check of a drafted answer or a risky plan: use `coding_agent_gate` (`verify_answer`, `preflight`).
- Tuning how your coding agent is run across many tasks: use `g6.metaharness` (its `coding` module uses
  `repo_tests.run` as the oracle).
- The repository is not a git repository, holds submodules, or you need an operating-system sandbox that `g6 doctor`
  says is unavailable.
- No repository: a mission is yours to plan; only the code-change objective is compiled.

## Python API

- `bind`: opens the local stores (under `$G6_HOME/workforce`, `G6_WORKFORCE_DB_DIR` or `db_dir=`, which binds that
  root for this workforce only); no network.
- `repository_dir`: where a venture's repositories live; a mission's `repo` is a git repository's name there.
- `allow_execution`: shows an approver what running the repository's code at its present revision means, and records
  the permission; a new commit needs a new one.
- `create_coding_mission`: freezes the coding standard for an objective on a repository (`module`, `function`,
  `helpers`, `risk`, `budget_cents`; the tier is at least `R2`); runs the suite in a scratch copy only after
  `allow_execution`.
- `answer_clarification`: answers the one replay question an idempotency objective asks before its standard is frozen.
- `confirm`: shows the frozen standard to an approver, records the confirmation and starts the checks.
- `submit_patch`: the full new text of the permitted files; G6 checks it under the frozen standard at once.
- `status`: the mission's `completion_state` once closed, its result and what it waits for.
- `certificate`: the certificate the close kept, or one issued now on the latest patch.
- `Approver`: any callable that is shown lines and returns who decides, or `None` to refuse.
- `NotConfirmed`: raised when an approver refuses; nothing is recorded.
- `api.decide`: a person's answer to a gate (an `R3` mission's patch approval) or a disagreement between checks.
- `verify.weakened_tests`: compares test sources before and after a change and names each weakening.
- `mutation_check.mutants`: small single-change versions of Python code (`<` to `<=`, a return value to `None`).
- `repo_tests.run`: runs named tests of a repository in place, with git's view compared before and after.
- `coding_learning.review`: opt-in: replays accepted probes on a closed mission's last patch; a disagreeing probe can
  only lower the result.
- `service.plan`: a mission whose plan you write yourself, instead of the compiled coding standard.

## What a person approves

- `allow_execution`: that G6 may run this repository's code at this revision and content.
- `confirm`: the frozen standard, before any patch is checked.
- `api.decide`: an `R3` mission's patch, and any disagreement between checks.
- `coding_learning.approve`: a learned probe, after `g6.learning.promote` recommends it; an `auto:` name is refused.
- `coding_learning.answer`: whether a change a learned probe disagrees with is intended (approve retires the probe).

Each answer is recorded under the identity the approver returns, with a principal that says it was not authenticated
(`coding_learning.approve` records the identity only). An `Approver` is any callable, so one an agent writes approves
on its own; an agent hands the approver to the person and never answers for them. G6 cannot prove a person gave it.

## Alone and composed

Alone, from Python only (no block, so no `g6_invoke` op): `from g6.workforce import bind, allow_execution,
create_coding_mission, confirm, submit_patch, status, certificate`.

Composed:

- `g6.reliability_pipeline`: the mission runs `run_intake` on the objective (a `risky` verdict raises the floor to
  `R3`) and calls `construct_replay_check`; a constructed check is frozen into the standard before the person
  confirms it.
- `g6.evidence`: the certificate is issued from the mission's frozen checks, controls and receipts.
- `g6.safety_engine`: the one door a worker calls the bench through obeys its `PolicyEngine` (the stop, cost and
  runtime ceilings, the allow-list) before anything runs; G6's own runs (the suite at creation, the reproduce stage,
  the controls) call the bench directly, outside that door.
- `g6.bundles`: the `agent_change` bundle uses `verify.weakened_tests`.
- `g6.learning`: `coding_learning` keeps probes as theories in a `TheoryStore` and validates them with `promote`.

## Failure modes it catches

- `moved_goalposts` (moving the goalposts after the work is done): the standard is frozen and confirmed before a
  patch exists. The bench refuses a patch that edits a test file, runner configuration or a path outside its copy, and
  runs only test files byte-identical to the frozen snapshot, whose digest is checked at every step. Outside a
  mission, `verify.weakened_tests` names a removed or changed assert, a removed test, a new skip, xfail or mock, an
  assert under a catch-all handler, and a deleted file.
- `unreproduced_failure` (a fix made for a failure nobody reproduced): before patch work starts, the reproduce stage
  runs every required test on the frozen baseline, and the mission is refused when none fails there (unless the
  standard disclosed a check it cannot make). Without `module` and `function`, the scope is derived only from tests
  that failed in the bench's copy. With certificates on (`G6_WORKFORCE_CERTIFICATES`, the default), each frozen test
  also runs on the unpatched baseline as a control.

## Blind spots

- `runner_in_process` (code under test running with the owner's permissions): tests run in a child process, confined
  by the operating system where G6 can (a repository in place read only, local servers failing), else as you, when no
  pass verifies, as always on Linux and macOS for now.
- `live_working_tree` (tests run in a repository that can change during the run): in-place runs use the repository
  itself; git's view is compared before and after, but ignored files, files the run created, files outside the
  repository and a change made and undone within the run are not covered.
- pytest only: Go and JavaScript tests are not run, and the reply says so. One test file that cannot be imported ends
  `repo_tests.run` as `collection_error`, with no result for the rest. It does not run the tests at the starting commit
  (a mission does), so a failure that predates the change is reported like one the change caused.
- By default a mission's tests (the repository's, or those a plan you write with `service.plan` names, which the
  caller may have written) and a coding mission's constructed checks can refute a change and never lift it: it
  closes at `qualified-draft` at most. Set to `founder-d2`, `G6_WORKFORCE_CERTIFICATE_POLICY` counts those tests as
  independent, so the certificate, and a mission with nothing left unchecked, can say `verified` on them; a
  constructed check never counts. Such a `verified` rests on a pass record written inside the process that ran the
  patch, so a patch aimed at the recorder could forge it; G6 names this gap on the close (`certificate_gaps`), the
  outcome and the result.
- Under `founder-d2`, a test the seeded defects never made fail stays a named gap; the replay constructor is two
  fixed templates.

## What it needs

- **git** (the `git` command on PATH; one older than 2.31 is refused): missions, the bench's in-place runs and the
  repository tests read and reset a working tree with it, with a clean environment, every config key that would start
  a program switched off, and no transport. A repository with submodules is refused (`G6_E_GIT_SUBMODULE_REFUSED`).
- **pytest**, in the Python running G6 (found by importing it with `-I`): the mission bench runs
  every test with G6's own interpreter, so the repository's imports must resolve there too; `repo_tests.run` and
  in-place runs use the repository's own `.venv` or `venv` first. Not one of G6's own dependencies: `pip install
  pytest`. No network and no account.

## Examples

Neither of these needs git.

```python
from g6.workforce import mutation_check, verify

before = {"test_price.py": "from price import total\n\ndef test_total():\n    assert total([2, 3]) == 5\n\n"
                           "def test_empty():\n    assert total([]) == 0\n"}
stronger = {"test_price.py": before["test_price.py"] + "\ndef test_one():\n    assert total([4]) == 4\n"}
skipped = {"test_price.py": "import pytest\n" + before["test_price.py"].replace(
    "def test_empty", "@pytest.mark.skip\ndef test_empty")}
loosened = {"test_price.py": before["test_price.py"].replace("== 5", "is not None")}

assert verify.weakened_tests(before, stronger) == []
assert verify.weakened_tests(before, skipped)
assert any("test_total" in f for f in verify.weakened_tests(before, loosened))
assert verify.weakened_tests(before, {})     

code = "def total(xs):\n    if len(xs) > 0:\n        return sum(xs)\n    return 0\n"
changes = mutation_check.mutants(code)
assert changes and len({c.source for c in changes}) == len(changes)
assert all(c.source.strip() != code.strip() for c in changes)
```

A mission (it needs git and a repository under `repository_dir(wf)`):

```text
wf = bind()
allow_execution(wf, "sample", approver)            # a person allows G6 to run this revision's code
found = create_coding_mission(wf, "make tests/test_policy.py pass", repo="sample", module="policy.py",
                              function="label")
confirm(wf, found["mission_id"], approver)         # a person confirms the frozen standard
submit_patch(wf, found["mission_id"], {"policy.py": "...full new text..."})
status(wf, found["mission_id"])["completion_state"]
certificate(wf, found["mission_id"])
```

## The four open problems

- **Learning signals**: every mission step, check, receipt and decision is recorded in the mission store and the run
  ledger; the opt-in `coding_learning.learn` turns a regression a mission caught into a candidate probe. Not yet: no
  learner runs on its own, and nothing feeds the meta-harness from missions.
- **Evaluators the model cannot influence**: the tests are frozen by digest before the patch; the patch may not touch
  them or the runner's configuration; a run's result is the runner's record checked by a positive control, not its
  printed tally. Not yet: the frozen tests are the repository's own, which the agent may have written before the
  freeze, so by default they can only refute.
- **Transfer by failure mode**: Not yet: a learned probe applies only to the same function of the same repository.
- **Enough held-out cases**: Not yet for missions: a mission's evidence is its own frozen tests and controls, with no
  sample-size rule. A learned probe needs `g6.learning.promote`'s held-out pairs before a person can accept it.
