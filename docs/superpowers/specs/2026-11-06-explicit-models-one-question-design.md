# G6 0.3: explicit models, one question, measured — design

Status: frozen by the owner on 2026-11-08, and 0.3.0 is all of it built. Sections 1 to 10 are version 2, approved on
2026-11-06: version 1 (178ef54) was challenged by Codex, all 16 of its challenges were read against the code and adopted, and
appendix A lists each. Sections 11 to 15 were added on 2026-11-07 and 2026-11-08 and approved with the freeze; section 16 is the owner's ruling
that no data reaches a cloud model without consent, which every section meets. Codex's
challenges of them are folded in as corrections within this scope (appendix B for section 11). Anything new goes to a list for
after 0.3.0. Building follows section 7's order, with 12.9, 13.4, 14.4 and 15.8.

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

## 11. Approved with the freeze, 2026-11-08: the rigour architecture as a test of what works in practice

**Status.** Version 2, proposed on 2026-11-07 and approved with the freeze on 2026-11-08. Version 1 (8e72041) was challenged
by Codex; all twelve challenges were read against the code and adopted, and appendix B lists each. It changes no step's order
in section 7.

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

## 12. Frozen 2026-11-08: the Context Engine, a person's own knowledge searched three ways

**Status.** Part of the design the owner froze on 2026-11-08. All the capabilities below stay in 0.3.0. The challenge
changes their contracts and their order within this section, not section 7's order. A failed gate requires a repair and a
new measurement; it does not remove a layer or make the release complete. Appendix C records all twenty challenges.

**Source.** The owner's direction of 2026-11-08:
- `ctx.elastic` for onboarding and specialisation;
- G6's own skill and MCP tools, not Elastic's Agent Builder or MCP server;
- G6's own fusion, without Elasticsearch's built-in hybrid retriever;
- persistent semantic search through `ctx.colbert`, and a passage-linked graph as the third mechanism;
- an agentic search planner, G6 indexing G6, fitness reports, approved extensions and a generic agentic wrapper.

The owner authorised porting the earlier repository's components under G6's licence. External claims about Elastic's
licence tiers, connectors and supported features were not checked in this challenge. Check the supported versions before
implementation. G6's choice to own its tools, fusion and scope does not depend on those claims.

### 12.1 Why it belongs in this design

- **A fourth source.** A person's documents may supply information the agent lacks: decisions, conventions, incidents and
  specifications. Each source records its origin, version and whether the agent could already read it. A repository file
  already supplied to the agent is not independent information.
- **It serves A.** A passage can accompany a candidate as evidence that it is already answered. Suppression requires an
  existing normative-source grant for that repository, source version and scenario, with no unresolved conflict. Otherwise
  the candidate stays proposed. Relevance, byte correspondence and source authority are separate records.
- **One intent authority.** The grant states its scope, exceptions and supersession rule. Its interpretation must map to
  the accepted predicate and compiled observation in 3.2. A match never becomes an issuer support receipt. A substantive
  decision to designate a source during a task counts against A's one-question budget; an access dialog cannot hide it.
- **Specialisation.** An approved source goes through an indexing job. Its readiness result says which content and backends
  are usable. It can then supply citations and a proposed skill. Installation and expertise are separate outcomes (12.5).

### 12.2 What exists (surveyed 2026-11-08, read from the code)

Current behaviour is narrower than the system to be built. A reuse decision does not claim that the new contracts
already exist.

| Need | Current G6 | Earlier repository | Decision and new work |
|---|---|---|---|
| Identity | `src/g6/knowledge/_types.py`: content-bound `Source`, `Document`, `Passage`; computed untrusted trust | — | retain canonical passages; add registration, grants and generation records separately |
| Index contract | `src/g6/knowledge/_index.py`: `Index`, `Hit`, `Searched`, backend flags and registry; no access context or typed paths | — | add the scoped envelope in 12.3; rank combination is a rule here, not an implementation |
| Conversion and checking | converters for text/Markdown, HTML and optional PDF; chunker checks offsets; `knowledge_manifest.verify_passage` compares supplied text and spans | — | reuse these primitives; build walking, rereads, lifecycle and snapshots; a comparison alone fetches or invalidates nothing |
| Local full-text | `knowledge_indexer.SqliteIndex`: FTS5/BM25, incremental passage IDs, budget and stored-text consistency checks; no scope schema | — | default lexical backend, subject to FTS5 readiness; add scoped stores and source membership; hashes do not resist a same-user rewrite |
| Elasticsearch | `src/g6/knowledge/ctx_elastic/`: read-only, mapping-checked fields and scalar filters; local HTTP, key file, fake-server tests; hits rebuild a cluster-document identity | writer and 25-tool server; single writes accept IDs, bulk omits them; provenance is not required or validated; JSON parsing lacks a typed capability policy | build a managed reader round trip and writer; retain generic cluster search separately; do not port its server or arbitrary queries/scripts |
| Meaning | `src/g6/knowledge/ctx_colbert/`: ColBERT, sentence embeddings, BM25; in-memory object has no 2,000 cap; the block caps each call at 2,000 passages and 5,000,000 characters; model objects are cached, passage encodings are not | RAGatouille rebuilds on corpus change; the old PLAID wrapper disables Windows and overwrites on ingest | build persistent storage and bounded candidate search; no claim about current upstream Windows support before the spike |
| Relations | `src/g6/memory/ctx_cognee/`: model links and shortest chains without passage IDs; code graph and conservative `complexity.impact_cone` | `concept_edges` stores supplied assertions; no traversal consumer found | use the table as a schema sketch; build integrity, provenance and traversal; Cognee stays optional |
| Fusion | `src/g6/context/fuse.py`: RRF by source/text, list position, heuristic near-duplicate merging; loses later metadata and can merge allow/deny | Python RRF and a single-choice keyword router | implement passage-ID fusion from its rank/budget ideas; preserve contributions and disagreements |
| Untrusted text | trust labels, instruction signals and a scrubber; packet metadata is not wholly scrubbed; `stale_after` is not a clock-based refusal | — | add redacted views, host action controls, expiry and checks at actual consumers |
| Gates and consent | `src/g6/sources/_gate.py` returns a decision; callers must honour it; MCP permissions are operation allow-lists; `src/g6/mcp/consent.py` supplies explicit client elicitation | CTX-009 records a failed-open ACL; its implementation was not re-established here | add host-bound grants and explicit dialog call sites; a configured key is not deletion consent |
| Skills | `ctx_ace.Store` records proposal/approval claims; code scanner has a narrow example vocabulary; component conformance checks a package | skill emitter has folder references and cached-source machinery, not the required passage contract | use notes only for proposals; build standalone skill linting, trials, installation and admission |

### 12.3 Three ways of searching, one envelope

**New common envelope.** Keep canonical `Passage` unchanged. Add a versioned request and response with a host-bound grant,
operation, typed filters, source registrations, generation and execution receipts. Payloads are passage search, graph path
or structured answer. Counts and aggregations are answers, not invented passages. Paths keep ordered, directed edges,
endpoints, witness references and proposed/asserted status. Preserve each backend's answered, empty, unavailable, failed
and partial state through fusion. An unavailable backend says nothing about absence of evidence.

