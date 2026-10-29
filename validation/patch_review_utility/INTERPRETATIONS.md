# How the V9 scripts read the protocol

The protocol (`docs/validation/patch_review_utility/PROTOCOL.md`, version 1) leaves some details open. This file
records how the scripts in this folder read each one, and which reading the study's coordinator ruled on. The
feasibility freeze entry hashes this file together with the scripts; the entry point records its digest in every
results file. Changing a reading after a freeze changes the hash and is a deviation, logged as the protocol requires.

1. **Which checks a requirement cites.** The review contract carries no checks, and G6's review needs stated checks
   for each requirement. Every requirement cites the same checks: the test files the patch adds or changes (pytest's
   default names, `test_*.py` and `*_test.py`) that are on the patched tree; a test file the patch deletes is not
   cited, since no check can run from it, and a test file it moves is cited at its new path. A patch that adds or
   changes no test leaves G6 with insufficient evidence. The repository's existing tests reach G6 through its own run
   of the tests that touch the changed modules. Accepted by the coordinator; the rule for deleted and moved files was
   added with the re-sync to Step 2's frozen commit.

2. **When arm 1 says a test fails or passes.** Across the three runs on the patched tree, a test fails when pytest
   reports it failed in at least two runs, and passes when pytest reports it passed in at least two. A run that could
   not run counts as failing every test. A test the patch deleted is not a failure. A skipped test the patch added is
   not a pass. "The tests the patch adds or changes" means every test in a test file the patch adds or changes.
   Accepted by the coordinator.

3. **When arm 1's suite cannot be run.** A suite that cannot be collected or run in two of the three patched runs is
   not accepted, as a defect found. A suite that cannot run in any base run is a tool error, since there is nothing to
   compare with. Runs killed at their time limit are ruled under item 10. Accepted by the coordinator.

4. **How the task statement is split.** The problem statement is requirement R1, whole. Each top-level list item of
   the stated requirements follows, with its indented detail; text before the first item, or text with no list item,
   is split into paragraphs. Each interface block follows, except one that says no new interface is introduced. Text
   repeated word for word is kept once. A requirement longer than G6's limit is split at paragraph breaks, then line
   breaks, then spaces, and never shortened. Accepted by the coordinator.

5. **Newcombe's interval.** Method 10 of Newcombe's 1998 paper: Wilson score limits for each arm's rate, combined with
   the phi coefficient of the paired table, without continuity correction, and with phi set to zero when any margin of
   the table is empty. Method 11 is the corrected one and is not used. Confirmed by the coordinator.

6. **The bootstrap.** For each difference, the patches of that difference's class (defective for the first two
   hypotheses, correct for the third) are resampled with replacement within each repository, each repository keeping
   its size, repositories in name order, from one random generator seeded with the level's seed plus one. The limits
   are the 5th and 95th percentiles, interpolating linearly between ordered values. Accepted by the coordinator.

7. **Comparing the agent's own review with G6.** Two comparisons, both with no verdict. First, the three differences
   with the agent's own review in G6's place: its false acceptance rate minus that of tests and continuous
   integration, its false acceptance rate minus that of the same checks without G6, and the correct acceptance rate of
   tests and continuous integration minus its own. Second, G6 against the agent's own review directly: G6's false
   acceptance rate minus the agent's, and the agent's correct acceptance rate minus G6's. A direct form of the second
   hypothesis would compare G6 with the agent's review on false acceptance again, the same difference as the first,
   so it is not computed. Ruled by the coordinator.

8. **The order of verdicts 5 and 6.** Fewer non-empty correct patches than the registered size makes the verdict
   inconclusive even when Experiment 1 is met; this is checked after verdict 4 and before verdict 5. Accepted by the
   coordinator.

9. **The oracle at the feasibility level.** The feasibility verdict is oracle inadequate only when a label audit was
   done and its bound is above 15%, since the audit is optional at feasibility. Accepted by the coordinator.

10. **Time limits and the token cap.** An arm stopped at its wall-clock limit, or at its token cap, with no reply is a
    tool error and counts toward the feasibility limit of 10% tool errors. A reply the arm produced itself is not: the
    agent's answer cut short or given after its limit, or G6 replying after its limit, is an abstention; G6's or the
    direct checks' own outcome stands when their deadline cut checks short. G6's reply says so only in its own codes:
    the reason code `run_timed_out`, or the verdict `timed_out` on a stated check or a run; its wording is never read.
    For the direct checks it is a run's verdict `timed_out`, or their run of the tests that touch the changed modules
    left out because the time limit was reached. Every disposition the time limit or the token cap shaped is
    tallied apart as "time or cap reached", whatever its category; an acceptance is never one. For arm 1, a base run
    killed at its limit is a tool error. When the base runs completed and the patched tree's runs were killed at the
    limit in at least two of the three, the patch caused it: arm 1 does not accept, as a defect found, marked "time or
    cap reached", since continuous integration reports a time-out as a failure and the label rule counts an over-time
    check as failing. G6's arm and the direct checks run each patch's review in a fresh interpreter of their own, in
    isolated mode, killed with its whole process tree when their time limit plus 60 seconds has passed, so no hang
    inside the review, Python code included, can stall the study; a child killed with no reply is a tool error. The
    child gets the study's environment without the research flag. Ruled by the coordinator, with the arm 1 reading
    clarified in a second ruling.

