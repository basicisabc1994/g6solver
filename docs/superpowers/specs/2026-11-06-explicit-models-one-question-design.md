# G6 0.3: explicit models, one question, measured — design

Status: frozen by the owner on 2026-11-08, and 0.3.0 is all of it built. Sections 1 to 10 are version 2, approved on
2026-11-06: version 1 (178ef54) was challenged by Codex, all 16 of its challenges were read against the code and adopted, and
appendix A lists each. Sections 11 to 15 were added on 2026-11-07 and 2026-11-08 and approved with the freeze; section 16 is the owner's ruling
that no data reaches a cloud model without consent, which every section meets. Section 17, the owner's north star
for the user interface (2026-11-08), is proposed and awaits the owner's approval of its scope. Codex's
challenges of them are folded in as corrections within this scope (appendix B for section 11, C for 12 and 13, D for 14 and 15). Anything new goes to a list for
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
All named capabilities stay in 0.3.0. Later steps depend on earlier measured results; a failed gate requires repair.

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
- **An inventory test, from the code.** There is no universal agent-op marker today. New work defines a versioned, typed
  routing manifest keyed by fully qualified MCP tool name or `(component, op)`. Each entry has `agent_facing`, a route id
  or an exemption with a reason, and the shipping server version. This is the code definition of "marked for agents".
  Every typed component op needs an explicit classification; an absent entry fails, rather than meaning false.
  An offline registration recorder enumerates actual MCP registrations, including loops, without starting a service or
  calling a model. It joins these with typed component operations and the manifest. Each MCP tool and agent-facing op
  must resolve to a route or reasoned exemption. Missing or unknown members, stale references and nonexistent tool names
  fail the test. A prose mention alone is not a route.
- **The skill routes; each component's own SKILL.md carries the detail.** The 1,800-word cap holds.
  Route ids resolve to short task instructions and linked detail files. Generic component discovery stays through
  `g6_help`, `g6_find` and `g6_describe`. The test checks links, names and the root word count separately.
  In the same capability commit, replace the current "at most 3 questions" guidance with A's persistent one-question
  default. A has no terminal fallback and no agent-supplied answer. Do not copy another component's approval fallback.
- **Measured** (rows for section 5): a frozen manifest has positive and negative tasks for every capability, fresh-session
  setup, expected valid calls, completion criteria, and time/call/question budgets. Count valid timely calls and useful
  completion on positive tasks, unnecessary or malformed calls on negative tasks, and recovery from a missing tool.
  Report refusals, unavailable runs and costs with full denominators. Freeze a positive minimum completion rate and maximum
  error rates before running; calling something or refusing everything cannot pass. Development and final cohorts stay
  separate under 3.6. Section 16 applies to every model call in these trials.

### 14.2 Eurisko as a method kind in `cognitive_bridge`

- **What exists.**
  - `src/g6/learning/seeds.py`: 16 first-party seed heuristics, loaded as unmeasured candidate theories. No product call was
    found in the code survey.
  - PR #4: a bounded bootstrap and `heuristic_discovery`, with fixed mutation operators, lineage and generation caps, and
    baseline/candidate success-and-cost fields. These are not observed paired trials. Its promotion checks a recommended
    assessment and truthy approval/shadow fields, not an adaptation-gate decision. The bootstrap discards its returned
    heuristic text; those calls are not an application mechanism.
  - The earlier repository's `adapt_eurisko` specialises, generalises and mutates heuristics, and synthesises them from gaps.
    But it credits every heuristic a run names alike. Its "worth" is caller-supplied or a default, with no established
    paired measure.
  - In PR #8's `cognitive_bridge`, a `MethodVariant` declares a kind, method, representation, engine, operation, cost and
    expected evidence. Non-engine kinds are ranked but never executed, and credit comes only from evaluator-admitted trials
    matched to a routing-descriptor digest. That digest hashes labels, not strategy bytes. The admission hook is external;
    the supplied scorer uses absolute resolution and regression rates and estimated cost, not paired benefit or actual cost.
    Its in-process assurance supplies control scaffolding, not execution custody or promotion authority.
