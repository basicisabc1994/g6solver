# AI building AI applications with G6

The objective is an agent that constructs an application from components, identifies a failure,
proposes a redesign and measures it against a frozen baseline. The evaluator, authorization and
promotion rules remain host-controlled. Better performance is a hypothesis to test.

## Install and discover

Use Python 3.12 or 3.13. From the checkout, install `pip install ".[all]"`, run `g6 doctor`, then
`g6 setup --client claude --allow patch_review.review` or `g6 setup --client codex --allow patch_review.review`.
Setup uses an absolute interpreter path, installs the using-g6 skill, and ends by showing the first review
call. Existing skills are preserved. An existing MCP entry that the client refuses to replace must be
inspected with that client's `mcp get g6` command; to replace it, remove it (`claude mcp remove --scope user
g6` or `codex mcp remove g6`) and run setup again.

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

Register permission with `g6 setup --client claude --allow patch_review.review` (or `--client codex`). Call
`g6_review_patch`
with the repository root, the task, `requirements` (a list of `{id, text, checks}`, where `checks` are
pytest node IDs and may be empty), optional extra `tests`, and a base ref (default `HEAD`). A `claim` and
`sources` are optional: their lexical support is reported and decides nothing. Poll its job.

An `intent` is optional too: an intent contract with a `goal` and any of `success_criteria`, `non_goals`,
`failure_criteria` and `assumptions`. Its items count as stated by you. Each success criterion becomes a
requirement, and each non-goal or failure criterion an obligation to keep behaviour (`kind: preserve`). Each is
not established until a requirement with the same text names the tests that check it; the two are merged, never
counted twice. Each assumption is listed in the scope with its source (`explicit`, `inferred` or `fallback`); it
is not a requirement and decides nothing. The intent's own `success_checks` and `failure_checks` test the text of
an answer, not a repository, so they are not used, and the unknowns say so. An intent that is not valid is
refused. Each requirement and each check in the reply says where it came from (`provenance`).

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
never a defect. So is every check of a run whose tests all passed by pytest's own count but whose process then
exited with an error (`run_inconsistent`, insufficient evidence: a graphical test suite whose display
connection fails at exit, say). That cannot get a patch accepted, so it is not an integrity problem; a run
that exits 0 while its tallies show a failure, an error or fewer tests finished than collected is one. Tier 2, the other tests that touch the changed modules, runs only when tier 1 found no
integrity problem and no defect; one that fails, or fails to collect, on the patch is replayed at the base
commit (a moved test file at its old path), and counts as a defect only if it passed there. When nothing so
far found a problem, the stated checks that passed also run against the base commit's code, in a copy: one
that passes there too may not exercise the change, and is listed as an unknown with a next action, never an
outcome (a requirement to keep behaviour passes at the base by design). Tier 3 (mutation, old-against-new
probes) runs only when you ask: mutation for test adequacy, and old-against-new probes as `differential.v1`
template instances (below).

`proposed_checks` is optional: at most 20 test files you propose, each `{id, requirement_id, intent, file,
source, test}`. `intent` is `preserve` (behaviour the patch must keep) or `change` (behaviour it must bring);
`file` is a relative path named `test_<name>.py`, and `source` its text, at most 64 kilobytes; `test` is an
optional node identifier inside it. A conftest, a pytest or tox configuration, a `.pth` file, an `__init__.py`,
a path with `..`, an absolute path or a link is refused, and so is the whole review, before anything runs. G6
places each check in a folder of its own (`g6_check_` and the start of its text's SHA-256) and, unless
integrity is already in doubt, runs it confined with no network, in a fresh copy of the patch and in one of the
base commit's code, neither holding the repository's history. Each run has its own time limit inside the
review's; checks the time cannot reach are listed as skipped. Where no operating-system boundary can confine
these runs, the review is refused, even where you allowed unconfined runs. The results: for `preserve`,
`counterexample` (passes at the base, fails on the patch) or `fails_on_base_too` (nobody is accused); for
`change`, `unconfirmed_failure` (fails on the patch, whatever the base gives: a new function cannot even be
imported there); otherwise `not_refuted`. A check whose own file the patch stops collecting (it imports a name
the patch removed, say) has failed, and its run at the base commit decides, as for the repository's own tests.
A run that times out, changes its copy, cannot be collected or set up for another reason, fails on an
operating-system error or on a refusal a client library wraps (the boundary refusing a file outside the copy,
the network or a name lookup), or fails without saying how, is a `check_error`, counted and never a finding.
A probe prints one line per observation, `G6-OBSERVATION {"input": ..., "output": ...}`, to the real output
(in a test, inside `with capsys.disabled():`), at most 32, each at most 1,000 characters and 16 levels deep;
when both runs pass and the observations differ it is `behaviour_changed`, a question only, and the differing
values are reported as JSON text. A proposed check never passes a requirement: the reply says how many did not
refute it. A counterexample or an unconfirmed failure gives at most insufficient evidence
(`unconfirmed_counterexample`, `unconfirmed_failure`), never a defect. The reason says so in G6's own words;
what the check printed or failed with is your text and stays in `assessment.completion`. The reproduction is in
the next actions: the check's text and SHA-256, its path and node identifier, the plain `python -m pytest` run
G6's run amounted to (`PYTHONPATH=src` where G6 put `src` on the import path, an empty configuration where the
repository has none; with `import_roots`, a `python -c` start that puts them before the top folder, as G6's run
had them, on any Python from 3.9), which needs no G6, the interpreter and boundary each run had, and the digests of the two
copies it ran in. The check is code you wrote: G6 ran it only inside its boundary with no network, so read it
before you run it, or run it in a container.

`template_instances` is optional too: instances of G6's reviewed templates, counted with `proposed_checks`
within the same 20, each `{id, template, requirement_id, intent, bindings}`. A template is G6's own code; an
instance is data. The templates:

- `example.v1`: a stated input gives a stated result, error or effect. Bindings: `function` and `cases`, each
  with `args` and `kwargs` and exactly one of `returns` (compared as JSON writes both: 2 and 2.0 differ, a
  tuple reads as a list), `raises` (an exception type) or `effect` (`{argument, equals}`: an argument's value
  after the call);
- `relation.v1`: a relation over at most 32 `inputs`, each the function's one argument: `round_trip` (with
  `inverse`), `idempotence`, `invariance` (with `transformation`) or `monotonicity` (inputs in ascending order;
  `order` `increasing` or `decreasing`, `strict` true or false);
- `access_rule.v1`: who may perform an `operation` on a resource: `cases` with `who`, `resource`, the call's
  `args` and `kwargs`, and `expected` `allowed` or `refused`, at least one of each; `refusal` is `exception`
  (with the `exception` type it raises) or `falsy_return`. One call per case;
- `differential.v1`: outside the change's declared scope, the base commit and the patch agree on the `calls`
  you state, on outputs or on exceptions only (`mode`). A difference is a counterexample when the instance's
  `intent` is `preserve`, and a question (`behaviour_changed`) when it is `change`. Outputs are compared in a
  canonical form, never by their repr: a mapping as its items sorted, a set sorted, another object as its
  type's qualified name and its attributes. When G6 sees a difference it observes the base commit again: an
  input the base commit gives differently on two runs (an order that follows the hash seed, say) is listed as
  `unstable` and is never a counterexample;
- `forbidden_dependency.v1` (intent `preserve`): the patch adds no import that breaks a layer rule. With
  `contract: "repository"`, the rule is the repository's own import-linter configuration (`.importlinter`,
  `setup.cfg` or `pyproject.toml`; layers and forbidden contracts, with their `ignore_imports`) as the base
  commit declares it, so the patch cannot loosen it; otherwise `package` and a `contract` of `layers` (highest
  first) and `forbidden` pairs (`{source, forbidden}`) you state. G6 reads the imports from the package's files
  before and after and runs no code. It follows chains of imports inside the package as import-linter does, so
  a new path from a lower layer to a higher one is a new violation (a forbidden contract with
  `allow_indirect_imports` checks direct imports only); imports under `if TYPE_CHECKING` count unless the
  configuration sets `exclude_type_checking_imports`; dynamic imports are not seen. An option, contract type
  or layer syntax G6 does not implement (`contract_types`, `include_external_packages`, `exhaustive`, sibling
  layers such as `a | b`, an `independence` contract, any option it does not know) makes the instance not
  applicable, with the option named: G6 never guesses what an option means. So does a package that two
  folders hold (the top folder and `src`, or an import root). A part of a contract that
  matches no module, and a module outside the package, are reported as out of scope, never as violations.

Bindings are data. A name is a dotted name of the letters A to Z (either case), digits and `_`, a module and
then a top-level name in it (`package.module.function`), with no keyword and no double-underscore name; an
exception is a built-in exception's name or such a name; a literal is JSON data, at most 16 levels deep and 32
kilobytes in all. Anything else is refused, and so is the review, before anything runs. G6 then finds each name by reading the files, never importing them: a module in the top folder
or in `src` and a top-level definition in it, on the working tree for an instance whose intent is `change` and
at the base commit for one that keeps behaviour; each call's arguments must fit the function's parameters. An
instance the repository gives nothing to apply to is refused alone, listed in the activities with the reason;
the review goes on. G6 writes each check from the template's fixed text, with your bindings in it as one
literal and nowhere else, and runs it as it runs a proposed check: it never passes a requirement, and a
counterexample or a failure gives at most insufficient evidence, with its reproduction (the text names its
template). A forbidden import against the repository's contract or yours is a failed check, so
`defect_detected`. A structural check that finds none never passes a requirement (the owner's ruling, R-C1):
the requirement keeps the status its stated checks give it, `not_established` when it has none, and the
contract holding is reported in the activity and under `completion.preservation`. When the patch could not
have broken the contract (it has fewer than two layers and no forbidden pair, or the patch adds no import
from a module the contract covers), the check reads `vacuous`: "does not exercise the change".

The formal path searches multi-step behaviour, and never lets a result about a model read as one about the
code. `state_invariant.v1` takes an abstract state model you state, in the closed grammar of G6's model
checker (`variables` with their sorts, `init`, `transition` with `next_<name>` for the next state, named
`invariants`; no command, comment or keyword, at most 64 variables), and binds it to the code: `constructor`
(a call that makes the initial state), `action` (the whole-number state variable whose value says which action
led to each step), `actions` (each such value's operation, called with the state first and literal
arguments), `observe` (how each state variable the invariants read is read on the real state: a call's value,
or with `refusal` an access outcome), `depth` (at most 12), `traces` (at most 16) and `search` (`bounded` or
`induction`). G6 first checks the invariants themselves: a tautology, or invariants no state satisfies
together, is reported and never searched. Then it searches the model, restricted to the actions you map: a
bounded search asks again, each found sequence of actions excluded, up to the number of traces; induction
proves or refutes the invariants on the model. Each trace is replayed on the real code by a test G6 writes
from the template's fixed text: the operation calls in order from a fresh initial state, then the observed
variables read and the invariants evaluated on them (with the model's own values for variables the code does
not show). It runs confined on the patch, and at the base commit when the intent is `preserve`. Only a trace
that breaks the invariants on the real code is a finding about the code, and then an unconfirmed one
(`unconfirmed_counterexample`, at most insufficient evidence); one the base commit breaks too is
`fails_on_base_too`. A trace the code keeps is `model_only`: the model was broader than the code, and nobody
is accused. A proof by induction, or a bounded search that finds nothing, is about the model only: the
requirement is left exactly as it was and says so. Each result comes in three parts, in its activity:
`specification` (template, where the bindings and the model came from, and the check of the invariants),
`model` (operation, verdict, depth or k, traces) and `correspondence` (`reproduced`, `not_reproduced` with
`model_only`, `not_replayable`, `not_replayed` or `not_searched`), and every model run is an activity.

`access_rule.v1` also takes a multi-step form, for grants, shares and revokes: `constructor`, the access as
`operation` with its `args` and `refusal`, `steps` (each an operation call with its `effect` on whether the
access may be allowed: `allow`, `refuse` or `none`), `initially`, `depth` and `traces`. G6 builds the model from
the steps and goes through `state_invariant.v1`'s path, so a revoke followed by the access is tried on the
real code. The single-step form is as before.

When a `relation.v1` or `differential.v1` function is one return statement over whole numbers and booleans
(arithmetic, comparisons, a conditional), G6 also reads its source as data and asks Z3 for an input between
your smallest and largest that breaks the relation (for `differential.v1`, where the base commit's version and
the patch's differ). Each input Z3 finds is re-run confined on the real code as a further check of the
instance (its id with `.symbolic`, counted within the 20) before it is reported; one the real code does not
reproduce is dropped and counted. A function outside that form is an activity that says "not in the subset",
never an error.

`search` is optional: `{"templates": false}` runs your free-form checks only, and lists each template instance
as skipped; `{"test_adequacy": true}` also measures test adequacy. The configuration is in `scope.search`.

`{"propose": true}` has G6 ask a model for checks, off unless you ask: the model you configured for G6
(`G6_LLM_PROVIDER` in your key file; with none, no call is made), or, from Python, the caller you pass as `llm`.
`"proposer": "templates_first"` (the default) asks template by template (with edge inputs for `example.v1` and
`differential.v1`), then for at most 3 distinguishing scenarios, asked as questions, then for free-form tests
only for the requirements no proposed instance applied to; `"proposer": "free_form"` asks for free-form tests
only. Limits: `max_model_calls` (default 10), `max_input_chars` per request (60,000), `max_output_tokens` per
reply (4,096) and `max_context_chars` (24,000). Reaching one is a skipped activity with its reason; a request
past the input limit is never sent. Everything the model returns is untrusted data. Each reply must fit a closed
schema, so a reply that claims a check is stated, adopted or confirmed is refused. An instance it proposes is
checked by its template (its bindings' provenance is `model_hypothesis`). A free-form test is a file G6 names
and places, run confined; a conftest, a configuration or a plugin is refused. What it proposes never passes a
requirement and gives at most `insufficient_evidence`, with its reproduction. Without a boundary for the checks
it would bring, no model is called. Every model call is an activity with the provider's token counts and the
model that answered, where the caller gives them; every request and reply is in
`assessment.completion.model_exchanges`. The repository context it sends is built by
`g6.harness.verification_proposer.repository_context`, which you can call on its own.

Every reply has `assessment.report`: the review as Markdown, at most about 40 lines, for you to show the
person as it is. It comes in this order: a headline in plain words (the outcome, "qualified draft", "merging
needs your separate approval"); the reproduced findings, integrity first, each failed check with its
requirement, the repository's plain command that reproduces it (such as `python -m pytest test_calc.py::test_add`;
G6 ran it with its own isolating options, and the exact command is in `reproduction.command`), and whether a base
run shows it passes at the base commit; what is not established and what would settle it; what was
demonstrated, with "fails at the base commit, passes on the patch" where the base run shows a requirement's
checks exercise the change; at most 3 questions and merging, for the person to decide; how it was decided
(proposed, tested, gate, approve), in plain names: the stated checks, tests that touch the changed code, deeper
checks; and the standing note that the task's specification may be incomplete. It is derived from the
other fields and decides nothing. Text from the repository or the request in it is shown inert, in code spans.

The repair loop, as the using-g6 skill gives it: fix what G6 reproduced; on the next call, add each earlier
reproduction as a stated check of its requirement (a proposed check's reproduction is a test file, added to the
repository's tests and listed by its node id), so the person can see the fix pass the original counterexample;
review again; and stop after 3 rounds, when a round changes nothing, or when the report says only the person can
decide. Never call a patch verified, and never merge it on the person's behalf.

Every reply has `assessment.completion`, built from what ran: `demonstrated` (requirements passed on their
stated checks, with the receipts), `not_established` (with what would settle each), `defects_reproduced`
(failed stated and repository checks apart from unconfirmed proposed ones, each with its reproduction),
`preservation`, `test_adequacy`, at most three `open_questions` showing the competing behaviours, and
`activities`: every run and every skip, with its result, seconds and model calls (none). With `test_adequacy`
asked for, G6 makes small changes to the changed lines (at most 6 in each of at most 3 files: `<` to `<=`, `+`
to `-`, a number plus one, a returned value to `None`), runs each requirement's stated checks against each one,
confined in a copy, and reports how many changes they caught and which survived, with every change not run and
why. That changes no outcome: a change can behave as the original does, so a survivor is a question about the
checks, not a defect. Making each change copies the whole file, so the work grows with the square of its size: a
changed file of more than 20,000 characters is left out, with why, and making the changes stops at the review's
deadline. A changed file that is a link to a path outside the repository is never read in G6's own process (the
rule the copies follow); it is left out, with why, and named in the unknowns.

G6 reads the template instances against the files after the tier 1 run, so reading and parsing a large package
never takes tier 1's time, and the structural check stops at the review's deadline. When G6's own work in its
process fails in any search stage (a file nested so deeply that Python's parser or G6's reading of it runs out of
depth, a RecursionError or a MemoryError, or anything else), that activity says `error` with its reason, and
the outcome from the other checks stands, with an unknown that the stage did not complete. The review itself
never fails for it, over MCP's job either.

A submodule that was never set up here (a gitlink with no `.git` in its folder, as some repositories ship
their vendored code) is accepted: git never enters its folder, and G6 hashes the folder's files itself into
the digest, so a run that changes one of them is caught as a tracked file would be. Git holds nothing to
compare that folder with, so whether the patch changed its files is listed as an unknown. A patch that changes
the gitlink entry itself, or a folder too large for G6 to hash, is insufficient evidence
(`change_inside_submodule`, `submodule_not_hashed`). A submodule that was set up (a `.git` in its folder, or
anything under `.git/modules`) is refused, `G6_E_GIT_SUBMODULE_REFUSED`. The copies the base-commit runs use
keep a tracked link only when it is relative and resolves inside the copy; each run lists the links it left
out. On Windows the copy makes each link with the system's separator, and as a folder's link when it names a
folder.

Nothing in `.git` that the patch's author controls hides a change. G6 lists the changed files with a private
index it builds from the base commit's tree, never the repository's own index, its stat cache or an entry
written into it, and reads no replace ref; every file's content is compared with the base commit's blob, after
git's own line-ending conversion, so an untouched file on a Windows checkout is never a change. A tracked file
marked assume-unchanged or skip-worktree is compared with the base commit's text, as every other tracked file is. One so marked that is not in the working tree (a
sparse checkout) cannot be compared, so the outcome is at most insufficient evidence
(`file_not_in_working_tree`), with an unknown, never "unchanged".

The tests run with the Python that `G6_REPO_PYTHON` names where you start G6 (a full path: a Poetry or conda
environment, or a system Python that holds the repository's dependencies), else the repository's own `.venv`
or `venv`, else G6's own; `scope.environment.interpreter_source` says which (`setting`, `repository` or
`g6`). Only G6's own environment sets it, never a request, and a setting that names no Python is a refusal
(`G6_E_REPO_PYTHON_INVALID`). So is an environment whose confined run would read a folder outside the repository
that holds no Python standard library, before anything runs: the base installation its `pyvenv.cfg` names (the
`home` line, which the repository or the patch can write) must be a real one, and the repository's own `.venv` must
not lead out of it, since the boundary opens those folders, read only, to the tests. The tests see none of your
environment but what `G6_REPO_ENV` gives them, a
JSON object of variable names to text set where you start G6 (`{"QT_QPA_PLATFORM": "offscreen"}`, say);
names that would change how G6 runs them (`PYTHON...`, `LD_...`, `DYLD_...`, `PYTEST_...`, `G6_...`,
`COV_CORE_...`, `PATH`, `HOME`, `USERPROFILE`, `TMP`, `TEMP`, `TMPDIR`) are refused (`G6_E_REPO_ENV_INVALID`).
The run, `scope.environment` and the receipt record each name with the SHA-256 of its value, never the value.
Hypothesis keeps its database in the run's own folder (`HYPOTHESIS_STORAGE_DIRECTORY`, unless `G6_REPO_ENV`
names it), since the repository is read only to a confined run; `env_set_by_g6` says so.

Each run lists the modules its pytest process had loaded from outside the tree it tested when the session ended
(an environment inside the repository, below `site-packages`, counts as outside; a subprocess's or a forked
child's modules are not seen). When a module the patch changed came from
there instead, an installed copy such as a package installed without `-e` beside a `lib/` layout, the run tested
code the patch did not change: its passes and failures are not used, the outcome is at most insufficient evidence
(`changed_code_not_loaded`), and an unknown names the module and where it came from. Name the folder that holds
the package in `import_roots` (at most 8 folders inside the repository, such as `["lib"]`): they go first on the
import path of every run of the review, before the repository's top, and are recorded in `scope.import_roots`.
Such a run, or one whose list is incomplete, gets no test receipt.

Proposed checks, the checks G6 compiles from template instances, and test adequacy's mutant runs run the same
way as the stated checks: the same import roots first, the same Python and the same variables. One of their
runs that loaded a changed module from outside the tree, or cannot say what it loaded, tested code the patch
did not change: it is a `check_error` (a mutant: not run), with that reason, never `not_refuted` and never a
counterexample. A template's names are found in the import roots first, as those runs import them.

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
the search configuration, and the total time; `unknowns` and `next_actions` follow.

A stale supplied artifact digest, invalid input, a git error, or a run no operating-system boundary
confined is a refusal: `blocked-escalated`, with no outcome. Merging is separate: `merge.status` is always
`requires_separate_approval`, coding-gate findings other than shortcuts are listed as merge reasons, and
the application never merges code.

Patch review needs an operating-system boundary (Linux with bubblewrap, Windows after the one-time access
grant): allowing unconfined runs does not make it run, and on macOS it refuses. The patch's own code runs in
pytest's own process, in the repository's own environment (its `.venv`, which the agent can change): a patch
written to defeat the checks can make them report a pass, and G6 does not catch that.

## Boundary and validation status

Linux requires a functioning bubblewrap installation; Windows uses AppContainer with the required access
grants. Other configurations refuse untrusted code unless the user explicitly permits unconfined execution.
An isolation record describes enforced restrictions, not complete security. Local preference files and
client consent assume a trusted host. An unrestricted same-user shell can change those files or inspect
held-out cases; use a separately protected host/account when that threat matters.

Automated API and FastMCP-client tests validate mechanisms. They do not establish fresh-user usability,
the real Claude/Codex clients' consent behavior, or application advantage over a baseline. Those are
separate release gates in [v0.1.1](releases/v0.1.1.md).
