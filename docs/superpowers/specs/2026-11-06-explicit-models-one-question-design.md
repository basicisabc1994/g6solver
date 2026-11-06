# G6 0.3: explicit models, one question, measured — design

Status: version 2, 2026-11-06, for the owner's review. Version 1 (178ef54) was challenged by Codex. All 16 of its challenges
were read against the code, all 16 are adopted, and appendix A lists each. Nothing here is built until the owner approves it.

## 1. Why

G6's coding study measured whether a harness around Claude Code makes it right more often, on real bugs neither had seen. No
registered test on unseen bugs met the fixed comparison rule. The study log records why:
- Claude Code alone was already right in most runs on bugs whose issue determines the fix.
- Most wrong final patches failed only the maintainers' new tests.
- The frozen reviewer's balanced accuracy was 53.6%.
- In 5 of the last pilot's 10 wrong final patches, the agent named the deciding behaviour and kept the other side.
- Tuning fitted the tasks it was tuned on and did not carry to new bugs.

These are records in the study's plan, read by people and blind auditors, not re-derived here from raw runs. Failing only
the maintainers' new tests is consistent with unstated intent, but does not by itself establish it.

The principle this design is built on: **a checker adds only what it knows that the author does not.** So G6's job is to bring
in information the agent lacks, and to say, for each piece of evidence, whether it is independent and what it covers. There
are three potential sources:
- **the person**, asked about one concrete behaviour;
- **execution**, where candidate patches behave differently;
- **exact engines**, where an independently defined property can be decided.

None of them is independent by default. Each record states its independence and scope, and a record that cannot state them
supports nothing.

## 2. Goals, non-goals, anti-goals

**Goals.** A technical developer working with Claude Code or Codex gets four things:
1. **Proposed behaviour questions.** G6 shows a concrete scenario where the task may not decide the behaviour, with the
   competing outcomes. The person answers it, and exactly that confirmed expectation is enforced on the final patch.
2. **A report that separates evidence by kind.** Code evidence, model results and unconfirmed hypotheses are kept apart.
   Each requirement shows what was checked, by what, and what was not.
3. **Scoped exact checks,** where an engine can decide a named question about an independently accepted property.
4. **A measurement on reproducible cases** from the developer's repository, of whether a specified agent setup with G6
   differs from it without G6. Each failure is reported as mechanical observations, kept apart from hypotheses.

**Non-goals.**
- Any claim of general uplift over Claude Code alone.
- Unattended self-improvement of G6's own code, which stays off and is not promoted.
- New features on the GUI or the TUI/CLI, which are frozen; MCP is the surface of record.
- General formal verification of programs.
- Automatic mining of arbitrary repository history.

**Anti-goals: these would make it a failure even if it works.**
- A question on a task that already decides the answer, which costs the person time for nothing. False questions are
  measured.
- An answer bound more broadly than the person confirmed. A single example never becomes a general rule unless the person
  sees and confirms the rule.
- "Checked" on anything only the author's own test supports.
- Two evidence authorities giving one requirement two statuses.
- A selector that hides which mechanism ran, or why.
- A model's opinion counted as evidence, or several reviews from one family counted as several voices.
- Code left behind a research flag with neither a route into the product nor a stated retirement.

## 3. The pieces

### 3.1 Evidence: one authority

- **The authority.** The product's certificate issuer, `evidence/certificate.py`, is the one rule that decides a
  requirement's status. It already:
  - binds a receipt to the artefact and check hashes and to its ledger position;
  - requires a negative control, even for a formal check;
  - rejects self-authored checks and checks frozen after the artefact;
  - keeps support and refutation apart, in Belnap's four states.
- **ECHA's role.** `research/echa` becomes an audit and analysis view over the same records. It is not a second authority. It
  differs from the issuer in two places:
  - it accepts a formal check with no negative control;
  - it does not bind a claim to its subject, so the same evidence can be retargeted to a patch that never ran.
