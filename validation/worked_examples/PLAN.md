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
