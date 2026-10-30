# Useful patch review: protocol, version 2 (registered, not executed)

Does G6 reduce the work of checking an AI-written Python patch, while accepting at most 5 percentage points more
defective patches than the repository's own tests do? The margin is set in advance; the hypotheses below give the
exact tests.

This protocol is registered in `docs/validation/REGISTRY.md` (entry V9) before any code for the scoped assessment is
written and before any evaluation patch exists. Version 2 changes only how broken benchmark tasks are handled; the
section "Changes from version 1" lists each change, and it was registered before any task was drawn. The rules of
`docs/validation/VALIDATION_PLAN.md` §0 apply except where the section "Deviations from the plan's rules" below names
a deviation. G6's status while this study is open: early alpha. Individual capabilities have been tested; general
application-level improvements have not yet been established.

## Why this study

Coding agents can already write and change software, including the software AI systems are made of. What limits
them is checking their own changes: deciding which changes are real, safe within their declared scope, and worth
keeping. Generating candidates is getting easier; that decision is the harder constraint. If G6 can make the decision
cheaper for a person, within a stated margin on accepted defects, later work on AI that helps build and improve AI
could rest on it. This study would not itself show that.

G6's programme has three objectives, each established on its own evidence:

1. G6 reduces the human effort of verifying software without an unacceptable increase in accepted defects.
2. AI agents can compose G6's components into applications that meet acceptance criteria set independently of them.
3. AI agents can propose and achieve reproducible improvements over frozen baselines, without controlling or
   weakening the evaluators that judge them.

**This study tests objective 1 only, for one kind of work: reviewing AI-written Python patches.** A positive result
would not show that G6 makes software correct in general, that it finds defects its checks do not cover, or that AI
can improve itself (objectives 2 and 3 need their own studies).

## Who it is for

Technical founders of Python software-as-a-service products who use Claude Code or Codex to write code, and who must
decide whether to merge what the agent wrote.

## Words used here

- A patch is **correct** when it passes every check in its label contract, and **defective** when it fails at least
  one (see "Two contracts").
- An arm **accepts** a patch when its rule in the table of arms says so. Everything else is **not accepted**.
- A **disposition** (Experiment 2) is a reviewer's recorded decision on a patch: merge, or do not merge.
- **Points** are percentage points. A **one-sided 95% bound** is the corresponding limit of the two-sided 90%
  interval.

## The two questions

**Experiment 1, discrimination (engineering, no people).** On the same patches, does G6's review accept defective
patches at most 5 points more often than the repository's tests, and at most 5 points more often than the same checks
run without G6; and does it accept correct patches at most 15 points less often than the repository's tests?

**Experiment 2, human effort (people).** With G6's report, does a founder spend at most 75% of the time per correct
disposition that they spend with the repository's tests alone, without merging more defective patches?

Time saved is never inferred from Experiment 1. Experiment 2 measures it.

## Two levels: feasibility first, then confirmation

| Level | Size | Purpose | What it may claim |
|---|---|---|---|
| Feasibility | 30 non-empty patches, or 45 tasks attempted, whichever comes first | Whether G6 gives useful positive and negative assessments, whether the experiment runs reliably, and the rates that size the confirmatory study | Nothing about superiority or safety. Its estimates are reported as feasibility estimates. |
| Confirmatory | Computed from the feasibility results and registered before any confirmatory patch is generated; never fewer than 100 non-empty patches per class | Tests the hypotheses below | Only those hypotheses |

**Feasibility ends as feasible or not feasible.** It is feasible when all three hold:

1. every arm returns a disposition or a counted tool error for every non-empty patch, and no arm has a tool error on
   more than 10% of them;
2. G6 accepts at least one correct patch and does not accept at least one defective patch;
3. every task used passed the oracle controls (see "Ground truth and its error").

Its ECHA verdict is `inconclusive` by design, because it tests no hypothesis, unless the gaming checks or the oracle
make it `contaminated_evaluation` or `oracle_inadequate`. Only a feasible result may be followed by a confirmatory
entry. A failed criterion is reported as such.