| Mechanism | Supported operations and boundaries |
|---|---|
| Exact: `SqliteIndex` | lexical passage search only in its first scoped schema; structured requests require Elasticsearch or return unsupported |
| Exact and structure: `ctx_elastic` | managed passage search, mapping-checked fields, scalar filters and counts; typed aggregations are new work; no raw query/script input |
| Meaning: persistent `ctx_colbert` | scoped semantic passage search, incremental add/remove and bounded candidate scoring; report the actual rung and its availability |
| Passage-linked graph | typed containment, static imports and relation-specific paths, with witnesses; model relations are discovery leads |

**Managed Elasticsearch.** The writer manages a dedicated, versioned G6 index with a fixed mapping, canonical passage
record and source-registration/scope key. Setting `_id` is only one part of this new protocol.
- Decode and validate that record on reads, retaining the original URI, hashes and offsets. Generic cluster-document
  search keeps its separate identity and mode. Test canonical round trips and fusion provenance before the writer.
- Set `_id` to the canonical passage ID. Offer typed create, upsert and removal by recorded membership. Pin and check a
  concrete target, including alias resolution; callers cannot choose a deletion target or perform cluster administration.
- Use bounded batches and per-item acknowledgements. An HTTP success does not stand for every bulk item succeeding.
  Unknown outcomes are retried idempotently against the same record and target. Tombstoned generations cannot return.
- Publish a generation only after visibility and read-back checks. Changed sources get new IDs; membership removes the old
  ones. Crash recovery follows 12.6, rather than delete-old/add-new with no commit point.
- Honour `src/g6/sources/_gate.py` and `action_gating` at each write call site. Deletion has its own scope and dialog consent;
  credentials or API-spend approval do not authorise it. Check that removed IDs cannot be read through authorised routes.
- The reused `src/g6/core/outside.py` supports methods and byte bodies, but only loopback plain HTTP, without redirects and with
  deadline/reply limits. That is the supported first transport. HTTPS and remote/cloud transports require separate work
  through the bound-core process; no tunnel may silently change a source's destination grant.
- Build a new scoped onboarding dispatcher. Do not relax the local indexer's refusal of network/nonpersistent backends.
  Fake-server checks and a supported real-cluster job are separate requirements. G6 does not bundle Elasticsearch.

**Persistent `ctx_colbert`.** Store token vectors by passage ID, with incremental membership, deletion, restart recovery,
batched encoding, explicit device/memory limits and a bounded candidate stage. Persistence alone does not bound a scan.
- Freeze the spike's corpus sizes, CPU/RAM/disk envelope, cold/warm latency percentiles, recall baseline, ingest/update/delete
  limits and restart/crash cases before comparing PyLate indexes and PLAID builds. State native Windows, WSL and Linux
  support separately. Include FTS with cached sentence embeddings and lexical-candidate ColBERT reranking as baselines.
- Pin model/revision, tokenizer, dimension, truncation, precision, chunker and index schema. Keep local-only safetensors
  loading without downloads. The current file-size/incomplete-transfer heuristic is not a complete snapshot check; add
  model/tokenizer/config readiness checks. A failed load or unsupported platform is unavailable, with its reason.
- A spike can conclude that no candidate meets the envelope. Then repair or choose a bounded persistent ColBERT route and
  rerun the gate. Persistence remains required for 0.3.0; neither in-memory fallback nor a failed spike completes it.
- A BM25 fallback is disclosed and counted as lexical once, not as independent semantic evidence. State scale only from
  measured supported configurations.

**Passage-linked graph.** Nodes are scoped entities and documents. Start with containment and explicit static imports,
with file/line witnesses. Imports and `impact_cone` are possible dependencies, with uncertainty, rather than behavioural
or causal conclusions. Every edge references a canonical passage ID and witness offsets.
- Add model relations after those results are measured. Each edge records subject/object mentions, relation, polarity,
  direction, scope, source version, witness span, extractor/model version and status. Enforce referential integrity and
  revocation. Ambiguous entities stay unresolved across projects and versions.
- `ctx_langextract` can check that a value occurs at an offset; it does not establish a relation. Define direction and
  composition rules by relation. A requested contradiction stays a hypothesis without a checked relation.
- Proposed edges may lead search to original passages. Evidence views return their claims separately; the path does not
  inherit their authority. Test laundering through fusion, context packs and skill drafting, as well as graph responses.

**G6's fusion is new work.** Implement rank fusion by canonical passage ID using `src/g6/context/fuse.py`'s rank and budget
ideas, not its source/text identity or word-overlap merge. Combine ranks, never raw scores. Keep immutable contributions
before deduplication: `(backend, query, rank, score scale, reason, passage ID, generation)`.
Honour declared ranks. Deterministic ties use passage ID.
- Different canonical passages stay distinct evidence, including near-duplicates that disagree. A presentation group may
  show both, with separate citations. Retain number/negation cautions, but do not treat them as semantic agreement.
- Preserve path and structured execution records. Keep the finite 2,000-candidate bound, forced untrusted output and explicit
  budget refusal. Count all rendered fields, provenance and path overhead in the model token budget.
- Model-visible metadata contains only the authorised redacted view, never raw records as a shortcut to preserving identity.
  View-to-canonical-span mappings keep redaction or compression distinct from original passage bytes.

### 12.4 The search planner: choosing among them

Explicit backend choice and a small fixed-fusion baseline come first. The planner remains in 0.3.0, built after paired
queries show what that baseline costs and misses. Its gate requires a named outcome improved or lower cost at equal quality
and budget. Learning waits for independently judged paired outcomes, costs and utility (3.5).

- **Fixed priorities.** Exact identifiers, paths, errors and API names go to exact search first. Conceptual queries try the
  available meaning rung. Dependency/relation queries begin with witnessed code or containment, then eligible graph edges.
  Overlapping needs can use several routes; priority order and route name break ties deterministically.
- **Availability.** Refused/unsupported routes stay refused; unavailable/failed routes retain that state. Fallback uses only
  operations authorised by the host, and records the actual rung. Do not count BM25 twice. Unknown applicability stays unknown.
- **Coverage.** Declare evidence needs before the search. Track found, conflicting and missing witnesses against those needs.
  No new passage stops that branch, not a declaration of complete coverage. Budget exhaustion retains unknown coverage.
- **Reading and reformulation.** A model can propose typed searches or broader terms within the same grant. No raw cluster
  syntax or model-written query JSON is admitted. Every internal read has the scope, disclosure and revocation checks in
  12.6. Set total search, model-call, token, elapsed-time and candidate limits before the run.
- **Decision event.** Section 11 is included in the freeze, but its event is implementation work. Record locally: evidence
  needs, known/unknown features, available operations, grants/generation, admissible routes, priority/tie rule, chosen route,
  alternatives and qualitative expected outcomes, limits, actual rung, contributions, costs and stop reason. Each feature
  must change an admissibility or priority rule. Chosen-action logs alone cannot train the router.

### 12.5 Onboarding a person's knowledge, and proposed skills