- **The design.**
  - A heuristic is a `MethodVariant` of a non-engine kind, `heuristic`. It proposes how to search or decompose (a strategy),
    never evidence. It sits beside the mandatory evidence plan and cannot satisfy or remove an engine obligation.
  - **Bind the definition.** A new immutable strategy artefact contains instruction text or operator implementation,
    trigger and scope, prompt/template and application mapping, implementation version and relevant configuration.
    Its content digest joins the variant definition and evaluator receipts. Admission resolves and checks the actual bytes.
    A changed body, trigger or mapping starts unmeasured even if its name stays. The present label digest identifies only
    the routing descriptor.
  - **Apply the strategy.** A new caller adapter supplies one frozen instruction to the search/decomposition stage, under
    the same task and information budget, and records the applied digest and host-observed execution. It cannot rewrite the
    standard, oracle, evidence plan or gate. Begin with the 16 finite seed instructions. PR #4's operator names need reviewed
    mappings that transform actual strategy instructions; without a mapping, a child is only a proposal. The trusted adapter
    checks lineage, generation and child caps. Do not port discarded bootstrap calls as evidence that a strategy ran.
  - **Admit paired credit.** The new outside evaluator records pair/cohort/case and replay identities, both strategy hashes,
    the common task, agent, information, oracle, environment and budget, and the one permitted strategy difference. It checks
    that difference against observed execution, with a fixed randomness and order policy. Both arms carry measured success,
    time and cost; missing, refused and interrupted arms remain visible. Caller-asserted digests or outcome fields do not
    supply custody. The archive's shared credit is not used.
  - **Measure worth.** Freeze the paired benefit/cost statistic, case grouping, regression limits and promotion bar before
    trials. Retain regressions and incomplete pairs in the report. A new scorer adapter uses that statistic and measured
    costs; do not relabel PR #8's absolute success proxy. Learning stays off and candidates stay unmeasured until admission
    and the paired scorer exist. New instructions start unmeasured; measured worth replaces fixed worth.
  - Promotion uses the actual adaptation gate, outside candidate control, with the paired receipts, shadow checks and the
    person's approval bound to the exact strategy. PR #4's local `assess/promote` checks are replaced on this path.
- **Where:** the selector's learning stage (section 7, step 5), once paired outcomes exist, with the seeds as its first
  candidates. PR #4 is harvested into it, and what is not used is retired under section 6's rule.

### 14.3 Experta: retired, its role kept

Experta is retired as an integration decision under section 6, with no engine port. Its rule-reasoning role is assigned to
`g6.datalog`'s sessions, `g6.prolog` and `graph_reasoning`. Current Datalog sessions keep facts over a fixed session program;
they do not establish a historical migration. Earlier notes name rule dicts that never fired, a cross-component forgery,
evidence inversion and a concurrency fault. Their historical attribution is not checked here and is not the retirement's
evidence. The current rule APIs and the decision to avoid a duplicate engine are enough to retain the role.

### 14.4 Order

The skill rule applies to every slice from now on. Define the manifest and measure routing first. Eurisko comes with
section 7, step 5: bind and apply seeds, measure fixed-strategy baseline/candidate pairs, then build the learned scorer and
content-changing operators against those results. Each step has a frozen gate under section 5; failure requires repair,
not removal of a layer from 0.3.0. The paired evaluator and measured strategy application precede any learned routing.
Section 16's consent boundary comes before model calls in operators, strategy application, evaluation or routing trials.

## 15. Frozen 2026-11-08: from intent to a formal property, with the translation checked

**Status.** Part of the design the owner froze on 2026-11-08. Codex's challenge is folded in as corrections within this scope.
The five checks, all four language adapters, the autoformaliser port and the synthesis fit stay in 0.3.0. Their build order
does not imply equal readiness today. Public wording is "person-accepted formalisation, with named checks and limits".

### 15.1 Why

A formal result concerns its encoded statement. A mistranslated property answers the wrong question (owner, 2026-11-08).

**What exists** (surveyed 2026-11-08):

- `src/g6/harness/verification_proposer.py` already proposes task-based formal models, including SMT initial-state, transition and
  invariant terms in `state_invariant.v1`, marked `model_hypothesis`. The accepted intent-to-formal bridge below is new work.
- `src/g6/harness/verification_formal.py` reports a formal result as specification, model and correspondence, and replays
  selected counterexample traces on supported code observations. It can report not reproduced, not replayable or not
  replayed. No counterexample means no replay, not general model/code correspondence. Intent fidelity is not established.

