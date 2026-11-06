# G6 0.3: explicit models, one question, measured — design

Status: draft for the owner's review, 2026-11-06. Nothing here is built until the owner approves it.

## 1. Why

G6's coding study measured whether a harness around Claude Code makes it right more often, on real bugs neither had seen.
Thirty-six tests were registered before they ran, and none on unseen bugs met the fixed comparison rule. The instrument also
said why:

- **Claude Code alone is already right in most runs** where the issue determines the fix (about 75–82%).
- **The missing piece is intent, not a bug a check could catch.** 291 of 345 wrong final patches fail only the maintainers'
  new tests, and almost every wrong patch passes the issue's own example.
- **Checkers built from the same information share the author's blind spot.** The frozen reviewer's balanced accuracy was
  53.6%, and a reviewer from the other model family shares the natural misreading.
- **The agent often sees the choice and takes the conservative side.** In the last pilot, in 5 of the 10 wrong final patches
  the agent named the deciding behaviour and kept the other side.
- **Tuning fitted the tasks it was tuned on** and did not carry to new bugs, even in the same repositories.

The principle this design is built on: **a checker adds only what it knows that the author does not.** So G6's job is to
bring in information the agent lacks, from three independent sources, and to measure whether that helped:

- from the **person**, asked once, at the exact point where the task does not decide;
- from **execution**, where candidate patches behave differently;
- from **exact engines**, where the problem has a model that a solver, model checker or witness check can decide.

Every result says exactly what was checked, by what kind of evidence, and what was not.

This is what G6 means by AI agents checking their own work: checking that knows where it cannot reach, and says so.

## 2. Goals, non-goals, anti-goals

**Goals.** A technical developer working with Claude Code or Codex gets four things:
1. The open choices in a task, found and put to them as one concrete question ("for this input, A or B?"). The answer is
   bound as an expectation that later checks enforce.
2. A report that separates what was checked, by what, from what was not.
3. Exact checks wherever the problem admits them.
4. A measurement, on their own repository's past bugs, of whether G6 and their agent setup help, with the reason for each
   failure and what would be needed.

**Non-goals.**
- Any claim of uplift over Claude Code alone.
- Unattended self-improvement of G6's own code, which stays off and off the front page.
- New features on the GUI or the TUI/CLI, which are frozen; MCP is the surface of record.
- General formal verification of programs.

**Anti-goals: these would make it a failure even if it works.**
- A question on a task that already decides the answer. That costs the person time and buys nothing.
- "Checked" on anything only the author's own test supports.
- A selector that hides which mechanism ran, or why.
- A model's opinion counted as evidence, or several reviews from one family counted as several voices.
- Code that stays behind a research flag with no route into the product.

## 3. The pieces, and what each reuses

Every piece names the existing code it builds on, by branch. Where two implementations overlap, one is kept.

### 3.1 The report: one evidence model for every mechanism

- **What it does.** Every mechanism writes typed evidence records. A claim's status is computed from its evidence, never
  written by the mechanism.
- **Reuses:**
  - ECHA's records and claim rule (`research/echa`: `EvidenceRecord`, `ClaimRecord`, `evaluate_claim`). Under that rule,
    support needs an active check with a recorded negative control, reviews are leads only, and reviews from one family are
    one voice.
  - The product's certificate issuer (`evidence/certificate.py`), which is already graduated. It carries the Belnap state
    per requirement and keeps the execution receipt, the evidence statement and the formal certificate apart.
  - `core/ledger.py` and `core/vault.py`, both already graduated.
  - `patch_report.py` renders the result.
- **Kept over** PR #8's `assurance.py` dossiers, which use neither the ledger nor the claim rule; PR #8's useful fields are
  folded in.

### 3.2 A: intent into an explicit model, and one question

- **What it does.**
  1. The agent states what "correct" means as an explicit model: a rule table over a finite domain of named cases, a state
     invariant, or pre- and postconditions.
  2. G6 checks the model for conflicts and vacuity, and finds the open choices:
     - where two independent readings of the task both fit but disagree;
     - where the agent declined a decision point ("kept for back-compatibility", "a separate change").
  3. G6 asks the person once, in the client's own dialog, showing the competing behaviours.
  4. The answer is bound as a new predicate on the task's frozen standard. Under the standard-refinement rule, a standard is
     re-frozen only by adding predicates and is never weakened. Later checks enforce that predicate on the patch.