1. **Preview and consent.** New MCP tools call the client's dialog explicitly, using `src/g6/mcp/consent.py` as transport. Host
   permission and fresh operation consent are separate. Preview canonical roots, exact source list/digests, scope,
   disclosure destinations, selected backends and effects. Bind acceptance to that preview and recheck it at commit.
   - The supplied name is an attribution, not authenticated identity. No agent-written name or `ctx_ace.approve` can replace
     a client response. Unavailable, declined, cancelled or invalid forms make no change, with no terminal fallback.
   - A folder/repository walk excludes credentials and ignored files, refuses escapes through symlinks, and checks roots
     again at use. Changed membership requires a new preview. URL fetching needs a destination grant and bounded fetching.
2. **Indexing job.** Convert supported files and register source-to-passage membership. Index only into selected, configured,
   ready backends. Return unread files, unsupported formats, exclusions, failures and each backend's generation/readiness.
   Installation alone means neither configured nor searchable. Existing caller-supplied provenance stays caller-supplied.
3. **Refresh and snapshot.** Reread authorised sources by commit, file hash or fetch, then compare passages. Use 12.6's
   tombstones and publication protocol. Record the exact rendered excerpts, bytes or immutable accessible versions, IDs,
   hashes, grants, generation and converter/chunker/model versions. Hashes alone cannot reconstruct a decision.
4. **Propose a skill.** Draft a standalone SKILL.md with sources, resolvable passage citations, declared tools, scope,
   search policy, checks and budget. Each factual line cites a passage. Check support separately from byte correspondence.
   - Build a standalone schema/linter for these fields. Reuse applicable component helpers, not package conformance as a
     standalone skill test. `skill_text_problems` checks a narrow code-example vocabulary, not prose safety; unsupported
     imports, languages or examples require review rather than a conclusion that the skill is safe.
   - Lifecycle is digest-bound: draft, authorised trial, measured/admitted, disabled/revoked. Keep corpus, check, model and
     skill versions with it. `ctx_ace` may hold the proposal; it is neither the consent authority nor the installer.
   - The dialog previews exact skill bytes, destination and effects. A person may authorise an unmeasured trial, visibly
     labelled and limited to opted-in tasks, with no added tool authority or default selection. Admission needs 12.8's
     held-out comparison with the same retrieved information, tools and budget in both arms.
   - Failed held-out tasks reject admission and disable the trial. Source/skill changes invalidate dependent admission;
     remeasurement and fresh consent bind the new version. Installation supports rollback and withdrawal.
5. **G6's MCP tools and skill.** Build add/list sources, scoped fused search, passage fetch, refresh/revoke, budgeted context
   assembly, skill proposal/trial and approved activation. Update `using-g6` with when to use them. The referenced
   `00_INDEX.md` was not found in this checkout; locate the approved vocabulary or obtain a concrete approved mapping before
   fixing names. Do not invent a second naming authority. REST, CLI and GUI stay frozen.

### 12.6 Rules that hold on every path

**Host-bound scope.** The host binds a session to a principal, project and permitted roots. Absent or unknown bindings deny
access. The caller can narrow a grant only; a project argument is not authority. Start with separate project stores for the
single-developer release. Add source registrations, grant revisions and revocation epochs as new records.
- Registration, search, read-by-ID, generic cluster fetch, manifest fetch, counts/aggregations, graph traversal and every
  intermediate node/edge use the grant. So do fusion provenance, planners, skill drafters, packs and subagent packs.
- Recheck revocation and expiry immediately before every model/tool use or return, including G6's internal model reads.
  Cache keys bind the query, principal, project, authorisation revision, disclosure destination and index generation.
  Saved packets cannot bypass the check. Close legacy generic-operation bypasses before exposing these tools.
- This is a host/API boundary. Direct library use and same-user filesystem access outside the host are not confined by a
  query filter. Do not describe separate stores or hashes as protection from a same-user attacker.

**Disclosure is separate from local storage.** Indexes remain local by default. Excerpts sent to a coding agent's hosted
model leave the machine. Internal planning, skill drafting, graph extraction and subagent context are disclosures too.
- Obtain an explicit per-source/session disclosure grant naming provider and destination. Refuse local-only sources on
  hosted routes. A tunnel or optional outside service needs the same check. Missing grants deny disclosure.
- All model-bound strings, including titles, reasons, edge labels and metadata, use a defined redacted view. Preserve its
  mapping to canonical spans. Raw bytes stay behind authorised fetch. Secret masking is not a confidentiality guarantee.
- Retrieved text stays untrusted data and passes instruction signals. Only host policy and operation consent can authorise
  actions; retrieved text supplies neither. Measure actual downstream actions with planted instructions across those fields.
  Labels and delimiters alone do not establish resistance. Use synthetic corpora; private content never enters public records.

**Lifecycle and freshness are new work, from the first slice.**
- Assign a stable source-registration ID and source-to-passage membership. Revocation first raises a deny epoch and writes a
  tombstone; in-flight results, cache hits and retries must honour it before physical deletion completes.
- Stage idempotent backend jobs with per-item receipts. Publish a new usable generation atomically only when its required
  backends pass read-back checks. Optional failures remain visible as partial readiness, not fabricated empty results.
  Readers pin the published generation. A crash/retry cannot expose a half-published or revoked source version.
- Check expiry against the clock at every authorised use. Existing `ContextItem.stale_after` and assembly do not supply this
  refusal. A trusted reread receipt records the source version and time; caller-supplied freshness is not enough. Observed
  change denies old passages until replacement publication. Failed refresh reports unavailable content and downtime;
  it does not serve expired or known-changed passages. Removed sources are tombstoned immediately.
- Build reverse dependency records for caches, packs, graph edges, skill trials/admission and other stored consumers. Each
  consumer must honour invalidation before use. Do not claim general invalidation until those records and checks exist.
- Distinguish removal from active retrieval, private audit retention and prior disclosure. The dialog states retention and
  deletion choices. Retained audit bytes are separately access-controlled, not searchable live content. Complete deletion
  removes those bytes too, leaving a receipt that says reconstruction is unavailable. Already delivered client/model text
  cannot be recalled. Retain only authorised tombstone identifiers needed to prevent resurrection.
- Reconstruction needs retained bytes or an immutable accessible version and the exact rendered excerpts and tool versions.
  The issuer's byte binding is a useful rule, not an existing context snapshot store.

### 12.7 G6 indexing G6

**Search and understand.** G6's repository, g6solver in section 8, is the first corpus. Index code with file/line provenance,
catalogue, skills, failure map and specifications. Source access is pinned to the task/replay baseline. Gold patches,
held-out diagnostics and later study answers stay outside the agent's available corpus. Public records may be indexed only
in a separate permitted snapshot that cannot contaminate that replay. Answers are independently labelled for a frozen query
set; the self-index is not an oracle.
- `design_governance` and `complexity` take supplied file maps. Build and version a source walker and synchroniser feeding
  both, with file hashes, parse failures and dynamic-import unknowns. Exclude the stale graph under `web/static/graph`.
- Compare native `rg`, discovery and existing atlas APIs first. A code-search MCP tool remains in scope, built after that
  measured baseline to expose scoped cited code and structure through the same envelope, rather than adding another engine.

