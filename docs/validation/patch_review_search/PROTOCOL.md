# Verification search on agent-written patches: protocol, version 1 (registered, not executed)

When G6 searches for evidence beyond the checks a task states, does it give reproducible findings on defective
agent-written Python patches that the repository's tests and the agent's own review accepted, and what does that cost
in correct patches not accepted, in model calls and in time? This study observes; it tests no hypothesis and claims
no superiority.

This protocol is registered in `docs/validation/REGISTRY.md` (entry V10) before any code for verification search is
written and before any evaluation patch exists. The rules of `docs/validation/VALIDATION_PLAN.md` §0 apply except
where the section "Deviations from the plan's rules" names a deviation. It runs on the patches, labels and contracts
of V9 (`docs/validation/patch_review_utility/PROTOCOL.md`, version 1). It changes nothing that V9 registered; the
two conditions it adds to how V9 is run are stated under "Data".

G6's status while this study is open is stated as follows. Early alpha. Individual capabilities have been tested;
general application-level improvements have not yet been established.

## Why this study

Coding agents implement changes and report them done. The people who delegate to them cannot yet delegate the
independent check of three things: whether the change satisfies the requirements, whether it preserves what already
worked, and whether it is sufficiently tested. V9 asks whether G6's review of the stated checks is useful. Stated
checks can be missing or weak, so G6 can also search: it asks a model for further checks, runs them confined against
the original code and the patch, and asks rather than accuses when a check a model wrote fails. Within one review, a
check that a model wrote never passes a requirement, so search can only lower that review's acceptance; criterion 4
below checks this. The question is whether what search finds is worth what it costs.

This study belongs to objective 1 of G6's programme (less human verification effort without an unacceptable increase
in accepted defects). It does not test objectives 2 or 3.

## Who it is for

Technical founders building software products who regularly use coding agents such as Claude Code or Codex, and who
want to delegate more development work with greater confidence. The study uses Python patches only.

## Words used here

The words of V9's protocol apply: **correct**, **defective**, **accepts**, **not accepted**, **disposition** and
**points**. An arm's **disposition** is its result for a patch: accepted or not accepted. In addition:

- **Verification search** is the stage of G6's patch review that adds checks beyond the stated ones. It is off unless
  the caller turns it on.
- The **proposer** is the part of verification search that asks the model for checks.
- A **free-form check** is a test a model writes in full.
- A **template** is a reviewed check pattern built into G6, such as a round trip, or a rule about who may perform an
  operation. A model fills it with names and values from the repository, and G6 generates the test from it.
- The **formal path** builds a small model of a sequence of operations, searches the model for a sequence that breaks
  a stated rule, and replays any such sequence (a **trace**) on the code.
- **Test adequacy** asks whether the stated checks fail when the changed lines are altered slightly. Each alteration
  is a **mutant**.
- A **reason code** is the label G6 gives each result in its report, such as `unconfirmed_counterexample`.
- The **search arms** are C and T. Arm E is the direct comparator; it is not a search arm.
- A **finding** is a result an arm reports against a patch together with a reproduction that a person or a script can
  run. The kinds of finding each arm can produce are listed under "Findings".
- A finding is **reproducible**, **compatible with the reference fix** or **not comparable** as defined under
  "Reruns". An **actionable finding** is a reproducible finding on a patch that the label contract labels defective.
- The **development smoke run** is one run of verification search on development fixtures only, under its own spending
  cap, before the freeze. It measures cost and touches no task of the pool.
- **Interval** means a two-sided 95% interval unless a section says otherwise.

## The level: feasibility only

| Level | Size | Purpose | What it may claim |
|---|---|---|---|
| Feasibility | V9's feasibility patches: 30 non-empty patches, or 45 tasks attempted, whichever comes first | Whether verification search runs reliably within its budget on real agent-written patches, what it finds, what it costs, and the rates that would size a later study | Observations with intervals. Nothing about superiority or safety. |

