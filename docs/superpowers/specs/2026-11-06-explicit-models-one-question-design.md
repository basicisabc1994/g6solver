# G6 0.3: explicit models, one question, measured — design

Status: version 2, approved by the owner on 2026-11-06. Version 1 (178ef54) was challenged by Codex. All 16 of its challenges
were read against the code, all 16 are adopted, and appendix A lists each. Building follows section 7's order. Section 11 is
a proposed amendment (2026-11-07, version 2 after Codex's challenge in appendix B) and is not approved. Sections 12 (the
Context Engine) and 13 (both agents know both allowances) are proposed on 2026-11-08 and are not approved.

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
  - the witness or certificate kind;
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

## 11. Proposed 2026-11-07, not approved: the rigour architecture as a test of what works in practice

**Status.** Version 2, proposed for the owner's approval. Version 1 (8e72041) was challenged by Codex; all twelve challenges
were read against the code and adopted, and appendix B lists each. Nothing here is built until the owner approves it, except
where an item restates what sections 3.1 and 3.2 already require, and it changes no step's order in section 7.

**Source.** The owner's architecture for intellectual rigour and metacognition (six subsystems under one controller), read
with Dörner, *The logic of failure* (Phil. Trans. R. Soc. B 327, 1990), and Shankar et al., *Who validates the validators?*
(arXiv 2404.12272, 2024). It is used here as a checklist against this design. Its own citations of other sources are not yet
checked, and nothing from it is quoted publicly.

### 11.1 Where this design already meets it, and what that rests on

| The architecture's subsystem | Where this design has it | Gap that matters in practice |
|---|---|---|
| Epistemic kernel: typed claims, four states, provenance | 3.1: the issuer keeps support and refutation apart in Belnap's four states; each record states identity, control, independence and scope | custody is checked for support but not for refutation (item 1); no diagnostic of what each check kind detects (item 4) |
| Necessity under a model versus permission | 3.4: a model result is about the model; merge authorisation is separate from the assessment | none for 0.3 |
| Seek the evidence that could change the conclusion | 3.3: a disagreement is a lead; negative controls; self-authored checks refused | the confirmed answer's check needs a witness of the rejected outcome (item 3) |
| Generative design (C-K theory) | step 6 tests composition against an outside oracle | none built; no failing test shows a need |
| Choosing the next experiment | 3.5: fixed priorities, `next_experiment` | no record of why a mechanism ran and what it expected to observe (item 6) |
| Action in a dynamic system (Dörner) | section 9: a repair must pass its test and break nothing | a repair is applied to a working tree that may have changed since it was tested (item 5) |
| Intent and criteria drift (Shankar) | 3.2: one concrete question, typed form, exact binding, explicit supersession | nothing shows the compiled check observes what the person confirmed (item 2); drift is not measured (item 7) |
| One controller | 3.5: the selector, which says what ran and why | as above |

**What the study's records motivate, and what they do not show.** The frozen review had 138 false alarms among 279 right
patches and 61 misses among 141 wrong ones on the named cohort and oracle, a balanced accuracy of 53.6%. Of 153 later findings
in G6's own review loops whose lines could be located, 66 were on lines a repair wrote. These motivate discriminating
diagnostics for checkers and stronger checks around repairs. They do not show that those actions were unchecked, which repair
caused which defect, or that the process proposed here would prevent them.

### 11.2 Amendments

1. **Custody for both polarities.** A failed run counts as a refutation only when G6 observed its execution and controls, as
   a pass already must: a failure without resolved execution and control records is an unconfirmed failure, shown as a
   caveat. The accepted-outcome reference that makes a failing check meaningful also needs observed provenance. Execution
   custody is kept separate from the independence policy for each polarity, so an author's own genuine reproduction is not
   discarded. The issuer is bound, so the rule is enforced in the unbound adapter in front of it (`evidence/mapping.py`,
   `harness/requirement_evidence.py`), and the issuer's failure branch goes on the issuance list. Tests: forged, missing,
   mismatched and stale negative records, and a conflicting pass and fail. **Why first:** a supplied failure can otherwise veto
   a requirement G6 never ran (reproduced offline, at the library interface). **Where:** its own change on the evidence line,
   after slice 2's follow-up, which edits the same files; the 3.1 mapping table carries the distinction.
2. **The check observes what the person confirmed.** The slice records and tests the correspondence from the confirmed
   scenario and scope to the executable setup, entry point, observation and assertion. The accepted case and the
   rejected-outcome witness exercise that same observation. The supported adapters and comparison semantics are stated: this
   version supports synchronous functions and their return value, error or argument effect, as `example.v1` does. Anything
   else, such as an HTTP response, database state or async handler, stays confirmed but not enforced, with the missing check
   shown. Translation failures are counted in A's harm and coverage, apart from a person's mistaken answer. This restates
   3.2's "bind exactly what was confirmed". **Where:** slice 3, through its review loop.
