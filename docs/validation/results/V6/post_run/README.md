<!-- SPDX-License-Identifier: Apache-2.0 -->
# V6 after the run: root causes, and which G6 components lift the three inconclusive workflows (2026-10-19)

**This is development, not a validation.** V6's verdict (`inconclusive`, `../README.md`) is final and unchanged. On the
owner's direction (DECISIONS #27) this note asks why the meta-harness approved nothing on MASSIVE, SciFact and MBPP,
and which other G6 components could lift them. Every case used here was already seen in V6, so no number below is a
claim. A claim needs a new registration on fresh cases (see the last section).

- **Root causes:** `oss/validation/v6_metaharness/post_run/rca.py`. It reads V6's dev-check outputs (dev split)
  and V6's held-out outputs (the eval split, already spent) from the work folder, which is never published. It is
  descriptive only.
- **Pilot:** `oss/validation/v6_metaharness/post_run/pilot.py`. It ran V6's dev and gate ids, 60 per workflow,
  each seen in V6, on the same `llama3.1:8b` through `g6.metaharness.evaluate.run_case`, with the V6-F2 fix on every
  arm. Its rows are in `pilot_rows.jsonl` (ids, outcomes and digests only), with `pilot.log` and `pilot_summary.json`.
  It made 940 model calls.

## Root causes (static G6, arm B)

| Workflow | Failures on held-out (spent) | Kinds | Another arm right on |
|---|---|---|---|
| MASSIVE | 22 of 60 | 20 wrong label (only 5 in the gold label's domain; `cooking_recipe` → `cooking_query` x3); 2 held at intake (V6-F2) | 6 of 22 |
| SciFact | 28 of 60 | 9 held at intake (V6-F2); 15 SUPPORT given wrongly (8 should be CONTRADICT, 7 NOT_ENOUGH_INFO) | 15 of 28 |
| MBPP | 19 of 60 | 13 fail the example test the task shows (assertion), 1 times out on it, 5 pass it and fail a hidden test | 8 of 19 |

Across all three:

1. **The options were too narrow.** V6's search could pick only three prompts, `self_review`, one extra check and one
   task-specific step (`ground` or `example_repair`). It offered no G6 component that addresses these failures, and the
   meta-harness had no way to offer one.
2. **No examples could reach the model.** `Profile.context_budget_chars` existed, but nothing used it, so a label task
   could never be shown labelled examples.
3. **The gate split was too small for the effects there were.** With 40 gate cases at p ≤ 0.05/6, a gain of 3 or 4 in
   60 cannot pass. MBPP's every-option arm G (44 against 41) and SciFact's best candidate (p 0.0096 against 0.0083)
   fell short this way.
4. **The intake held SciFact and MASSIVE tasks** for a person, with no way for a person to accept them (V6-F2, now
   fixed).

## Pilot (60 seen cases per workflow; development only)

| Workflow | Arm | G6 part | Success of 60 | Against B | Calls per case |
|---|---|---|---|---|---|
| MASSIVE | B, static G6 | | 38 | | 1.0 |
| | 8 nearest labelled examples (MASSIVE train partition) | `g6.context` exemplars | **50** | 14 wins, 2 losses (p 0.004) | 1.0 |
| | vote of 3 | `g6.reasoning.weighted_vote` | 42 | 5 wins, 1 loss (p 0.22) | 3.0 |
| SciFact | B, static G6, with `accept_risky` | | 31 | | 1.0 |
| | a contradicted SUPPORT becomes CONTRADICT | `g6.grounding.check_claim` | 34 | 8 wins, 5 losses (p 0.58) | 1.0 |
| | a second look at every SUPPORT | `g6.reasoning`-metered revision | **40** | 15 wins, 6 losses (p 0.08) | 1.7 |
| | vote of 3 | `g6.reasoning.weighted_vote` | 33 | 6 wins, 4 losses (p 0.75) | 3.0 |
| MBPP | B, static G6 | | 40 | | 1.0 |
| | repair from the shown test, up to 3 rounds | sandbox, `g6.reasoning` budget | **49** | 9 wins, 0 losses (p 0.004) | 1.7 |
| | one repair round (V6's `example_repair`) | | 45 | 8 wins, 3 losses (p 0.23) | 1.3 |

Two-sided sign tests on the paired cases. The two arms that use more calls beat a vote that uses even more (3.0), so
the gain is not just extra compute; the gate's own compute-matched control would still decide that.

What it means:

- **MASSIVE** needs *information*, not reasoning. The model confuses labels whose names do not say how they differ;
  examples tell them apart.
- **SciFact** needs a *targeted check* on the one label the model over-uses (SUPPORT). The lexical claim check helps a
  little at no cost; a general vote does not.
- **MBPP** needs *execution feedback*: run the test the task shows and repair, more than once.
- **GSM8K's** gain in V6 was the working prompt; none of these is needed there.

## What was built from this

Owner direction, DECISIONS #27: recovery must be surfaced to the user by the meta-harness.

- **Recovery on every refusal** (`g6.metaharness.recovery`, eeebb25d0a). A decision that is not `recommend` says why,
  from the gate's aggregates only. An underpowered win comes with an estimate of the held-out size that would confirm
  it, and is never reported as "no effect". An empty proposal says why, too.
- **`mh.diagnose()` / MCP `metaharness_diagnose`.** It sorts dev failures into root causes:
  - held at intake;
  - rejected by a check;
  - held by the harness;
  - wrong answers by kind (label, number, code, text);
  - an over-used label;
  - an oracle that gives no answer;
  - candidates refused so far.

  Each cause maps to remedies naming the G6 component they use and who acts: the meta-harness (a candidate the gate
  judges) or a person.
- **Remedies the meta-harness can try itself.**
  - `Profile(exemplars=k)` with `Workflow(examples=...)`: labelled data the person declares, never a held-out case.
  - The built-in steps `g6.metaharness.steps.repair_with_tests` and `steps.second_look`, which a person declares in the
    workflow.

  All three are searchable and gated like any profile change.

## What a claim needs

A new registration (call it V6b), frozen before any of its cases is read:

- **Fresh cases from the unused pools:**
  - MASSIVE: 2,854 left;
  - SciFact train: 689 left;
  - MBPP test ids 11-510: 137 left, enough for one more 20/40/60 draw and no more;
  - GSM8K: 1,199 left.
- **A search space that includes these remedies:** `exemplars`, `repair_with_tests`, `second_look`.
- **Gate splits sized for the effects seen here.** At p ≤ 0.05/6 and power 0.8 (`recovery.cases_needed`), the observed
  effects need about:

  | Effect | Gate cases |
  |---|---|
  | MASSIVE examples | 70 |
  | MBPP repair | 70 |
  | SciFact second look | 163 |
  | SciFact claim check | 908 |
- **Fixes for V6's design gaps:**
  - more than four workflows, or an interval four can pass (V6-F3);
  - a hand-written-prompt baseline;
  - compute matched by tokens as well as calls;
  - a second model;
  - a person approving.

Not audited: no second person checked these failure kinds, the pilot arms or their code.