- **Before anything converts records,** a mapping table fixes, for each evidence kind:
  - the identity of the artefact, model, standard, check and environment;
  - its negative control;
  - the author's independence;
  - its ordering and scope;
  - how conflicting evidence is kept.

  A source record that lacks any of these is refused promotion. The two probe cases above become contract tests first.
- **The renderer.** `harness/patch_report.py` stays the renderer. It already separates unconfirmed checks and the standing
  limits of the specification.
- **What PR #8 brings.** Its dossiers carry independently supplied frozen checkers with positive and negative controls. Those
  protections move into the issuer's inputs, with migration tests that keep them.

### 3.2 A: one behaviour question, confirmed and enforced

The first version is one vertical slice, not a general ambiguity classifier.

1. **Propose candidate choices.** G6 proposes concrete, possibly unresolved choices. Each candidate carries:
   - the task's citation;
   - repository evidence;
   - an executable scenario (input and setup);
   - the competing observable outcomes;
   - any evidence that the task already settles it.

   Sources:
   - the agent's declined decision points (H-decide's tagger, from its final report);
   - disagreement between two readers of the task. Replay 6's second reader is given room to challenge the first reader's
     factors and entry points, not only to fill its table;
   - base-and-patch differences that `differential.v1` already turns into questions.

   **Coverage is unknown, and so is whether the task is determined.** Reader agreement never marks a task determined.
2. **Ask, with a typed behaviour form.** It is distinct from approval, since `mcp/consent.py` asks only for a name. The form
   shows:
   - the exact input and setup;
   - outcome A and outcome B;
   - the cited scope;
   - the options "neither", "the premise is wrong", "the task already says" and "skip";
   - a free-text box.

   The person also chooses whether the answer confirms one case or a stated rule; a rule is shown in full before it is
   confirmed. There is no terminal fallback and no agent answer. A decline, cancel or skip leaves the expectation
   unconfirmed, and it is reported.
3. **Budget and timing.**
   - One question per task by default (owner, 2026-11-06), persistent across retries and MCP calls.
   - The question may be held until candidates exist, when execution is likely to find a better one.
   - Without a dialog, the candidate question is reported, never answered on the person's behalf.
4. **Bind exactly what was confirmed.**
   - The confirmed expectation is stored with immutable digests: the question, its options, the answer, the source baseline
     and the scope.
   - It is compiled into one `example.v1` check, an input with its output, error or effect, through the review machinery that
     exists, and enforced on each later patch with a receipt.
   - Confirmed intent is kept apart from the agent's tentative model. Only accepted predicates are frozen; disputed ones stay
     proposed.
   - Joint consistency is checked before the standard gains a revision. An answer that contradicts an accepted predicate starts
     an explicitly superseding standard, which invalidates the evidence that depended on the old one.
   - CSIR's `standard` refinement holds predicate names only, so it is not the binding. A refinement record built on it must
     also bind each definition and its compiled check.
5. **Measure the slice before it is promoted:**
   - the share of questions that were unnecessary (the task already decided);
   - the questions missed;
   - the harm from wrong answers (induced wrong patches);
   - the person's time.

Reused: `intent_engine`, `requirements_from_intent`, `g6_premortem`, `differential.v1` and `verification_search`'s question
ranking, replay 6's reader protocol (graduated from `validation/`), and `mcp/consent.py`'s transport. New: the
`g6_confirm_behaviour` tool (planned as VS-D5 and never built), the behaviour form, and the store of confirmed expectations.

### 3.3 C: a bounded disagreement collector

- **What it does.** Several candidate patches run on shared inputs. A disagreement is a lead with recorded input provenance,
  never a verdict. The study found the wrong reading is shared and voting amplifies it, so agreement is never acceptance.
- **What happens to a disagreement.** It goes to an exact engine only when an independently accepted predicate already
  distinguishes the outcomes. Otherwise it stays an unresolved difference, or uses the question budget.
- **Scope at first.** Pure functions with supplied shared cases, using `differential.v1` and its result types. `_inputs` is
  not used as a general input generator, since it finds nothing for `x + 1` against `x + 2`. Adapters for state and effects
  come later, each when it is justified.
- **What is measured:** coverage and cost, and how well it tells wrong patches from right ones, separately.