3. **A witness of the rejected outcome, and no false "vacuous".** The issuer's negative-control rule stays mandatory. A adds an
   adapter-level test that the control runs the confirmed setup, shows the named rejected observation, and is rejected by the
   frozen check for the intended assertion; the accepted-outcome reference runs too. The control comes from the base or a
   candidate where one shows the rejected outcome, otherwise from a bounded, isolated mutant that implements it. A collection
   error or unrelated crash never counts. If no witness can be made, the report says "negative control unavailable;
   enforcement not established", and the answer is kept. **Where:** slice 3.
4. **Scoped diagnostics for each kind of check, not an accuracy badge.**
   - First, a control diagnostic labelled as such: gold against the unfixed base, with empty and base counted once.
   - A repair-discrimination card is published only on independently labelled, runnable candidate patches, with near misses
     as their own stratum. Labels are relative to the frozen predicate, standard and oracle version, not to complete program
     correctness.
   - Each card shows rejected wrong out of eligible wrong, rejected right out of eligible right, independent bugs, distinct
     patches, control roles, check, criteria and environment versions, and coverage. Inapplicable, unrun, timed-out, errored
     and unlabelled cases are shown with their own denominators and never counted as detections. Zero observations means
     unavailable.
   - The estimand is declared beforehand: one observation per independent bug or stratum with binomial intervals, or a
     patch-weighted rate with bug-cluster resampling, as 3.6 requires.
   - It is described as "historical rejection rates on this scope", never as the chance that a given finding is right.
   - **Cohort discipline:** the card's population, what it may disclose and its refresh policy are fixed before evaluation. A
     criterion change needs independently versioned labels. Once a card informs tuning or routing, its cohort is retired for
     confirmatory claims and reuse is labelled retrospective. Held-out per-case diagnostics never go into an agent's repair
     feedback.
   - **Reuse:** `core.binomial`'s bounds and a small count-and-render adapter over executed receipts and scoped labels. Not
     `lessons.calibrate` or `trusted`, which bound majority agreement by bug, a different quantity. `CodingReplay` stays for
     measuring model verdicts.
   - **Where:** step 4.
5. **Repairs applied to the state they were tested on.**
   - Capture the pre-repair state and the tested result. Apply only if the receiving working tree still matches, and on a
     conflict preserve the concurrent work. Rerun the required checks on the resulting tree, keep a recoverable diff or
     snapshot, and record the action's status across an interruption.
   - Record as separate outcomes whether the original reproduction was restored, the preservation checks passed, which checks
     are unknown or failed, and whether a review was received. Count distinct applied repair events, including interrupted
     ones, not MCP retries. An unresolved event is never shown as "Fixed".
   - For review, promise only that the complete repair diff and its context were supplied, omissions disclosed, and findings
     reproduced where possible; this is not line coverage.
   - Adoptions keep their gate and monitor records (`metaharness/monitor.py`).
   - **Reuse:** `repo_tests.snapshot`, `verification_controller`'s stale-observation rule, and the completion loop's reruns of
     earlier reproductions. `action_logic`'s textual `VerificationContract` is not required here.
   - **Where:** section 9's repair path.
6. **The selector records each decision as an event.** The record holds:
   - the decision id;
   - the artefact, standard and environment versions;
   - the candidate mechanisms and their applicability;
   - known and unknown features, with their provenance;
   - the policy version and the reason;
   - the cost cap;
   - the hypotheses, with the observable outcome expected under each (qualitative outcomes are allowed);
   - whether the result discriminated between them;
   - the actual receipt and how it was read.

   Reversibility comes from host-enforced capabilities and a concrete recovery plan, never from location alone. The
   consequence of error is the owner's policy or an attributed declaration, and otherwise unknown. Each field is used only if
   the design states how it changes a fixed priority or admissibility rule. These logs alone cannot train a router: learning
   still needs independently judged, paired mechanism outcomes, costs and a declared utility (3.5). `lessons.Prediction` is
   kept for directional numeric hypotheses only. **Where:** step 5.
