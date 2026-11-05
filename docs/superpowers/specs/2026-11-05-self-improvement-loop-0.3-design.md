# G6 0.3.0: the self-improvement loop in G6's own code

Status: design for challenge, 2026-11-05 (night). Owner's direction: the loop's five steps run in G6's code, unattended, with
a person in the loop only to promote what users get and for safety; coding first, the same machinery later for any AI work;
the problem is what the user implicitly wants, checked through an explicit model chosen by the domain's evidence standard.

## 1. What the record says the design must fix

- **Task level:** within a run the loop made 6 of 22 wrong first patches right and broke 1 of 100 right ones (determined
  unseen bugs). The pilot bar is 45%.
- **Harness level:** the rule test raised balanced accuracy to 63.7% on stages 1 and 2 and fell to 49.1% on stage 4. Layers
  built from the issue's words share their holes (replay 5 added 3 catches).
- **Where wrong patches fail:** of 345 wrong final patches, 54 (16%) break a test that existed before the patch; 291 (84%)
  fail only the maintainers' new tests. So most of the gap is intended behaviour no existing test encodes.
- **What a reader would need:** 12 of 19 failures needed reading the code or running the example; 7 of 19 needed the
  maintainers' intent.
- **G6's own code:** classes recur until the design changes (attribution, deadline, masking, week books); repairs add defects.

## 2. The loop, step by step, and what is new

One loop, in `src/g6/metaharness`, over a `Domain`. Steps 1 and 4 exist; 2, 3 and 5 are new wiring of existing parts.

| Step | Existing part reused | New |
| --- | --- | --- |
| 1. Observe and check | The study's runner and `check`; `completion_loop`'s failure rows | A `CaseRecord` reader: per case, the method used, its verdict and finding, the pre-choice context, and a one-bit truth joined later |
| 2. Diagnose | `lessons` (root causes, `Prediction`), `FailureRecord`, `tick_rca` | `diagnose.py`: each miss gets a kind (false acceptance or false alarm), a mechanism class from one list, and the requirement source the check did not consult; plain code first, a model's classification only as a scored prediction |
| 3. Propose | Reflective and failure-trace strategies, `Profile`, `level2` | An edit space over the reviewer: its instruction, its requirement sources, its representation (template, relation, formal rule model), and the method chosen per context (PR #8's selector) |
| 4. Challenge | Frozen replay, the gate (`EvidencePolicy`), cut held-out tranches, cross-family review | A registration record written and committed before each replay or block; negative controls (right patches) as a hard limit on false alarms |
| 5. Promote, measure, roll back | Shadow adoption, the monitor, generations | Two tiers (section 4) and a route out of shadow as a draft PR |

**One list of causes.** "Why it failed" is decided in eight places today. `diagnose` uses `lessons.ROOT_CAUSES`, extended
with the reviewer's blind spots; the others map onto it, and a test holds the mapping.

**What may never be edited by the loop:** its gate, its judge, the truth, the held-out tranches, the envelope and the stop.

## 3. Implicit requirements and explicit models

For each task G6 builds a requirement set, each requirement with its source and provenance:

| Source | Provenance | How it is checked |
| --- | --- | --- |
| The issue's words and examples | stated | example and relation tests |
| The repository's existing tests that touch the changed code (`verification_search.derive_tests`) | repository_stated | run on base and patch, no model |
| Callers, signatures, docstrings and sibling implementations of the changed code | repository_stated when quoted from the code | relation and differential checks |
| The issue's rule, written as an explicit model (replay 6's formal model, agreed by two readers; a state machine; a relation set; a graph property) | model_hypothesis until the code or a person confirms it | the model's cases run as tests; a trace replayed on the code |
| What only the maintainers or the user know | none | a question to the user, with the distinguishing scenario |

- **The last row is the product's honest answer for an undetermined task:** G6 asks rather than guesses. In the study those
  bugs are counted apart, as now.
- **Evidence standard per domain**, declared as data on the `Domain`: what counts as a reproduced failure, which kinds of
  evidence it accepts and what each cannot show. Coding: an executed test on the code, or a model property only with a
  replayed trace. Science, maths, law and engineering are declared later (empirical replication, a checked proof, cited
  authority and argument, prototype and test), not built in 0.3.0.
- **Level 3:** the selector learns which source and representation catch which class of miss, by context known before the
  choice. Its record is what G6 reports as its metacognitive contribution.

## 4. Autonomy, people and safety

- **Tier 1, inside the study:** the gate promotes reviewer generations alone, recorded and reversible, and the daily digest
  says what changed and why.
- **Tier 2, what users get:** leaving shadow writes a `claude/loop-gen-<n>` branch with the change and its evidence, pushes it
  and opens a draft PR. GitHub tells the owner; only the owner merges.
- **Questions that wait:** one durable store (`workforce/decisions`), answered through the client's dialog when the owner is
  in a session, never through a terminal and never by an agent. `metaharness_confirm`'s terminal fallback is removed.
- **Safety stops:** the stop file always works. A safety stop also opens a GitHub issue, and the loop does not resume until the
  owner answers.
- **Budget:** both allowances are paced by `self_maintenance/pacing` (about a seventh a day, at most a fifth, the owner's
  reserve released in the last 24 hours). G6 counts its own Claude sessions' usage from their result events against an
  allowance the owner sets, and reads Codex's limit directly.