**The confirmatory size** is the smallest number per class that gives each primary rate a two-sided 95% interval no
wider than ±10 points and 80% power for each hypothesis at its margin. Power is computed with the true difference set
to the less favourable to G6 of zero and the feasibility estimate, from the feasibility rates and discordance, by a
script frozen in the confirmatory entry. An interval of ±10 points around a rate near 50% needs about 100 patches per
class, so about 200 non-empty patches. If the pool, the budget or the spending cap cannot reach that size, the
confirmatory study is not run and only the feasibility study is reported, as feasibility.

## Data

**Pool.** The Python tasks of the public SWE-bench Pro (`ScaleAI/SWE-bench_Pro`, configuration `default`, version
2.0.0, revision `2d52cb3df914a3fcf80c7f66738b3a88ae37fc50`, test parquet SHA-256
`1067f72c1f43bbc578276888625db3b836cebb535546c9eaf824c5d5dd104a7f`), the population prepared for V8. Every Python ID
in V8's draft split comes from three repositories: ansible, openlibrary and qutebrowser. The feasibility entry
records the number of Python tasks per repository after exclusions. The owner may name another pool before the
feasibility freeze. Public benchmarks may be in model training data; that risk is reported, not assumed away.

**Excluded before any draw:** every ID listed anywhere in `docs/validation/results/V7/splits.json` or
`docs/validation/results/V8/splits.draft.json`, and every task seen while building or tuning Steps 2 and 3. V8 is a
draft and has opened none of its scored tasks; excluding them keeps V8 runnable later on its own tasks. Also excluded
before the draw: the IDs on a published list of broken tasks, if one exists at the level's freeze (see "Broken
tasks"). If the pool after exclusions cannot reach the confirmatory size, the owner may release V8's draft IDs or name
another pool before the confirmatory freeze; nothing else changes the pool. A later revision of the benchmark, for
example one that removes broken tasks, is another pool, and only the owner may name it, before a freeze.

**The draw, fixed now:**

- The seed of a level is the integer value of the first 16 hexadecimal digits of the SHA-256 of the text
  `V9:<level>:<commit>`, where `<level>` is `feasibility` or `confirmatory` and `<commit>` is the full 40-character
  hash of G6's frozen commit for that level. It cannot be known before G6 is frozen.
- Within each repository the task IDs are sorted and then shuffled with the seed. Repositories take turns in
  proportion to their remaining pool: each next task comes from the repository whose share of the tasks drawn so far
  is furthest below its share of the pool, with ties broken by repository name.
- Feasibility takes tasks in that order until it has 30 non-empty patches or has attempted 45 tasks. Confirmation
  continues the same order with its own seed over the tasks feasibility did not draw. The feasibility and
  confirmatory tasks are disjoint.
- **Stopping depends on label counts only.** Confirmation draws until both classes reach the registered size, or the
  pool or the spending cap is exhausted. No arm's output is ever looked at to decide whether to continue.
- A task whose environment does not build, that fails the oracle controls, or that the task screen marks
  label-invalid (see "Broken tasks") is replaced by the next task in order before any patch is generated for it, and
  the replacement is logged.

**Patches.**

- One patch per task: the first complete patch the frozen agent produces. It is retried only when the infrastructure
  failed before the agent started, and the retry is logged.
- The agent is frozen in the entry: the product (Claude Code or Codex) and its version, the model ID, the reasoning
  effort, the prompt and the tool entitlements, recorded again for every session.
- The agent writes the patch with no network access, because the upstream fixes are public. It sees the task
  statement and the repository at the base commit, never the label contract or G6's evaluation configuration.
- Sessions are ones the owner launches with their own subscription, or run under a spending cap the owner approves in
  writing (§0 rule 1).
- Every patch is kept, whatever its quality; none is selected or edited.
- An empty patch, or one that does not apply to the base commit, leaves no review decision to make. It is recorded
  and counted, and is left out of the primary analysis. A sensitivity analysis counts it as defective.

**Never in evaluation:**

- the six engineering cases of `validation/patch_review/PROTOCOL.md`;
- the V7 pilot patches;
- any fixture or patch seen while building or tuning Steps 2 and 3.