7. **Criteria correspondence and drift.**
   - The store and supersession are slice 2's (`harness/confirmed_expectation.py`), which slice 3 wires in. A supersession
     preserves the old and new answers, the stage at which each was given, the reason, the scope and the standard and check
     versions, and reruns the affected evidence.
   - Within the normal one-question flow, the slice measures correspondence at each step on a fixed scenario set: the options
     shown, the recorded answer, the compiled observation, the repair's objective and the final report. It records which
     outcomes each version accepts, and separates a changed specification from a mistranslation.
   - Human criteria drift is measured only in a separate, predeclared evaluation with paired judgments of the same fixed cases
     before and after seeing outputs, with the order controlled and reasons recorded. It reports invitations, exposures,
     pairs, attrition and changes; with too few pairs it says unmeasured.
   - **Where:** slice 3's measurement, then that evaluation.

### 11.3 Route or retire: the components this architecture names (section 6's rule)

"Retire" here means from the 0.3 product integration plan. No public library API or shared dependency is removed, and no
confidence or status mapping changes a requirement's status on its own (3.1).

| Code | Today | Decision proposed |
|---|---|---|
| `harness/verification_controller.next_experiment` | review ordering wired in, off by default; also used by `algorithm_design/autonomous.py` | the low-level scheduler under the selector (item 6), not the selector itself |
| `cogarch/decision_making/voi.py` | library functions and an invokable next-experiment policy (`policies.py`); no coding integration | stays optional; not the selector until paired outcomes exist |
| `metaharness/lessons` (`calibrate`, `trusted`, `gap`) | these functions have no production caller; the module is used by `reflex`, `tick_rca` and the loop | not the card's statistics (item 4) |
| `metaharness/coding_replay` | measures model verdicts against labels; used by the study, no product caller | stays for model verdicts; not deterministic card execution |
| `action_logic` controls | bound; used by generic material-action gating, not by coding repair | unchanged; not required on the repair path (item 5) |
| `core/evidence` status vocabulary | bound; the adjudicator emits it, and action gating uses the module's records and grade, conflict and refutation functions | a lossy display correspondence to the issuer's four states is documented; never a promotion adapter |
| `formal/four_valued` modal frames | library functions and invoke ops; its nonmodal parts are used by `formal/contract.py` | frames stay optional; the shared four-valued implementation stays |
| `debate` | no block, no production caller; one injected caller | not an evidence path; may generate alternatives, never counted as independent votes |
| `reliability_pipeline/metacognition.py` | disclosure only, no production caller | not needed by this slice; left as an unintegrated library |
| `workforce/mutation_check` | opt-in in patch review; also run by `bundles/agent_change.py` | suite-adequacy reporting, kept apart from item 4's patch rejection rates |
| `reasoning/structure_map`, `metaharness/level2.choose` | no production caller | parked with step 6 and the unattended loop |
| `metaharness/monitor.py` | post-adoption monitoring | the record of adoption effects (item 5) |

### 11.4 Not adopted now, with the reason

- **A general modal or deontic logic engine, a C-K design engine, POMDP or model-predictive control, an
  expected-value-of-sample-information optimiser:** none is shown necessary for 0.3, and no failing test shows a need.
- **What they stand for is kept as concrete obligations:**
  - permission separate from evidence;
  - the confirmed criterion's correspondence to its check (item 2);
  - bounded observe, act and recheck, with stale-state rejection, stopping and recovery, for working-tree writes (item 5);
  - known unsupported cases listed.
- **Detecting unknown unknowns** is excluded as a guarantee. Measured coverage of a predeclared scenario set is still needed
  for the missed-question count.
- **The architecture's state names:** G6 keeps its internal state names. Public copy says "checked" or "challenged", never
  "verified" as a claim (owner, 2026-11-05), and each statement carries its artefact, standard, check and scope.

## 12. Proposed 2026-11-08, not approved: the Context Engine, a person's own knowledge searched three ways

**Status.** Version 1, proposed for the owner's approval. Nothing here is built until the owner approves it and Codex's
challenge has been read and answered. It changes no step's order in section 7.

**Source.** The owner's direction of 2026-11-08:
- `ctx.elastic` as a capability for onboarding and specialisation;
- G6's own skill and MCP tools, not Elastic's Agent Builder or Elastic's MCP server;
- no built-in hybrid retriever from Elasticsearch;
- semantic search over large data by `ctx.colbert`;
- graph search as the third mechanism;
- an agentic search layer that chooses among the three;
- G6 indexing its own code.

An outside analysis the owner shared was read as a list of ideas. Its claims about Elastic were checked against Elastic's
documentation, and two were corrected:
- Agent Builder needs an Enterprise licence on a self-managed install.
- The `rrf` retriever is reported to be licence-gated.

The owner authorised porting the earlier private repository's components under G6's licence.

### 12.1 Why it belongs in this design