- **Running unattended:** the improve-control runner (survives Claude Code), start and stop over MCP; a scheduled task for
  reboots is the owner's step.

## 5. Before anything runs unattended

Close, each as its own change with a Codex review loop, the reproduced open findings on the loop's control code: the stop
erased during a start and the frozen-file recheck (improve-control), the funnel and frozen-snapshot findings (loop-fixes), the
week-book redesign (loop-followup-2), the domain fingerprint (tick-rca), masking rebuilt from files (k-followup), the
attribution redesign and one Windows job object per run (host-k3, K3), the Codex refusal code, the `test_reproduce_review`
pin, and `metaharness_confirm`'s fallback. Also, from the cross-family challenge: `compare.judged` decides by bug (PLAN.md,
2026-11-05, night, later), and the oracle is kept from candidate code (section 9).

## 6. What is measured, in order, each registered before it runs

1. The K3 pilot (running): conversion against the 45% bar.
2. Replay 6's development check: does an explicit rule model catch what the issue-word layers miss, without false alarms?
3. The method-selection test, at no model cost.
4. A requirement-source replay: existing tests plus callers and siblings, on recorded patches.
5. Stage 5: arms A, T and K3 (arm F), the two primaries at 0.05.
6. Arm L on stage 5's frozen order, in blocks, after the loop's steps 2, 3 and 5 run in code.

## 7. The 0.3.0 bar

- **Ships when all of these hold:**
  - The loop runs unattended on the study for 7 days with no safety stop and no budget overrun.
  - In that time it completes at least 3 cycles of all five steps, with at least one promotion and one refusal recorded.
  - It survives injected faults with no lost or doubled promotion: a stop mid-tick, a killed process mid-promotion, a
    provider limit, a reboot and low memory.
  - Every promotion is gated and reversible, every open finding above is closed, and the integration line's tests pass.
- **Promoted when** a stage 5 primary passes at p < 0.05 by bug, and arm L beats arm F on the held-out block.
  - Either primary suffices, as the owner ruled. With two looks, up to about a 10% chance that one passes by luck, and that is
    stated wherever the result is.
  - L against F: one sign test by bug on the held-out block, at 0.05, registered before arm L's first run, with one look.
  - Otherwise the result is reported as not yet shown.

## 9. Changes from the cross-family challenge (adopted before anything is built)

- **Oracle isolation.** Code a candidate wrote (a patch, a reviewer's test, a probe) never runs beside the maintainers' fix or
  tests. It runs in the task image, or with `/oracle` removed; the maintainers' tests are copied in for scoring alone.
- **A boundary for what the loop may never edit.** Proposers and candidates run confined, in their own writable workspace,
  with no read or write over the gate, the judge, the truth, the held-out tranches, the envelope or the stop.
- **The stop interrupts.** Cancellation is checked before every model request and before every adoption, with bounded
  termination of work in flight, not only between ticks.
- **Two kinds of diagnosis.** Development diagnosis, shown to proposers, reads development cases only. `tick_rca` stays
  evaluator-only, since it reads held-out results, and its remedies stay a person's.
- **Diagnosis says what it knows.** Each record separates the observed symptom, the mechanism hypothesis and the tested
  remedy. `Prediction` scores the remedy's effect, not the cause.
- **Gate the two errors apart.** False acceptances (a wrong patch passed) and false alarms (a right patch accused) are gated
  as separate label-conditioned rates, with right patches as the negative controls.
- **Requirements from the repository are hypotheses.** A caller or docstring can certify a bug. Relations read from the code
  stay hypotheses until an executed distinguishing example or a person settles them. Conflicting sources are resolved
  explicitly. `derive_tests` reads from the frozen base and keeps those tests' bytes, so a candidate's added test is never
  `repository_stated`.
- **A replay gain is necessary, not sufficient.** No reviewer generation reaches what users get on a replay result alone. It
  needs the live L-against-F comparison: conversions, breaks, abstentions and cost.
- **Arm L's blocks are fixed first.** Its disjoint bug blocks are allocated before stage 5's first run, and each block retires
  once its results have fed a change.
- **Promotion is recoverable.** Each promotion has an operation id written before it starts. A restart reconciles the head, the
  evidence, the branch and the PR before anything is retried.
- **Owned resources are capped and reconciled.** Containers get the runner's limits, and a restart cleans up what the record
  says the run owned, not only `finally` blocks. One Windows job object per run.
- **Pacing per provider.** Separate reservations and settlement for Claude and Codex. A daily ceiling sits beside the
  cumulative pace, so an unused week cannot be spent in one day. Readings persist across restarts, and an incomplete session
  is charged conservatively.
- **Questions and starts through the client.** The decisions store gains bound clarification answers. Enrolment and resume go
  through the client's dialog, not `input()`.
- **Cross-family by producer.** The reviewer is chosen as the family that did not write the work, and a reviewer generation's
  change is itself challenged by the other family.
- **Level 2 credit from shadow.** `level2` earns credit from shadow adoptions, removed on rollback, not only from people's
  activations.
- **The selector executes.** PR #8's selector gets admission and execution adapters. A recommendation is not counted as an
  executed selection.
- **A monitor that can fire.** The audit stream is sized to the rollback sensitivity wanted; five audited pairs cannot trigger
  it.

## 8. Generalising

The `Domain` seam stays the only place coding is known: cases, truth, evidence standard, judge, edit space. Nothing else is
built for another domain until coding shows the loop works.