Development uses its own fixtures.

## Two contracts, both frozen before any arm runs

**The label contract** decides correct or defective. No arm ever sees it.

- It holds the benchmark's own hidden tests for the task: the tests the reference fix makes pass, and the tests that
  must keep passing. The benchmark's own procedure adds them in the evaluator's environment after the arms have run.
- It also holds behavioural checks from a withheld specification, but only when the owner names an author for them.
  That author must not write G6 code (§0 rule 3), and must not have seen G6's checks or any arm's output for the task.
  Without such an author the label contract is the hidden tests alone, and the report says so.
- The contract and its results are kept in G6's evaluator-only vault, `g6.research.echa.Vault`. Every arm runs in a
  confined environment that cannot read it.

**The review contract** states what the patch must do. Arms 2, 3 and 4 receive it identically.

- It is the task's own statement (the benchmark's problem statement, and its stated requirements and interface where
  the benchmark gives them), split into numbered requirements.
- The splitting is done by a script frozen in the feasibility entry and developed on development tasks only. No
  person or model rewrites it.
- It contains none of the label checks.
- The checks G6 may cite for a requirement are:
  - the tests the patch itself adds or changes;
  - the repository's existing tests;
  - G6's own checks.

  The hidden tests are never among them.

## Ground truth and its error

**Label rules.**

- Every label check runs three times.
- A check fails when it fails in at least two of the three runs, or when it runs longer than its timeout: ten times
  its median time on the reference fix, and at least 60 seconds.
- A check that gives different results across three runs on the reference fix is dropped for that task before any
  patch is generated, and counted.

Labels are computed by running the checks in the same confined environment for every patch; no model judges an
outcome (§0 rule 6).

**Oracle controls, per task, before its patch is generated:** the reference fix passes every label check that is
kept, and the base commit fails at least one test the reference fix makes pass. A task that fails either control is
replaced.

**Bounding the oracle's error (§0 rule 6).** A person named by the owner audits a seeded sample of labelled patches.
The person must not write G6 code, and must not have seen the label or any arm's output.

- **The sample:** 10 patches at feasibility (if the owner names a person by then) and at least 40 at confirmation.
- **The audit:** for each patch, the auditor reads the task statement, the reference fix and the patch, and records
  whether the patch does what the task asks.
- **A disagreement with the label** is settled by a test the auditor writes to show the behaviour. When that test
  shows the label wrong, it counts as a label error.

Confirmatory labels are adequate when the one-sided 95% Clopper–Pearson upper bound on the label error rate is at
most 15%. Otherwise, or without an audit, the confirmatory verdict is `oracle_inadequate`. Disagreements between the
hidden tests and the withheld specification checks are reported.