### 15.2 Three layers of specification, each a hypothesis until its translation is checked

1. **Confirmed examples:** the person's, from A, compiled as `example.v1`.
2. **Properties:** `relation`, `differential` and `state_invariant`, drafted by the caller or a model.
3. **Formal statements:** SMT-LIB, a Lean 4 statement, a TLA+ specification or a Datalog program. A model (the autoformaliser)
   drafts them from the confirmed intent and the properties.

Authority follows where the expectation came from, as rule 2 of the verification-search rules already says. Example
acceptance does not accept a generalisation. Preserve the confirmed executable examples even if the general rule stays
unconfirmed. Expectation provenance, formalisation acceptance and check-result kind are separate fields.

### 15.3 The translation checks (section 11's componentry)

All five checks below are required for acceptance on the declared scope. They are not a general guarantee of fidelity.
The observation adapters, supported grammar, renderers and acceptance receipts are new work.

1. **Examples through the same observation.** A versioned, reviewed adapter binds 11.2 item 2's setup, entry point, inputs,
   observation and assertion to typed formal terms. It covers return, error or argument effect explicitly, including
   domains, equality/comparison semantics, accepted observations and rejected-outcome witnesses (11.2 item 3). An encoding
   of a return cannot stand for an effect. Unsupported values or missing controls stay visible and unconfirmed.

   Initial reuse is `src/g6/cegis/z3_verifier.py`'s restricted single-return int/bool path, within its grammar and finite
   domain. This is not a general `example.v1` converter. Each language needs its own adapter and tests:

   | Language | New bridge obligation |
   |---|---|
   | SMT-LIB | Bind typed input/output terms, return/error/effect tags and numeric/comparison semantics to the executable observation; refuse variants outside the adapter's declared support |
   | Lean 4 | Encode reviewed values and ground propositions; bind the target and referenced definitions |
   | TLA+ | Bind initial state, actions or trace and an observation projection, with explicit bounds |
   | Datalog | Bind extensional facts, result relation and the declared meaning of non-derivability |

   Support is advertised per tested fragment, never from a language name alone. The engine decides the formal question;
   adapter correspondence to the executable observation needs its own tests. Missing adapter, engine or decision is
   unconfirmed, neither a failed property nor a successful translation.
2. **Domain, reachability and consistency.** Ask separately whether the domain is satisfiable, each guarded obligation is
   reachable within it, and the domain and all constraints can hold jointly. Test that accepted observations satisfy the
   relation and rejected observations violate it. State totality or uniqueness only where the function adapter requires
   it. Keep assumptions apart from claims; a specification relation need not hold for every unconstrained output.

   `formal.check_contract` checks SMT validity under premises, some validity without them, and multiple formulas' joint
   satisfiability without the premises. Reuse it for those named entailment obligations, not as a blanket specification
   acceptance test. Other languages need their corresponding obligations. Freeze interpretation fixtures: free
   `result = x + 1` is a useful relation despite failing unconstrained validity; `true` without premises does not constrain
   behaviour; `x > 100 => result = x + 1` is unreachable on `0 <= x <= 10`. Add a conflict that arises only within the domain.
3. **Diverse candidate comparison.** Two model families draft candidates within the same accepted scope. Record family,
   generation provenance, common sources and whether either saw the other. This is diversity, not demonstrated independence.
   `check_equivalence` is an SMT equality/equivalence check under common sorts and premises. It says nothing by itself about
   intent; two copies of `result = x + 2` agree while both miss an intended `x + 1`. Other languages need explicit comparison
   adapters. This check passes when equivalence is decided in the accepted domain, or a disagreement is resolved against an
   independently accepted criterion or the person's explicitly accepted scope. Agreement alone never accepts the intent.

   A distinguishing assignment is not necessarily an executable input or two unique outputs. Only a replayable observation,
   through check 1's adapter, can become A's question. For `result >= x` versus `result > x`, ask about the disputed observation,
   not invented unique results. Traces, answer sets and overlapping relations retain their native meaning. Include "both
   permitted", "neither", "premise wrong", "already specified" and skip where applicable. An assignment that cannot be
   concretised stays a model disagreement. Lack of a witness never marks intent determined.
