<!-- SPDX-License-Identifier: Apache-2.0 -->
# Worked examples on well-known Python projects: the plan (fixed 2026-10-29, before any run)

**Label.** Worked examples: mechanism demonstrations on real code; they show what happened in these runs and say
nothing about how often G6 helps.

These runs are separate from the registered studies V9 and V10 and touch none of their material, code or budget.
They show G6 in the product's real flow: a person installs G6, runs `g6 setup`, and asks Claude Code to fix an issue
in a well-known Python project; Claude Code follows the `using-g6` skill, reviews its patch with `g6_review_patch`
and repairs what G6 reproduces. Each outcome is then checked independently against the fix and tests the project's
maintainers merged later, which neither the agent nor G6 sees.

This file was committed before the first paid call and before any candidate task was looked at. After that commit
nothing here changes; a needed change is a new dated section at the end that says why, written before the runs it
affects.

## 1. Projects

A candidate project is kept when all of these hold, each read on the day the rule is applied:

- **P1, well known:** at least 10,000 GitHub stars (`gh api repos/<owner>/<name>`, `stargazers_count`), or among the
  100 most downloaded packages on PyPI in the 30-day list at <https://hugovk.github.io/top-pypi-packages/>.
- **P2, permissive licence:** GitHub's licence for the repository is MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0 or
  PSF-2.0, and the licence file at the task's parent commit says the same.
- **P3, tests that run offline:** the project's tests run with pytest, and at each task's validation (section 3) the
  maintainers' tests run in a container with no network. A project whose tests cannot is dropped at its first task,
  and the record says why.