**Fitness reports begin as capability/claim inventories.** For each candidate, show availability, exact operation, claim
source, declared guards/introduced failures, limits, applicable certificate rules and freshness, untested preconditions and
one executable use-case check. Certificates cover their named rules/inputs, not general fitness. Registry description may
import Python; inventory untrusted packages by static metadata or isolated inspection, not parent-process discovery.
- Today four components explicitly declare `bad_at`; two parameter-contract JSONs cover two operations of workforce, not two
  components; five certificate files exist. Report gaps. Filling limits is an attributed review task, not model-made fitness.
- A restriction needs a minimal reproduction and checkable desired behaviour before it becomes an extension proposal.

**The generic agentic wrapper stays in 0.3.0, after concrete contracts.** `SolverPlanner` in
`src/g6/solver/agentic_runtime.py` offers narrow planning choices and fallbacks, not a generic preserved deterministic floor.
The archive's 166 runtime files do not supply one shared contract. Build the common wrapper after two concrete
implementations establish reusable semantics.
- Begin with an explicit pure-function slice: typed inputs/outputs, independently accepted frozen standard, known-good and
  known-bad controls, paired deterministic baseline and a defined preservation/merge relation. Planning, reranking and value
  generation have separate contracts. A model's self-written check is not an independent standard.
- Generalise those contracts into one opt-in higher-order wrapper. Preserve the deterministic result according to the
  declared relation. Admit additions only through the frozen checks in `harness.run`, with at most two repairs; otherwise
  return the deterministic result, marked degraded. The harness cannot establish that its supplied standard captures intent.
- Boolean/permission decisions, optima, ordered lists, exceptions and side effects have no generic meaning of “add”. Require
  an explicit contract or refuse the wrapper for that function. Side effects need an isolated plan/commit boundary; retaining
  a return value cannot undo them. The person enables a particular function and contract, not a blanket code-wide mode.
- `component_creator` supplies dry-run scaffolding under configured roots. AST inspection avoids imports; generated handlers
  later import and call the target. Build per-block `model_json_schema` tool schemas; the current generic MCP `params_json`
  surface does not already provide them. Classes, async, variadic and positional-only functions need explicit adapters and
  preservation contracts in the later wrapper step, not silent wrapping by the current function loop.
- Execute generated material and its target under strict OS isolation (`G6_REQUIRE_ISOLATION` or host-enforced equivalent),
  including imports. Refuse unsupported hosts; the optional unconfined fallback is not acceptable for this flow.

**Extensions the person approves.** Warn with a cited, reproduced limit. Propose an additive package only where an explicit
extension seam supports the use case. G6 does not rewrite its own running code.
- Entry-point discovery and no-shadowing exist, but imports run in-process. Build a digest-bound installer and an
  out-of-process, capability-limited extension path. Discover untrusted metadata without importing it in the parent.
- Specify granted roots/network, isolated imports, restart behaviour, rollback and revocation. Produce a reviewable patch
  or package with tests. The adaptation gate admits shadow evaluation only. Dialog consent binds the exact shadow
  activation; production integration or merge remains the person's action under 3.7.
- Bound inputs include `src/g6/core/registry.py`, `src/g6/core/outside.py`, `src/g6/core/confine.py`,
  `src/g6/core/extension_points.py`, `src/g6/core/door.py`, `src/g6/core/llm.py` and `src/g6/core/host_models.py`.
  Bayesian also binds `src/g6/evidence/certificate.py`. An additive wrapper may not overcome an internal limit.
  If a seam is insufficient, report that and follow section 9 and the issuance process. Do not bypass a bound/core change.
  The gate, judge, held-out splits, envelope, stop and bound files stay outside G6's mutation authority.

### 12.8 What is measured before each piece is promoted (rows for section 5)

Each row needs a frozen protocol before its implementation step: unit/denominator, independently labelled cases, baseline,
corpus/model/tool versions, supported environment, limits, missing/failure handling, costs and numerical admission thresholds.
An unset threshold blocks that step. Tuning retires the cohort; a new held-out cohort admits the next version (3.6).
Count developer/reviewer time, CPU/GPU/RAM/disk, model calls/tokens and subscription capacity, even for local retrieval.
Safety failures block admission; partial/missing runs stay in the denominator and are reported separately.

| Piece | Protocol and admission rule |
|---|---|
| Scope, disclosure and deletion | enumerate interfaces, principals, grants/revokes and crash/restart races; zero forbidden reads/disclosures or tombstone resurrection on that suite; report retained history and prior delivery separately |
| Untrusted text | frozen text/title/edge/metadata attacks at real planner, drafter and agent consumers; zero unauthorised actions/policy changes on the declared suite; count provider/test costs, without a universal immunity claim |
| Retrieval and fusion | independent eligible-passage and normative-support labels; recall@k, unsupported citations, availability and partial failures against direct file search, per mechanism and fused; byte-match accuracy reported separately; no loss of canonical provenance or conflicts |
| Freshness | source versions, clock, expiry events and refresh commit point fixed; zero uses of denied/expired versions after that point; measure readiness delay, failed refresh and downtime against frozen limits |
| Scale and persistent meaning | frozen corpus sizes, platform/device and cold/warm latency percentiles, peak RAM/disk, update/delete/recovery limits; all supported-envelope limits must pass; outside-envelope results are diagnostics |
| Graph | independently labelled direction/relation witnesses and multi-hop queries; no proposed lead admitted as evidence; compare containment/import baseline before model edges; usefulness and resource limits fixed in the protocol |
| Planner | paired exact, dense, graph, fixed fusion and planner queries at equal information/budget; improve a named outcome or lower cost at equal quality, with no safety regression; learning needs separate paired judged trials |
| Usefulness for A | one cited candidate first, then A's frozen base-only cohort; blind labels for false suppression, missed ambiguity, wrong patches, unnecessary questions and person time; zero false suppressions or induced wrong patches on the suite, with saved time/questions measured against direct search |
| Proposed skill | independently labelled held-out tasks, same information/tools/budget in both arms; admit only a named improvement with no added harmful outcome; failed trials disabled, changed versions invalidated |
| Self-index, fitness and extensions | baseline-pinned labelled queries, claim versus executable use-case results, reproduced limits; extension checks use shadow only, exact package/grants and rollback cases; missing checks stay unchecked |
| Generic wrapper | two concrete typed contracts, paired deterministic controls and accepted preservation relation per contract; zero preservation/isolation failures on the suite and a named usefulness gain before generalisation |

### 12.9 Order

Each step is its own change, tests first, with a Codex review loop of at most four reviews and three repairs. Specify the
principal/grant, disclosure, revocation and consumer paths before writing leakage tests. Those are the first implementation
tests. Later steps are gated on the earlier measured result and remain required for 0.3.0; failed gates require repair.

1. **One safe local slice.** Contracts, separate project stores, source registration, dialog consent, redacted views,
   tombstones/generations, expiry, refresh and snapshots first. One source → local lexical search → cited passage → one A
   candidate. Test scope, disclosure, revocation, canonical identity and false suppression. Compare with direct file search
   before funding later layers. Wider A usefulness waits for A's frozen cohort.
