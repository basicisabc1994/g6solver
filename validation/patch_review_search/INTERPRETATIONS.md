# V10: how the scripts read the protocol

Where `docs/validation/patch_review_search/PROTOCOL.md` (version 1, registered on the verification search branch)
leaves a mechanic open, the scripts in this folder read it as below. A reading is marked for the coordinator's ruling
until it is ruled, and then carries the ruling. Items 1 to 14 cover arm E's run mechanics (`arm_e.py`); the model call,
E's prompt and the repository context wait for verification search's proposer interface (VS-5).

1. **R, the runs of each kept test on each tree.** The protocol sets R to the number of times G6 runs a proposed
   check on the patched tree. G6's verification search, on the branch as it stands (`search` in
   `src/g6/harness/verification_search.py`), runs each proposed check once on the patched tree and once at the base
   commit's code, so R would be 1, and "passes in more than half of its runs" means "passes in its one run". The script
   takes R as a frozen parameter, read from G6's frozen commit when it is chosen (`g6_parameters`: the calls with
   `at_base=False` in `search`'s loop over the checks; any other shape is refused). Ruled by the coordinator: accepted
   as written.

2. **The reply format.** A reply is a JSON object `{"files": [{"path": ..., "text": ...}]}`. Anything else is a reply
   the frozen script cannot parse: a tool error. Ruled by the coordinator, provisionally: VS-5's frozen prompt fixes
   E's reply format, and it mirrors G6's proposer's format for free-form checks where it can, so parse failures are
   comparable.

3. **Which files are kept, and where they go.** The protocol keeps "only files named `test_*.py`, placed where G6
   places free-form checks". The script applies G6's whole rule for what a check file may be, which is narrower: the
   name is `test_` followed by letters, digits and `_` only (`test-a.py` is discarded); never a conftest, an
   `__init__.py` or another configuration file; a relative path with no `..`, no empty part and no folder G6's copy
   leaves out or that holds installed code; and text UTF-8 can hold, at most 64 kilobytes of it (G6's limit per
   check). A kept file goes in a folder of its own, `g6_check_` and the first 12 hexadecimal digits of the SHA-256 of
   its text, inside the folder its path names. It is discarded when a link or a file is in the way, or that folder
   already exists: G6 checks this on the tree under review, and the script checks it on the base commit's tree too,
   since E runs there as well. A second file with the same path and text is discarded. Ruled by the coordinator,
   amended: G6's 64 kilobyte limit per check file applies as well. The protocol borrows G6's 20-check limit, so E
   mirrors G6's whole rule for what a check file may be, size included; otherwise the comparison partly measures what
   each arm is allowed to write rather than who checks.

4. **What a test function is.** The test functions are those pytest collects by default, read from the file's syntax
   tree in file order: module-level functions whose names begin with `test`, and methods whose names begin with `test`
   in classes whose names begin with `Test`. A repository's own pytest settings for these names are not read. A file
   with none is discarded. A file that cannot be parsed is kept as one test, which will not pass (it cannot be
   collected). When the limit of 20 is reached inside a file, its first functions are kept and the rest discarded,
   each counted. Ruled by the coordinator: accepted as written.

5. **Each kept file runs on its own.** Each run is one pytest run of one kept file in a fresh copy, as G6 runs each
   check in a copy of its own. Run together, a file that cannot be collected would stop every kept test, and two kept
   files with the same name in different folders could not both be imported. A kept file runs whole; only its kept
   tests are read. Ruled by the coordinator: accepted as written.

6. **A kept test's outcome in one run.** Read from V9's outcome plugin, per phase:
   - `failed` when its body failed (an assertion or an exception raised there): the kind that makes a finding;
   - `error` when it could not be collected or set up, a teardown failed, the run did not finish within its limit, or
     pytest stopped before its tests;
   - `skipped` for a skip or an expected failure;
   - `missing` when nothing was recorded for it;
   - `passed` otherwise.

   A parametrised function passes in a run only when every case passed, and failed when any case failed in its body.
   A test passes when it passed in more than half of its runs; it is a finding when it failed in more than half; it
   ends in an error when it errored in more than half. A skipped or missing test does not pass, and it is neither a
   finding nor an error. Ruled by the coordinator: accepted as written.