4. **Back-translation with limits.** A new supported grammar/IR has an exhaustive renderer for its constructors. Render the
   actual checked artefact and referenced definitions, with domains, guards, quantifiers, observation, frame conditions,
   temporal bounds and exclusions. Show the formal text and digest beside its words in the behaviour form; never render
   just the original prompt. Build restricted SMT relations, fixed Lean proposition constructors, bounded TLA+ state/trace
   forms and finite Datalog rules in that order after measured earlier results. These renderers do not exist in the APIs.

   Unsupported constructs stay unconfirmed. A different model may offer a rendering as another unconfirmed interpretation,
   not a substitute for a supported renderer. A paraphrase can hide a changed quantifier or liveness rule; the person's
   approval records scoped acceptance, not established semantic fidelity. Measure incorrect renderings as well as formulas.
5. **Scope and correspondence are bound.** Record what the statement covers and the exact observation it constrains,
   including unsupported cases. Bind statement and definition bytes, adapters, assumptions, domain/bounds, rendering and
   standard revision. A superseded answer invalidates and reruns dependent evidence (11.2 item 7). Code correspondence is a
   separate obligation, not supplied by agreement, rendering or confirmation.

**One interaction budget.** All translation questions and rule confirmations use A's persistent budget, one per task by
default across retries. Combine the executable scenario and full rule/rendering confirmation in the original form when
ready. If that budget is already spent, stop unconfirmed unless the person explicitly requests more. No terminal fallback
or agent answer is allowed. Start with one candidate per family and at most two repair attempts in total; freeze wall-clock,
token and output caps before running. Do not regenerate until agreement. Unresolved or unavailable comparisons stay
unconfirmed. Section 16 governs drafting, repair, comparison and model rendering, including their supplied context.

What the outcomes mean:

- **An incomplete or failed acceptance check** leaves an unconfirmed formalisation, with the reason. Retain scoped solver
  diagnostics, conflicts and confirmed examples; it cannot support intent fidelity or a code requirement.
- **The person's confirmation** supplies normative acceptance of the displayed rule and scope, bound to the statement,
  definitions and rendering digests. `person_confirmed` is expectation provenance, not execution evidence or an independence
  waiver. The formal candidate and its check-result kind stay distinct.
- **Before promotion, extend 3.1's mapping for this path.** The evidence branch's compiled `example.v1` mapping is not a
  general formal-approval mapping. New work binds formal result/certificate, model, adapters, assumptions, accepted and
  rejected controls, author, freeze ordering, environment and subject bytes to resolvable receipts. The issuer alone sets
  requirement status. A model-only result supports only its explicitly modelled claim; code support also needs the stated
  correspondence evidence. Confirmation cannot relabel a model-written check as person-authored.
- **The autoformaliser's own acceptance is never evidence:** a parse that succeeds, or a process that exits zero.

### 15.4 Porting the earlier repository's autoformaliser (authorised under G6's licence, 2026-11-08)

- **The port targets** Lean 4, SMT-LIB2 and Z3 expressions, with context, retrieval from a library of earlier statements, and an
  attempt budget.
- **Harvest only generation/repair control flow and useful prompts.** G6's host callers replace the backend, behind
  section 16's per-source/provider consent check at send time. Context, retrieved statements, repair diagnostics and model
  renderings carry sources. Without a cloud grant, withhold that data; use a supported local path or report unavailable.
  Nothing is downloaded automatically. Any local weights come only from the person's cache through the host caller.
  Add overall wall-clock, token, attempt and output budgets, engine screens and confinement, not just an attempt count.