### 3.4 D: scoped exact checks, under a common envelope

- **The envelope.** A common evidence envelope wraps each engine's native, typed result. It holds:
  - the query, its polarity and quantifiers;
  - the assumptions and the model hash;
  - the domain or bound, and the coverage or completeness;
  - the witness or proof kind;
  - a validation receipt;
  - the limitations.
- **Yes, no or unknown applies only to named existential decision queries,** such as "does a counterexample exist within
  this bound", and only with operation-specific mappings. These keep their own native types and are never forced into it:
  - approximation factors (for example `vertex_cover_2approx`);
  - optima;
  - counts with a cap;
  - answer sets;
  - derivation trees;
  - Prolog's "not derivable";
  - Lean's rejections;
  - induction-step traces (`not_inductive`);
  - partly checked kernel answers.

  Belnap conflict stays a fourth state; it never becomes "unknown".
- **Validation is performed and given a receipt by the adapter.** The formal path runs one query unless `recheck` is called
  separately, and that re-check reruns the same solver. So no adapter relies on automatic re-checking.
- **Each claim stays about what it covers.** A model result is about the model. A finding about code needs a trace replayed on
  the real code, as `verification_formal` already does.
- **The kernels are not "exact engines" across the board.** The 22 algorithm kernels are fixed implementations with
  instance checkers of different strength, some approximations and some partly checked, and they are not presented as
  exact.

### 3.5 The selector: a staged state machine with fixed priorities

- **Its stages:**
  1. facts available at the start;
  2. model validation;
  3. candidates and disagreements;
  4. a budgeted next action.

  A feature not yet known is unknown, and the selector reroutes when an observation arrives.
- **How it decides.** It applies fixed, documented priorities and explicit capability checks, with
  `harness/verification_controller.next_experiment` as the scheduler. The mandatory tests stay outside the optional choice of
  A, C or D. It always reports which mechanism ran and why, and the person can override it.
- **Learning comes later.** Learned routing from PR #8's `MetacognitiveMethodSelector` waits until there are independently
  evaluated paired outcomes for each mechanism, and an admission service. Until then, PR #8's `MethodRouter` is used only to
  plan the methods for an explicit evidence need.
- **The plan check.** Plans run through `g6_compose`, the product's executor, with a small policy validator. CSIR's judgement
  over `MissionIR` is a structural plan screen. It catches cycles, missing providers, authority and effect mistakes, and
  unordered writers. It does not show independence, because its rule against a node checking its own output compares
  component names only. It stays research until it catches a failure the policy validator misses, and it is never described
  as an independence check.
- **What the selector does not use:**
  - CSIR's `solvability` describes the modelled problem, and is never advice about expected gain;
  - ECHA's `competence` is a family success rate, not a mechanism's benefit, and is not used to rank mechanisms.

### 3.6 B: measurement on reproducible cases

- **The first supported cohort.**
  - Python repositories with an explicit replay manifest, reproducible base and fixed environments, and issue text.
  - Bug pairs the developer selects. There is no automatic history mining.
  - G6's own repository, g6solver, is the worked example (owner, 2026-11-06).
- **A read-only readiness report comes first.** It covers:
  - the runtime and resources;
  - the installer and dependencies;
  - the credentials and provider route;
  - the task data;
  - oracle isolation;
  - the count of usable cases.
- **What it measures.** A reproducible agent profile, not the developer's exact interactive environment, and it says so.
- **What it keeps from the study:**
  - the agent sees only the base commit;
  - the oracle is kept in a separate image;
  - gold, base and empty-patch controls;
  - containers, with no unconfined fallback (owner, 2026-11-06).
- **Cohorts and missingness.**
  - Development, selection and final cohorts are frozen by name. An evaluation cohort is retired once it informs routing or an
    adaptation.
  - Scheduled, completed, failed and missing runs are reported per arm, with reasons, beside the complete-pair comparison and
    its sensitivity to missingness. Repeats are clustered by bug.
  - G6 with a person is reported as one combined intervention, with the person's time and the extra compute. An
    equal-information, equal-budget comparison is required before anything is claimed for the harness alone.
  - Past bugs are not assumed unseen merely because they are historical.