- **Reuses:**
  - `intent_engine` (`IntentContract`, `IntentElicitor`, `interpret`) and `verification_search.requirements_from_intent`.
  - `g6_premortem` (integrate-0.3).
  - The VS-4 path (`harness/verification_formal.py`): `state_invariant.v1` → `formal.check_contract` →
    `check_invariant`/`prove_invariant` → a trace replayed on the real code, reported in three parts (specification, model,
    correspondence).
  - Replay 6's rule tables (study code, never run): two readers, quotes checked against the issue, a bug marked ambiguous
    where the readers differ. They graduate from `validation/` into `harness/`.
  - Hypothesis H-decide's tagger for declined decision points (registered, not built).
  - `mcp/consent.py` elicitation.
  - CSIR's `standard` refinement (`research/mission_bridge/csir`).
- **New:**
  - the MCP tool `g6_confirm_behaviour` (planned as VS-D5, never built);
  - a store of confirmed expectations, digest-bound to the question and the artefact.
- **Rules:**
  - At most one question per task by default; the person can ask for more or none.
  - No terminal fallback, and no agent answer.
  - A decline or cancel leaves the expectation unconfirmed, and it is reported as such.

### 3.3 C: candidates, chosen by where they behave differently

- **What it does.** Several candidate patches run on shared inputs. The inputs where they disagree are not settled by a vote,
  because the study found the wrong reading is shared and voting amplifies it. They become A's question, or go to an exact
  engine where one exists.
- **Reuses:**
  - `differential.v1`, extended from base-against-patch to candidate-against-candidate;
  - `workforce/patch_checks._observe` and `_inputs`;
  - `verification_formal`'s Z3 search for a missed input;
  - the L9 scouts' probes (integrate-0.3).

### 3.4 D: exact engines and one three-valued contract

- **What it does.** Where the problem has a model an engine can decide, the engine decides. Every engine answers on one
  contract:
  - **yes**, with a witness that is checked again;
  - **no**, with a complete check under a stated bound;
  - **unknown**, with its reason.

  An unfinished search is never read as no.
- **Reuses:**
  - `formal` (16 operations, each Z3 answer re-checked) and `model_checking`;
  - `cegis.Z3Verifier`, whose `Counterexample`, `NoCounterexample` and `Inconclusive` answers already have this shape;
  - `algorithm_design`'s 22 kernels, each with an independent instance checker (PRs #2 and #3, on integrate-0.3);
  - `prolog`, `datalog` and `lean`.
- **New:** the shared contract type, and adapters that map each engine's own answer onto it.

### 3.5 The selector

- **What it does.** For each problem it chooses among plain review, A, C and D, and which engine within D. It works from a
  signature of the problem:
  - Is there an explicit model, and does an exact engine apply?
  - Are candidates available?
  - Is the task determined? This comes from H-decide and from whether the replay-6 readers agree.
  - What has the person asked for: a question budget, which engines are allowed, a time budget?
- **How it decides.** It applies a fixed, documented priority first. It learns only from outcomes an evaluator admitted, and
  it always reports which mechanism ran and why. The person can override it.
- **Its plan is checked before anything runs.** The selector writes its plan as a typed mission graph. CSIR's judgement then
  checks the graph, including that no node checks its own component's output.
- **Reuses:**
  - PR #8's `MethodRouter` and `MetacognitiveMethodSelector`, a contextual bandit that an evaluator must admit;
  - `research/mission_bridge` `MissionIR` and the CSIR judgement (obligations A1–A6);
  - `research/echa` `competence`, a posterior from admissible episodes;
  - CSIR `solvability` as advice that never gates an attempt ("no gain is possible here");
  - `decision_making` and `g6_compose` as the executor.

### 3.6 B: the instrument, on the developer's own repository

- **What it does.**
  1. Finds fixed bugs in a repository's history that have the maintainers' tests.
  2. Runs the developer's agent setup with and without G6 on those bugs.
  3. Compares them by bug, with intervals and the power the sample gives.
  4. For each failure, says where it got through, by plain-code measures:
     - Was there headroom?
     - Did the failure need intent the task did not state?
     - Did the checker see it?
     - Did the repair carry the rule?
  5. Writes "what would be needed" as a governed adaptation proposal.
- **Reuses**, graduating study code from `claude/runtime-k` and `claude/replay6-followup`:
  - `selection.py`, and `run.py`'s validate, run and check steps (these need containers);
  - `compare.py`, the within-run pairs, `headroom.py` and `layers.py`;
  - the power resampling, which is not committed anywhere yet and will be;
  - for the diagnosis, `metaharness`'s `method_rca`, `tick_rca` and `lessons`.
  - AHI's `FailureCase`, `AdaptationProposal`, `Control` and `Funnel`, where effective needs counterfactual evidence and
    adoption is in shadow only.
  - `metaharness/gate.py` and `evaluate.py` as the held-out paired gate for any adaptation. PR #8's own gate is folded into
    them.
- **New:** MCP tools to start, inspect and report a measurement, and a Markdown report a developer can read in five minutes.

### 3.7 Changes to G6 itself

