<!-- SPDX-License-Identifier: Apache-2.0 -->
# V7 — G6's check-and-fix against Claude Code checking itself, on SWE-bench Pro: protocol (draft, not frozen)

**Status:** draft, not frozen. The lead freezes it after review, before any scored run: its SHA-256, the scripts'
SHA-256 and G6's commit then go into `oss/docs/validation/REGISTRY.md` (entry "V7"). No model has been called for this
study. No scored task's text, patch or tests has been opened by a person.

**Governing documents:** `oss/docs/validation/VALIDATION_PLAN.md` §0 (rules 1-9) and "Reporting". Where this protocol
departs from them, §11 lists the departure and its reason. If they conflict in a way §11 does not list, they win.

**What V7 asks:** after Claude Code has solved a real repository task once, does a second pass driven by G6's own
checks fix more tasks than a second pass in which Claude Code checks itself, with the same budget? And does G6's check
find the stage-1 patches that are wrong?

## 0. The owner's request, choices and approvals

- **The request** (2026-10-23, verbatim): "if the core audience is coders we should really show that this provides value
  over claude code alone using for example sonnet on SWE-bench hard to validate the system's abilities." Then: "use G6
  to engineer a better coding harness for the particular task to show the power of G6."
- **Design D** ("design D pls", 2026-10-23), chosen because a result that can be significant within $200 is not
  possible with 20 tasks and three independent arms. It replaces the earlier three-arm design and its option 1 for
  arm C, which are removed (their code and tests are deleted, not kept inert).
- **Hosted model (rule 1):** model calls are paid by an Anthropic API key the owner puts in `~/.g6/.env`, with a spend
  limit the owner sets. Putting the key there is the owner's written approval for this study (owner ruling
  2026-10-21) and its cap. The key holds $200 of credit (lead, 2026-10-23).
- **Transparency** (owner, 2026-10-23: "ensure that we store all experimental log and api output etc for transparency
  and later analysis"): every run is archived in full (§8).
- **Shadow learning** (2026-10-24): the owner asked "can you use T2 learning component for the learning process (i.e.
  theory formation) so there is continual learning during the run as well if this possible?" and chose shadow learning:
  G6 forms lessons during the run and archives them, and no lesson is given to Claude Code (§4, step 5).
- **Who decided what:**
  - The task pool is the full SWE-bench Pro V2 set: the lead's default, since the owner did not choose.
  - The stage-1 caps (high effort, 100 turns, $4, 50 minutes) are the builder's choice, accepted by the lead.
  - The stage-2 caps ($1.50, 30 turns, 15 minutes) and A2's words are the lead's.

## 1. Population

SWE-bench Pro, public set, **V2** (Scale AI; Deng et al. 2025, arXiv 2509.16941): 642 tasks from 11 repositories (Go,
Python, JavaScript, TypeScript), each a real pull request with hidden fail-to-pass and pass-to-pass tests.

| Item | Source and revision | SHA-256 |
|---|---|---|
| Task list | Hugging Face `ScaleAI/SWE-bench_Pro` at `2d52cb3df914a3fcf80c7f66738b3a88ae37fc50` (config `default`, version 2.0.0), `data/default/test-00000-of-00001.parquet`, 6,511,689 bytes | `1067f72c1f43bbc578276888625db3b836cebb535546c9eaf824c5d5dd104a7f` |
| Dataset card | the same revision, `README.md` | `243f51de326a4502f0e353eb3e25cb712c465e78d80e15cdaac4db70e5a9b83f` |
| Official harness and task folders | `github.com/scaleapi/SWE-bench_Pro-os` at `66f92766bba642462d4bbe5479e83f91f9211862`, tarball | `a459a929729c95d15cb7b4c5c599d36ea4e9a067bedc9d018cee6032b577e6cc` |
| The harness tree's own checksums | `v2/SHA256SUMS` (every file under `v2/` checked against it: no mismatch) | `9d84f8507c89241d42d8b3ef911600a1ec75dbbb32687ce9b45b93318502c0bd` |
| Task images | `ghcr.io/scaleapi/swe-bench_pro-v2:<instance_id>`, pulled anonymously; each run records the image's digest | per run |

- **Licence:** the harness and tooling are MIT (`LICENSE`, Scale AI 2026). The dataset card says the task content
  (problem statements, patches, tests) is derived from the 11 upstream repositories and stays under their licences; the
  card names no licence for the dataset as a whole. **V7 publishes task ids and results only**, never task text.