11. **Infrastructure errors in the labels.** A run in which the executor itself failed (the overlay did not apply, the
    command could not start, no outcome report) is run once more. If the error persists, the patch gets no label: it
    is left out of the primary analysis, counted and reported, and the sensitivity analysis counts it as defective,
    with the decisions the arms gave it (an empty patch, which no arm reviewed, counts as accepted by none). In
    calibration a persisting error fails the task's oracle controls, so the task is replaced. A skip on the patched
    tree stays a failed run, and a timeout stays the label rule. Ruled by the coordinator.

12. **Escalation.** None of the four arms produces an escalation to a person at this commit. The tally is kept, at
    zero. Accepted by the coordinator.

13. **The draw.** One random generator, seeded with the level's seed, shuffles each repository's sorted task ids in
    repository name order. Before the first task is drawn every drawn share is zero, so the largest repository goes
    first. Shares are exact fractions. Accepted by the coordinator.

14. **Defect categories.** A defective patch's categories come from its failed label checks: `fail_to_pass` (a hidden
    test the reference fix makes pass), `pass_to_pass` (a hidden test that must keep passing) and `specification` (a
    withheld specification check). Accepted by the coordinator.

15. **G6's model calls.** Read from G6's reply when the reply reports them, as the sum of each activity's model calls
    under `assessment.completion.activities`. When the reply has no such field they are counted as zero, since G6's
    review at this commit makes no model call when no claim and no sources are given. The results record which source
    was used. Ruled by the coordinator.