- **The report has three parts:**
  - **Mechanical observations from receipts:** baseline headroom, the first failing stage, whether the checker detected the
    failure, and whether the named reproduction later passed.
  - **Semantic labels,** such as "needed unstated intent", supplied only by a person or an independent frozen audit, with
    provenance and uncertainty. Otherwise the field says unknown.
  - **Hypotheses,** marked as such.

  "Passed the named reproduction" is never "the rule is fixed": generalisation needs an independently defined rule oracle.
  Headroom and hindsight-selected layers are labelled retrospective diagnostics.
- **What goes back to G6.** Detailed held-out diagnoses go to an owner-only report. What a proposer may receive is defined
  before any run.
- **Reused:**
  - from `claude/runtime-k` and `claude/replay6-followup`: `selection.py`'s filters, which the first cohort leaves to the
    person, and `run.py`'s validate, run and check steps with their image split;
  - `compare.py` and the within-run pairs;
  - `metaharness/evaluate.py`'s committed analytic power, with study resampling added;
  - `method_rca`, `tick_rca` and `lessons` for statistical diagnostics only;
  - the graduated `core/adaptation_gate.py` and AHI's `FailureCase`, which holds a root-cause hypothesis and never asserts a
    cause;
  - `metaharness/gate.py` as the held-out gate. PR #8's receipt admission and overlap checks are carried over with tests that
    show they still hold.

### 3.7 Changes to G6 itself

- Any change G6 proposes to its own harness goes through the adaptation gate and the held-out gate. It is adopted in shadow at
  most, and only a person merges.
- Repairs to G6's own code follow section 9.

## 4. One coding task, end to end

1. A task arrives, through the plugin's hook or an MCP call. `g6_premortem` gives the intent contract.
2. The selector's stage 1: G6 proposes candidate choices from the task. Whether to ask now or hold the question is decided by
   the budget rule (3.2).
3. The agent writes its patch, or several candidates.
4. Stages 2 and 3:
   - the declined-decision tagger reads the agent's report;
   - candidates' disagreements are collected (3.3);
   - scoped exact checks run where a property is accepted (3.4);
   - the one question is asked if it has not been.
5. Checks:
   - the confirmed expectation, compiled as `example.v1`, on the patch;
   - the repository's own tests, tiers 1 and 2.
6. The report (3.1): each requirement with its evidence and its kind, what was not checked, proposed and answered questions,
   and model results kept apart from code evidence.
7. A reproduced finding goes to the K-mode repair loop, applied automatically when the fix is clear and offered otherwise
   (section 9).

## 5. What is measured before each piece is promoted

The uplift study has stopped. Each piece is measured lean, and published with its result and the decision taken on it,
beside a statement that no general uplift was shown:

| Piece | What is measured | Bar | Cost |
|---|---|---|---|
| Replay cohort | Gold, base and empty controls pass and the patch is captured | all controls behave | containers |
| A, the slice | Unnecessary, missed and harmful questions, and the person's time, on the frozen cohort with a simulated maintainer as an upper bound; then real developers | set before it runs | subscription runs, then people's time |
| A, location | H-decide's registered test | as registered (gap ≥ 20 points, ≥ a third of wrong runs tagged) | none |
| Evidence | The probe cases (a formal check with no control, retargeted evidence) are refused | all refused | none |
| C | Coverage, cost and how well it tells wrong patches from right, on recorded bugs | set before it runs | small |
| D | One named decision query per adapter, with its mapping tests | mapping tests pass | none |
| Selector | Paired outcomes per mechanism, before any learning | set before it runs | runs |
| B | The readiness report and the controls on g6solver | the report reproduces the study's own reading of the same cases | containers |
| Self-improvement | One problem class. Problem generator, semantics, evaluator, limits and held-out distribution are frozen outside what the candidate can write. The candidate proposes an implementation or a representation, and its correspondence to the fixed problem is checked independently. Feasibility, optimality and generalisation are reported separately, tuned and held-out side by side | set before it runs | subscription runs |