**Broken tasks.** OpenAI's audit of SWE-bench Pro's public tasks ("Separating signal from noise in coding
evaluations", 8 July 2026, `openai.com/index/separating-signal-from-noise-coding-evaluations`) estimates that about
30% of them are broken: their hidden tests cannot judge a patch by what the task asks. In our words, it names four
ways this happens:

1. **an unstated detail:** a hidden test requires something the task statement does not ask for;
2. **an underspecified statement:** the statement allows more than one behaviour, and the hidden tests require one of
   them;
3. **a misleading statement:** the statement asks for something other than what the hidden tests require;
4. **weak tests:** the hidden tests can pass a patch that does not do what the statement asks.

The second and third kinds are read as cases of the first: in each, a hidden test enforces something the task does not
state. On a task of these three kinds, a patch can do what the task asks and still be labelled defective. On a task of
the fourth kind, a patch can be labelled correct without doing it. This study handles them as follows.

- **A published list.** At each level's freeze, before that level's draw, the evaluator checks whether OpenAI or the
  benchmark's maintainers have published the IDs of the tasks found broken. If they have, the list's source and hash
  go in that freeze entry, and its IDs join the exclusions. When version 2 was registered, no such list had been
  found.
- **The label audit records a kind for each label error.** The auditor judges each patch without its label, as above.
  When the auditor's test shows the label wrong, the auditor records which of the four kinds explains it, or "other".
  The kind is recorded before any result is unblinded, and without seeing any arm's output, because it decides which
  patches the primary analysis keeps.
  - A patch labelled defective whose label rests only on hidden tests of the first three kinds is **label-invalid**.
    It is left out of the primary analysis, counted as a label error and reported. A sensitivity analysis keeps it as
    labelled.
  - Every other label error counts as before, and its patch stays in the analysis. The number of label errors of the
    fourth kind is reported beside the results.
- **The task screen, an option the owner may switch on at a level's freeze.** It needs a screener, named by the owner
  under the auditor's conditions, who is not the label auditor: a screener reads the hidden tests and could then
  predict a patch's label, so the audit would no longer be blind. Before a task's patch is generated, the screener
  reads the task statement (with its stated requirements and interface), the reference fix and the kept hidden tests.
  The screener records whether a hidden test requires behaviour of the first three kinds, naming the test and the
  behaviour in one sentence. Such a task is label-invalid. It is replaced, as a task that fails the oracle controls
  is, and it is counted and reported with the screener's sentence. Weak tests (the fourth kind) are recorded, and the
  task is kept. Unless the owner switches the screen on, it does not run.
- **Without a label auditor,** no patch is left out under this rule. The report says that the labels were not checked
  for broken tasks, and it gives the audit's estimate beside the results.

The correction runs one way: labels of the fourth kind are counted but not corrected. Without the screen, only the
audited sample is checked, and the rest of the primary analysis is not.

**Counting after exclusions.** The primary rates, the hypotheses and verdict 3 count non-empty patches after
label-invalid patches are left out. Stopping a level's draw counts labels before the audit, because the audit comes
after the labels.

## Arms

Every non-empty patch goes through every arm, so comparisons are paired by patch.

| Arm | What it does | Accepts when |
|---|---|---|
| 1. Tests and continuous integration | Runs the repository's tests, as its own continuous integration runs them, at the base commit and on the patched tree, three times each, confined. The test command per repository is frozen in the entry. | No test that passed in all three runs at the base commit fails on the patched tree, and every test the patch adds or changes passes. A test that passed in only some base runs is left out and counted. A suite that cannot be collected or run on the patched tree is not accepted. |
| 2. The agent's own review | In the session that wrote the patch, after the patch is captured, the agent gets a frozen review prompt with the review contract. It has the same tools it had while writing, no network, the same time limit as G6's arm, and a token cap frozen in the entry. Edits it makes while reviewing are discarded. | Its answer ends with the line `VERDICT: ACCEPT`. Any other ending, a hedged or doubled verdict, or a timeout is not accepted. |
| 3. G6's review | The frozen G6 commit's patch review, given the patch, the repository at the base commit and the review contract, confined | The assessment is `supported_within_scope` **and** every requirement of the review contract is `passed_on_stated_checks` |
| 4. The same checks without G6 | The deterministic checks G6's frozen review runs, with their budgets, called directly from a short script frozen in the entry, on the same inputs | Every check passes |

- **Time limits:** arms 2, 3 and 4 each have a wall-clock limit per patch, frozen in the entry (30 minutes unless the
  feasibility entry sets otherwise). Arm 1 has a limit of 60 minutes per run.
- **Model calls:** G6's arm makes no model calls unless the frozen commit does. If it does, they are counted, and
  arm 2's caps are set at least as large.
- **Merge authorisation** is reported separately by G6 and is never an outcome of this study.
- **What arm 4 isolates:** the difference between arm 3 and arm 4 is what G6 adds over its underlying checks.

**Everything that is not acceptance is "not accepted".** It is tallied separately as:

- defect found;
- insufficient evidence (an abstention);
- escalation to a person;
- test integrity compromised;
- tool error or refusal.

Tool errors and refusals count as not accepted, and are reported, never dropped.

## Outcomes

**Experiment 1, primary:**

- **False acceptance rate:** defective patches accepted ÷ all non-empty defective patches. Here and in every rate
  and hypothesis below, label-invalid patches are left out (see "Broken tasks").
- **Correct acceptance rate:** correct patches accepted ÷ all non-empty correct patches.

**Experiment 1, secondary:**

- abstentions and escalations of correct patches;
- detection by defect category;
- model calls, processor time, wall-clock time and setup effort per arm;
- each primary rate per repository;
- the sensitivity analysis that counts empty and non-applying patches as defective;
- the sensitivity analysis that keeps label-invalid patches as labelled (see "Broken tasks").

**Experiment 2** outcomes are defined in its design section below.

## Analysis and hypotheses for Experiment 1

**The unit is the task.** There is one patch per task, and comparisons are paired by patch. Every difference below is
reported with its two-sided 90% interval [L, U], so U is the one-sided 95% upper bound. The interval is built from
two methods, and the decision uses the larger U and the smaller L of the two:

- Newcombe's hybrid score interval for a difference of paired proportions (Newcombe 1998, method 10; an approximate
  interval);
