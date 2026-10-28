# How the V9 scripts read the protocol

The protocol (`docs/validation/patch_review_utility/PROTOCOL.md`, version 1) leaves some details open. This file
records how the scripts in this folder read each one, and which reading the study's coordinator ruled on. The
feasibility freeze entry hashes this file together with the scripts; the entry point records its digest in every
results file. Changing a reading after a freeze changes the hash and is a deviation, logged as the protocol requires.

1. **Which checks a requirement cites.** The review contract carries no checks, and G6's review needs stated checks
   for each requirement. Every requirement cites the same checks: the test files the patch adds or changes (pytest's
   default names, `test_*.py` and `*_test.py`). A patch that adds or changes no test leaves G6 with insufficient
   evidence. The repository's existing tests reach G6 through its own run of the tests that touch the changed modules.
   Accepted by the coordinator.

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
    direct checks' own outcome stands when their deadline cut checks short (a run that timed out, or a check skipped or
    left unknown because the time budget was spent). Every disposition the time limit or the token cap shaped is
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

Two readings are mechanics rather than rulings, recorded so the hash covers them. The executor runs the files of the
test ids asked for, as the benchmark's own procedure runs test files, so a test id that no longer exists is missing
instead of stopping the whole run. When the executor's command runs a container, each run names its container, and a
run past its timeout stops the container itself by that name.
