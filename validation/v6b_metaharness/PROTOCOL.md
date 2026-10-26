<!-- SPDX-License-Identifier: Apache-2.0 -->
# V6b — the meta-harness's recovery loop on fresh cases: frozen protocol

**Status:** frozen before any arm reads a gate split or an eval split. Its SHA-256, and the SHA-256 of every script
beside it (`workflows.py`, `run.py`, `analyze.py`, `plumb.py`), are recorded in `oss/docs/validation/REGISTRY.md`
(entry "V6b"). `run.py run` refuses to start unless the registry holds the current hashes.

**Governing documents:** `oss/docs/validation/VALIDATION_PLAN.md` §0 and §0.1 (rules for every study), and V6's
protocol (`oss/validation/v6_metaharness/PROTOCOL.md`), which V6b follows except where §10 below says otherwise. If
this protocol conflicts with them in a way §10 does not list, they win.

**What V6b asks:** V6 (`results/V6/README.md`) was `inconclusive`. Its meta-harness approved a change on one workflow
of four (GSM8K). The post-run root cause (`results/V6/post_run/README.md`) found that the other three had failures the
declared options could not address. The library then gained a recovery loop:

- `mh.diagnose()` names the root causes of the dev failures;
- `RecoveryStrategy` turns them into candidates;
- the candidates are labelled examples (`Profile.exemplars`), a second look at an over-used label
  (`steps.second_look`) and repair against the shown tests (`steps.repair_with_tests`);
- the meta-gate judges each candidate, and an approval activates it.

Does this loop raise held-out success over static G6 on fresh cases of the workflows V6 left inconclusive (MASSIVE,
SciFact, MBPP), with no rise in false accepts?

## 1. Population

The same four third-party public datasets as V6. The dataset authors wrote the items and set the answers (validation
rule 9). Item text is not published: rows carry ids, outcomes, counts and digests only (VALIDATION_PLAN §0.1).

| Workflow | Dataset and revision | Licence | Fresh pool | Oracle |
|---|---|---|---|---|
| `gsm8k` | GSM8K `test.jsonl`, `openai/grade-school-math` commit `3101c7d5072418e28b9008a6636bde82a006892c` | MIT | 1,199 | the last number in the reply's final segment equals the number after `####` |
| `massive` | MASSIVE 1.1 `data/en-US.jsonl`, partition `test` (the tarball as in V6, ETag `51e0da2a3ff7a016f109e1d1b4306e93-3`) | CC BY 4.0 | 2,854 | the reply names exactly one of the 60 intent labels, and it is the labelled one |
| `scifact` | SciFact `claims_train.jsonl` and `corpus.jsonl` (release tarball as in V6, ETag `cb7da4d8609e30f2c7483b61aa447f7e`) | claims CC BY 4.0; abstracts ODC-By 1.0 | 689 | the reply names exactly one of SUPPORT / CONTRADICT / NOT_ENOUGH_INFO, and it is the label for the claim and its first cited abstract |
| `mbpp` | MBPP **full** `mbpp.jsonl` (Austin et al. 2021), `google-research/google-research` commit `f46ca8374b4cddef97ca4208ad986049d74d296a`, test task ids 11-510 | CC BY 4.0 (dataset card); repository Apache-2.0 | 380 | the reply's code passes all three of the item's tests, run out of process by `g6.core.sandbox.run_script` (15 s, 512 MB) |

- **Exclusions:**
  - Every id V6 drew is excluded, in any split. That is 120 per workflow, read from V6's `splits.json` (its SHA-256 is
    in the registry).
  - SciFact uses train claims only, as in V6; V5 plans SciFact dev.
  - No item is in V1 (VitaminC), V2 (AgentDojo), V3 or V4.
- **Answer rules** are V6's (`workflows.py`):
  - The final segment of a reply is the text after its last line that starts with `Answer:`, or the whole reply when
    there is none.
  - Labels are matched as whole words, longest match first.
  - The SciFact pair is the claim with its first cited abstract.
- **MBPP:**
  - The task shows the item's text and its first test.
  - The oracle runs all three tests. The item's `test_setup_code` (non-empty for one item in 500) and the reply's code
    form one module, which the tests import whole, so they share one namespace as in MBPP's own evaluation.

## 2. Splits

`run.py draw` (`workflows.draw`) reads ids only:

1. Take each workflow's pool of ids and remove V6's ids.
2. Sort the rest and shuffle it by `random.Random("V6b:20261020:<workflow>")`.
3. Cut it in order into the splits below.

| Split | GSM8K | MASSIVE | SciFact | MBPP | Who reads it |
|---|---|---|---|---|---|
| `dev` | 20 | 20 | 20 | 20 | the proposers (`diagnose`, `RecoveryStrategy`, `SearchStrategy`) and the study's development (§9) |
| `gate` | 70 | 70 | 163 | 70 | the meta-gate of D and F only (`Workflow.suite.held_out`) |
| `eval` | 100 | 100 | 100 | 100 | every arm, once, after every arm is frozen: the study's held-out split |

- **Gate sizes.** They come from `g6.metaharness.recovery.cases_needed` at p ≤ 0.05 / 6 and power 0.8, applied to the
  effects V6's development pilot measured on seen cases:
  - MASSIVE examples, 14 wins and 2 losses in 60: 70 cases;
  - MBPP repair, 9 and 0: 70;
  - SciFact second look, 15 and 6: 163;
  - GSM8K: 70, chosen to match.
- **Pooled eval:** 400 tasks.
- **Record:** `splits.json` in the results folder holds the ids.

## 3. Model and settings

- **Model.** One model for every arm: `llama3.1:8b` on the local Ollama 0.40.2 (digest
  `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`, 8.0B, Q4_K_M).
  - Ollama's default sampling; no seed can be set. `max_tokens` 2048.
  - It is reached at its loopback address `http://127.0.0.1:11434` (§10 item 11).
- **Model calls.** They go through a study subclass of `g6.core.llm.OllamaCaller`. Its `complete` is the library's own,
  with the same endpoint and request body, and it also keeps the reply's `prompt_eval_count` and `eval_count`, so
  compute can be matched by tokens as well as calls.
- **G6 code.** The `oss/src/g6` tree on disk, after commit `1d3106a22e`, which fixed the two defects the study's
  preparation found (§9).
  - It is pinned by the `g6.integrity` manifest SHA-256, recorded in the registry and checked at the start and end of
    the run.
  - The meta-gate's judging code is pinned by `judging_sha256`: at confirmation, before every decision, and at the
    start and end of the run.
- **Executor.** Every arm except A runs through `g6.harness.run` with `g6.metaharness.executor.model_call_factory()` and
  its default system prompt, "Do the task. Reply with the answer only."
- **No proposer uses a model.** `RecoveryStrategy` and `SearchStrategy` are model-free.

## 4. The workflow each arm uses (what judges; frozen)

Each workflow fixes:

- the task template;
- one standard check (mandatory) and one optional check;
- the declared pipeline steps;
- the oracle;
- an answer normaliser, used only for voting;
- `accept_risky = "v6b-scripted-approval (study script, not a person)"`, so the intake does not hold tasks (the V6-F2
  fix). The intake still runs.

| Workflow | Standard check | Optional check | Declared pipeline steps | Declared examples |
|---|---|---|---|---|
| gsm8k | the reply contains a number | the final answer is a whole number | `self_review` | none (the bank is the dev split) |
| massive | the reply names exactly one listed label | the reply is a single label and nothing else | `self_review` | 3,000 rows of the MASSIVE en-US **train** partition (below) |
| scifact | the reply names exactly one of the three labels | the reply is a single label and nothing else | `self_review`, `ground`, `second_look_support`, `second_look_contradict`, `second_look_not_enough_info` | none (dev split) |
| mbpp | the reply holds Python code that parses and defines a function | the code imports without error, out of process | `self_review`, `repair_with_tests` (3 rounds), `repair1` (1 round) | none (dev split) |

**The steps:**

- `self_review` and `ground` are V6's.
- `second_look_<label>` is `g6.metaharness.steps.second_look(label, labels)`. When the answer names that label, it asks
  once more and keeps a revision that names exactly one label.
- `repair_with_tests` and `repair1` are `g6.metaharness.steps.repair_with_tests(rounds=3)` and `(rounds=1,
  name="repair1")`. They run the task's shown test (never a hidden one) out of process, and give the model each failure
  to fix.

**MASSIVE's declared examples:**