2. **Onboarding, managed Elasticsearch and fusion.** After the local result passes, add typed envelopes, canonical reader
   round trips and contribution-preserving fusion before the writer. Add the scoped dispatcher, jobs and MCP/skill flow.
   Run fake-server checks and the separate supported real-cluster job; unsupported operations remain explicit.
3. **Persistent `ctx_colbert`.** After step 2's retrieval/cost baseline, freeze and run the library/platform spike, then build
   persistence, incremental correctness and bounded search. Gate graph work on its measured route/fusion result.
4. **Passage-linked graph.** Measure containment/static imports first, then add typed model relations and cited multi-hop
   retrieval. Keep lead/evidence separation through consumers. All lifecycle rules already apply.
5. **Planner.** Use the earlier per-route and fixed-fusion measurements to build the staged planner and its paired gate.
6. **Skills and G6 self-index.** Build trials/admission and the versioned walker; measure against equal-information and
   native-tool baselines. Fitness inventories progress to use-case checks before proposing reproduced extensions.
7. **Approved extensions and generic wrapper.** Build the isolated installer/shadow path, then concrete wrapper contracts;
   generalise only after two measured implementations. Production merge remains the person's action.

These steps share developer, reviewer and subscription capacity with section 7; they are not a reason to postpone its
question slice. Builder slots remain shared, at most three at once. The touched paths also include MCP consent, memory
proposal records, skills, hooks, solver and component generation where needed. They are not confined to `knowledge` and
`context`. Changes to bound core follow issuance rather than being described as harmless plumbing.

### 12.10 Not adopted, with the reason

- **Elastic's Agent Builder/MCP server and built-in hybrid retriever.** G6 owns its tools and fusion, by the owner's decision.
  Licence-tier claims remain unchecked here. Use no ELSER, `semantic_text` or document-level security dependency; ColBERT
  handles meaning and the host handles scope. Check required features on supported versions before implementation.
- **Outside connectors.** Later source adapters use the same grants and disclosure rules. Do not assume permission inheritance;
  check each supported connector. No vendor permission claim is established by this offline challenge.
- **Raw Cypher, OWL `run_code` and model-written cluster queries.** They bypass the typed capability policy. Parsing JSON is
  not an authorisation or semantic check.
- **A formula for context usefulness.** No measured model exists; rules on rank, stated evidence needs and budget are measured.
- **The earlier vendored code-graph tool.** The inspected copy has an MIT licence and 369 files with conflicting proprietary
  headers; reuse needs licence review and restoration of notices. It also needs Memgraph. This design builds its own graph.
  A broader claim about a vendored Elasticsearch distribution was not established and is not a dependency of this decision.
- **Runtime rewriting, import-hook hot reload, hotfix/patch-server or Darwin Gödel Machine paths.** They are not the approved
  extension/issuance path. Historical claims beyond the inspected loader/reload functions remain unchecked.
- **Removing the planner, model graph, skills, extensions or generic wrapper from this increment.** Declined as removal because
  the owner froze them into 0.3.0. Their safety corrections are adopted; their deferral becomes measured build order (12.9).

### 12.11 The owner's decisions

1. Approved with the freeze (2026-11-08), including all layers above.
2. Step 1 starts once the frozen design is committed, in a free builder slot, without changing section 7's priorities.
3. Any additional private usefulness corpus needs the stated local-storage, retention and per-source disclosure choices.
   A local-only choice excludes hosted consumers; it does not authorise sending passages to a coding agent's model.

## 13. Frozen 2026-11-08: both agents know both allowances

**Status.** Part of the design the owner froze on 2026-11-08. The display, allowance advice and agent guidance stay in 0.3.0.

**Source.** The owner wants Claude Code and Codex to know both allowances, so useful subagent work can fit day-to-day use.
That is an objective to measure. A display cannot ensure full use, efficient fan-out or avoidance of early exhaustion.

### 13.1 What exists

- **Codex.** `src/g6/self_maintenance/usage.py` has `codex_limits()`, which starts `codex app-server` and reads
  `account/rateLimits/read`. It starts no conversation or model request; an I/O thread does exist. G6 does not read a sign-in
  file. The app-server protocol may itself use provider I/O. The dated one-window/24% observation was not repeated in this
  challenge and is not a supported-window specification.
- **Existing pacing is weekly.** `src/g6/self_maintenance/pacing.py` uses day-based target d/7, ceiling d/5 and reserve
  release at day 7. Its `AccountReading` has one usage/reset pair. `usage.limits_reading` selects weekly usage and only
  treats a full short window as a limit. These cannot supply lossless multi-window advice or duration-aware pacing as is.
- **Claude Code.** Status-line `rate_limits`, five-hour/seven-day shapes, version availability, missing fields and the lack
  of a usage command are external assumptions unchecked in this challenge. Capture a supported payload and check the
  supported version before implementing its adapter. The person's existing renderer is an input to installation preview,
  not an assumption about its current behaviour. No G6 status-line observer exists yet.

### 13.2 The design

**A lossless normalised reading.** Reuse the raw Codex reader, not its weekly meter adapter or ledger types. Build a new
validated provider-neutral window record for every reported window from both providers:
- local provider/profile binding and window identity;
- duration, used share, reset and declared fixed/rolling/replenishing semantics;
- actual observation time, schema/source version, availability/staleness and quota-block state;
- per-window failures and provider-level ordinary-usage permission, including `ordinaryUsageAllowed=false`.

Missing is unknown, never zero. Unknown duration/reset/semantics means unknown pace. A known full window still constrains
capacity. Do not collapse a nearly full short window into a provider's comfortable weekly reading. Derive elapsed fraction
from reset minus duration only for a declared fixed window; other semantics need an explicit adapter.

**Claude's status-line observer.** Keep the tee conditional on that captured supported payload. Build a best-effort observer
with no model call, isolated from renderer failure. Store only an allow-listed normalised schema, not the raw input.
- Forward original stdin bytes exactly once to the original command. Preserve quoting, stdout, stderr and exit behaviour;
  bound observer parsing/writing time and prevent recursion. Observer failure must not blank or corrupt the old renderer.
  A renderer failure must not prevent an otherwise valid observation being recorded.
- Keep the original command/config separately. Preview installation and rollback, including Windows/shell quoting and
  existing command forms. Give exact supported settings instructions for the person to apply; no universal one-line
  promise and no automatic edit of their settings. Uninstall restores the saved configuration.
- Persist with atomic replacement and concurrent-writer ordering by actual observation time. A late older session cannot
  overwrite newer data. Missing/empty/malformed fields produce an explicit failure/missing record; they do not refresh the
  last-good timestamp. Merge windows by identity, retaining per-window failure and age.
- Bind the cache to a local account/provider profile token, without raw account identifiers. Invalidate on sign-in/profile
  change; if continuity cannot be established, show unknown. Reject expired-window readings. State age limits in settings;
  last-good and last-attempt times are distinct. A background-only session may have no current producer.
