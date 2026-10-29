<!-- SPDX-License-Identifier: Apache-2.0 -->
# Worked examples: results so far

**Worked examples: mechanism demonstrations on real code; they show what happened in these runs and say nothing
about how often G6 helps.**

- The plan is `PLAN.md`. It was fixed before any run, and its dated changes are in section 9.
- The selection is `SELECTION.md`.
- The scripts are `selection.py`, `run.py` and `wecommon.py`.
- The trails (transcripts, every round's patch, G6's replies, logs) are in the local archive, outside the repository.

**State (2026-10-29):**
- All ten runs are done: runs 1 to 5 on G6 2680cc9, runs 6 to 10 on G6 a923002.
- Spent: $1.84 of the $100 stop.
- If the security review of W1 to W5 leads to a fix pass, runs 6 to 10 stay as made, on a923002.

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

## Held (2026-10-29, after run 5; resumed under option B)

After run 5 the series was held before run 6, because the server defect above would have stopped G6's server there
too. The owner chose option B. Runs 6 to 10 run on G6 a923002 (W1 to W5), with each task's repository fetched from
upstream (PLAN.md section 9).

## Runs 6 to 10 (G6 a923002)

Before each paid run:
- the runner checked the task's repository: HEAD is the parent commit, HEAD's tree equals upstream's (from GitHub's
  API), one commit, a clean tree;
- the key check passed;
- a dry run with no key and no way out had shown G6's server starting with its 20 tools in every one of these
  projects.