- `random.Random("V6b:20261020:massive-examples").sample` takes 3,000 of the 11,514 train rows, sorted by id.
- At run time, any row whose utterance equals a dev, gate or eval utterance (lower case, words only) is dropped, and
  the count is reported.
- Each example has the same input form as a case. Its answer is its intent label.
- A profile with `exemplars = k` sees the k nearest examples before the task (`g6.metaharness.exemplars`).
- For the other three workflows, the bank is the dev split (the library's default).

**The search options** (`Spec.space`), used by `SearchStrategy` in D and by the random draws in F:

- the prompt: the default or one of V6's two declared prompts per workflow;
- the pipeline: none, or one declared step;
- extra checks: none, or the optional check;
- exemplars: 0 or 8.

That gives 24 profiles for gsm8k and massive, 72 for scifact and 48 for mbpp. `SearchStrategy` gets
`max_candidates = 3` and `max_profiles` equal to the whole space, so every declared option is searched.

**Objectives, evidence policy and provenance** are V6's:

- success (max);
- model calls per case bounded at 4 and wall clock per case at 120 s, as one-sided 95% upper bounds;
- `EvidencePolicy(min_cases=30, min_discordant=5, alpha=0.05, max_evaluations=6, call_tolerance=1.0,
  max_eval_model_calls=10000)`. The gate needs a win by the exact sign test at 0.05 / 6, at least 5 discordant cases,
  no more calls than the base unless the candidate also beats the compute-matched control, every bound held, and no
  rise in false accepts;
- provenance is `independent` for both suite splits.

## 5. Arms

All arms use the same model and task text. The *default profile* is `Profile(routing={"answer": "local"})`.

- **A, one direct call:** the model once, with the default system prompt and the task; no harness. Everything it
  returns is released.
- **B, static G6:** the default profile through the harness.
- **B2 (A/A):** B again, an independent run with fresh sampling.
- **D, the meta-harness:** `adapt`, then a scripted `confirm`, then K = 3 cycles. Each cycle:
  1. `mh.diagnose()`: the dev failures' root causes and remedies, recorded.
  2. `mh.propose(RecoveryStrategy(workflow))`, then
     `mh.propose(SearchStrategy(space, max_candidates=3, max_profiles=<space size>))`. Each offers at most one new
     candidate, and each may spend 4,000 model calls on dev.
  3. Every candidate goes through `evaluate` (the gate split) and `decide`.
  4. The scripted approval runs.
  5. The next cycle starts from the head.

  After K cycles the head is frozen. There are at most 6 gate evaluations, the policy's limit.
  - **Scripted approval (it is not a person):**
    - The study script approves a gate `recommend`. When two are recommended in one cycle, it takes the one with more
      net wins on the gate split; on a tie, the first proposed, which is RecoveryStrategy's.
    - It confirms the workflow and activates under the name "v6b-scripted-approval (study script, not a person)".
    - The library records the name as given; it cannot tell a script from a person.
    - Nothing the recovery plans ask of a person (more held-out cases, a fixed check) is done.
- **E, compute-matched self-consistency by calls:** n = ceil(D's mean model calls per eval case), at most 8.
  - B's own executor is sampled n times per case and `g6.reasoning.weighted_vote` picks the answer, comparing answers
    by the workflow's normaliser. A tie or no majority takes the first non-empty sample. The harness checks then run.
  - When n = 1, E is B.
- **E_tok, compute-matched by tokens:** the same vote with n_tok = ceil(D's mean tokens per case / B's mean tokens per
  case), at most 8. Tokens are prompt plus output tokens, as Ollama reports them. When n_tok = 1, E_tok is B; when
  n_tok = n, E_tok is E.
- **F, random search:** a separate meta-harness on the same workflow and gate split.
  - In cycle k it draws, by seed `V6b:20261020:<name>:F`, as many profiles as D evaluated in cycle k. They are drawn
    uniformly from the whole declared space, never the head and never one already drawn.
  - They go through the same `evaluate`, `decide` and scripted approval. F reads no dev case.
- **H, a hand-written prompt:** the default profile with one prompt per workflow (below).
  - The study author wrote these from each task's description, before any V6b split was read. They are not an
    independent person's.
  - The author had read V6's prompts, V6's results and the post-run root causes. So the author knew that asking for
    working helps GSM8K and that the model over-uses SUPPORT on SciFact, and H may carry that knowledge.
  - gsm8k: "Solve the problem step by step, showing each calculation, and check the arithmetic. On the last line write
    only the final number as 'Answer: <number>'."
  - massive: "Work out what the user wants done. Each label is a domain and an action (domain_action): first pick the
    domain the request belongs to, then the action within it. Reply with exactly one label, written as in the list,
    and nothing else."
  - scifact: "Decide from the abstract only. SUPPORT: the abstract's findings show the claim is true. CONTRADICT: its
    findings show the claim is false. NOT_ENOUGH_INFO: the abstract does not test or state the claim. Do not answer
    SUPPORT just because the topic matches. Reply with one label only."
  - mbpp: "Use exactly the function name and arguments the example test uses. Make sure the function returns (not
    prints) the value the test expects, and handle edge cases. Reply with the complete code in one Python code block,
    including any imports."
- **G, every option on:** the default profile with every declared pipeline step in declared order, the optional check
  and 8 exemplars.
- **NULL, the suite's negative control:** `g6.metaharness.executor.null_factory` (an empty answer) through the harness.
- **Learning gradient:** each distinct earlier head of D (after cycle n < K) is also measured on `eval` as `D@n`.
- **Same-profile rule:** an arm whose frozen profile is the default profile is B, and its eval rows are B's rows, not a
  rerun. That covers D or F with no activation, E with n = 1 and E_tok with n_tok = 1. B2 measures what a rerun would
  add.
- **Order and restarts:**
  - Workflows run in the order gsm8k, massive, scifact, mbpp.
  - Within each workflow: the oracle self-test, D's adaptation, F's adaptation, then the eval measurements: NULL, A, B,
    B2, H, D, F, G, D@n, E and E_tok.
  - An adaptation that a crash interrupts restarts from a fresh workspace. The interrupted workspace is kept and the
    restart counted. Measured rows are kept and never measured again.

## 6. Primary endpoints

Per workflow w, on its 100 eval tasks:

1. **Success, D against B,** paired by task. Test: the exact one-sided sign test on the tasks where only D succeeds
   (wins) against the tasks where only B succeeds (losses). The four workflows' p-values are Holm-adjusted together at
   family-wise alpha 0.05. D *beats* B on w when the Holm-adjusted p ≤ 0.05 and wins > losses.
2. **Success loss, D against B:** the opposite one-sided sign test (losses against wins), Holm-adjusted across the four
   workflows as a separate family. D *loses* to B on w when the adjusted p ≤ 0.05 and losses > wins.
3. **False accepts** (released results the oracle marks wrong). This is a hard constraint per workflow, with margin 0:
   D's count must not exceed B's.

The pooled cluster-bootstrap interval of V6 is reported but decides nothing (V6-F3). `analyze.decision_rule` computes
all three endpoints. The verdict calls it, and so does the planted-effect control (§7).

**A recovery win on w:**

- w is one of the targets: massive, scifact or mbpp;
- D beats B on w;
- D's frozen profile on w still holds every field that an activation from a `RecoveryStrategy` proposal set
  (`strategy == "recovery"`).

## 7. Secondary endpoints and controls

**Comparisons** (descriptive, no correction):

- D against E, E_tok, F, H, A and G; H against B; B against A; per workflow and pooled.
- For each of these: success, released, false accepts, blocked, mean calls, tokens, characters and seconds per case,
  and calls and tokens per success.
- The pooled D − B cluster-bootstrap interval: workflows resampled, then tasks; 10,000 resamples, seed 20261020.

**What D did** (descriptive):

- The funnel: diagnoses, offers by strategy, gate evaluations, recommendations and activations by strategy, and the
  refusal codes with their recovery plans. Demotions are 0 by construction (the monitor is not run).
- Which remedies D's frozen profile holds, whichever proposer offered them.
- The gate-versus-eval gap: D's success on the gate split at each activation against its eval success.
- The learning gradient: G_D(n) = eval success of `D@n` minus B's.

**Controls:**

- **A/A:** B2 against B by the same sign tests, and the decision rule applied to B2 as if it were D, with recovery
  origin granted. It must find no gain, no loss and no rise in false accepts. If it fires, that is reported as a failed
  null control.
- **Planted effect, on the actual decision rule:**
  - B2 stands in for D, with planted gains: a seeded share of B2's failing eval tasks turned into successes, and so
    into non-false-accepts.
  - `decision_rule` judges each of 2,000 replicates per scenario, with recovery origin granted.
  - Scenarios:
    - +0.10 and +0.15 on each target;
    - pilot-sized gains (massive +0.20, scifact +0.15, mbpp +0.15), with and without gsm8k +0.30;
    - +0.15 on two targets;
    - +0.15 on one target, which must not meet rule 5.
  - Reported: how often rule 5's count (at least two recovery wins) is met, how often rule 2 would fire, and the gain
    detection rate per workflow.
- **Suite negative control:** NULL's eval success per workflow (it must be 0), and the negative control inside every
  gate evaluation.
- **Oracle self-test:** the oracle on each `gate` and `eval` case's own reference answer must pass, and on an empty
  reply must fail. The reference answers are GSM8K's worked solution, the labelled label, and MBPP's reference code run
  in the sandbox.
- **Gaming checks (rule 8):**
  - Every eval output is scanned for file, process, network and import-machinery access and for the oracle's file
    names. Matches are listed and inspected before the write-up.
  - No arm is given the answers.
  - The pinned hashes are compared at the start and end of the run: the five frozen files, the G6 source manifest, the
    judging code, the data files and the model digest.
- **Leakage canary:**
  - No prompt the model receives while proposing or diagnosing may contain a `gate` input or an `eval` input.
  - No prompt during the gate may contain an `eval` input.
  - No proposer view may contain a gate or eval input.
  - Positive control: every dev input must be found in the views.
  - MASSIVE's example bank must hold no study case's utterance.

## 8. Verdict rule, in order

1. **`contaminated_evaluation`:** an evaluator mutation or a leak.
   - A mutation: a frozen file, the G6 source, the judging code or a data file changed during the run, or the run's
     start and end record is missing.
   - A leak: a canary hit, or an example repeating a study case.
2. **`unsafe_or_regressive`:** on any workflow, D's false accepts exceed B's; or D loses to B (§6.2) on any workflow; or
   a protected item is breached (an activation not by the scripted approver, or by its proposer; a report claim that
   does not hold).
3. **`oracle_inadequate`:** the oracle's self-test rejects more than 5% of a workflow's eval reference answers, or
   accepts an empty reply.
4. **`insufficient_failure_opportunities`:** B fails fewer than 10% of the 400 pooled eval tasks.
5. **`mechanism_demonstrated`:** recovery wins (§6) on at least two of the three targets (massive, scifact, mbpp).
6. **`component_validated_no_transfer`:** the gate recommended, and the script activated, at least one change for D on
   at least one target, and D beats B on none of the targets where it activated a change.
7. **`inconclusive`:** otherwise. Examples: D beats B on one target only; D's gains came from search candidates and
   not from RecoveryStrategy; D never activated anything on a target.

**A property of rule 2, computed before the freeze.** The false-accept clause has margin 0 per workflow.

- If the gate activates a change that has no effect on a workflow, eval noise alone makes D's false accepts exceed B's
  there with probability about 0.45 (`analyze.py power`, 100 eval cases, about 15% discordance).
- An ineffective activation can therefore end in `unsafe_or_regressive` rather than `component_validated_no_transfer`.
- The per-workflow one-sided sign test on false-accept-discordant tasks is reported beside the clause, so a reader can
  see whether a rise is beyond noise. The clause still decides.

No result of this study is `verified`. The meta-harness's own labels are `qualified-draft` at best.

## 9. Development, sample size and the pre-registered expectation

- **Development used the `dev` split only** (`run.py devcheck`): A, B, a rerun of B, and the recovery remedies on the 20
  dev cases per workflow, with the library's diagnosis and RecoveryStrategy's offers on dev (no evaluation).
- **Plumbing.** `plumb.py` tested the runner and the analysis end to end with `g6.core.llm.FakeLLM`, on pool items
  outside every drawn id.
- **Speed probes** used made-up prompts only. No `gate` or `eval` item was read before the registry entry. The registry
  entry lists the development numbers and every change they led to.
- **Found by the study preparation, fixed in the library before the freeze** (commit `1d3106a22e`):
  - **V6b-F1:** `exemplars.Bank` matched the whole task text, so a label list that every task repeats pulled the same
    examples to the top each time. It now matches only the input the template wraps, minus lines every example
    repeats.
  - **V6b-F2:** `SearchStrategy` with a space larger than `max_profiles` took the first `max_profiles` combinations in
    sorted-key order, so the field that sorts first (`exemplars`) never varied. It now samples the whole space by a
    fixed seed. V6b sets `max_profiles` to the space size in any case.
- **Note (V6's runner, not the library):** V6 reached Ollama as `localhost`. On this machine each request then paid
  about 2.1 s of name-resolution fallback: 2.2 s against 0.06 s for a short call at `127.0.0.1`, measured on made-up
  prompts. V6's seconds per case are mostly this overhead. It changed no output.
- **Sample size** is fixed by §2. The registry entry gives the power of the gate and of the decision rule, simulated by
  `analyze.py power` from the pilot effects and from half of them.
- **Expected outcome (pre-registered):** given in the registry entry, written before any `gate` or `eval` item is read.

## 10. Departures from V6's protocol and VALIDATION_PLAN.md §0, with reasons

1. **Fresh cases, new seed.** Every V6 id is excluded and the seed is 20261020, because V6's cases (and the pilot's)
   have been seen.
2. **MBPP full instead of MBPP sanitized (dataset departure).** The sanitized test pool has only 137 ids V6 did not
   draw, too few for 20 + 70 + 100. The full file (same commit, same licence, test ids 11-510) leaves 380.
   - The task shows the item's `text` and first test. Sanitized prompts and test imports do not exist in it.
   - The oracle imports the code together with the item's setup code, as MBPP's own evaluation runs them in one
     namespace.
3. **Split sizes** are set per workflow from the pilot effects (§2): eval 100, and gate 70 to 163, against V6's 60 and
   40.
4. **`accept_risky`** is set on every workflow (the V6-F2 fix), under the scripted approver's name. In V6 the intake held
   15% of SciFact's and 3% of MASSIVE's held-out tasks for every G6 arm.
5. **Per-workflow primary endpoint (the V6-F3 fix).** V6's pooled interval could not exclude zero unless D gained in
   three of four workflows. V6b decides by per-workflow sign tests with Holm across the four, and reports the pooled
   interval descriptively.
6. **Verdict rules.** Rule 2 adds a Holm-significant loss of success. Rule 5 needs two of the three targets, and needs
   the gain to come from a RecoveryStrategy activation, because the loop is what V6b tests. Rule 6 is restricted to the
   targets. Rules 5 and 6 no longer need D > E and D > F: those are secondary, and the gate already requires a
   compute-matched win for a candidate that uses more calls.
7. **Arms.** V6's C (hints only) and `HintStrategy` are dropped; they are not part of the recovery loop. Added: H (a
   hand-written prompt), E_tok (compute matched by tokens) and `mh.diagnose()` in every cycle (about 20 dev runs per
   cycle).