- a bootstrap that resamples tasks within each repository (stratified by repository; 10,000 resamples, percentile
  limits). Its seed is the level's seed plus one.

With only three repositories, nothing is generalised beyond them. Each repository's rates are reported on their own.
The exact sign test on the discordant pairs (McNemar's exact test) is reported for each comparison as a secondary
result.

**The three hypotheses**, where "worse for G6" is positive:

1. **False acceptance against tests and continuous integration:** D1 = G6's false acceptance rate − arm 1's. Margin
   +5 points.
2. **False acceptance against the same checks without G6:** D2 = G6's false acceptance rate − arm 4's. Margin
   +5 points.
3. **Correct acceptance against tests and continuous integration:** D3 = arm 1's correct acceptance rate − G6's.
   Margin +15 points.

Each hypothesis has three possible results:

- **met** when U is at most its margin;
- **not met** when L is above its margin (G6 is clearly worse than the margin allows);
- **inconclusive** when the interval straddles the margin.

**Experiment 1 is met only when all three are met.** It is not met when any one is not met, and inconclusive
otherwise. Because all three must hold, this is an intersection–union test. Each hypothesis at one-sided α = 0.05
keeps the overall error rate at 0.05 with no correction for multiple tests.

**Reporting:**

- Each hypothesis's own result is reported with its difference, its interval and its margin.
- No single hypothesis is ever presented as Experiment 1 being met.
- "Non-inferior within a margin" is never written as "no additional defects".
- The agent's own review (arm 2) is compared with G6 on the same three differences as a secondary result, with no
  verdict.

## Verdicts

The study's ECHA verdict (§0 rule 7; ECHA §8.4) rests on Experiment 1 at the confirmatory level. The first of these
that applies is the verdict:

1. `contaminated_evaluation`: the gaming checks found an arm that read the label contract or the vault, used the
   network, or could change its evaluator.
2. `oracle_inadequate`: the label audit's bound is above 15%, or there was no audit.
3. `insufficient_failure_opportunities`: fewer non-empty defective patches than the registered size, after
   label-invalid patches are left out.
4. `unsafe_or_regressive`: hypothesis 1 or 2 is not met (unsafe), or hypothesis 3 is not met (regressive). The
   report says which.
5. `component_validated_no_transfer`: Experiment 1 is met.
6. `inconclusive`: otherwise, including fewer non-empty correct patches than the registered size.

Experiment 2 ends as met, not met or inconclusive and is reported beside the ECHA verdict. Any claim about time saved
needs Experiment 2 met. "Not met" and "inconclusive" are reported as plainly as "met".

## Design of Experiment 2

Experiment 2 runs only after Experiment 1's feasibility level is feasible, and only with founders the owner recruits.

**Patches.**

- They are drawn by seed from Experiment 1's non-empty patches of the same level.
- Each method gets half correct and half defective patches; reviewers are not told the mix.
- Patches are matched across methods by repository and by size (lines changed, in thirds).

**Reviewers.**

- They are founders unfamiliar with G6.
- A pilot with 3 or 4 reviewers sizes the confirmatory run.
- The confirmatory run has at least 12 reviewers, each reviewing 8 patches (4 per method). The counts are fixed in
  the confirmatory entry.

**Assignment.**

- Which method a reviewer uses first is counterbalanced across reviewers. Which patch goes with which method is
  randomised by a seed in the entry.
- No reviewer sees the same patch twice, and each patch is reviewed under both methods by different reviewers. This
  gives enough overlap for a paired and hierarchical analysis by reviewer and by repository.

**Materials.**

- **Both methods:**
  - the task statement and the review contract;
  - the diff;
  - a confined terminal with the patched repository and its tests runnable;
  - a timer.
- **G6's method** adds G6's frozen arm-3 report for that patch.
- **Nobody** uses an AI assistant, and the agent's own review is not shown.

**G6's report must keep three things apart (amendment A5):**

1. the review contract, with the evidence for each requirement;
2. the requirements with no adequate check;
3. a standing note that the task's specification may be incomplete, which G6 cannot rule out.

A frozen script checks every arm-3 report for the three parts before results are unblinded. A report missing a part
counts as a tool error in Experiment 1 and is not shown in Experiment 2.

**Disposition and time.**

- Time runs from opening the materials to recording merge or do not merge, with a written reason.
- The cap is 45 minutes. No decision at the cap counts as do not merge, and is counted.

**Primary outcome:** time per correct disposition.

- For reviewer r and method m, E(r, m) is the total minutes over all of r's reviews under m, incorrect ones included,
  divided by the number of correct dispositions. E is infinite when there are none.
- Each reviewer's ratio is R(r) = E(r, G6's method) ÷ E(r, tests alone). A reviewer with no correct disposition under
  either method is left out and reported.