- **Why V2:** its images were rebuilt from sanitised repositories (no fixing commit, stray refs, stashes or hooks). Every
  task passes with its reference patch and fails with an empty one (642/642 each, the harness's `GATE.md`). It ships
  the locked evaluation protocol this study follows.
- **The full set, not HARD-51:** the owner said "SWE-bench hard". The dataset has a 51-task HARD subset (tasks at least
  two of five model families failed). The full V2 set is the lead's default.
- **Not the archive's set:** the archive row "SWE-bench Pro, 38 of 100 against 27 of 100" used v1 with OpenAI's Codex;
  V7 is not a rerun of it and is not compared with it.

## 2. The task order

`sample.py` (its docstring is the rule) reads three columns, `instance_id`, `repo` and `repo_language`, and nothing
else. Seed `20261023`. The ids are in `oss/docs/validation/results/V7/splits.json`.

- **The 10 tuning ids** (`dev`): one repository per language class (`internetarchive/openlibrary`,
  `navidrome/navidrome`, `NodeBB/NodeBB`). They are never scored. The dry run used the first two; the pilot uses the
  first four (`pilot`).
- **The scored order** (`scored`, 60 ids, fixed now):
  - the 20 evaluation ids registered before, in their order (8 repositories, disjoint from the tuning repositories);
  - then 40 more, by the same rule: the round-robin continued from where it stopped, now over all 11 repositories,
    each repository's seeded order less the ids already taken and less the 10 tuning ids.
  - The 40 added tasks include tasks from the three tuning repositories (12 of them), but not the tuning tasks
    themselves.
- **Who reads what:** nobody opens a scored task's problem statement, patch or tests. The runner reads a task's
  `instruction.md` only to hand it to Claude Code, and reads the hidden test patch's file names only to assert they are
  absent (§6). The verifier copies the task's `tests/` folder by path.

## 3. The agent and its settings

- **Claude Code `2.1.309`**, the official linux-x64 build (`downloads.claude.ai/claude-code-releases/2.1.309/linux-x64/
  claude`, SHA-256 `4d704a8aecbe038affa3000f498ae17c1b4f753add8facb8764120affc99b658`, matching its release manifest),
  copied into each task's container. A musl image is refused and recorded.
- **Model** `claude-sonnet-5-5`, `--effort high`.
- **Flags, the same in every stage:** `--output-format stream-json --verbose`, `--dangerously-skip-permissions` (in the
  throwaway container, with `IS_SANDBOX=1`), `--disallowedTools WebFetch WebSearch`, `--strict-mcp-config`, and no MCP
  server, skill or appended system prompt. Stage 2 adds `--resume <the stage-1 session>`.
- **Caps** (`v7common.CAPS`, `CAPS_STAGE2`):

  | | Turns | Wall clock | Spend (`--max-budget-usd`) |
  |---|---|---|---|
  | Stage 1 | 100 | 3,000 s | $4.00 |
  | Each stage-2 pass | 30 | 900 s | $1.50 |

  - The runner backs each cap up from the stream: past the wall clock, past twice the turns in assistant messages, or
    once its own token count passes 1.05 times the spend cap. Its count uses the brief's prices: $2 input, $10 output,
    $0.20 cached input and $2.50 cache writes per million tokens (the last is an assumption).
  - A stopped run's patch is still captured and graded.
- **Container:** the task's image, with the task's own limits (1 CPU, 4,096 MB), the repository at `/app` (or
  `/testbed`). Before stage 1 the runner records whether the repository's history could hold the fix.
- **Network (locked protocol):** every container of a task is on one internal Docker network with no route out. A
  sidecar runs `egress_proxy.py`, which passes only `CONNECT api.anthropic.com:443` and logs every request.
- **The patch:** the official locked agents' capture (`git add -A; git diff --cached; git reset`) after each stage.

## 4. The design: one task, three arms

1. **Stage 1, arm A:** Claude Code solves the task once, given its `instruction.md`.
2. **The snapshot:** the finished container is saved as an image (`docker commit`). Both stage-2 passes start from that
   same state and resume the same Claude Code session (its files are in the image).
3. **Stage 2, arm A2 (self-check):** a container from the snapshot. Claude Code resumes and is told, the same words
   every task: "Check your work: reproduce the issue, run the tests you can, and fix anything that is wrong."
4. **Stage 2, arm G (G6 checks):**
   - **G6's check** (`g6check.py`) runs in its own throwaway container from the snapshot, under a standalone CPython
     3.12.15 with G6 installed from the wheel built from the release ref. It uses G6's shipped functions, unchanged:
     - `workforce.repo_tests.change` and `diff_text`: no file changed is a finding;
     - `coding_agent_gate.scan_diff`: a test shortcut in the diff;
     - `safety_engine.detect_test_weakening`: the change's test files before and after;
     - the changed Python files that do not parse (`swe_diagnostics.patch_check`'s rule);
     - `workforce.repo_tests.derive_tests`, then `repo_tests.run` on those visible tests. A `failed` verdict is a finding.
       Any other verdict (a collection error G6 cannot place, no tests, a timeout) is recorded and is not a finding.
   - `repo_tests` runs pytest only, so for the Go and JavaScript tasks G6's check has no test run; its other checks
     apply to every language. The library offers no reproduction runner, so none is used.
   - Given the container's own `python3`, the check points a `.venv` at it for the run, which `repo_tests` reads as the
     repository's own interpreter.
   - **If G6 flags a problem**, Claude Code resumes in a fresh container from the snapshot. G6's findings are its
     instructions: "G6's pre-finish check of your change failed:", the findings, then "Fix what it names, keep every
     existing test active, then finish." The caps are those of A2.
   - **If G6 does not flag**, G keeps the stage-1 patch and spends nothing more.
   - G6's check never runs in a container where Claude Code works, so A2 and G resume from identical states, and G's
     resumed pass sees G6's words only (no G6 tool or skill).
5. **Shadow lesson** (`shadow.py`), after both stage-2 passes and before grading, and only when G6's check found
   something (no finding, no model call):
   - A model (`claude-sonnet-5-5` through `g6.core.llm`, the same key, at most 1,200 output tokens, one request with
     no retry and a 120-second timeout) is given which of G6's checks fired, G6's findings as Claude Code was given
     them (cut to 12,000 UTF-8 bytes), G's outcome as anyone sees it without the grader (whether G acted, how its run
     ended, whether its patch changed) and the lessons so far. It is not given the task id, which names a public task
     it may have seen. It writes one lesson: a failure code, one instruction a coding agent could follow, and why.
   - The lesson runs `g6.core.llm` and `g6.learning` from the runner's own tree (`oss/src` at the frozen commit), not
     from the wheel the containers install.
   - A failure code new to the phase's `g6.learning.TheoryStore` becomes a candidate theory; a code the store holds is
     recorded as a recurrence. Nothing credits, promotes or activates a theory: the library needs paired held-out
     evidence and a person's acceptance for that.
   - **No lesson reaches Claude Code in this study:** no arm's prompt, flags or environment carries one, so A2 and G
     are as before. A failed or unreadable lesson is archived and changes nothing else.
   - The library's T2 (`g6.learning.baselines.TemplateStrategy`, T1 and T2 merged) is not used: its seven fixed hints
     are keyed to question-and-answer failure codes, none of which fits a patch.
6. **Grading** (`evaluate.verify`): the stage-1 patch, A2's final patch and G's final patch, each by the official
   verifier on a pristine container, **after both stage-2 passes have finished**. `run_arm.grade_task` refuses a task
   whose stage 2 has not finished.
7. **Nothing in stage 1, stage 2 or the lesson sees the hidden tests or the grader's result.**
   - Before each container's work (stage 1, A2, G6's check, G) the runner asserts that `/tests`, `/solution`,
     `/logs/verifier`, the grader's patch files and every file the hidden test patch creates are absent. If one is
     present, the task stops before any model call (`OracleVisible`).
   - When G6 acts is decided by G6's check alone, never by a grade.

