<!-- SPDX-License-Identifier: Apache-2.0 -->
# V6b — the meta-harness's recovery loop on fresh cases: results (2026-10-19)

**How it ran:**

- Registered in `../../REGISTRY.md` (entry "V6b") before any arm read a gate split or an eval split.
- The frozen scripts (`oss/validation/v6b_metaharness/`) ran once, unchanged, from 11:50 to 14:04 on 2026-10-19: one
  start, no crash, no restart.
- 25,807 model calls to `llama3.1:8b` on the local Ollama, about 12.1 million prompt and output tokens, and no other
  network call.
- Every hash the runner pins was the same at the end as at the start: the five frozen files, the G6 source manifest,
  the judging code, the data files and the model digest.

## Verdict: `contaminated_evaluation`, from a canary false positive

The verdict follows the registered rules in order. **Rule 1 fired**, because the proposer-view canary reported one eval
snippet inside MASSIVE's proposer views. It is not a leak:

- The matched eval utterance is a single five-letter word. The same word is part of the 60-label list that every
  MASSIVE input carries, and it also occurs inside a dev utterance and a dev label.
- The canary tests raw substring containment, so it finds that word in every MASSIVE view.
- The views hold dev material only, by construction (`g6.metaharness.propose.build_view` reads `Workflow.suite.dev`,
  and the eval split is not in the workflow at all).
- The prompt canary, which matches a whole input field, found 0 hits in the 22,404 model calls made while diagnosing,
  proposing and gating.

This is finding V6b-F3: a defect in this study's canary, found after the run. The verdict stands as registered.

**What the other rules give,** applied as registered with that one false positive set aside. This is descriptive only and
not the verdict:

- Rule 2: no false-accept rise, no Holm-significant loss, nothing protected breached.
- Rule 3: the oracle passed its self-test.
- Rule 4: B failed 218 of 400 tasks.
- Rule 5 is not met: there are **no recovery wins**. D beat B on SciFact, but with a SearchStrategy candidate. On GSM8K
  the gain did come from RecoveryStrategy, but GSM8K is not a target.
- Rule 6 is not met: D's one activation on a target (SciFact) did beat B.
- So rule 7 gives `inconclusive`, which is the outcome the registration expected.

In short: on fresh cases, RecoveryStrategy's candidates for the three workflows V6 left inconclusive were each offered,
and each was refused by the gate.

**The person's approval was scripted.** Every confirmation, risky-task acceptance and activation was made by the study
script, under the name "v6b-scripted-approval (study script, not a person)". No person approved anything. Nothing here
is `verified`.

## What ran

- **Workflows** (PROTOCOL §1):
  - GSM8K test (MIT);
  - MASSIVE 1.1 en-US test (CC BY 4.0);
  - SciFact train claims (claims CC BY 4.0, abstracts ODC-By 1.0);
  - MBPP **full**, test task ids 11-510 (CC BY 4.0).

  Every id V6 drew was excluded. Per workflow: 20 dev cases, gate 70 (SciFact 163), and 100 eval cases, drawn by seed
  20261020 from ids only. Pooled eval: 400 tasks.
- **Declared remedies:**
  - MASSIVE: 3,000 labelled examples from its train partition; none repeated a study utterance, so 0 were removed.
  - SciFact: `steps.second_look` for each label.
  - MBPP: `steps.repair_with_tests` with 3 rounds and with 1 round.
  - Every search space includes `exemplars` 0 or 8.
- **Arms** (PROTOCOL §5):
  - A: one direct call;
  - B: the default profile;
  - B2: a rerun of B;
  - D: 3 cycles of `mh.diagnose()`, then `RecoveryStrategy` and `SearchStrategy` (the whole declared space), the gate
    (`evaluate`/`decide`) and the scripted approval;
  - E and E_tok: self-consistency matched by calls and by tokens;
  - F: random draws from the same space, as many per cycle as D evaluated, through the same gate;
  - H: a hand-written prompt;
  - G: every option on;
  - NULL: an empty answer.
