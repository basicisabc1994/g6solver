# Seed strategies: fixed-strategy pairs

Do G6's 16 first-party seed instructions help, measured as pairs? This folder is the instrument for the 0.3 design's
section 5 row "Selector: paired outcomes per mechanism, before any learning" (design 14.2, "measure worth").

**State: `evidence_pending`.** No trial has been run. Every seed is `unmeasured`, and nothing here is a result.

## What is measured

Each seed (`g6.learning.strategy.seed_artefacts`) is a candidate against no strategy at all, on the same cases, with
the same agent, information and budget (`g6.learning.paired_eval.PairedEvaluator`). The baseline arm is run once per
case in a replay and named in each seed's receipt. Each pair is a receipt in the evaluator's ledger, and the
evaluator keeps what the receipt names beside it (`receipts.jsonl.evidence/`: the task, the oracle, each arm's reply
and judge verdicts, each file under its SHA-256). Worth is read from that ledger and those kept files and from
nowhere else (`g6.learning.worth.worth`): a pair counts only when it can be built and scored again from them, and a
row appended to the ledger by hand counts for nothing.

Published per seed: cases, complete cases, incomplete pairs (an arm that was refused, interrupted or stopped by the
budget stays in the record), wins, losses, net benefit, the exact one-sided sign test's p-value, the cost ratio where
both arms report a cost, the time ratio, the cases that regressed, and whether the bar is met.

## The policy

`policy.json` is frozen at policy digest `ef832fdf51c771c0ac3f32ab3f6773bcce581d34ab9670083690755b985086d5`.

| Field | Value | Why |
|---|---|---|
| `min_complete_cases` | 30 | the meta-harness gate's own minimum of held-out cases |
| `min_discordant` | 5 | the gate's minimum of cases where the arms disagree |
| `alpha` | 0.003125 | 0.05 divided by the 16 strategies measured in one cohort |
| `max_regressions` | 0 | on cases marked critical |
| `max_cost_ratio` | 1.25 | candidate over baseline: dollars where both arms report them, else measured time |
| `max_incomplete_share` | 0.2 | pairs with an arm that did not complete |

The policy's digest is written into the ledger for a cohort before its first trial. The runner and the reading take
no policy from their caller: a `policy.json` with other content is refused before anything runs, and a cohort whose
trials were begun under another policy, or under none, is not read. With a small cohort no seed can meet the bar, and
every seed stays `insufficient`; that is a result, and it is published as it is.

What this does not close: the ledger's head is kept nowhere outside this folder, so whoever can write the ledger
file and the kept files together can make pairs that read again. Keeping the head with the certificate issuer is a
later change.

## What is sent, and where

Each arm sends one task's text, with the seed's hint block in the candidate arm, to the model the person set in their
key file. Every call passes G6's one consent check, with the prompt declared as its source: without the person's
grant for that provider the arm is `refused`, and the refusal stays in the receipt. The expected answer, the rubric
and the scoring rule are never sent to the agent.

## How to run

    python validation/strategies/run.py --cohort <cohort.json> --replay <replay id> --agent-id <name of the model>

The cohort file is `{"cohort_id", "split", "instances": [{"instance_id", "input", "expected_output", "metadata"}]}`
with `split` one of `heldout` or `shadow`. Receipts and strategy files go under the person's G6 home (`strategies/`)
unless `--out` names another folder; the `rank_strategies` op of `cognitive_bridge` reads that folder.

## What is owed

1. The frozen replay cohort of the design's section 3.6 (development, then final). It does not exist yet.
2. The seed run on the development cohort, then on the final cohort: 16 candidate arms and one shared baseline arm
   per case per replay, on the person's own model, one run at a time.
3. Publication of every `Worth` field per seed, whatever it shows.
4. Then, and only then, operator children against their parents under the same policy (`g6.learning.operators`).