16. **What the direct checks run.** Arm 4 runs every deterministic check G6's frozen review (Step 2, commit b3c342b)
    runs whose result can change G6's outcome, and accepts only when each passes; `arms.py` lists them with their
    lines in G6's review. G6's run of the passing stated checks against the base commit's code is left out: it can
    only add an unknown and a next action, never change G6's outcome, so it cannot change arm 3's acceptance. Ruled
    by the coordinator. Where the G6 under test reads which code a run loaded (the pool-compatibility change), arm 4
    reads it the same way: a run of the stated checks, or of the tests that touch the changed modules, that loaded a
    changed module from outside the tree, or cannot say what it loaded, is no evidence of the patch, and that check
    does not pass. At b3c342b, which has no such reading, arm 4 runs as before. On the same principle (every
    deterministic rule that can stop G6's acceptance is mirrored), where the G6 under test reads the submodule folders
    git does not look into, arm 4 does not accept a patch that changes a submodule's entry, or a repository with such
    a folder G6 could not hash, before the runs or only after them. It reads them as G6's review fix pass (8b5364c)
    does: a folder not hashed on either side is "not hashed", never "changed", and Python's bytecode folders
    (`__pycache__`) are left out of the hash. Ruled by the coordinator.

    Arm 4 decides acceptance as G6 does, but it does not always give the same kind of non-acceptance, and its kinds
    are its own: the per-reason secondary measures read arm 4's categories as arm 4's, not as G6's. There are two
    differences, kept in the code because neither changes whether a patch is accepted. First, arm 4 does not run the
    tests that touch the changed modules once any earlier check has failed, while G6 still runs them unless an
    earlier check found an integrity problem or a failing test; so where G6 would go on to find a defect after a
    shortfall in the evidence, arm 4 reports the shortfall (insufficient evidence) instead. Second, when the scan for
    git-ignored files stops at its limit, arm 4 counts it as an integrity problem and G6 counts it as insufficient
    evidence. Ruled by the coordinator.

17. **Where each arm runs.** At a study level, every arm runs in a fresh container of the task's own benchmark image,
    removed when it ends; the image is never changed and never fetched during a study. Inside the container the
    image's repository must be at the task's base commit, and the patch (for the labels, with the hidden tests) is
    applied there. Arm 1 and the labels run in the pristine image, with nothing of G6's mounted and the network
    closed: the repository's tests with the image's own interpreter, as its own continuous integration would run
    them. Arms 3 and 4 run in the same image with G6's pinned toolchain mounted read-only (a Python 3.12, G6 from a
    wheel built at the frozen commit, git 2.31 or newer, and bubblewrap; the images carry neither G6's Python nor,
    in ansible's case, a git G6 accepts), while the repository's tests still run with the image's own interpreter,
    named by `G6_REPO_PYTHON`. The toolchain's sources are pinned by hash in `environment/toolchain.json`, and a
    toolchain is used only when it matches its lock. Every task image is named by its digest, never by a tag. The
    study's toolchain lock is built at the verification search freeze commit, which includes the pool-compatibility
    change; the lock built at b3c342b, and every throwaway toolchain the development check uses, is for development
    only and never runs the study. Ruled by the coordinator.

18. **One test environment per repository, and the image's pytest settings cleared.** Each repository's test
    environment is declared once in the manifest (qutebrowser's choice of Qt binding, for example) and is the same
    for arm 1, the labels and G6's arms: set in the container where the study runs the tests itself, and passed to G6
    through `G6_REPO_ENV` (one JSON object of names to text), which G6 applies inside its confined runs. It may not
    name a pytest, Python, coverage or library loader setting, `PATH`, the home or temporary folders, or a G6
    setting: the names G6 refuses. Every container also clears the image's own
    `PYTEST_ADDOPTS` and any other `PYTEST_*` variable the image sets, before anything runs. This departs from the
    images: qutebrowser's sets `PYTEST_ADDOPTS` to rerun failing tests three times and to go on past collection
    errors. The reason: G6 refuses runner settings from the environment, so leaving them for arm 1 and the labels
    would make one side more lenient than the other, and V9's own rules of three runs are what handle checks that do
    not give the same result each time. Ruled by the coordinator.

19. **Testing the patched code, not the installed one: each repository's import roots and test environment.** Where
    an image installs the repository's own package outside the repository, `import` from the repository's folder
    loads the installed, unpatched code, and every arm would test the base commit. Each repository therefore names
    its import roots, the folders that make the patched code importable: `PYTHONPATH` in arm 1's and the labels'
    containers, and G6's `import_roots` for its arms (the direct checks pass the same roots to G6's runner). A
    planted-change control in the development check shows, per image, that an arm fails a chosen test when a
    throwaway change breaks the function it exercises, and passes it without the change; a control that does not
    fail means the arm is not testing the patch. The choices, from the benchmark's own run scripts and Dockerfiles,
    read for V7's development tasks only:
    - ansible: import roots `lib`. Its image installs ansible-core without the editable option (`pip install .` in
      the task's instance Dockerfile), and the benchmark runs `ansible-test units` from the checkout
      (`run_script.sh`), which imports ansible from the checkout's `lib`. Test environment `ANSIBLE_VERBOSITY=3`
      (`run_script.sh`, beside `PYTHONPATH=/app`, which the repository's folder on the import path already gives).
    - qutebrowser: no import roots: its image installs it in editable mode from `/app` (`pip install -e .`, instance
      Dockerfile). Test environment `QUTE_QT_WRAPPER=PyQt6`, `QT_QPA_PLATFORM=offscreen` and
      `QTWEBENGINE_CHROMIUM_FLAGS=--no-sandbox --disable-dev-shm-usage --disable-gpu` (`run_script.sh`). Its
      `PYTEST_QT_API=pyqt6` is a `PYTEST_*` name, refused and cleared (item 18); PyQt6 is the only binding the image
      has, so pytest-qt chooses it anyway. The study sets no `DISPLAY` and starts no Xvfb: the image's pytest-xvfb
      plugin starts an Xvfb display for every run and sets `DISPLAY` (the repository's `tests/conftest.py` stops every
      test when there is none), and Qt's offscreen platform connects to that display. When the plugin stops the
      display at the end of the run, the X library ends the process with exit code 1 after pytest has finished with
      0 (`XIO: fatal IO error`). Arm 1 and the labels read pytest's own exit status, from the study's outcome plugin,
      so a run whose tests all passed reads as passing. No test environment setting removes the error without
      changing a test's outcome: `QT_QPA_OFFSCREEN_NO_GLX=1` keeps Qt off the display, and so removes the error, but
      fails one of the QtWebEngine backend tests; pytest-xvfb 2.0.0 reads no setting that turns it off (only its
      `--no-xvfb` option). The test environment is therefore left as the run script sets it.
    - openlibrary: no import roots: the repository's folder is on the import path for every arm, which is what the
      `PYTHONPATH=/app` of two of its four V7 run scripts gives. Test environment
      `OL_CONFIG=/app/conf/openlibrary.yml` (two of its V7 run scripts and Dockerfiles; the file is in all four
      images).
    Ruled by the coordinator.

Two readings are mechanics rather than rulings, recorded so the hash covers them. The executor runs the files of the
test ids asked for, as the benchmark's own procedure runs test files, so a test id that no longer exists is missing
instead of stopping the whole run. When the executor's command runs a container, each run names its container, and a
run past its timeout stops the container itself by that name.

A third mechanic, from V10: `stats.stratified_bootstrap` takes a `confidence`, so V10 can ask for its 95% interval.
V9's 90% stays the default, and gives the same interval as before the argument was added: checked on 200 random
cases against the earlier code (commit 7ee1fb4), and kept as a test, `tests/validation/test_v9_stats.py`,
`test_v9s_90_percent_bootstrap_is_what_it_was_before_it_took_a_confidence`, on three fixed cases whose intervals the
earlier code gave. The executor also reports each test's phases (set-up, call, teardown) beside its outcome, for V10's
arm E; V9's outcomes are unchanged. Ruled by the coordinator: accepted.

Version 2, "Broken tasks" (`broken.py`, with `draw.py`, `stats.py` and `run.py`):

58. **The freeze entry's broken-task fields.** The manifest's `broken_tasks`, required at a study level, records the
    published-list check (`published_list`: `{"found": false}`, or `{"found": true}` with its `source`, its `file`
    and the file's `sha256`) and the task screen (`screen`: `{"on": false}`, or `{"on": true}` with its `screener` and
    its `record`). A screener who is also the audit's named `auditor` is refused. The entry is copied into the results.
    For the coordinator's ruling.

59. **A published list's IDs.** Its format is not known in advance, so every pool ID its file holds as a whole ID
    (not as part of a longer one: bounded by characters other than letters, digits, underscores and hyphens) is
    excluded, and the file is read only when its SHA-256 is the one the entry records. The draw's caller adds these
    IDs to the excluded ones (`broken.published_ids`, then `draw.order`), and the entry point refuses a manifest
    holding a task on the list. For the coordinator's ruling.

60. **The task screen and the replacement.** The screen's record is `{"screener", "tasks": {task_id:
    {"label_invalid", "test", "sentence", "weak_tests"}}}`; a task marked label-invalid names the test and the
    behaviour in one sentence. The record names hidden tests, so a study level reads it only from inside the vault,
    and only when it is by the screener the entry names; its digest joins the calibration's inputs. Calibration lists
    a label-invalid task for replacement beside the tasks that fail the oracle controls, with the reason "the task
    screen: <test>: <sentence>", and lists the tasks with weak tests, which are kept. `draw.replacements` logs each
    replacement with the next task in order not already taking another's place. For the coordinator's ruling.

61. **The label audit's kinds.** The audit's record (the manifest's `audit`) is `{"auditor", "audited",
    "label_errors", "errors"}`, with one entry per label error: its `task_id`, its `kind` (1 to 4, or "other") and the
    auditor's `test`. A patch is label-invalid when its error is of kinds 1 to 3 and the run labelled it defective. An
    error of kinds 1 to 3 on a patch not labelled defective cannot make it label-invalid: it is counted as a label
    error, its patch is kept, and it is reported as `inconsistent`. Every label error, label-invalid ones included,
    counts toward the audit's bound. The kinds are fixed before the results are unblinded because the audit is an
    input to `run`, hashed with the manifest into the results, and `run` is the step that writes the labels beside
    the arms' outputs. Open: the auditor compares a judgement with the label, and the entry point computes the labels
    only within `run`. Giving the auditor the labels without the arms' outputs needs a label step inside the vault
    before `run`, which the scripts do not have. For the coordinator's ruling.

62. **Counting after exclusions.** Label-invalid patches are left out of every primary rate, the per-repository rates,
    detection by category, every hypothesis and comparison with the agent's own review, and so verdict 3, which
    counts the primary analysis's defective patches. They are counted and listed. Tallies, costs and the feasibility
    criteria still cover every non-empty patch, since the protocol's counting after exclusions names only the primary
    rates, the hypotheses and verdict 3; so the criterion that G6 gave both results still reads a label-invalid
    patch's label. The sensitivity analysis that counts
    empty patches as defective leaves label-invalid ones out, and a second one keeps them as labelled: every arm's
    rates and the three hypotheses, reported and deciding nothing. `stopping` counts labels before the audit, with
    label-invalid patches. The results report the audit's summary: its errors by kind, the label-invalid patches and
    the number of errors of the fourth kind. Without an audit, no patch is left out, and the results say the labels
    were not checked for broken tasks, with OpenAI's estimate. For the coordinator's ruling.
