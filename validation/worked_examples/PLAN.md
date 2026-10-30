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