The last row is the test of the owner's view that self-improvement can work where there is an independent source of truth.
If the system writes both the model and the candidate, it can improve by making the problem easier, so the model stays
outside its reach. Each row is reported whatever it shows.

## 6. Capabilities, adapters and retirements

Every research module either gets a route into the product, or is retired with its reason stated. None stays behind a flag
with neither.

| Code | Valid job | Adds what information | Trust boundary | Integration test | Decision |
|---|---|---|---|---|---|
| `evidence/certificate.py`, `core/ledger.py`, `core/vault.py`, `core/adaptation_gate.py` | Already the product | — | — | existing | use directly |
| `research/echa` records, claims, trace | Audit and analysis view over issuer records | none new; explanation | issuer is authoritative | ECHA status never overrides issuer | wire as a view; flag removed |
| `research/echa` custody | Its binding structure, for receipts | artefact binding | its records are partial falsifiers, not independent support | binding kept, records not promoted | reuse structure; retire its records as a source |
| `research/echa` competence | None valid for ranking | — | — | — | retire for selection; keep as a research analysis |
| `research/echa` episodes | Run records for B | run history | bounded summaries, not artefacts | — | wire into B; flag removed |
| `research/ahi` contracts, lifecycle | Already wrap the graduated gate | — | — | — | retire the duplicate wrapper; `FailureCase` and `Funnel` move to core for B |
| `research/mission_bridge` `MissionIR`, checks, CSIR judgement | Structural plan screen | structural defects | names are not independence | must catch a failure the policy validator misses | stays research until it does |
| CSIR `standard` | Name-level revision chain | revision history | names only | contradictory refinement is refused | used only with bound definitions (3.2) |
| CSIR `solvability` | A statement about the modelled problem | — | caller assertions | — | retire as selector advice |
| CSIR `certificate` | Adapter to the issuer | — | — | — | already delegates; keep |
| `research/mission_bridge` fixtures, recorded replay | Tests | — | runs no operations | — | test-only, stated |
| PR #8 `MethodRouter` | Plan methods for an explicit evidence need | — | caller-supplied models | — | use in 3.5 |
| PR #8 selector, admission | Learned routing | — | needs an admission service | paired outcomes first | later |
| PR #8 dossiers, adaptation gate | Their controls and admission checks | — | — | migration tests keep them | fold into the issuer's inputs and `metaharness/gate.py` |
| `complexity` (PR #2) | Scope and impact discovery | the cone a change may reach | static | — | wire into A's scenario proposal |
| `algorithm_design` kernels, `autonomous` (PR #3) | Fixed kernels with instance checks; an optional bounded executor | witnesses where checked | not exact across the board; host-owned standard | per-kernel mapping tests | D, operation by operation |
| Replay 6, H-decide, `compare.py` | 3.2, 3.6 | — | — | — | graduate from `validation/` |

## 7. Order of work, by dependency

Each slice is its own change, with its tests first and a Codex review loop of at most four reviews and three repairs. Findings
from the last review go to their own follow-up change.

1. **A frozen replay cohort.** Base, fixed and empty controls, and patch capture. The two host-executor defects are reproduced
   and repaired first: a file written inside the box did not reach the kept patch, and a review called an empty patch clean.
2. **Minimal evidence semantics.** Identity and polarity in the issuer's terms, and the confirmed-example semantics, with the
   probe cases as contract tests. The existing report renders the first results.
3. **The question slice, end to end.**
   1. Propose one concrete scenario.
   2. Show it with the behaviour form.
   3. Get the answer from a person, or from the simulated maintainer as an upper bound.
   4. Compile exactly that expectation.
   5. Enforce it on the final patch.
   6. Measure unnecessary, missed and harmful questions, and the person's time.

   **This is the slice whose failure would waste the most later work, so it comes first.** The client transport is tested
   before any claim of human use.
4. **The report and the B importer,** expanded only as far as the slice showed is needed. C's disagreement collector, and
   one correctly scoped exact-engine adapter, each as its own increment.
5. **The staged selector** over the mechanisms that exist. Learning only after paired outcomes and admission.
6. **The self-improvement measurement,** with its outside oracle.

The website shows each piece only once its section 5 row has run, with its result.

## 8. Left to the builder, and the owner's decisions

**Left to the builder:** module names inside the existing packages; the exact replay manifest format; how the behaviour form
renders in each client.

**Decided by the owner (2026-11-06):**
1. **The question budget:** one question per task by default. The person can ask for more, or for none.
2. **Containers:** B uses containers on the user's machine, as the study did. Without them, B refuses rather than running
   unconfined.
3. **B's worked example** is G6's own repository, g6solver.
4. **The site's tagline and the owner-held lines** stay as they are.
5. **Repair is automatic when the fix is clear, and every improvement is shown clearly to the person using G6** (section 9).

## 9. Repair: automatic when the fix is clear, and shown clearly

**When a fix is clear.** All four must hold:
- a finding is reproduced by a test that fails on the code as it stands;
- the repair makes that test pass;
- the repair breaks no test that passed before, on the same checks;
- it changes nothing the frozen standard or a confirmed expectation forbids.

The repair is then applied automatically. In the product, that means to the working tree. For G6's own code, it means on its
branch: merging stays a person's step.

**When it is not clear,** the repair is offered, not applied. That covers:
- a finding with no failing test;
- a repair that breaks another test;
- two plausible repairs;
- a choice only the person can make, which becomes A's question.

**What the person sees, for every repair applied:** "Fixed: <what was wrong>". Below it:
- the failing test, before and after;
- the checks that still pass;
- the diff;
- what was not checked.

This is in the report, before anything else. An improvement is stated in the terms that were measured, never as "now correct".

## 10. Public wording

The site describes the product within these limits:

> G6 proposes behaviour questions where the task may not decide, records the person's answer, and runs scoped checks where
> supported. It reports model results separately from code evidence. Optional replay measures a specified setup on
> reproducible cases, and its explanations separate observed failures from hypotheses.

Study statistics carry their denominators, their cohorts and how to reproduce them.

## Appendix A: Codex's challenge of version 1, and what changed

| # | Challenge | Change |
|---|---|---|
| 1 | The question finder rebuilds the author's abstraction; agreement is not determination | 3.2 step 1: candidates with fields; coverage unknown; precision measured |
| 2 | Adding a predicate name enforces nothing and can freeze a contradiction | 3.2 step 4: `example.v1` binding, consistency check, superseding standard |
| 3 | ECHA and the issuer disagree; evidence can be retargeted | 3.1: the issuer is the authority; mapping table; probe cases as tests |
| 4 | Yes/no/unknown loses meaning for most engine outputs | 3.4: an envelope with native payloads; three values only for named existential queries |
| 5 | "Each Z3 answer re-checked" is false | 3.4: adapters perform and receipt validation |
| 6 | The instrument is a curated laboratory, not a history importer | 3.6: first cohort with manifests and selected pairs; readiness report |
| 7 | Semantic causes are not plain-code measures | 3.6: observations, labels and hypotheses apart |
| 8 | The selector's signature arrives after the work | 3.5: staged state machine, unknown features, fixed priorities |
| 9 | CSIR is a structural screen; A5 is not independence | 3.5 and 6: a plan screen, not independence; `g6_compose` plus a policy validator |
| 10 | Execution gives a difference, not the intended answer | 3.3: a bounded collector; route to D only with an accepted predicate |
| 11 | One question needs an interaction design | 3.2 steps 2 and 3: the behaviour form, a persistent budget, holding the question |
| 12 | Self-improvement against a self-written model is not independent truth | 5: the model is frozen outside the candidate's reach |
| 13 | Adaptive use contaminates the measurement | 3.6: frozen cohorts, retirement, missingness, combined-intervention reporting |
| 14 | Wiring every package rebuilds graduated code | 6: a capability table with retirements |
| 15 | The order postpones the decisive test | 7: cohort, semantics, then the question slice first |
| 16 | Public copy must separate observations, plans and the product | 10 and 5: wording and published decisions |