7. **The kind of not accepted.** When arm 1 accepts and a kept test does not pass, E's category is `defect_found` if
   any kept test is a finding, and `insufficient_evidence` otherwise. When arm 1 does not accept, E takes arm 1's
   category. When arm 1 has no disposition (a tool error), E's disposition is a tool error of its own kind,
   `arm_1_without_disposition`, so it counts among E's tool errors. A-and-E reads arm A the same way
   (`arm_a_without_disposition`). Ruled by the coordinator: accepted as written.

8. **No test kept.** A parsed reply with no test file, on every call, gives E arm 1's disposition, counted as
   `no_test_file`. Replies that held files, none of them kept, also give arm 1's disposition (no kept test can fail),
   counted apart as `none_kept`. Ruled by the coordinator: accepted as written.

9. **Spending.** Before each call the script asks whether E's share of the spending cap still allows one. A patch on
   which the share runs out before E's due calls are made is a tool error (`spend_share_reached`), whether the patch
   was reached after the share was spent or the share ran out during it. Ruled by the coordinator: accepted as
   written.

10. **Time.** The arm's wall-clock limit (V9's arm 3's) covers E's calls and runs: each call gets the time left, and
    each run the smaller of its own limit and the time left. The arm reaching its limit is a time-out, a tool error
    marked as shaped by the limit. A call that times out on its own is a time-out too, marked only when the arm's
    limit was reached. Each run's own limit is read from G6's frozen commit, G6's per-run limit for a proposed check
    (`RUN_S`, by `g6_parameters`), like R. Ruled by the coordinator, amended: each run's own limit is read from G6's
    frozen commit (G6's per-run limit for a proposed check), like R. It is not a separate 60-second constant.

11. **Feedback.** With feedback, the tests kept so far are run before each call after the first, R times each on the
    patched tree, and the request carries each test's outcomes. Those are their R runs; no test runs twice. Without
    feedback, every call comes first and no request carries results. What G6's proposer receives, and in what form,
    waits for VS-5. Ruled by the coordinator: accepted as written.

12. **The seed.** The frozen seed is always recorded. It is sent in the requests only when the freeze says so
    (`send_seed`). Ruled by the coordinator, amended: E's script makes no random choice. The seed is sent to the model
    only if the hosted interface takes one and G6's proposer sends it (VS-5 decides); otherwise it is recorded and not
    sent.

13. **E-filtered.** A kept test "fails at the base" when it failed in its body in more than half of its base runs,
    and "ends in an error" when it errored in more than half of its runs on the patched tree; those are set aside. A
    skipped or missing test is not set aside. A tool error of E is also E-filtered's and A-and-E's. Ruled by the
    coordinator: accepted as written.

14. **The call count.** The larger of arms C's and T's calls on the patch, at least one, and never above the frozen
    model-call cap, which C and T share. Ruled by the coordinator: accepted as written.

Items 15 to 24 cover the study driver (`driver.py`), the reruns (`reruns.py`) and the freeze-readiness check
(`freeze_check.py`).

15. **C's and T's search configurations.** C is `{"templates": false}`: free-form checks only, with test adequacy
    off (G6's default). T is `{"templates": true, "test_adequacy": true}`: test adequacy is on for T because the
    protocol's test adequacy measure (arm T) reads its mutation runs; in this version of G6 test adequacy changes no
    outcome. G6's search on its branch has no setting of its own for the formal path: templates use it. Each arm gets
    its configuration as data, a fresh copy on every call. Ruled by the coordinator: accepted as written.

16. **A run that failed from infrastructure before its first model call.** The arm says so by raising
    `InfrastructureFailure`. The run is retried once, and the retry is logged in the record and the report. A second
    such failure is a tool error (`infrastructure_before_first_call`). Any other exception, or a reply without a
    disposition, is a tool error and is not retried. Ruled by the coordinator: accepted as written.

17. **Spending.** (What an arm is charged is now its meter's count, item 25; what it reports is a cross-check.) The
    cap is in cents, and each of C, T and E has a third of it. Each reply says what the arm spent
    on the patch (`spent_cents`), priced by the study script from the provider's token counts and the frozen price
    list, which wait for VS-5. An arm whose spending reaches its share has none left. What an arm reports spending on
    the patch where its share ran out is charged in full. Ruled by the coordinator: accepted as written.

18. **C's and T's dispositions.** Their own review's tool error is theirs. When arm A has no disposition (a tool
    error), they have none either: a tool error of the kind `arm_a_without_disposition`. When arm A does not accept,
    they take arm A's category; when it accepts, their own review's. Ruled by the coordinator: accepted as written.

19. **E's calls after a tool error of C or T.** E gets the calls C and T made (none for an arm that was not run or
    failed before its first call), so E makes at least one. Ruled by the coordinator: accepted as written.

20. **Reruns.** A reproduction is `{file, text, node}`: the test file's path, its text and the node id to run. For C
    and T it is the file and text G6 reports in its next actions; VS-5 must give the text and the node id in that
    form. A rerun reads its outcome as arm E reads a run: a failure in the body fails, a pass passes, and anything else
    (it could not be collected, set up or finished in time, it was skipped, or nothing was recorded) is an error. A
    reproduction that cannot be run that way is not reproducible and is not run. The reruns start once every arm has
    run on every patch. Ruled by the coordinator: accepted as written.

21. **Attribution to search.** The reason codes are read with or without their stage (`evidence:unconfirmed_failure`
    or `unconfirmed_failure`). Whether the deadline was reached during the search stage is what the arm reports
    (`deadline_in_search`), from G6's report once VS-5 says where it is. Ruled by the coordinator: accepted as written.

22. **The record.** Each arm's output on each patch, each retry and each rerun result is one entry. An entry carries
    the SHA-256 of its data (JSON with sorted keys) and a chain hash, the SHA-256 of the previous chain hash and its
    own, and is written as a JSON line to a file that must be new. The final chain hash is the record's; committing it
    to G6's public repository happens outside the driver. Ruled by the coordinator: accepted as written.

23. **Draw order and inputs.** The driver takes the non-empty patches V9 reviewed, each with its draw index and arm
    A's disposition (V9's arm 3 result); empty and non-applying patches are left out of the arms, as V9 handles them,
    and handed to the driver as `excluded` (`{id, draw_index, reason}`, the reason `empty` or `not_applying`). Each is
    recorded with its reason before any arm runs and counted in the report. V9's own flow keeps them too: its
    `_review_task` (`validation/patch_review_utility/run.py`) gives each task's row the state `trees.apply` returns
    (`patch`: `empty`, `not_applying` or `non_empty`), and an empty or non-applying task's row stays in V9's results.
    Patches out of draw order, an excluded patch without a draw index of its own, or one with another reason, are
    refused before anything runs. Ruled by the coordinator: accepted, with one condition: empty and non-applying
    patches are removed before the driver only if each one is counted and reported in the study's record, with its
    reason, never dropped silently; if V9's flow already counts them, cite where.