- **Acceptance uses named engine questions and polarity under 3.4:**

  | Question | Decision and limit |
  |---|---|
  | Does a domain/guard/constraint conjunction have a witness? | SMT SAT supplies a witness; UNSAT reports no witness for this encoding, including an unreachable guard or conflict as applicable |
  | Is an obligation valid under assumptions? | Search the assumptions conjoined with the obligation's negation: SAT is a counterexample; UNSAT supports the stated entailment, with domain consistency checked separately |
  | Do two relations differ in the domain? | SAT of the domain conjoined with their difference distinguishes them; UNSAT establishes equivalence only under those assumptions |
  | Does a named Lean theorem match and pass the native checks? | Bind its target name and non-empty expected statement to the already accepted artefact and frozen definitions; check supplied derivation source with G6's screens, kernel and axiom report |
  | Is a TLA+ invariant/property violated within the declared model/bounds? | Preserve the native TLC result and scope; missing TLC is unavailable |
  | Is a Datalog goal derivable from these facts/rules? | Preserve answers and the declared non-derivability interpretation; absence alone is not arbitrary logical negation |

  Witness searches are existential; validity and equivalence range over every assignment in the stated domain. Each request
  records quantifiers, assumptions, bounds and the native query. Unknown, timeout and unavailable never become
  a positive or negative decision. Preserve native UNSAT and conflict diagnostics; refuse unsupported evidence promotion,
  not every inconsistent specification. Generated SMT uses G6's typed declarations and term interface, not arbitrary programs.
  Drafting a Lean proposition, accepting its scope, generating derivation source and checking it are separate steps. Without
  that source, report "accepted specification; derivation not supplied". A co-generated expected statement cannot replace
  the accepted digest. No engine establishes the natural-language correspondence.
- **Four archive defect fixtures, with exact outcomes:**

  | Fixture | Required outcome |
  |---|---|
  | Parsed SMT `(assert false)` with no solver decision | Cannot mint evidence from parse success; a real consistency query records UNSAT |
  | Lean exits zero on `def unrelated : Nat := 0`, with accepted target `expected` and statement `1 = 1` | Cannot satisfy the frozen target name and non-empty expected statement |
  | Java/jar/TLC unavailable | Unavailable, with no success receipt and no automatic download |
  | Curry–Howard application of assumptions `P` and `Q` without compatible types, and a bodyless `P => Q` | Neither enters a supported evidence path; assert absence or refusal at the exposed boundary |

  The archive's Curry–Howard construction path returned success after construction; its checker did structural validation
  and rejected empty input. It did not answer valid unconditionally. The two invalid derivations target the actual hole.
  Excluding that code is not repairing its old checker. These fixtures test admission, not a blanket ban on UNSAT.
- **Not ported:** the backend loader, archive success schema and acceptance helpers, confidence-based grounding labels,
  strategy/theory credit, library trust labels, or the math-reasoning and Curry–Howard conversions. Retrieved statements
  remain versioned untrusted context with source/content hashes and section 12's access scope; any claim of an independently
  checked statement needs a resolvable receipt. Shared retrieval is recorded in candidate provenance. Expose no archive
  `z3_python` conversion unless a reviewed typed adapter is supplied: its implicit integer and division semantics are not
  general Python semantics. The SMT-LIB and typed Z3-expression targets remain in the port.

### 15.5 Program synthesis

- **The coding agent synthesises the program.** G6 supplies a person-accepted specification with named checks and limits,
  and checks the result only within the supported adapters and evidence rules.
- **`cegis` fills supported sketches against an explicit oracle and domain,** optionally with the restricted single-return
  int/bool SMT verifier. Its counterexample needs confirmation by the oracle. A's partial example table leaves other inputs
  unanswered and may leave synthesis inconclusive. Neither candidate nor translator fills the missing expected values.
  An accepted relation becomes an executable oracle only through a reviewed adapter with an explicit output-selection rule;
  without it that route is unsupported. The oracle and standard stay outside candidate control, as section 5 requires.
- **`algorithm_design` proposes catalogued compositions** and runs fixed kernels with native instance checks. Representation
  compatibility is not semantic compatibility; neither path is a general implementation synthesiser for a formal statement.
- **The shared idea is a distinguishing observation,** not a common implementation for program and specification search.
  Keep model-only results, kernel limits, unanswered oracle inputs and unknown correspondence in the coding agent's report.
- **G6 does not become a general synthesiser,** under the scope rule of 2026-10-28.

### 15.6 Its place in `cognitive_bridge`

`MethodRouter` has five evidence-method families: graph, causal, formal, predictive and optimisation. They are not five
formal-language engines. Its current formal route uses `formal.check_validity`/`check_propositional`; it has no acceptance
receipt input or Lean/TLC/Datalog route. New work adds explicit language adapters and receipt validation at execution.

