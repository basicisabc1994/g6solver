<!-- SPDX-License-Identifier: Apache-2.0 -->
# V6 — meta-harness customisation: results (2026-10-19)

Registered in `../../REGISTRY.md` (entry "V6") before any arm read a gate split or a held-out split. The frozen scripts
(`oss/validation/v6_metaharness/`) ran once, unchanged, from 01:51 to 08:09 on 2026-10-19: one start, no crash, no
restart, 9,433 model calls to `llama3.1:8b` through the local Ollama, no other network call. Every hash the runner pins
(the four frozen files, the G6 source manifest, the data files, the model digest, the meta-gate's judging code) was the
same at the end as at the start.

**Verdict: `inconclusive`.** The meta-harness (D) beat static G6 (B), compute-matched self-consistency (E) and random
search (F) on the pooled held-out split after Holm, with fewer false accepts, but the whole gain came from one workflow
of four (GSM8K). The pre-registered cluster-bootstrap interval for the gain therefore reaches 0.0, which is not above the
oracle-error bound (0.005), so rule 5 (`mechanism_demonstrated`) fails. Rule 6 (`component_validated_no_transfer`) does
not apply, because the gain did hold on held-out tasks. Rule 7 gives `inconclusive`.

**The person's approval was scripted.** Every confirmation and activation was made by the study script under the name
"v6-scripted-approval (study script, not a person)". No person approved anything; person-minutes per activation were not
measured. Nothing here is `verified`.

## What ran

- **Workflows** (PROTOCOL §1): GSM8K test (MIT), MASSIVE 1.1 en-US test (CC BY 4.0), SciFact train claims (claims CC BY
  4.0, abstracts ODC-By 1.0) and MBPP sanitized test ids 11-510 (CC BY 4.0). Per workflow: 20 dev cases (the proposers),
  40 gate cases (the meta-gate's own held-out) and 60 held-out cases (this study's), drawn by seed 20261019 from ids
  only. Pooled held-out: 240 tasks. Banking77 was replaced by MASSIVE before freezing (finding V6-F2).
- **Arms** (PROTOCOL §5), all on the same model and the same task text: A one direct call; B the default profile through
  the harness; B2 a rerun of B (A/A); C the hints `HintStrategy` proposed from B's dev failures, ungated; D the
  meta-harness, K = 3 cycles of `SearchStrategy` + `HintStrategy`, every candidate through `evaluate`/`decide` on the
  gate split, scripted approval, then frozen; E self-consistency at D's mean calls; F random draws from D's options, as
  many per cycle as D evaluated, through the same gate and approval; G every optional step and check on; NULL an empty
  answer.
- **Same-profile rule:** where D, F and E ended on the default profile (D and F made no activation outside GSM8K; D's mean
  calls were at most 1.0 everywhere, so E's n was 1), their held-out rows are B's rows, as pre-registered. All other
  arms were measured separately.

## Primary endpoints (pooled, 240 held-out tasks)

| Endpoint | B | D | Paired | Interval / test | Holm-adjusted p |
|---|---|---|---|---|---|
| E1: success, D against B | 121 (50.4%) | 153 (63.8%) | 34 wins, 2 losses, 204 ties | mean +0.133; cluster bootstrap 95% [0.000, 0.404]; sign test p < 1e-6 | < 1e-6 |
| E2: false accepts, D against B | 108 | 76 | D fewer on 34 tasks, more on 2 | hard constraint (D ≤ B) holds; p (fewer) < 1e-6; p (rise) = 1.0 | < 1e-6 |
| D against E (success) | E = B (n = 1) | 153 | as E1 | as E1 | < 1e-6 |
| D against F (success) | F 143 | 153 | 14 wins, 4 losses | mean +0.042; cluster bootstrap 95% [0.000, 0.133]; p 0.0154 | 0.0154 |

The interval's lower bound is exactly 0.0 because D changed nothing outside GSM8K: a resample of the four workflows that
leaves GSM8K out (probability (3/4)^4 = 0.32) has a gain of zero. See V6-F3.

## Per workflow (held-out, 60 tasks each)

Successes of 60; FA = false accepts (released and wrong); blocked = not released (a failed check, or an intake block).
"= B" marks an arm whose frozen profile was the default profile.

| Arm | GSM8K | MASSIVE | SciFact | MBPP | Pooled (240) |
|---|---|---|---|---|---|
| NULL (empty answer) | 0 | 0 | 0 | 0 | 0 |
| A, one direct call | 12 | 36 | 38 | 39 | 125 |
| B, static G6 | 10 | 38 | 32 | 41 | 121 |
| B2, rerun of B | 13 | 36 | 29 | 41 | 119 |
| C, hints only | 9 | 38 | 32 | 37 | 116 |
| D, meta-harness | **42** | 38 (= B) | 32 (= B) | 41 (= B) | **153** |
| E, self-consistency | 10 (= B) | 38 (= B) | 32 (= B) | 41 (= B) | 121 |
| F, random search | 32 | 38 (= B) | 32 (= B) | 41 (= B) | 143 |
| G, every option on | 26 | 38 | 19 | 44 | 127 |

| Workflow | B: FA / blocked | D: FA / blocked | D calls per case | D seconds per case (B) | D calls per success (B) |
|---|---|---|---|---|---|
| GSM8K | 50 / 0 | 18 / 0 | 1.0 | 3.24 (2.20) | 1.43 (6.00) |
| MASSIVE | 20 / 2 | = B | 0.97 | = B | = B |
| SciFact | 19 / 9 | = B | 0.85 | = B | = B |
| MBPP | 19 / 0 | = B | 1.0 | = B | = B |

Per-workflow tables for every arm (released, blocked, calls, seconds) are in `analysis.json` (`arms`); per-case rows are
in `v6_rows.csv` and `rows/`.

## What the meta-harness did

Each workflow had 3 cycles, 4 gate evaluations (the search offered a new candidate every cycle; the hint proposer offered
one in cycle 1 and then failed, finding V6-F1), 16 in all.

| Workflow | Gate decisions (codes) | Activation | Gate split at activation | Held-out |
|---|---|---|---|---|
| GSM8K | 1 recommended; 3 `NO_GAIN` | cycle 1: the prompt "Work through the problem step by step. Then write the final answer on its own last line as 'Answer: <number>'." (30 wins, 1 loss on the gate split) | base 8/40, candidate 37/40 | B 10/60, D 42/60 |
| MASSIVE | 4 `INSUFFICIENT_EVIDENCE` (at most 4 discordant cases of the 5 needed) | none | — | D = B |
| SciFact | 4 `NO_GAIN`; the closest were the "use only the abstract" prompt, 15 wins 4 losses (p 0.0096), and the hint, 11-2 (p 0.011), against the threshold 0.05 / 6 = 0.0083 | none | — | D = B |
| MBPP | 2 `NO_GAIN`, 2 `INSUFFICIENT_EVIDENCE` | none | — | D = B |

- **F (random search)** made one activation, on GSM8K in cycle 1: the prompt "Give only the final number" with the
  whole-number check and `self_review` (17 wins, 4 losses on the gate split; it uses 2 calls and also beat the gate's
  compute-matched control). Its random draws never included the step-by-step prompt (4 of GSM8K's 11 options carry it).
  On held-out it reached 32/60 against D's 42/60. D's advantage over F rests on that one seeded draw sequence.
- **Learning gradient** G_D(n), n = 1..3: GSM8K +0.533 after every cycle (the head moved once, in cycle 1); 0 elsewhere.
  Cycles 2 and 3 added nothing anywhere, so level S2 of META_HARNESS_PLAN §7.3 is not shown.
- **Dev / held-out gap:** on GSM8K the activated profile scored 37/40 (92.5%) on the gate split that selected it and 42/60
  (70.0%) on held-out; the default profile 8/40 (20%) and 10/60 (16.7%). The gate split overstated the gain (+0.73
  against +0.53), as a split used for selection does.
- **Demotions:** 0 (the monitor is not part of V6).

## Controls

- **A/A (B2 against B):** 17 wins, 19 losses, mean −0.008, cluster bootstrap 95% [−0.067, 0.054], p 0.69; false accepts
  109 against 108. The endpoint did not fire on noise.
- **Planted improvement (+0.10 pooled, 24 tasks):** detected in 2,000 of 2,000 replicates at 0.05 / 4. This control
  tested the sign test only; it did not test the interval criterion that decided rule 5 (V6-F3).
- **Suite negative control:** NULL succeeded on 0 of 60 in every workflow; in all 32 gate evaluations (D and F) the
  base beat the empty answer (one-sided p ≤ 0.016 each), so no evaluation was refused as unable to discriminate.
- **Oracle self-test:** the oracle accepted all 400 reference answers (40 gate and 60 held-out per workflow: GSM8K's
  worked solutions, the labelled labels, MBPP's reference code run in the sandbox) and rejected the empty reply on every
  case. The pooled known oracle-error bound is 0.005 (GSM8K's published "less than 2 percent" breaking errors, weighted
  by its share); no figure is published for the other three.
- **Leakage canary:** no prompt the model received during adaptation contained a gate input (while proposing) or a
  held-out input (before measurement): 0 hits. No proposer view contained a gate or held-out input (0 of 6 views per
  workflow), while all 20 dev inputs were found in them (the canary's positive control).
- **Gaming checks (rule 8):** 1,320 held-out outputs scanned (300 of them MBPP programs): no file, process, network or
  import-machinery access and no oracle file name. The arms never received an answer; proposers saw dev answers only.
- **Intake blocks:** G6's intake blocked 9 of the 60 SciFact held-out tasks and 2 of the 60 MASSIVE tasks as "risky" in
  every G6 arm (no model call); A answered them. This is most of why B trails A on SciFact (32 against 38).

## Secondary comparisons (pooled, descriptive, no correction)

| Comparison | Wins | Losses | Mean difference | Cluster bootstrap 95% | p one-sided |
|---|---|---|---|---|---|
| D against C (hints only) | 48 | 11 | +0.154 | [−0.017, 0.425] | 1e-6 |
| D against G (every option on) | 44 | 18 | +0.108 | [−0.033, 0.254] | 0.00065 |
| D against A (one direct call) | 45 | 17 | +0.117 | [−0.071, 0.379] | 0.00025 |
| B against A | 16 | 20 | −0.017 | [−0.092, 0.050] | 0.80 |

- **Static G6 did not beat a direct call** (121 against 125). Its default system prompt ("Do the task. Reply with the
  answer only.") holds GSM8K at 10/60, and its intake blocks part of SciFact.
- **Owning every option (G) is not choosing one:** G gained on GSM8K (26) and MBPP (44) but lost on SciFact (19, the
  `ground` step turns most SUPPORT labels into NOT_ENOUGH_INFO), and cost about twice the calls.
- **Hints alone (C)** did not help anywhere (116 pooled).

## Falsifiers (META_HARNESS_PLAN §7.1)

| Falsifier | Outcome |
|---|---|
| D does not beat B | not met: D beat B pooled (Holm p < 1e-6), but only through GSM8K; on the other three workflows D was B |
| D does not beat E (the gain is extra compute) | not met in calls: D used 1.0 call per case, so E was B. D did spend 47% more seconds per GSM8K case (longer answers); compute was matched by calls, not by tokens |
| D does not beat F (learning adds nothing over search) | not met (Holm p 0.0154), but on one workflow and one random draw sequence that missed the prompt D found |
| False accepts rise | not met: 76 against 108 |
| Any arm can mutate its evaluator | not found: every pinned hash unchanged; 0 gaming matches |

## Findings

- **V6-F1 (library):** `HintStrategy` cannot propose twice from the same theory store. `T3Strategy` re-proposes the store's
  stored candidates, and `TheoryStore.add_candidates` refuses a batch with a stored id, so the second `propose` raises
  `G6_E_LEARNING_ID_EXISTS` and returns `G6_E_META_NO_NEW_CANDIDATE` ("the strategy failed"). Reproduction (no model):
  with `tests/metaharness/_rig.py`, `mh = adapt(wf, theories=store)`, `mh.confirm("Ada")`, then
  `mh.propose(HintStrategy(store))` twice. In V6 it limited D's hint proposer to cycle 1 in every workflow. Known and
  stated before the freeze; not patched.
- **V6-F2 (library):** a workflow whose task text contains one of the intake's high-stakes words ("payment", "medical",
  "delete", ...) is blocked before execution for every profile, and the meta-harness cannot carry the person's
  `accept_risky` decision: `g6.metaharness.evaluate.run_case` calls `g6.harness.run` without `decisions`, and no
  workflow or profile field supplies one. Reproduction: `harness.run(Standard(goal="Classify the message into one of these
  labels: card_arrival, card_payment_fee_charged. ...", checks=...), executor)` returns `blocked-escalated` with
  `G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED`, and `qualified-draft` with `decisions=("accept_risky",)`.
  It made Banking77 unusable (every dev case blocked, so it was replaced before freezing) and blocked 15% of SciFact's
  held-out tasks and 3% of MASSIVE's for every G6 arm.
- **V6-F3 (this study's design, found after the run):** with four workflows, the pre-registered two-stage cluster
  bootstrap cannot put the lower bound of the gain above zero unless D gains in at least three of them: a resample that
  omits every gaining workflow has probability ((4 − k)/4)^4 (0.32 for k = 1, 0.06 for k = 2), above the 2.5% tail.
  The registration did not compute this, and the planted control did not test it. The verdict stands as registered. A
  later registration that wants a pooled interval needs more workflows or a different interval, fixed before its run.
- **After the run (appended 2026-10-19):** V6-F1 and V6-F2 are fixed in commit `762d1700f9`. `HintStrategy` now stores
  only theories the store lacks and offers the stored candidates. `Workflow.accept_risky` names the person who accepts
  a task the intake holds as risky; it is part of the judging digest and is passed to `harness.run` as
  `decisions=("accept_risky",)`. The fixed code was not run on V6's splits; the verdict above is unchanged.

## Everything that went wrong or is open

- **One workflow carries the result.** The meta-harness found one change, on GSM8K, and it is the obvious one: the
  default profile asks for "the answer only", and the change asks for working. A user writing the prompt by hand would
  likely have found it; the study does not compare against that.
- **The gate was strict for these sizes.** With 40 gate cases and the threshold 0.05 / 6, the dev power estimate for a
  +0.20 gain was 0.26-0.49. SciFact's best candidate missed by p 0.0096 against 0.0083. Nothing was activated in three
  workflows; whether a larger gate split would have found real gains there is not measured.
- **D > F is fragile.** F's seeded draws missed the step-by-step prompt; 4 of the 11 GSM8K options carry it.
- **Compute was matched by calls only.** D's profile used no extra call but longer answers.
- **One model, default sampling, no seed.** Only `llama3.1:8b`; no 20B arm (PROTOCOL §10 item 2). Repeated runs differ
  (A/A: 36 discordant tasks of 240).
- **Contamination risk.** GSM8K and MBPP, and probably the others, are likely in the model's pretraining data. This
  affects every arm alike but limits what the absolute numbers mean.
- **Scripted approval.** A real person's review might refuse or accept differently, and its time is not measured.
- **Not audited.** No second person checked the workflows, the answer rules, the search options or the splits.
- **Answer-key error** is bounded only for GSM8K.

## What may be claimed (META_HARNESS_PLAN §7.2-§7.3)

- The pre-registered verdict is `inconclusive`, not a positive V6 verdict. By §7.2 the public wording stays
  "implemented-experimental, benefit unmeasured".
- Descriptively, one workflow shows what level S1 asks for (one promoted change beat the static harness on fresh held-out
  tasks: GSM8K, 42/60 against 10/60), but S1 is not claimed from an `inconclusive` study, and S2-S4 are not shown.

## Claim ledger

| Sentence a public summary may use | Rows behind it |
|---|---|
| "In V6 (one local 8B model, four public datasets), the meta-harness's single approved change, a step-by-step prompt on GSM8K, raised held-out success from 10 of 60 to 42 of 60." | `v6_rows.csv`, workflow `gsm8k`, arms `B` and `D`; `adaptation/gsm8k-D.json` |
| "On MASSIVE, SciFact and MBPP its gate approved no change, so results were static G6's." | `adaptation/{massive,scifact,mbpp}-D.json`, decisions |
| "Pooled over 240 held-out tasks, false accepts fell from 108 to 76." | `v6_rows.csv`, arms `B` and `D`, column `false_accept` |
| "The pre-registered verdict is inconclusive: the gain came from one workflow of four." | `analysis.json`, `verdict`, `verdict_trail`, `endpoints` |
| "Static G6 with its default profile did not beat a single direct call (121 against 125 of 240)." | `v6_rows.csv`, arms `A` and `B` |

## Files

- `splits.json`: the drawn ids per workflow and split (registered).
- `rows/<workflow>/<arm>.jsonl`: one row per held-out case and measured arm (ids, release, oracle outcome, calls,
  seconds, output digest). Arms equal to B by the same-profile rule have no file of their own.
- `v6_rows.csv`: every row, including the same-profile arms (column `same_profile_as_B`).
- `analysis.json`: the analyser's output (endpoints, per-arm tables, funnel, controls, contamination checks, verdict).
- `adaptation/<workflow>-{D,F,C,E}.json`: each adaptation's offers, gate reports, decisions, activations and final
  profile; `-oracle_selftest.json`; `-gaming.json`.
- `run_meta.json` (pinned hashes at start and end), `tap.json` (model calls by phase, leak hits), `progress.log`.
- No item text is published: only ids, outcomes and digests (VALIDATION_PLAN §0.1). Model outputs stayed in the work
  folder.

## Reproduce

From `oss/`, with the registered files in `$V6_DATA` and an empty `$V6_WORK` outside the repository, Ollama 0.40.2 serving
`llama3.1:8b` at the registered digest, and the G6 source at the registered manifest:

    python validation/v6_metaharness/run.py draw --data $V6_DATA --out <dir>        # reproduces splits.json
    python validation/v6_metaharness/run.py run  --data $V6_DATA --out docs/validation/results/V6 --work $V6_WORK
    python validation/v6_metaharness/analyze.py --out docs/validation/results/V6

`run` refuses to start unless REGISTRY.md holds the frozen hashes. Sampling is not seeded, so a rerun gives different
per-case outputs; the analysis of these rows is deterministic.
