# V10: how the scripts read the protocol

Where `docs/validation/patch_review_search/PROTOCOL.md` leaves a mechanic open, the scripts in this folder read it as
below. Items 1 to 44 were written against version 1, registered on the verification search branch. Items from 45
read version 2, registered in commit 683308d on the `claude/v10-amend` branch, whose file has the SHA-256
`715dce8a8167d8c1efb7abb7d5b35f8831caee5317b4b8b230d9b805d77852b4`; it depends on V9's protocol, version 2, SHA-256
`39a7859e9e44e852328ad545aa440b12849ac5b281c20d9b41eb65e6f58a0000`. Version 2's numbers and names (arm E's run
count n, the repositories P) replace version 1's in every item. A reading is marked for the coordinator's ruling
until it is ruled, and then carries the ruling. Items 1 to 14 cover arm E's run mechanics (`arm_e.py`); the model call,
E's prompt and the repository context wait for verification search's proposer interface (VS-5).

1. **n, the runs of each kept test on each tree.** The protocol sets n to the number of times G6 runs a proposed
   check on the patched tree. G6's verification search, on the branch as it stands (`search` in
   `src/g6/harness/verification_search.py`), runs each proposed check once on the patched tree and once at the base
   commit's code, so n would be 1, and "passes in more than half of its runs" means "passes in its one run". The script
   takes n as a frozen parameter, read from G6's frozen commit when it is chosen (`g6_parameters`: the calls with
   `at_base=False` in `search`'s loop over the checks; any other shape is refused). Ruled by the coordinator: accepted
   as written.

