<!-- SPDX-License-Identifier: Apache-2.0 -->
# Worked examples: results so far

**Worked examples: mechanism demonstrations on real code; they show what happened in these runs and say nothing
about how often G6 helps.**

- The plan is `PLAN.md`. It was fixed before any run, and its dated changes are in section 9.
- The selection is `SELECTION.md`.
- The scripts are `selection.py`, `run.py` and `wecommon.py`.
- The trails (transcripts, every round's patch, G6's replies, logs) are in the local archive, outside the repository.

**State (2026-10-29):**
- Runs 1 to 5 are done.
- The series is held before run 6 (see "Held").
- Spent: $0.98 of the $100 stop.

## How the runs were made

- **G6:** commit 2680cc9 of claude/launch. It contains the fix pass's Critical commit 5dad0fe and item L1. G6 was installed as the README at that commit says: `python -m venv --copies .venv` in G6's folder, then `pip install ".[all]"`, then `g6 setup --client claude --allow patch_review.review`.
- **Agent:** Claude Code 2.1.309, headless, model claude-sonnet-5-5, with the using-g6 skill. It worked in a container whose only way out was api.anthropic.com.
- **Projects:** each project's `.venv` was made with links, as projects make it. Its dependencies are the versions that were published when the fix was merged.
- **Free check before the first paid run:** one confined review of a project whose `.venv` uses links, with no model call. It passed.
- **Key check before every run:** a confined G6 check found no key. The unconfined control did find it.
- **Validation of every task, with no model:**
  - the maintainers' tests fail on the parent commit and pass with their fix;
  - the maintainers' fix passes the independent check, and an empty patch fails it.

## The runs, in order