- **A fourth source.** Section 1's principle is that a checker adds only what it knows that the author does not. A person's
  own documents are a fourth source of what the agent lacks, beside the person, execution and exact engines: design
  decisions, conventions, incident reports, and specifications kept outside the repository.
- **It serves A directly.** 3.2 asks each candidate question to carry "any evidence that the task already settles it". A
  cited passage that settles the scenario turns a question into a citation, which is the cheapest way to cut unnecessary
  questions (A's first anti-goal).
- **A passage never confirms an expectation.** A document is not the person. A proposal backed by a passage stays proposed
  until the person confirms it, or names that source as normative for the repository.
- **It is the onboarding step for specialisation.** A person points G6 at their material, which is searchable at once, and
  G6 can propose a skill that uses it. A proposed skill is a hypothesis until it is measured (12.8).
- **Independence is stated, not assumed** (section 1). A passage's record says where it came from, which version, and
  whether the agent could already read it. A file in the repository the agent was given is not new information.

### 12.2 What exists (surveyed 2026-11-08, read from the code)

| Need | Current G6 | Earlier private repository | Decision |
|---|---|---|---|
| Passage identity and provenance | `knowledge/_types.py`: `Source`, `Document`, `Passage`. `passage_id` binds the source URI, source hash, document hash and offsets. Trust is computed and always untrusted | — | use as is; every backend indexes G6 passages |
| One index contract | `knowledge/_index.py`: `Index`, `IndexBackend` (with `writable` and `persistent`) and a backend registry. Backends are combined by rank, never raw score | — | use as is |
| Convert, chunk, re-check | `knowledge_converter`; `knowledge_chunker`, with checked offsets; `knowledge_manifest.verify_passage`, which answers matches, changed, source unavailable or unverifiable | — | use as is |
| Local full-text | `knowledge_indexer.SqliteIndex`: SQLite FTS5 BM25, incremental by passage id, with a storage budget and a tamper check | — | the default backend, needing no install |
| Elasticsearch | `ctx_elastic`: read-only search. Fields are checked against the mapping. It reaches a local cluster over HTTP only, takes its key from the person's key file, and is tested against a fake server | A writer (index, bulk, create, delete, reindex, aliases) and a 25-tool MCP server. It takes keys from environment variables and lets the cluster generate ids, stores no provenance, and runs caller- and model-written query and script JSON unchecked | rewrite the writer inside `ctx_elastic` on `g6.core.outside`, with typed inputs only; do not port the MCP server |
| Meaning | `ctx_colbert`: ColBERT, then sentence embeddings, then BM25. It loads weights only from the person's cache, holds passages in memory (up to 2,000) and re-encodes them on each call | A RAGatouille wrapper that re-indexes everything on any change; a PLAID index that is disabled on Windows and rebuilt on every ingest | build a persistent index (12.3) |
| Relationships | `ctx_cognee`: the shortest chain between two labels over a Cognee server's graph, whose edges are model-generated and carry no passage ids. Code graphs in `design_governance` and `complexity.impact_cone` | `knowledge_manifest`'s unused `concept_edges` table (source, target, type, weight, passage id); a raw-Cypher Neo4j adapter; an OWL store with a `run_code` op | port `concept_edges` as the seed; do not port the Cypher or `run_code` ops |
| Fusion | `g6.context.fuse`: rank fusion in Python over `ContextItem`, with near-duplicate merging guarded on numbers and negation, and a budget | Python RRF in `ctx_rag`; a keyword router that picks one backend | use `fuse`; add a `Hit`-to-`ContextItem` adapter and a fusion over `Searched` results |
| Untrusted text | computed trust, `grounding.instruction_signals`, the `context` scrubber and `ContextItem`'s rules | — | use as is |
| Outward acts | `sources/_gate.py`, through `action_gating` | — | gates every write to an outside index |
| Proposal lifecycle | `ctx_ace.Store` (a proposal until approved); `metaharness/_code_safety.skill_text_problems`; `component_creator`'s SKILL.md render and conformance check | a skill emitter whose evidence is a folder path | reuse, citing passages |
| Permissions | none beyond sensitive-op allow-lists | an ACL in `context_engine` that failed open (recorded as CTX-009 and not ported) | new (12.6) |

### 12.3 Three ways of searching, one contract

- **Exact terms and structure: `ctx_elastic`, or `SqliteIndex` when no cluster is installed.**
  - Full-text search with fields checked against the mapping, exact filters, and counts and aggregations, all through typed
    inputs.
  - It uses only Elasticsearch features from the free tier: full-text, vector search and structured queries. It does not
    use the `rrf` retriever, ELSER or `semantic_text`, document-level security, Agent Builder or Elastic's MCP server.
  - G6 never bundles Elasticsearch. The earlier repository vendored a copy, which stays out. The person installs it, and
    without it the local index answers.
- **Meaning: `ctx_colbert`, made persistent.**
  - Token vectors are kept on disk by passage id, with incremental add and remove, batch encoding and a device setting.
  - The weights check stays: only a complete safetensors copy already in the person's cache.
  - A spike chooses the index library before the design names one, comparing PyLate's own indexes with a PLAID build on
    Windows as well as Linux. The archive's PLAID path was disabled on Windows.
  - Its scale is stated from measurement, not claimed.
- **Relationships: a passage-linked graph.**
  - Nodes are entities and documents. Every edge carries the passage id that states it, from `concept_edges`.
  - Entities come with exact offsets from `ctx_langextract`. A code repository's own structure comes from
    `design_governance` and `impact_cone`.
  - An edge a model proposed is marked as such, and is a lead, not evidence.
  - A multi-hop query returns the cited passages along its path, never a bare chain.
  - Cognee stays an optional outside service.
- **Identity across backends.** Each backend stores the G6 passage id and source, so one passage found two ways is still one
  passage. The Elasticsearch writer sets `_id` to the passage id, so a repeated upload is idempotent. It also stores the
  source, hashes and offsets, so a hit cites the original document rather than the cluster's record.
- **Fusion is G6's.** Results are combined by rank (`g6.context.fuse`), never by raw score, through one adapter from `Hit` to
  `ContextItem`. Every fused item keeps each backend's rank and reason.

### 12.4 The search planner: choosing among them

- **A staged planner with fixed, stated priorities,** as 3.5 requires of the selector, and with the same event record
  (11.2 item 6):
  - exact identifiers, paths, error strings and API names go to exact search first;
  - conceptual questions go to meaning;
  - questions about what depends on, decides or contradicts something go to the graph;
  - results are fused when more than one route applies.
- **Reading and stopping.** The planner reads each result, and may reformulate or widen the search within a stated budget of
  searches and tokens. It stops on coverage, on the budget, or when a search finds no new passage.
- **Only typed queries are admitted.** The model may propose the next search, but the planner takes no query text in a
  cluster's own syntax and no model-written query JSON, as the earlier repository ran unchecked.
- **Learned routing comes only after paired outcomes exist** for each mechanism (3.5). The archive's keyword router is a
  starting rule, not a learned one.
- **"Extensible" is the claim, with its measured limits:** index size, latency, and recall at each size. Public copy never
  says "infinite".

### 12.5 Onboarding a person's knowledge, and proposed skills

1. **Add a source:** a folder, a repository, files or a fetched URL.
   - G6 converts and chunks it, and indexes it into every installed backend with provenance.
   - It records the source in a manifest with its scope (12.6).
   - The source is searchable at once, with citations.
   - Adding is a sensitive operation, confirmed in the client's dialog, with no terminal fallback.
2. **Refresh.**
   - A sweep re-reads each source by its git commit, file hash or fetch, and re-checks passages with `verify_passage`.
   - It deletes a changed or removed source's passages from every backend, and indexes the new ones.
   - A task's report records a context snapshot: the index generation, and the ids and hashes of the passages used. So what a
     decision relied on can be reconstructed later. Binding evidence to bytes is already the issuer's rule (3.1).
3. **Propose a skill.**
   - From indexed sources, G6 drafts a provisional skill: a SKILL.md naming its sources, its search policy and token budget,
     the tools it may use and its checks. Every factual line cites a passage.
   - It is checked by `skill_text_problems` and by `component_creator`'s conformance check.
   - It stays a proposal until the person approves it in the client's dialog (`ctx_ace`'s lifecycle).
   - It stays provisional until its 12.8 row has run. Approval installs it; it does not make it expert.