- Raw Codex reads can take up to 30 seconds. Cache them outside the synchronous renderer/hook path with a bounded refresh;
  failed reads retain their old observation time and report failure. Do not copy the old 15-minute cache rule as freshness.

**New pure pacing policy.** Short-window capacity is a constraint; weekly pacing is an advisory objective. All-window
capacity, provider blocking, owner caps and reserve take priority over weekly advice.
- Do not pass an elapsed fraction to the weekly `target`/`ceiling` functions. For a supported fixed weekly window use
  elapsed fraction f clamped to [0, 1], target share f and paced ceiling min(1, 7f/5). Before reserve release, also cap that
  ceiling at 1-r, where r is the owner's reserve share.
- Specify reserve release as a fraction of that window: default 6/7 elapsed for the fixed weekly objective. Do not apply
  “last 24 hours” to a five-hour window. Short windows constrain remaining capacity; they do not inherit a weekly pace.
  Unknown or other window semantics report unknown until a duration-aware policy for them is stated and tested.
- Settings state reserve, age limits, near-full threshold, pace tolerance and parallelism cap. Validate ranges and test
  exact boundaries. Fresh all-window headroom is necessary for a suggestion to increase optional spending.

**Advice, with honest limits.** Return behind pace, on pace, ahead of pace or unknown for each eligible weekly objective,
plus every window's headroom, age, blocking state and reason. Preserve the owner's reserve and caps.
- Behind pace permits a suggestion to use more only when useful independent parallel work exists, readings are fresh and
  every constraining window has room. Low use does not establish that tomorrow's allowance would otherwise be wasted.
- On pace keeps the usual workload, owner cap and reserve. It does not automatically increase fan-out.
- Ahead of pace suggests conserving optional fan-out. Near-full or blocked windows recommend no additional optional
  subagents there. Unknown/stale readings never trigger “use more”, a higher cap or a cross-provider transfer. They cannot
  increase optional spend relative to the no-reading policy.
- This advice is a display/recommendation, not an enforcement claim. Parallel sessions may act on the same headroom and
  already-running work remains in flight. Mandatory checks and the person's task work keep their existing policy.
- Cross-provider suggestions additionally need a configured executor/bridge, comparable task/tool support and a per-source
  disclosure grant for that provider. Unused allowance alone supplies none of them.
- If the owner enables control of optional G6 fan-out, build it only at a real launch boundary: atomic in-flight reservations,
  conservative marginal-cost caps, all-window constraints and crash release/expiry. Reserve before launch and account for
  pending work. Unknown/stale data cannot raise admission. Without a controlled seam, advice remains advisory.

**Delivery.** One G6 MCP tool returns both providers' windows and advice. Locate the approved vocabulary mapping as in 12.5
before fixing its name. Claude's plugin adds a one-line context note only when advice changes. Codex gets the same tool and
skill/`AGENTS.md` guidance to read it before fan-out. No automatic provider switch is implied. GUI and TUI/CLI stay frozen.

### 13.3 What is measured

Freeze fixtures, supported versions, environment, units/denominators, missing/failure cases, costs and numerical thresholds
before implementation. Deterministic correctness comes before claims about usage benefit.
- Test all reported Codex and Claude windows, fixed/unknown semantics, invalid times/durations/shares, full short windows,
  provider blocks, reserve release, reset and threshold boundaries. Zero window loss or stale-driven spending increases on
  the suite. Include missing/empty/malformed status input, unsupported versions, sign-in switches, two racing sessions and
  background-only use. Test exact forwarding, quoting, renderer failure, observer failure, timeout and rollback separately.
- If optional admission is enabled, test atomic reservations, concurrent launches, all-window refusal and crash recovery.
  No duplicate reservation of room on the declared suite. Recorded readings test rules, not causal benefit.
- Report weekly exhaustion and unused allowance as observations only. To assess advice, compare equal-workload periods or
  paired replay decisions under a frozen protocol: avoidable refused work, useful completed tasks, owner interruptions,
  uptake and actual added cost. Report confounding, missing readings and already-running work. No guarantee of efficient use.

### 13.4 Order and the owner's decisions

Build the normalised window contract and boundary fixtures first, then the supported Claude observer and both-provider MCP
display, then the advice and guidance. Optional enforcement follows only if enabled at a controlled launch boundary. Each
step has its own tests and Codex review loop. It shares skill/MCP/hook and subscription capacity with the question slice;
it may use a free slot without displacing section 7's priorities.

The owner has approved the capability with the freeze. The person chooses whether to install the previewed status-line
wrapper, its supported settings and rollback, the stated policy defaults or explicit thresholds, and any optional launch
control. These are concrete installation/policy decisions, not permission for an agent to infer source disclosure grants.

## 14. Frozen 2026-11-08: the agent uses what ships; Eurisko and Experta

**Status.** Part of the design the owner froze on 2026-11-08. Codex's challenge is folded in as corrections within this scope.

### 14.1 The skill routes to what ships

- **The skill changes with the capability.** `using-g6`, and the guidance Codex reads through its plugin and `AGENTS.md`,
  changes in the same commit as each capability an agent should use:
  - A's behaviour question and the report;
  - D's exact checks;
  - the Context Engine's tools;
  - the allowance advice;
  - fitness reports and extensions;
  - the formal bridge (section 15).
- **It never names a tool that has not shipped.** An agent told to call a missing tool fails worse than one never told.
- **An inventory test, from the code.** Every MCP tool, and every component op marked for agents, is either in the skill's
  routing or listed with why an agent does not call it. Every tool the skill names exists. The test fails on a member it
  cannot classify.
- **The skill routes; each component's own SKILL.md carries the detail.** The 1,800-word cap holds.
- **Measured** (subscription runs): on development fixtures, a fresh session given a task that needs a capability calls it.
  Each capability is counted.

### 14.2 Eurisko as a method kind in `cognitive_bridge`

- **What exists.**
  - `learning/seeds.py`: 16 first-party seed heuristics, loaded as unmeasured candidate theories. Nothing in the product
    calls it.
  - PR #4: a bounded bootstrap and `heuristic_discovery`, with fixed mutation operators, lineage and generation caps, and
    paired success-and-cost trials. Its promotion only checks that an approval and a shadow digest are present.
  - The earlier repository's `adapt_eurisko` specialises, generalises and mutates heuristics, and synthesises them from gaps.
    But it credits every heuristic a run names alike, and its "worth" is a fixed number, never measured.
  - In PR #8's `cognitive_bridge`, a `MethodVariant` declares a kind, method, representation, engine, operation, cost and
    expected evidence. Non-engine kinds are ranked but never executed, and credit comes only from evaluator-admitted trials
    bound to the variant's definition.
- **The design.**
  - A heuristic is a `MethodVariant` of a non-engine kind, `heuristic`. It proposes how to search or decompose (a strategy),
    never evidence.
  - It ranks with the other kinds, and earns credit only from paired trials that vary that one heuristic, admitted by the
    evaluator and bound to its definition. The archive's shared credit is not used.
  - New heuristics come from PR #4's operators, with lineage and caps. Each starts unmeasured, and its measured worth replaces
    any fixed one.
  - Promotion goes through the adaptation gate with a person's approval. The mere presence of an approval is not enough.