**The pilot** (`run_arm.py pilot`): the full design on the first 4 tuning ids. Its results are kept apart (phase
`pilot`), never scored. It measures cost and proves the pipeline end to end, and its spend counts against the $180.

## 5. Order and spend

1. The dry run and probes (done, no model; §10).
2. `run_arm.py build-wheel --ref <the release branch>`.
3. `run_arm.py pilot`.
4. The lead enters the measured costs and G6's commit, and freezes this protocol and the registry entry.
5. `run_arm.py batch`: the scored order, two tasks at a time.
6. The oracle self-test on the completed tasks (§8), the gaming scan, a person's review, then the report.

**Spend rules** (`v7common.may_start`, `run_arm.batch_tasks`):

- The cap is `G6_BENCH_MAX_USD`, default 180 and never above 180.
- Every run not yet finished is counted at its reservation: stage 1 $4.50, each stage-2 pass $2.00 (its cap plus $0.50
  for the one model message that can complete after Claude Code reaches its budget), and the lesson $0.10 (its input
  is cut by UTF-8 bytes, and a token covers at least one byte, so at the prices in `v7common.PRICES` it cannot cost
  more; a lesson is expected to cost about $0.02, which the projection below counts for every flagged task).
- A task starts only when all of its runs fit, at $8.60, beside every task in flight.
- Tasks run in the fixed order, two at a time. When the next task does not fit and nothing is in flight, the batch
  stops, and the report says how many tasks completed. The analysis covers completed tasks only, and counts every run
  started.