There is no confirmatory level in this protocol. An interval of ±10 points on a rate near 50% needs about 100 patches
per class. V9's pool after exclusions (about 169 Python tasks; the count is recorded in V9's feasibility entry) cannot
supply them: if the agent solves about four tasks in ten, 100 correct patches alone would need about 250 tasks. A
confirmatory study would be a new registered study on a larger pool, an owner decision taken after this one reports.

**Feasibility ends as feasible or not feasible.** It is feasible when all of these hold:

1. each of arms A, C, T and E returns a disposition or a counted tool error for every non-empty patch, and none has a
   tool error on more than 10% of them;
2. no search arm and not arm E used more than its frozen limits; a run stopped at a limit is counted, with the limit
   it reached;
3. in each search arm, the search stage ran at least one check to a pass or a fail (not a `check_error`, not refused)
   on at least half of the non-empty patches, so the study can say something about search;
4. no report of a search arm lists a check that a model originated as passing a requirement, and every formal result
   is reported in its three parts (see "The report check");
5. before the labels are read, a frozen script compares each search arm's results on the stated checks with arm A's on
   every patch and lists the patches where they differ; every patch that a search arm's own review accepted and arm A
   did not is on that list;
6. V9's feasibility level met its own criterion 3 (oracle controls) for every task used.

A failed criterion is reported as such, with the patches concerned.

## Data

- **Patches, labels and contracts:** exactly V9's feasibility draw, its patches, its label contract and its review
  contract. None is regenerated, selected, edited or relabelled for this study. Empty and non-applying patches are
  handled as V9 handles them.
- **The draw** is V9's. Its seed is computed from G6's frozen commit, so it cannot be known before G6 is frozen.
- **Two conditions this study adds to how V9 is run,** without changing V9's protocol:
  1. V9's feasibility freeze names a G6 commit that includes verification search, used with search off in V9's arms;
  2. V9's labels stay unread, for V9 as for this study, until this study's arms and reruns are recorded (see
     "Safeguards").

  If either condition fails, this study is not run on V9's patches, and the registry says so.