- **Same-profile rule:** where D, F, E or E_tok ended on the default profile, their eval rows are B's rows, as
  pre-registered. D kept one call per case everywhere, so E is B on all four workflows.

## Primary endpoints (per workflow, 100 eval tasks each)

| Workflow | B | D | Wins | Losses | Holm p (gain) | Holm p (loss) | False accepts B / D | D's frozen change, and its proposer |
|---|---|---|---|---|---|---|---|---|
| GSM8K | 20 | **81** | 61 | 0 | < 1e-6 | 1.0 | 80 / 19 | the working prompt; **RecoveryStrategy** |
| MASSIVE | 60 | 60 (= B) | 0 | 0 | 1.0 | 1.0 | 38 / 38 | none |
| SciFact | 50 | **73** | 27 | 4 | 5.1e-5 | 1.0 | 50 / 27 | V6's "use only the abstract" prompt; **SearchStrategy** |
| MBPP | 52 | 52 (= B) | 0 | 0 | 1.0 | 1.0 | 46 / 46 | none |

- D beat B, Holm-significantly, on GSM8K and SciFact. Neither counts as a recovery win: GSM8K is not a target, and
  SciFact's change came from the search proposer.
- No workflow shows a loss, and false accepts never rose.
- The pooled D − B difference is +0.21 (88 wins, 4 losses; cluster bootstrap 95% [0.000, 0.475]). It is descriptive
  only. As in V6 (V6-F3), its lower bound is 0, because two of the four workflows did not change.

## Per workflow, every arm (successes of 100 eval tasks)

"(= B)" marks an arm whose frozen profile was the default profile.

| Arm | GSM8K | MASSIVE | SciFact | MBPP | Pooled (400) |
|---|---|---|---|---|---|
| NULL (empty answer) | 0 | 0 | 0 | 0 | 0 |
| A, one direct call | 22 | 65 | 52 | 54 | 193 |
| B, static G6 | 20 | 60 | 50 | 52 | 182 |
| B2, rerun of B | 22 | 63 | 53 | 49 | 187 |
| H, hand-written prompt | 78 | 63 | 67 | 49 | 257 |
| **D, meta-harness** | **81** | 60 (= B) | **73** | 52 (= B) | **266** |
| E, self-consistency at D's calls | 20 (= B) | 60 (= B) | 50 (= B) | 52 (= B) | 182 |
| E_tok, self-consistency at D's tokens | 24 (3 votes) | 60 (= B) | 49 (2 votes) | 52 (= B) | 185 |
| F, random search | 20 (= B) | **77** | 50 (= B) | 52 (= B) | 199 |
| G, every option on | 44 | 73 | 66 | 63 | 246 |

Cost per case, from Ollama's reported counts:

| Workflow | B: calls / tokens | D: calls / tokens | E_tok: calls / tokens | G: calls / tokens |
|---|---|---|---|---|
| GSM8K | 1.0 / 115 | 1.0 / 279 | 3.0 / 355 | 2.0 / 885 |
| MASSIVE | 1.0 / 297 | = B | = B | 2.0 / 810 |
| SciFact | 1.0 / 426 | 1.0 / 460 | 2.0 / 852 | 3.15 / 2,303 |
| MBPP | 1.0 / 138 | = B | = B | 2.88 / 1,849 |

Each table above counts tokens as the prompt tokens Ollama evaluated plus the output tokens. The per-arm tables for
false accepts, blocked runs, characters and seconds are in `analysis.json` (`arms`); per-case rows are in
`v6b_rows.csv` and `rows/`.

## What the meta-harness did

`mh.diagnose()` ran at the start of every cycle, on dev, and named:

- wrong numbers on GSM8K;
- wrong labels on MASSIVE (and, in cycle 3, an over-used `datetime_query`);
- wrong labels on SciFact, with SUPPORT over-used (5 of 7 wrong answers in cycle 1);
- wrong code on MBPP.

RecoveryStrategy offered the matching remedy in cycle 1 every time. The gate's threshold is p ≤ 0.05 / 6 = 0.0083.