Candidate engine queries can run before acceptance as model-only diagnostics, including the queries needed for 15.3.
The issuer-facing promotion path requires all five acceptance checks and their controls. At that boundary, resolve and
check the statement/definitions, scope, assumptions, adapter and rendering digests and current standard revision. A
`candidate_unreviewed` or other status string is not enforcement. Strategy recommendations in 14.2 remain beside this plan.

### 15.7 What is measured (rows for section 5)

| Piece | What is measured | Bar | Cost |
|---|---|---|---|
| Proposal fidelity and check detection | By supported language/fragment: right accepted/refused and wrong accepted/refused, before and after each check; proposal errors apart from detection errors; unsupported, unrun, timed-out and unavailable cases with full denominators | freeze minimum right acceptance above zero and maximum wrong acceptance/error rates before running; refusing everything fails | model calls and engine time |
| Rendering and person acceptance | Right/wrong formulas crossed with right/wrong renderings; wrong acceptance, right refusal, abstentions and person time, including survivors of checks 1 to 3 | freeze detection, usable-completion and time bars before running; rejecting every survivor fails | model calls and person's time |
| Observation and code correspondence | Accepted/rejected return/error/effect witnesses, unsupported adapters, and true model results paired with bad code correspondence; report preserved example enforcement separately | no promotion without required correspondence and custody on the suite; minimum supported coverage fixed beforehand | local engine/execution time |
| Archive defects | All four fixture groups in 15.4, including both invalid Curry–Howard derivations; check native diagnostics and supported-boundary refusal separately | no unsupported evidence receipt on any fixture; expected native outcomes retained | no model calls |
| Questions from disagreement | Replayable and non-replayable differences, unnecessary/missed/harmful questions, exhausted budgets, skips and unresolved scope; count all translation interactions | A's bars plus no extra question without the person's request; minimum useful completion fixed beforehand | person's time and replay cost |
| Cost and completion | Calls, tokens, wall-clock, attempts and useful completed translations per eligible case; refusals and unavailable cases included | frozen caps and positive minimum completion, per language; unavailable is not detection | all model, engine and person costs |

Before each row runs, freeze a fixture manifest with supported fragments, independent labels, discriminating witnesses,
rendering/adapter versions, environment availability, denominators, numerical thresholds and a pass/stop rule. Use small
human-authored requirements and reference formulas, exhaustive finite int/bool tables or separately written oracles.
Deterministic AST mutations cover boundaries, omitted guards, constants, result/input swaps and supported quantifier/domain
changes. Label a mutation wrong only with a known witness or independent checker; equivalent mutations are not wrong.
Include cases that match A's example but fail elsewhere, and model results with bad code correspondence. Do not let one
model write both the candidate and its labels. Keep development and final cohorts apart; retire any cohort that informs
tuning or routing under 3.6. Failed bars require repair and a fresh final cohort, not narrower post-run denominators.

### 15.8 Order

It comes after the question slice, which supplies the confirmed examples and the form, and after slice 2's evidence
semantics. It runs beside D (section 7, step 4), as its own change with its own review loop. Section 16's consent boundary
precedes every added model caller, including the port. Freeze the measurement protocol before each step.

1. Bind examples, observations and issuer mapping; implement and measure the narrow SMT bridge, explicit query polarity
   and deterministic renderer against right and wrong fixtures.
2. On that measured result, build the diverse-candidate, budgeted-form and receipt path, and harvest the bounded
   autoformaliser loop. Measure usable completion, wrong acceptance and person time together.
3. On those results, build and measure the Lean, TLA+ and Datalog adapters, renderers and comparison obligations, one
   supported fragment at a time, with native engines and archive fixtures. Missing engines remain visibly unavailable.
4. Bind the accepted artefacts at `cognitive_bridge` execution and the supported synthesis/oracle adapters; measure the
   complete developer route and update the skill in the same capability commits.

All these layers remain required for 0.3.0. A failed gate requires repair before the dependent step; it is not permission
to remove a language, the port, comparison, rendering or the synthesis fit from the frozen scope.

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

## 17. Proposed 2026-11-08, not approved: the north star for the user interface

**Status.** Proposed by the owner on 2026-11-08 as a north star for the interface, separate from the technical design. The
owner's specification (G6-UX-VALUE-001) is read here against the frozen design. It needs the owner's approval of 17.4's
scope, and Codex's challenge is folded in as corrections.