2. **The reply format.** A reply is a JSON object `{"files": [{"path": ..., "text": ...}]}`. Anything else is a reply
   the frozen script cannot parse: a tool error. Ruled by the coordinator, provisionally: VS-5's frozen prompt fixes
   E's reply format, and it mirrors G6's proposer's format for free-form checks where it can, so parse failures are
   comparable. Ruling (coordinator, 2026-10-29): a reply that holds exactly one fenced block whose content parses as
   the registered object is read, prose before or after it ignored; two or more fenced blocks, or none that parses,
   are still a counted tool error; there is no re-ask, which would change E's call count and budget; the frozen prompt
   is unchanged. Version 2's text says only that "a reply the frozen script cannot parse is a counted tool error for
   E", so the ruling applies. Each exchange records its reading (`json`, `fenced` or `fenced_with_prose`, `arm_e.read`)
   and E's counts total them. In the paid rehearsal's 14 replies: 13 `fenced`, 1 `fenced_with_prose`, none bare.

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
    (`RUN_S`, by `g6_parameters`), like n. Ruled by the coordinator, amended: each run's own limit is read from G6's
    frozen commit (G6's per-run limit for a proposed check), like n. It is not a separate 60-second constant.

11. **Feedback.** With feedback, the tests kept so far are run before each call after the first, n times each on the
    patched tree, and the request carries each test's outcomes. Those are their n runs; no test runs twice. Without
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

Item 37's criterion 4 is now computed: see item 42.

Items 38 to 41 cover the headline sentences (`analysis.headlines`).

38. **N and P.** N is the number of analysed patches (non-empty and labelled), and P the number of their repositories.
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

Items 42 to 44 cover the report check (`report_check.py`), the comparison of stated-check results
(`stated_checks.py`) and the study runner's inputs to the driver (`runner.py`).

42. **The report check and criterion 4.** V9's A5 check is reused for the three parts. The completion section's parts
    must be present in the form G6 gives them: lists, and `defects_reproduced` with its two lists. Test adequacy must be
    present as a list for both search arms: the protocol's "where the arm measures it" cannot be told from an empty
    list. A check a model originated is any whose provenance is neither `stated` nor `derived` (`proposed`,
    `adopted_proposal`, and `structural`, a template filled from a model's names). In a search arm's report, a check
    with no provenance fails the report (`checks_without_provenance`). The formal results read are the `formal` parts
    of activities and defects; a requirement's `formal` notes are G6's notes that a result is about the model only.
    The driver checks a search arm's report when its result carries it (`report`), against the patch's
    `contract_ids`, and a report that fails is a tool error of the kind `report_check_failed` (V9's A5 failure is
    `a5_report_incomplete`). Criterion 4 is met when every search arm's output carries a report check and none lists a
    model-originated check as passing a requirement or a formal result without its three parts; a report short of
    another part is a tool error, counted by criterion 1. While the outputs carry no report check, criterion 4 is
    inconclusive. Ruled by the coordinator: accepted, with one change: in a search arm's report, a check with no
    provenance fails the report check (a tool error, `report_check_failed`), since the verification search commit
    gives every check a provenance and its absence is a G6 fault; in arm A's record (V9's arm 3), a check with no
    provenance stays a stated one. Counting `structural` as model-originated for criterion 4 is accepted: a
    structural check never passes a requirement (the revised plan rule 2).

43. **The comparison of stated-check results and criterion 5.** A result on the stated checks is each stated check's
    verdict by its id, as G6's reply gives it (V9's `arms.stated_check_verdicts`: the checks whose provenance is
    `stated`, or that carry none). Arm A's comes from V9's arm 3, which now keeps it (`stated_checks`, with the defects
    it reproduced, in V9's details for arm 3), through the patch's `arm_a_stated_checks`; each search arm's from its
    report. Two results differ when any check's verdict differs or a check is in one and not the other. The driver
    compares them on every patch once every arm has run, and records the comparison before any label is read.
    Criterion 5 is met when every patch a search arm's own review accepted and arm A did not is on the list,
    not met when one is not, and inconclusive when any patch's results cannot be read, or the record holds no
    comparison. Ruled by the coordinator: accepted.

44. **The runner's inputs, and arm A's findings.** The driver's patches are V9's non-empty tasks in the order V9's
    results list them (its draw order), each with arm 3's disposition as arm A's, the review contract's ids, arm 3's
    results on the stated checks and arm A's findings; V9's empty and non-applying tasks are the excluded patches, and
    any other patch state is refused. Arm A's findings are its failed stated checks (provenance `stated`, code
    `check_failed`) and the regressions of the repository's own tests G6 ran (provenance `derived`, code
    `derived_test_regressed`), and so are C's and T's. Each finding's reproduction is the check's or test's node, its
    file, and that file's text on the patched tree V9 reviewed, rerun with V9's arm 1 command; one whose file is not on
    that tree is kept with no text, so the reruns count it as not reproducible. A reply with no completion section
    (G6's review before verification search, as at b3c342b) gives none. Ruled by the coordinator: changed: a
    `derived_test_regressed` (a regression of the repository's own tests that G6 ran, outcome `defect_detected`) is
    one of arm A's findings, and so C's and T's; its reproduction is the repository test's node id and the frozen
    test command. V9's arm 1 counts the same evidence, and it cannot inflate the headline (M needs arm 1 to accept).
    V10 version 2's findings table says so. The rest of item 44 is accepted.

Items from 45 cover version 2 of the protocol: arm R (`arm_r.py`), its place in the driver and the measures, and
V9 version 2's broken tasks as V10 inherits them. The version 2 items of both studies share one numbering for ruling:
this file's 45 to 57 and 63, and V9's 58 to 62 (`validation/patch_review_utility/INTERPRETATIONS.md`).

45. **Where arm R writes its tests.** G6 places a free-form check in a folder of its own, `g6_check_` and the first 12
    hexadecimal digits of the SHA-256 of its text, inside the folder its file names (`src/g6/harness/
    verification_search.py` at f786774: line 223, `FOLDER = "g6_check_"`, and lines 350 to 354, `placement`). That
    folder depends on the test's text, so it cannot be named before the test is written. R's prompt therefore names
    one folder of that form at the copy's top, `g6_check_review`, which no repository holds; when R's kept tests run,
    each file is placed as arm E's are, by `placement`.
    Ruling (coordinator, 2026-10-29): accepted as written.

46. **How a launcher enforces R's session set-up.** The script records the set-up (`SESSION`), and the gaming scan
    checks the transcript; the launcher, the owner's or an approved one, enforces it: the session starts with a fresh,
    empty configuration folder of its own (no saved settings, memory, plugins, skills or hooks, and no instruction
    files from the person's own configuration), an empty tool-server configuration that the product is told to keep to
    strictly, and the copy as its working folder inside a fresh temporary folder (so no instruction file above the copy
    is read); the commands it runs have no network, by a sandbox or container whose only outbound connection is to the
    model provider. The launcher writes what it did into the session record, beside the transcript. For the
    coordinator's ruling.
    Ruling (coordinator, 2026-10-29): accepted. The launcher writes what it did into the session record, and the
    record also states the model, the Claude Code version and that the configuration folder was empty as checked at
    the start.

47. **The diff R gets.** The prompt is V9's arm 2 prompt with only the two registered changes, so the diff is not
    added to it: the launcher gives it to the session beside the prompt (`prepare`'s `diff`), as V9's arm 2 session
    has the patch in its working tree.
    Ruling (coordinator, 2026-10-29): accepted as written.

48. **R's copy and the history check.** The copy is a new repository into which the base commit alone is fetched
    (`git fetch --depth=1` of that commit), checked out detached, with the patch applied to the working tree and not
    committed. The history check (`history_problem`) holds when `HEAD` is the base commit, every reference names it,
    and the only commit object in the copy's store is the base commit. A full clone fails it. This check is the one the
    protocol's history rule needs for every copy arms C, T, E and R use; only R's copy is made here. For the
    coordinator's ruling.
    Ruling (coordinator, 2026-10-29): accepted. The same history check must hold for the copies arms C, T and E use
    before the study's first run: a freeze-readiness item (a copy that fails it is refused, never run).

49. **Collecting and keeping R's tests.** Every file in R's folder, at any depth, in path order, is collected before
    the copy is discarded; a test file outside the folder is never collected. They are then kept as arm E's are
    (G6's rule for a check file, 64 kilobytes at most, at most 20 test functions in file order), and everything else is
    counted.
    Ruling (coordinator, 2026-10-29): accepted as written.

50. **R's cost.** The session record gives its model and its usage, the number of each kind of token the session
    reports. Each kind is priced in dollars per million tokens by the frozen price list for that model; a model or a
    kind of token the list does not price is refused, and a record with no usage is not priced and is counted. For the
    coordinator's ruling.
    Ruling (coordinator, 2026-10-29): accepted. An R record with no usage is also listed in the verdict's report as
    not priced, never shown as zero dollars. The launcher's own dollar meter is the stop; the record's pricing is
    the accounting.

51. **The gaming scan of R's transcript.** V9's scan is reused: vault reads, network use from a command, shortcuts, and
    edits to test or runner files (its edits to its own tests' folder excepted). Every git command R ran is listed;
    the history check, not the scan, keeps the upstream fix out of reach. The set-up is checked from the transcript's
    first record: a tool server connected, or a tool one gives, is a `session_set_up` finding. An edit outside R's copy
    is an `edit_outside_copy` finding. The findings that void the study stay V9's (`vault_read`, `network`); whether
    a `session_set_up` finding voids it too is for the coordinator's ruling. V9's scan now reads a Windows session's
    paths too: an edit to a test or runner file named with backslashes was missed before. For the coordinator's
    ruling.
    Ruling (coordinator, 2026-10-29): a session_set_up or edit_outside_copy finding voids that arm R session: R's
    result on that patch becomes a counted tool error, the finding is reported, and the study is not voided. The
    findings that void the study stay V9's (vault_read, network). The Windows backslash fix is accepted.

52. **Arm R in the driver.** Arm R runs after E on each patch, all four arms on one patch before the next, as an
    injected callable, `arm_r(patch, *, share_left, meter)`: the study's runner prepares the session, an approved
    launcher runs it, and the runner judges it and prices its tokens into the meter, so R is charged as the other arms
    are. When R's result carries the session's transcript, the driver scans it and keeps the scan with R's output in
    the record. Version 1's names follow version 2: arm E's run count is n, and the permitted sentence's number of
    repositories is P.
    Ruling (coordinator, 2026-10-29): accepted as written.

53. **Spending shares.** A quarter of the cap each for C, T, E and R, unless the freeze entry sets other shares: each
    arm's fraction of the cap, together at most the whole. The driver and criterion 2 read the same shares. For the
    coordinator's ruling.
    Ruling (coordinator, 2026-10-29): accepted as the default, a quarter each for C, T, E and R. The owner sets the
    cap and the shares at the freeze from the rehearsal's projection, which gives each arm its own estimate so the
    shares can follow it.

54. **Criteria 1 and 2 for arm R.** Criterion 1 holds R as it holds the other arms. Under criterion 2, R's spending is
    held to its share; a session past V9's arm 2 time limit or token cap abstains (or, with no reply, is a tool
    error), is counted as stopped at a limit (`killed_at_time_limit`, `stopped_at_token_cap`, or marked as shaped by
    a limit), and is not a failure of criterion 2. R has no call cap or wall-clock check of its own beyond these. For
    the coordinator's ruling.
    Ruling (coordinator, 2026-10-29): accepted as written.

55. **The filtered findings of E and R.** A finding of arm E or R is left out when its test failed in its body in more
    than half of that arm's own n runs at the base (the arm's record of its tests), not the reruns' three base runs.
    Measures 3 and 4 are then given again for E and R.
    Ruling (coordinator, 2026-10-29): accepted as written.

56. **The verdict.** It is decided only when the gaming scan's findings are given; the scans of R's transcripts the
    record holds are added to them. The findings that void the study are V9's (`vault_read`, `network`). History
    beyond the base never reaches a session: the copy is refused before it is made (item 48). R's `session_set_up`
    and `edit_outside_copy` findings are reported with the others but do not decide verdict 1 by themselves, and no
    script decides "could change its evaluator": both are for the coordinator's ruling. Verdict 2 is
    feasibility criterion 6 alone. V9's feasibility label audit is reported beside it: its sample, label errors, errors
    by kind, label-invalid patches, and the one-sided 95% Clopper–Pearson upper bound on its label error rate. Every
    git command R ran is reported beside it too.
    Ruling (coordinator, 2026-10-29): a session_set_up or edit_outside_copy finding voids that arm R session (a
    counted tool error, the finding reported), not the study; the findings that void the study stay V9's
    (vault_read, network). Whether an arm could change its evaluator is judged by the coordinator from the reported
    scan, written into the record before any label is read, never after.

57. **The version 2 sentences.** The second sentence gives arm R's rates beside A's, with all six intervals, and is
    not printed when any of arm T's, A's or R's four rates has no interval. The first sentence gives K_R, with its
    interval, and the note the protocol asks for beside K_R: that M is chosen by V9's arm 2, which uses R's model on
    nearly R's prompt. Similarity to the hidden tests, still not built, covers the checks C, T, E and R wrote. For the
    coordinator's ruling.
    Ruling (coordinator, 2026-10-29): accepted as written.

63. **V9's label-invalid patches in V10.** V10 reads V9's results, whose rows mark each label-invalid patch
    (`label_invalid`, from V9's label audit). Such a patch is left out of every V10 measure, as V9 leaves it out of
    its primary rates, and listed in the counts. A sensitivity analysis keeps it: every arm's false and correct
    acceptance, discovery and headline counts with V9's label-invalid patches kept as labelled. Feasibility counts
    every non-empty patch, as before, since its criteria do not read labels. The verdict reads V9's audit summary
    from V9's results unless another is given, and reports it beside the verdict with its one-sided 95% bound. For
    the coordinator's ruling.
    Ruling (coordinator, 2026-10-29): accepted as written.

Items from 64 cover the entry point for V9's feasibility level and V10 together (`study.py`, with `bindings.py`).

64. **One freeze file.** Every frozen parameter is in one file: V9's manifest is its `v9` section, and the file gives
    the level and G6's commit once, for both studies. The entry point refuses to start without that file, or when it
    names no G6 commit (40 lower-case hexadecimal digits); at the feasibility level V9's own checks apply and the arms
    run only from that commit, as this checkout's clean `HEAD`.
    Ruling (coordinator, 2026-10-29): accepted as written.

65. **The order, in steps.** `draw`, V9's `calibrate`, `prepare` (once the agent's patches exist), `arms`, `label`.
    V9's entry point ran its arms and its labels in one call; it is split into `review`, `label` and `results`, with
    `run` unchanged, so that V9's labels stay unread until V10's arms and reruns are recorded (V10's "Data", second
    condition). The record holds V9's rows first, then V9's gaming findings, then V10's driver entries; its final hash
    is written beside it (`<record>.final`) before any label is read. A record that exists already refuses a second
    run.
    Ruling (coordinator, 2026-10-29): accepted as written.

66. **The draw and the stopping rule.** Every pool ID that V7's or V8's split files hold as a whole ID is excluded,
    with the tasks seen while building verification search and a published list's IDs. V9's tasks must be the draw's
    first tasks, in order, less the tasks the freeze file logs as replaced, and at the feasibility level they must end
    where V9's rule ends them: at the 30th non-empty patch, the 45th task, or the end of the order. For the
    coordinator's ruling.
    Ruling (coordinator, 2026-10-29): accepted. The freeze-readiness check looks for the published broken-task list
    on OpenAI's own pages and records found true or false with the source; it is never guessed.

67. **The arms' bindings.** Arms C, T and E are callables the freeze file names. Until VS-5, stand-ins take their
    place behind the same interface; each raises, a counted tool error, and a study level refuses them. At the
    feasibility level C and T run each in a child process, as the driver requires; E runs in the study's process, as
    the driver has it. Each arm is given the patch with its text, its requirements, V9's arm 1 disposition and its
    trees, the inputs VS-5's callables are expected to need.
    Ruling (coordinator, 2026-10-29): accepted. Verification search's proposer has landed, so at the freeze C and T
    bind to verification search at the frozen commit (the launch's post-fix commit), not stand-ins; a stand-in at a
    study level stays refused.

68. **Arm R from its sessions.** The entry point never launches a session. A launcher leaves, for each patch, V9's
    arm 2 session record with R's model, its token counts by kind and its transcript's file name, beside it in the
    sessions folder. A patch with no record is a counted tool error ("no session record"); a record without its
    transcript leaves the scan of R's transcript not run, shown as none. R's cost is charged when its session is
    judged, after the session has run, so R's share cannot stop a session already run: the launcher has to keep R's
    sessions within R's share.
    Ruling (coordinator, 2026-10-29): accepted. Because R is charged only when judged, before each R session the
    launcher checks that the money left in R's share and in the whole cap covers that session's worst case, and
    starts none that could pass either.

69. **The labels step.** It runs only for a record that verifies against the hash the owner committed, when the vault
    and the calibration are those the arms ran with. Every reproducible finding is rerun on its task's reference fix.
    The gaming findings given to V10's verdict are those of V9's arm 3 (arm A) and the scans of R's transcripts; the
    scan of arms C, T and E's traces waits for VS-5's trace format.
    Ruling (coordinator, 2026-10-29): accepted as written.

Items from 70 cover the cost calibration (`calibration.py`), development calibration and never study data.

70. **What calibration runs on.** Only the `model.patch` files V7's archive manifest lists, each in its own task's
    folder and with the hash the manifest records. The manifest lists four: two are empty, so they get no review and
    are listed as not run, and two have content, both in the archive's `gold` folder, which may hold the benchmark's
    reference fixes rather than an agent's patches; their cost is measured all the same. The tasks file gives each
    task's repository, source, base commit and statement, since the script reads no dataset. The arms get no arm A or
    arm 1 disposition: calibration measures cost, not dispositions.
    Ruling (coordinator, 2026-10-29): the two non-empty patches in V7's gold folder are not used unless V7's archive
    manifest or runner code shows they are an agent's patches; benchmark reference fixes are never development
    material, and the patches are never opened. If they are reference fixes, or it cannot be told, the projection
    comes from the rehearsal and the proposer's smoke run. V7's runner shows what they are: its dry run with no
    model (`replay --task <id> --patch gold`) applies the benchmark's reference solution, and the archive's folder
    is that dry run's (`legacy/dryrun-2026-10-23/gold`). They are reference fixes and are not used.

71. **The hard dollar stop.** One cap over all four arms, not the study's quarter shares: no model call begins once
    the total spent reaches it, and no arm run starts after that. A call that begins below the cap may end above it.
    Arm R's sessions are launched outside the script and counted when they are judged, so the launcher has to keep
    them within the cap. One run's cap is at most 50 dollars; the script cannot know earlier development spend, so the
    50-dollar total across runs, and VS-5's 10-dollar smoke run first, are the owner's to keep. Arms C and T run in
    the script's own process, so a hung call can stall calibration until VS-5's caller enforces its own time-outs.
    Ruling (coordinator, 2026-10-29): accepted. Before each R session its worst case must fit both R's share and the
    whole cap (item 68's ruling).

72. **The cost record.** Per patch and arm: model calls, tokens by kind where the provider reports them (C's and T's
    reply `usage`, E's exchanges, R's session record), cents, dollars, and wall-clock and processor seconds; per arm,
    totals and means per patch. It is labelled development calibration, says it is not study data and that it
    informs the owner's spending cap only, and is written only to its output path, never to the freeze file or to
    anything of G6's.
    Ruling (coordinator, 2026-10-29): accepted as written.

Where the work stands: slices J (items 45 to 63, with V9's 58 to 62) and K (items 64 to 72) are complete, every item
awaiting the coordinator's ruling; the launch slice came next, and nothing of J or K was left part-done.

Items from 73 cover the session launcher (`launcher.py`), written for the paid rehearsal before the freeze.

73. **The launcher reuses V7's runner.** Each Claude Code session runs headless in a container on an internal network
    whose only way out is V7's proxy, which lets through the model's endpoint alone, so a session's own commands have
    no network, as V9 and V10 require. The container holds no saved memory, plugin, skill, hook, tool server or
    instruction file of its own, and `--strict-mcp-config` loads no tool server. Web tools are refused. The model,
    effort and caps are the rehearsal's (V7's runner's `claude-sonnet-5-5` at high effort, the candidate the registry
    records), since the freeze entry names V9's agent.
    Ruling (coordinator, 2026-10-29): the model is claude-haiku-4-5, not claude-sonnet-5-5, for the patch session,
    arm 2 (the same session, resumed) and arm R (a fresh session, the same model): the owner's choice for V9
    (2026-10-28), which the feasibility freeze entry names; the registry's Sonnet line is the V8 draft's candidate.
    Effort is Claude Code's default for that model, none set, recorded in each session record. The container, proxy,
    no-configuration and --strict-mcp-config set-up is accepted as written. Sessions that ran on Sonnet keep their
    records, labelled not the study's model, and are left out of the projection.

74. **Arm 2 and the patch session share one session.** The patch session's patch is captured as V7 captured it, and
    arm 2 resumes that same session (`--resume`) with V9's frozen review prompt, in the same container. Arm R is a
    fresh session in its own container, in R's copy, and the tests it writes are copied back for `arm_r.collect`. For
    the coordinator's ruling.
    Ruling (coordinator, 2026-10-29): accepted as written.

75. **The diff beside R's prompt.** Item 47 gives R the diff beside its registered prompt. The launcher gives it in
    Claude Code's appended system prompt, so the prompt R reads is the registered text word for word. For the
    coordinator's ruling.
    Ruling (coordinator, 2026-10-29): accepted as written.

76. **Tokens and endings.** A session's `tokens` counts every kind of token its usage reports, cache reads included,
    so arm 2's token cap must be set with that in mind. A session Claude Code stopped at its turn or budget cap ends
    as `token_cap`, the arm 2 rule's stop at a cap; a time-out as `timed_out`. The dollar stop counts a cache write at
    twice the input price, the higher published rate.
    Ruling (coordinator, 2026-10-29): accepted for the stop (a cache write at twice the input price). The cost
    record prices each kind at its published rate where the usage says which (a five-minute write 1.25 times input,
    a one-hour one twice), else twice, and the projection gives that figure. Arm 2's token cap is set at the freeze
    from the rehearsal's measured totals by kind, cache reads included.

Items from 77 cover the paid rehearsal (`rehearsal.py`), on development fixtures, before the freeze.

77. **The rehearsal's G6 arms and their model.** Arms C, T and E call `claude-haiku-4-5`, the first smoke run's model
    and V9's agent's, through `verification_proposer.StudyCaller`, with the smoke's proposer settings; each call's worst
    case is reserved on the rehearsal's ledger first and its usage charged after. C and T are V9's arm 3 rule
    (`arms.g6_review`) with their search configuration. E's prompt in the rehearsal (`E_SYSTEM`) is the rehearsal's:
    E's frozen prompt is the freeze entry's. For the coordinator's ruling.

78. **Every test run is confined.** In the rehearsal, V9's arm 1, the labels and the tests arms E and R wrote run with
    bubblewrap (`CONFINED`: no network, only the run's copy and its plugin folder writable, the home folder not bound),
    in the container where G6's process holds the key file; a key check before the first paid call showed the file out
    of reach of a confined test and of a check G6 runs. The study itself runs them in each task's image, as V9 item 17
    says. For the coordinator's ruling.

79. **What the rehearsal found and changed.** Five faults, each fixed test first: the fixtures had no `.gitignore`, so
    the agent's own pytest run left bytecode that V7's capture took as binary stubs git cannot apply (every fixture now
    ignores bytecode, as a real repository does); V9's reader of G6's model calls counted each of verification
    search's calls twice (it now counts the `model call` activities once each); arm E refused a reply the model had put
    in one Markdown fence (it now reads exactly one fenced block); an agent that committed its change left nothing
    for V7's capture against HEAD (the launcher now captures against the task's base commit); and the launcher
    charged a session the sum of its streamed messages' usage, whose output counts are placeholders, so every
    session's output went uncharged (a session is now charged its result record's usage; Claude Code's own
    `--max-budget-usd` had still held each session to its cap, and arm R's cost in V10's costs is read from the same
    record, so it is now right too). Two observations,
    unchanged: arm R's tests' folder also holds the bytecode of running them, which collection reads and keeping
    discards and counts; and a patch that adds no test leaves arm A, and so C and T, with insufficient evidence (V9
    item 1), so their findings come from the proposer alone. For the coordinator's ruling.