| Workflow | Cycle | RecoveryStrategy's candidate on the gate | SearchStrategy's candidate on the gate | Activation |
|---|---|---|---|---|
| GSM8K | 1 | working prompt: 55 vs 15 of 70, 43 wins, 3 losses: **recommend** | V6's working prompt + whole-number check + `self_review` + 8 examples: 52 vs 16, 40-4: recommend | **RecoveryStrategy's** (more net wins) |
| | 2-3 | no new candidate | V6's working prompt variants: 5-7 and 4-6, `NO_GAIN` | — |
| MASSIVE | 1 | 8 examples: 57 vs 46 of 70, **15 wins, 4 losses, p 0.0096**: `NO_GAIN` (recovery plan: "about 116 [gate] cases would confirm a gain of this size... do not read this result as 'no effect'") | V6's label-only prompt + check: 2-4, `NO_GAIN` | none |
| | 2-3 | no new candidate | V6's prompt: 1-2 `INSUFFICIENT_EVIDENCE`; then the "Think briefly" prompt + label-only check + `self_review` + 8 examples: 11-2, p 0.011, `NO_GAIN` | none |
| SciFact | 1 | `second_look_support`: 111 vs 99 of 163, **29 wins, 17 losses, p 0.052**: `NO_GAIN` (plan: about 546 cases) | V6's "use only the abstract" prompt: 124 vs 103, 32-11, p 0.00096: **recommend** | **SearchStrategy's** |
| | 2 | 8 examples (on the new head): 13-16, `NO_GAIN` | + `second_look_contradict`: 2-6, `NO_GAIN` | — |
| | 3 | no new candidate | + label-only check: 6-6, `NO_GAIN` | — |
| MBPP | 1 | `repair_with_tests`: 40 vs 34 of 70, 11-5, p 0.105: `NO_GAIN` | `repair1` + imports check: 8-3, p 0.113, `NO_GAIN` | none |
| | 2 | `repair1`: 5-5, `NO_GAIN` | imports check: 5-8, `NO_GAIN` | none |
| | 3 | no new candidate | "one code block" prompt + `repair_with_tests`: 11-3, p 0.029, `NO_GAIN` | none |

- **The funnel:**
  - 18 gate evaluations for D, of which 6 were RecoveryStrategy's and 12 SearchStrategy's.
  - 3 recommendations: 1 from RecoveryStrategy (GSM8K) and 2 from SearchStrategy (GSM8K, SciFact).
  - 2 activations, both in cycle 1: GSM8K (RecoveryStrategy's) and SciFact (SearchStrategy's).
  - Every refusal carried a recovery plan, read from the gate's aggregates only. Each underpowered win came with the
    gate size that would confirm it, and with "do not read this result as 'no effect'".
  - Demotions: 0 (the monitor is not run).
- **F (random search)** made one activation. On MASSIVE its seeded draw was exactly RecoveryStrategy's candidate (8
  examples, nothing else).
  - On the same 70 gate cases, F's evaluation gave 58 vs 45, 15 wins and 2 losses, p 0.0012: recommend. D's had given
    15-4, p 0.0096: refused.
  - On eval, F reached 77 against B's 60 (22 wins, 5 losses). The remedy RecoveryStrategy proposed did transfer on
    MASSIVE. D's own evaluation of it fell on the wrong side of the threshold, by sampling noise (finding V6b-F4).
- **Gate against eval:**
  - GSM8K: the activated profile gained +0.57 on the gate split (15 → 55 of 70) and +0.61 on eval (20 → 81 of 100).
  - SciFact: +0.13 on the gate (103 → 124 of 163) and +0.23 on eval (50 → 73).
- **Learning gradient:** each head moved once, in cycle 1. Cycles 2 and 3 added nothing on any workflow.

## Controls

