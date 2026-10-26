<!-- SPDX-License-Identifier: Apache-2.0 -->
# V6 — customisation through the meta-harness (H4): frozen protocol

**Status:** frozen before any arm reads a gate split or a held-out split. Its SHA-256 and the SHA-256 of every script
beside it are recorded in `oss/docs/validation/REGISTRY.md` (entry "V6"). `run.py run` refuses to start unless the
registry holds the current hashes.

**Governing documents:** `docs/transition/META_HARNESS_PLAN.md` §7.1-§7.3 (the V6 sketch) and
`oss/docs/validation/VALIDATION_PLAN.md` §0 (rules for every study). Where this protocol departs from §7.1, the
departure is listed in §10 with its reason. If this protocol conflicts with them in any way not listed there, they win.

**What V6 asks (H4):** does tuning G6's profile for a repeated task through `g6.metaharness` (proposals from dev
failures, the meta-gate, a person's approval) raise success on tasks it has never seen, compared with static G6, beyond
what extra compute (E) or plain search through the same gate (F) gives, without a rise in false accepts?

## 1. Population

Four workflows, each from a third-party public dataset with a fixed answer or an executable oracle. The dataset authors
wrote the items and set the answers (validation rule 9). Item text is not published; rows carry ids and outcomes only.

| Workflow | Dataset and revision | Licence | Pool | Oracle |
|---|---|---|---|---|
| `gsm8k` | GSM8K `test.jsonl` (Cobbe et al. 2021), `openai/grade-school-math` at commit `3101c7d5072418e28b9008a6636bde82a006892c` | MIT | 1,319 problems | the last number in the reply's final segment equals the number after `####` |
| `massive` | MASSIVE 1.1 (FitzGerald et al. 2022), `amazon-massive-dataset-1.1.tar.gz` (S3, ETag `51e0da2a3ff7a016f109e1d1b4306e93-3`), `data/en-US.jsonl`, partition `test` | CC BY 4.0 (the tarball's `LICENSE`; derived from SLURP, CC BY 4.0) | 2,974 utterances | the reply names exactly one of the 60 intent labels, and it is the labelled one |
| `scifact` | SciFact `claims_train.jsonl` and `corpus.jsonl` (Wadden et al. 2020), release tarball `data.tar.gz` (S3 `release/latest`, ETag `cb7da4d8609e30f2c7483b61aa447f7e`); licence file from `allenai/scifact` at commit `68b98a56d93e0f9da0d2aab4e6c3294699a0f72e` | claims CC BY 4.0; abstracts ODC-By 1.0 (repository `LICENSE.md`; the Hugging Face card says CC BY-NC 2.0) | 809 train claims | the reply names exactly one of SUPPORT / CONTRADICT / NOT_ENOUGH_INFO, and it is the label for the claim and its first cited abstract |
| `mbpp` | MBPP sanitized `sanitized-mbpp.json` (Austin et al. 2021), `google-research/google-research` at commit `f46ca8374b4cddef97ca4208ad986049d74d296a`, test task ids 11-510 | CC BY 4.0 (dataset card); repository Apache-2.0 | 257 problems | the reply's code passes every one of the item's tests, run out of process by `g6.core.sandbox.run_script` (15 s, 512 MB) |

- **Exclusions (V1-V5):** V1 used VitaminC and V2 AgentDojo; V3 and V4 use their own task sets and TimeMachine-bench.
  V5 plans SciFact **dev**, so V6 uses SciFact **train** only. No V6 item is in any of them.
- **Answer rules** (`workflows.py`): the *final segment* of a reply is the text after its last line starting
  `Answer:`, or the whole reply. Labels are matched as whole words, longest match first (a label inside a longer
  label's match does not count). The SciFact pair is the claim with its first cited abstract; its label is that
  abstract's rationale label, or NOT_ENOUGH_INFO when the abstract has no rationale. MBPP shows the item's first test
  (and its imports) in the task; the oracle runs all of the item's tests.

## 2. Splits

`run.py draw` (`workflows.draw`): each workflow's pool of ids, sorted, is shuffled by `random.Random("V6:20261019:<name>")`
and cut in order into:

| Split | Size per workflow | Who reads it |
|---|---|---|
| `dev` | 20 | the proposers (D, C) and their dev runs; the study's own development (§9) |
| `gate` | 40 | the meta-gate of D and F only (`Workflow.suite.held_out`) |
| `eval` | 60 | every arm, once, after every arm is frozen: the study's held-out split |

The draw reads ids only. `splits.json` (in the results folder) records the ids. In §7.1's terms, `dev` and `gate`
together are the study's development side ("gain on dev"), and `eval` is its held-out side. Pooled held-out: 240 tasks.

## 3. Model and settings

- One model for every arm: `llama3.1:8b` (Ollama 0.40.2, digest
  `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`, 8.0B, Q4_K_M), through `g6.core.llm.OllamaCaller`
  at the local Ollama, Ollama's default sampling (no seed is settable through `OllamaCaller`), `max_tokens` 2048.
- No proposer uses a model: `SearchStrategy` and `HintStrategy` are model-free, so no arm gains model capacity.
- G6 code: the `oss/src/g6` tree as on disk, pinned by `g6.integrity` manifest SHA-256 (recorded in the registry and
  checked at the start and end of the run); the meta-gate's judging code is pinned again by `judging_sha256` at
  confirmation and compared before every decision.
- Every arm runs through `g6.harness.run` (except A), with `g6.metaharness.executor.model_call_factory()` and its
  default system prompt "Do the task. Reply with the answer only."

## 4. The workflow each arm tunes (what judges; frozen)

For each dataset, `workflows.SPECS` fixes: the task template; one standard check (mandatory); one optional check a
profile may add; one or two optional pipeline steps a profile may add; the oracle; an answer normaliser used only for
voting (it never reads an answer).

| Workflow | Standard check | Optional check | Optional pipeline steps |
|---|---|---|---|
| gsm8k | the reply contains a number | the final answer is a whole number | `self_review` |
| massive | the reply names exactly one listed label | the reply is a single label and nothing else | `self_review` |
| scifact | the reply names exactly one of the three labels | the reply is a single label and nothing else | `self_review`, `ground` |
| mbpp | the reply holds Python code that parses and defines a function | the code imports without error, out of process | `self_review`, `example_repair` |

- `self_review` (component `reasoning`, op `revise`): one more call of the same model, shown the task and the draft,
  asked to return it unchanged or corrected.
- `ground` (`grounding.check_claim`): a SUPPORT label that `g6.grounding.check_claim` does not ground in the abstract
  becomes NOT_ENOUGH_INFO.
- `example_repair` (component `reasoning`, op `repair_from_example`): runs the one example test the task shows (never a
  hidden test) out of process; on a failure, one more call asks for a fix.
- **Objectives:** success (max); model calls per case bounded at 4 and wall clock per case bounded at 120 s, as one-sided
  95% upper bounds (`Objective(direction="bound")`). See §10 item 4.
- **Evidence policy:** `EvidencePolicy(min_cases=30, min_discordant=5, alpha=0.05, max_evaluations=6, call_tolerance=1.0)`:
  the gate needs a win by the exact sign test at 0.05 / 6 on the 40 gate cases, at least 5 discordant cases, no more
  calls than the base unless the candidate also beats the compute-matched control, no objective over its bound, and no
  rise in false accepts.
- **Provenance:** `independent` for both suite splits (the dataset authors wrote them).

## 5. Arms

All arms use the same model, the same task text and the same harness checks. *Default profile* = `Profile(routing={"answer":
"local"})`.

- **A, one direct call:** the model once with the default system prompt and the task; no harness. Everything returned is
  released.
- **B, static G6:** the default profile through the harness.
- **B2 (A/A):** B again, an independent run (fresh sampling).
- **C, memory only:** every hint `HintStrategy` proposes from B's failures on `dev` (one `propose`, its candidate
  theories), added to the default profile with no gate and no other change.
- **D, the meta-harness:** `adapt` → scripted `confirm` → K = 3 cycles. Each cycle: `propose(SearchStrategy(space,
  max_candidates=3, max_profiles=32))` and `propose(HintStrategy(store))`, each offering at most one new candidate (the
  best-ranked one not proposed before; dev budget 1,500 calls per `propose`); every candidate through `evaluate` (the
  gate split) and `decide`; the scripted approval; then the next cycle starts from the head. After K cycles the head is
  frozen. At most 6 gate evaluations, the policy's limit.
  - The search options (`Spec.space`): the prompt (the default, or one of two declared prompts per workflow) x the
    pipeline (none, or one optional step) x the extra checks (none, or the optional check): 11 profiles for gsm8k and
    massive, 17 for scifact and mbpp.
  - **Scripted approval (it is not a person):** the study script approves every gate `recommend`; when two are
    recommended in one cycle, the one with more net wins on the gate split (ties: the first proposed). It confirms the
    workflow and activates under the name "v6-scripted-approval (study script, not a person)". The library records the
    name as given; it cannot tell a script from a person.
- **E, compute-matched self-consistency:** n = ceil(D's mean model calls per held-out case). B's own executor is
  sampled n times per case and `g6.reasoning.weighted_vote` picks the answer, answers compared by the workflow's
  normaliser (a tie or no majority: the first non-empty sample), then the harness checks run. When n = 1, E is B.
- **F, random search:** a separate meta-harness on the same workflow and gate split. In cycle k it draws, by seed
  `V6:20261019:<name>:F`, as many profiles as D evaluated in cycle k, uniformly from the same declared options (never
  the head, never one already drawn), through the same `evaluate`, `decide` and scripted approval. It reads no dev case.
- **G, every optional step on:** the default profile with every optional pipeline step and every optional check.
- **NULL, the suite's negative control:** `g6.metaharness.executor.null_factory` (an empty answer) through the harness.
- **Same-profile rule:** an arm whose frozen profile is the default profile (D or F with no activation, C with no hint,
  E with n = 1) *is* B; its held-out rows are B's rows, not a rerun. B2 measures what a rerun would add.
- **Learning gradient:** each distinct earlier head of D (after cycle n < K) is also measured on `eval`.
- **Order and restarts:** per workflow, D, then F, then C adapt (each on its own workspace), and only then is every arm
  measured on `eval`. An adaptation a crash interrupts restarts from a fresh workspace (the interrupted one is kept and
  the restart counted, since its gate reads already happened); measured rows are kept and never re-measured.

## 6. Primary endpoints

1. **E1, held-out success, D against B,** paired by task, pooled over the 240 tasks. Point estimate: the mean paired
   difference. Interval: a two-stage cluster bootstrap 95% percentile interval (workflows resampled, then tasks within
   each; 10,000 resamples, seed 20261019). Test: the exact one-sided sign test on per-task wins and losses.
2. **E2, false accepts** (released results the oracle marks wrong), D against B, pooled. Hard constraint, margin 0:
   D's count must not exceed B's (the gate's own rule). Its test, entered into the Holm family: the exact one-sided sign
   test that D has fewer false accepts (tasks where only B false-accepts against tasks where only D does). The opposite
   one-sided p (a rise) is reported.

**Holm:** across the four tests the verdict reads: E1 (D > B), E2 (D fewer false accepts), D > E and D > F (each the same
paired sign test on success as E1). Family-wise alpha 0.05.

## 7. Secondary endpoints and controls

- D against C, E, F, G and A; B against A (the same paired test and interval; descriptive, no correction).
- Per workflow: success, released, false accepts, blocked, mean calls and seconds, calls and seconds per success.
- The funnel: proposals, gate evaluations, recommendations, activations (and refusal codes), demotions (the monitor is not
  run: 0 by construction). Person-minutes per activation: not measured, because every approval is scripted.
- The dev / held-out gap: D's gate-split success at each activation against its held-out success.
- The learning gradient G_D(n) = held-out success of D's head after cycle n minus B's, n = 1..3.
- **A/A:** B2 against B by E1's test and interval, and E2's counts. A significant A/A result is reported as a failed
  null control.
- **Planted improvement (survival control):** B2 with 10% of the pooled tasks (24) turned from failure to success, chosen
  by seed among B2's failures, compared with B by E1's test; 2,000 seeded replicates. Reported: the share detected at
  the strictest Holm level (0.05 / 4) and at 0.05. This shows whether the study can detect the target effect (+0.10).
- **Suite negative control:** NULL's held-out success per workflow (it must be 0), and the negative control inside
  every gate evaluation.
- **Oracle self-test:** the oracle on each `gate` and `eval` case's own reference answer (GSM8K's worked solution;
  the labelled label; MBPP's reference code in the sandbox) must pass, and on an empty reply must fail.
- **Gaming checks (rule 8):** every program generated on the held-out split is scanned for file, process, network and
  import-machinery access and for the oracle's file names; matches are listed and inspected before the results are
  written up. (Programs generated during adaptation also run only in the sandbox and are not scanned.) The frozen files,
  the G6 source manifest, the data files and the model digest are hashed at the start and end of the run. No arm is
  given the answers: the oracle runs only in the evaluator, and the proposers see dev answers only.
- **Leakage canary:** every prompt the model receives during adaptation is checked for any `gate` input (while
  proposing) and any `eval` input (before measurement); every proposer view D's strategies receive is checked for any
  `gate` or `eval` input.

## 8. Verdict rule, in order

1. Evaluator mutation (a frozen file, the G6 source, a data file or the judging digest changed during the run) or leakage
   (a canary hit) → `contaminated_evaluation`.
2. E2's hard constraint broken (pooled false accepts D > B), or a protected item breached (an activation not by the
   scripted approver, or by its proposer; a hash-chain or report claim that does not hold) → `unsafe_or_regressive`.
3. The oracle's error cannot be bounded: its self-test rejects more than 5% of a workflow's held-out reference answers,
   or accepts an empty reply → `oracle_inadequate`.
4. B makes too few errors to measure: B fails fewer than 10% of the 240 pooled held-out tasks →
   `insufficient_failure_opportunities`.
5. D > B, D > E and D > F, each with more wins than losses and a Holm-adjusted p ≤ 0.05, and the lower bound of E1's
   interval above the pooled known oracle-error bound → `mechanism_demonstrated`. The bound is the task-weighted sum, per
   workflow, of the self-test's reference-failure share plus the published answer-key error where one is published
   (GSM8K: "less than 2 percent of problems contain breaking errors"; none is published for the other three).
6. A gain on dev but none on held-out: the gate recommended and the script activated at least one change for D in at
   least one workflow (each activation is a gate-certified gain on the development side), and E1 shows no held-out gain
   (Holm-adjusted p > 0.05, or wins not above losses) → `component_validated_no_transfer`.
7. Otherwise (for example, D beats B on held-out but not E or F, or D never activated anything) → `inconclusive`.

No result of this study is `verified`. The meta-harness's own labels are `qualified-draft` at best.

## 9. Development, sample size and the pre-registered expectation

- Development used the `dev` split only (`run.py devcheck`): A, B, a rerun of B, the two declared prompts and G on the
  20 dev cases per workflow. Its numbers are in the registry entry. The runner's plumbing was tested end to end with
  `g6.core.llm.FakeLLM` on pool items outside the 120 drawn ids per workflow (items no split contains). No `gate` or
  `eval` item was read before the entry.
- **Sample size:** fixed by compute on this machine at 20 / 40 / 60 per workflow (about 2,500 model calls per
  workflow). The power calculation run on dev (registry entry) gives the chance that these sizes detect a given gain.
- **Expected outcome (pre-registered):** given in the registry entry, written before any `gate` or `eval` item is read.

## 10. Departures from META_HARNESS_PLAN.md §7.1 and VALIDATION_PLAN.md §0, with reasons

1. **Three splits, not two.** The meta-gate needs its own held-out split of at least 30 cases, and that split cannot be
   the study's held-out split, or D would be selected on the cases it is scored on. So the study's development side is
   `dev` (proposers) plus `gate` (the meta-gate), and `eval` is held out from both.
2. **One model, not two (rule 1).** Only `llama3.1:8b` runs, because every arm costs about 2,500 calls per workflow on
   this machine. No 20B arm is part of this registration.
3. **Split sizes** are set by compute (§9); the dev power calculation reports what they can detect rather than choosing
   them. The gate split (40) is above the gate's floor of 30.
4. **Objectives.** The library's default objectives (owner ruling Q9) refuse any candidate whose calls or wall clock rise
   by more than 5%; every declared prompt that asks for working, and every pipeline step, would be refused on cost
   alone, and §7.1's arm E presupposes a D that may spend more calls. The study therefore bounds calls (4 per case) and
   wall clock (120 s per case) instead, and arm E answers the compute question.
5. **Label lists live in each case's input.** `Workflow.answer_leaks` refuses a task template or check predicate that
   contains a held-out answer, and a closed label set names every answer. The guard works as written; the label list is
   moved into the input, and no predicate names a label.
6. **E** votes over B's own prompt with `g6.reasoning.weighted_vote` (the vote `self_consistency` uses) rather than
   calling `self_consistency`, whose `DirectStep` prompt would change the prompt as well as the compute.
7. **Holm** covers four tests (E1, E2, D > E, D > F) so that rule 5's "after Holm" holds for all three comparisons.
8. **E2's operational form** (the count constraint and the sign test) is defined in §6, as §7.1 does not fix one.
9. **The A/A arm** is a rerun with fresh sampling: `OllamaCaller` cannot set a seed.
10. **A known library defect, stated before the run:** `HintStrategy` raises `G6_E_LEARNING_ID_EXISTS` from its second
    `propose` on the same theory store (`T3Strategy` re-proposes stored candidates and `TheoryStore.add_candidates`
    refuses the batch), so D's hint proposer can offer a candidate in cycle 1 only. D runs the library as it is; the
    refusal is recorded in each later cycle. This is finding V6-F1.
11. **The person's approval is scripted** (§5, D). Person-minutes per activation are therefore not measured.
12. **Banking77 was replaced by MASSIVE before freezing**, on dev evidence. On the 20 Banking77 dev cases every G6 run
    (B, its rerun, both prompts, G) was blocked at intake with `G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED`,
    with no model call, while A answered 11 of 20. The intake's word list makes any goal that names "payment" risky, and
    the Banking77 label list names it ("card_payment_fee_charged" and others). The person's `accept_risky` decision is
    the only way past it, and no workflow or profile field carries it to `g6.harness.run`, so no arm but A could release
    anything and the workflow could not measure customisation. This is finding V6-F2. MASSIVE (intent classification
    too) has no tripwire word in its label set; an utterance that names one (for example "delete") is still blocked, as
    G6 would block it for a user, and that counts against every G6 arm alike. SciFact keeps its intake blocks for the
    same reason.

## 11. Reproduce

From `oss/`, with `$V6_DATA` holding the files above and `$V6_WORK` an empty folder outside the repository:

    python validation/v6_metaharness/run.py draw --data $V6_DATA --out docs/validation/results/V6
    python validation/v6_metaharness/run.py run  --data $V6_DATA --out docs/validation/results/V6 --work $V6_WORK
    python validation/v6_metaharness/analyze.py --out docs/validation/results/V6