**The north star (owner).** G6 brings the most end-to-end value to the person by studying their real workflow, finding the
binding constraint on the value they want, and suggesting and helping deliver its removal. Its worth is what it lets the
person accomplish, with the least burden on them. A person who needs G6 less afterwards counts as success.

### 17.1 What it means for every feature: the permanent gate

Every slice brief and every 0.3.0 feature answers the owner's eight questions before it is built:
1. which outcome it improves;
2. which real workflow constraint it addresses;
3. what evidence shows that constraint exists;
4. why it is better than a simpler alternative;
5. what effort, risk, delay, cost or uncertainty it removes;
6. how its working is checked;
7. how end-to-end value is measured;
8. whether it adds a constraint of its own.

A feature that cannot answer them is not prioritised for being impressive.

### 17.2 The loop, mapped onto what exists

The owner's loop is understand → observe → model → diagnose → recommend → implement → check → measure → learn, applied with
the Theory of Constraints. Each stage maps onto existing code:

| Stage | Existing piece | New |
|---|---|---|
| Understand | `g6_premortem` and `intent_engine`; A's question (3.2); the Context Engine (12) for authorised context | the outcome is stated before the task |
| Observe and model | G6's own task records: review rounds, failure rows, repair loops, question records, timings | a lightweight per-task workflow record built from them, with observed and inferred steps kept apart |
| Diagnose | G6's constraint machinery for its own loop (`identify_constraint`, `tick_rca`, the reflexive bottleneck step), pointed at the person's records | bottleneck hypotheses, each with its evidence, counterevidence, expected end-to-end effect and a cheap test; a hypothesis is never shown as fact |
| Recommend | the report (`harness/patch_report.py`) and section 9's repair rule | at most three ranked suggestions, which may be a step outside G6 or "do nothing" |
| Implement and check | section 9 (automatic when the fix is clear), the client's dialog, the issuer (3.1) | technical correctness and user value are assessed apart; "checked" never means the person's outcome improved |
| Measure and learn | section 5's rows; B (3.6) | time to a checked outcome, rework cycles, the person's effort, and one optional question ("did this make your work easier?") outside A's budget, rate-limited |

### 17.3 Fixed choices

- **States and words.** G6's states stay `verified`, `qualified-draft` and `blocked-escalated`. The specification's other
  spellings are not adopted. Public copy says "checked".
- **The interface.** The GUI and TUI/CLI are frozen (owner, 2026-10-07), so in 0.3.0 the interface is the MCP replies and the
  report the agent shows. The report leads with the outcome, the most likely constraint, and the next action. Its evidence,
  inference and recommendation are kept visibly apart.
- **Data.** Workflow records are the person's data: local by default, under section 16's consent rule, inspectable and
  correctable by the person, and never used to infer a person's motives.
- **G6's own friction is counted:** its questions, approvals, time and tool calls. If G6 itself becomes the constraint, it
  simplifies within its safeguards.

### 17.4 Scope (recommendation; the owner decides)

**In 0.3.0:**
- the 17.1 gate on every slice;
- the report restructured around outcome, constraint and next action;
- the per-task workflow record;
- the diagnose step on the coding vertical, with hypotheses only;
- at most three suggestions;
- G6's own friction counters;
- the optional feedback question;
- the specification's acceptance tests AT-003, AT-004, AT-005, AT-007 and AT-008, on fixtures.

**After 0.3.0:**
- versioned workflow graphs from git, CI and task systems;
- proactive suggestions;
- a visual workflow explorer, which needs the GUI freeze lifted;
- learned intervention selection;
- other verticals.

### 17.5 What is measured (rows for section 5)

| Piece | What is measured | Bar | Cost |
|---|---|---|---|
| Diagnosis | On recorded coding tasks, how often the named constraint matches a blind reviewer's reading. A slow step that does not bind is not chosen (AT-003) | set before it runs | model calls |
| Suggestions | Adoption, dismissal and repeats after a dismissal (AT-007) | set before it runs | people's time |
| Value | Time to a checked outcome and rework cycles, before and after, reported as association unless controlled | reported as measured | runs |
| Self-friction | G6's questions, approvals and time per checked outcome | reported | none |

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