- **A/A (B2 against B):** 31 wins and 26 losses pooled (p 0.30; per workflow p 0.25-0.85). The decision rule applied to
  B2 found no gain and no loss.
  - Its false-accept clause did fire on MBPP: B2 had 50 false accepts against B's 46. This is the property computed and
    registered before the freeze (PROTOCOL §8): the margin-0 clause fires by noise for a change with no effect.
  - It did not decide anything here, because D's MBPP rows are B's.
- **Planted effect, on the decision rule itself** (B2 stands in for D; 2,000 replicates per scenario):

  | Planted scenario | Rule 5's count (two recovery wins) met |
  |---|---|
  | +0.10 on each target | 1.00 |
  | +0.15 on each target | 1.00 |
  | pilot-sized | 1.00 |
  | +0.15 on two targets | 0.82 |
  | +0.15 on one target | 0.00 (correct: it needs two) |

  Rule 2 never fired in a planted scenario. The rule detects the effects this study was sized for.
- **Suite negative control:** NULL succeeded on 0 of 100 eval tasks in every workflow. In all 36 gate evaluations (D and
  F), the base beat the empty answer (p ≤ 6e-5).
- **Oracle self-test:** the oracle accepted all 773 reference answers (373 gate, 400 eval) and rejected the empty reply
  on every case.
- **Leakage canary:**
  - Prompt canary: no prompt during adaptation contained a gate input (while diagnosing or proposing) or an eval input:
    0 hits.
  - Proposer-view canary: 0 gate snippets in any view; 0 eval snippets on GSM8K, SciFact and MBPP; 1 on MASSIVE (the
    false positive above).
  - Positive control: all 20 dev inputs were found in the views, on every workflow.
  - MASSIVE's example bank held no study utterance.
- **Gaming checks (rule 8):** 2,500 eval outputs were scanned (500 of them MBPP programs). No file, process, network or
  import-machinery access, and no oracle file name. The proposers saw dev answers only.
- **Pinned hashes:** all the same at start and end (above). The run had one start, with its end record.

## Secondary comparisons (pooled, descriptive, no correction)

| Comparison | Wins | Losses | Mean difference | p one-sided | Per workflow |
|---|---|---|---|---|---|
| D against H (hand-written prompt) | 36 | 27 | +0.023 | 0.16 | GSM8K 81 vs 78, MASSIVE 60 vs 63, SciFact 73 vs 67, MBPP 52 vs 49 |
| D against F (random search) | 93 | 26 | +0.168 | < 1e-6 | F won MASSIVE (77 vs 60); D won GSM8K and SciFact |
| D against E_tok (tokens matched) | 87 | 6 | +0.203 | < 1e-6 | GSM8K 81 vs 24, SciFact 73 vs 49 |
| D against G (every option on) | 59 | 39 | +0.050 | 0.027 | G won MASSIVE (73) and MBPP (63) |
| D against A (one direct call) | 100 | 27 | +0.183 | < 1e-6 | |
| H against B | 105 | 30 | +0.188 | < 1e-6 | GSM8K 78, SciFact 67 |
| B against A | 24 | 35 | −0.028 | 0.94 | static G6 did not beat a direct call (182 vs 193) |

- **A hand-written prompt did nearly as well as the meta-harness** (257 against 266). Both of D's activations were
  prompts of the kind a person writes: the working prompt, and "use only the abstract". The study author wrote H knowing
  V6's results (PROTOCOL §5), so this is an upper bound for an unbriefed person.
- **Compute.** The gains were not extra calls: D used 1.0 call per case. They were not extra tokens either: E_tok, given
  D's tokens (3 votes on GSM8K, 2 on SciFact), stayed at B's level.
- **Every option on (G)** beat B on MASSIVE (73) and MBPP (63, 12 wins and 1 loss), but at 2-3 calls and 2.7 (MASSIVE)
  to 13 (MBPP) times the tokens per case. The gate never saw G, because the search space holds one pipeline step at a time.

## Falsifiers

