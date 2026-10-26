# Development round 1

**Development, not a validation.** Nothing here is a claim; a registered study (V7) is the only place a result is claimed.

Task model `llama3.1:8b`; reflector `gpt-oss:20b`; method v0 digest `c82a805a01e3`. Dev-eval cases per workflow: 100, fresh from the pool ledger.

| Workflow | A direct | B as built | H hand-written | D meta-harness | F random edits |
|---|---|---|---|---|---|
| arc | 86/100 | 86/100 | 85/100 | 86/100 | 86/100 |
| banking77 | 48/100 | 48/100 | 56/100 | 82/100 | 83/100 |
| gsm8k | 24/100 | 23/100 | 78/100 | 83/100 | 85/100 |
| massive | 66/100 | 66/100 | 58/100 | 70/100 | 70/100 |
| mbpp | 62/100 | 61/100 | 57/100 | 60/100 | 61/100 |
| scifact | 71/100 | 73/100 | 73/100 | 75/100 | 71/100 |
| **pooled** | 357/600 | 357/600 | 407/600 | 456/600 | 456/600 |

| Tuning run | Outcome | Minutes | Canary hits |
|---|---|---|---|
| arc-D | refused | 2.6 | 0 |
| arc-F | no_winner | 0.2 | 0 |
| banking77-D | activated | 4.9 | 0 |
| banking77-F | activated | 0.8 | 0 |
| gsm8k-D | activated | 6.3 | 0 |
| gsm8k-F | activated | 4.2 | 0 |
| massive-D | no_winner | 1.6 | 3 |
| massive-F | no_winner | 0.4 | 0 |
| mbpp-D | no_winner | 6.5 | 0 |
| mbpp-F | no_winner | 2.0 | 0 |
| scifact-D | refused | 3.2 | 0 |
| scifact-F | refused | 0.8 | 0 |

## Root causes (written after the round, 2026-10-19)

**What the round shows.** Where the declared options held a gain, G6 engineered it itself: GSM8K 23 to 83 and
banking77 48 to 82 of 100, each change proposed, tested on held-out cases and approved by G6's own loop. Random edits
from the same options did as well (85 and 83), and that is the main finding to fix.

**Every single change, measured.** Each one-field change from G6 as built (6 to 10 per workflow), run on the 20 dev
cases and the 100 dev-eval cases from a freshly loaded model (all 52 in `single_change_sweep.json`):

| Workflow | Best single change | Dev-eval, as built → with it | Dev, as built → with it | D | F |
|---|---|---|---|---|---|
| gsm8k | a step-by-step prompt | 24 → 88 | 3 → 17 | 83 | 85 |
| banking77 | 8 labelled examples | 47 → 83 | 16 → 18 | 82 | 83 |
| massive | 8 labelled examples | 68 → 79 | 18 → 19 | 70 | 70 |
| mbpp | repair with tests | 60 → 66 | 8 → 8 | 60 | 61 |
| scifact | none above noise (best 73) | 71 | 16 | 75 | 71 |
| arc | none (86 is the ceiling here) | 86 | 19 | 86 | 86 |

Where the head profile stayed G6 as built (massive, mbpp, scifact and arc), D and F differ from B only by server noise
(cause 3).

**Cause 1: the canary's false alarm (fixed in 55642651cc).** The held-out MASSIVE utterance "s." is in the reflection
prompt's own wording ("...cases that already pass."), so every reflector call matched it. No held-out input can reach
the reflector. The audit also found that quoted held-out inputs would be missed, since the reflector reads its cases as
JSON. The canary now matches both forms and counts, as `canary_blind`, the inputs it cannot tell apart from allowed text
(2 in round 1's splits).

**Cause 2: tuning matched random edits.** The declared options give 6 to 10 single changes per workflow, and random
edits at D's budget try nine, so both arms reach the same single-change gains; the reflector's own prompt rewrites never
beat a declared option on the dev split. Two limits of the method (v0) cost two real gains:

- *A lone candidate races one block.* On MASSIVE both arms proposed the 8-examples change, but racing stops once one
  candidate is left after a block (`select.py`), so it ran 10 of the 60 selection cases, saw 0 wins and 0 losses, and
  was not confirmed.
- *A tie on dev is never raced.* On MBPP, repair-with-tests tied G6 as built on the 20 dev cases (8 and 8), and a
  candidate must beat its parent on dev to go to racing (`reflect.py`), so a 6-case gain on dev-eval was never tested.

**Cause 3: the model server's prompt cache (fixed in fee225f3bf).** At temperature 0 with a fixed seed, an output still
depends on the requests before it: one unrelated prompt first changed 4 of 100 MASSIVE outputs. B ran straight after A;
D and F ran after their tuning runs. Every arm is now measured from a freshly loaded model, and B is measured twice as
the control.

**Round 2.** The two method limits are fixed by hand (a lone candidate keeps racing to the end of the selection split;
a candidate that ties its parent on dev goes to racing), which makes a new v0 with its digest recorded (design §4.3).
Round 2 runs on GSM8K, MASSIVE, ARC and banking77, keeping MBPP's 393 and SciFact's 445 fresh cases for the registered
study, and then takes the first self-evolution step: v0 against the method change its own diagnosis proposes, over
paired tuning runs on both task models.