## Appendix D: Codex's challenge of sections 14 and 15, and what changed

All sixteen challenges were read against the owner's frozen scope. Every named capability stays in 0.3.0. Recommendations
to start narrowly become measured build order, with repair after a failed gate. Section 16 governs every added model call.

| # | Challenge | Change |
|---|---|---|
| 1 | Examples lack an observation bridge; contract and equivalence APIs are SMT-specific | Adopted as order: 15.3 adds typed return/error/effect bindings and per-language adapters. The restricted SMT path is measured first; Lean, TLA+ and Datalog bridges remain required in 0.3.0. |
| 2 | `check_contract` cannot accept arbitrary specification relations | Adopted: 15.3 separates domain, guard reachability, joint consistency and observation tests from entailment; adds the three interpretation fixtures and a domain-relative conflict. |
| 3 | `person_confirmed` is normative provenance, not formal or code evidence | Adopted: 15.2–15.3 separate provenance, acceptance and result kind; add receipt mapping and correspondence obligations under the single issuer, preserving confirmed examples. |
| 4 | A back-translation can conceal the formula's mistake | Adopted as order: 15.3 requires renderers of the actual supported artefact, visible formal text/digests and renderer-error measurement. Later language renderers follow measured SMT results; model renderings stay unconfirmed. |
| 5 | Model-family diversity does not establish independence or intent | Adopted: 15.3 records shared sources and generation provenance; equivalence is scoped and disagreements need an accepted criterion, with unresolved comparisons left unconfirmed. |
| 6 | Symbolic differences need not be executable questions and can exceed A's budget | Adopted: 15.3 uses replayable observations, native answer spaces and A's persistent budget for every interaction; combines rule confirmation when ready and caps candidate repair. |
| 7 | Engine success lacks query polarity and Lean target/source obligations | Adopted: 15.4 names queries, quantifiers and SAT/UNSAT meanings; binds Lean target, expected statement and definitions to prior acceptance, with missing derivation source reported separately. |
| 8 | The variant digest binds routing labels rather than strategy bytes | Adopted: 14.2 adds immutable strategy content, trigger, mapping and configuration binding, resolved bytes at admission and fresh measurement after changes. |
| 9 | Absolute success and estimated cost are not paired heuristic worth | Adopted as order: 14.2 requires host-observed baseline/candidate receipts and a predeclared paired benefit/cost scorer; learning waits for the measured evaluator and remains required in 0.3.0. |
| 10 | Ranked heuristics are not applied; operator labels do not transform instructions | Adopted as order: 14.2–14.4 add caller application and observed digests beside the evidence plan; measure seeds before content-changing operators, with trusted lineage/caps and the actual adaptation gate. |
| 11 | The fourth archive defect was misstated and refusal needs exact fixtures | Adopted: 15.4 distinguishes structural checking from construction success; specifies parse-only SMT, unrelated Lean target, missing TLC and both invalid Curry–Howard fixtures, retaining native conflicts. |
| 12 | The port must exclude loaders, weak trust labels and implicit numeric conversions | Adopted: 15.4 harvests only bounded generation/repair and prompts; excludes archive acceptance/trust helpers and backend loading, scopes retrieval and requires typed conversions plus section 16 consent. |
| 13 | CEGIS needs an oracle; catalogue search is not general formal synthesis | Adopted: 15.5 states each component's actual role, explicit oracle/domain and output-selection obligations, and inconclusive partial tables; neither translator nor candidate supplies missing answers. |
| 14 | "Marked for agents" has no code definition and scans can miss registrations | Adopted: 14.1 adds a typed versioned manifest, offline registration recorder, route/exemption and link checks, the word cap, and corrected question/fallback guidance. |
| 15 | Refusing everything can win the measurements without helping a developer | Adopted: 14.1 and 15.7 require right/wrong acceptance/refusal, rendering errors, missingness, useful completion, costs, independent labels and frozen pass/stop gates with positive completion bars. |
| 16 | Wording overstates correspondence and confuses method families with languages | Adopted: 15.1 and 15.6 correct existing-code claims, add execution-boundary receipt checks and keep pre-acceptance diagnostics model-only; 14.3 removes the unchecked historical migration claim. |