- **Where:** the selector's learning stage (section 7, step 5), once paired outcomes exist, with the seeds as its first
  candidates. PR #4 is harvested into it, and what is not used is retired under section 6's rule.

### 14.3 Experta: retired, its role kept

The earlier repository's `adapt_experta` had four recorded defects: rule dicts that never fired, a cross-component forgery,
evidence inversion, and a concurrency fault. It was folded into `g6.datalog`'s sessions, not ported as an engine. Rule
reasoning is done by `g6.datalog`, `g6.prolog` and `graph_reasoning`. Under section 6's rule, Experta is retired as a
component, with that reason.

### 14.4 Order

The skill rule applies to every slice from now on. Eurisko comes with step 5.

## 15. Frozen 2026-11-08: from intent to a formal property, with the translation checked

**Status.** Part of the design the owner froze on 2026-11-08. Codex's challenge is folded in as corrections within this scope.

### 15.1 Why

A formal result is only as strong as the translation into it. A derivation of a mistranslated property certifies the wrong
statement (owner, 2026-11-08).

**What exists** (surveyed 2026-11-08):
- No translation from intent to a formal statement exists in G6.
- `harness/verification_proposer.py` drafts abstract models, marked `model_hypothesis`.
- `harness/verification_formal.py` reports a formal result as specification, model and correspondence, and replays
  counterexamples on the code. So the correspondence between the model and the code is checked, but the correspondence
  between the intent and the specification is not.

### 15.2 Three layers of specification, each a hypothesis until its translation is checked

1. **Confirmed examples:** the person's, from A, compiled as `example.v1`.
2. **Properties:** `relation`, `differential` and `state_invariant`, drafted by the caller or a model.
3. **Formal statements:** SMT-LIB, a Lean 4 statement, a TLA+ specification or a Datalog program. A model (the autoformaliser)
   drafts them from the confirmed intent and the properties.

Authority follows where the expectation came from, as rule 2 of the verification-search rules already says.

### 15.3 The translation checks (section 11's componentry)

A formal statement counts as the intent's translation only when all of these hold:

1. **Examples.** It accepts every confirmed example and rejects each rejected-outcome witness (11.2 item 3). The engine decides
   this, never the model.
2. **Neither vacuous nor conflicting**, by `formal.check_contract`.
3. **Two independent translations agree.** They come from different model families, with their independence stated, and are
   compared by `check_equivalence`. Where they disagree, the distinguishing assignment becomes A's question to the person: for
   this input, A or B? This is CEGIS run on the specification rather than the program.
4. **Back-translation.** The statement is rendered in plain words and shown to the person in the behaviour form. The renderer
   is a template where one exists, otherwise a model other than the one that wrote the statement. The person confirms or
   rejects it, and a rule is shown in full first (3.2).
5. **Scope and correspondence are recorded:** what the statement covers, and which observation it constrains (11.2 item 2). A
   superseded answer reruns the evidence that depended on it (11.2 item 7).

What the outcomes mean:
- **A statement that fails any check** is reported as an unconfirmed formalisation, with the reason, and checks nothing.
- **A statement that passes checks 1 to 3** stays `model_hypothesis` until the person confirms check 4. Then it is
  `person_confirmed`.
- **The autoformaliser's own acceptance is never evidence:** a parse that succeeds, or a process that exits zero.

### 15.4 Porting the earlier repository's autoformaliser (authorised under G6's licence, 2026-11-08)

- **It targets** Lean 4, SMT-LIB2 and Z3 expressions, with context, retrieval from a library of earlier statements, and an
  attempt budget.
- **Its model** is called through G6's host callers. Nothing is downloaded automatically: any local weights are read only
  from the person's cache, as `ctx_colbert` does.
- **Acceptance moves into G6's engines:**
  - Z3 runs `check`;
  - a Lean statement is bound to the expected theorem, as `g6.lean` already reports for kernel, axioms and statement;
  - a missing TLC is unavailable, never success.
- **The archive's four defects become refusal tests:**
  - an SMT translation was parsed and never checked;
  - Lean exiting zero accepted an unrelated declaration;
  - a missing TLC reported success;
  - the Curry–Howard checker answered valid unconditionally.
- **Not ported:** the archive's math-reasoning and Curry–Howard format conversions, which return placeholder success.

### 15.5 Program synthesis

- **The coding agent synthesises the program.** G6 supplies the checked specification and checks the result.
- **`cegis` and `algorithm_design` synthesise bounded pieces,** where an engine decides the property: D (3.4), and section 5's
  self-improvement row.
- **The same search for a distinguishing input** runs over candidate programs and candidate specifications (15.3, check 3).
- **G6 does not become a general synthesiser,** under the scope rule of 2026-10-28.

### 15.6 Its place in `cognitive_bridge`

`MethodRouter` maps an evidence need to the formal kind, one of its five allowlisted engines. That kind consumes only a
statement that passed 15.3. Until then, the plan stays `candidate_unreviewed`.

### 15.7 What is measured (rows for section 5)

| Piece | What is measured | Bar | Cost |
|---|---|---|---|
| Translation fidelity | On a fixed set of confirmed specifications, with known right and mutated formalisations, by language: the share of wrong translations refused by checks 1 to 3 before the person sees them, and the share passing them that the person rejects at check 4 | set before it runs | model calls |
| Archive defects | All four are refused | all refused | none |
| Questions from disagreement | Unnecessary and missed questions, counted as A's are | as A's | the person's time |
| Cost | Model calls per translation | reported | — |

### 15.8 Order

It comes after the question slice, which supplies the confirmed examples and the form, and after slice 2's evidence
semantics. It runs beside D (section 7, step 4), as its own change with its own review loop.

## 16. Ruled by the owner, 2026-11-08: no data reaches a cloud model without consent

**Status.** The owner's ruling, part of the frozen design. Every section, and G6's existing model calls, must meet it.

**The ruling.** "Users must provide consent to share any data with cloud model, otherwise it needs to only stay local if
users do not want to share with non-local model; this must be implemented for trust effectively and correctly."

### 16.1 What it means

- **Default deny.** Nothing derived from the person's data reaches a model not running on the person's machine unless the
  person has granted it. That covers a passage, a quote, a summary, a derived answer or a patch.
  - The person's data is their documents, their repository, their confirmed answers, and anything indexed from them.
  - Granting is through the client's dialog. There is no default grant, no terminal fallback and no answer from an agent.
- **What a grant names:** the source or scope; the provider, for example Anthropic through Claude Code or OpenAI through
  Codex; and the time it was given.
  - It can be revoked, and revoking stops future sending.
  - The dialog says plainly that what was already sent cannot be recalled.
- **The coding agent counts.** Claude Code and Codex run their models in the cloud.
  - G6 returns nothing from an ungranted source to them: no passage text, no quote, no summary.
  - The reply says only that results were withheld from a source not shared with this provider, and how the person can grant
    it.
- **A local model may use anything in scope without a grant.** That means Ollama, or weights on the person's machine.
  - "Local" means G6 can see the endpoint is on this machine, from a loopback address or a local process.
  - A proxy that forwards to the cloud is not local. When G6 cannot tell, it treats the endpoint as cloud.