## 6. Oracle

The benchmark's own verifier (`evaluate.py`), as the locked protocol's re-grader does it (`v2/tooling/patch_replay.py`
under Harbor 0.22's verifier). The steps:

1. A fresh container from the task's image.
2. The patch applied with the re-grader's command (nothing for an empty or missing patch).
3. The task's `tests/` copied to `/tests`, and `/tests/test.sh` run as root with its output in `/logs/verifier`.
4. The reward read from `reward.json` or `reward.txt`.

A patch is **resolved** when its reward is 1. No model judges anything. One verifier runs at a time.

## 7. Pre-registered questions

- **H1 detection:** G6's flag (from its stage-2 check on the stage-1 patch) against the hidden outcome of the stage-1
  patch. One-sided Fisher exact test: the flag rate on failed stage-1 patches exceeds the rate on passed ones.
- **H2 final resolve:** G against A2, paired by task; one-sided exact sign test on the discordant pairs (G resolves and
  A2 does not, against the reverse).
- **Holm** across H1 and H2 at family alpha 0.05.
- **Secondaries,** reported and not tested for the headline: G vs A, A2 vs A, breaks (a passing stage-1 patch made
  failing), cost per arm, G6 false-flag rate.
- **Shadow learning,** counted and not tested: lessons written, new candidates, recurrences by failure code, cost.
- **Wording rule for the report:** the headline names the comparison exactly ("G6's check-and-fix against Claude Code
  checking itself with the same budget"); a non-significant result is reported as such.

`analyse.py report` computes these on the completed tasks: Fisher's exact test, the sign test
(`g6.reasoning.compare.sign_test_p`), Holm, and a two-stage cluster bootstrap interval for each paired comparison
(repositories, then tasks; 10,000 resamples, seed 20261023).

**Expected tasks completed under the cap** (`analyse.py project`). These assume the pilot's 4 tasks run first, two at a
time, every task costing the same:

| Stage-1 mean cost | Stage 2 at $1.00 a pass, G acting on half | Stage 2 at its caps, G acting on all |
|---|---|---|
| $2.00 | 45 | 31 |
| $3.00 | 35 | 25 |
| $4.00 | 28 | 21 |

**Power, one-sided at 0.025 (Holm's first step):**

- H2 (`analyse.py power`), sign test: with 15% of tasks won by G and 3% by A2, 0.15 at 30 tasks and 0.28 at 40; with
  20% and 5%, 0.23 and 0.36.
- H1, Fisher's test, with half the stage-1 patches failing and G6 flagging 60% of failed against 20% of passed: 0.35 at
  25 tasks, 0.55 at 35, 0.72 at 46.

The study can establish a difference only if the effect is large. Every result, significant or not, is reported.

## 8. The archive, and the controls

**The archive** (`v7common.Archive`):

- **Where:** `V7_ARCHIVE`, default `~/g6-validation/V7` (the folder the lead named on the study machine), outside every
  worktree.
- **One folder per run,** `<phase>/<task id>/<arm>/<UTC timestamp>/`, created once. The arms are `task` (the task's
  own record), `A`, `A2`, `G`, `lesson`, `grade-A`, `grade-A2` and `grade-G`; the phases are `pilot`, `eval`, `selftest`,
  `dryrun` and `build`.
- **Write-once:** a run folder or a file that already exists is refused, and nothing in the pipeline deletes or
  overwrites an archived file. The one exception is the shadow lessons' working store, `shadow/<phase>.db` in the
  archive root (and SQLite's own journal files beside it), which every lesson updates; each lesson's
  `theories.json` is its write-once record.
- **What a run folder keeps,** complete and unfiltered except for key redaction:
  - Claude Code's full stream-json output, every line (`stream.jsonl`: its messages, tool calls and results, usage per
    message, and the final result with its cost), and its standard error;
  - the exact prompt (`prompt.txt`) and command (`command.json`: the docker and Claude Code arguments, caps, model,
    effort, session resumed);
  - the environment (`environment.json`: image digest, Claude Code's version, the repository's history state);
  - for G, G6's check output exactly as returned (`g6_check.json`, its standard output and error) and the findings
    handed to Claude Code (`findings_given.txt`);
  - the patch after the stage (`model.patch`);
  - for a lesson, what it was given (`lesson_input.json`), its prompt, the full API response (`response.json`), the
    lesson as read (`lesson.json`) and the store's theories after it (`theories.json`). The store itself,
    `shadow/<phase>.db` in the archive root, is a working copy; each lesson's `theories.json` is the record;
  - for a grading run, the verifier's full output (`verifier/`, exit codes);
  - the task run's egress proxy log (`egress.jsonl`).
- **`run.json`,** written last, indexes the folder (each file's size and SHA-256). It records how the run ended
  (`finished`, `cap_turns`, `cap_cost`, `timeout`, `error`, `refused`, `kept`, `no_session`, `interrupted`), its
  spend (reported and estimated), the G6 wheel's ref, commit and SHA-256, and any redaction.
- **`runs.jsonl`** at the root is append-only: one line before each run starts, and one when it ends. A run left
  without its end by a crash is ended as `interrupted` at the cost its stream shows, and is never run again.
- **One attempt per task:** a task whose stage 1 started Claude Code is never run again, whatever came of it. The one
  exception is a stage 1 the API refused before the model saw anything: it ended in an error with no token in or
  out and no cost (a key or account fault). There is no outcome to choose among, so it is an infrastructure
  failure, not an attempt: it stays in the archive and the task may start again. (Added before the freeze, after the
  first pilot of 2026-10-25, whose four tasks were refused this way: the key was not scoped to a workspace.)
- **The spend rules and the analysis read only the archive.**
- **The key never reaches the archive:**
  - Text is redacted before it is written.
  - When a run finishes, its folder is scanned for the key's value (held in memory, never printed) and for
    `sk-ant-`-shaped strings. A hit is replaced by `<withheld>` in place, the only change the archive allows; it is
    recorded in `run.json`, and the run is flagged.
- **What the repository holds:** only small, publishable things: `results/V7/archive_manifest.json` (every archived
  file's path, size and SHA-256, regenerated by `analyse.py manifest`) and the analysis's summaries.
  - **The raw transcripts stay in the archive.** They hold the upstream repositories' code and issue text, so
    publishing them is the owner's decision after a licence check.

**Controls:**

- **Oracle self-test:** on the two dry-run tasks the reference patch resolved and an empty patch did not, through the
  same container, capture and verifier path (§10). After the scored runs, `evaluate.py selftest` does the same on every
  completed task, each check its own archived run.
- **Gaming checks (rule 8), before any result is read:**
  - `analyse.py gaming` scans every scored run's stream, patch and repository state, and the task's egress log. It looks
    for test edits, access to the grader's files, use of git history, and network fetches.
  - A person inspects every match and writes `gaming_review.json` in the archive root. A confirmed finding voids that
    task. `analyse.py report` refuses to run without the review.
- **The key:** read only through `g6.core.keys`, and only for a task the spend rules let start. It reaches the Claude
  Code process in the container through `docker exec -e ANTHROPIC_API_KEY`, by name, with its value only in the docker
  client's environment.
  - Residual: the agent's own shell inherits the variable inside its container, as with any Claude Code run.

## 9. Verdict, in order (one, rule 7)

1. A confirmed finding that an arm reached the oracle, the fix or its evaluator → `contaminated_evaluation`.
2. A2 beats G (more wins than losses, sign test at 0.05 after a Bonferroni factor of 2) → `unsafe_or_regressive`.
3. The oracle self-test fails: more than 5% of the completed tasks' reference patches unresolved, or any empty patch
   resolved → `oracle_inadequate`.
4. Stage 1 fails fewer than 10% of the completed tasks → `insufficient_failure_opportunities`.
5. H2 passes after Holm → `mechanism_demonstrated`.
6. H1 passes after Holm and H2 does not → `component_validated_no_transfer` (G6's check finds failing patches, and its
   fixes do not beat Claude Code's own check).
7. Otherwise → `inconclusive`.

**Pre-registered expectation:** `inconclusive`, with `component_validated_no_transfer` the likeliest alternative. Every
miss of every arm is published in the per-task rows.

## 10. Development history so far (no model)

- **Dry run** on the tuning tasks `openlibrary-6e889f4a` (Python) and `navidrome-dfa453cc` (Go), with the scripts of
  2026-10-23 (before design D):
  - each container was prepared (sidecar, network rule, Claude Code, standalone Python, G6), with a wheel built from
    commit `70854de825` for that purpose;
  - the reference patch, or nothing, stood in for the agent and was captured as an arm's patch is;
  - graded: reference resolved 2/2 (17/17 and 1/1 required tests passing); empty unresolved 2/2.
  - The probe: `claude --version` printed `2.1.309 (Claude Code)`; `g6-mcp` listed its 12 tools over stdio; Claude
    Code's flags parsed with no key and a dead proxy; a direct connection was refused, and the proxy refused
    `github.com:443`.
  - Its records are kept in the archive under `legacy/dryrun-2026-10-23/`.
  - Design D changed the pipeline around those steps (the snapshot, the resumes, G6's check, the archive). The pilot
    is its first end-to-end run.
- **Offline checks** (`oss/tests/validation/test_v7_swebench.py`, 52 tests, with no network, no Docker and no model):
  - the draw and the fixed scored order;
  - the $8.60 task reservation, and runs in flight;
  - shadow learning: the lesson after both stage-2 passes and before any grade, from G6's findings and G's outcome
    only, without the task id, never in what Claude Code is given, a new candidate or a recurrence, no model call
    without a finding, its full API response and cost archived (the cost counted even when archiving the response
    fails, and a crashed lesson ended at its responses' cost), its cost bounded by its reserve in UTF-8 bytes, a
    failed lesson (even one that cannot start) changing nothing and never costing the task its grading, the store's
    lock free during the model call, one request with a short timeout, and the report's count of lessons;
  - two tasks at a time, never past the cap, and a stop that is reported;
  - A2's words the same for every task, and equal stage-2 caps resuming the same session;
  - G spends nothing when G6 does not flag;
  - no grading before both stage-2 passes;
  - the grader's material absent from every container, and a task stopped when it is present;
  - the archive: write-once, and a `run.json` and ledger lines for every run that hit a cap, failed or crashed;
  - the key scan withholding a planted key and flagging the run, and the manifest's hashes;
  - G6's check on a real git repository;
  - H1, H2 and Holm on known tables, and the verdict's order;
  - the report naming the comparison and counting stopped runs.
- **A library fact found earlier:** `coding_agent_gate.verify_answer` cannot be a meta-harness pipeline step's op
  (`G6_E_OP_NOT_CLEARED`). Design D does not use the meta-harness.

## 11. Departures from VALIDATION_PLAN.md §0, with reasons

1. **Rule 1, a hosted model:** Claude Sonnet 5.5 through Claude Code, under the owner's written approval and cap (§0).
2. **Rule 3, splits:** design D has no tuning, so the scored tasks need no disjoint development split. The 10 tuning
   ids are kept out of the scored order, and the pilot uses four of them. The dataset's authors wrote and labelled the
   tasks (rule 9).
3. **Rule 5, compute matching:** A2 and G have the same caps, and G spends nothing when G6 does not flag, so G never
   spends more than A2's cap. Costs are reported per arm. A self-consistency baseline has no meaning for patches.
4. **Rule 4, n:** the number of scored tasks is set by the money (§5, §7): 21 to 46, by the projection. The cluster for
   the intervals is the repository.
5. **Rule 6:** the oracle's error is bounded by the harness's release gate (642/642 reference resolved, 0/642 empty) and
   by this study's self-test.
6. **G6's check is G6's shipped functions run by the study's script** (`g6check.py`), not an MCP session. The test run
   is pytest only (the library's own scope). The `.venv` pointer to the container's interpreter is how the library's
   documented interpreter rule finds the project's Python.
7. **The full V2 set, not HARD-51** (§1): the lead's default; the owner did not choose.
8. **G6's wheel is built at run time** from a named ref, so its commit is not known at this draft. The freeze names it.
9. **The earlier designs are withdrawn:** the three-arm design and its arm C (option 1: one candidate approved by the
   owner) are replaced by design D. Nothing of them was run with a model.

## 12. Open before the freeze

- The lead names the ref the wheel is built from, enters the pilot's measured costs and the commit, and freezes.

## 13. Reproduce

`README.md` beside this file lists every command, in order.