| # | Task (licence) | Issue, fix | G6, round by round | G6's findings | Independent check (round patches, final) | Cost, time |
|---:|---|---|---|---|---|---|
| 1 | psf/black (MIT) | [#496](https://github.com/psf/black/issues/496), [#5634](https://github.com/psf/black/pull/5634) | 1: refused (a template instance missing `bindings.relation`); 2: test integrity compromised | 1, false alarm | fail, fail; final **fails** (2 of 2 FAIL_TO_PASS) | $0.25, 3 min |
| 2 | psf/black (MIT) | [#3424](https://github.com/psf/black/issues/3424), [#5521](https://github.com/psf/black/pull/5521) | 1: test integrity compromised | 1, false alarm | fail; final **fails** (1 of 1 FAIL_TO_PASS) | $0.31, 5 min |
| 3 | pydantic/pydantic (MIT) | [#14064](https://github.com/pydantic/pydantic/issues/14064), [#14065](https://github.com/pydantic/pydantic/pull/14065) | none: G6's server did not start | none | final **passes** | $0.10, 1 min |
| 4 | encode/starlette (BSD-3-Clause) | [#3687](https://github.com/Kludex/starlette/issues/3687), [#3720](https://github.com/Kludex/starlette/pull/3720) | 1: supported within scope | none | pass; final **passes** | $0.13, 1 min |
| 5 | encode/starlette (BSD-3-Clause) | [#3689](https://github.com/Kludex/starlette/issues/3689), [#3718](https://github.com/Kludex/starlette/pull/3718) | 1 to 3: refused (template instances missing or misnaming `bindings.function` and `bindings.mode`); 4: supported within scope | none | fail ×4; final **fails** (1 PASS_TO_PASS) | $0.20, 1.5 min |

**What happened, run by run**

1. **black #5634**
   - **G6's finding:** an integrity reason, `ignored_file_can_change_tests`. The files it names are Black's own test fixtures under `tests/data/gitignore_used_on_multiple_sources/`, which Black's repository keeps and ignores on purpose.
   - **The finding was a false alarm.** G6 gives the same reason on the maintainers' fix (`run.py g6replay`). Because of it, G6 ran none of the tests that touch the changed code.
   - **The agent's patch is incomplete.** It handled a `# fmt: off` at the top level only. The maintainers' tests also cover one inside a function, and both of those fail on the agent's patch. The agent named that case as untested in its own report.
2. **black #5521**
   - **G6's finding:** the same integrity reason on the same fixtures. It was a false alarm, again shown on the maintainers' fix.
   - **The maintainers' data-file test for the issue's case fails on the agent's patch.**
3. **pydantic #14065**
   - **G6's MCP server failed to start** (see "A defect found"). The agent said so, did not change any permission, and delivered without a review.
   - **Its patch passes the maintainers' tests.**
4. **starlette #3720**
   - **G6 gave "supported within its stated scope"** in one round.
   - **The patch passes the maintainers' tests.**
5. **starlette #3718**
   - **G6 refused three reviews** for invalid template instances. The agent then dropped the templates, and G6 gave "supported within its stated scope".
   - **The patch fails a maintainers' test.** With `root_path` "/app/" (a trailing slash), the parent commit's code and the maintainers' fix both strip the prefix, but the agent's patch does not. This is a regression on an input none of the stated checks covered, and G6 did not reproduce it.

**In every run where G6 finished a review, its reply was larger than Claude Code accepts as a tool result.** That is four replies (64,108 characters in run 1). Claude Code saved each reply to a file, and the agent read it with `jq`. In run 1, the agent then paraphrased the report instead of showing it verbatim, as the skill asks.

## A defect found in G6 during the runs

`g6 setup` registers the server as `python -c "from g6.mcp import main; main()"`. Two things combine:

- `python -c` puts the current folder first on the import path;
- Claude Code starts the server in the project's folder.

As a result, a project whose top-level package has the name of one of G6's dependencies replaces that dependency inside G6's own server process:

- **pydantic's repository:** G6 imported the repository's `pydantic`, and its server failed to start (run 3).
- **starlette's repository:** G6's server imported the repository's `starlette` and ran it there, unconfined (runs 4 and 5). This was checked with no model.

## Corrections (2026-10-29, after run 5; the records above are left as first written)

1. **Runs 1 and 2's integrity alarms came from the harness, not from Black.**
   - **Where they came from:** this harness made each task's repository with `git init` and a plain `git add`. Black's
     repository tracks test fixtures under `tests/data/gitignore_used_on_multiple_sources/` that Black's own
     `.gitignore` files match on purpose. A plain `git add` left them untracked and git-ignored.
   - **Why G6 raised them:** G6's rule for `ignored_file_can_change_tests` goes by file names only. It saw git-ignored
     files the tests could load and gave "test integrity compromised".
   - **A real clone gives no alarm.** In a repository fetched from upstream, at G6 a923002, `run.py g6replay`
     reviewed the maintainers' fix of #496 with one of their tests as the stated check. G6 gave "supported within
     its stated scope" with no integrity reason.
   - **The "false alarm" judgement stands,** because the reason was never about the agent's patch. But
     `run.py g6replay` reproduced it on the maintainers' fix only because the oracle image shared the same repository.
   - **G6's rule is unchanged for the launch.** Since W3b (in a923002) its report says what to do about git-ignored
     files.
   - **From run 6 on, the harness fetches upstream's base commit itself** (PLAN.md section 9). The runner checks the
     repository's tree against upstream's before every paid run.
2. **Run 5's last review did not drop the templates,** as the run's account above says. On its fourth try the agent's
   `differential.v1` instance was accepted. It compared the base and the patch on one call, with `root_path` "/app".
   That call does not reach the regressing case (a trailing slash), so it found no difference.

## Held

| # | Task | State |
|---:|---|---|
| 6 | pydantic #14052 | held before its run: the defect above would stop G6's server here too |
| 7 | click #3769 | not run yet |
| 8 | attrs #1593 | not run yet |
| 9 | fastapi #15077 | not run yet |
| 10 | click #3678 | not run yet |