- **G6's own model calls follow the same rule.** Every call G6 makes to a non-local provider with the person's data needs a
  grant for that data. That covers:
  - the K-mode reviewer that receives a patch;
  - the proposers;
  - the autoformaliser (15.4);
  - the search planner (12.4);
  - the skill proposer (12.5);
  - the fitness reports (12.7).

  The repository G6 runs in is a source like any other, so its first use asks once per repository and provider. This changes
  the K loop's current behaviour: without a grant, it reports that the review was not sent and ends `untested`.

### 16.2 How it is built

- **One consent check, in front of every provider caller,** outside the bound files. `core/llm.py` and
  `core/host_models.py` stay unchanged and are wrapped, as `WatchedClaude` is (`self_maintenance/usage.py`).
- **Grants are kept in a hash-chained ledger under `G6_HOME`,** read under a lock. A grant is checked at the moment of sending,
  against the exact sources whose data the request carries.
- **Each request carries its sources.** Data with no recorded source counts as the person's, and is withheld from a cloud
  provider.
- **A disclosure log the person can read:** what went where, by source, passage or file, provider and time. It records ids and
  hashes, never the text.
- **An inventory test, from the code.** Every caller that can reach a non-local provider goes through the check. A new caller
  fails the test until it does.

### 16.3 What is measured (a row for section 5)

| Piece | What is measured | Bar | Cost |
|---|---|---|---|
| Consent | Planted ungranted data never appears in any request bound for the cloud, on every path, captured at the caller boundary with fakes. That covers the Context Engine, reviews, proposers, the autoformaliser, the planner and the skill proposer | zero, on every path | none |
| Revocation | After a revocation, no later request carries that source | zero | none |
| Local only | With no grant, every capability that can run on a local model still does, and says what it could not do | listed per capability | none |

### 16.4 Order

It comes first in every slice that sends data to a model: before the Context Engine's tools are exposed (12.9, step 1), and
before section 15's autoformaliser. The change to the existing K loop is its own change on the integration line, test first,
with its own review loop.

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

## Appendix C: Codex's challenge of sections 12 and 13, and what changed

All twenty challenges were read. The report treats section 11 as unapproved and recommends removing or deferring layers.
The owner's freeze of sections 1 to 15 governs this edit. All named layers stay in 0.3.0. Code survey corrections and
safety requirements are adopted. No model or service was run for this document edit.

| # | Challenge | Change |
|---|---|---|
| 1 | Scope lacks host authority and misses internal/legacy reads | Adopted: 12.6 binds principal/project and deny-by-default grants; every read, graph intermediate, cache and model consumer checks revision/generation and revocation; boundary limits stated. |
| 2 | Local indexes do not keep model-bound excerpts private | Adopted: 12.6 separates storage from per-source/session provider disclosure; all fields use redacted views with canonical mappings; host actions and downstream injection tests added. |
| 3 | Hashes and stale fields do not implement lifecycle or reconstruction | Adopted: 12.6 and first slice add clock checks, membership, deny-first tombstones, atomic generations, retry receipts and dependencies; retention, deletion and prior disclosure are separate. |
| 4 | Retrieval cannot silently decide intent or suppress A's question | Adopted: 12.1 requires versioned normative scope, conflict handling and predicate/observation mapping; substantive designation uses A's budget; false suppression and wrong patches measured. |
| 5 | Elasticsearch reading and current fusion lose identity and disagreement | Adopted: 12.3 adds managed record decoding and new passage-ID fusion with immutable contributions, distinct conflicts, full rendered budgets and redacted metadata. |
| 6 | Flat hits cannot represent paths, aggregates or partial execution | Adopted: 12.3 adds a scoped envelope with typed passage/path/structured payloads; backend capabilities and unsupported/unavailable outcomes stay explicit. |
| 7 | Gate approval and document IDs are not a writer protocol | Adopted: 12.3 specifies pinned targets, typed membership writes, per-item receipts, idempotent retries, publication/read-back and independent deletion consent; local HTTP limit retained. |
| 8 | Stored assertions and value offsets do not establish graph relations | Adopted as order: containment/import witnesses are measured before model edges; typed relation provenance, uncertainty, composition and laundering checks added. Model graph remains required in 0.3.0. |
| 9 | Sensitive permissions and lesson approval are not client consent | Adopted: 12.5 adds explicit digest-bound dialog flows, exact roots/effects and job readiness; unavailable consent changes nothing; `ctx_ace` remains a proposal store. |
| 10 | Existing skill scans supply neither standalone admission nor safety | Adopted as order: new skill schema, visible authorised trials, held-out admission, version invalidation and rollback follow measured retrieval. Skill installation remains in 0.3.0. |
| 11 | Discovery and adaptation do not supply confined production activation | Adopted as order: isolated installer, digest/grant checks and shadow activation follow reproduced use-case limits; production merge stays with the person; bound-core limits are explicit. |
| 12 | SolverPlanner does not supply a generic preserved-result wrapper | Adopted as order: two typed concrete contracts, independent standards and strict isolation precede generalisation. Removal declined because the owner froze the generic wrapper into 0.3.0. |
| 13 | Persistence needs a workload, bounded search and platform acceptance | Adopted: 12.2 corrects caps/cache claims; 12.3 freezes resource/quality/recovery tests and model schema, compares cheaper baselines and requires repair after an unsupported spike. Persistent ColBERT stays required. |
| 14 | Planner priorities lack applicability, utility and stopping rules | Adopted as order: explicit routes/fixed fusion are measured first; 12.4 defines overlap, availability, unknown coverage, limits and event fields. Planner remains required; learning needs paired judged trials. |
| 15 | Fitness declarations and self-index answers are not an oracle | Adopted as order: baseline-pinned claim inventory and executable use-case checks precede extensions; workforce contract count corrected, walker versioned and held-out/gold material excluded. |
| 16 | Promotion rows lack protocols and postpone the decisive local test | Adopted as order: 12.8 requires frozen denominators, labels, thresholds, costs and failure handling; 12.9 measures one safe local A slice first. No frozen layer is removed. |
| 17 | Weekly meter types discard short windows and misapply pacing | Adopted: 13.1–13.2 reuse only the raw Codex read; all-provider windows, explicit semantics, new fractional policy and short-window constraints replace weekly-type reuse. |
| 18 | Status-line tee can fail, race or advertise stale capacity | Adopted: 13.2 requires supported fixtures, exact forwarding, failure isolation, atomic ordered writes, profile binding, unchanged last-good time, expiry and rollback; stale data never increases spend. |
| 19 | Advice cannot ensure useful fan-out or exhaustion prevention | Adopted: 13.2 keeps advice advisory; useful work, reserve and all-window headroom are required; optional control needs a real launch seam/reservations, and provider transfer needs an executor and disclosure grant. |
| 20 | Present-tense claims overstate readiness, checking and external facts | Adopted: sections 12–13 distinguish code survey, new work and measured admission; external assumptions stay unchecked pending supported-version checks; missing vocabulary reference is a naming prerequisite. |