4. **MCP.** G6's own tools are added, with names fixed from the canonical vocabulary (`00_INDEX.md`):
   - add a source;
   - search, fused and cited;
   - fetch a passage with its provenance;
   - list sources;
   - refresh;
   - assemble a context pack within a token budget;
   - propose a skill.

   One section of the `using-g6` skill tells the agent when to use them. REST, the CLI and the GUI stay frozen.

### 12.6 Rules that hold on every path

- **Scope before retrieval.**
  - Each source is registered with a scope: at least the person and the project.
  - Every backend query is filtered by scope inside its adapter, where a caller cannot widen it.
  - Results are checked again against the source's scope record before they leave G6. A model never decides access.
  - A removed or revoked source's passages are deleted from all three backends and from caches.
  - This is new code, and the leakage test comes first (12.9).
- **Retrieved text is data.**
  - Passages keep their computed untrusted trust and pass the tripwire.
  - They are scrubbed for secrets before they reach a model.
  - They can never authorise a tool action or change a policy.
  - A document with a planted instruction is a test fixture on every path.
- **Writes to an outside index** go through `sources/_gate` and `action_gating`, with the key taken from the key file only.
  They are local by default, as the reader is.
- **Freshness.** An item past its stale time is refused (`ContextItem.stale_after`), and a changed source invalidates
  anything that cited it.