8. **The search covers every declared option:** `max_profiles` is the space size (24 to 72) instead of V6's 32, and the
   dev budget per `propose` or `diagnose` is 4,000 calls instead of 1,500, so the larger search does not exhaust it.
9. **V6's `example_repair` step is not declared for MBPP.** The library's `repair_with_tests(rounds=1)` (`repair1`) does
   the same with the library's own step.
10. **One model, not two (rule 1).** Only `llama3.1:8b` runs. `gpt-oss:20b` is a reasoning model, about ten times
    slower per call on this machine, so it does not fit the run (registry: projection). This is a limitation.
11. **Ollama's address.** V6b reaches the same local Ollama at `127.0.0.1` instead of `localhost` (§9 note), and reads
    the token counts from the same reply (§3).
12. **The person's approval is scripted** (§5, D), as in V6. Person-minutes per activation are not measured.
13. **H is the study author's own,** not an independent person's, and was written with knowledge of V6 (§5).
14. **Compute is matched by calls (E) and by tokens (E_tok).** Wall clock is measured and reported but matches nothing.

## 11. Reproduce

From `oss/`, with `$V6B_DATA` holding the files above (V6's data plus `mbpp/mbpp.jsonl`) and `$V6B_WORK` an empty folder
outside the repository:

    python validation/v6b_metaharness/run.py draw --data $V6B_DATA --out docs/validation/results/V6b
    python validation/v6b_metaharness/run.py run  --data $V6B_DATA --out docs/validation/results/V6b --work $V6B_WORK
    python validation/v6b_metaharness/analyze.py --out docs/validation/results/V6b