| # | Task (licence) | Issue, fix | G6, round by round | G6's findings | Independent check (round patches, final) | Cost, time |
|---:|---|---|---|---|---|---|
| 6 | pydantic/pydantic (MIT) | [#14041](https://github.com/pydantic/pydantic/issues/14041), [#14052](https://github.com/pydantic/pydantic/pull/14052) | 1: insufficient evidence | none | fail; final **fails** (1 of 1 FAIL_TO_PASS) | $0.16, 26 s |
| 7 | pallets/click (BSD-3-Clause) | [#3571](https://github.com/pallets/click/issues/3571), [#3769](https://github.com/pallets/click/pull/3769) | 1: supported within scope | none | fail; final **fails** (3 of 6 FAIL_TO_PASS) | $0.20, 1.5 min |
| 8 | python-attrs/attrs (MIT) | [#1575](https://github.com/python-attrs/attrs/issues/1575), [#1593](https://github.com/python-attrs/attrs/pull/1593) | 1: refused (template bindings); 2: supported within scope | none | pass, pass; final **passes** | $0.15, 1.8 min |
| 9 | fastapi/fastapi (MIT) | [#15401](https://github.com/fastapi/fastapi/issues/15401), [#15077](https://github.com/fastapi/fastapi/pull/15077) | 1: test integrity compromised | 1, false alarm | pass; final **passes** | $0.20, 1.7 min |
| 10 | pallets/click (BSD-3-Clause) | [#2819](https://github.com/pallets/click/issues/2819), [#3678](https://github.com/pallets/click/pull/3678) | 1: supported within scope | none | fail; final **fails** (10 of 10 FAIL_TO_PASS; see the run) | $0.15, 1.9 min |

**What happened, run by run**

6. **pydantic #14052**
   - **The fix:** the agent stopped the docstring visitor from entering nested classes.
   - **G6's review:**
     - the agent's test was demonstrated: it fails at the base and passes on the patch;
     - G6 left R2 ("existing behaviour unchanged") not established. Its stated check was the whole test file, and two
       of the file's tests are skipped or expected to fail, so the check did not run to a pass;
     - there was no reproduced finding.
   - **The patch fails the maintainers' test for documented nested definitions,** and G6 did not reproduce it.
7. **click #3769**
   - **G6 gave "supported within its stated scope".**
   - **The patch fixed the iterating progress bar only.** Three of the maintainers' six new cases use `bar.update(n)`,
     and there the bar still ends at 14/20 and 18/20.
   - **G6 did not reproduce this.**
8. **attrs #1593**
   - **The template bindings were refused once.** G6's refusal named the missing binding (`bindings.cases`) and the
     two names `example.v1` does not take.
   - **On the second call** the agent's `example.v1` instance was accepted, and G6 gave "supported within its stated
     scope".
   - **The patch passes the maintainers' tests.**
9. **fastapi #15077**
   - **G6's finding:** the integrity reason `ignored_file_can_change_tests`, on `.pdm-build/fastapi.pth`.
   - **Where the file comes from:** the project's build backend, pdm-backend. Its editable install, the project's own
     development install, writes `.pdm-build/` with a `.gitignore` of its own that ignores everything in it. This
     harness did not make it, and a developer's install of fastapi makes the same file.
   - **The finding was a false alarm.** G6 gives the same reason on the maintainers' fix (`run.py g6replay`). Because
     of it, G6 ran neither the base comparison nor the tests that touch the changed code.
   - **The agent asked rather than acting.** W3b's text says to track or remove such a file. The agent instead asked
     the person whether removing or force-tracking it is acceptable, and did not do either.
   - **The patch passes the maintainers' tests.**
10. **click #3678**
    - **G6 gave "supported within its stated scope".**
    - **The patch fails all ten of the maintainers' FAIL_TO_PASS tests,** but on details the issue does not state:
      - eight assert the automatic help option's internal name, `_click_default_help` (the agent named it
        `_click_help_flag`) before they reach any behaviour;
      - two expect a new warning the maintainers added.
    - **The issue's own two examples pass on the patch:** an argument named `help` takes its value, and an option
      named `--help` keeps its default. They pass on the maintainers' fix too (`run.py replay`, with a test file
      holding only those two examples).
    - **By the plan's rule the patch fails the independent check.** That check cannot show whether the patch fixes the
      issue's behaviour.

**What the runs show about W1 to W5** (from `observe.py`, read from each archived transcript)

| # | G6's server (W1, W5) | Reply sizes (W2) | Report shown whole (W2) | Template instances (W4) |
|---:|---|---|---|---|
| 6 | connected, 20 tools | 5,730 characters, in the transcript | yes | none |
| 7 | connected, 20 tools | 6,645, in the transcript | yes | none |
| 8 | connected, 20 tools | 2,682 (the refusal) and 5,405, in the transcript | yes (as a Markdown quote) | refused once for bindings, then accepted |
| 9 | connected, 20 tools | 5,645, in the transcript | no: the agent summarised it | none |
| 10 | connected, 20 tools | 5,811, in the transcript | yes | none |

For comparison, at 2680cc9 (runs 1 to 5), every finished reply was 64,108 to 242,490 characters, and Claude Code
saved each to a file. No report was shown whole, and in run 3 G6's server did not start.

## The ten runs together, in plain words

These are worked examples. They show what happened in these ten runs and say nothing about how often G6 helps.

- **The independent check:**
  - four final patches pass: runs 3, 4, 8 and 9;
  - six fail: runs 1, 2, 5, 6, 7 and 10. Run 10's failures are on details the issue does not state.
- **What G6 concluded:**
  - **No defect reproduced.** G6 reproduced no defect in any patch. In each of the five runs where the patch fails on
    behaviour (the issue's, or in run 5 behaviour the base already had: runs 1, 2, 5, 6 and 7), G6 concluded
    something else:
    - "test integrity compromised" in runs 1 and 2;
    - "supported within its stated scope" in runs 5 and 7;
    - "not enough evidence" in run 6.
  - **"Supported within its stated scope" was given five times:**
    - in runs 4 and 8 the patch passes;
    - in runs 5 and 7 it fails on a case none of the stated checks reached;
    - in run 10 it fails on details the issue does not state.
  - **Integrity alarms.** Three runs gave "test integrity compromised" (1, 2 and 9), all false alarms:
    - in runs 1 and 2 because of this harness's repositories;
    - in run 9 because of a file the project's own build backend writes.
    - Each alarm stopped G6 from running the tests that touch the changed code.
- **The repair loop:**
  - no run repaired anything G6 reproduced, because G6 reproduced nothing;
  - no run had more than one finished review; run 3 had none.
- **G6's own faults met in the product flow, and their fixes:**
  - at 2680cc9, a project's packages replaced G6's own in its server: it failed to start in run 3 and ran the
    repository's starlette in runs 4 and 5. W1 fixed this: at a923002 the server started in all five projects;
  - G6's replies were too large for Claude Code. W2 fixed this: at a923002 they were 2,682 to 6,645 characters, and
    four of the five reports were shown whole;
  - template refusals did not say what was wrong. W4 fixed this: in run 8 a refusal named the missing binding and
    the next call was accepted.
- **Spent:** $1.84 in all, of the $100 stop: $0.10 to $0.31 a run, 26 seconds to 5.4 minutes each.

## Round 2: G6 with its own model

**These are worked examples, not a study.** Each round 2 run is a new run of the same task: the agent writes a new
patch, which can differ from its round 1 patch for reasons that have nothing to do with G6. So a difference between
the rounds is not evidence that G6's model helps, and a sameness is not evidence that it does not. What follows says
what happened in each run.

**How round 2 was run** (PLAN.md section 9, "Round 2: G6 with its own model" and the two dated notes after it):

- **G6:** commit 9ad134d, which includes the security review's fix pass, installed as its README says. Two lines in
  the image's copy (`round2_g6.py`) put G6's check proposer (VS-5) on for every review, at the code's own limits:
  `templates_first`, at most 10 model calls a review.
- **G6's model:** claude-sonnet-5-5, through the `anthropic` provider. Its key is in G6's key file in the container
  (owner-only), written only through standard input, and removed before G6's state folder is copied out.
- **The agent, its container and its way out** are as in round 1. The task's repository is fetched from upstream.
- **Before the first run:**
  - one G6 model call from the container through the proxy replied, and only api.anthropic.com was reached;
  - a dry run with no key passed the repository, hook and server checks.
- **The key check before every run:** its own review asked G6's model while the confined test looked for the key.
  The confined test found nothing; the unconfined control found the key in the environment and in G6's key file.
- **Cost:** G6's caller reports no token counts, so each of G6's model calls is counted at an upper bound: the
  request's characters over two in, the 4,096-token reply limit out. G6's costs below can only be overstated.

| # | Task | Round 1 (G6 alone): G6's outcome, independent check | Round 2 (G6 + its model): G6 by round | What the proposer did | Round 2 independent check | Cost (agent + G6), time |
|---:|---|---|---|---|---|---|
| 1 | black #5634 | test integrity compromised (a harness false alarm); fails | 1: refused (template bindings); 2: not enough evidence; 3: supported within scope | 8 calls a review, 16 in all, all replied. Three free-form checks a review: 1 check error, 2 failing on the base too, because they could not import black (see below). No finding. | final **fails** (2 of 2 FAIL_TO_PASS), as in round 1 | $0.31 + $1.54, 4.7 min |
| 2 | black #5521 | test integrity compromised (a harness false alarm); fails | 1: supported within scope | 8 calls. Three `differential.v1` instances (2 check errors, 1 refused for arguments `black.trans.TErr` cannot take) and 2 free-form checks (check errors): none could import black in G6's copy. No finding. | final **fails** (1 of 1 FAIL_TO_PASS), as in round 1 | $0.44 + $0.96, 8.1 min |
| 3 | pydantic #14065 | G6's server did not start (2680cc9); passes | 1: test integrity compromised (`run_not_trusted`) | 8 calls; 2 free-form checks, both skipped after the integrity reason. No finding. | final **passes**, as in round 1 | $0.14 + $0.94, 2.8 min |
| 4 | starlette #3720 | supported within scope; passes | 1: supported within scope | 9 calls; 3 `differential.v1` instances and 2 free-form checks, all run, none refuting the patch. No finding. | final **passes**, as in round 1 | $0.11 + $0.85, 1.5 min |
| 5 | starlette #3718 | supported within scope; fails (a trailing-slash `root_path` regression) | 1: supported within scope | 8 calls; 2 free-form checks, both run, refuting nothing. No finding. | final **passes**: this run's patch keeps the trailing-slash case. G6 reproduced nothing, so the change is the agent's, not G6's | $0.12 + $0.82, 1.6 min |
| 6 | pydantic #14052 | not enough evidence; fails | 1: not enough evidence (R2's whole-file check has skipped tests) | 9 calls; 2 `example.v1` and 2 `differential.v1` instances (1 refused) and 3 free-form checks, all run, none refuting the patch. No finding. | final **fails** (1 of 1 FAIL_TO_PASS: documented nested definitions), as in round 1 | $0.12 + $0.85, 3.5 min |

**What happened, run by run**

1. **black #5634**
   - **What the proposer gave:** each review asked G6's model 8 times, once a stage from `example.v1` to the
     free-form checks.
   - **Why its checks did not run:** the three free-form checks it proposed in each review could not be collected,
     at the base or on the patch: `ModuleNotFoundError: No module named '_black_version'`. Black's build writes that
     file at install time and git ignores it, and G6 runs a proposed check in a copy of the repository that leaves
     ignored files out.
   - **G6's verdicts:** one check was a check error and two failed on the base too. G6 put them under "for you to
     decide", as its rule for checks a model proposed says, and reproduced nothing.
   - **The agent's patch fails the same two maintainers' tests as in round 1** (an indented `# fmt: off`).