- **Private material** stays on the person's machine, and tests use synthetic corpora. Nothing indexed reaches a public
  branch, digest or record.

### 12.7 G6 indexing G6

- **Search and understand.** G6's own repository is the first corpus. It is g6solver, B's worked example (section 8).
  - The corpus covers code with file and line provenance, the catalogue, the SKILL.md files, the failure map, the
    specifications and the study's public records.
  - Code structure comes from the import graph and `impact_cone`; text and meaning come from the other two mechanisms.
  - It is also where the Context Engine is measured first (12.8), on a corpus whose answers are known.
- **Fitness for a use case.** Given a person's use case, G6 retrieves the components that claim it, with:
  - their declared failure modes ("guards" and "introduces");
  - their limits;
  - their conformance results and certificates.

  It reports, with citations, what fits, what is unchecked and what is missing. A retrieved description is a claim to check,
  never evidence that the code does what it says.
- **Indexing G6's code.** Nothing walks G6's own installation today: `design_governance` and `complexity`'s atlas take file
  maps by design.
  - A small walker feeds G6's own files to both, and the results are kept in the local index with file and line, updated by
    file hash.
  - Code search becomes one more MCP tool.
  - The Understand-Anything graph committed under `web/static/graph` is stale and is not used.
- **Fitness data is thin, and the report says so.** Today about 4 components declare `bad_at`, 2 have parameter contracts and
  5 hold certificates. So most fitness reports will say "unchecked", and filling in each component's limits is part of this
  work.
- **Making code agentic.** One generic higher-order wrapper, ported once from the earlier repository's agentic-runtime pattern,
  with `solver/agentic_runtime.SolverPlanner` (its one ported instance) as the template. The archive copied that pattern into
  166 components.
  - The deterministic function's result is the floor. A model may add to it, never drop from it.
  - Each addition is checked against a frozen standard through `harness.run`, with at most two repairs. Otherwise the
    deterministic result stands, marked degraded.
  - It is off unless the person turns it on for that function.
  - Wrapping a person's own module goes through `component_creator`: a dry run by default, writing only under configured
    roots, from source read with `ast` and never imported. Tool schemas come from each block's input `model_json_schema`, not
    a generic `params`.
  - Generated code runs only under `core/confine`.
  - Classes, async functions and variadic functions, which `component_creator` skips today, are a later increment.
- **When G6 is too restrictive for the use case (owner, 2026-11-08).** G6 never rewrites its own code at run time.
  - When a fitness report finds that G6's own code is too restrictive for what the person needs, G6 warns, naming the limit
    and citing where it is.
  - It then proposes an extension: an additive component registered through the extension framework. It never edits G6's
    core files.
  - The extension is built and checked like any change, with tests first and the adaptation gate. It is applied only after
    the person approves it in the client's dialog, with no terminal fallback and no agent answer.
  - The gate, the judge, the held-out splits, the envelope, the stop and bound files are never edited by G6 (3.7).

### 12.8 What is measured before each piece is promoted (rows for section 5)

| Piece | What is measured | Bar | Cost |
|---|---|---|---|
| Scope | Planted passages in another scope are never returned, on each backend, through fusion and through the graph. A deleted source leaves no passage anywhere | zero, on every path | none |
| Untrusted text | Planted instructions cause no tool action and no policy change | zero | none |
| Retrieval | Recall at k, and citation accuracy (each cited span re-checked by `verify_passage`), per mechanism and fused, on a query set about g6solver written before any tuning | set before it runs | none |
| Freshness | After an edit and a refresh, the old passage is refused; the time from adding a source to it being searchable | set before it runs | none |
| Scale | Index size, memory and query latency at stated corpus sizes, per backend, on this machine and in CI | reported as measured | none |
| Planner | Paired outcomes per mechanism, against fixed fusion, before any learning | set before it runs | none |
| Usefulness for A | On A's frozen cohort, the share of proposed questions that a cited passage settles, judged blind | set before it runs | subscription runs |
| Proposed skill | Held-out tasks of its class, with and without the skill | set before it runs | subscription runs |

### 12.9 Order

Each step is its own change, test first, with a Codex review loop of at most four reviews and three repairs.

