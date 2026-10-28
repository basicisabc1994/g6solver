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
    by the coordinator.

17. **Where each arm runs.** At a study level, every arm runs in a fresh container of the task's own benchmark image,
    removed when it ends; the image is never changed and never fetched during a study. Inside the container the
    image's repository must be at the task's base commit, and the patch (for the labels, with the hidden tests) is
    applied there. Arm 1 and the labels run in the pristine image, with nothing of G6's mounted and the network
    closed: the repository's tests with the image's own interpreter, as its own continuous integration would run
    them. Arms 3 and 4 run in the same image with G6's pinned toolchain mounted read-only (a Python 3.12, G6 from a
    wheel built at the frozen commit, git 2.31 or newer, and bubblewrap; the images carry neither G6's Python nor,
    in ansible's case, a git G6 accepts), while the repository's tests still run with the image's own interpreter,
    named by `G6_REPO_PYTHON`. The toolchain's sources are pinned by hash in `environment/toolchain.json`, and a
    toolchain is used only when it matches its lock. Every task image is named by its digest, never by a tag. Ruled
    by the coordinator.

18. **One test environment per repository, and the image's pytest settings cleared.** Each repository's test
    environment is declared once in the manifest (qutebrowser's choice of Qt binding, for example) and is the same
    for arm 1, the labels and G6's arms: set in the container where the study runs the tests itself, and passed to G6
    through `G6_REPO_ENV`, which G6 applies inside its confined runs. It may not name a pytest, Python or library
    loader setting, `PATH`, `HOME` or a G6 setting, the names G6 refuses. Every container also clears the image's own
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
      has, so pytest-qt chooses it anyway. Its `DISPLAY` and Xvfb display are not used: the offscreen platform needs
      none.
    - openlibrary: no import roots: the repository's folder is on the import path for every arm, which is what the
      `PYTHONPATH=/app` of two of its four V7 run scripts gives. Test environment
      `OL_CONFIG=/app/conf/openlibrary.yml` (two of its V7 run scripts and Dockerfiles; the file is in all four
      images).
    Ruled by the coordinator.

Two readings are mechanics rather than rulings, recorded so the hash covers them. The executor runs the files of the
test ids asked for, as the benchmark's own procedure runs test files, so a test id that no longer exists is missing
instead of stopping the whole run. When the executor's command runs a container, each run names its container, and a
run past its timeout stops the container itself by that name.
