# Development round 3

**Development, not a validation.** Nothing here is a claim; a registered study (V7) is the only place a result is claimed.

Task model `llama3.1:8b`; reflector `gpt-oss:20b`; method v0 digest `c82a805a01e3`. Dev-eval cases are fresh from the pool ledger; each cell is successes out of the cases measured.

| Workflow | A direct | B as built | G every component on | D meta-harness | DG meta-harness from G |
|---|---|---|---|---|---|
| gsm8k | 21/100 | 21/100 | 54/100 | 81/100 | 54/100 |
| massive | 65/100 | 65/100 | 78/100 | 65/100 | 78/100 |
| mbpp | 24/40 | 24/40 | 23/40 | 24/40 | 23/40 |
| scifact | 64/100 | 64/100 | 57/100 | 64/100 | 57/100 |
| **pooled** | 174/340 | 174/340 | 212/340 | 234/340 | 212/340 |

| Tuning run | Outcome | Minutes | Canary hits |
|---|---|---|---|
| gsm8k-D | activated | 9.4 | 0 |
| gsm8k-DG | refused | 17.0 | 0 |
| massive-D | refused | 5.5 | 0 |
| massive-DG | refused | 7.2 | 0 |
| mbpp-D | refused | 8.3 | 0 |
| mbpp-DG | refused | 13.6 | 0 |
| scifact-D | refused | 4.1 | 0 |
| scifact-DG | no_winner | 7.0 | 0 |

B measured again last, from a freshly loaded model: the control that each arm's measurement starts from the same server state.

| Workflow | Identical outputs |
|---|---|
| gsm8k | 100/100 |
| massive | 100/100 |
| mbpp | 40/40 |
| scifact | 100/100 |

## What round 3 found

The question: does self-tuning that starts from every prebuilt component (DG) beat each mode alone? **No: the two modes
did not stack.** Every DG tuning run kept its starting point, so DG equals G on every dataset. Task by task, from
`rows/` (gained, lost):

| Workflow | G against A | D against A | D against G | DG against G |
|---|---|---|---|---|
| gsm8k | 43, 10 | 64, 4 | 34, 7 | 0, 0 |
| massive | 14, 1 | 0, 0 | 1, 14 | 0, 0 |
| scifact | 7, 14 | 0, 0 | 14, 7 | 0, 0 |
| mbpp | 1, 2 | 0, 0 | 2, 1 | 0, 0 |

Root causes, from `tuning/` and the method's code:

1. **GSM8K, DG refused.** From every component on, the reflector proposed the step-by-step instruction; it won 15,
   lost 6 on the selection split, then tied 10, 10 on the confirmation split, and the gate needs a win. From G6 as
   built (D), the same kind of instruction was approved (21 to 81); on top of every component on (54) it added nothing
   the gate could confirm.
2. **MASSIVE, D refused at wins 11, losses 4, p 0.059 (boundary 0.043 at look 4 of 4).** Its change, eight labelled
   examples with an instruction naming a few labels, came within a few cases of the gate; G already shows eight
   labelled examples (78).
3. **SciFact, G below the model alone (57 against 64; 7 gained, 14 lost), and DG's proposals inert.** G's pipeline has
   a second look at each of SciFact's three labels (`second_look_support`, `second_look_contradict`,
   `second_look_not_enough_info`, see `tuning/scifact-DG.json` `initial_profile`). `steps.second_look` asks the model
   again, with its own fixed question, whenever the answer names its label, so with all three every answer is asked
   again, and the profile's prompt cannot reach the final label. DG's three proposals were all prompts, including the
   registered study's winning SciFact instruction ("Use only the abstract, not outside knowledge"); each changed no
   case on the 60-case selection split (0, 0). One layer masked another.
4. **SciFact, D refused: an overfit caught.** The prompt plus a label-only check won 15, lost 1 on the 60 cases it was
   selected on, then went 3, 5 on the 40 confirmation cases (p 0.855); the gate refused it.
5. **MBPP, both refused for evidence, not for loss.** The pool had 133 fresh ids left (20 dev, 33 selection, 40
   confirmation, 40 dev-eval); the gate needs 30 held-out cases and 5 where base and candidate differ, and had 20 and 1
   (D), 30 and 2 (DG). Every arm is within one case of the others.

What it means for G6: the modes are alternatives, chosen per task on held-out evidence, not layers to switch on all at
once. A layer that re-decides the answer hides upstream improvements from the gate, so a component, like any change,
earns its place on the user's own examples. Not acted on: the method proposes one change at a time and never proposed
turning G's steps off; the experiments are frozen (owner, 2026-10-20), so this is recorded, not engineered.

Method: v0.1, `Method` digest `c82a805a01e3` (round 2's); `method_code_digest` `c80f75490826` differs from round 2's
only in `g6/metaharness/__init__.py`'s docstring and maturity label.