1. **The contract.** Passage identity across backends, the `Hit`-to-`ContextItem` adapter, fusion over `Searched` results,
   and the scope model with its leakage and deletion tests, on the local index first. This comes first because scope cannot
   be added later without rereading every path.
2. **The Elasticsearch writer and the onboarding flow.** Adding a source, searching with citations and fetching a passage,
   through the MCP tools and the skill, on both the local index and Elasticsearch. Tests use the fake server, plus one job
   against a live cluster where one is available.
3. **Persistent ColBERT,** after its spike.
4. **The passage-linked graph,** and cited multi-hop retrieval.
5. **Refresh and snapshots.**
6. **The planner.**
7. **Proposed skills; G6's self-index, fitness reports and extensions; then the agentic wrapper.**

This runs beside section 7's steps, not ahead of them:
- Its files are in `knowledge` and `context`, plus new MCP tools, none of which the question slice touches.
- Its usefulness row for A waits for A's frozen cohort.
- Builder slots are shared with the trust loops, at most three at once.

### 12.10 Not adopted, with the reason

- **Elastic's Agent Builder and Elastic's MCP server.** G6 owns its tools (owner, 2026-11-08), and Agent Builder also needs
  an Enterprise licence on a self-managed install.
- **Elasticsearch's `rrf` retriever, ELSER and `semantic_text`, and document-level security.** G6 fuses results and enforces
  scope itself, meaning is ColBERT's job, and whether these are in the free tier is not established.
- **Elastic's content connectors** (Google Drive, SharePoint). They come later, as sources behind the same scope rule.
  Elastic documents that some of them do not carry the source's permissions by default.
- **The earlier repository's raw-Cypher Neo4j adapter, the OWL store's `run_code`, and natural-language-to-query JSON.** All
  three run text G6 has not checked.
- **A formula as the objective for choosing context.** No measured model of usefulness exists. Context is chosen by rules on
  rank, coverage and budget, which 12.8 measures.
- **The earlier repository's vendored code-graph tool.** It is MIT-licensed, but 369 of its files carry a proprietary header
  stamped over the MIT notice, which would have to be restored before any reuse. It also needs a Memgraph server, and this
  design's graph already has a seed.
- **The earlier repository's hot reload, import-hook extension loader, hotfix overlay, patch server and Darwin Gödel Machine
  loop.** They would bypass the gates described in 3.7.
- **Run-time rewriting of G6's own running code.** Ruled out by the owner on 2026-11-08. A limit found at run time becomes a
  warning and an extension the person approves (12.7).

### 12.11 The owner's decisions

1. Approve this section.
2. Whether step 1 of 12.9 starts now, in a free builder slot beside the trust loops.
3. A first private corpus for the usefulness rows besides g6solver, if any. It stays on this machine.

## 13. Proposed 2026-11-08, not approved: both agents know both allowances

**Status.** Version 1, proposed for the owner's approval, and built only once the owner approves it.

**Source.** The owner, 2026-11-08: Claude Code and Codex should each know how much of both weekly allowances is left, so the
agent can scale how many subagents it uses and use the allowance fully without running out early under day-to-day work.

### 13.1 What exists

- **Codex.** `self_maintenance/usage.py` reads Codex's own windows through `codex app-server`'s `account/rateLimits/read`,
  which starts no thread and calls no model.
  - On 2026-11-08 it reported one 10,080-minute window, 24% used, with its reset time.
  - G6 reads no sign-in file.
- **Pacing.** `self_maintenance/pacing.py` already holds the rule the loop paces by:
  - a target of d/7 of the allowance by day d;
  - a ceiling of d/5;
  - a reserve released in the last 24 hours.
- **Claude Code.** It has no usage command: `claude --help` was checked on 2.1.319. Its status-line input does carry
  `rate_limits`, with a five-hour and a seven-day window, each with `used_percentage` and `resets_at`.
  - That field has been in the input since 2.1.80, according to Claude Code's changelog as quoted in issues on its
    repository.
  - Some accounts report it missing.
  - It can be absent until the first reply of a session.
  - The person's current status-line command does not read it.

### 13.2 The design

- **Reading Claude.** A G6 status-line command reads Claude Code's status-line input, saves only the `rate_limits` block and
  the time it was read under G6's home, and passes the same input on to the person's existing status-line command, printing
  that command's output unchanged.
  - It adds no model call and stores no account identifier.
  - Installing it is a one-line change to the person's Claude Code settings, which the person makes. G6 gives the exact line,
    and never edits the person's settings itself.