- Any change G6 proposes to its own harness goes through AHI's lifecycle and the held-out gate. It is adopted in shadow at
  most, and only a person merges.
- The self-improvement loop stays as it is, unattended runs stay off, and it is not promoted.

## 4. One coding task, end to end

1. A task arrives, through the plugin's hook or an MCP call. `g6_premortem` gives the intent contract. The agent drafts the
   explicit model.
2. G6 checks the model and finds the open choices (3.2).
3. The selector picks what to do (3.5). If there is an open choice and the person allows a question, it asks one now.
4. The agent writes its patch, or several candidates.
5. The checks run:
   - the confirmed model, as generated cases and replayed traces on the patch (3.2, 3.4);
   - candidate disagreements, as a question or an exact check (3.3);
   - the repository's own tests, at tiers 1 and 2.
6. The report: each requirement with its evidence and its kind, what was not checked, the open choices, and the answered ones
   (3.1).
7. A reproduced finding goes to the repair loop that exists (K mode). The repair is applied automatically when the fix is
   clear, and offered otherwise (section 9). The two harness defects the pilot found are fixed first: a file written inside
   the box never reached the kept patch, and a review called an empty patch clean.

## 5. What is measured before each piece is promoted

The uplift study has stopped. Each piece is measured lean, with the instrument, before the site promotes it:

| Piece | What is measured | Bar | Cost |
|---|---|---|---|
| A, location | H-decide: tagged runs fail more often than untagged ones, on recorded runs | as registered (gap ≥ 20 points, ≥ a third of wrong runs tagged) | none |
| A, value | One question, answered by a simulated maintainer, as an upper bound; then real developers: minutes per question against wrong patches avoided | set before it runs | subscription runs, then people's time |
| C | Candidate disagreements found on recorded bugs, and how often they mark a wrong patch | set before it runs | none to small |
| D, self-improvement | A problem class with exact answers. The system builds its own model and improves against the engine's score. Unseen instances, tuned and held-out results side by side | set before it runs | subscription runs |
| B | G6's own repository and one public repository as the first worked examples | the report reproduces the study's own reading | containers |

The D row is the test of the owner's view that self-improvement can work where the system has an independent source of
truth. It is reported whatever it shows.

## 6. Research code: every module wired in, or retired with a reason

| Code | Where it goes | Flag |
|---|---|---|
| `research/echa` records, claims, competence, trace | 3.1 report, 3.5 selector | removed as wired |
| `research/echa` episodes, custody | 3.6 instrument, as the record of each run | removed as wired |
| `research/ahi` contracts, `Funnel`, lifecycle | 3.6 diagnosis and 3.7 changes to G6 | removed as wired |
| `research/mission_bridge` `MissionIR`, checks, CSIR judgement | 3.5 selector's plan check | removed as wired |
| CSIR `standard` | 3.2 binding a confirmed answer | removed as wired |
| CSIR `certificate` | 3.1, already the product issuer's adapter | removed |
| CSIR `solvability` | 3.5, advisory | removed as wired |
| `research/mission_bridge` fixtures, replay over recorded values | tests only | stays test-only, stated |
| PR #8 `MethodRouter`, selector | 3.5 | not flagged; merges with 3.5 |
| PR #8 assurance dossiers, adaptation gate | folded into 3.1 and the metaharness gate | not kept separately |
| PRs #2 and #3 (`complexity`, `algorithm_design`, `autonomous`) | 3.4 and the D measurement | already on integrate-0.3 |
| Replay 6, H-decide, headroom, layers, compare, power | 3.2, 3.6 | graduate from `validation/` |

## 7. Order of work

Each slice is its own change, with its tests first and a Codex review loop of at most four reviews and three repairs. Findings
from the last review go to their own follow-up change.

1. **The report (3.1).** Everything else reports through it.
2. **A (3.2)** with `g6_confirm_behaviour`, and the two harness defects.
3. **B (3.6)**, with G6's own repository as the first worked example.
4. **The selector (3.5).**
5. **C (3.3).**
6. **D (3.4)**, and the self-improvement measurement of section 5.

The website shows each piece only once its section 5 row has run.

## 8. Left to the builder, and decisions for the owner

**Left to the builder:** module names inside the existing packages; the exact rule-table format; the shape of the shared
three-valued type; and how the question renders in each client's dialog.

**Decided by the owner (2026-11-06):**
1. **The question budget:** one question per task by default. The person can ask for more, or for none.
2. **Containers:** B uses containers on the user's machine, as the study did. Without them, B refuses rather than running
   unconfined.
3. **B's worked example** is G6's own repository, g6solver.
4. **The site's tagline and the owner-held lines** stay as they are.
5. **Repair is automatic when the fix is clear, and every improvement is shown clearly to the person using G6.** This holds for
   the product and for G6's own code (section 9).

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
