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
with the repository root, the task, `requirements` (a list of `{id, text, checks}`, where `checks` are
pytest node IDs and may be empty), optional extra `tests`, and a base ref (default `HEAD`). A `claim` and
`sources` are optional: their lexical support is reported and decides nothing. Poll its job.

The checks adapt to cost. Tier 1 always runs: the working-tree digest, test integrity, and the stated
checks with the extra tests in one confined run. Integrity is what these checks find, and nothing more:

- the signs of weakening `safety_engine.detect_test_weakening` reads in each changed test file (by either of
  pytest's default names), `conftest.py`, start-up file and Python file under a `tests`, `test` or `testing`
  folder: a test or check removed or changed, a parametrize case dropped, a skip, mock or early return added
  (a moved file is compared with its old text). It compares text; it does not show that the tests are as
  strong as before;
- a changed `conftest.py`, `pytest.toml`, `.pytest.toml`, `pytest.ini` or `.pytest.ini`, or a changed pytest
  section in `pyproject.toml`, `setup.cfg` or `tox.ini`, an empty one included (a dependency or metadata
  change does not count);
- a git-ignored file that could change the tests: a `conftest.py` hidden through `.git/info/exclude`, a pytest
  configuration, start-up, `.pth` or test-named file, a Python file named like a tracked module, bytecode
  outside `__pycache__`, a compiled extension module, a link, or a package's `.egg-info` or `.dist-info`
  folder declaring a pytest plugin. Any other ignored Python file (a generated `_version.py`, say) is listed
  as an unknown and bound into the test receipt, path to SHA-256;
- a run that changed tracked files, whose result cannot be believed, or that began on a tree other than the
  one under review, and the repository changing during the review;
- a shortcut the coding gate finds in the diff against the base of the test, test-runner and
  continuous-integration files (a source file's diff is not scanned: an assert it drops weakens no test).
  One found only in the `claim` text is insufficient evidence (`claim_reports_shortcut`), not an integrity
  problem.

A stated check the environment cannot collect (a dependency it lacks) is not established; one whose own code
fails to collect is a defect. A check the time limit cuts off, or never reaches, is not established either,
never a defect. Tier 2, the other tests that touch the changed modules, runs only when tier 1 found no
integrity problem and no defect; one that fails, or fails to collect, on the patch is replayed at the base
commit (a moved test file at its old path), and counts as a defect only if it passed there. When nothing so
far found a problem, the stated checks that passed also run against the base commit's code, in a copy: one
that passes there too may not exercise the change, and is listed as an unknown with a next action, never an
outcome (a requirement to keep behaviour passes at the base by design). Tier 3 (mutation, old-against-new
probes) is not run yet.

A submodule that was never set up here (a gitlink with no `.git` in its folder, as some repositories ship
their vendored code) is accepted: git never enters its folder, and G6 hashes the folder's files itself into
the digest, so a run that changes one of them is caught as a tracked file would be. Git holds nothing to
compare that folder with, so whether the patch changed its files is listed as an unknown. A patch that changes
the gitlink entry itself, or a folder too large for G6 to hash, is insufficient evidence
(`change_inside_submodule`, `submodule_not_hashed`). A submodule that was set up (a `.git` in its folder, or
anything under `.git/modules`) is refused, `G6_E_GIT_SUBMODULE_REFUSED`. The copies the base-commit runs use
keep a tracked link only when it is relative and resolves inside the copy; each run lists the links it left
out.

The tests run with the Python that `G6_REPO_PYTHON` names where you start G6 (a full path: a Poetry or conda
environment, or a system Python that holds the repository's dependencies), else the repository's own `.venv`
or `venv`, else G6's own; `scope.environment.interpreter_source` says which (`setting`, `repository` or
`g6`). Only G6's own environment sets it, never a request, and a setting that names no Python is a refusal
(`G6_E_REPO_PYTHON_INVALID`). The tests see none of your environment but what `G6_REPO_ENV` gives them, a
JSON object of variable names to text set where you start G6 (`{"QT_QPA_PLATFORM": "offscreen"}`, say);
names that would change how G6 runs them (`PYTHON...`, `LD_...`, `DYLD_...`, `PYTEST_...`, `G6_...`,
`COV_CORE_...`, `PATH`, `HOME`, `USERPROFILE`, `TMP`, `TEMP`, `TMPDIR`) are refused (`G6_E_REPO_ENV_INVALID`).
The run, `scope.environment` and the receipt record each name with the SHA-256 of its value, never the value.
Hypothesis keeps its database in the run's own folder (`HYPOTHESIS_STORAGE_DIRECTORY`, unless `G6_REPO_ENV`
names it), since the repository is read only to a confined run; `env_set_by_g6` says so.

Each run lists the modules it loaded from outside the tree it tested. When a module the patch changed came from
there instead, an installed copy such as a package installed without `-e` beside a `lib/` layout, the run tested
code the patch did not change: its passes and failures are not used, the outcome is at most insufficient evidence
(`changed_code_not_loaded`), and an unknown names the module and where it came from. Name the folder that holds
the package in `import_roots` (at most 8 folders inside the repository, such as `["lib"]`): they go first on the
import path of every run of the review and are recorded in `scope.import_roots`.

Each confined run in place gets a test receipt, listed in `scope.receipts`. The review builds the coding
gate bound to its own snapshot digest, so a receipt counts as G6-observed (`matched`) only for the tree
under review; over `g6_invoke` the gate has no binding and every receipt stays caller-declared. A run gets
no receipt when it was unconfined or when git ignores a file that could change it. A receipt does not bind
the packages and pytest plugins installed in the interpreter's environment (the repository's own `.venv`
included, and a plugin installed outside the repository can still change results), stale bytecode in
`__pycache__`, or ignored files that are not Python (data the tests read, say). The receipt store is a file
owned by the same user as G6: it stops a caller's JSON from claiming that G6 ran a test, not a program
running as that user, which can write the file itself.

`assessment.outcome` is exactly one of, in this order: `test_integrity_compromised`, `defect_detected`,
`insufficient_evidence`, `supported_within_scope`. All four are `qualified-draft`. Supported within scope
means every stated requirement passed its stated checks with integrity intact; it does not show the patch
does everything the task needs. Each requirement reads `passed_on_stated_checks`, `failed` or
`not_established` (with a reason and a suggested test in words). The assessment keeps three parts apart:
the requirements with their evidence, `requirements_without_adequate_check`, and `specification_note`,
which says the task's specification may be incomplete. Its `scope` records the digest, base commit,
changed files, environment, each check run (tier, command, verdict, seconds), the checks skipped and why,
and the total time; `unknowns` and `next_actions` follow.

A stale supplied artifact digest, invalid input, a git error, or a run no operating-system boundary
confined is a refusal: `blocked-escalated`, with no outcome. Merging is separate: `merge.status` is always
`requires_separate_approval`, coding-gate findings other than shortcuts are listed as merge reasons, and
the application never merges code.

## Boundary and validation status

Linux requires a functioning bubblewrap installation; Windows uses AppContainer with the required access
grants. Other configurations refuse untrusted code unless the user explicitly permits unconfined execution.
An isolation record describes enforced restrictions, not complete security. Local preference files and
client consent assume a trusted host. An unrestricted same-user shell can change those files or inspect
held-out cases; use a separately protected host/account when that threat matters.

Automated API and FastMCP-client tests validate mechanisms. They do not establish fresh-user usability,
the real Claude/Codex clients' consent behavior, or application advantage over a baseline. Those are
separate release gates in [v0.1.1](releases/v0.1.1.md).