- **Reading Codex** stays `codex_limits()`.
- **One reading per provider.** Each reading holds:
  - the used share for each window;
  - the window's length and reset time;
  - when it was read, and from where.

  A reading older than a stated age is shown as stale, with its age. A missing reading is unknown, never zero.
- **Pace.** `pacing.target` and `pacing.ceiling` are applied to the share of each window that has passed, using the
  provider's own window: weekly for both, and the five-hour window for Claude as well. Each provider is then behind pace, on
  pace, ahead of pace or unknown.
- **Advice, by a fixed and stated rule.**
  - **Behind pace,** so allowance would go unused: more subagents in parallel, up to the cap the person set.
  - **On pace:** as usual.
  - **Ahead of pace:** conserve. New subagents go to the other provider if it has room, and fewer run in parallel.
  - **Near the ceiling, or a short window nearly full:** start no new subagents on that provider.

  The thresholds sit in G6's settings, with their defaults stated. The advice never blocks anything, and the person's own
  caps stay in force.
- **Delivery.**
  - An MCP tool returns both readings and the advice. Its name is fixed from the canonical vocabulary (`00_INDEX.md`).
  - For Claude Code, the plugin's hook adds a one-line note to the context only when the advice changes, not on every turn.
  - For Codex, the same tool, with a line in the skill and the `AGENTS.md` guidance to read it before fanning out.
  - The GUI and the TUI/CLI stay frozen.
- **Honest limits.**
  - A Claude reading is only as fresh as the last interactive session's status line. A background session sees the last
    reading, with its age.
  - Readings are the providers' own figures. G6 adds no estimate it cannot state.

### 13.3 What is measured

- The rule is tested on recorded readings: each verdict and its advice, at the boundaries of each threshold.
- Afterwards, each week's use is reported against pace, as observed: whether either allowance ran out early, and how much went
  unused.

### 13.4 Order and the owner's decisions

It is one small change, test first, with its own Codex review loop. It touches none of the question slice's files, so it can
go first.

The owner decides:
1. whether to approve this section;
2. whether to install the status-line wrapper on this machine, which is one line in the Claude Code settings;
3. the thresholds, or the stated defaults.

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

## Appendix B: Codex's challenge of section 11 version 1, and what changed

Codex read section 11 at 8e72041 against the code, with one offline probe on synthetic objects. Every challenge was read
against the code. The issuer probe was rerun on slice 3's tree with the same result. One "not found" is an artefact of the
checkout: `harness/confirmed_expectation.py` and its supersession exist on `claude/evidence-semantics`, which the integration
line Codex read does not yet carry. All twelve are adopted.

| # | Challenge | What changed |
|---|---|---|
| 1 | The issuer's failure branch turns a supplied failure with no ledger record into a refutation | new item 1: custody for both polarities, at the adapter; the issuer goes on the issuance list |
| 2 | A card built from gold, base and empty controls looks excellent while missing near misses | item 4: a control diagnostic first; a candidate card only on labelled runnable patches, with near misses apart and full denominators |
| 3 | The person can confirm the right criterion while G6 compiles the wrong observation | new item 2: correspondence from scenario to setup, entry point, observation and assertion; unsupported cases stay unenforced and shown |
| 4 | No producer for a control showing the rejected outcome; a missing control is not vacuity | item 3: an adapter-level witness from the base, a candidate or a bounded mutant; "negative control unavailable" otherwise |
| 5 | `lessons.calibrate` bounds majority agreement by bug, not either rate; `CodingReplay` calls a model | item 4 reuses `core.binomial` and a small adapter; `CodingReplay` stays for model verdicts |
| 6 | Supersession was claimed as existing on a tree that lacks it; revisions alone cannot measure drift | item 7: the store is slice 2's, wired by slice 3; correspondence measured at each step; human drift only in a paired evaluation |
| 7 | Recomputing a held-out card spends the cohort | item 4's cohort discipline |
| 8 | The textual `VerificationContract` adds paperwork; "the next review covers the lines" overpromises | item 5 uses executed checks and promises only that the diff was supplied |
| 9 | Applying a repair to a working tree that changed since it was tested | item 5: pre-repair capture, apply only on a match, preserve concurrent work, rerun, recover; 11.4's rationale corrected |
| 10 | `lessons.Prediction` cannot hold an experiment's expected outcomes; chosen-action logs do not train a router | item 6: an event record with qualitative expectations; learning still needs paired outcomes |
| 11 | Several "Today" cells were wrong or too broad, and two substitutions were not equivalences | 11.3 rewritten row by row; "retire" means from the integration plan, never removing a public API |
| 12 | The study paragraph overstated what 53.6% and the repair-line count show | 11.1 states the denominators and what they do not show |
