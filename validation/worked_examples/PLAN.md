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