- The statistic is the median of R(r) over reviewers. Its two-sided 90% interval comes from a bootstrap over
  reviewers (10,000 resamples, percentile limits).

A fast wrong decision adds time without adding a correct disposition, so it is never counted as time saved.

**Safety constraint:** the number of defective patches reviewers merged with G6's report must be no greater than the
number they merged with the tests alone. The paired difference per reviewer and its interval are reported.

**Secondary outcomes:**

- correct patches not merged;
- minutes per review;
- how well each reviewer explained the evidence they relied on, scored against a rubric frozen in the entry. The
  scorer is blind to method, and mentions of the method are removed from the reasons before scoring.
- a mixed-effects model of log minutes per review, with reviewer and patch as random effects, as a sensitivity
  analysis.

**Experiment 2 has three possible results:**

- **met** when the upper limit of the median ratio's interval is at most 0.75 and the safety constraint holds;
- **not met** when the lower limit is above 0.75, or the safety constraint fails;
- **inconclusive** otherwise.

The 75% figure is a hypothesis to test, reported with its uncertainty, not an expected result.

## Safeguards

**Frozen before each level runs**, in that level's entry:

- G6's commit and configuration;
- the pool's file hash, the excluded IDs and the seed's inputs;
- whether a published list of broken tasks was found, with its source and hash if so, and whether the task screen
  runs, with its screener;
- the agent: product, version, model ID, effort, prompts, tool entitlements and network rule;
- the arms' scripts, test commands, time limits, token caps, review prompt and verdict rule;
- the scripts for the label contract, the review contract and the A5 report check;
- the statistics scripts and the gaming-scan patterns;
- for confirmation, the size computed from feasibility.

**The other safeguards:**

- **Gaming checks (§0 rule 8):** before results are unblinded, the patch-writing transcripts and every arm's trace
  are scanned for:
  - reads of the label contract or the vault;
  - network access;
  - edits to tests or runner configuration outside the patch itself;
  - shortcuts.

  An arm that can read or change its evaluator voids the study.
- **One look at held-out data:** no configuration, threshold or check selection is chosen after seeing evaluation
  results. A held-out pool supports one registered decision; reusing it makes the result exploration only.
- **No early stopping** for a favourable result. Stopping follows the label-count rule above, and nothing else.
- **Deviations** from this protocol are logged as they happen and reported with the results.
- **The owner may tighten** any margin or threshold before a freeze. Nothing is loosened after.