| Falsifier | Outcome |
|---|---|
| D does not beat B on the targets | met on MASSIVE and MBPP (D = B); not met on SciFact (73 vs 50), but with a search candidate |
| The gain is not from the recovery loop | met on every target. RecoveryStrategy's candidates were refused on all three; its one activation was on GSM8K |
| D does not beat E (the gain is compute) | not met: D used 1 call per case, and beat the tokens-matched E_tok too |
| D does not beat F | mixed: F beat D on MASSIVE with RecoveryStrategy's own candidate |
| False accepts rise | not met: 130 vs 214 pooled, never higher on a workflow |
| Any arm can mutate its evaluator | not found: every pinned hash unchanged, 0 gaming matches |

## Findings

- **V6b-F1 (library; found by the study's preparation, fixed before the freeze in `1d3106a22e`):**
  - The defect: `exemplars.Bank` matched the whole task text, so MASSIVE's label list pulled the same examples to the
    top for every task.
  - Development check on 300 train-partition queries: the share of retrieved examples carrying the query's intent was
    0.115 with the old match and 0.475 with the fix.
- **V6b-F2 (library; fixed before the freeze):** `SearchStrategy` truncated a large space in sorted-key order, so
  `exemplars` never varied. V6b set `max_profiles` to the space size in any case.
- **V6b-F3 (this study's canary; found after the run):**
  - The defect: the proposer-view canary (`run.py` `in_view`) tests substring containment, so an eval utterance that is
    a single word of the shared label list matches every MASSIVE view.
  - Reproduction: `in_view("<any word of a MASSIVE label>", view.text())` is True for any MASSIVE proposer view, because
    every dev input in the view carries the 60-label list.
  - It decided this study's verdict. A later registration should match whole input fields, as the prompt canary does,
    or skip snippets that occur in the shared template or label list. V6 used the same canary and had no hit.
- **V6b-F4 (the gate at this size; descriptive):**
  - The same candidate (8 examples on MASSIVE), on the same 70 gate cases, was refused in D (15-4, p 0.0096) and
    recommended in F (15-2, p 0.0012).
  - The model's default sampling makes repeated gate evaluations differ, and with 70 cases this effect sits at the
    threshold. RecoveryStrategy's own recovery plan put the size needed at about 116.
  - SciFact's second look (29-17, p 0.052; plan: about 546) and MBPP's repair (11-5, p 0.105) also showed gains the gate
    could not confirm at the registered sizes. The pilot's effects, measured on seen cases, were larger than these.
- **V6b-F5 (the registered false-accept clause; computed before the freeze, seen in the A/A):** the per-workflow
  false-accept constraint has margin 0. On the A/A it fired on MBPP by noise. With an ineffective activation it would
  decide `unsafe_or_regressive`. A later registration should use a test, or a margin.
- **Note (V6's runner):** V6 reached Ollama as `localhost`, which cost about 2.1 s of name resolution per request on
  this machine. V6's seconds per case are mostly that overhead. V6b used `127.0.0.1`.

## Everything that went wrong or is open

- **The verdict was decided by a canary defect** (V6b-F3), not by a leak. Correcting that rule, by whatever means, is a
  new registration on fresh cases.
- **RecoveryStrategy proposed the right remedies but the gate refused all three on the targets.** The gate sizes came
  from pilot effects on seen cases, which overstated the effects here: SciFact's second look won 29-17 on 163 cases
  against the pilot's 15-6 on 60.
  - On MASSIVE the remedy demonstrably transferred: F's activation of the identical candidate gave 77 against 60.
  - So the open question is the gate's power at these sizes, not whether the remedies help.
- **MBPP's repair barely helped on the full file.** On dev, all 9 of B's failures failed the shown test, and 3 rounds
  rarely fixed them. The pilot's gain was on the sanitized file.
- **Only one remedy per search point.** G (every option on) beat B on MASSIVE and MBPP, but the declared space held one
  pipeline step at a time, so the gate never judged a combination like G's.
- **One model, default sampling, no seed.** `gpt-oss:20b` was about 12 times slower per short call and did not fit.
- **Scripted approval; a hand-written baseline by the study author; no audit.**
- **Contamination risk.** All four datasets may be in the model's pretraining data. This affects every arm alike.
- **Tokens are Ollama's `prompt_eval_count` plus `eval_count`.** A cached prompt prefix is not re-evaluated, so prompt
  tokens can under-count repeated prefixes.

## What may be claimed

- The pre-registered verdict is `contaminated_evaluation`, caused by a defect in the study's own canary; no leak was
  found. With that hit set aside, the registered rules give `inconclusive`. Neither is a positive verdict.
- Under VALIDATION_PLAN's wording rule, the public wording stays "implemented-experimental, benefit unmeasured". No
  claim that the recovery loop raises held-out success may be made from V6b.
- Descriptively, and only so:
  - `mh.diagnose()` and RecoveryStrategy named the failures and proposed the remedies the V6 post-run analysis had
    found, on all four workflows.
  - The gate approved one of them, GSM8K's working prompt, which held on fresh cases (20 → 81 of 100).
  - The MASSIVE remedy transferred when the gate approved it in F (60 → 77).

## Claim ledger

| Sentence a public summary may use | Rows behind it |
|---|---|
| "In V6b the registered verdict was contaminated_evaluation: the study's own leak canary fired on a one-word utterance that is part of every task's label list; no leak was found." | `analysis.json`, `contamination.proposer_view_canary`, `verdict_trail` |
| "On fresh cases, the meta-harness's recovery proposer offered the expected remedy on all four workflows; the gate approved it only on GSM8K, where held-out success rose from 20 to 81 of 100." | `adaptation/*-D.json` (offers, decisions); `v6b_rows.csv`, workflow `gsm8k`, arms `B` and `D` |
| "On SciFact the meta-harness's approved change came from its search proposer, a prompt, and raised held-out success from 50 to 73 of 100." | `adaptation/scifact-D.json`; `v6b_rows.csv`, workflow `scifact`, arms `B` and `D` |
| "A hand-written prompt reached 257 of 400 against the meta-harness's 266." | `v6b_rows.csv`, arms `H` and `D` |
| "False accepts did not rise on any workflow (pooled 214 for static G6, 130 for the meta-harness)." | `v6b_rows.csv`, column `false_accept`, arms `B` and `D` |

## Files

- `splits.json`: the drawn ids per workflow and split (registered).
- `rows/<workflow>/<arm>.jsonl`: one row per eval case and measured arm (ids, release, oracle outcome, calls, tokens,
  characters, seconds, output digest). Arms equal to B by the same-profile rule have no file of their own.
- `v6b_rows.csv`: every row, including the same-profile arms (column `same_profile_as`).
- `analysis.json`: the analyser's output (decision rule, secondary comparisons, per-arm tables, funnel, controls,
  contamination checks, verdict).
- `adaptation/<workflow>-{D,F,E}.json`: each adaptation's diagnoses, offers, gate reports, decisions, recovery plans,
  activations and final profile; `-oracle_selftest.json` (with the example-bank counts); `-gaming.json`.
- `run_meta.json` (pinned hashes at start and end), `tap.json` (model calls and tokens by phase, leak hits),
  `progress.log`.
- No item text is published, only ids, outcomes, counts and digests (VALIDATION_PLAN §0.1). Model outputs stayed in
  the work folder.

## Reproduce

From `oss/`, with the registered files in `$V6B_DATA` and an empty `$V6B_WORK` outside the repository, Ollama 0.40.2
serving `llama3.1:8b` at the registered digest, and the G6 source at the registered manifest:

    python validation/v6b_metaharness/run.py draw --data $V6B_DATA --out <dir>        # reproduces splits.json
    python validation/v6b_metaharness/run.py run  --data $V6B_DATA --out docs/validation/results/V6b --work $V6B_WORK
    python validation/v6b_metaharness/analyze.py --out docs/validation/results/V6b

`run` refuses to start unless REGISTRY.md holds the frozen hashes. Sampling is not seeded, so a rerun gives different
per-case outputs; the analysis of these rows is deterministic.