- **Evaluation data stays independent of development.** Verification search is built and tuned on development fixtures
  only: small repositories written for the purpose, the six engineering cases of `validation/patch_review/PROTOCOL.md`
  and the fixtures of the verification-search tests. Its templates, strategies, limits and prompts are fixed in G6's
  frozen commit before any task is drawn. The freeze entry lists every repository and fixture used to build or tune
  verification search. Any pool task opened during that work is named there, and its patch is left out of this study's
  analysis (it stays in V9's).
- **Repository history:** the scratch copies used by arms C, T and E contain no repository history beyond the base
  commit, checked by a frozen script before the arms run, so no check can reach the upstream fix through git.
- **Public benchmarks may be in model training data,** including the upstream fixes. A model that proposes checks
  could recall the reference behaviour. The study measures this as far as it can (see "Secondary measures") and does
  not assume it away.

## Arms

Every non-empty patch goes through every arm, so comparisons are paired by patch. V9's arms are reused, not rerun.

| Arm | What it does | Accepts when |
|---|---|---|
| V9 arm 1. Tests and continuous integration | As registered in V9 | As registered in V9 |
| V9 arm 2. The agent's own review | As registered in V9 | As registered in V9 |
| V9 arm 4. The same checks without G6 | As registered in V9 | As registered in V9 |
| A. G6 without search | V9's arm 3, reused: G6's review at the frozen commit with verification search off | As V9's arm 3: the assessment is `supported_within_scope` and every requirement of the review contract is `passed_on_stated_checks` |
| C. G6 with free-form search | G6's review at the frozen commit with search on, the proposer restricted to free-form checks (no templates, no formal path) | Arm A accepts, and this arm's own review accepts by arm A's rule |
| T. G6 with templates and the formal path | G6's review at the frozen commit with search on: templates first, the formal path where a template uses it, free-form checks where no template applies. This is the configuration Experiment 2 shows to reviewers | Arm A accepts, and this arm's own review accepts by arm A's rule |
| E. Model-written tests without G6 | The same model as arms C and T is asked by a frozen prompt to write pytest tests. Its tests run with V9's arm 1 command, without G6 (details below) | V9's arm 1 accepts, and every kept test passes (defined below). A kept test that cannot be collected, set up or finished in time counts as not passing |

**Inputs and the model.**

- Arms C, T and E receive the patch, the review contract, and the same repository context: the context G6's proposer
  builds by its frozen selection. E's script uses that selection to build its prompt, and nothing else from G6. None
  of these arms sees the label contract, the reference fix, the vault or another arm's output.
- Arms C, T and E reach the model through the same hosted interface, with the same model ID and sampling settings
  (including temperature) and the same per-call limits on input and output, each with its own frozen prompt. They do
  not go through an agent sign-in such as a Claude Code or Codex subscription session. The freeze entry records all
  of this.
- Any random choice in G6's search or in E's script uses a seed recorded in the freeze entry.

**Arm E in detail.**

- **Calls:** on each patch, E makes as many model calls as the larger of arms C and T made on that patch, and at least
  one, each asking for further tests, and pools the files. If G6's proposer receives the results of running its
  earlier checks between calls, E receives the results of running its earlier tests the same way; otherwise neither
  does. The freeze entry records which applies.
- **Kept tests:** E's script keeps only files named `test_*.py`, places them where G6 places free-form checks in its
  copy of the patched tree, and keeps at most 20 test functions in file order (20 is G6's own limit on checks).
  Everything else is discarded and counted.
- **Runs:** each kept test runs R times on the patched tree, where R is the number of times G6 runs a proposed check on
  the patched tree, frozen in the entry. A test **passes** when it passes in more than half of its runs. Outcomes are
  read per test from a machine-readable report written outside the copy; a test with no recorded outcome counts as
  not passing.
- **Errors:** a provider error, a time-out, or a reply the frozen script cannot parse is a counted tool error for E.
  Only a parsed reply with no test file gives E arm 1's disposition, and those patches are counted.
- **At the base:** before any label is read, E's kept tests also run R times at the base commit. This changes no
  disposition; it feeds the variants under "Secondary measures".

**For all arms of this study.**

- **Limits:** arms C, T and E have the same model-call cap per patch and the wall-clock limit of V9's arm 3 (30
  minutes unless the feasibility entry sets otherwise). G6's own limits on checks, inputs, models and mutants are those
  of its frozen commit.
- **Order and spending:** arms C, T and E run patch by patch in draw order, all three on one patch before the next,
  each under a third of this study's spending cap. Model calls stop at an arm's share. A patch reached after its arm's
  share is spent is a counted tool error for that arm, never dropped.
- **One run each:** each arm runs once per patch. A run that failed from infrastructure before its first model call is
  retried once, and the retry is logged. No other run is repeated.
- **No person is in the loop.** G6 is called through a client without elicitation, so no confirmation dialog can open,
  nobody answers G6's questions and every proposal stays unconfirmed. Confirmation is tested elsewhere: in G6's own
  tests, and when the owner operates the real dialog in Claude Code before release.
- **A failed proposer:** a patch on which a search arm's proposer failed is listed, with G6's reason, beside its
  disposition.
- **Merge authorisation** is reported separately by G6 and is never an outcome of this study.

**Everything that is not acceptance is "not accepted",** tallied separately as in V9. For arms C and T, not accepted
is further split by reason code. A patch that arm A accepted and a search arm did not is attributed to search only
when that arm's reasons include `unconfirmed_counterexample` or `unconfirmed_failure`, or its deadline was reached
during the search stage. Otherwise it is reported as a difference between runs.

## Findings

| Arm | What counts as a finding | Its reproduction |
|---|---|---|
| V9 arm 1 | A test that passed in all three base runs and fails on the patched tree | The test's node id and the frozen test command |
| V9 arm 2 | None: the review is text without a runnable reproduction, and edits made while reviewing are discarded | Not applicable |
| V9 arm 4 | A check that fails | The check's command, if V9's arm 4 script records it; otherwise arm 4's discovery rate is reported as not computed |
| A, C, T | A failed stated check (`defect_detected`). In C and T also: a counterexample a proposed check found (`unconfirmed_counterexample`), and a proposed check of what the change was meant to do that fails (`unconfirmed_failure`). In T also: a formal trace reproduced on the code | The test file and command G6 reports in its next actions, with their digests |
| E | A kept test that fails by an assertion or an exception raised in its body, in more than half of its runs. Collection, setup and time-out errors are counted separately and are not findings | The test file and its node id |

Not findings: test integrity results (counted separately, as in V9); behaviour that differs between the original and
the patch with no failing expectation (`behaviour_changed`, a question only); checks that fail at the base too
(`fails_on_base_too`); checks that could not run (`check_error`); and formal results about a model that did not
reproduce on the code (`model_only`, `not_replayable`).

## Reruns

The evaluator reruns every finding's reproduction with a frozen script that uses V9's arm 1 command for the
repository and does not import G6. A finding that cannot be run that way is not reproducible.

- **Each rerun** uses a fresh copy, in V9's arm 1 environment for the repository, confined, without network, with a
  time-out frozen in the freeze entry (60 seconds per reproduction unless the entry sets otherwise).
- **A rerun fails** on a failed assertion or an exception raised in the reproduction's body. It **errors** when the
  reproduction cannot be collected, set up or finished in time. Errors are counted, and are neither failures nor
  passes.
- **Before any label is read,** every reproduction is rerun three times on the patched tree and three times at the base
  commit. A finding is **reproducible** when it fails in at least two of the three runs on the patched tree. The base
  results are recorded.
- **After the labels are read,** every reproduction is rerun three times on the benchmark's reference fix. A finding is
  **compatible with the reference fix** when it passes in at least two of the three runs there, and **not comparable**
  when it errors in at least two (for example, because the reference fix names the interface differently).

## Measures

**Primary**, each per arm with its interval (exact Clopper–Pearson for a rate; for a paired difference, Newcombe's
hybrid score interval for paired proportions, method 10):

1. **False acceptance rate:** defective patches accepted ÷ non-empty defective patches.
2. **Correct acceptance rate:** correct patches accepted ÷ non-empty correct patches.
3. **Actionable defect discovery:** defective patches with at least one actionable finding ÷ non-empty defective
   patches.
4. **The headline count.** Let M be the non-empty defective patches that V9's arm 1 and arm 2 both accepted, and L the
   non-empty correct patches they both accepted. For each of arms A, C, T and E the study reports:
   - K, the patches in M with at least one actionable finding;
   - K_ref, the patches in M with at least one actionable finding compatible with the reference fix;
   - J, the patches in L with at least one reproducible finding.

   Each comes with its exact Clopper–Pearson interval. If M is zero, the headline sentence is not used, and the report
   says that no defective patch was accepted by both.
5. **Human verification effort:** Experiment 2 below, when it runs.
6. **Cost,** per patch (mean and median) and in total, per arm: model calls; input and output tokens as the provider
   reports them; dollars, computed by the study script from those tokens and the price list frozen in the freeze
   entry; wall-clock seconds; and processor seconds, measured by the study script for the arm's process tree.

Every rate is also given per repository. The paired differences reported are: C and T against A, C and T against E,
T against C, and C, T and E against V9's arms 1, 2 and 4, each on measures 1 to 3. Arm A against V9's arms 1, 2 and 4
is not estimated here; V9's report gives it.

**A property that holds by construction:** arms C and T accept a patch only when arm A does, so their false and
correct acceptance rates can only be equal to or lower than A's. The study measures both sides of that trade: the
defective patches no longer accepted, and the correct patches no longer accepted. Neither side is reported without
the other.

**Secondary:**

- **Search's own contribution:** let M_A be the non-empty defective patches arm A accepted. For C and T, how many of
  them have at least one actionable finding.
- **Each search arm's own-run acceptance:** its rates by its own review alone, without the condition on arm A, and the
  list of differences between runs from criterion 5.
- **Two variants of arm E,** computed from E's existing runs, with no new runs:
  - **E-filtered:** E's kept tests that also fail at the base, or that end in a collection, setup or time-out error,
    are set aside; E then accepts when arm 1 accepts and every remaining test passes;
  - **A-and-E:** arm A accepts and every kept E test passes.

  C and T are compared with both, on measures 1 to 3.
- **For each reason code:** the number of findings, the share on defective patches, the share reproducible, the share
  compatible with the reference fix and the share not comparable. This is the precision a reason code would need
  before it could earn more authority in a later registered study.
- **Findings on correct patches:** correct patches with at least one reproducible finding ÷ non-empty correct patches,
  per arm, and how many of those findings are compatible with the reference fix. Those cases are not relabelled; they
  are listed as places where the label contract may be incomplete.
- **Similarity to the hidden tests:** after the labels are read, a frozen script measures the similarity of each
  model-written check (in C, T and E) to the benchmark's hidden tests for its task. The measure and its threshold are
  frozen in the freeze entry. Findings above the threshold are reported separately, and the headline counts are also
  given without them.
- **Template applicability (arm T):** for each template, the tasks on which it was applied, refused (with G6's reason)
  or not chosen, as recorded in T's reports, which are frozen before the labels are read. On this pool many templates
  will rarely apply; that is reported as it falls.
- **The formal path (arm T):** from T's activities, the state models built, refused for exceeding the limits, or found
  vacuous or conflicting; the traces found; the traces reproduced on the code; `model_only`; `not_replayable`.
- **Test adequacy (arm T):** per patch, whether any mutant of the changed lines survived the stated checks, against the
  label, as a table with a third row for patches with no mutation run. The difference in the defective share between
  patches with and without surviving mutants is reported with Newcombe's hybrid score interval for two independent
  proportions. In this version of G6 test adequacy changes no outcome.
- **Questions:** the number per patch (at most 3), as G6 reports them. Nobody answers them in this study.
- **Activities:** from each search report, the activities performed and skipped, with G6's reasons.

## The report check

A frozen script checks every report of arms C and T before the labels are read:

- the three parts that V9's protocol requires (amendment A5);
- the completion section: what was demonstrated, what was not established, which defects were reproduced, what was
  checked for preservation, test adequacy where the arm measures it, open questions and the activities performed;
- that no check a model originated is listed as passing a requirement;
- that every formal result gives its specification, its model and its correspondence to the code as separate parts,
  and that only a reproduced trace is reported as a finding about the code.

A report that fails the check counts as a tool error for that patch. A report that lists a model-originated check as
passing a requirement also fails feasibility criterion 4.

## Experiment 2: human verification effort

Experiment 2 runs only if the owner recruits reviewers for it, and only after this study's feasibility result is
feasible. It follows V9's Experiment 2 design for patches, materials, assignment, the 45-minute cap, time per correct
disposition and the safety constraint, with these differences:

- G6's method shows arm T's report instead of V9's arm 3 report.
- Its reviewers are people the owner recruits who have not used G6 before, and who took part neither in G6's
  fresh-user trial (the usability trial before release) nor in V9's Experiment 2. The two groups are recruited
  separately.
- Patches and assignment use a seed equal to V9's feasibility seed plus three.
- At this level it is a pilot of 3 or 4 reviewers. Each reviewer's ratio and the median are reported, with no interval
  and no result of met or not met.

If Experiment 2 does not run, nothing is said about time or effort saved.

## Analysis

- **The unit is the task,** with one patch per task, paired across arms. Rates and differences are reported with the
  intervals named under "Measures".
- **The bootstrap:** resampling tasks within each repository (stratified by repository, 10,000 resamples, percentile
  limits, seed equal to V9's feasibility seed plus two). It is reported beside each paired difference as a check on
  the approximate interval, and it gives the interval for every share computed over findings, resampling tasks with
  all their findings, because one patch can yield many findings. The same resampled task sets serve every comparison.
  The number of findings per patch is reported.
- **Small numbers:** a zero denominator gives a count and no interval. A comparison with no discordant pairs is
  reported, not interpreted. Intervals are not adjusted for the number of comparisons.
- **No hypothesis is tested.** No difference is called significant, and no arm is called better.
- **Every number is reported,** including those against search: correct patches lost, findings on correct patches and
  cost. "Not feasible" is reported as plainly as "feasible".

## Verdict

The study's ECHA verdict (§0 rule 7) is the first of these that applies:

1. `contaminated_evaluation`: the gaming checks find that an arm of this study read the label contract, the reference
   fix or the vault, used the network from a check, reached repository history beyond the base commit, or could
   change its evaluator;
2. `oracle_inadequate`: feasibility criterion 6 fails, or V9's feasibility label audit, if it was run, puts the upper
   bound on label errors above 15%;
3. `inconclusive`: otherwise, by design, because the study tests no hypothesis.

## What the results permit saying

Only sentences of these forms, with the numbers and intervals filled in from the outcome entry:

- "On N agent-written Python patches from R repositories, of the M defective patches that the repository's tests and
  the agent's own review both accepted, G6 with verification search in its full configuration (arm T) gave a
  reproducible finding on K, of which K_ref also pass on the benchmark's reference fix. Model-written tests run
  without G6 (arm E) did so on K_E. On the L correct patches that both accepted, arm T gave a reproducible finding on
  J." Arm C's numbers are reported beside these and never in their place.
- "On the same patches, arm T's false acceptance was X% against arm A's Y%, and its correct acceptance X′% against
  Y′%", with all four intervals. Neither pair is used without the other.
- "Arm T cost a mean of C model calls, S seconds and D dollars per patch (median C′, S′ and D′)."

Never: that G6 is better than another method, that it verifies or proves a patch, that it finds all defects, that a
finding identifies the defect the label records, that a model satisfying a property means the program satisfies it,
or that results transfer beyond these repositories, this language, this model and this agent.

## Safeguards

**Frozen before the study runs,** in the freeze entry, registered together with V9's feasibility freeze:

- G6's commit, with verification search, and the search configuration of arms C and T;
- the model, the hosted interface, the sampling settings, the per-call limits, the call cap, the seeds and the price
  list;
- E's prompt and script, and R;
- the owner's spending cap for this study, separate from V8's and V9's;
- the repositories and fixtures used to build verification search, and any pool task opened while building it;
- the scripts for the history check, the reruns, the report check, the comparison of stated-check results, the
  similarity measure and its threshold, the statistics and the gaming scan.

**The other safeguards:**

- **Order:** V9's patches, then V9's arms, then arms C, T and E, then the reruns on the patched trees and at the base.
  Every arm's output and every rerun result is hashed into an append-only record. The record's final hash is committed
  to G6's public repository before any label is read. The labels, however early they are computed, stay unread in the
  evaluator-only vault, `g6.research.echa.Vault`, until then.
- **Gaming checks (§0 rule 8):** before the labels are read, every trace of arms C, T and E, including every model
  request and reply, is scanned for reads of the label contract, the reference fix or the vault; for network access
  from a check; for checks or prompts that run git or read `.git`; for edits outside the scratch copies; and for
  shortcuts. An arm that can read or change its evaluator voids the study.
- **One look:** no configuration, limit, template or prompt is chosen after seeing any evaluation output. The study
  supports one registered report; rerunning it with changes makes the result exploration only.
- **Deviations** are logged as they happen and reported with the results.
- **The owner may tighten** any limit or criterion before the freeze. Nothing is loosened after.

## What G6's frozen commit must provide

The study depends on these. They are acceptance tests for building verification search. A measure whose input is
still missing at the freeze is reported as not computed, and the freeze entry says so.

1. A search configuration that restricts the proposer to free-form checks (arm C), and one that uses templates, the
   formal path and free-form checks as a fallback (arm T).
2. Per-call limits on the proposer's input and output, and the model-call cap.
3. The repository context the proposer sends, built by a selection that a study script can call.
4. In each report's activities: every model call with the provider's token counts where it gives them; every check
   run with its result; every formal run with its verdict and depth, and every model refused for exceeding the limits
   or found vacuous or conflicting; every mutation run; and every skipped activity with its reason.
5. Every model request and reply, kept for the gaming scan.
6. Every finding's reproduction as a self-contained test file and command that the repository's own test command can
   run without importing G6.
7. Scratch copies that contain no repository history beyond the base commit.

## Deviations from the plan's rules (VALIDATION_PLAN.md §0)

1. **Rule 1, local models:** arms C, T and E use a hosted model, under a spending cap the owner approves in writing
   before the freeze. V9's deviations for patch generation and arm 2 apply unchanged.
2. **Rule 3, splits:** as in V9. In addition, verification search is developed on fixtures disjoint from the pool by
   task and by repository, and any pool task opened while building it is left out of this study's analysis.
3. **Rule 4, analysis:** the study is descriptive at feasibility level. It reports two-sided 95% intervals and tests
   no hypothesis, so the sign test and Holm correction do not apply. The bootstrap is stratified by repository, for
   V9's reason (three repositories).
4. **Rule 5, compute matching:** the matched baseline is arm E. On each patch it gets the same model, the same inputs
   and as many calls as the larger of arms C and T made, and asks for tests directly. This replaces
   `g6.reasoning.self_consistency`, because the model's job here is to write executable checks, not to answer. V9's
   arm 2 is an agent session, not a single direct call, and its cap was set before G6 made model calls; its use is
   reported beside every comparison of C and T with it. The calls and tokens each arm actually used are reported beside
   every comparison. Nothing is promoted, so the promotion clause does not apply.
5. **Rule 7, verdicts:** the verdict is `inconclusive` by design unless the gaming checks or the oracle decide
   otherwise, as set out under "Verdict".

## What this study cannot show

- That verification search improves on G6 without it, or on any other method: feasibility has no power for that.
- That G6 verifies, proves or finds all defects, that a finding identifies the defect the label records, or that a
  property shown about a model holds for the program.
- What happens when a person answers G6's questions or confirms a proposal: nobody does in this study.
- How well checks supplied by the calling agent itself work (G6 accepts them; this study does not measure them).
- How search performs with a model other than the frozen one, or on patches written by another agent.
- That the result transfers to other repositories or languages.
- Objectives 2 and 3.

## Decisions the owner makes before the freeze

1. **The spending cap** for arms C, T and E, set in the freeze entry and informed by the cost measured in the
   development smoke run.
2. **The model** for arms C, T and E. The default is the model V9's agent uses, so that search, E and the agent's own
   review share one model.
3. **Reviewers for Experiment 2,** recruited separately from V9's, or the decision not to run it, in which case nothing
   is said about effort.

## The registry entries this study will add

1. This protocol (the entry that registers this file).
2. The freeze: G6's commit, the search configuration, the model and interface, the limits, the cap, the seeds and the
   scripts, registered with V9's feasibility freeze.
3. The record's final hash, committed before any label is read.
4. The feasibility outcome, appended after the run.
5. Experiment 2's pilot outcome, if it runs.