## Deviations from the plan's rules (VALIDATION_PLAN.md §0)

1. **Rule 1, local models.**
   - Patch generation and arm 2 use a hosted agent (Claude Code or Codex), only in sessions the owner launches or
     under a spending cap the owner approves in writing.
   - G6's arm uses no model unless its frozen commit does.
2. **Rule 3, splits.**
   - The task statements and hidden tests are the benchmark authors' (rule 9).
   - The withheld specification checks need an author who does not write G6 code; without one, they are left out.
   - Development and evaluation are disjoint by task, not by repository, because the pool has only three Python
     repositories.
3. **Rule 4, analysis.**
   - The unit is the task. With three repositories, a cluster bootstrap by repository cannot give a valid interval,
     so the bootstrap is stratified by repository instead.
   - Holm correction is replaced by the intersection–union rule, which needs all three hypotheses to hold.
   - The sign test is reported as a secondary result.
4. **Rule 5, compute matching.**
   - G6's arm makes no model calls, so the self-consistency baseline does not apply.
   - Arm 2 gets a frozen cap rather than G6's zero. If G6's frozen commit makes model calls, arm 2's cap is at least
     as large.
5. **Rule 7, verdicts.** The ECHA verdict rests on Experiment 1. Experiment 2 is reported beside it.

## What this study cannot show

- That G6 makes software correct in general, or finds defects outside the checks it runs.
- That the result transfers to other repositories, other languages, other agents, or patches that are not
  AI-written.
- That the labels are free of broken tasks. The screen and the audit find only what their readers recognise; the
  correction runs one way (weak tests are counted, not corrected); without the screen only the audited sample is
  checked; and without an auditor nothing is.
- Objectives 2 and 3: composition of applications by agents, and self-improvement.

## Decisions the owner makes before the feasibility freeze

1. **The thresholds:** approve or tighten the margins (5, 5 and 15 points) and the 75% target. Both are tested on the
   bound of an interval, which is stricter than testing a point estimate.
2. **The agent:** which one (Claude Code or Codex), its version, model and effort. Then the sessions, or the spending
   cap, for generating patches and running arm 2.
3. **The label auditor**, who is needed for confirmation. Optionally, an independent author of withheld specification
   checks. Optionally, the task screen, with a screener who is not the label auditor (see "Broken tasks").
4. **Reviewers:** recruiting unfamiliar founders for Experiment 2.
5. **The pool:** another one, if not the default. Later, if the confirmatory size cannot be reached, whether to
   release V8's draft IDs.

## Changes from version 1

Version 2 was registered on 2026-10-29, before any task was drawn, any patch was generated or any arm was run. No
evaluation data had been read. After OpenAI's audit of SWE-bench Pro's public tasks was checked, the owner decided
that a published list of broken tasks is excluded, and that the label audit treats a hidden test that enforces
something the task does not state as label-invalid: left out, counted and reported. The task screen is the drafter's
addition; it runs only if the owner switches it on at a freeze. Version 2 changes:

1. the section "Broken tasks", with the four kinds, the published list, the audit's kinds, the optional task screen
   and the counting after exclusions;
2. under "Data", the exclusion of a published list of broken tasks, checked at each level's freeze, and the sentence
   that a later revision of the benchmark is another pool;
3. under "The draw", the replacement of a task the screen marks label-invalid;
4. under "Outcomes", label-invalid patches left out of every rate and hypothesis, and the sensitivity analysis that
   keeps them;
5. under "Verdicts", verdict 3 counted after label-invalid patches are left out;
6. under "Safeguards", the list check and the screen among the frozen items;
7. under "What this study cannot show", the limits on finding broken tasks;
8. under "Decisions the owner makes", the optional screen and its screener.

Apart from these changes, the protocol is as in version 1.

## The registry entries this study will add

1. This protocol, frozen (the entry that registers this file).
2. The feasibility freeze: G6's commit, the pool, the exclusions, the seed's inputs, the agent and the scripts.
3. The feasibility outcome, appended after the run.
4. The confirmatory freeze, with the size computed from feasibility.
5. The confirmatory outcome.
