# Research

G6 is a research programme in **general capability through harness engineering**: improving not the model's
weights but the procedures that plan, check and revise its work. Every public claim is either backed by a
pre-registered study or labelled for what it is. Misses and withdrawn claims stay on this page.

The working theory is published in full:

- [*A Computational Structural Intermediate Representation for Compositional, Evidence-Constrained and Self-Improving AI Agents*](https://github.com/basicisabc1994/g6solver/blob/main/docs/theory/G6_Computational_Structural_Intermediate_Representation.md)
  — computational structure, the Resource-Yoneda view, ECHA and AHI.
- [*Evidence-Constrained Harness Adaptation (ECHA) v0.1*](https://github.com/basicisabc1994/g6solver/blob/main/docs/theory/G6_Evidence_Constrained_Harness_Adaptation_Spec_v0.1.md)
  — how a harness may learn without letting the learner redefine success.
- [*The Validation Paradox*](https://github.com/basicisabc1994/g6solver/blob/main/docs/theory/G6_Self_Validation_Paradox_Resolution.md)
  — how a system can learn when it cannot always tell whether it has succeeded.
- [*CSIR–ECHA research protocol*](https://github.com/basicisabc1994/g6solver/blob/main/docs/theory/CSIR_ECHA_RESEARCH_PROTOCOL.md)
  — the four pre-specified hypotheses and their falsifiers.

## Status of claims

| Claim | Status |
|---|---|
| Deterministic gates (action gating, patch promotion) refuse the bypasses found in the 2026 audits | `implemented` — regression-tested |
| `action_gating` stops harmful agent actions without stopping everything | **the action-gate study: `unsafe_or_regressive`** (2026-10-18). On AgentDojo's 30 harmful tool calls the gate let one run (a password change to an attacker's value); a read-only allow-list let none. That miss is fixed: a credential or permission change now goes to a person. A re-run on the same calls is a development measurement, not a validation. [Results](https://github.com/basicisabc1994/g6solver/blob/main/docs/validation/results/V2/README.md) |
| Self-training improves benchmark scores | **withdrawn pending re-validation** (a scoring defect could inflate gains) |
| G6 helps independent coding agents on held-out tasks | `aspirational` — protocol published, not yet run |
| `grounding.check_claim` does not call negated or wrong-figure claims supported | **the claim-checking study: `component_validated_no_transfer`** (2026-10-18). On 174 such VitaminC pairs it called 0 supported (95% upper bound 2.2%); a relevance baseline called 32% supported. It found support for only 19% of paraphrased claims, against 82% for one call to a local 20B model. [Results](https://github.com/basicisabc1994/g6solver/blob/main/docs/validation/results/V1/README.md) |
| A multi-call reasoning harness beats a single call | **not claimed** — the one archived observation was negative; any new method must pass the compute-matched gate in `g6.reasoning.compare` |
| The meta-harness improves a user's held-out results | **the first meta-harness study: `inconclusive`** (2026-10-19). On one local 8B model and four public datasets, its gate approved one change, a step-by-step prompt on GSM8K, which raised held-out success there from 10 of 60 to 42 of 60; on the other three it approved nothing. Pooled over 240 held-out tasks, false accepts fell from 108 to 76. The gain came from one dataset of four, so the pre-registered interval does not exclude zero. Approval was scripted, not a person's. [Results](https://github.com/basicisabc1994/g6solver/blob/main/docs/validation/results/V6/README.md) |
| G6 with its default settings beats one direct model call | **not shown** — in the first meta-harness study it scored 121 of 240 held-out tasks against the direct call's 125 (descriptive, not an endpoint) |

## The studies

The [validation plan](https://github.com/basicisabc1994/g6solver/blob/main/docs/validation/VALIDATION_PLAN.md)
pre-registers five studies, and the meta-harness studies have their own protocols. Each one is compared against a baseline
given the same number of model calls, runs on local models, and judges outcomes with an external oracle rather than an
AI judge. Every study ends in one stated verdict. The owner unblocked the runs on 2026-10-18, with evaluation sets drawn
from third-party public datasets. Each study is registered in the
[registry](https://github.com/basicisabc1994/g6solver/blob/main/docs/validation/REGISTRY.md) before its evaluation split is
read.

| Study | Question | Status |
|---|---|---|
| Claim checking | Does `grounding.check_claim` separate supported from unsupported claims? (VitaminC) | Ran 2026-10-18: `component_validated_no_transfer` |
| The action gate | Does `action_gating` stop harmful actions without stopping everything? (AgentDojo) | Ran 2026-10-18: `unsafe_or_regressive`. Its miss fixed afterwards; not re-validated |
| Static composition | The pre-registered mission-bridge experiment | Not run; waits on the mission-bridge port |
| Governed adaptation | Evidence-constrained harness adaptation (TimeMachine-bench) | Not run |
| Bundles in use | Descriptive case tables | Not run. A public dataset is chosen for four bundles; a bundle with none gets no row until an independent author writes its tasks |
| The meta-harness, first study | Does tuning through the meta-harness beat static G6, extra compute and random search? (GSM8K, MASSIVE, SciFact, MBPP) | Ran 2026-10-19: `inconclusive`. Two library faults it found fixed afterwards; not re-validated |

A validated claim about the fixed action gate needs a new registration on tool calls the gate has not seen.
Pre-registrations, raw results and misses are published as they are produced.

## The meta-harness (built; its first study inconclusive) {#the-meta-harness}

The meta-harness (`g6.metaharness`) tunes G6 to a task you repeat. It is `implemented`: built in eight
phases, each with its own tests, including null-instrument controls (two profiles that behave the same are recommended
no more often than the gate's alpha allows, over 500 seeds) and survival controls (a planted improvement is recommended
at the rate the gate's power predicts). [How it works](metaharness.md).

- **What it does.** Proposes changes to how G6 runs that task (which of your models runs each step, budgets, prompt and
  hints, extra checks, optional steps, compiled shortcuts, your own code files, an optional LoRA adapter), tests each
  on held-out examples whose answers you trust, at equal compute, and shows you the evidence.
- **What it does not do.** It does not change your base model. It does not activate a change without a person's
  approval; the only thing it does on its own is move back to a version you approved earlier. It cannot change your
  checks, the oracle, the held-out examples, the approval rules or the stop. It does not take over an agent's shell
  or network, and it does not rewrite G6's own source code.
- **What its results can say.** `qualified-draft` at best, never `verified`. A tuned version can never raise the
  label of a task result.
- **What the first study found** (2026-10-19, [results](https://github.com/basicisabc1994/g6solver/blob/main/docs/validation/results/V6/README.md)).
  Four workflows from public datasets (maths word problems, intent labels, scientific claims, Python functions), one
  local 8B model, three tuning cycles each, with every approval made by the study script. The gate approved one change,
  on GSM8K: asking for the working instead of "the answer only" raised held-out success from 10 of 60 to 42 of 60. On
  the other three it approved nothing, because no option won clearly enough on its 40 gate cases. Pooled, the tuned
  version beat static G6 (153 against 121 of 240) and a random search through the same options (143), with fewer false
  accepts (76 against 108). Self-consistency at the same number of calls was static G6 itself, since the tuned version
  used one call per task. The verdict is `inconclusive` because one
  dataset carries the whole gain: with four datasets, the pre-registered interval could exclude zero only if three of
  them gained (a flaw in the study's own design). Controls held: a rerun of static G6 against itself
  showed no effect (p 0.69), a planted gain was found in 2,000 of 2,000 replicates, and no held-out input reached a
  proposer.
- **What the first study found wrong in the library.** The hint proposer could propose only once from a store. And a
  task whose text contains a high-stakes word such as "payment" was blocked for every profile, with no way for a person to
  accept it. Both are fixed; neither fix has been re-validated.
- **After the first study: root cause and recovery** (development, not a validation;
  [note](https://github.com/basicisabc1994/g6solver/blob/main/docs/validation/results/V6/post_run/README.md)). On the owner's
  direction, the three workflows with no approved change were taken apart. Their failures needed G6 parts the study
  never offered:
  - **MASSIVE:** labelled examples of similar inputs (`g6.context`) took it from 38 to 50 of 60 at one call.
  - **SciFact:** a second look at the label it over-used went from 31 to 40.
  - **MBPP:** repairing against the test the task shows went from 40 to 49.

  All of these cases were seen in the first study, so none of this is claimed. These remedies are now built in, and
  the meta-harness offers them itself. Every refusal now says why and what to try next. A second study, registered
  on fresh cases, then tested them
  ([results](https://github.com/basicisabc1994/g6solver/blob/main/docs/validation/results/V6b/README.md)).
- **What is claimed.** That it is built and tested as described, and what the first study measured. Not that it
  improves results in general: that study is inconclusive, its one gain is a prompt a person might well have written by hand, and compute was
  matched by calls, not tokens. The design rests on mathematical arguments in the owner's design notes
  (`docs/theory/`); they are not machine-checked and are not cited as evidence.

## Open problems

- Turning weak or partial evidence into reliable learning signals.
- Independent evaluators that the adapting system cannot influence.
- Transfer of learned safeguards to unseen task families.
- Enough held-out examples: most real workflows have few, so a fair test of a change is often too small to decide. In
  the first meta-harness study, 40 gate cases per workflow were too few to approve any change on three of four
  datasets.
