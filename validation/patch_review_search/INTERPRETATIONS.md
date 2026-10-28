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
    is not "before its first model call": a tool error, never retried. Ruled by the coordinator: accepted.

26. **C and T in a child process.** At a study level the driver refuses arms C and T unless each is a child arm
    (`ChildArm`, `children_only`). Each run is a fresh `python -I` interpreter started through G6's
    `component_process.run_process`, in a call window of the arm's wall-clock limit (V9's arm 3's) plus 60 seconds
    (V9's `KILL_GRACE_S`), and killed with its process tree when the window ends. A killed run is a tool error of the
    kind `time_out`, the kind arm E gives a time-out, marked as shaped by the limit; V9's arms 3 and 4 call the same
    case `killed_at_time_limit`. The child's meter is a file it appends one line to per call, so the driver reads what
    it counted before the kill; the child is told what was left of the arm's share when the run began. The child gets
    the study's environment without the research flag, as V9's arms do. An operator stop is never caught. Arm E runs
    in the driver's own process: each of its calls and runs has its own time limit inside the arm's. The hosted
    interface VS-5 builds must enforce its own time-out on each call; a watchdog in the driver for arm E is revisited
    at VS-5. Ruled by the coordinator: accepted; V10's arms use `time_out`, as arm E does, and telling the child what
    was left of its share is accepted.

    The kinds map across the two studies as follows: V10's `time_out` for a run stopped at its limit is V9's
    `killed_at_time_limit`.

Items 27 to 33 cover the analysis of the primary measures (`analysis.py`).

27. **The analysed patches.** V9's non-empty, labelled tasks, paired by task across every arm. An arm with no
    disposition on one of them does not accept it, and is counted as missing. Unlabelled tasks, and the patches the
    record excludes, are counted and left out of every rate. Ruled by the coordinator: accepted as written.

28. **The exact interval.** The two-sided 95% Clopper–Pearson interval is built from V9's one-sided upper bound at
    97.5%: the upper limit for the count, and one minus the upper limit for the complementary count. Ruled by the
    coordinator: accepted as written.

29. **Actionable findings, and findings per patch.** A finding is actionable when the reruns before the labels found
    it reproducible and the patch is defective. The compatible and not comparable counts are over the actionable
    findings, from the reruns on the reference fix, with no interval: the shares over findings, with their bootstrap
    over tasks, come with the secondary measures. The findings per patch are reported over every analysed patch: how
    many patches have any, their mean, and the most on one patch. Ruled by the coordinator: accepted as written.

30. **Measure 3 for V9's arms.** Arm 2 gives no finding, by the protocol's table of findings, so its discovery is 0 and
    its differences on measure 3 are computed. The findings of arms 1 and 4 are not rerun by V10's driver, so their
    discovery, and every difference on measure 3 that involves them, is reported as not computed. Ruled by the
    coordinator: accepted as written.

31. **The bootstrap.** V9's stratified bootstrap, given a 95% confidence (a new argument; V9's 90% stays its default
    and was checked identical to the old one in 200 random cases), with seed V9's feasibility seed plus two for every
    comparison: the same resampled task sets serve every comparison on the same patches. Comparisons on the defective
    and on the correct patches resample different task sets, since their patches differ. Ruled by the coordinator:
    accepted as written.

32. **Cost.** Arms C, T and E: the meter's calls and cents, and the driver's wall-clock and processor seconds for the
    run. Arm A: V9's arm 3 seconds, processor seconds and model calls; its cents are not reported (with search off it
    makes no model call) and are counted as missing. Dollars are cents divided by 100. Input and output tokens wait for
    VS-5's meter, and are named as not built. Ruled by the coordinator: accepted as written.

33. **Arm A's findings.** Arm A's findings are its failed stated checks. The driver reruns those the patch carries
    (`arm_a_findings`, as reproductions built from V9's arm 3 reply by the study's runner, which is not built yet)
    with the other arms' findings, so arm A's discovery and its K, K_ref and J are computed the same way. Ruled by the
    coordinator: accepted as written.

Items 34 to 37 cover the feasibility criteria (`analysis.feasibility`).

34. **Criterion 1** is read over every non-empty patch, labelled or not. An arm with no disposition on one fails it,
    as does a tool error rate above 10% (V9's limit, `TOOL_ERROR_LIMIT`). Ruled by the coordinator: accepted as written.

35. **Criterion 2.** For each search arm and arm E: its metered cents over the study are at most its share (a third of
    the cap), its most calls on one patch at most the call cap, and its longest run at most the arm's limit plus the
    grace its child is killed after. Runs stopped at a limit are counted by kind (`time_out`, V9's
    `killed_at_time_limit`, `spend_share_reached`, or another disposition marked as shaped by a limit). Without the
    frozen limits, the criterion is inconclusive. Ruled by the coordinator: accepted as written.

36. **Criterion 3** reads, for each search arm and patch, whether its search stage ran a check to a pass or a fail
    (`search_stage.ran_to_result`, the field VS-5 settles), over every non-empty patch; an arm with no output on a
    patch did not. When any reply does not say, the criterion is inconclusive. "At least half" is twice the count at
    least the number of patches. Ruled by the coordinator: accepted as written.

37. **Criteria 4 and 5, and the outcome.** Criterion 4 waits for the report check of arms C and T, and criterion 5 for
    the frozen comparison of stated-check results: both are reported inconclusive, criterion 5 with the patches its
    list must hold (each search arm's own review accepted, arm A did not). Criterion 6 is read from V9's oracle
    controls for every non-empty task. The study is feasible only when all six are met, not feasible when any is not
    met, and inconclusive otherwise. Ruled by the coordinator: accepted as written.

Items 38 to 41 cover the headline sentences (`analysis.headlines`).

38. **N and R.** N is the number of analysed patches (non-empty and labelled), and R the number of their repositories.
    Ruled by the coordinator: accepted as written.

39. **How the numbers are written.** The sentence text is the protocol's exactly, numbers filled in; the second
    sentence has no closing full stop in the protocol, and has none here. Percentages are written to one decimal place,
    model calls and seconds to one decimal place, dollars to two. Each sentence's intervals are given beside it
    (`a% to b%`), not inside the sentence, so its text stays the protocol's. Ruled by the coordinator: accepted as
    written.

40. **Arm C beside arm T.** Arm C's numbers (its K, K_ref and J; its false and correct acceptance; its costs) are given
    in an entry labelled as arm C's beside each sentence, never as a sentence of their own and never in arm T's
    place. Ruled by the coordinator: accepted as written.

41. **A sentence not printed.** The first when M is zero (the protocol's rule) or L is zero (J would have no
    interval); the second when any of arm T's and arm A's four rates has no interval, so neither pair is used without
    the other; the third when arm T's costs were not recorded. Each is listed with its reason. Ruled by the coordinator:
    accepted as written.