- **P4, in no SWE-bench dataset:** the repository is not one that SWE-bench, SWE-bench Verified, SWE-bench Multimodal
  or SWE-bench Pro publishes as a source. No benchmark data is downloaded or opened; only the published names and
  descriptions are used:
  - SWE-bench takes its tasks from 12 repositories, listed in Table 12 of its paper (Jimenez et al., "SWE-bench: Can
    Language Models Resolve Real-World GitHub Issues?", arXiv 2310.06770): astropy/astropy, django/django,
    pallets/flask, matplotlib/matplotlib, pylint-dev/pylint, pytest-dev/pytest, psf/requests,
    scikit-learn/scikit-learn, mwaskom/seaborn, sphinx-doc/sphinx, sympy/sympy and pydata/xarray. SWE-bench Verified
    is a human-validated subset of SWE-bench's test set, so it uses the same repositories. These 12 are excluded by
    name.
  - SWE-bench Multimodal (Yang et al., arXiv 2410.03859) takes its tasks from 17 repositories, all JavaScript and
    TypeScript: GoogleChrome/lighthouse, PrismJS/prism, alibaba-fusion/next, bpmn-io/bpmn-js,
    carbon-design-system/carbon, eslint/eslint, grommet/grommet, highlightjs/highlight.js, openlayers/openlayers,
    prettier/prettier, quarto-dev/quarto-cli, scratchfoundation/scratch-gui, Automattic/wp-calypso, chartjs/Chart.js,
    diegomura/react-pdf, markedjs/marked and processing/p5.js. None is a Python project.
  - SWE-bench Pro (Deng et al., arXiv 2509.16941; <https://scaleapi.github.io/SWE-bench_Pro-os>; the README of
    `scaleapi/SWE-bench_Pro-os`) names none of its 41 repositories. It says its public and held-out sets come from
    repositories under strong copyleft licences (GPL), and its commercial set from private startup codebases that
    are not released. A project kept by P2 has a permissive licence and is public, so it is outside all three sets.
  These sources were read on 2026-10-29.

**Candidates,** named before any task was looked at: pydantic/pydantic, psf/black, pallets/click, pallets/jinja,
encode/httpx, encode/starlette, fastapi/fastapi, Textualize/rich, networkx/networkx, python-attrs/attrs,
pypa/packaging, marshmallow-code/marshmallow, and three the rule is expected to drop: pallets/flask and psf/requests
(SWE-bench) and python-pillow/Pillow (its licence is MIT-CMU, which P2 does not list). The selection record says, for
each, which rules it met and why it was kept or dropped.

## 2. Tasks

A task is a bug the maintainers fixed recently. For each kept project, merged pull requests are read newest first
(read-only GitHub API calls on the host) and a pull request qualifies when:

- **T1:** it was merged into the default branch on or after 2026-06-01 and on or before 2026-10-29;
- **T2:** it closes exactly one issue (GitHub's closing link), and that issue reports a bug with a clear expected
  behaviour: it says what happens and what should happen instead, or gives a reproducer and names the wrong result.
  An issue about a security vulnerability is left out. The record gives a one-line reason for each issue judged;
- **T3:** it changes at least one Python source file that is not a test, and adds or changes at least one Python test
  file (a file under a `test` or `tests` folder, or named `test_*.py` or `*_test.py`, or a `conftest.py`); it changes
  no source in another language (C, Cython, Rust and the like); documentation and changelog files do not count;
- **T4:** its parent commit is well defined: the first parent of its merge commit when that commit has two parents;
  when it has one (a squash or a single rebased commit), that commit's parent, provided the files that commit changes
  are the pull request's files. Otherwise it does not qualify, and the record says so.

The two newest qualifying pull requests of each kept project are its tasks. **The run order** is all tasks of all kept
projects by merge time, newest first. Runs go in that order until the list ends or the dollar stop (section 6) is
reached; tasks not reached are listed as not run.

## 3. Validation of a task, before its run (no model)

In the task's image (section 4), with no network:

1. the project installs at the parent commit, and its tests start;
2. the maintainers' added and changed test files are put in place on the parent commit's code (the files as they are
   at the merge commit; test files the pull request deletes are deleted), and those test files are run: call this
   result **base**;
3. the same test files are run with the maintainers' whole fix in place: **fixed**.

**FAIL_TO_PASS** is the set of tests that fail in base and pass in fixed; **PASS_TO_PASS** the tests that pass in both.
A task whose FAIL_TO_PASS is empty, or whose project does not install or run offline, is dropped before its run, the
record says why, and the next task in the order takes its place. Validation never involves the agent or G6.

## 4. Each run, in the real product flow

- **The image** (one per task) is built from the local image `g6-a2-linux` (Python 3.12, git, bubblewrap); its image
  identifier is recorded. On top of it:
  - **G6** from the tip of `origin/claude/launch` at the time of the run, installed as the README's first journey
    does (`pip install "<source>[all]"`, into its own virtual environment). The tip must contain the fix pass's
    Critical commit on the confined run's read paths (5dad0fe, "a confined run never reads a folder the repository's
    environment names"); no run starts before it is on `origin/claude/launch`. Each run records the commit.
  - **Claude Code** 2.1.309, linux-x64, the build V7 registered (SHA-256
    `4d704a8aecbe038affa3000f498ae17c1b4f753add8facb8764120affc99b658`).
  - **The project** at the parent commit, as a new git repository with that tree as its only commit, so neither the
    fix nor anything after it is in reach; its development environment in `.venv` inside it (kept out of git through
    `.git/info/exclude`), installed with the project's own test dependencies by the command the selection record
    gives for the project.
- **The container** has its own name (`g6we-...`), runs as an ordinary user (not root), with the seccomp and AppArmor
  settings bubblewrap needs inside Docker, and is attached only to an internal network whose one way out is a proxy
  container of the run's own that passes `api.anthropic.com:443` and nothing else. Both are stopped and removed by
  their names when the run ends; no other container is touched.
- **Setup:** `g6 setup --client claude --allow patch_review.review` runs in the container as that user, before the
  agent, and its output is recorded, with `g6 doctor`'s.
- **The key** comes from `g6.core.keys` on the host and goes only into the environment of the `docker exec` that
  starts Claude Code, by name; it is never printed, logged or written into a record, and every archived text is
  masked for it. **Before the run, a key check:** in a container from the same image, a process holding the key in
  its environment, as Claude Code does, asks G6 to review a scratch repository whose one test looks for the key in its
  own environment, in every process environment it can read under `/proc`, and in the key file's paths. G6 runs the
  test confined; it must find nothing. A control runs the same test unconfined in the same environment and must find
  the key. Only yes or no is recorded. A run whose key check fails does not start.
- **The agent:** Claude Code, headless, model `claude-sonnet-5-5`, effort left at Claude Code's default, in the
  repository's folder: `claude -p --model claude-sonnet-5-5 --output-format stream-json --verbose --max-turns 200
  --max-budget-usd 10 --dangerously-skip-permissions --disallowedTools WebFetch WebSearch`. It uses the user-level
  registration and skill `g6 setup` made. Web fetch and web search are off so that the public fix stays out of reach.
  Nobody steers it during the run. Its prompt is this text, with the issue's title and text (not its comments)
  filled in:

      You are working in a clone of {owner}/{name}, at the commit just before the maintainers fixed the issue below.
      The project's development environment is the virtual environment in .venv (its Python is .venv/bin/python).
      There is no network apart from the model.

      Fix the issue. Deliver the fix the way the using-g6 skill says: write the requirements and their checks,
      review the patch with g6_review_patch, show its report, and repair what G6 reproduces. Nobody is available to
      answer questions during this run: where the skill says to ask the person, put the questions in your final
      report and carry on with the most likely reading. Leave your changes uncommitted in the working tree. End with
      your final report.

      Issue #{number}: {title}

      {text}

- **The per-run cap:** $10 (Claude Code's own budget flag, and the runner's count at the prices in section 6 stops the
  run past $10.50), 200 turns (Claude Code's flag; the runner stops a run past twice that many model calls), and 120
  minutes of wall clock.
- **Rounds:** each `g6_review_patch` call is a round. A hook in the container's Claude Code settings saves the working
  tree's diff against the base commit just before each call, through a separate git index (the repository's own index
  is not touched); it says nothing to the agent. G6's replies are read from the transcript.
- **After the run:** the final patch (the diff against the base commit, new files included, `.venv` left out), the
  transcript, standard error, the round patches, G6's replies, the proxy's log and the costs are archived. The archive
  is the local artifacts folder outside the repository, never the repository.

## 5. The independent check, after each run (no model)

- **The patch:** in a fresh container from the task's image, with no network, the agent's final patch is applied to
  the base commit, the maintainers' test files are put in place as in section 3, and those files are run. The patch
  **passes the independent check** when every FAIL_TO_PASS test and every PASS_TO_PASS test passes; otherwise it
  fails, and the failing tests are listed. The same check is run on each round's patch and reported beside it.
- **Each finding G6 reproduced** in a round (a failing stated check, a counterexample, a regression of the
  repository's tests, an integrity reason) is judged:
  - **real** when it fails on that round's patch for a reason the maintainers' fix or tests confirm: G6's
    reproduction passes with the maintainers' fix in place, or a maintainers' test fails on that patch for the same
    behaviour;
  - **false alarm** when the maintainers' fix shows the behaviour G6 flagged (the reproduction fails there in the
    same way), or the flagged behaviour is what a maintainers' test requires;
  - **undetermined** when neither shows it, with why.
  Each judgement records the reproduction, its run on the maintainers' fix where it can run, and the reason in words.
- **Also recorded:** G6's outcome in each round and at the end, each round's report verbatim, the agent's final
  report, its patches round by round, costs, times, and anything that went wrong.

## 6. Spend

A hard **$100** stop for all worked examples together. Cost is counted from each response's reported usage at
Anthropic's published prices for Claude Sonnet 5.5, read on 2026-10-29 from
<https://platform.claude.com/docs/en/about-claude/pricing>, per million tokens: $2 input, $2.50 for a 5-minute cache
write, $4 for a 1-hour cache write, $0.10 for a cache hit, $10 output. A cache write whose duration the usage does not
give is counted at the 1-hour price. A model Claude Code calls besides the main one is counted at that model's
published price, and a run's cost is the larger of the transcript's count and Claude Code's own per-model usage. A
run starts only if the money spent so far plus $10.50 stays within $100. Every run counts, failed ones included. This
stop is separate from the rehearsal's $15 and from V9's $60.

## 7. What is reported

Every run the rule chose, in order, including runs where G6 found nothing, was wrong, or the run failed:
`RESULTS.md` beside this file holds the summary table (project, its licence, links to the issue and the fix, G6's
outcome by round, its findings with real, false alarm or undetermined, the independent check on the final patch and
on each round's patch, cost and minutes) and the projects and tasks the rule dropped, with why. No run is dropped or
run again to get a better result. A run may be run again only after a failure of this plumbing (a container that did
not start, the proxy, the API refusing before any token, a fault in these scripts); the failed attempt stays in the
archive and the table, marked as such. Results are described in plain words: what G6 did in these runs, never a rate.

## 8. What these runs cannot show

- How often G6 helps: there is no comparison arm, and the tasks are few.
- Whether the model saw a fix during its training: the fixes are public and recent, and the model's training data is
  not known here. Newest merges go first.
- Anything about other models, other agents, or settings other than these.
- G6's own components beyond what the agent chose to call.

## 9. Changes after the plan's commit

### 2026-10-29: a changed test data file is run through the test module that reads it (before any run)

**Why.** Applying section 2 showed that one of the chosen tasks (psf/black #5521) has its test case only in a data
file under `tests/data/cases/`, which a test module (`tests/test_format.py`) reads and runs, one test per file. Run directly, such a file is not a pytest module, so "those test files are run" (section 3) would run
no test at all, and the task would be dropped for a reason that has nothing to do with the fix.

**Change.** In sections 3 and 5, a changed or added file under the tests folder that holds no pytest test (no
function or class pytest collects) is run through the test module of the project that reads it: that whole module is
run in its place. The selection record names the module for each task. Nothing else changes.

### 2026-10-29: G6's own environment follows the README at the commit run (before any run)

**Why.** A check with no model, before any run, found a defect in G6 at the tip of `origin/claude/launch`
(f3411d1): when G6's own virtual environment and a repository's `.venv` are both made with `python -m venv` from the
same Python (links, the default on Linux), G6 takes the repository's interpreter for its own, does not give the
confined run the repository's `.venv`, and every review stops with `G6_E_ISOLATION_UNAVAILABLE`. The owner decided
(2026-10-29) that G6's confinement code is not changed before the launch: the launch line refuses that case up front
with an accurate message (item L1), and the README and Get started make G6's own environment with
`python -m venv --copies`, which avoids the defect. A project's `.venv` stays an ordinary one, made with links.

**Change.** In section 4, G6 is installed exactly as the README says at the commit run: `python -m venv --copies` for
G6's own environment, then `pip install "<source>[all]"` from that commit, then `g6 setup --client claude --allow
patch_review.review`. Each project's `.venv` is made the project's normal way, with links. No paid run starts before
the L1 commit is on `origin/claude/launch`; G6 is installed from a tip that contains both 5dad0fe and L1, and each
run records the commit. Before the first paid run, one review with that install and a project whose `.venv` uses
links must run confined, with no model call. Nothing else changes.

### 2026-10-29: runs 6 to 10 on G6 a923002, and the task's repository fetched from upstream (before run 6)

**Why.** Runs 1 to 5 were made on G6 2680cc9. They showed two problems, and the series was held before run 6:

- **G6's own server.** `g6 setup` registered the server with `python -c`. In pydantic's repository, G6's server
  imported the repository's `pydantic` and did not start (run 3). In starlette's repository, it imported the
  repository's `starlette` (runs 4 and 5).
- **The harness's repository.** Each task's repository was a new one made with `git init` and a plain `git add`. Black
  keeps test fixtures that its own `.gitignore` files match, so they were left untracked and git-ignored. G6's
  name-only rule then gave the integrity reason `ignored_file_can_change_tests` in runs 1 and 2; a real clone of
  Black gives no such alarm.

The owner chose option B: the series waits for G6's fixes and goes on with them. The worked examples' launch fixes W1
to W5 are merged on `origin/claude/launch` at a923002:

- W1 starts the server with `python -P`;
- W2 sends a compact reply, report first;
- W3b says what to do about git-ignored files;
- W4's template refusals name each missing binding and give a valid instance;
- W5 makes no network request at server start.

**Change.**
- **G6:** runs 6 to 10 run on G6 a923002 (it contains 5dad0fe and L1), installed exactly as the README at that commit
  says: in G6's folder, `python -m venv --copies .venv`, then `pip install ".[all]"`, then `g6 setup --client claude
  --allow patch_review.review`. Runs 1 to 5 stay as they were made, on 2680cc9.
- **The task's repository (section 4):** from run 6, it is upstream's base commit fetched alone (`git fetch
  --depth=1 --no-tags`), checked out detached, so git tracks exactly what upstream tracks and nothing after the base is
  reachable. Before each paid run, the runner checks four things:
  - HEAD is the parent commit;
  - HEAD's tree is upstream's tree for that commit (read from GitHub's API);
  - exactly one commit is reachable;
  - the working tree is clean (only `.venv` and the install's leftovers are excluded).

  If any check fails, no agent starts.
- **Validation:** the validation and the check's own control are taken again on the new images before the runs.

Nothing else changes: the tasks, their order, the prompt, the caps, the independent check and the $100 stop are as
before.

### 2026-10-29: Round 2: G6 with its own model (before any of its runs)

**Why.** The owner asked for it: "G6 + model is necessary to see uplift - we should run this please". In round 1
(runs 1 to 10) G6 made no model calls of its own, so it judged only the checks the agent stated. Round 2 switches on
G6's own check proposer: verification search's `propose` (VS-5), with the model the person configures for G6.

**What round 2 is.** The same ten tasks, run again in the same order. Each run is new: the agent writes a new patch,
which can differ from its round 1 patch. Round 1's records stay as they are.

**What changes from round 1:**

- **G6:** `origin/claude/launch` at 9ad134d, which includes the security review's fix pass (F1 to F6 and F8). It is
  installed exactly as its README says, with two G6-side settings:
  - **The proposer is on for every review, whatever the agent passes.** At 9ad134d only the caller's `search`
    argument switches it on: its default is off, and neither the server nor the environment has a setting for it.
    So, as the smallest G6-side default for this round, the image's copy of G6 carries a one-function change made
    by `round2_g6.py` before G6 is installed. In `g6.harness.verification_search.search_config`, the proposer's
    settings are fixed at the code's own defaults, with `propose` on: `templates_first`, at most 10 model calls a
    review (the code's default, which is higher than 6), 60,000 characters a request, 4,096 tokens a reply and
    24,000 characters of repository context. Every other `search` setting stays as the caller gives it. Nothing
    else in G6 changes.
  - **G6's model is claude-sonnet-5-5**, through the provider `anthropic`. These lines are written into G6's key
    file in the container (`~/.g6/.env` of the container's user, owner-only), the one place G6 reads a key from
    (`g6.core.keys`): `G6_LLM_PROVIDER=anthropic`, `G6_LLM_MODEL=claude-sonnet-5-5` and the key.
- **The key's path.** The host reads the key through `g6.core.keys`, as before, and gives it to two places in the
  container:
  - Claude Code's process environment, by name;
  - G6's key file, written through standard input. It is never printed, logged or archived. It is removed from the
    container before G6's state folder is copied out, and every archived text is masked for it.
- **The key check (section 4)** also puts the key file in place. Its confined test must find the key in none of its
  channels (the key file among them), and its unconfined control must find both the environment variable and the
  key file. With the proposer on, the check's own review uses G6's model, so the check also shows that the confined
  runs of a review that holds the key cannot read it. Before the first paid run, one G6 model call is made from the
  container through the proxy, to show the path works.
- **Cost.** G6's model calls do not appear in Claude Code's stream. They are counted from the model-call records in
  each review's full assessment, which G6 saves in its state folder. They are priced at the published Sonnet 5.5
  prices in section 6, with no cache discount assumed.
  - A run's cost is the agent's plus G6's.
  - A run starts only if the money spent so far in both rounds, plus $10.50 for the agent and $5.00 for G6, stays
    within $100.
- **The task's repository** is fetched from upstream, as from run 6 of round 1. **The independent check** is
  unchanged.

**What is recorded for each run:**
- G6's outcome in each round;
- the checks and template instances the proposer made, and which ran, failed or were refused;
- any reproduced counterexample, with its command, and whether the agent's repair loop used it;
- the independent check on every round's patch and on the final patch;
- G6's model calls and cost, the agent's cost, and the time.

**How it is reported.** Each task is compared with round 1 in RESULTS.md, under "Round 2: G6 with its own model", as
a worked example. The agent's patches vary from run to run, so a difference between the rounds is not evidence that
G6's model helps.

**Known limits of the proposer, to watch for and report:**
- it does not see the results between its calls;
- its free-form path had been tested only with a fake model;
- a check that a model proposed gives at most "not enough evidence" ("ask, don't accuse").

### 2026-10-29: round 2's G6 cost counted at an upper bound (before any of its runs)

**Why.** G6's Anthropic caller returns a reply's text but not its token counts, so the proposer's model-call records
hold characters only. Sonnet 5.5 also thinks by default, and those tokens are billed as output without appearing in
the text.

**Change.** Each of G6's model calls whose record gives no token counts is counted at an upper bound:
- input: the request's characters divided by two (code and English run nearer four characters a token);
- output: the full 4,096-token reply limit;
- both priced at the published Sonnet 5.5 prices.

The records say how many calls were counted this way. The $100 stop uses this count, so it can only overstate what
G6's model cost.

### 2026-10-29: round 2's G6-side change needs a second line (before any of its runs)

**Why.** A dry run with no model showed the proposer never running. At 9ad134d, `review_patch` builds no search
configuration at all when the caller passes neither `search` nor template instances, and then no proposer runs,
whatever `search_config` returns.

**Change.** `round2_g6.py` also adds one line to `g6.harness.patch_review._inputs`, so that every review gets the
search configuration: the caller's, or the defaults when it gives none. With the first edit, that means the proposer
runs on every review. Each anchor must be found exactly once in both files, or the image is not built. Nothing else in
G6 changes.

### Round 3 (2026-10-30): formal methods (before any of its runs)

**Why.** The owner approved it on 2026-10-30. In rounds 1 and 2, G6 reproduced no defect in any patch, and in five
runs of each round the final patch failed the maintainers' tests on cases no stated check reached. Round 3 asks what
happens when the agent is told to state each requirement as a property and check it with G6's formal tools, beside
Claude Code alone and G6 as shipped, on three models.

**Label: development.** These ten bugs, their fixes and the round 1 and 2 diagnoses have been seen, by the people who
wrote this section among others. Round 3 is development, not evaluation: it shows what happened in these runs, never a
rate, and no claim about G6 rests on it alone.

**What round 3 is.** The same ten tasks as rounds 1 and 2 (tasks.json, in its order), each run new. Three arms, which
differ only as stated here:

- **A, no G6:** Claude Code alone. `g6 setup` is not run, so no MCP server and no skill is registered, and the `g6`
  command is removed from the agent container's path before the agent starts. Its prompt is round 1's (section 4)
  with the G6 instruction taken out; its second paragraph reads: "Fix the issue. Nobody is available to answer
  questions during this run: put any questions in your final report and carry on with the most likely reading. Leave
  your changes uncommitted in the working tree. End with your final report."
- **B, G6 as shipped:** exactly round 1's setup: `g6 setup --client claude --allow patch_review.review`, the `using-g6`
  skill as shipped, `g6_review_patch` with no G6 model. Its prompt is round 1's, unchanged.
- **C, G6 and formal methods:** B's setup, and B's prompt with this addendum after its second paragraph, before the
  issue (a change to the prompt only, not to the shipped skill; it names no bug, project, test or diagnosis):

      Before you say the fix is done, also check it with G6's formal tools:
      1. State each requirement of the issue as a property: what must hold, and for which inputs.
      2. Check each property with G6. Compare the patch with the base code on the inputs the issue names and on variants
         of them: a differential.v1 template instance in g6_review_patch (intent preserve where behaviour must stay, change
         where it must change). Where a property is over whole numbers and booleans, or a small state machine, also check
         it through g6_invoke: component formal (find_counterexample, check_validity, check_equivalence) or model_checking
         (check_invariant, prove_invariant); g6_describe gives each op's parameters. For behaviour over several steps, use a
         state_invariant.v1 template instance.
      3. Repair what a check refutes, then review again with g6_review_patch. Stop when a review reproduces no new
         counterexample and every property you stated has been checked, or when the budget ends.
      In your final report, list each property, the tool that checked it, and what it gave.

  The `formal` ops and `model_checking`'s symbolic ops run Z3 in process and need no permission beyond B's; TLC and
  SANY are not offered (they are sensitive, and the image has no Java).

**Models and order.** Claude Code headless, as in section 4, with `--model` `claude-haiku-4-5`, then
`claude-sonnet-5-5`, then `claude-fable-5-1`. All of Haiku's runs come first, then Sonnet's, then Fable's; the runner
refuses a run of a later model while a run of an earlier one is left. Within a model, the tasks go in tasks.json's
order, each with arms A, B and C.

**Noise control.** Sonnet's arm A runs twice per bug (repeats 1 and 2), so the difference two runs of the same arm make
on their own is seen beside the differences between arms. That makes 30 Haiku runs, 40 Sonnet runs and 30 Fable runs:
100 in all.

**Caps and the stop.**
- **Round 3's own hard stop: $100,** agent and G6 together (G6 calls no model in round 3), kept in round 3's own
  archive and ledger, apart from rounds 1 and 2 (which spent $13.48 under their own stop). A run starts only if the
  money round 3 has spent, plus that run's own stop, stays within $100; a run started and not ended counts its whole
  stop. After each run, its cost and the round's total are written to the ledger (`ledger.jsonl` in round 3's
  archive).
- **Per run, Haiku and Sonnet:** round 1's caps: $10 (Claude Code's `--max-budget-usd`), the runner's stop past $10.50,
  200 turns, 120 minutes.
- **Per run, Fable:** the cap is the budget left divided by the Fable runs left (this one included), at most $10, so
  that its stop (the cap and 5%) fits; rounded down to the cent.
- **Cost:** counted as in section 6, and in round 3 also never below Claude Code's own reported `total_cost_usd`: a
  run's cost is the largest of the transcript's count, Claude Code's per-model usage at the published prices, and its
  reported total. No price for Fable 5.1 is stated in this repository, so its tokens are counted at the highest price
  on the pricing page (`UNKNOWN_PRICE`), which can only overstate.

**What stays as in round 2's runs (from run 6 of round 1):** the task's repository fetched from upstream and its four
checks, the container, its user and its security settings, the internal network whose one way out is the proxy to
`api.anthropic.com:443`, the key's path into Claude Code's environment only, the key check before every run (in every
arm, A included), the round hook, the archive, the oracle images and the independent check (section 5).

**G6.** Installed as the README says (section 9, "G6's own environment"), with no round-2 change: the proposer is off
and G6 has no model or key file. The commit is that of `claude/formal-round` built into round 3's images. It may hold
one change to G6, the generated-file copy (a copy for a check takes a git-ignored Python file only when the project's
committed build configuration names it and G6's ignored-file judge finds it cannot shadow or load anything: black's
`src/_black_version.py`), and only after an independent security review of it has passed; otherwise the commit before
that change. The commit used is named in a dated note here before the first paid run, and each run records it.

**What counts as a pass:** the independent check of section 5, unchanged: every FAIL_TO_PASS and PASS_TO_PASS test
passes on the final patch.

**What is reported** (RESULTS.md, under "Round 3: formal methods"): every run in the schedule, in order, including
failed and refused ones and runs not reached before the stop; per model and arm, the independent check on the final
patch and each round's patch, G6's outcome by round (B and C), in C the properties stated and the tools called (the
`g6_invoke` components and ops, the template instances), the findings judged as in section 5, cost and minutes; and
Sonnet's two arm A runs of each bug side by side. Differences are described in plain words, never as a rate.

**Runs made again.** A run whose agent started is never made again for a better result. A run that ended in `error`
(this plumbing) may be made again, with the reason recorded (`--rerun-why`); the failed attempt stays in the
archive, the ledger and the table, marked as such.

### 2026-10-30: round 3 runs on G6 894597f (before any paid run of round 3)

Round 3's runs use G6 at `894597f` on `claude/formal-round`, built into round 3's images. It holds the generated-file
copy and its review's fixes:
- **The first review (of `286c173`)** found it safe with fixes: a generated file's content was never checked, so the
  agent could write code into it and forge the base and the patch sides of a check; the file taken never reached the
  review's reply; and the names were read at HEAD, not at the review's base.
- **`894597f`** fixes all three, each with a test that failed first: a generated file is taken only if it parses as
  literal data (a version string, its tuple, the build tools' type stub), its bytes read once, checked, copied and
  hashed; `generated_copied` and `generated_left_out` reach the reply; and every copy made for a change reads the
  names at the change's base. The scan also keeps to the copy's deadline.
- **A second, fresh review (of `894597f`)** passed it with three minors, none of which can forge a pass, because both
  copies hold the same bytes: an aliased `from __future__ import ... as TYPE_CHECKING` lets typing-only code run (it
  can break imports, not call anything the agent wrote); a mutant run outside a review still reads the names at HEAD;
  and a taken file could shadow an installed package of the same name if the base's configuration names it. They are
  left for after round 3, and named here so that any import failure in a G6 check is read against them.

Nothing else in G6 changes for round 3. Arms B and C both run this commit; arm A does not have G6.

### 2026-10-30: Haiku's first three runs, and a smoke test before the rest (before any further run of round 3)

**What the three runs showed.** The owner stopped the batch after black-5634's three Haiku runs to check that the
round was testing what it says. The plumbing held: isolation, proxy, ledger, server connected with 20 tools, skill
present, independent check, traces. But **neither G6 arm called G6.** Claude Code defers MCP tools: the model sees their
names and must load one with `ToolSearch` before calling it. Arm B never searched. Arm C loaded `g6_review_patch` near
the end of its run and finished without calling it. Every round 1 Sonnet run looked at did load and call it. So these
three runs say nothing about G6 or its formal tools. They are kept, reported, and labelled a pilot; they cost $2.81.

**What changes.**
- **G6 use is counted.** Each run's record and ledger line give its G6 calls by tool and its `ToolSearch` calls. A
  run in arm B or C with no G6 calls is reported as such, and never described as a result about G6.
- **A smoke test first.** Sonnet's arms B and C on black-5634 run now, ahead of Haiku's remaining runs (the only
  exception to the order rule). They are the scheduled runs, with the same prompts and setup, and they count as such.
- **The gate.** The rest of round 3 starts only if both show real G6 calls, and C shows at least one formal tool or
  template. Otherwise nothing further runs until the owner decides.
- **Haiku.** Its remaining runs wait. They resume only with a change that gets it calling G6 (for instance the tool's
  exact name in the prompt, or the tools loaded up front), stated in its own dated note first, because arm B would then
  no longer be G6 exactly as shipped. If no such change works, the owner decides whether Haiku's rows stay unrun.

The $100 stop, the prompts and G6's commit (`894597f`) are unchanged. No run is made again for a better result.

### 2026-10-30: the smoke test's result, a stricter gate, and a three-bug probe (before any further run of round 3)

**The result.** Sonnet's arms B and C on black-5634 both called G6 (3 and 5 calls), both got `supported_within_scope`,
and both failed the same 2 maintainers' tests. Together they cost $0.53. In arm C neither `differential.v1` instance ran:
the first had the wrong bindings, and the second was refused because its function was a helper in a new test file, which
does not exist at the base commit. The base's own entry point needs a `Mode` object, which a JSON binding cannot pass.
No `formal` or `model_checking` op was called.

**The gate is stricter.** The earlier note asked for "at least one formal tool or template", which an attempt satisfied.
It now means one that **ran**: a template instance with a result other than `refused`, or a `formal` or `model_checking`
op that returned a result. An attempt does not count.

**The probe.** Sonnet's arm C runs next on attrs-1593, click-3678 and pydantic-14052, ahead of the order rule, with the
same prompt and setup. They are the scheduled runs and count as such. These bugs were chosen for simpler signatures
than black's. If checks ran on them, the rest of round 3 may go ahead, with black's limit reported as a limit. If they
were refused again, nothing further runs until the owner decides, and the next step is a change to G6 with its own
review.

**A finding kept for the report.** In both smoke runs G6 gave `supported_within_scope` on a patch the maintainers' tests
reject. It says what it did not check, but this is the false acceptance the round exists to look at.

### 2026-10-30: the probe's result, and arm D (before any run of arm D)

**The result.** Sonnet's arm C ran real checks on all three bugs: on attrs-1593 two templates, on click-3678 and
pydantic-14052 two `proposed_checks` each. All passed, and none found a defect. G6 gave `insufficient_evidence` on attrs
(its patch passes the maintainers' tests) and on pydantic (fails 1), and `supported_within_scope` on click (fails 10).
All three of G6's "supported" verdicts in round 3 so far are on patches the maintainers' tests reject. No `formal` or
`model_checking` op has been called. The stricter gate is met, but the rest of round 3 stays held at the owner's choice.

**Why arm D.** A read of the code found one escape hatch wired into a review, `proposed_checks`, which can build what
a template cannot; the agent has to think of it, and neither a refusal, the addendum nor the skill's steps pointed there.
G6 does not itself find from the source that a check needs another form: that is not built, and belongs after launch.

**What changes.**
- **In G6:** a template's refusal now names `proposed_checks` as the fallback. Only that text changes; no outcome does.
  The commit is named in a note here before arm D runs, after a fresh review, on images built from it. Arms A, B and C
  stay on `894597f`.
- **Arm D:** arm C's addendum, plus: use `proposed_checks` when a template is refused or cannot express the inputs, and
  make every check assert a specific expected value taken from the issue, so that it can fail. It names no bug.
- **Runs:** Sonnet's arm D on black-5634, attrs-1593, click-3678 and pydantic-14052. Arm D is outside the 100 scheduled
  runs and is reported as its own arm. Runs already made stay as they are. Same $100 stop; the proposer stays off.

### 2026-10-30: arm D's commit, after its review (before any run of arm D)

A fresh review of `f50953c` passed with minors: only refusal text changes in G6, nothing that is digested or decided
reads that text, and the prompts of arms A, B and C are byte-identical to before. Arm D runs on G6 `f50953c`, on images
built from it, and the runner refuses any other commit, because a run made on the wrong one could not be made again.

**Minors kept for later:** five earlier refusals of a whole instance (`literal_problem` among them) do not carry the
fallback text, and the fallback also follows `OUT_OF_TIME`, where a proposed check could not run either.

### 2026-10-30: DRAFT, what "supported" should mean (not approved; nothing here is built or run)

**Status.** A proposal for the owner. It changes no code until the owner approves it, and it needs the three decisions at
the end, because it overrides a binding rule.

**The problem.** G6 gave `supported_within_scope` on 7 of Sonnet's 9 runs, and the maintainers' tests reject 6 of those 7
patches. The verdict says every requirement passed its stated checks. In these runs the agent wrote those checks along
with the patch, so a misreading of the bug goes into both, and the checks pass. G6 does not record who wrote a check:
"stated" means only that the caller named it. A reader takes the verdict as an outside opinion, and here it is not one.

**The proposed rule.**
- A stated check is **self-checked** when its file is added or changed by the patch under review.
- A requirement's status does not change: it still reads `passed_on_stated_checks`, naming its checks.
- If any assessed requirement passes on self-checked checks alone, a new evidence reason applies,
  `checks_written_with_patch`. The outcome is then at most `insufficient_evidence`, under the existing precedence. It
  is not a defect, and the reason says so: nothing shows the patch is wrong, and nothing independent of it shows it right.
- The next action names the requirement, and says what would settle it: a check that already exists at the base commit
  outside the patch. A person confirming the behaviour would be the second way, but `g6_confirm_behaviour` (VS-D5) is
  not built, so this draft does not depend on it.
- **Also fixed:** the gate's "nothing else was found" is not said when a proposed check could not be judged. The cause of
  black D's two errors is traced first.

**What it does to the runs so far (worked out offline from the archive, no model, no spend).** In all nine of Sonnet's
runs, the main requirement's checks sit only in files the patch touched. So all 7 "supported" verdicts become
`insufficient_evidence`: the 6 false acceptances go, and so does the 1 correct one (attrs D).

**What this does not do.** The rule makes the verdict honest. It does not make G6 better at finding bad patches: on
these runs it gives the same answer for every patch, right or wrong, so it separates nothing. Any report of it must say
that fewer false acceptances here came from fewer acceptances. Telling good from bad needs a source of checks independent
of the author. The candidates (VS-5's proposer in a separate session, or a person's confirmation) are out of scope here
and would need their own note.

**Limits.**
- File-level is coarse. A new test added to an existing test file makes that whole file self-checked. Comparing single
  tests is left for later, if this proves too blunt.
- It can be gone around. An agent could commit its tests first and name that commit as the base. G6 records who says
  what and does not authenticate it; this is the same-user limit G6 already states.
- It catches the common case only. An untouched test can be weak, and a self-written one can be right.

**Alternatives rejected.**
- *A fifth outcome.* It breaks the four outcomes that replies, docs and the V9 and V10 protocols are written against.
- *Wording only, keeping the verdict.* `supported_within_scope` would still count as accepted in every measure.

**What it costs.**
- Agents working on new behaviour will mostly get `insufficient_evidence`. The skill must tell them to report that
  plainly to the person, and not to keep rewriting tests to chase a verdict.
- Rule 1 of the verification-search plan ("off changes nothing") and A5's meaning of the outcome both change, so the
  off-path fixture (`tests/harness/data/patch_review_off_path.json`) has to be re-based deliberately. That is a change of
  meaning, and is recorded as one, never as a test fix.
- The term appears in 23 files outside the worked examples, 32 times in `tests/harness/test_patch_review.py` alone. The
  `correct` case in `validation/patch_review/run.py` ships its test in the patch, and would change.
- V9's arm 3 and V10's arms accept only on `supported_within_scope`. Before either freezes, it must be checked that their
  review-contract tests sit outside the patch, or those arms could never accept. Neither study has run, so this can
  still be amended first.
- `patch_review` shipped in 0.2.0, so this reaches users only in a later release, which is the owner's step.

**How it would be tested, in order.**
1. Test first, with no model: a check in a patch-added file, one in a patch-changed file, one outside the patch, and a
   requirement with both kinds. A fresh review of the commit follows.
2. No spend: replay G6 on the nine stored patches. Expected: all nine `insufficient_evidence`. Any other result stops
   the work.
3. Only after that, about $1: Sonnet on the same four bugs as a new arm E, outside the 100 scheduled runs, under the same
   $100 stop. The question is behaviour, not score: does the agent tell the person plainly, or chase the verdict?

**Decisions for the owner.**
1. May this override rule 1 and A5, so a decided field changes for everyone, search on or off?
2. Is the rule on by default, as drafted, or an option a caller turns on?
3. Is step 3's roughly $1 approved, once steps 1 and 2 pass?

The full run and Haiku's remaining runs stay held either way.

### 2026-10-30: approved, and the distillation loop (before any run it covers)

**The owner approved the draft above in full:** the rule overrides rule 1 and A5, it is on by default, and arm E is
approved. It is commit `b52da4d`. The owner then set a new goal and budget: G6 should solve these tasks more often than
Claude Code alone, at 95% or better, through G6 distilling its own repair instructions, with the round's stop raised from
$100 to **$180**, counting what was spent. This supersedes the hold on further runs for the runs named here.

**The replay step is replaced.** The worked-out result above (all nine fall to `insufficient_evidence`) stands, and arm E
tests the rule live on all ten bugs, so the separate replay is not run. black D's two checks failed at collection on both
trees; G6's records keep no pytest output, so why is still unknown.

**The runs, all Sonnet, each twice, on G6 `b52da4d`:**
1. **Arm A**, no G6, on all ten bugs: the comparison this round has not had. These are the scheduled runs.
2. **Arm E**, arm D's prompt under the new rule, on all ten bugs.
3. **Generations.** `G<n>` is one text from G6's `FailureTraceStrategy`, reading the failed runs of the best text so far,
   on the seven training bugs only. A generation replaces its parent only if it passes more of the maintainers' tests.
4. **The held-out look.** The best text, as `H<n>`, runs once on the held-out bugs: `black-5521`, `click-3769` and
   `starlette-3718`. They were chosen by hash before any tuning. Nothing about them reaches the distiller, and they do
   not change the text.

**What the distiller may read:** a failed training run's final report, its patch, and the names of the maintainers' tests
it failed. **What the agent may read:** the text only, never those tests. A text naming a project, an issue number, a test
or a file from the runs is refused. Each accepted text is committed before it runs.

**The stops.** The training score reaches 95%; or 3 generations in a row do not improve; or 8 generations; or $170 spent.
Round 3's hard stop stays below that at $180. The rest of the schedule stays held, Haiku included.

**What a result will and will not show.** These are development bugs, and most of them were seen before this loop. A
training score can overfit, even with bugs unnamed, so it is always reported beside the held-out score and arm A. Three
held-out bugs at two runs each is a small sample. The maintainers' tests can also ask for details an issue does not state,
so 95% may not be reachable by any honest means; if not, that is reported. The formula "G6 fixes its own repair loop
through harness distillation" is used only if the held-out score also beats arm A, and always with these numbers. G6's
own gate needs 30 cases, so it cannot promote this text on 14 runs, and it is not reported as having done so.

### 2026-10-30: the review's fixes, the baseline, and the next change (before arm E or any generation)

**The fresh review of `b52da4d` said fix first.** Its main finding: one unrelated old test that passes lifted the cap, and
the next action told the agent to cite one. Fixed in `c53d310`, which replaces `b52da4d` as the loop's G6:
- A check is independent only if it exists at the base commit in an unchanged file. For new behaviour it must also not pass
  at the base commit. This changes the earlier ruling on review I8 ("an unknown, never another outcome") for such
  requirements. A requirement can keep behaviour only through an intent, because callers cannot set `kind`, so a review
  of a refactor with plain requirements ends at `insufficient_evidence`.
- A self-written check in a git-ignored file no longer escapes, and a folder check on a source-only change is no longer
  capped. The loop's crash, budget and audit findings are fixed too. These fixes were tested first and rerun under
  bubblewrap (329 pass); there was no second fresh review of them.

**Arm A, the baseline (Sonnet, no G6, twice on each bug): 11 of 20, $1.18.** On the training bugs 9 of 14 (64%); on the
held-out bugs 2 of 6. It passes attrs-1593, fastapi-15077, pydantic-14065, starlette-3718 and starlette-3720 both times,
pydantic-14052 once, and black-5521, black-5634, click-3678 and click-3769 never. So 95% on training needs both
black-5634 and click-3678, which arm A has not passed.

**The owner's next request:** use G6's formal methods to construct a better self-correction loop. This is not designed
yet. It needs its own dated section before any run it covers.

### 2026-10-30: a self-correction loop built with G6's formal methods (before arm E or any generation)

**What the failed baseline runs on training bugs showed.** On `black-5634` and `pydantic-14052` the agent fixed the one
shape of input in the issue; the maintainers' tests try others (nesting, line endings, trailing comments, other scopes).
On `click-3678` the tests require a literal internal name and exact warning text that neither the issue nor the
repository states. So `click-3678` looks out of reach for an honest agent, which would cap training at 12 of 14 (86%).

**G6's model checker on the stop rule** (`loop_model.py`, about the model only, never about an agent or a patch). The
rule "stop once nothing tried is failing" can stop with every shape untried. "Every listed shape tried, nothing open,
every result taken after the last repair" gives `k_inductive` (k=1) and can still finish. Dropping the rerun gives a
violation again, so the rerun stays.

**G1 is that rule written by hand from the model, not distilled.** Later generations are distilled from it. A headline
about distillation is allowed only if a distilled generation, G2 or later, beats G1 and arm A on training and arm A on
the held-out bugs. Otherwise the report says the gain came from the hand-written rule.

**The owner asked why G6's CEGIS component was not used.** It now runs the outer loop: `loop.py` calls `g6.cegis.cegis`
unchanged, with harness text as the candidate, training bugs and repeats as the domain, the distiller as its search and
each run's independent check as the oracle's answer. Its Z3 verifier is not used: it covers small integer functions.
The `cegis` status is reported as it falls; a clean `no_counterexample_in_domain` means only these bugs and repeats.

**The final comparison changes before any generation:** arm A and the best generation both run five times on each
held-out bug, not twice, because six runs say little. The held-out single look stands.

**An audit of what each task states, before any further run.** A reviewer who has not seen G6's checks reads each of the
ten issues and the maintainers' test changes. A bug is *not determined by its task* if a test it must pass requires a
name, message or format that neither the issue nor the repository at the parent commit states. For held-out bugs the
reviewer returns the label only. No bug, test or run is dropped: the score on all ten is the score, and the score on
determined bugs is reported beside it, labelled, with arm A's on the same bugs. The owner's 95% stays the target on all ten.

**The audit's result, recorded before any paid run in this section.** Nine bugs are determined by their task, including
all three held-out bugs. `click-3678` is not: its tests require the storage name `_click_default_help` and warning texts
stated in neither the issue nor the repository. On all ten, 18 of 20 runs in a two-repeat round is the most an honest
agent can reach if `click-3678` fails both times, which is 90%, so the 95% target may be out of reach on these bugs.

### 2026-10-31: why G6 changed no fix, and arm L, where the loop runs the agent (before any L run)

**What arm E's training runs showed.** In all 14, no source file was edited after the first G6 review, so G6 changed no
fix and cost about 3.6 times arm A's runs. Every check came from the patch's author; G6's own proposer has no model in
this setup. Of 21 reviews, 18 said `insufficient_evidence`, so that word carried no signal. In the failed black and
pydantic runs the agents usually named the risky shape in their report, left it untested and stopped. `click-3678` is
not determined by its task, as the audit found. Two runs per bug are few; these counts describe these runs only.

**The owner's reading:** the primitives exist but are not assembled around how raw Claude Code fails, and the agent does
not use them well. Text asks an agent to behave; it cannot make it. So arm L moves control out of the agent:

1. **A scout, before any patch.** A separate Claude Code session in the same container reads the issue and the repository
   and writes a goal tree of required behaviours and input shapes, with a runnable probe for each. Its prompt follows the
   stages of open PR #4 (constraints, heuristics, decomposition, adversarial critique); the tree is PR #5's `GoalTree`,
   taken unchanged. It never sees a patch, so its reading is not the patch author's. It never sees the maintainers' tests,
   which are not in the container. Its changes to the repository are discarded; its probes become read-only.
2. **The agent fixes the bug,** with G6's server and skill present, as in arm E.
3. **A Stop hook, not a request.** When the agent tries to finish, the runner's hook runs the probes on the current tree.
   A failing probe blocks the stop and comes back as a counterexample. The agent repairs it, or waives that probe with a
   reason recorded in the run. The hook lets go after four blocks, so a wrong probe cannot hold a run for ever.

Probes are a model's reading, so they are obligations for the agent and evidence of nothing: only the maintainers' tests
score a run. `L<n>` runs on training bugs and `M<n>` is the same setup on held-out bugs. The scout's cost counts in the run.

**This is a prototype in the runner; it changes no G6 source a run uses.** G6's commit stays c53d310. If it earns its
place, it moves into G6 behind a fresh review. Claiming it for G6 before then would be wrong: until then the report says
"the runner's loop". Held-out rules, the $180 stop and the rule against rerunning for a better result all stand. If it
beats arm E on training, `M1` and arm A get the five-repeat comparison. Fable on the same setup would then test whether
the limit is the model or the structure, on the owner's approval of that run's cost.

**Added the same day, before any run that uses it: the issue's discussion, and arm T.** Every run so far saw the issue's
body only; a person also sees its comments. Arms L and M now get the comments made before the fix's pull request opened,
without bots' comments. Arm T is plain Claude Code with that same text, so a gain from reading the thread is not credited
to the loop. Arm T may run five times on held-out bugs, as arm A does.

**`click-3678` stays out of honest reach, and is kept.** Asked to make it reachable, I read its issue, all six comments,
the linked items and the repository at the parent. The storage name and the warning phrases its tests need appear in
none of them; the pull request that introduces them opened with the fix. No loop can derive an arbitrary name, and any
channel that delivered it would give an agent the maintainers' tests. So nothing here does. The score on all ten stays
the score; the most it can show with `click-3678` failing is 90% on a two-repeat round.

**L1's one smoke run, and L2 (before any L2 run).** On `black-5634` repeat 1 the scout cost $0.16 and wrote 15 shapes and
12 probes, 6 of them failing on the base. The Stop hook fired, the probes passed first time, and so it never blocked. The
run still failed, on one maintainers' test where arms E and G1 failed two. The scout probed each dimension alone, and
left three shapes "unclear", which the agent then reported as questions. L2's scout combines dimensions in pairs and may
not leave a shape unclear. L2 runs on all 14 training runs. It is a text change to the scout, naming no bug, chosen after
seeing that run and that bug's tests, so it is development; only the held-out comparison can support a claim.

**L2 on `black-5634`, and L3 (before any L3 run).** Both L2 runs failed one maintainers' test. In repeat 1 the probes could
not import the package, pytest exited 2, and the hook let the agent go: a broken instrument had switched the loop off. In
repeat 2 all 22 probes passed, yet the failing cases sit where two dimensions the scout had listed cross, and it had not
written them although its prompt asked. So L3 makes the count mechanical. The scout declares dimensions as data and writes
one test over their product; G6's `InputDomain` (`g6.cegis`) gives the number of combinations, up to 200. The runner refuses
probes that cannot run, reproduce nothing or miss combinations, and sends the scout back once. A collection error on the
patch now blocks the stop. This was chosen after reading that bug's failing cases, so it is development.

**L3 on `black-5634`, and L4 (before any L4 run).** L3 passed repeat 1, the first pass of that bug by any arm, and failed
repeat 2. The difference is which dimensions each scout chose: repeat 2's never crossed scope with trailing comments. L3's
count was also broken (two `-q` flags hide the test names), so both scouts were refused once falsely and the runner's
count of combinations did not hold; that run stands as run. L4 fixes the count and adds PR #4's adversarial critique as a
separate session: a fresh reader attacks the tree before any patch and extends it.

**Two instrument faults stopped distillation.** G2, G3 and G4 were never run. The audit's drive-letter pattern matched
ordinary words before an escaped newline, and the distiller kept the model's escaped line breaks. So `cegis.json`'s
`inconclusive` says nothing about distilled text. Both are fixed. G1 is what went to the held-out comparison, as the plan
says, and its H1 runs stand. The loop's arms may be compared on held-out bugs after it: that is a second look, it will be
reported as one, and no held-out run or test is read to tune anything.

**What replaying every archived probe set showed, and L5 (before any L5 run).** `probe_replay.py` ran each scout's probes
on every archived patch of the training bugs and on the maintainers' fix. No model was called and nothing reached an agent.

- `L2` scored 10 of 14, level with G1 and with no pass on `black-5634` or `click-3678`. The Stop hook gave no genuine
  counterexample in any L run: probes used the system Python, not the project's `.venv`, so they failed to collect on both
  pydantic bugs, on one black run, and falsely blocked attrs. So the L runs so far do not measure what they were built to.
- On `black-5634`, `L3-1`'s 249 cases pass on the fix, pass on the one accepted patch and fail all 12 rejected patches. The
  other sets catch 6 of 12. A probe the agent can see is one it fits to, so it says little at the end.
- Three sets fail on the maintainers' fix: 37, 31 and 255 cases. Almost all are one shape, which L1's scout had called
  unclear. L2's rule "never leave a shape unclear" forced an invented expectation. That rule is withdrawn. The 255 are one
  test that crashed; the agent said so and waived it, correctly.

**L5, a CEGIS loop the runner holds.** `g6.cegis.cegis` runs it: `search` is the agent, `evaluate` the probes.
1. Two scouts, each a fresh session, neither seeing the other's work. One's probes are visible; the other's stay in a
   root-only folder, and its session transcript is removed from the container, so no agent can read them.
2. Each must pass the runner's gate: the probes run, the product is complete, failures are assertions rather than crashes.
   The scouts assert exact values and write no probe where the issue and repository are silent.
3. The agent works with G1's text and the visible tree, without Stop hook or waivers. When it finishes, the runner runs
   both sets and sends failures back in the same session. It stops when all pass, when nothing new fails, or after three
   repairs. A check is one reader's view, so the agent still decides; only the maintainers' tests score the run.

**How the claim will be judged.** All of L1 to L5 was chosen on training bugs, so training scores flatter it. In particular
the scout's list of kinds of shape and G1's came after reading what training bugs' tests try; they name no bug, but they
are not independent of those bugs. Arm T gives the comparison with the discussion alone. Only held-out bugs and fresh bugs
can support a claim, and any fresh set is drawn by `selection.py`'s rule with this text frozen.

**L5 on `black-5634`, and L6 (before any L6 run).** The outer loop worked: both gates passed and the checks ran. But both
runs' probes were clean on patches the maintainers' tests reject, because the rule on silence left out the very shape that
fails. Every failing patch on this bug, in all arms, fails the same 72 of `L3-1`'s cases, all where an indented scope
meets a trailing comment, so several attempts would share the failure. Only `L3-1` listed that crossing and asserted exact
output. L6 keeps exact assertions and stops relying on a scout's imagination: dimensions come from reading the code path as
well as the issue; a seeded `test_random` composes inputs from all the building blocks, and the runner counts at least 300;
there are two hidden scouts; and the principle applies to shapes the code treats alike, unless the repository shows they
are meant to differ. The survey cited in the plan's verification-search section found biased random inputs reach
distinguishing inputs sooner than a solver does.

**This is the last change chosen from `black-5634`.** Three reviews of this work agree the loop has been tuned on one bug.
After L6 the text is frozen and judged on bugs it has not seen.

**G1's held-out result (the first look).** G1 passed 3 of 15 on held-out bugs and arm A 5 of 15, against 10 of 14 and 9 of 14
on training. So a stop rule in the prompt did not generalise, and advice alone is not the way forward. The loop arms are
compared on held-out bugs later, as a second look, reported as one.

### 2026-10-31: the loop frozen at L7, and how it is judged (before any L7, M7 or fresh run)

**L6 and the fault behind L7.** L6 passed `black-5634` twice and `pydantic-14052` twice. But pydantic's test plugin replaces
pytest's summary, so the outer loop read 10 failing hidden cases as none, and the earlier replay misread pydantic's probes
as passing on the base. So a run's failure is now decided by pytest's exit code, and an unreadable run is not a pass. That
is L7, with the plugin switched off. L7's text is L6's. This commit freezes it: no change to the scouts, the gate, the outer
loop or the prompt before these comparisons end.

**Fresh bugs.** `tasks_fresh.json` applies section 2's rule to the window 2025-12-01 to 2026-05-31, before the ten's: the
two newest bugs of each kept project, judged on the issue's text only, before any run. `pydantic-13146` failed validation
(no test goes from failing to passing), so the next qualifying bug took its place. Eleven bugs, all validated. Nobody has
read their fixes or tests to tune anything, and no run on them will be read for that purpose. A blind audit labels each as
determined by its task or not, labels only, by the rule used for the ten.

**The comparison, fixed now.** Arms A (Claude Code alone), T (the same, with the issue's discussion) and M7 run twice on
each fresh bug, and T and M7 five times on each held-out bug, a second look at those bugs. Every run is reported.
- The loop may be said to beat Claude Code alone on fresh bugs only if M7 passes more runs than both A and T, and an exact
  one-sided sign test on the pairs of runs that differ (M7 against each, same bug and repeat) gives p < 0.05.
- Otherwise the report says it was not shown. The training score is never the headline, and the held-out score stands
  beside every tuned score.
- The owner's 95% stays the target on the ten, with `click-3678` in the score.
- What ran is the runner's loop built on G6's `cegis` and `InputDomain` and the model-checked stop rule; it is not yet in G6.

**The fresh bugs' audit, recorded before any of their runs.** A blind reviewer gave labels only. Determined by the task:
`pydantic-13243`, `black-5148`, `click-3434`, `click-3404`, `starlette-3189`, `fastapi-14616`, `fastapi-13920`. Not determined:
`black-5144`, `attrs-1509`, `starlette-3166`, `pydantic-13133`. No bug is dropped: the score on all eleven is the score, and the
score on the seven goes beside it, with the same rule applied to each comparison.

### 2026-10-31: the owner's scoring change, tests a task-determined fix can pass (before any amended test exists, and before any further run)

**What the owner asked.** "We need to modify the tests so that a 100% result is achievable." On five of the 21 bugs the audits
found that the maintainers' tests need a name, message or format the task does not state: `click-3678`, `black-5144`,
`attrs-1509`, `starlette-3166` and `pydantic-13133`. No honest run can be expected to pass those, so 100% was out of reach by
construction. The owner's measure is **harness adaptation uplift**: what adapting the harness adds over plain Claude Code.

**The rule.** The set of bugs comes from the audits' labels, so it was fixed before this change and no outcome chooses it.
1. Only those five bugs change. The other 16 keep the maintainers' tests exactly, including every bug that every arm fails.
2. A fresh reviewer who sees no run, patch or score writes one diff per bug against the maintainers' test files. The diff is
   the smallest that removes what the task does not state: a pinned name, message or format becomes the weakest check the
   task does state, and a behaviour the task states stays checked. A bug's diff changes no test because a run failed it.
3. **Controls, no model:** on the amended tests at least one test still fails on the parent and passes with the maintainers'
   fix, and every maintainers' test that no longer passes on the fix is listed. A diff that fails the first is refused.
4. **Every arm alike.** The saved patches of every arm are rescored without a model and new runs get both verdicts. The
   amended tests stay out of every agent's reach, as the maintainers' do. On a fresh or held-out bug I read neither the diff
   nor the tests behind it; the reviewer reports counts only.
5. **Both scores, always.** Every report gives "maintainers' tests" and "amended tests" side by side. An amended score is
   never called the maintainers' tests, and the comparison rule above is applied to each separately.
6. L7 stays frozen. This changes how runs are scored, not what any agent sees.

**Uplift, as it will be reported.** M7's pass rate minus arm A's and minus arm T's, same model and budget, on bugs the loop was
not tuned on (the fresh bugs, and the held-out bugs as a second look), on both scores, beside the tuned L7 score. A tuned score
alone is not uplift: G1 gained one run on training (10 of 14 to 9 of 14) and lost two on held-out bugs (3 of 15 to 5 of 15).

**What this supersedes.** "The owner's 95% stays the target on the ten, with `click-3678` in the score" now reads on both
scores: `click-3678` stays in, on its amended tests. The section above saying `click-3678` is out of honest reach still holds
for the maintainers' tests. The owner's 95% is read on the amended tests, and the maintainers' score stands beside it.

**A review from another model family, asked for by the owner.** Codex reviewed the scoring change read-only and showed four faults
in it, each with a reproduction; none had affected a saved result, and no agent sees any of this. All four are fixed, test first.
A newer refused control could be passed over for an older good one, so a control now counts only if it is the newest to end and ran
the diff's exact bytes. A finished run with no independent verdict could be saved as a failure, which could manufacture a significant
win, so such a run is now not saved and a restart checks it again, a check with no test report raises, and `compare.py` reads such a
row as missing (of 108 saved rows, none is). A cached amended verdict held for any diff, so it now holds for one run and one diff.
An unreadable parent report passed the control, so a failure must now be seen on the parent. Not adopted: checking the oracle image's
identity in each amended check, since nothing rebuilds those images.

### 2026-10-31: L7 retired unrun, and arm L8, with a critic from another model family (before any L7, L8 or M run)

**Why L7 is not run.** The owner asked for a second opinion from another model family. Codex read the code and the raw training
runs, forming its view before reading this plan, and answered that L7 was not worth about $45 as frozen. I checked each claim
against the code; all held. The gate only advised: a set still failing it went on to the fixer (the words "must pass" above overstate
it). pytest exit 5 and all-skipped runs counted as clean. Cases were counted when collected, not when run. Failure ids were cut at a
space, so two cases could be one. A failure the agent had seen once was never sent again, even after a repair changed it. Scout
spend sat on top of the fixer's cap. No L7 or M7 run exists and no fresh bug has been spent on the loop, only arm A's two runs on
`pydantic-13243`, which stand, so the freeze above is lifted at no cost. Codex also showed that both of L6's `black-5634` final patches
are byte for byte the first round's: the repair loop changed nothing there, so those passes came from the visible probes.

**L8's instrument (commit 64c490a).** A probe run is read from pytest's own junit report. A set that exercised nothing on the base is
quarantined, reported, and sends no counterexample. Cases count when they ran. A failure goes back while the agent is still changing the
code, and the loop ends when it leaves the code as it was. G6's `classify` (plan rule 3) separates a case that passed before the
change, a regression, from what one reader says the fix still owes. On six real archived probe sets, in the task images, this keeps ids
with spaces whole and sees pydantic's failures, which the old reader could not.

**A free experiment on a critic that reads the patch.** Every scout so far was barred from seeing a patch. The top cause in both
reviews is a fix that handles the issue's example and misses the rule, and that is visible only in a patch. So critics read the issue,
the parent and a candidate patch, blind to its outcome and without running code, and wrote tests. Six archived training patches, three
that fail and three that pass, one patch per bug in each batch. Each test ran on the patch and on the maintainers' fix, which only this
measurement may see: failing on the patch and passing on the fix confirms a finding, and failing on the fix makes the test wrong.

| Patch (its verdict) | Codex | Claude Sonnet, the fixer's model |
|---|---|---|
| `black-5634` A-1 (fails) | confirmed | said none |
| `pydantic-14052` A-1 (fails) | confirmed | said none |
| `click-3678` L2-1 (fails, amended tests) | confirmed | confirmed, one more test wrong |
| `pydantic-14052` G1-1 (passes) | said none | said none |
| `black-5634` L6-1 (passes) | wrong: its test also fails on the fix | said none |
| `click-3678` A-1 (passes, amended tests) | confirmed, a defect the tests miss | confirmed, the same |

Codex caught 3 of 3 failing patches and Claude 1 of 3. The two it alone caught are the failure that has held every arm back. Six patches
on three bugs are few, and this compares one model with another, so it cannot separate lineage from model strength or effort. It is a
reason to try the critic, and proves nothing about unseen bugs.

**L8's critic.** When the fixer first finishes, Codex reviews the patch once, read-only, on the host, with the task, the parent and the
patch, as above. It never sees a maintainers' test or the fix. Its test runs on the patched tree in the container; if it fails, the
agent gets the critic's rule, reasons and test as a counterexample, can reject it with a reason, and the loop runs as above. A critic
can be wrong, as Codex was once here, so nothing it says scores a run. Only the maintainers' tests and the amended tests do. L8's other
text is L7's. Codex is billed to the owner's sign-in, not the $180, so each run records the critic's seconds and verdict, and that cost
is reported apart. A run whose critic fails stays in the score as it fell, with that recorded.

**How it is judged.** The comparison rule above stands with L8 and M8 in place of L7 and M7, on both scores, through `compare.py`. L8 first
runs on the training bugs, as development: a fault it shows may be fixed, and then L8 freezes before any M8 run. Critic replies on fresh and
held-out bugs are archived and unread, like their runs. Codex's own estimate of the reachable rate on unseen bugs is 60 to 80%, low
confidence, against the owner's 95%. It also notes, fairly, that moving 95% onto amended tests answers a different question: hence both
scores, always.

**The five amendments, as they ended.** All are in `amendments/`, one diff and one note per bug. A blind audit from another model family,
with the task, the parent, the maintainers' tests and each diff but no fix and no run, kept `click-3678`, `black-5144` and `starlette-3166`,
and sent back `attrs-1509` and `pydantic-13133`; the reviewer accepted every finding. On `click-3678` the amendment goes past a name or
message: a fix that drops the automatic help option when a parameter takes its name now passes, because the parent's documentation says
so. The audit upheld that. `attrs-1509` took three passes. On the last, reviewer and auditor seemed split on one assertion, and I ruled the
maintainers' assertion should stand; the reviewer showed that this put back the unstated item and that an honest fix would fail, and that it
now agreed with the audit, so I withdrew the ruling and took the audit's replacement. Controls hold both ways: the fix passes, the same
fix with the unstated item changed passes, and a fix with the stated behaviour wrong fails. No audit has read the last form of that one
hunk, which differs from the audited replacement only in line wrapping. On `click-3678`'s saved runs the amendment moved arm A from 0 of 2
to 2 of 2, G1 from 0 of 2 to 2 of 2, and E and L2 from 0 of 2 to 1 of 2 each: it lifts plain Claude Code as much as any harness.

**L8's one smoke run, and L9 (before any L9 or M run).** `L8` on `click-3678` repeat 1 passed the amended tests and failed the maintainers',
for $0.80, and the instrument did its work: three real repair rounds where L7 would have allowed one. But the critic reviewed nothing. Python
makes `mkdtemp` folders private on Windows, Codex's sandbox was refused the pack, and it answered `VERDICT: none`, which reads as a clean
review. So the pack now sits in an ordinary folder, and the critic must quote a random line from inside the pack; a reply that does not is
marked `unread`, never `none`. That run stays in the record as L8 and is not counted as a critic's result. The generation that runs is L9,
identical but for that fix, and `compare.py` reads L9 and M9. A real Codex call through the fixed path then read the pack, passed the proof and
found on an archived `click-3678` patch what the blind experiment had found, in 52 seconds. Everything said above of L8 and M8 from here means L9 and M9.

**L9's first two runs, and a leak.** On `click-3678` both failed the maintainers' tests and passed the amended ones, as arm A's did, so they show no uplift
on this bug. They do show the mechanism for the first time: each time Codex found a defect in about a minute, its test failed on the patch, the agent
repaired, and the test passed. Claude Code's memory reaper then stopped the stream. Part of the cause was the runner's: each Codex call left a broker and
a codex process behind, about 0.4 GB a time, and removing nine of them took free memory from 4.1 to 8.1 GB. `run_codex` now ends them after every call. No
agent sees any change, so this stays L9. A run in flight when the stream stopped has no row and is made again, and its remains were removed.

### 2026-10-31 (evening): the owner's new goal, in three stages (before any M9 run, and before stage 2's bugs are drawn)

**The goal, as the owner set it, replacing the 95% goal:** "Measure whether a harness improved on training bugs beats Claude Code
alone on bugs it never saw, reporting three stages separately: (1) unseen bugs in the training repositories; (2) unseen bugs in
repositories never used in training; (3) a harness change G6 proposes itself from its own failure records, with no change written
in session. Primary measure: uplift over Claude Code alone under the frozen comparison rule. 95% is a stretch target, reported
beside it. Budget $180 in total. Agents never see the maintainers' tests. Report tuned and held-out scores together, and
maintainers' and amended scores together."

**Why it changed, and what did not.** Nothing about L9 on an unseen bug is known, so no result chose this. The only look at unseen
bugs so far, H1 on the held-out bugs, went the wrong way (3 of 15 against arm A's 5 of 15), and it stands. The comparison rule, the
amended tests, the $180 stop, and every rule keeping maintainers' tests from agents are unchanged. The goal is to measure, so "not
shown" is a valid ending for any stage.

**Stage 1** is the comparison already fixed: A, T and M9 on the eleven fresh bugs, and T and M9 on the held-out bugs as a second
look. All of them come from the six repositories used in training, so a win says "unseen bugs in familiar repositories" and no more.

**Stage 2, repositories never used in training.** The bugs are drawn by rule, so nobody picks them:
1. **Repositories:** PyPI's top 100 in rank order, each resolved to its GitHub repository from PyPI's own record, leaving out the six
   training repositories. Rules P2 and P4 and tasks' rules T1 to T4 are applied as in section 2, with one change: the window is
   2025-12-01 to 2026-10-29, the two earlier windows together.
2. **Bugs:** the two newest of each repository judged to report a bug, on the issue's text alone, taken in rank order until ten bugs
   pass validation. That judgement is made only after L9 is frozen, so no stage 2 issue can influence the loop. A bug that fails the
   free validation steps gives way to the next one, and the record says so.
3. **Arms:** A, T and M9, twice each, the same frozen L9 as stage 1. The comparison rule is applied on its own to these bugs. The
   audit and the amendments follow the 2026-10-31 rule: labels from a blind reviewer, a diff only for a bug not determined by its
   task. I read neither their fixes nor their tests, and their runs are not read to tune anything.

**How each comparison is read.** To clear the rule M9 needs at least 5 wins and no losses, or 7 and 1, on the runs that differ, against
both A and T. That is hard to clear even if a gain is real, and the rule is not loosened. Two repeats of one bug are not independent,
so a p-value overstates the evidence; beside it, each report counts the bugs on which M9 passed more often than A and T, and fewer.

**95%** is reported beside the uplift on the amended tests, with the maintainers' score next to it. It is no longer what a stage is
judged by.

**Stage 3** gets its own dated section before its first run.

### 2026-10-31 (night): stage 3, harness training by G6 (before any stage 3 run, and before stage 2's bugs are judged)

**Why it needs new bugs.** L9 was tuned by hand on the seven training bugs, and on the runs saved so far it has no failed run on the
amended tests. G6 can therefore learn nothing more from those bugs' verdicts, and no change could show a gain there. Two things
follow, and both are now in the runner. A failure record can be a **near miss**: a first patch the runner's own probes or critic
caught, which needs no maintainers' test. And stage 3 gets a **development pool** of its own.

**The development pool.** Stage 2's rule goes on in rank order after its ten bugs are taken, and the next repositories give five more
bugs, two at most from each, judged the same way. They share no repository with stage 2's bugs, so stage 2's bugs stay in repositories
never used in training or in development. No development bug is ever judged on, and `evolve.records` refuses every bug that is.

**What runs, in order.**
1. L9, the parent, frozen and unchanged, twice on each development bug. Its failed runs and near misses there, with those on the
   training bugs, are the failure records.
2. Up to four candidates, one for each operator from PR #4 (`regression`, `boundary`, `counterexample`, `metamorphic`), written by G6's
   `FailureTraceStrategy`. A model picks which part of the harness each adds to (fixer, scout, repair or critic), with a fixed fallback
   for each operator, and the record says who chose.
3. A candidate can only add at most 120 words to a part's instructions. The sets, the scoring, the reply forms and the stop rule cannot
   be changed, and any other field is refused. It is refused if it names a project, an issue, a test or a file, or if it is a near copy
   of an earlier one. Each text is committed before it runs, and nobody edits it.
4. **A cheap screen:** one run on each development bug the parent failed. A candidate that solves none of them stops there.
5. **The paired check:** twice on every development bug, against the parent, using PR #4's `assess`. A bug counts as solved if any of its
   runs passed, so one slipped run is noise and losing a bug outright is a veto, and the candidate must also pass more runs overall.
   Being cheaper is not a gain. Training stops at the first candidate recommended, because each paired check costs real money.
6. **The test:** the recommended text as `M<n>`, twice on each stage 2 bug, judged under the frozen rule against M9, A and T, on both
   scores, with the bug counts beside it. Beating M9 is what would show a gain from training itself. Beating only A and T would not.

If no candidate is recommended, that is the result, and no stage 3 test is run.

**What a result can be called.** The candidates' text is G6's, and nothing in session edits it. The machinery around them was written in
session, and so was the parent. The report says "G6 proposed this change from its own failure records" and never that G6 engineered
itself. The operators were chosen by a person and the proposer is a model, so a gain here is a gain for this composition on these bugs.

**The proposer on real records (no run, nothing committed, texts discarded).** Three real calls on L9's near misses gave two clear texts
and one empty reply, and a repeat of that operator gave a clear text. Its cause is unknown, because the reply was not kept then. A reply
with no text is now kept and labelled. All three chose the scouts as the part to change.

**The agentic kernel.** `g6.agentic` was designed (800a2e8) and never built. What runs here is `agentic.choice`, a model decision from a
fixed menu on G6's `SolverPlanner` pattern, with a recorded fallback. It decides nothing about evidence.

**Spend.** The runner's count holds $10.50 for each run that started and never ended. Four such runs were killed, and the rule says each
counts until it is closed with its streams' cost. They will be closed that way when no stream is running, with the amounts in the report.
The $180 stop and the loop's $170 stop are unchanged.

**Codex's wiring review (2026-10-31 night, before any stage 3 run).** The owner holds that the gap is wiring: the primitives exist and
are not composed to address the failures. Codex was asked to test that and try to break it, reading the source, the loop, the PRs, the
override design and training evidence only, with a proof of reading.
- **Its verdict: partly.** "Mainly wiring" is not established. The useful missing wiring concerns the quality, delivery and reuse of
  evidence. Connecting more parts will not by itself find the right requirement, generate a distinguishing input or correct a mistaken
  expectation, and an unstated maintainer choice or an interpreter the container lacks is out of any wiring's reach.
- **Counterevidence, both checked in the archive.** On `L9-black-5634-2`, all 48 scout failures left at the end also failed before the
  patch, and the run passes the maintainers' tests: the fixer was right to leave them, so more repair pressure could only have done harm.
  L9 also tells the fixer a critic's test "failed before your change" when no base result exists, which is a wording fault. L9 keeps it:
  changing it now would change what agents see, and nothing shows a lost pass.
- **Its decision: freeze L9 now**, without its three proposed additions, none of which has a demonstrated gain. This plan does that.
- **Its estimate, low confidence and labelled an inference:** about +5 points on unseen bugs in training repositories and about 0 in new
  repositories, with a negative result plausible. It would budget zero for its own additions until shown.
- **Taken into stage 3, and no agent sees any of it:** the proposer gets opaque ids in place of the maintainers' test names and reads the
  issue; a near miss now says what repair changed and how many failures predate the patch, so mistaken probes show; and records are
  taken across bugs, because the proposer reads only the first eight. A test keeps stage 2's repositories apart from every other pool.
  Reports add a 95% interval that resamples whole bugs through G6's `compare_methods`, read beside the rule and not by it.
- **Not taken:** `Pool`, which four candidates do not need; final-artifact review, whose gain is unshown; and a scout-only limit, because
  each candidate changes one part anyway, and the model chose the scouts on every real call so far.
- **G6's gate is not applied.** `gate.py` needs 30 confirmation cases, and stage 2's bugs give 20 outcomes on ten bugs. A candidate that
  wins is therefore reported as recommended and tested, never as promoted by G6.

**Stage 2 grows, by a rule on cost alone (before any stage 2 issue is read).** Ten bugs give 20 outcomes, too few for a real gain to clear
the rule, and stage 3's comparison with its parent runs on the same bugs. The money is there: the four killed runs measured $1.26 in all,
against $42 the runner holds for them, so real spend is about $34, not its $85. But L9's cost per run is not settled, and one run on an
easy bug has passed $1.18. So stage 2's size is set when L9 ends, from `loop` rows alone: with `u` L9's mean cost per run on the training
bugs, and `left` the $170 stop less the spend then recorded, the bugs are the largest `n` from 10 to 20 for which
`u * (37 + 10 + 28 + 4n) + 8 + 0.24n <= left`. That counts M9 on stage 1 and the held-out bugs (37 runs), L9 on the development pool (10),
at most eight screens and two paired checks (28), M9 and the trained arm on stage 2 (4n), and $8 for arms A and T on stage 1 and for proposals, with
A and T on stage 2. No outcome enters it, and it is fixed before any issue is judged. If the $170 stop arrives first, the runs that remain
are not made, and the report says so.

### 2026-10-31 (night): a control, a trust measure and reference material (before any M9 run and before stage 2's bugs are judged)

**What the owner asked.** That the system can be trusted when it says a change is done, that its debugging strategy be better, and that the
loop be coded into G6 itself, for any cognitive work, with programming as the first case.

**Arm R, a control.** Claude Code with the issue's discussion, as arm T, plus one review from another model family and one repair if the
reviewer's test fails on the patch. No scouts, no probes, no G6. It runs twice on each stage 1 and stage 2 bug and five times on each held-out
bug. If it matches M9, the gain comes from a second reviewer and not from the loop or G6, and the report says so. It joins the comparison as a
report and does not change the frozen rule, which stays M9 against A and T. Its cost goes into the sum that sets stage 2's size.

**The trust measure.** Every report now says, for each arm, how often "done" meant right: on the runs a system called clean, and on those it
left unsure, on both scores. On the saved training runs so far, the signal does not separate good patches from bad: G6's review gave
"insufficient evidence" on 29 right and 14 wrong patches, its integrity alarm fired on five correct patches, and the loop's "clean" was right 7
of 9 times while "unsure" was right 7 of 7. That is a small, tuned sample and is reported as a warning.

**Reference material for the proposer.** With `G6_LIBRARY` set, stage 3's proposer reads up to three passages from the person's own folders.
They reach the proposer and nothing else, and a text sharing eight words in a row with a passage is refused. Reference material can only
improve what the proposer writes: it is not verification. **Web search stays off for every agent in these studies,** because each bug's fix is
public and search would break the rule that agents never see the maintainers' tests.

**The loop in G6.** The design approved on 2026-10-29 stands, and its next phase, the local shadow loop, is being built on its own branch with a
domain-neutral seam. `evolve.py` stays here for stage 3 until that loop can replace it. Nothing built there changes these runs.

### 2026-10-31 (late): the final review, and a feed of labelled patches (before any M9 run and before stage 2's bugs are judged)

**Why.** The trust measure above shows that nothing G6 says yet separates good patches from bad. The approved loop design rests its gate decisions
on replays of recorded patches, and none exist for coding. `final_review.py` starts both.

**What it is.** After a run has ended, another model family reads the finished patch, with L8's reviewer text unchanged, and its test, if it wrote
one, runs on the parent and on the patch, in a container with no network from the agent's image. The reviewer gets the issue with its discussion,
the parent and the patch. It gets no maintainers' test or fix, and is not told the arm or any verdict. Each distinct patch is reviewed once, in
the order of the patches' digests.

**What it may say.** "Reproduced": its own test ran and failed on the patch. "Opinion": it says defect, with no such test. "None": it found
nothing. "Failed": there was no review, which is no verdict, and it is made again. Each is reported as right or wrong on both scores, beside what
the run said of itself.

**What it does not do.** It changes no run, no verdict, no repair and no rule. L9 and M9 are unchanged, and no agent ever reads a review. The
frozen comparison stays M9 against A and T.

**When.** On the training bugs' saved patches now, as development: 79 distinct patches, 60 right and 19 wrong on the amended tests. On a stage's
bugs only after all of that stage's runs have ended, and nothing read from those is used to tune anything.

**The limit, stated before any result.** 14 of the 19 wrong training patches come from one bug, black-5634, and the other five from two more. The
effective sample is about three bugs, not 79 patches. A result on it is a lead for the loop to test on unseen bugs, not a measure, and it is
reported bug by bug. Reviews use the owner's Codex sign-in, outside the budget, and are reported apart.

### 2026-11-01: L9 frozen, the replay's result, and stage 2's size (before any M9 run and before stage 2's bugs are judged)

**L9 is frozen.** On the seven training bugs, tuned: 12 of 14 on the maintainers' tests and 14 of 14 on the amended tests, against arm A's 9 and 11. Mean cost $0.59 a run, highest $1.11, against about $0.06 for arm A. These are scores on bugs it was tuned on, and they are reported beside the held-out scores, never in place of them. M9 runs L9's text unchanged on G6 c53d310.

**The final review on the training patches.** 79 distinct patches, no failed review. It found nothing on 34, and all 34 were right. It reproduced a defect on 45, of which 19 were wrong and 26 right. So it flagged all 19 wrong patches, and a flag meant wrong 42% of the time. This is development data, the wrong patches sit on three bugs, and attrs-1593 alone gave 9 false alarms. It says nothing yet about unseen bugs, where it runs report-only after each stage's runs have ended.

**Closures.** Four runs killed before they ended are closed at their streams' measured cost with the runner's 5% margin, $1.32 in all, in place of a $10.50 hold each: black-5634 arm G1, arm L7 and arm L9, and black-5144 arm A. None gave a result and none is counted.

**Stage 2's size.** The rule with arm R added, taking $0.20 a run for R as an upper estimate until R has run: 0.59 × (75 + 4n) + 8 + 0.24n + 0.20 × (37 + 2n) ≤ what is left of $170, about $132. That holds at n = 24, so stage 2 takes the plan's upper limit, 20 bugs.

### 2026-11-01 (later): where the loop's gain comes from, and arms X1 and X2 (before any X run)

**Why now.** The owner asked where exactly the bottleneck is, whether the research code should be switched on, and whether the bugs should change. Stage 1 goes on unchanged from its own worktree: nothing here touches M9, T, R or A on a stage's bugs, or the comparison rule. Everything below reads the seven training bugs only.

**The first patch decides the run.** `check` scores each round's patch, so the first can be set against the last. In 70 saved runs of E, G1 and L2 to L9, the first patch's verdict on the maintainers' tests was the final verdict every time: no failing patch became a passing one, and no passing one a failing one. So L9's gain over arm A on training came before its first review, from the scouts and the discussion. Its critic and repairs added nothing the score can see. That does not show they cannot: L9's only failures are `click-3678`, which the maintainers' tests put out of honest reach, so the critic has never had a wrong patch it could fix. A repair of a defect the maintainers' tests do not cover also scores nothing.

**Detection is not the bottleneck.** On all 14 wrong `black-5634` patches the final review states the same rule, and it is the failure the maintainers' tests catch; on the three wrong `pydantic-14052` patches it does the same. It saw neither test. Its text names no bug, so it is the likeliest part of the loop to carry to unseen bugs. Two fixable bugs are few, and it also flagged 26 right patches.

**Fresh draws and a selector help less than hoped.** On saved patches, keeping the first patch the review finds nothing in lifts `pydantic-14052` from 0.80 to 1.00 by four draws and leaves `black-5634` at 0.38 from 0.26, because a clean review of black is rare. Choosing among candidates by each other's probes lifts `black-5634` from 0.08 to 0.16 and `pydantic-14052` not at all: attempts on black fail alike, so more of them adds little.

**The research code is not the missing piece.** ECHA, CSIR and AHI record, certify and gate evidence. Nothing outside `g6.research` imports them, and none of them outputs anything that changes what an agent does next, so switching them on would change no score. AHI's funnel is the right question to ask of a run, and the table above is that funnel by hand. `coding_learning`'s probes fire only on the same function of the same repository, so never on an unseen bug.

**What "trained" has meant so far.** Four of the seven training bugs pass in every arm, and `click-3678` cannot pass, so two bugs carry all the signal. The changes from L1 to L9 were written by hand in session, mostly from one of them, and the plan has said so. A harness tuned on two bugs has no reason to generalise, whatever it is called. Metacognition generalises only if what is learned changes what gets observed on a new bug, and only if there are many failures to learn from. This is the case for a larger failure-rich training pool, which stage 2's preparation can supply, and it is reported as a limit of L9, whatever M9 scores.

**The one link never measured: a right finding, then a right fix.** Three arms, Sonnet, no G6, no scout, with the issue's discussion. All are development, on training bugs:

- **X1:** a fresh session on a saved wrong patch, told the saved review's rule, reasons and test.
- **X2:** a fresh session on the parent, told the same, and told the attempt was thrown away. If anchoring on the first idea stops a repair, X2 beats X1.
- **R** on `black-5634`, `pydantic-14052` and `click-3678`, twice each: the same-session repair, with its patch from before the review now kept so `check` scores it. This changes nothing an agent sees, and stage 1's R runs, on the earlier code, do not have it.

The cases are fixed by rule in `convert_cases.json`, before any run: every distinct patch from a no-scout arm (A, E, G1) that the review reproduced a defect on and that is wrong on the amended tests, ten in all, six of them black. The controls are four such patches that are right: the first four by digest, one per bug first. They measure harm. X1 and X2 use the saved reviews, so they make no Codex call. About $4 of the $180.

**How it will be read.** Conversion is the share of wrong starts whose final patch passes, on both scores, by bug, and it is ten starts on three bugs: a lead, not a measure. At least half converting on black or pydantic means the critic can carry a loop, and the next generation is a critic-led one, reviewing again after each repair until it finds nothing, with no scout. Under a quarter on both means repair is the limit, and what is tried next is a stronger fixer or a better statement of the behaviour. Between the two, it is judged by which of X1 and X2 did better. A next generation is judged on stage 2's bugs, beside M9, never on a bug it was shaped on.

**Disclosure.** Reading the end of stage 1's log while checking on it, I saw three arm T outcomes on fresh bugs, as I had seen arm A's last rows before. They were used for nothing, and stage 1's progress is now read by line count only.

### 2026-11-01 (later still): X's first result, and arm K, reviewed until nothing reproduces (before any K run)

**What X showed so far.** `pydantic-14052`: all 6 wrong starts converted, X1 and X2 alike, at about $0.05 a run. `black-5634`: 0 of 12. Starting from the parent changed nothing there, so anchoring on the first idea is not what stopped them. In 5 of the 6 X1 runs the agent agreed with the reviewer and fixed the case it reported, with the repository's suite passing, and in one it declined. The maintainers' tests failed because they need `# fmt: off` inside an indented block, and the review had reported a trailing comment at the top level. Its rule was right and its test was one layer of the defect, so the repair stopped at that layer. The review's rules on three scout-arm patches, which had dealt with the top level, do name the indented block. Those are the only sign that a second look reaches it, and that is what arm K tests. The rule above, at least half on either bug, sends the next generation to a critic-led loop.

**Arm K.** Plain Claude Code on Sonnet with the issue's discussion, as in arm T, with no G6, no scout and no probes. When it finishes, Codex reviews the patch as in arm R. If its test fails on the patch, the agent gets the rule, reasons and test in the same session. Then a fresh Codex review reads the new patch, and so on, up to `MAX_REPAIRS` repairs, three, so four reviews at most. It ends when a review reproduces nothing, when the agent leaves the code as it was (it may reject a finding), or when the repairs run out. Earlier reviewers' tests stay in place. Each review's patch is kept for `check`. A failed review ends the loop and is recorded as no clean review. Nothing a review says scores a run.

**How it is judged.** `K1` first runs twice on the seven training bugs, as development, and a fault it shows may be fixed before it freezes. Its design comes only from training bugs, and it was fixed before any stage 1 result was read. Once frozen it runs twice on the eleven fresh bugs, five times on the held-out bugs and twice on stage 2's, by the comparison rule against A and T, on both scores. M9 stays stage 1's frozen primary, so K on stage 1's bugs is a second look, and it is reported as one. Stage 2 is the first test of K on repositories nobody has tuned on. K was written in session, so it can never count for stage 3. A cost of about $0.20 a run keeps the three stages inside the $170 stop; Codex's reviews are outside the budget and are reported apart.

### 2026-11-01 (morning): X's whole result, K's first eight runs, and a second stop (before any K run on a bug kept for measurement)

**X as it ended.** Of 20 wrong starts, 8 converted on the amended tests: all 6 on `pydantic-14052`, both on `click-3678` (the maintainers' tests still fail there, as they must), and none of the 12 on `black-5634`. X1 and X2 did the same. None of the 8 control runs lost a right patch.

**R on the training bugs.** `R-pydantic-14052-2` is the first run in the archive whose first patch failed and whose final patch passed, for $0.06. Both R runs on black stayed wrong after their one repair, as X had.

**K1's first eight runs, on four training bugs.** All of these are development.

| On these four bugs, two runs each | Maintainers' tests | Amended tests | Mean cost |
|---|---|---|---|
| A | 3 of 8 | 5 of 8 | about $0.06 |
| L9 | 6 of 8 | 8 of 8 | $0.58 |
| K1 | 6 of 8 | 8 of 8 | $0.15 |

Four runs went from a failing first patch to a passing final one: both `pydantic-14052` runs at the second patch, and on `black-5634` at the third patch in one run and the fourth in the other. In the 70 earlier runs that happened never. So the second look reached what the first did not, as the three scout-arm rules suggested. This says the mechanism works, on these bugs, and does not say it generalises: the idea for K came from reading black's failure, and it is scored here on black. Four conversions on two bugs are few. `click-3678` stays failing on the maintainers' tests, as it must.

**What K does not do yet.** The reviewer does not know when to stop. Four of the eight runs ended at the cap with a defect open and a patch that passes the amended tests, and a fifth (`click-3678`) ended with the agent declining a finding, so "defect open" does not mean wrong. `attrs-1593` repeat 2 was right from the first patch and was repaired three times, with no harm the tests can see and $0.08 spent. That is K's false-alarm cost. K's text does not change for this: a change now would be tuned on these eight runs.

**The second stop.** Claude Code's memory reaper stopped stage 1 and K at about 08:30, at the start of stage 1's M9 runs on fresh bugs, after 8 of K's 14 training runs. No host process survived, so their containers were orphans and were removed. Two runs that gave no result are closed at their measured cost and will be remade, and that is disclosed: M9 on `pydantic-13243` repeat 1, $0.89, and K1 on `fastapi-15077` repeat 1, $0.05. Spend stands at $49.63. The cause was mine: I ran two streams together, with a lower memory floor for the second, after recording that doing so caused the first stop.

**From here, one stream at a time, never two.** K1's six remaining training runs; then stage 1 as frozen; then K1, unchanged unless a remaining run shows a fault, on the fresh bugs and the held-out bugs. No result of stage 1 is read before K1 runs on those bugs, so K's freeze stays independent of them. Codex's quota is a risk here: stage 1 and K together ask for about 160 more reviews, and a review that fails leaves a run as plain Claude Code, recorded as such.

### 2026-11-01 (morning, later): failure-rich bugs, for stage 2 and for G6's own training (before stage 2's bugs are judged)

**Why.** The owner asked whether the bugs should change. In part they should. A harness can only gain on runs Claude Code alone fails, and to clear the comparison rule it needs at least 5 wins and no losses on the runs that differ. A bug arm A always passes adds a pair that cannot differ in the harness's favour, and four of the seven training bugs were of that kind, which is why so little could be learned from them.

**What does not change.** Stage 2's draw, its 20 bugs, its arms, its audit and the comparison rule. The comparison on all 20 stays the primary result. No bug is dropped, and no bug is chosen by how a harness did on it.

**What is added.** Before any comparison run, arm S runs three times on each stage 2 bug. S is plain Claude Code, exactly as arm A, about $4 in all. S's runs are never rows of the comparison: arms A, T, R, M9 and K make their own runs afterwards. If S's failures chose the bugs and A's two runs were those same failures, a bug's bad luck in screening would flatter every harness. Fresh A runs remove that. A bug is failure-rich on a score if at least one S run fails on that score.

**How it is read.** The same rule, on the same fresh runs, is applied to the failure-rich bugs alone, on each score, and reported beside the result on all 20, as a second analysis fixed here before any S run. It answers a narrower question, bugs Claude Code alone gets wrong at least sometimes, and it is never reported without the result on all 20. The count of failure-rich bugs is reported, and if it is too small to clear the rule, that is the finding.

**G6's own training.** Stage 3's development pool takes its bugs the same way, with one difference: it is development, so a candidate that S passes three times in three gives way to the next one, and the record says so. G6's training then has failures to learn from, which L1 to L9 mostly did not. The pool stays disjoint from stage 2 by repository.

### 2026-11-01 (midday): K1 frozen (before any K run on a bug kept for measurement)

K1's six remaining training runs all passed at their first patch and showed no fault, so K1 freezes as it stands: the code of commit ea1e491, three repairs at most, and no text of its own. On the seven training bugs it scored 12 of 14 on the maintainers' tests and 14 of 14 on the amended tests, at a mean of $0.11 a run, beside L9's 12 and 14 at $0.59 and arm A's 9 and 11 at about $0.06. These are training scores, and K's idea came from one of these bugs, so they are reported beside the held-out scores and never in place of them. No stage 1 result had been read when it froze.

## 2026-11-01 (evening): recorded before any stage 1 score is read

No rule changes here. The comparison rule, its arms and its bugs stand as frozen.

**What the rule can show.** At the training pass rates (9 of 14 for the controls, 12 of 14 for the treatment), two runs per bug per arm, simulated with 200,000 seeded trials and an exact binomial check, the rule fires with probability 0.34 at 11 bugs, 0.45 at 14, 0.64 at 20 and 0.92 at 40. If both repeats of a bug agree, the figures are 0.36, 0.45, 0.59 and 0.84. Unseen rates may well be lower than training rates. So at 11 bugs a "not shown" is the likelier outcome even if the training effect is real, and it is a statement about the sample as well as the method.

**A disclosure for K.** In `reviews_until_clean`, a review that says `defect` with a test that will not run ends the same way as a clean review. K1 stays frozen, so its report will count how many runs ended that way, on each score.

## 2026-11-01 (evening): K1 is a named second primary

The owner named K1 a second primary on 2026-11-01, before any stage 1 score was read.

- **M9 stays primary, and its rule does not change.** K1 is judged by the same rule, against A and T, on the same bugs, with both scores. The two results are reported separately and never pooled.
- **Two primaries are two chances.** Beside each result goes its exact p value and whether it also clears 0.025, which is the bar with both counted: at least 6-0 or 8-1 on differing pairs, against 5-0 or 7-1 at 0.05. A result that clears 0.05 and not 0.025 is reported as exactly that.
- **K1 on stage 1 bugs is still a second look,** since its method was tuned on those repositories. Stage 2 is its first test on untuned repositories. K1 can never count for stage 3.

**The credits.** The owner confirmed that the API credits expire on 2026-11-04, and the work carries on. Stage 2 therefore runs whole bugs in the frozen order, with every arm on a bug before the next bug starts, so no pair is missing. If time runs out, the rule is applied to the bugs that finished, the number is stated, and no bug is dropped or chosen after its results are seen.

## 2026-11-01 (late): stage 3 becomes the loop's own tick

No stage 3 run has started and no stage 1 score has been read. Stage 3 asks whether G6 can propose a change to itself from its own failure records, with no change written in session. As written, `evolve.py` is started from a session, so a person still chooses when it runs and what it is shown, and "nothing yet shows G6 designing itself" would stay true after it.

- **The change.** The self-improvement loop's own tick proposes the change, on a domain that tunes the reviewer: it writes a test, G6 runs it, and an exact judge compares that with the maintainers' verdict. The reviewer never sees the fix.
- **Its cases.** Recorded patches from the training bugs and from further development bugs. No fresh, held-out or stage 2 bug enters them.
- **Its comparison.** The winner is a change to the reviewer, so its parent is K1. It runs twice on each stage 2 bug that finished, as a new arm, against K1, A and T, under the same rule, with both scores, and is described as recommended and tested, never promoted. K1 itself still cannot count for stage 3: only the change the loop made can.
- **If the loop adopts nothing,** stage 3 reports that. "Not shown" is a valid ending, and no change is written by hand to fill the place.
- **Timing.** That domain is not built yet, and it lands after the API credits expire on 2026-11-04, so stage 3's runs need new credit, which is the owner's decision. `evolve.py` stays in the repository, unused for this stage.

## 2026-11-01 (night): K1's report, written before any stage 1 score is read

No rule, arm or bug changes here. `compare.py` could not yet report what the two entries above promise, so it is changed now, while no stage 1 score has been read and no K1 run has started on a fresh or held-out bug.

- **Sections of its own.** K1 is printed in sections named for it, against A and T, on both scores, and is never pooled with M9. R and M9 appear in those sections as "report only".
- **The bar with both primaries counted.** Beside the rule, each result says whether it also clears 0.025, for M9 as well as K1.
- **What K1 said.** K1 was counted as an arm with no loop, so its trust table would have shown a plain "done". It now says "clean" or "unsure" from its own record, as M9 does.
- **How each run ended.** The report counts K1's runs by the end of the last review, on each score: nothing found, a defect with no test, a defect whose test would not run, a defect whose test found nothing, or the reason on the record. On the training bugs, already seen, 8 of the 14 runs found nothing, 1 ended on a defect whose test would not run, 4 ran out of repairs with a defect open and 1 ended with the agent leaving the code as it was. So 9 runs said "clean", and one of those rests on no test that ran.
- **Stage 3's comparison** now names K1 as the parent, as the entry above says, where the script still named `--loop`.
- **No verdict on tuned bugs.** A Codex review of this change showed the tuned sections printing "frozen rule: met" against A alone. Seen bugs are development, so the rule gives no verdict on them, for L9 as for K1, and those sections now say that it was not applied.
- **The bugs that finished.** A second Codex review showed the script still reading every planned stage 2 bug, so one bug never started would have turned any result into "not shown", for M9 as well, against the entry on the credits above. Stage 2 is now read as the bugs every arm finished, in the frozen order, stopping at the first that did not, and the report states the number. Stage 3 reads the same list, as far as its arm reached. How far the runs got decides the list, and how a bug came out cannot.

## 2026-11-02: what happened to stage 1's K1 runs, written before any stage 1 score is read

- **Three more kills, by the session's memory guard.** Three K1 runs were killed part way, `click-3434` once and `starlette-3166` twice, measured at $0.09, $0.08 and $0.35. All are closed as killed, not counted as failed, and remade, as the rule on infrastructure kills says. Their containers stayed up with nothing driving them, so any spend after the kill is not in the tally. These are counted with the earlier orphaned run in the report.
- **A verdict recovered from its record.** `run.py` prints at most 8,000 characters, so the check of `K1-attrs-1509-1`, four rounds long, cut its own verdict off, and `loop.one` said "no verdict" twice. The check had finished both times with the same result. The row was written from that check's saved record, once every patch of that run matched the record by size. No run was repeated and no outcome was read to do it. `check` with no run named scores the latest run of a bug, so retrying that check after repeat 2 had finished would have scored the wrong patch: this is why the row was not left to a retry. `run.py` is unchanged. The same printing limit cannot give a wrong verdict, only none.
- **The run order changed by accident.** That stop made the stream skip to the held-out bugs, so K1's last four fresh bugs ran after them. No rule depends on that order.

## 2026-11-02: stage 1's result

Every run had ended and every amended verdict was in at 08:30, and no score was read before that. Killed runs were remade as recorded above.

**The rule.** Neither primary meets it, on either score, so uplift over Claude Code alone on stage 1 is not shown. Neither clears 0.025, and the 95% target is not met. M9's best is 72.7% and K1's 68.2%, both on the amended tests.

| Runs passed | A | T | R (report only) | M9 | K1 |
|---|---:|---:|---:|---:|---:|
| Eleven fresh bugs, 22 runs, maintainers' tests | 10 | 9 | 9 | 9 | 9 |
| Eleven fresh bugs, 22 runs, amended tests | 13 | 12 | 13 | 16 | 15 |
| The seven determined bugs, 14 runs, both scores | 9 | 8 | 9 | 8 | 8 |
| Held-out second look, 3 bugs, 15 runs, both scores | 5 | 0 | 5 | 6 | 5 |
| Training, tuned, 14 runs, maintainers' tests | 9 | not run | not all run | 12 (L9) | 12 |
| Training, tuned, 14 runs, amended tests | 11 | not run | not all run | 14 (L9) | 14 |

| Pairs that differ | Against A, maintainers | Against A, amended | Against T, maintainers | Against T, amended |
|---|---|---|---|---|
| M9, fresh | 1-2, p 0.875 | 4-1, p 0.188 | 0-0 | 4-0, p 0.063 |
| K1, fresh | 0-1, p 1.000 | 3-1, p 0.313 | 1-1, p 0.750 | 4-1, p 0.188 |
| M9, held-out | 6-5, p 0.500 | the same | 6-0, p 0.016 | the same |
| K1, held-out | 5-5, p 0.623 | the same | 5-0, p 0.031 | the same |

The 95% interval by bug on the amended tests against A is -5% to +36% for M9 and -9% to +32% for K1.

**What the counts show.** These are counts by bug and arm. No fix, test, run or reply of these bugs was read for them.

- **Most bugs cannot separate the arms.** On the maintainers' tests 9 of the 11 fresh bugs gave every arm the same score: four passed in every arm and five failed in every arm. So at most 3 of 22 pairs differ, and the rule needs at least 5.
- **The loops' gain sits on the amended bugs.** On those four bugs, 8 runs, the amended tests give A 4, T 4, R 4, M9 8 and K1 7. On the seven determined bugs the loops are one run behind A.
- **Two effects cancel on the held-out bugs.** `click-3769`: A 0, T 0, R 5, M9 4, K1 5 of 5. `starlette-3718`: A 5, T 0, R 0, M9 0, K1 0. `black-5521`: M9 2, every other arm 0.
- **The issue's discussion is the likely reason for the second.** Every arm that reads it scored 0 of 5 on `starlette-3718`, and the only arm that does not scored 5 of 5. T against A over the 37 pairs is 1 win and 7 losses on the maintainers' tests. R, M9 and K1 all carry the discussion. This rests on one bug, and its cause has not been read, so it is a hypothesis.
- **"Clean" did not mean right.** K1 said clean on 8 fresh runs, right on 3 by the maintainers' tests and on 6 by the amended ones, and on 10 held-out runs, right on none. All 8 held-out runs whose last review found nothing were wrong. M9's "clean" was right on 6 of 11 fresh runs by the maintainers' tests, 11 of 11 by the amended tests, and 3 of 9 held-out. A's "done" was right on 10 of 22, 13 of 22 and 5 of 15.
- **Repairs ran out** on 13 of K1's 22 fresh runs and 2 of its 15 held-out runs.
- **Training did not carry.** K1 and L9 went from 12 of 14 on training to 9 of 22 on fresh bugs, while A went from 9 of 14 to 10 of 22. The bugs differ, so this is a rough reading, but the uplift over A fell from +21% to -5% on the maintainers' tests.

**What it does not show.** That the loops have no effect. The rule was expected to fire about one time in three at 11 bugs even if the training effect were real, and the interval on the amended tests includes useful gains. M9 and K1 do beat T on the held-out bugs, but the rule asks for A as well.

**Spend.** $82.74 by the runner and $34.65 for Fable's reviews, $117.39 of $180. What the orphaned containers spent after their kills is unmeasured.

**Still to come for stage 1:** the final review of these patches, report only.

**Stage 2 stands as frozen.** Its bugs are unseen, and M9, K1, the rule and the order do not change.

**One exploratory arm is added, before any stage 2 run: K0.** It is K1's review loop on arm A's prompt, without the discussion. It tests the hypothesis above, and it is closer to the product, where K reviews whatever a person asked and nobody supplies a discussion. It was chosen after reading this table, so it is report only: never a primary, no verdict of the rule, and it runs last on each bug, so `finished` never waits for it. A good result would be a hypothesis for a fresh test, not a finding.

## 2026-11-02 (morning): a scored bug becomes development for later generations, before any stage 2 run

No recorded result changes here. Stage 1's numbers stand, and M9, K1, K0, the rule and stage 2's order and sealing do not change.

**Why.** The owner: "failure is good as long as we learn from this and don't repeat our mistakes - how else do we get enough signal to learn?" Stage 3's cases were limited on 2026-11-01 to the training bugs and further development bugs. That leaves too little to learn from: on the amended tests L9 and K1 have no failed run on the training bugs. Stage 1 left 118 failed runs on the maintainers' tests and 95 on the amended tests. They were barred so that stage 1 could be a fair test of M9 and K1, and it now has been.

**The change.** A bug whose stage has been scored and recorded is development for every generation that comes after. So stage 1's eleven fresh bugs and three held-out bugs join the training bugs from this entry on, and stage 2's bugs join once stage 3's runs have ended. This replaces "No fresh, held-out or stage 2 bug enters them" in the entry of 2026-11-01 (late), for fresh and held-out bugs only.

**What holds it honest.**
- **A recorded result is never rewritten.** Stage 1 was the test of M9 and K1 and stays that. Nothing read from these bugs reaches M9, K1 or K0, which are frozen.
- **What a generation tuned on is never its unseen score.** Stage 3's rule reads stage 2's bugs, as before. If its change read stage 1's failures, then stage 1's bugs are development for it, so `compare.py`'s section "stage 3, stage 1 bugs" gets no verdict of the rule, and is reported beside the stage 2 result as a tuned score.
- **Stage 2 stays sealed.** No fix, test, run or reply of a stage 2 bug is read to tune anything until stage 3's runs have ended.
- **No agent that runs a task, reviews a patch or proposes a change sees a maintainers' test or fix, on any bug, ever.** Whatever reads a failed run gets the issue, the patch, the run's own record and the reviewer's replies. Anything drawn from the maintainers' tests or fix reaches those agents only as counts worked out by plain code on the judge's side: passed or failed, how many tests failed, and whether the patch touched the same files as the fix.
- **Blind labels, as the audits have been since 2026-10-30.** To learn why a patch failed, an auditor who runs no task, reviews no patch and proposes nothing may read the maintainers' tests and fix, and returns labels from closed lists and no text. Only the labels are kept. Plain code turns them into counts. Two raters from different model families label independently, and their agreement is reported beside the counts. A label is a model's opinion and never changes a verdict.

**What it costs.** Stage 1's bugs can no longer be an unseen test of anything built after this entry. They are all from the six training repositories, so stage 2's new repositories were always the stronger test. The next unseen set after stage 2 has to be drawn fresh.

## 2026-11-02 (morning): stage 2's twenty bugs and their audit, before any of their runs

**The bugs, in the frozen order.** `urllib3-5347`, `urllib3-5254`, `charset-normalizer-772`, `charset-normalizer-715`, `setuptools-5362`, `setuptools-5352`, `pygments-3204`, `pygments-3158`, `pluggy-809`, `pluggy-799`, `botocore-3852`, `botocore-3835`, `anyio-1424`, `anyio-1354`, `s3transfer-423`, `python-dotenv-711`, `python-dotenv-700`, `pyjwt-1216`, `pyjwt-1201`, `platformdirs-666`. Every one passed `images`, `validate`, `selfcheck` and `freecheck`. `aiobotocore-1704` failed `validate`, since no test fails on the parent and passes with the fix, so it gave way under the rule. `selection_stage2.md` is the record of how they were reached. No model has run on any of them.

**The audit, labels only.** A blind auditor read each issue and the tests its fix changed, and returned labels and nothing else. All 20 issues report a bug on their own text. Determined by the task, 13: `urllib3-5347`, `urllib3-5254`, `charset-normalizer-772`, `pygments-3204`, `pygments-3158`, `pluggy-809`, `botocore-3852`, `anyio-1424`, `anyio-1354`, `s3transfer-423`, `python-dotenv-700`, `pyjwt-1201`, `platformdirs-666`. Not determined, 7: `charset-normalizer-715`, `setuptools-5362`, `setuptools-5352`, `pluggy-799`, `botocore-3835`, `python-dotenv-711`, `pyjwt-1216`. No bug is dropped. The score on all the bugs that finish is the score, and the score on the determined bugs goes beside it, with the same rule applied to each comparison, as on stage 1. The seven get amendments under the 2026-10-31 rule. An amendment is scored offline with no model, so the runs do not wait for it, and it is written without reading any run.

**The order of the runs.** Arm S three times on every bug first, as the 2026-11-01 entry says. Then, bug by bug in the frozen order, A, T, R, M9 and K1 twice each, and K0 twice last. One stream, floor 6.5 GB, and the stream stops once the whole study has spent $170, which counts Fable's $34.65: `loop.py`'s own stop reads only the runner's tally, so the driver holds the study's figure. At about $2.70 a bug that is about 19 of the 20.

**The credits.** Stage 2 runs on the credits that expire on 2026-11-04. Its bugs stay sealed, and nothing read from its runs reaches the loop until stage 3's runs have ended.

## 2026-11-02 (afternoon): one more kill, and the limit restated

**One more kill, by the session's memory guard.** Stage 2's stream was killed at about 15:42, part way through `setuptools-5362` arm M9 repeat 1. The run measured $1.32. It is closed as killed at $1.38 with the runner's 5% margin, not counted as failed, and remade, as the rule on infrastructure kills says. Its container stayed up with nothing driving it for at most about five minutes, until it was removed, so any spend in that time is not in the tally. Every saved run stands: arm S on all 20 bugs, the first four bugs whole, and A, T and R on the fifth. No score was read to do this: `stage2.log` is read by its markers only.

**The limit.** The owner restated it at the restart: the API credits are strictly limited to $180. So the ruling of 2026-11-02 on cost covers the loop's gate and nothing else, and the stream's stop stays at $170 for the whole study, which leaves room for a run already under way. The study stands at $132.38, Fable's $34.65 included. If the stop comes before the twentieth bug, the rule applies to the bugs that finished, with their number stated, as the 2026-11-01 entry says.

## 2026-11-02 (evening): stage 3 as it will run, before its tick

Stage 1 is scored. Stage 2's bugs stay sealed, and no score of theirs has been read. M9, K1, K0, the comparison rule and stage 2's order do not change. No reviewer reply has been read, and no reader has been scored: the only Codex calls so far timed three tuning cases, and whether their verdicts were right was not looked at.

**The domain.** `CodingReplay` on the patches of the bugs stage 1 scored, as the entry of 2026-11-02 (morning) allows. `export_reviewer_cases.py` makes one case for each distinct patch that has a verdict: 416 patches on 21 bugs. A case holds the issue as the engineer saw it, with its discussion, the patch, and the code round the change at the parent, read from the agent's own image. That matches what K1's reviewer may read, narrowed to the files the patch touches, since a replay is one model call with no tools. A label is passed or failed from the independent check, and on a bug with an amendment only the final patch has one, from the amended tests. No maintainers' test or fix reaches a case. Stage 2's bugs are refused by name.

**The cut.** Three bugs chosen by a seeded hash go behind the seal: 67 cases. The other 18 bugs, 349 cases, are cut by bug. The loop's first seed from 20261102 gave a tuning side of one bug, so the seed is the first from 20261102 up with at least 4, 3, 5 and 3 bugs on the tuning, selection, held-out and monitor sides, and at least 15, 10, 30 and 10 of each label. That rule reads labels and bug names and no reply. It gives 20261106: tuning 77 cases on 4 bugs, selection 50 on 3, held-out 127 on 5, monitor 95 on 6.

**The reviewer and the proposer.** Both are Codex on the owner's sign-in, as K1's reviewer is, so no API credit is spent. The reviewer starts from K1's own reading steps, word for word, and the verdict line sits in each case, so a proposer edits how it reads and not the form of its answer. The proposers are the loop's own, and they see each failed case's input, its expected label and the reviewer's reply. No hypothesis of mine is in their prompts: what the blind labels and the funnel suggested stays out, and afterwards it is scored whether the loop reached it.

**The method, fixed before the tick.** Two cycles, three candidates a proposer, 300 tuning calls a proposer, one race block of the whole selection split, then one held-out evaluation at the gate's own threshold of 0.01. The single block comes from a rehearsal with a fake model on these cases: cases run in bug order, so a shorter first block held one bug and one label, every candidate tied, and the race dropped a perfect candidate on nothing. That is a defect in racing grouped cases. It is worked round here and goes on the loop's list. By my estimate a tick is about 1,000 to 1,300 calls and six hours.

**The code.** The tick runs from `claude/loop-reflex` at bc9e75e, unchanged, and the owner enrols it. Batch 1's third review and the reflexive step's first review have not finished. What they find is reported beside stage 3, and a repair after enrolment does not reach this tick. A tick needs a person's enrolment, and that is the only person in it. It is one tick from `stage3_tick.py`, which starts no runner.

**Codex beside the K arms.** The 2026-11-01 decision kept the loop away from the study's K1 runs so that the two never contended for Codex. Read today, Codex's only limit is a weekly one, at 14%, resetting on 2026-11-04 at 13:48 UTC. So the tick and the Codex reviews of our own code run alongside the stream, and the tick's watcher stops the loop at 60%, which leaves K1's and K0's reviewer room. If a K run's review fails from a limit all the same, that run is reported as such.

**Arm K2.** If the gate recommends a change and the loop adopts it, `stage3_adopted.py` takes the head through `leave_shadow` and writes its reviewer text to `harness/K2.json`, word for word, as a fourth step of K1's reviewer prompt. Nothing about K2 is written by a person or an agent. K2 is otherwise K1. A change that is no reviewer text, such as a retry, cannot reach K1's reviewer: that is reported and K2 is not made. If nothing is adopted, stage 3's result is that nothing was adopted, and no K2 run is made.

**The runs and the rule.** After stage 2's stream ends, K2 runs twice on each bug stage 2 finished, in the frozen order, one stream, and `compare.py --trained K2` judges it against K1, its parent, and against A and T, under the same frozen rule, on both scores. It is reported as recommended and tested, never as promoted. Stage 2's bugs were not seen by anything that made K2, so that is the unseen score. The tuned score sits beside it: the reviewer's agreement on the tuning and selection cases, and K2 on stage 1's bugs if money is left, with `compare.py` giving that section no verdict.

**Money.** The limit is $180 and there is no new credit. Stage 2 stops at $170. K2's runs stop before a run once the study has spent $178, since K1's dearest run so far was $0.60. K1 has cost about $0.11 a run on stage 2, so 40 runs are about $5 by my estimate. Stage 3 must end before the credits expire on 2026-11-04. If it cannot, it is reported as not run or as part run, with the bugs that finished stated.

**What this cannot show.** The replay measures reading and not fixing, and K2 is a reviewer that also writes tests. A prompt tuned on one narrow reading is carried to a wider one, which is a hypothesis. The held-out split rests on five bugs and the gate counts cases, so a recommendation is weaker than its p value says, which is why the rule on stage 2's bugs is what counts. Codex judged and tuned on Codex's own reading, so nothing here says the change would help another reviewer.

## 2026-11-02 (evening, later): how many passes stage 2's amendments get, before any amended score of theirs is read

Stage 1 set no limit on the audit's passes, and its hardest bug took three. Stage 2's seven now have a bound, the review loop's own: at most three revisions and four audits. The first audit kept `botocore-3835` and sent six back; after the first revision the second kept `charset-normalizer-715` and `setuptools-5352` and sent four back. What the last audit still sends back stays as the reviewer left it, is reported with the audit's counts, and its amended score is shown beside the maintainers' score, as every amended score is. The reviewer's rule includes the plain-code control: a diff that leaves no test failing on the parent and passing with the fix is refused, so a bug may keep the maintainers' tests unchanged and say why. Nobody who reads a stage 2 score has read these diffs, and only counts reach me.

**How the audits ended (counts only).** Four audits and three revisions, as bound above. Kept: `botocore-3835` (first audit), `charset-normalizer-715` with no diff and `setuptools-5352` (second), `python-dotenv-711` (third), `setuptools-5362` and `pluggy-799` (fourth). Contested at the end: `pyjwt-1216`, whose last audit counted one change over-relaxed, one assertion still pinned and a shallow fix able to pass, while the reviewer rejected those two findings in each of the last three rounds on the plain-code control. Its amended score is reported with this note, and the maintainers' score beside it. The controls run after the stream, and only their counts are read.

## 2026-11-02 (night): stage 3's tick ended, and nothing was adopted

The loop's one tick ran from 16:32 to 21:33 on Codex's sign-in, with no API credit. It measured the reviewer on the 77 tuning cases (50 right), named the constraint `false_alarm` (23 false alarms against 4 accepted defects in the failure records), offered 5 candidates of which 4 were new, measured 4, kept 1, raced 4, and took a champion to the held-out split. There it lost to its parent, K1's reading: 14 wins, 22 losses, p 0.93 against the 0.01 it needed, and the gate refused it with `G6_E_META_NO_GAIN`. So stage 3's result is the one this plan named in advance: nothing adopted, and no K2 to run. None of the 254 per-case records carries an error, so no case was lost to a provider's failure. The $8 kept for K2 stays unspent. The loop's folder is in the job's scratch space until it is archived. The scoring of whether the proposers reached the two hypotheses kept out of their prompts (running the issue's own scenario, reading neighbouring code) waits for a read of the four candidates.

## 2026-11-03: stage 2's result

Every run had ended at 10:04, all 20 bugs in the frozen order, and stage 3 had ended with no K2 to run. No score was read before then. The amendments' controls ran first, with only their counts read: all six diffs passed, each control on the diff's own bytes, with 1 to 68 tests failing on the parent and passing with the fix. `charset-normalizer-715` keeps the maintainers' tests, as its reviewer left it.

**The rule.** Neither primary meets it, on either score, so uplift over Claude Code alone on bugs from repositories never used in training is not shown. Neither clears 0.025, and the 95% target is not met. K1's best is 85.0% and M9's 72.5%, both on the amended tests.

| Runs passed | A | T | R (report only) | M9 | K1 | K0 (exploratory) |
|---|---:|---:|---:|---:|---:|---:|
| Twenty new-repository bugs, 40 runs, maintainers' tests | 23 | 24 | 26 | 21 | 26 | 24 |
| Twenty new-repository bugs, 40 runs, amended tests | 31 | 31 | 34 | 29 | 34 | 32 |
| The thirteen determined bugs, 26 runs, both scores | 21 | 22 | 24 | 19 | 24 | 22 |
| Stage 1, eleven fresh bugs, 22 runs, maintainers' tests | 10 | 9 | 9 | 9 | 9 | not run |
| Stage 1, eleven fresh bugs, 22 runs, amended tests | 13 | 12 | 13 | 16 | 15 | not run |
| Training, tuned, 14 runs, maintainers' tests | 9 | not run | not all run | 12 (L9) | 12 | not run |
| Training, tuned, 14 runs, amended tests | 11 | not run | not all run | 14 (L9) | 14 | not run |

| Pairs that differ, stage 2 | Against A, maintainers | Against A, amended | Against T, maintainers | Against T, amended |
|---|---|---|---|---|
| M9 | 2-4, p 0.891 | 2-4, p 0.891 | 2-5, p 0.938 | 3-5, p 0.855 |
| K1 | 3-0, p 0.125 | 3-0, p 0.125 | 2-0, p 0.250 | 3-0, p 0.125 |

The 95% interval by bug on the amended tests against A is -20% to +10% for M9 and +0% to +20% for K1.

**What the counts show.** These are counts by bug and arm. No fix, test, run or reply of these bugs was read for them.

- **Most bugs cannot separate the arms, again.** On the maintainers' tests 15 of the 20 bugs gave A, T, R, M9 and K1 the same score: eight passed in every one of those arms and seven failed in every one. K1 and A differ on two bugs only, `urllib3-5254` (A 1 of 2, K1 2) and `s3transfer-423` (A 0, K1 2), so 3 of 40 pairs differ, and the rule needs at least 5.
- **The amendments moved every arm alike.** On `setuptools-5352`, `pluggy-799`, `botocore-3835` and `pyjwt-1216` every arm failed on the maintainers' tests and passed on the amended ones, but for one T run on `setuptools-5352`. `setuptools-5362` failed in every arm on both, and `python-dotenv-711` passed in every arm on both. So the amended score adds 7 or 8 runs to every arm, and unlike stage 1 the loops gain nothing there. `pyjwt-1216`'s amendment stayed contested (the entry above); since it moves every arm alike, no comparison turns on it.
- **M9 lost runs A won:** `anyio-1424` (A 2, M9 0), `anyio-1354` (A 2, M9 1) and `urllib3-5254` (A 1, M9 0), and won `s3transfer-423` (A 0, M9 2).
- **One bug where a review helped.** On `s3transfer-423`, A, T and the screening arm S failed all 7 runs, and R, M9, K1 and K0 passed all 8. K0 is K1's loop without the issue's discussion, so the discussion is not what made the difference there. This rests on one bug, and its cause has not been read, so it is a hypothesis.
- **K0, report only.** Stage 1 suggested the issue's discussion cost runs. Here K0 is level with A (24 against 23, 3-2) and two runs behind K1, which reads the discussion; its only bug apart from every other arm is `urllib3-5347`, which every other arm passed and K0 failed twice. So stage 2 does not bear that hypothesis out. K0 was chosen after reading stage 1, so this is not a finding either way.
- **"Clean" was right more often than on stage 1.** K1 said clean on 33 runs, right on 23 by the maintainers' tests and on 30 by the amended ones. M9's "clean" was right on 12 of 17 and 15 of 17, R's on 24 of 38 and 32 of 38, and A's "done" on 23 and 31 of 40.
- **How K1's runs ended**, as the entry of 2026-11-01 (evening) asks: 33 with the last review finding nothing, 6 with the repairs run out and a defect open (3 of them right on both scores), and 1 with the agent leaving the code as it was. None ended on a defect whose test would not run.
- **Training did not carry, though less steeply than on stage 1.** K1 went from 12 of 14 on training to 26 of 40 here, and A from 9 of 14 to 23 of 40, so K1's uplift over A fell from +21% to +7.5% on the maintainers' tests; on stage 1's fresh bugs it was -5%. The bugs differ, so this is a rough reading.

**What it does not show.** That K1 has no effect on unseen repositories. Every one of the three pairs that differ favours K1, and its interval on the amended tests runs from no gain to +20%. The rule was expected to fire about two times in three at 20 bugs if the training effect were real, but it needs bugs that tell the arms apart, and 18 of these 20 gave K1 and A the same score. A larger unseen set is needed to decide it, and by the entry of 2026-11-02 (morning) it has to be drawn fresh.

**Verdicts read from their records.** `run.py` prints at most 8,000 characters, which cut the verdict off for all 15 runs of `setuptools-5362` and for K1's second run of `setuptools-5352`. Each row was written from that check's saved record, as on stage 1 (entry of 2026-11-02). No run was repeated and no outcome was read to do it. All 16 failed, on both scores.

**Spend.** The study stands at $160.27 of $180: $125.62 by the runner, up $42.88 since stage 1's tally, and $34.65 for Fable's reviews. Stage 3's tick ran on Codex's sign-in and spent no API credit. What the one killed run's container spent after its kill is unmeasured.

**The three stages, together.** Stage 1, unseen bugs in the training repositories: not shown. Stage 2, unseen bugs in repositories never used in training: not shown, with K1 ahead of A on every pair that differs. Stage 3, a change G6 proposed itself from its own failure records: the loop proposed and raced candidates with no change written in session, and the gate adopted nothing, so there was no K2 to measure. On no stage is the 95% target met.

## 2026-11-03 (afternoon): two replays on recorded patches, before any of their runs

The owner approved, after the three stages, a reviewer that reproduces the issue before it judges, measured against the
truth on patches already recorded, and a check that the product's reviewer behaves as the measured one. Both are replays:
no agent runs, no API credit, the owner's Codex sign-in only, and they change no score, verdict or rule above.

**Patches.** The distinct final patches of every arm on stage 1's fresh and held-out bugs and stage 2's bugs, which are
development from the 2026-11-02 (morning) entry on, in the order of their digests. Truth is each patch's own verdict on the
amended tests, and the maintainers' verdict is reported beside it. The 79 training patches are reported apart, since they
shaped the reviewer. The reviewer reads what the final review reads (the issue with its discussion, the parent tree from
the agent's image, the patch) and is told nothing of the arm or the verdict. No maintainers' test or fix reaches it.

**Replay 1: the reviewer reproduces first.** The hypothesis, from the blind labels (12 of the 19 failures both raters agreed
on were within reach of running the issue's own example or reading the surrounding code): a review that first writes a test
of the issue's own scenario, which G6 runs at the parent, where it must fail, and on the patch, and that is then given those
results and reads the patched code's callers and neighbours before it judges, agrees with the truth more often than the
final review as frozen. The reviewer still runs no code itself; G6 runs its tests in the bug's own container, as the final
review does. The baseline is the final review as frozen, on the same patches: stage 1's from its run now going, stage 2's
from the same script, run first.
- **Measures:** per patch, right when the review reproduces a defect on a wrong patch or finds none on a right one;
  false alarms on right patches and misses on wrong ones kept apart, each with an exact bound; paired by patch against
  the baseline, and by bug.
- **Prediction, written now:** on stage 1 and stage 2 together, agreement with the truth on the amended tests rises by
  at least 10 points, with false alarms on right patches up by no more than 5.
- **Kill rule:** agreement up by less than 5 points, or false alarms up by more than misses fall: the hypothesis is
  refuted at this rung, and what follows is a test of the reading step alone, or a change of frame.
- **What it changes:** if it holds, the reproducing reviewer is the review loop's reviewer for the next study, which is
  sized before it runs by whether it can meet the rule. If it fails, the next study does not use it.
- **The test:** an exact one-sided sign test on the patches where the two reviews differ in being right, p below 0.05.

**Replay 2: the product's reviewer against the measured one.** The product sends the same prompt as the review loop's
reviewer, byte for byte, but no tools and only the files the patch touches, cut at 120,000 characters; the measured
reviewer had the whole parent tree and tools to read it. On the same patches, the product's pack, built by the product's own
code from the saved patch and the parent files, goes to Codex with no tools.
- **Measures:** how often its verdict matches the final review's on the same patch, and its agreement with the truth.
- **Prediction:** the two verdicts match on at least 85% of patches.
- **What it changes:** under that, the product does not carry the measured reviewer's behaviour, and its reviewer is
  given the whole tree through a caller outside core before any claim rests on the study.

**Order and stops.** Both start after stage 1's final review ends, so neither competes with it for the sign-in. Each runs
first on a pilot of the first 40 patches by digest. After each block of 40 a registered futility stop applies: it stops
when even every remaining patch going its way could not reach its prediction, and only then; it never stops early for
success. Nothing is dropped or rerun for a better result. Counts are read on unseen bugs only, as before. A Codex limit
pauses a replay; it resumes where it stopped, and a review that failed on a limit is not counted as a review.

## 2026-11-03 (afternoon, later): where stage 1's final review stands

Stage 1's final review, which is report only and changes no score, verdict or rule, was stopped by the machine's
low-memory guard with 165 of about 185 patches reviewed. Each review is saved as it ends and a rerun skips the saved ones,
so it resumes where it stopped; it is restarted only when the owner asks, and its counts are reported then, whole. The three
stages' results above stand without it: stage 1, uplift not shown; stage 2, uplift not shown, the review loop 3-0 ahead of
Claude Code alone (p 0.125) on both scores; stage 3, nothing adopted. The 95% target is met in none.

## 2026-11-03 (evening): stage 1's final review, whole

Restarted at the owner's word, it ended with every patch of stage 1's fresh and held-out bugs reviewed. Report only: it
changes no score, verdict or rule above. `compare.py`'s tables, pooled once per arm over A, T, R, M9 and K1, counted by run:
whether the run's patch was right, given what the final review said of it.

| Stage 1 | Review reproduced a defect | Review found nothing |
|---|---|---|
| 11 fresh bugs, amended tests | right 57 of 86 (66%) | right 12 of 24 (50%) |
| 11 fresh bugs, maintainers' tests | right 37 of 86 (43%) | right 9 of 24 (38%) |
| 3 held-out bugs, both scores | right 18 of 30 (60%) | right 3 of 35 (9%) |

Four more held-out runs drew a defect with no test behind it, all on wrong patches. On unseen bugs the review's word does not
part right patches from wrong ones: on the fresh bugs' amended tests a reproduced defect fell on a right patch more often than
"nothing found" did, and on the held-out bugs "nothing found" came before a wrong patch 32 times in 35. This is the baseline
the replay of 2026-11-03 (afternoon) is measured against, and it agrees with the first-review calibration read after stage 1.

## 2026-11-03 (night): replay 2's effort, and failed reviews in both replays, before any of their runs

**Effort.** Replay 2 calls Codex as the product's reviewer does, through the product's caller, which leaves Codex at its
default reasoning effort; the final review it is matched against ran Codex at high effort. So replay 2 measures the product's
reviewer as it ships, its pack and its effort together. A match under 85% says, as registered, that the product does not
carry the measured reviewer's behaviour; it does not by itself say the pack is the cause. If that matters for what follows,
a follow-up gives the product's pack to Codex at high effort, so the two are told apart, and it is registered before it runs.
Replay 1 uses the final review's own call, at high effort, so its comparison has no such difference.

**Failed reviews.** In both replays a review that failed for any reason is no review: it is saved as failed, counted nowhere
and made again on a resumed run. That covers a provider limit, a reviewer process that ends in failure whatever it printed,
a reply that does not quote its pack or gives no verdict, a container, git or patch that fails, and a saved record that
cannot be read. Two reviewer failures in a row pause the replay, as a Codex limit does; a container, git or patch failure is
not the reviewer's and does not count toward the pause. The futility stop is read before each review, so a resumed replay
honours a stop its saved reviews already make, and a patch whose review failed is still to be reviewed while it waits. It is
read on stage 1 and 2 together, where the predictions are; the training patches, reported apart, have no prediction and so no
stop.

## 2026-11-04 (early): replay 1's pilot, what the saved reviews show, and a test of the issue's rule

Written after reading replay 1's pilot and the saved reviews below, and before any run that follows.

**Replay 1's pilot.** It ran from claude/replays-followup 4fed39c on the first 40 patches by digest of stage 1 and 2's bugs. It
used the Codex sign-in, took 107 minutes, and had no failed review. On the 40 paired patches the frozen review and the
reproducing review were right as often: 24 and 24 on the amended tests (one patch each way), and 25 and 26 on the
maintainers'. The prediction was a rise of 10 points. The registered futility stop does not fire, as it stops only when
success is arithmetically out of reach, and 380 patches remain.

**Replay 1 pauses after its pilot.** This is a rule written now, after the pilot. Going on would spend about 17 more
hours of the sign-in on an effect that 38 equal pairs in 40 make unlikely. A stop for futility can only lose a true
effect; it cannot make one. The pilot is reported as a pilot, with its intervals. The hypothesis is not refuted at
n = 40, and the run can resume where it stopped.

**Replay 2 is held.** It measures whether the product's reviewer matches the frozen one. The frozen one's verdict barely
moves the odds (below), so a match or a mismatch would decide nothing now.

**What the saved reviews show** (counts read with plain code from the saved records and the truth, no model call):
- On stage 1 and 2's 420 patches, amended tests, the frozen review agrees with the truth on 52.6%. It raises a false
  alarm on 49.5% of right patches and misses 43.3% of wrong ones.
- A patch the review said "reproduced" is wrong 38% of the time. After "none" it is 28%, and the base rate is 34%.
- The review's test passing on the parent and failing on the patch (introduced by the patch) carries a signal on the
  maintainers' tests: 66% wrong, against 53%. It carries none on the amended tests (39% against 37%), and the pattern
  reverses on the training bugs.
- In the pilot, step 1's test of the issue's own example passes on the patch for 33 of the 34 patches whose bug it
  reproduced, wrong patches included (11 on the amended tests, 16 on the maintainers'). The agents' wrong patches fix
  the issue's example. They fail on the behaviour around it.

**Replay 3, registered now: a test of the issue's rule, written once per bug.**
- **What it is.** A reviewer is shown the issue and the parent, never a patch. It writes one test of the rule the issue
  states: the example, its variants a maintainer would also expect to hold, and the other paths that share the code.
  Each case is its own test. G6 runs the test on the parent and on each distinct patch of that bug.
- **A patch's verdict.** A test that fails no case on the parent reproduces nothing: the bug has no verdict, and its
  patches count as "none". Otherwise a patch is "wrong" when any case fails on it.
- **Cost.** One call per bug, not per patch.
- **Development.** On the training bugs only, with at most three versions of the instruction, chosen by agreement on
  the training patches. The instruction is then frozen and its digest written here before stage 1 and 2 run. It may
  state the rule in general words only: no bug, file or issue of any stage.
- **Measures, as replay 1's.** Agreement with the truth, false alarms on right patches and misses on wrong ones, on both
  scores. It is paired with the frozen review by patch and by bug.
- **Prediction, on stage 1 and 2 together, amended tests:** agreement at least 10 points above the frozen review's
  52.6%, with false alarms on right patches under 25%.
- **Kill rule.** Agreement up by less than 5 points, or false alarms not under 40%: then a test of the rule is no
  better a judge than the review, and the next frame is the maintainers' intent.
- **What it changes.** If it holds, the review loop's reviewer gains this step for the next study, sized before it runs.
  Nothing here is a score of G6 against Claude Code.

## 2026-11-04 (early, later): replay 3's measure, before its second version and before any scored run

Written after replay 3's first development run (training bugs only, instruction 838d8da877502eee), and before any other run.

**The development run.** It ran from claude/replay3 7c9609b on the 7 training bugs and 129 patches, in 30 minutes, with
7 reviewer calls and no failure. Every patch of a bug got the same verdict.
- On the maintainers' tests: false alarms 0 of 80, misses 30 of 49, agreement 76.7%. All 19 patches it flagged were
  wrong.
- On the amended tests: false alarms 17 of 97 (all on one bug, where the two scores differ), misses 30 of 32,
  agreement 63.6%.
- Paired with the frozen review on its 79 patches: right 53 and 53, with 17 patches each way.

**A flaw in the registered measure, found now.** On stage 1 and 2, 279 of 420 patches are right on the amended tests. A
reviewer that always says "none" would agree on 66.4%, with no false alarm. That meets replay 3's prediction, at least
62.6% and false alarms under 25%, while finding nothing. The frozen review's 52.6% is below that constant. Agreement
rewards abstaining wherever most patches are right. Replay 1's prediction had the same weakness, though its pilot did
not turn on it.

**Replay 3's measure, prediction and kill rule, replacing the ones above:**
- **Measure.** Balanced accuracy: the mean of the share of wrong patches caught and the share of right patches cleared,
  on each score. A reviewer that always says the same thing scores 50%. Agreement, false alarms and misses are still
  reported beside it.
- **Prediction, on stage 1 and 2 together, amended tests:** balanced accuracy at least 10 points above the frozen
  review's, with false alarms on right patches under 25%. The maintainers' tests are reported beside it.
- **Kill rule.** Balanced accuracy up by less than 5 points, or false alarms not under 40%.
- **Choosing the instruction.** By balanced accuracy on the training patches, not agreement. Version 2 aims at what
  version 1 missed, in general words only: the dimensions the repository's own tests of the same feature cover.

## 2026-11-04 (early, frozen): replay 3's instruction, before its scored run

**Version 2** (fd359f6a87402b72, claude/replay3 97beb23) asked for the dimensions the repository's own tests of the
feature cover, and 6 to 20 cases. On the 7 training bugs it gave the same verdict as version 1 on every one of the 129
patches. Balanced accuracy ties: 44.4% on the amended tests and 69.4% on the maintainers'. The frozen review, which was
developed on these bugs, scores 78.3% and 83.3% on its 79.

**Frozen.** On a tie the earlier and simpler instruction stays: **838d8da877502eee** (claude/replay3 69ce2e1). The
scored run on stage 1 and 2's 34 bugs uses it and no other, after one Codex review of the branch. Any repair from that
review may touch the runner but not the instruction.

**Read now, before the scored run.** Two instructions in different words, and two independent reviewer calls a bug, gave
one verdict a bug, the same both times. A test of the issue's rule passes or fails a bug's patches together. So on
unseen bugs it can at most tell bugs whose patches are mostly wrong from bugs whose patches are mostly right. That is
weaker than a judge of each patch. Replay 3's prediction is unchanged and is read as registered.

## 2026-11-04 (morning): replay 3's runner, before its scored run

Replay 3's code went through the Codex review loop. There were four reviews, run under the rules in
`validation/own_code/RCA.md`: the first review had no cap and walked every class in the ledger's list. They found 10, 3, 4 and
3 defects, all reproduced. Three repairs followed (claude/replay3 286988a, b639fb3 and 370ceeb), and the last review's
count-changing finding became its own change (claude/replay3-followup 5c2eccb). The loop ended at its limit, never clean.

The scored run uses **claude/replay3-followup 5c2eccb** and instruction **838d8da877502eee** on the 34 bugs of stage 1 and 2,
from a clean tree.

Open, each counted on the archive and touching no count:
- a patch on two bugs across separate task selections (0 of the 549 distinct patches is on two bugs);
- a patch printing the report marker to hide a failure from the study's shared parser `run.read_probes` (0 of 549 contain
  it);
- two overlapping runs within one process (one run holds the folder);
- a container left after a failed removal;
- the junit file trusted after the test process exits (0 patches add junit or atexit).
The last three are in the shared runner, for a change of their own.

## 2026-11-04 (morning): replay 3's result

The scored run used claude/replay3-followup 5c2eccb and instruction 838d8da877502eee on the 34 bugs of stage 1 and 2. It
covered all 420 distinct patches, none failed, and one restart resumed after a GitHub 503 reading an issue thread. Counts
are from `replay_compare.py` and plain code.

**Amended tests: the registered score.**
- **Replay 3:** balanced accuracy 63.7%, false alarms 48 of 279 (17.2%), misses 78 of 141 (55.3%), agreement 70.0%.
- **Frozen review:** balanced accuracy 53.6%, false alarms 49.5%, misses 43.3%, agreement 52.6%.
- **Paired by patch:** only replay 3 right on 115, only the frozen review on 42.
- **By bug:** better 19, worse 8, same 7, one-sided sign test p 0.026.

**The prediction holds on its registered measure, by 0.1 point.** It needed 63.6% or more, with false alarms under 25%.

**Maintainers' tests, reported beside it.**
- Replay 3 has balanced accuracy 52.5%, false alarms 23.7% and misses 71.2%.
- The frozen review has 50.8%, 51.0% and 47.3%.
- By bug: better 13, worse 14, same 7, p 0.65. So no gain on this score.

**How to read it.**
- **Where the gain comes from.** Almost all of it is fewer false alarms, a quieter reviewer, while misses rose.
- **The unit.** Verdicts cluster by bug: one test a bug, 23 bugs all "none", 3 all "reproduced", and 8 with patches
  told apart. So the bug is the honest unit, and a sign test by patch would overstate the result.
- **The two truths disagree.** It is a gain against what the issues ask for (amended), not against the maintainers'
  hidden tests.
- **The scale.** A balanced accuracy of 63.7% is far from the 95% goal.
- **What it is not.** Nothing here is a score of G6 against Claude Code.

**What it changes, as registered.** The review loop's reviewer gains this step for the next study, which is sized before
it runs. The maintainers' result is carried into that sizing: the step is predicted to cut false alarms, not misses.

## 2026-11-04 (late morning): where the ceiling is, and choosing among a bug's candidates

These counts were read on stage 1 and 2's 34 bugs before anything below them was computed. They cover every finished run of
the nine arms and come from plain code (`compare.saved`, the check and recheck records), with no model and no container.

**A bug's runs (maintainers' tests / amended tests).**
- Bugs where every run is right: 16 / 20; every run wrong: 11 / 4; mixed: 7 / 10.
- A perfect chooser among the recorded runs reaches 23 of 34 (67.6%) / 30 of 34 (88.2%).
- Arm A's mean share right per bug is 52.9% / 69.1%.

**Inside the mixed bugs**, by distinct patch, balanced accuracy is:
- the frozen review, 0.36 / 0.40, worse than chance;
- replay 3's rule test, 0.54 / 0.64.

**Which tests a wrong run fails** (wrong runs: 244 / 162):
- new behaviour only (FAIL_TO_PASS): 201 / 132;
- existing behaviour only (PASS_TO_PASS): 38 / 29;
- both: 5 / 1.

Wrong runs that break existing tests sit in 4 of 18 bugs / 3 of 14.

**What this says.**
- **No judge can reach 95% on these candidates.** Between 4 bugs (amended) and 11 (maintainers') have no right patch among
  roughly 16 runs of nine arms.
- **So the goal has two parts.** Generation: a right patch exists. Selection: the right patch is the one returned.
- **What selection can buy.** Uplift from choosing among these candidates is bounded by the gap between arm A and a
  perfect chooser: 52.9% to 67.6% / 69.1% to 88.2%.
- **Regression tests help only a little.** Running the existing tests, the Agentless-style filter, reaches only the few
  bugs whose wrong runs break existing tests.
- **The main way a patch fails** is reading the new behaviour differently from the maintainers.

**Lean experiment, registered here and computed after this commit: choosing one of a bug's recorded runs.**
- **Inputs.** Plain code only: no model, no container, no spend.
- **Pool.** Every finished run of the bug with a non-empty final patch and a truth on the score, from any arm.
- **R0, random.** The pool's expected share right. This is the baseline.
- **R1, agreement.**
  - Runs are grouped by their normalised source change: files outside tests, added and removed lines, with whitespace,
    blank lines and comment lines dropped.
  - A run of the largest group is returned. Ties are averaged.
- **R2, rule test then random.** Runs whose patch replay 3's rule test (instruction 838d8da877502eee) passed, meaning it
  said "none". If no run passed, all runs.
- **R3, rule test then agreement.** R1 applied within R2's set.
- **Measure.**
  - The share of bugs whose returned run is right, as an expected value over ties, on each score.
  - Paired by bug against R0, with a sign test.
  - Beside it, arm A's share right per bug.
- **Prediction.**
  - R1 is at least 3 points above R0 on the amended tests.
  - R3 is at least 5 points above R0, with gains on more mixed bugs than losses.
  - R2's separation inside the mixed bugs is already counted above, so R2 is reported but is not a new test.
- **Kill rule.** If R1 and R3 are both within 2 points of R0 on both scores, agreement by patch text carries no signal.
  The next step is then agreement by behaviour: the candidates run on shared inputs, the `differential.v1` design.
- **Standing.**
  - These bugs have been scored, so this is development.
  - Any rule it favours is scored on bugs not yet used before it is claimed.
  - The pool mixes G6's arms with arm A, so it is not "Claude Code run N times". A gain here says that selection carries
    signal, not that a harness beats Claude Code.

## 2026-11-04 (midday): choosing among candidates fails, and why

The lean experiment of 61efa3c was computed by `choose_among.py`, plain code, on all 34 bugs of stage 1 and 2.

**Result.**

| Rule | Amended tests | Maintainers' tests | Amended, by bug against R0 |
|---|---|---|---|
| R0, random | 71.3% | 52.9% | — |
| R1, agreement | 67.5% | 50.6% | better 1, worse 6 |
| R2, rule test then random | 73.7% | 54.6% | better 3, worse 0, p 0.125 |
| R3, rule test then agreement | 71.2% | 53.5% | better 4, worse 5 |
| Arm A | 69.1% | 52.9% | |
| A perfect chooser | 88.2% | 67.6% | |

**Both predictions fail.** R1 is 3.8 points below R0, not 3 above, and R3 is level with R0, not 5 above. The kill rule as
written does not fire, since R1 is more than 2 points away. But it is away in the wrong direction, so agreement by patch
text carries negative signal here, and the next step it names is reconsidered below.

**Why: the wrong runs share one misreading, and the other family's reviewer shares it too.** These counts were made after
the result. They are exploratory and were not registered. In the 10 mixed bugs on the amended tests, the commonest
wrong change holds on average 61% of a bug's wrong runs. Flagged by replay 3's rule test, by distinct patch:
- right patches: 17 of 59 (29%);
- the commonest wrong change: 12 of 37 (32%);
- other wrong changes: 40 of 55 (73%).

The frozen review flags 66%, 37% and 56% of the same groups. On the maintainers' tests the pattern holds, and on the
11 bugs where every run is wrong the rule test flags 0 of 17 patches of the commonest wrong change.

**What it means.**
- **The reviewers catch the wrong patches that are wrong in their own way, and miss the wrong patch that many runs make.**
  That patch is the natural reading of the issue to both model families, so it is no Sonnet habit.
- **Why voting backfires.** Voting among candidates amplifies that reading.
- **The Swiss cheese holes line up.** A reviewer from another family is no independent layer against the commonest
  error. A layer counts as independent by the errors it misses, measured, and not by the model family it comes from.
- **What the next step must bring.** It cannot be another reading of the issue by a model: it needs evidence the issue's
  natural reading does not give.
- **Behaviour agreement is predicted to fail the same way.** The `differential.v1` step named by the kill rule would
  vote on the same shared reading, so it is set aside until the labels below say which evidence separates the readings.

## 2026-11-04 (midday): contrastive blind labels, registered before they run

**Purpose.** To find, for the commonest wrong change, what separates it from a right patch and which evidence could have
shown it, as classes.

**The protocol.** It is the 2026-11-02 one:
- the auditors run no task, review no patch and propose nothing;
- they may read the maintainers' tests and fix, and return labels from closed lists and no text;
- only the labels are kept, as counts;
- two raters from different families label apart: Codex on the owner's sign-in, and Claude Opus 5.5 through `claude -p`
  on the owner's sign-in. No API credit is spent.

**Cases.** One case for each of the 14 bugs that are mixed or all wrong on the amended tests:
- the commonest wrong change, as one representative patch;
- beside it, the commonest right change, or the maintainers' fix on a bug where every run is wrong;
- for an amended bug, the amendment's diff, since the amended tests are the truth here.

Stage 2's bugs can now be read, as stage 3's runs have ended.

**Labels.**
- **What the commonest wrong change does differently:** `example_only`, `missed_stated_case`, `symptom_not_cause`,
  `output_detail`, `api_shape`, `over_reach`, `other`.
- **The cheapest evidence, without the maintainers' tests or fix, that tells the two apart:** `closer_issue_reading`,
  `issue_discussion`, `issue_example`, `variant_of_issue_example`, `existing_tests`, `docs_or_docstrings`,
  `analogous_code`, `callers`, `nothing_available`.
- **Why many independent attempts make the same wrong change:** `example_shaped`, `nearest_code`, `literal_reading`,
  `convention_not_visible`, `other`.

**What each outcome decides.** Read on the labels both raters agree on, with agreement reported.
- **If evidence from running inputs leads, on half or more:** `variant_of_issue_example` or `issue_example`. The next
  step is executed, not prompted. G6 composes variants of the issue's example along the rule's other inputs and paths,
  then runs them on the parent and on each candidate, with no model judging the result.
- **If evidence from the repository leads:** `analogous_code`, `callers`, `existing_tests` or `docs_or_docstrings`. The
  next step gathers that evidence for the author before it edits, through G6's existing context and grounding
  components, and the reviewer gets the same.
- **If `nothing_available` leads:** these bugs turn on intent the issue does not give. The target excludes them, and is
  restated with that number.

Whichever step it is, it is scored on bugs not yet used.

## 2026-11-04 (late morning): the contrastive labels' result, and replay 4 registered before it runs

**The labels.** Both raters labelled all 14 bugs, Codex and Claude Opus 5.5 on the owner's sign-ins, with no API credit
spent. Only labels were kept, counted by `contrast_labels.py` in plain code.

**What the commonest wrong change does differently** (agreed on 11 of 14, kappa 0.64):
- `example_only`: 7;
- `missed_stated_case`: 2;
- `output_detail`: 1;
- `over_reach`: 1.

**Why many runs make it** (agreed on 11, kappa 0.50):
- `example_shaped`: 9;
- `literal_reading`: 1;
- `convention_not_visible`: 1.

**The cheapest evidence that tells it apart** (agreed on 8, kappa 0.42):
- `variant_of_issue_example`: 4;
- `issue_example`: 1;
- `existing_tests`: 1;
- `analogous_code`: 1;
- `closer_issue_reading`: 1.

Each rater alone put running inputs first: 7 of 14 for Claude and 10 of 14 for Codex. Only Claude gave
`nothing_available`, on 2 bugs.

**The decision, as registered.** Running inputs leads, at 5 of the 8 agreed labels. So the next step is executed, not
prompted.

**Replay 4: symptom variants.**
- **The machinery.** Replay 3's runner is used unchanged:
  - one reviewer call per bug, shown the issue and the parent and never a patch;
  - its pytest file runs on the parent and on each distinct patch;
  - a patch is "reproduced" when a case that fails on the parent also fails with it.
  
  The code is `claude/replay4`: replay 3 takes the instruction as a parameter, and its own instruction and records are
  unchanged.
- **The instruction.** `replay4.SYMPTOM_FIRST`, digest 780e81dac0e14cb1, asks for 6 to 16 cases: the issue's example,
  then variants of it along the rule's other inputs, paths, call forms and options.
- **The one change from replay 3: each case asserts only that the symptom is absent.** The symptom is what the issue
  reports, and any other outcome skips. Replay 3 asserted exact values, and a model writing what a variant should return
  writes the reading the patches share. Here no model writes a right result. The issue gives the symptom, the reviewer
  gives the inputs, and running them on the parent says which variants show it.

**A development check, on the 34 bugs of stage 1 and 2.**
- **Standing.** These bugs are development: their labels shaped this step, and replay 3's results on them have been
  read. So the result is a go or no-go for a fresh test, and never a score.
- **Cost.** One reviewer call per bug on the Codex sign-in, and local containers. No API credit.
- **Measures, amended tests first.**
  - Inside the mixed bugs: the share of distinct patches flagged among right patches, the commonest wrong change, and
    other wrong patches. This is the shared-misreading table of 3812894, so replay 3's figures sit beside it.
  - Over all 34 bugs: balanced accuracy, false alarms and misses.
  - Replay 3 and replay 4 combined: flagged by either.
- **Prediction.**
  - Replay 4 flags the commonest wrong change at least 25 points more often than right patches. Replay 3 was 3 points
    apart.
  - Its balanced accuracy is at least replay 3's 63.7%, with false alarms at or under 25%.
- **Kill rule.** If the gap between the commonest wrong change and right patches is under 10 points, a symptom oracle on
  executed variants does not break the shared reading. The next hypothesis is then the repository's own evidence
  (analogous code and callers), gathered before anyone edits.
- **If it passes.** Replay 4 is scored on bugs not yet used, against the frozen review and replay 3, under the
  balanced-accuracy amendment. Those bugs and their runs are drawn and registered first.

## 2026-11-04 (late morning): stage 4's bugs, the rule before the draw

**Purpose.** Unseen bugs for the next scored step: replay 4 if its development check passes, the next hypothesis if not.
Arm A's runs on them stand for Claude Code alone. Every bug of stages 1 and 2 is now development, so the measurement
bottleneck is fresh bugs.

**Source.** The 2026-10-31 screen, `selection/candidates_unseen.json`, read at 2026-10-31T11:14:52Z with the window
2025-12-01 to 2026-10-29. The draw takes the repositories it kept at PyPI ranks 45 to 100, in rank order. None of them
was used in training, stage 2 or the development pool, and the screen was made before any of today's work.

**The rule: stage 2's, unchanged.**
- Each repository gives at most its two newest candidates judged to report a bug with a clear expected behaviour (T2).
  Security reports are left out.
- A bug that fails `images`, `validate`, `selfcheck` or `freecheck` gives way to the next candidate in order.

**One difference: who judges T2.** A model reader judges T2 on the issue's text alone, applying the rule's words, and
gives a reason for every candidate it looks at, all before any run. Stage 2's T2 was mine. The judgement never sees a
fix, a test or a run.

**Size.** It is set by the pre-spend check once replay 4's development check has ended, from the effect that check
measures, and recorded before any run. It is at most the pool.

**Runs.** Arm A only: Claude Code alone, Sonnet 5.5, the prompt unchanged. The number of repeats is set with the size.
They run on the API remainder, with the stop at $178 of the $180 limit.

**Audit and amendments: as for stage 2.** A blind auditor labels each bug determined by its task or not. A bug that is
not determined gets an amendment written without reading any run.

## 2026-11-04 (midday): replay 4's development check, and stage 4's runs registered before any of them

**Replay 4.** It ran on all 420 patches of stage 1 and 2 with instruction 780e81dac0e14cb1, and none failed. The counts
come from `layers.py` and `replay_compare.py`, plain code.

**Inside the mixed bugs, amended tests: share of distinct patches flagged.**

| Layer | Right | Commonest wrong | Other wrong |
|---|---|---|---|
| Symptom test | 0 of 59 | 8 of 37 (22%) | 11 of 55 (20%) |
| Rule test | 29% | 32% | 73% |
| Frozen review | 66% | 35% | 53% |

**Over all 34 bugs, amended tests.**
- **Symptom test:** false alarms 0 of 279, upper bound 1.1%; misses 80.1%; balanced accuracy 59.9%.
- **By bug, against the frozen review:** better 19, worse 9.
- **Rule test, for comparison:** balanced accuracy 63.7%.

**Maintainers' tests.**
- Symptom test: false alarms 0 of 198 and balanced accuracy 56.3%, the best of the three.
- Rule test: 52.5%. Frozen review: 50.8%.

**The prediction fails.**
- The gap between the commonest wrong change and right patches is 22 points, where 25 were needed.
- Balanced accuracy is 59.9%, where 63.7% was needed.

**The kill rule does not fire,** as it needed a gap under 10 points.

**What it shows.**
- **A precise layer.** A test that asserts only the issue's symptom is absent never flagged a right patch on either
  truth.
- **Independent where the others are not.** It catches the commonest wrong change as often as other wrong patches
  (22% and 20%). Replay 3 is the reverse: it misses the shared reading and catches the patches wrong in their own way
  (73%).
- **Why recall is low.** Most wrong patches remove the symptom on every variant and are wrong in the value they return,
  which a symptom's absence cannot see. So the symptom is a lower bound of correctness, not a test of it.
- **What it means for a repair loop.** Every patch such a layer sends back is wrong, so its repairs can only be asked of
  wrong patches. Replay 3's and the frozen review's false alarms (17% and 49%) send right patches back too.
- **A correction this entry makes.** Review v1 of `claude/stage4` found that the layer table normalised lines by
  dropping all whitespace, string literals included. It now tokenises them. Recomputed with that change, R0 to R3 of
  61efa3c are unchanged.

**Stage 4's runs.**
- **Bugs.** Every drawn bug that passes `images`, `validate`, `selfcheck` and `freecheck`, in draw order.
- **Scoring: no amendments.** The maintainers' tests are the score on all bugs, with the score on the 21 bugs the blind
  audit labelled determined beside it. The audit found all 31 report a bug and 10 not determined, so amendments would
  take rounds that the credit's expiry does not allow.
- **Order.**
  1. Arm A twice on every bug: Claude Code alone, Sonnet 5.5.
  2. Then arm V4 twice on every bug. V4 is arm K's loop, with replay 4's test written from the issue and the parent
     before the agent starts. Its code is `claude/stage4`, and its first review's twelve findings are repaired at
     6fe39a3. V4's runs wait for its second review.
- **Spend.** The API remainder, stopping before a run once the study has spent $178 of the $180 limit.

**Prediction 1, reviewer level, unseen bugs.** Replay 4, unchanged, on arm A's distinct patches of stage 4:
- false alarms at most 5% on the maintainers' tests, with the 95% upper bound under 10%;
- at least 10% of wrong patches flagged.

This is the precise-layer claim, on bugs it was not developed on. Replay 3 runs beside it for comparison.

**Prediction 2, harness level.** V4 against A, by bug, on the maintainers' tests:
- no more bugs worse than better;
- the share right no lower.

At 20 to 30 bugs, G6's own power function (`metaharness.evaluate.power`) puts the chance of meeting the frozen
comparison rule under one half at any effect a 20%-recall layer can give. So this is a measured point on the learning
curve and is not powered to claim uplift. A gain is claimed only if the rule is met.

**Next hypothesis, recorded now.** Recall needs an oracle for the returned value that no model's reading of the issue
supplies. The candidate is relations between outputs (G6's `relation.v1` design):
- the same input through two entry points agrees;
- a round trip holds;
- the same change of input moves the output the same way.

Like replay 4, it is developed on stage 1 and 2 and scored on bugs not yet used.

## 2026-11-04 (afternoon): stage 4's order, changed before any of its runs, and V4's go

**Why the order changes.**
- Preparation runs at the memory floor of 6.5 GB free. After three hours it had passed 2 bugs and refused 2, of 31.
- The API credit is recorded as expiring today.
- Run in the registered order, arm A on every bug first, V4 could end with no bug at all, so prediction 2 could not be
  tested. Cost is not the limit: recorded runs average $0.064 for A and $0.124 for K1, about $12 for both arms twice on
  31 bugs, against $17.73 left to the stop.

**The new order: whole bugs, as stage 2 ran them.**
- Each bug is taken in draw order as soon as preparation passes its four steps. It runs A twice, then V4 twice.
- Once preparation has ended and every prepared bug has run, or the stop is reached, replay 4 and replay 3 run on A's
  patches of those bugs. They use Codex sign-in, not the API.
- Prediction 2 is read on the bugs where all four runs finished; a bug the stop cuts short counts for neither
  prediction. Prediction 1 is read on A's patches of the whole bugs.
- The stop, the scoring and both predictions are unchanged.
- Bugs whose image does not build give way under the rule. So far that is yarl-1852 and yarl-1919.

**V4's go.** The registered condition was its second review. That review, on 6fe39a3, found three, all reproduced.
- **Finding 2, repaired at `claude/stage4` f2fce3b.** The bug's test before the agent now comes out of the run's one
  wall-clock allowance. The first session's deadline had counted from after it.
- **Finding 3, repaired at f2fce3b.** Each check removes the test's folder as root and writes the test alone. A check copies
  the whole folder, so a `conftest.py` left beside the test could have replaced every case.
- **Finding 1 stays open, for its own change.** The layer table's per-line reading can merge two different multiline-string
  changes. On stage 1 and 2's recorded runs, every patch the tokenised change merges, where exact bytes would not, differs
  only in comment, blank or layout lines: 16 patches on amended tests, 28 on maintainers'. None differs at a line the
  tokenizer cannot finish, so no recorded count moves. This is the third time the class has been met, so the repair is
  a whole-file token identity read from the bug's image, not another rule for single lines. V4 does not use the layer
  table.
- **Exact bytes would be wrong in the other direction.** It splits those same comment-only duplicates. It would put the
  symptom test's share of the commonest wrong change at 1 of 21, where the tokenised identity gives 8 of 37. So the replay
  4 entry's "as often as other wrong patches" holds only where comment-only differences count as one change, and that
  is the reading this table intends.
- **A third review** runs on f2fce3b. Any finding is reported against the runs it touches, and no run is redone for a better
  result.
## 2026-11-04 (afternoon): replay 5, relations between outputs, registered before it runs

**What it is.** Replay 3's machinery with instruction fd779fd79374dfe4, at `claude/replay5` c42cb9c.
- One test per bug, written from the issue and the parent alone, then run on the parent and on each distinct patch.
- Each case asserts only a relation the issue or the repository states: two paths agree, a round trip returns its input,
  equivalent inputs agree, or the same change of input moves the output the same way.
- Both sides come from calls to the code. The test writes no expected value, so no model writes what a right result is.
- A patch is "reproduced" when a case that fails on the parent still fails on it.

**Why.**
- Replay 4's symptom test flagged no right patch, but flagged only 28 of 141 wrong ones (19.9%, amended tests). Most wrong
  patches remove the symptom and return a wrong value.
- A relation can see a wrong value without anyone stating the right one.

**Development check.**
- **Bugs:** the 34 of stage 1 and 2, all 420 distinct patches, as replay 4 was checked.
- **Cost:** Codex sign-in, no API credit.
- **When:** after stage 4's runs, since it needs containers and memory is the limit.

**Prediction, amended tests, distinct patches.**
1. False alarms on right patches at most 5%, with the 95% upper bound under 10%.
2. At least 30% of wrong patches flagged.
3. At least 10 wrong patches flagged that the symptom test passed.

The maintainers' tests are reported beside. So is the union of the symptom and relation tests, as the Swiss cheese table
reads two precise layers with different holes.

**Kill rule.** Under 15% of wrong patches flagged, or false alarms over 15%: replay 5 goes no further.

**If it passes,** it is scored unchanged on stage 4's arm A patches, bugs it was not developed on, with the same three
measures.
## 2026-11-04 (afternoon): where 95% can be reached, from recorded runs

Stage 1 and 2's 34 bugs are split by the blind audit's determined label (`compare.DETERMINED`, `DETERMINED_2`). This uses
recorded runs only, from every arm, counts only.

| Bugs | Truth | Some arm right, by bug | Arm A, runs right |
|---|---|---|---|
| Determined (20) | maintainers' or amended | 18 of 20 | 30 of 40 (75%) |
| Not determined (14) | maintainers' | 5 of 14 | 8 of 37 (22%) |
| Not determined (14) | amended | 12 of 14 | 19 of 37 (51%) |

**What it shows.**
- **Undetermined bugs cap every harness.** On the maintainers' tests, across every arm and every repeat, only 5 of the 14
  undetermined bugs ever got a right patch. Neither selection nor repair can reach 95% there: it needs behaviour the issue
  does not state. So the 95% target is read on determined bugs, which for stage 4 are the audit's 21.
- **On determined bugs, Claude Code alone gets 75% of runs right.** A perfect chooser by bug would reach 90%.
- **The 15 points above Claude Code sit in four bugs** where some arm was right and A was not always: s3transfer-423 (A 0
  of 2, any arm 8 of 15), urllib3-5254 (1 of 2, 12 of 15), black-5148 (1 of 2, 2 of 10) and pygments-3158 (0 of 2, 1
  of 15). Selection or repair can only win there.
- **Two determined bugs no arm solved** in 10 runs each: click-3404 and starlette-3189. Reaching 95% needs generation on
  bugs like these, which no reviewer or chooser can supply.

**Next, for the generation part.** A root cause of those two bugs from their recorded wrong patches: what the runs share,
and what the issue and the parent say that the runs did not use. These bugs are development now, under the 2026-11-02 rule,
and the lesson is taken as a class, never as the bug's fix.
## 2026-11-04 (afternoon): why no arm solved two determined bugs

This root cause comes from recorded runs only: the issue, the final patches, the agents' reports, and the check's failing
test names and messages. No model or container was run, and the maintainers' fix was not read.

**click-3404, 10 runs.** None passed, with 10 to 18 maintainers' tests failing.
- 7 runs read the issue one way, and 3 restored the reporter's table literally.
- The discussion states the polarity and the tie-break; the reporter's own table has them swapped.
- The maintainers' tests check the whole precedence rule: every pair of sources, declaration order and value kind. Every
  run fixed the issue's one case and left the ties to the old rule.
- Arm M9's blind readers wrote checks expecting the reporter's table, and M9's second run followed them to a worse patch.

**starlette-3189, 10 runs.** None passed, with 2 failing each.
- Every run made the two classes send what the reference class sends.
- But every run also kept, and adapted to the new context, extra machinery the reference class does not have.
- The maintainers' test fails on exactly that machinery, which the full test client the agents checked with happens to
  satisfy.

**The class both share.** The missed requirement sits in behaviour the patch kept from the old code. The issue names a
reference: the old version's rule, the maintainers' words, a parent class. The runs checked the example against it, never
the behaviour they kept. A test of the whole rule against the reference would have shown it, and no run's checks reached it.

**What it means for the next steps.**
- **Reviewer side.** Replay 5's relations are this test: the reference and the patched path agree on the same input, and
  the same change of input, such as a swapped declaration order, moves the output the same way. These two bugs are among
  replay 5's 34, so its development check reads them directly.
- **Author side, a hypothesis recorded now and not run.** Before finishing, the agent lists the behaviours its patch keeps on
  the touched path and checks each one against the reference the issue names. It needs author runs, which need credit or a
  host session, and both are the owner's to give.
- **Blind readers who disagree.** M9 took its blind readers' checks as the answer. Where the readers disagree with each
  other, or with the maintainers' words in the discussion, the issue should be reported as ambiguous and their checks not
  sent back. That is a rule for any arm that uses blind readers.
## 2026-11-04 (evening): headroom, where a self-checking loop can gain, and stage 4's forecast before its results

**Owner's direction, 2026-11-04 (evening).** Return to the thesis of how agents check themselves:
- show uplift wherever it is structurally possible, on determined bugs;
- have the system itself say where none is possible, why, and what would have to change;
- use subscription credit for further testing;
- stop only once that is established;
- then build the 0.3.0 loop, and then assess readiness for an early alpha.

**Headroom, coded.** `validation/worked_examples/headroom.py` is at `claude/replay5` 6e51039, built test first, and is plain
code over recorded patches and saved reviews. It puts each bug in the first class that holds:

| Class | When it holds |
|---|---|
| undetermined | the audit says the target is not in the issue |
| solved | every distinct patch is right |
| generation | no patch is right |
| separable | a layer flags a wrong patch and never a right one |
| inseparable | otherwise |

It names what must change for each class.

**Stage 1 and 2's 20 determined bugs, maintainers' and amended alike.**
- 15 solved: one run of A is right 97% of the time.
- 2 generation: click-3404 and starlette-3189.
- 2 inseparable: black-5148 and pygments-3158, A right 25%. Every layer that flags their wrong patches flags their right ones
  too.
- 1 separable: s3transfer-423, A right 0%, bound 100%, separated by the final review.

**The classes account for every loop arm's gains and losses on determined bugs.**
- **s3transfer-423 (separable).** Every self-checking arm (K0, K1, R, M9) was right on both runs. Every arm without a
  reviewer (A, S, T) was right on neither.
- **black-5148 (inseparable).** K1, M9 and T lost to A: a reviewer that also flags right patches sends right patches back.
- **Solved bugs.** Ties, except where M9 lost on anyio-1354, anyio-1424 and urllib3-5254. On urllib3-5254, K1's "win" is A's
  first run, which ended in an error with no patch.
- **Reading.** A self-checking loop gains exactly where its check separates right from wrong, and loses where its check
  flags right patches. On these bugs the two cancel: +1 against −1.
- **What must change.** A check that separates more determined bugs and never flags a right one. The symptom test flags no
  right patch, but separates no determined bug; its catches were on undetermined bugs. Replay 5's relations are the next
  candidate, scored on stage 1 and 2 by its development check.

**Stage 4's forecast, registered now.** Two whole bugs have been seen so far, both ties (uvicorn-3236, uvicorn-3199).
- **Headroom is read the same way on stage 4** once replays 4 and 3 have run on its patches: A's and V4's runs as candidates,
  and the symptom, rule and frozen layers where they exist.
- **Forecast 1.** V4 is better than A on no more determined bugs than V4's own test separates, plus run-to-run variance.
  The frozen rule is not met.
- **Forecast 2.** V4 is worse than A on no determined bug through its test. Each V4 run whose final patch is wrong had its
  test hold on that patch or reproduce nothing on the parent, never send back a patch that was right.
- **How 2 is checked.** From V4's records: every repair note followed a check on a patch the test then still failed.
- **Uplift is claimed only if the frozen rule is met,** as before.
## 2026-11-04 (evening): the reviewer improved on recorded data, and what showing the uplift takes

**Root cause of the false alarms on the inseparable bugs.** From recorded reviews only. On black-5148 and pygments-3158 the
reviewers flagged the right patches on cases that pass on the parent and fail on the patch. The reviewer treated as a
requirement either the parent's behaviour beside the fix, or its own extension of the fix beyond what the issue asks.

**The rule proposed from it.** A finding stands only on a case that fails on the parent and still fails on the patch. A case
that passed on the parent and fails on the patch is a note, not a finding.

**Measured on all 420 recorded patches of stage 1 and 2.** No model was called: the saved reviews already hold every case's
result on the parent and on the patch.

| Reviewer, amended tests | False alarms on right | Misses on wrong | Balanced |
|---|---|---|---|
| Final review, frozen | 138 of 279 | 61 of 141 | 53.6% |
| Final review, gated | 89 of 279 | 92 of 141 | 51.4% |
| Rule test, gated | 44 of 279 | 86 of 141 | 61.6% |
| Symptom test, unchanged by the gate | 0 of 279 | 113 of 141 | 59.9% |

**What the gate does to headroom on the 20 determined bugs.**
- separable: 2, s3transfer-423 and black-5148; inseparable: 1, pygments-3158.
- A gated loop keeps the one win, turns the one loss into a win, and never sends a right patch back on those bugs.
- Misses only mean that nothing goes back, which is Claude Code alone.

**Power,** from G6's `metaharness.evaluate.power`, at the frozen rule's p < 0.05, by number of determined bugs:

| Loop | 20 | 50 | 100 | 200 | 300 |
|---|---|---|---|---|---|
| Frozen, as measured (+1 −1) | 0.00 | 0.02 | 0.03 | 0.04 | 0.04 |
| Gated (+2 of 20, discordant 15%) | 0.06 | 0.42 | 0.80 | 0.98 | 1.00 |

**What follows.**
- **The frozen loop cannot show uplift at any size.** On determined bugs its one gain is cancelled by its one loss.
- **Stage 4 cannot decide the gated loop.** It has 21 determined bugs, at power 0.06.
- **To show it,** arm K2 (K1's loop with the gate) runs against A, both twice, on about 100 determined unseen bugs. The
  determined share has been about 60% (20 of 34, 21 of 31), so that is about 160 drawn bugs.

**Cost and time,** from recorded means.
- About $0.36 a bug for both arms twice, so about $58 on API credit, beyond the $17.73 left. The alternative is subscription
  runs, which keep the sign-in out of every container, so the author runs on the host with tests in the bug's container.
- Preparation at the memory floor has run at about 10 minutes a bug, so about 27 hours for 160 bugs.

**The registered prediction, frozen before K2's first run.** K2 against A on the determined bugs, on the maintainers' tests:
- more bugs better than worse;
- the frozen rule met;
- no bug worse through a finding that rests on a case that passed on the parent.

**The 95% target, stated exactly.**
- Recorded runs bound the gated loop at about 85% of runs right on determined bugs: A's 77.5%, plus two bugs.
- 95% also needs the two generation bugs and pygments-3158. They need a better first attempt and a check that quotes the
  issue, not a better chooser.
## 2026-11-04 (evening): the powered study runs on the subscription, through a host-session author

**Owner's decision.** "Spend on subscription please." The aim is a measurable uplift over Claude Code, with G6 improving
Claude Code on the bugs where that is possible.

**The design.** The sign-in never enters a container: Claude Code runs on the host, every test in the bug's container.
- **The author.** `claude -p` on the host, in a scratch copy of the parent tree.
  - The copy is kept outside any `.claude` folder, because Claude Code protects those from edits.
  - Flags: `--restricted --tools "Read,Edit,Write,Glob,Grep"`, settings with `blockReadsOutsideWorkingDirectories` and
    `blockWritesOutsideWorkingDirectories` true and `disableAllHooks`, `--permission-mode acceptEdits`,
    `--permission-prompts none`.
  - No Bash or PowerShell tool at all. Native Windows has no sandbox, and read-only shell commands run without a prompt.
  - No API key in the environment, so the sign-in is used.
- **The one test tool.** An MCP server, `box`, passed by `--mcp-config` with `--strict-mcp-config` and allowed alone. Each
  call does two things:
  - mirrors the scratch copy into the bug's container, which has no network;
  - runs the command there as the unprivileged user.
- **The rest is unchanged.** The final patch comes from the container as now, scored by the same independent check, and K2's
  reviewer and parent check run as on `claude/k2`.

**Probed with harmless dummy files, CLI 2.1.318.**
- The sign-in works under `--restricted`.
- A read of a canary file outside the scratch folder was refused, and its content never reached the output.
- An edit inside the scratch folder was applied.
- A write outside it was refused.
- The MCP tool was the only other tool, and was called with no denial.

**Record and pacing.**
- **A separate archive root,** so host runs are never pooled with container runs. Arms A and K2 are compared there only
  with each other.
- **Not counted as API spend.** Claude Code's cost figure under a subscription is an estimate, so it is recorded apart and
  never added to the study's spend.
- **Usage limits.** The plan's limits pace the study. A run that meets one is paused until the reset the message gives,
  never scored.

**Order of work.**
1. Build the host author, test first, with fakes.
2. Its own Codex review loop.
3. A pilot on development bugs.
4. Draw about 130 new bugs by the T2 rule, with the blind determinacy audit and preparation.
5. Run A and K2 twice each on stage 4's bugs and the new ones. The prediction registered at 1a98c52 is read on the
   determined bugs.
## 2026-11-04 (evening): stage 5's bugs, the rule before the draw

**Purpose.** The powered K2 study (1a98c52, f52e316) needs about 100 determined unseen bugs. Stage 4's 31 count, since K2
has seen none of them. Stage 5 adds about 130.

**The rule.** Stage 4's T2 rule, unchanged in every other respect:
- **Projects.** The 63 that the 2026-10-31 screen kept (`selection/candidates_unseen.json`), in rank order from 1 to 100.
- **Candidates.** Each project gives at most its next two newest: merged, linked to an issue, not marked mechanical. A
  candidate is skipped if stages 1, 2 or 4 drew it, or if any task set holds its bug.
- **Judgement.** A model reader judges T2 on the issue's text alone, never on a fix, a test or a run. Security reports are
  left out.
- **Stop.** At 130 kept, or when the projects run out. If they run out first, the count is what it is: no third
  candidate per project is drawn.

**After the draw, in order.**
1. Tasks drafted: parent commit, maintainers' fail-to-pass tests.
2. The blind determinacy audit: two families, Codex and Opus, labels only.
3. Preparation: `images`, `validate`, `selfcheck`, `freecheck`.

A bug that fails preparation gives way, with no replacement. There are no amendments. The record is `selection_stage5.md`.

**The owner's framing of the goal, 2026-11-04 (evening).**
- Show on real bugs that G6 improves Claude Code.
- Show that G6's self-diagnosis and self-rectification loop does this from data, essentially autonomously.
- Then offer it as a framework: users bring their own tasks with a truth signal, and G6 first analyses whether their data
  suffices, then builds and validates their harness.
- This study is the first part. The 0.3.0 loop is the second. It must make headroom its pre-spend check, and make
  structural edits part of what it can propose.
**Addendum, before any candidate is read.** The 2026-10-31 screen saved at most six mechanically passing candidates per
project, and only 53 are unused: 48 under the cap of two per project.
- **A deeper screen.** The 63 kept projects are screened again by `selection.screen`, the same mechanical rules T1, T3, T4
  and T2's one-issue part, over the same window (2026-06-01 to 2026-10-29), up to 10 passing candidates each.
- **Then the rule above.** Each project gives its next two newest unused candidates, and stops at 130.
- **The window does not widen.** An earlier merge risks bugs already in the model's training data.
**Second addendum, still before any candidate is read.** The deeper screen ended with no errors and gives too few
candidates.
- **What it gave.** 190 mechanically passing candidates in the 63 projects. 127 are unused, but they sit in 26 projects, so
  the cap of two per project leaves 46.
- **What that comes to.** At past rates (T2, preparation, then the determined share), 46 candidates come to about 13
  determined bugs, or about 34 with stage 4's 21. The study needs about 100.
- **The fix: more projects, not more bugs per project.** More per project would correlate the sample.
  - The next PyPI ranks, 101 to 250, are screened with the same project rules (P1, P2, P4) and the same mechanical rules,
    over the same window.
  - Each kept project gives at most its two newest unused candidates, as before.
  - Ranks 1 to 100 are taken first, then 101 to 250, in rank order.
  - The draw stops at 130.
- **If the supply still falls short,** the study runs on what there is. Its power is reported for the number reached.
**The host session's settings, from the probes, before any host run.** `claude/host-author` 9b2ac40, built test first. Five
probe transcripts and the driver's own environment showed three gaps in the first build, all repaired:
- **The driver's environment leaked.** The driving Claude Code session's variables, its messaging socket and token among them,
  would have reached every author. The host environment is now an allowlist, and every CLAUDE* and ANTHROPIC* name is dropped.
- **The account's claude.ai connectors reached the session.** That was 49 tools (Gmail, Drive, Docs) and one server's
  instructions, despite `--strict-mcp-config`. They are now switched off. A session whose init names any other server or MCP
  tool is stopped and its run refused, never scored.
- **A refused request was retried on another model.** In 3 of 5 probes Claude Code retried after a refusal (category "cyber")
  on an older model and finished the session there. That retry is now off. Refusals, the models that wrote and any switch are
  recorded.

**The pilot of those settings,** one session on Sonnet 5.5 with the study's flags and environment:
- exactly the five file tools and the box's `run`, with `box` the only server;
- no connector tools or instructions in the transcript;
- the subscription's sign-in (no API key) and one model throughout, CLI 2.1.318;
- the box call and the edit worked, with no denial.

The skills, plugins and agents its init lists are Claude Code's own bundled ones; with no Skill or Agent tool, none can be used.

**How a run a refusal ends is counted, set now.**
- It is a run that was made, scored on the patch it left, as Claude Code alone would leave it. Both arms meet the same
  classifier.
- Refused runs are reported by arm, and the primary result is also read without the bugs where any run was refused.
- No refused request is resent or reworded.
- **How the widened screen reads its rules, set before it runs.**
  - P1 is read as written: 10,000 stars, or a PyPI rank of 100 or better. A project ranked 101 to 250 therefore meets it by
    stars alone. The count dropped for P1 is reported.
  - The window is the deeper screen's, 2026-06-01 to 2026-10-29. The 2026-10-31 screen's own window began 2025-12-01, so
    "the same window" above means the deeper screen's.
  - Ranks come from PyPI's list as read now, with its update date recorded.
  - A repository that an earlier screen covered, a training repository, or a repository an earlier rank already gave, is
    skipped.
  - The script is `rescreen6.py`, kept with the selection record. It writes `selection/candidates_stage5b.json`.
**Third addendum, before any candidate is read.**

**What the widened screen gave.** All 150 ranks were screened, with no errors.
- Only 9 projects meet P1, P2 and P4, and 104 fail P1 among other rules.
- 4 of the 9 have mechanically passing candidates: 30 in all, 8 under the cap.
- So stage 5 has about 54 candidates, and stage 4 and 5 together about 36 determined bugs at past rates.

**Power at that supply.** From `metaharness.evaluate.power`, for the gated effect registered at 1a98c52:

| Determined bugs | 36 | 50 | 80 | 100 |
|---|---|---|---|---|
| Power | 0.26 | 0.42 | 0.69 | 0.80 |

A study at 36 cannot decide, so it is not run as the powered study.

**The fix, inside the rule.** P1 already admits any project with 10,000 stars. Only the population was limited, to PyPI's ranking.
- **Population.** GitHub's Python repositories with at least 10,000 stars, from `gh search repos`, in star order, at most 1,000.
- **Left out:**
  - forks and archived repositories;
  - every repository an earlier screen covered, and the training repositories.
- **Rules.** P2 and P4, the mechanical rules and the window are unchanged, with up to 10 passing candidates each.
- **Draw.** Two newest unused per project. Ranks 1 to 100 first, then 101 to 250, then this population in star order, to 130.
- **A screen that meets GitHub's rate limit** waits and retries the same project. It never skips one.
- **Record.** The script is `rescreen7.py`. It writes `selection/candidates_stage5c.json`.
- **If this still falls short,** the study's power is reported for the number reached, and it is not called powered.
## 2026-11-04 (evening): replay 6, a formal model of the issue's rule checked on every case, registered before it is built

**Owner's direction, 2026-11-04 (evening).** Push further: let the model use formal methods to model the problem, and so
find what "the LLM alone is literally unable to with any combination of looping/tools and or prompt engineering".

**What can be claimed, decided now.**
- A solver is itself a tool, so "no combination of tools could do it" cannot be measured.
- What can be measured is narrower: on held-out bugs, a formal route catches, and later fixes, what every LLM-only arm
  recorded here missed.
- Every claim is over a stated finite domain, never for all inputs.

**Why this class.** The two generation bugs (the 2026-11-04 afternoon section) failed the same way in 20 runs across five
arms. Each run fixed the issue's example and left the rest of a stated rule to the old code. A formal model must give every
case in its domain a value, and G6 then runs every case, not only the cases a test happens to sample.

**What it is.** Replay 3's machinery: one reviewer pass per bug, from the issue and the parent alone, then the same check on
the parent and on each distinct patch. Its three parts:
1. **The domain and the probe.** Reader 1 (Codex) writes the input domain as named factors, each with a few values and at
   most 512 cases in their product. It also writes a probe file whose `observe(case)` calls the code and returns named JSON
   observations. The probe runs only in the bug's container.
2. **The rule, as data, twice.** Reader 1 and reader 2 each write a rule table over that domain, independently. Reader 2 is
   Claude, `claude -p` with no tools, on the subscription. Neither reader sees the parent's outputs. Each row of a table has:
   - `when`: factor values;
   - `expect`: a JSON value, or equality with another observation of the same case;
   - `quote`: the issue's words the row rests on.
3. **G6's checks, in plain code, before anything runs on a patch.**
   - A row whose quote is not a verbatim span of the issue or its discussion is dropped.
   - If two rows of one table match a case with different expectations, the table conflicts with itself and is refused.
   - The two tables are compared on every case both cover. Where they differ, the bug is reported as ambiguous and nothing is
     flagged on it. This is the afternoon section's rule for blind readers who disagree.
   - At least one agreed case must fail on the parent. Otherwise the model reproduces nothing and the bug is left out: the
     parent gate.

**The finding rule.** A patch is flagged when an agreed case that fails on the parent still fails on it, which is K2's gate.
The ungated count is reported beside.

**Components.**
- The run and container steps are replay 3's.
- On a finite domain, enumerating every case decides coverage, conflict and the two tables' agreement exactly, so no solver is
  added. `formal.check_contract` and `check_equivalence` (Z3) come in only if a test shows a domain too large to enumerate.
- The probe is the only code a model writes, and it never runs in G6's process.

**Development check.**
- **Bugs:** stage 1 and 2's 34, all 420 distinct patches, on the amended tests, as replays 4 and 5 were checked.
- **Cost:** Codex sign-in and subscription calls, no API credit.
- **When:** after replay 5, since both need containers and memory is the limit.
- **Where:** built on `claude/replay6` from `claude/replay5`, test first with fakes, with its own Codex review loop.

**Prediction.**
1. Where the model stands, false alarms on right patches are at most 5%, with the 95% upper bound under 10%.
2. Where the model stands, at least 30% of wrong patches are flagged.
3. On at least one of click-3404 and starlette-3189, at least half the distinct wrong patches are flagged.
4. At least 5 wrong patches are flagged that replays 3 and 4 both passed.
5. The two readers disagree on a larger share of the 14 undetermined bugs than of the 20 determined ones, by at least 10
   points. If so, disagreement between formal readers is a mechanical determinacy check, cheaper than the blind audit. It
   would also let G6 itself say where no uplift is possible.

Reported beside:
- on how many bugs the model stands, is ambiguous, or reproduces nothing;
- the maintainers' tests;
- the union with replays 4 and 5.

**Kill rule.** If false alarms are over 15% where the model stands, or no wrong patch is flagged on either generation bug,
replay 6 goes no further and arm F is not built.

**If it passes.**
- It is scored unchanged on stage 4's patches, bugs it was not developed on, with the same measures.
- Then arm F is registered, with its own prediction, before any run. It is K2's loop in which, on bugs whose model stands, the
  reviewer's finding is the table of failing cases. That is the step that could show a fix no LLM-only arm reached. It runs
  on stage 5 in host mode.

**What it cannot do.**
- The model is only as right as the readers' reading of the issue. Two readers who misread it the same way give a confident
  wrong model, so false alarms are always read against the maintainers' tests, never against the model.
- Undetermined bugs stay undetermined.
## 2026-11-04 (evening): the parent gate is by case, found by replay 6's review, before replay 5 runs

**What the review found.** Replay 6's first Codex review reproduced the fault. Replay 3's verdict flagged a patch when any case
failed on the parent and any case failed on the patch. It did not require the same case. Replays 4, 5 and 6 share that verdict.

**What was registered.**
- Replays 5 and 6 were registered with the stricter rule: a case that fails on the parent still fails on the patch.
- K2's gate, and the gated counts at 1a98c52, were computed case by case from the saved results, so they used it too.

**Repaired at `claude/replay5` cf211ee, test first, before replay 5's first run.**
- `Instruction.same_case` turns the rule by case on. Replays 5 and 6 turn it on.
- Replays 3 and 4 keep the rule they were scored with, so their recorded results stand as recorded.

**Reported beside them.** Replays 3 and 4 are also scored by case, from the per-case results their records already hold, with
no rerun. The difference between the two rules is reported, by patch.

## 2026-11-04 (night): replay 6's code review ended at its limit; a follow-up comes before its development check

**The loop.** `claude/replay6` had four Codex reviews and three repairs (ba72967, ee6c61f, db29929). The reviews found 7, 10, 5
and 7. Every finding was reproduced by its own test before it was counted. The loop never reached a clean review.
- Codex's filter stopped review 3 partway. It was not retried or reworded. A fresh Claude reviewer stood in, and the tests
  Codex had already written were kept.
- One earlier control fails by design: `test_old_replay_sources_and_instructions_are_identical`, since replay 3 gained
  `same_case` (the section above).

**Rejected, with the reason.**
- Review 3, lead 1: a probe that reads its own expected values can only make its own cells pass. So it can cause a miss,
  never a flag. Recorded as a limit.
- Review 4, finding 5: the patch's bytes change between the duplicate check and the run. Only something writing to the frozen
  archive during a run can do that, and nothing does. Each saved record names the bytes it reviewed, so it would show.

**Open from review 4, reproduced on db29929.** They are repaired as their own change, `claude/replay6-followup`, test first,
with its own review loop. Review 4's tests are its first failing tests.
1. A probe that returns text which is not Unicode (a lone surrogate) passes as JSON. The cell then fails on both trees and
   flags the patch, though the model reader refuses the same text.
2. Reader 2 is sent the probe with comments and docstrings removed, but data stays. A constant holding reader 1's table reaches
   reader 2, so the two tables are no longer independent.
3. The probe's process works in the project folder, and replay 3 runs the parent and then the patch in one container. A file
   the probe writes there (here a `pytest.py`) changes the patch's run.
4. On restart, a saved compiled test is reused however the compiler changed since, because the record is named by the
   instruction alone. No replay 6 record exists yet, so this moves no count.
6. A probe that overruns is killed, but the processes it started are not. They keep running into the patch's run.
7. The check that an observation is JSON runs in the probe's own namespace and trusts the returned object's equality. So a
   probe can approve a value that JSON would change (a tuple read as a list).
- **Lead, not counted:** a project that is importable only through pytest's `pythonpath` setting cannot be imported in the
  probe's process. Its cells are skipped: a miss, not a flag.

**Decided for that change: remove the root, not the instances.** Findings 3, 6 and 7 share one root. The probe shares too much
with what judges it: a namespace, a folder, a process tree and a container.
- The probe runs in a namespace of its own. Whether a value is JSON is decided by an exact walk of types, with no equality
  the value supplies.
- The probe's text is read by the same rule as the model's data (finding 1: one fact, one rule).
- The probe keeps the project folder as its working folder, since probes read the project's files by relative path. It gets
  the pytest process's import path. (Amended before any build: an empty folder would turn those probes into misses, and a
  container for each run already removes what finding 3 found, as the probe's answer is computed once a run.)
- On a timeout, the whole process group is ended.
- Each run of replay 6, the parent's and the patch's, gets a container of its own.
- A compiled test's record is named by the compiler's source as well as the instruction.
- What reader 2 is sent is masked mechanically wherever a literal equals one of reader 1's expected values. The masks are
  counted and reported.
- Cost if wrong: one container start more per patch, and a reader 2 that sees less of the probe.

**Replay 5 is unchanged.** It shares replay 3's one container per patch. Counted on the records: none of the 79 saved model
tests in round 3's replay records writes outside a temporary folder. The count looked for writes by `open`, `Path` writes,
`os.chdir`, and `shutil` and `os` file moves. So the shared container moved no recorded count of replays 3 and 4. Replay 5
runs as registered and reports the same count for its own tests.

**When.** Replay 6's development check waits for the follow-up's review loop. It still runs after replay 5.