24. **The freeze-readiness check's groups.** Every feature the scripts detect or mirror belongs to one group: `base`
    (what G6's frozen review at b3c342b gives), `pool` (the pool-compatibility change) or `search` (verification
    search). All are required unless the check is told fewer; a parity fixture is required with its group. A group not
    required is reported, never a failure. A tree from the pool-compatibility branch alone (8b5364c) has no
    verification search, so it can never pass with every group required. Ruled by the coordinator: accepted as written.

25. **Spending is metered as it happens** (the coordinator's change to item 17). Every arm run on a patch gets a
    fresh meter, which counts calls, characters and cents as each model call happens; VS-5's MeteredCaller will be
    that meter. The driver charges the arm what the meter counted, read after the arm returns and after it raises, so a
    run that fails after its calls is charged for them, and the next patch sees the reduced share. While the arm runs,
    its share counts what the meter has counted so far. What the arm reports (its spent cents and its calls) is a
    cross-check: a difference in either is logged in the record and the report, and the meter is what is charged. The
    arm's model calls, and so E's call count, are the meter's. An infrastructure failure after the meter counted a call
    is not "before its first model call": a tool error, never retried. For the coordinator's ruling.

26. **C and T in a child process.** At a study level the driver refuses arms C and T unless each is a child arm
    (`ChildArm`, `children_only`). Each run is a fresh `python -I` interpreter started through G6's
    `component_process.run_process`, in a call window of the arm's wall-clock limit (V9's arm 3's) plus 60 seconds
    (V9's `KILL_GRACE_S`), and killed with its process tree when the window ends. A killed run is a tool error of the
    kind `time_out`, the kind arm E gives a time-out, marked as shaped by the limit; V9's arms 3 and 4 call the same
    case `killed_at_time_limit`. The child's meter is a file it appends one line to per call, so the driver reads what
    it counted before the kill; the child is told what was left of the arm's share when the run began. The child gets
    the study's environment without the research flag, as V9's arms do. An operator stop is never caught. Arm E runs
    in the driver's own process: each of its calls and runs has its own time limit inside the arm's. For the
    coordinator's ruling.
