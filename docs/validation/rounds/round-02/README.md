# Development round 2

**Development, not a validation.** Nothing here is a claim; a registered study (V7) is the only place a result is claimed.

Task model `llama3.1:8b`; reflector `gpt-oss:20b`; method v0 digest `c82a805a01e3`. Dev-eval cases per workflow: 100, fresh from the pool ledger.

| Workflow | A direct | B as built | H hand-written | D meta-harness | F random edits |
|---|---|---|---|---|---|
| arc | 82/100 | 82/100 | 76/100 | 82/100 | 82/100 |
| banking77 | 52/100 | 52/100 | 54/100 | 81/100 | 78/100 |
| gsm8k | 26/100 | 26/100 | 85/100 | 90/100 | 26/100 |
| massive | 67/100 | 67/100 | 47/100 | 74/100 | 67/100 |
| mbpp | 61/100 | 61/100 | 57/100 | 61/100 | 61/100 |
| **pooled** | 288/500 | 288/500 | 319/500 | 388/500 | 314/500 |

| Tuning run | Outcome | Minutes | Canary hits |
|---|---|---|---|
| arc-D | refused | 3.5 | 0 |
| arc-F | refused | 0.7 | 0 |
| banking77-D | activated | 6.0 | 0 |
| banking77-F | activated | 2.3 | 0 |
| gsm8k-D | activated | 8.9 | 0 |
| gsm8k-F | refused | 7.6 | 0 |
| massive-D | activated | 1.7 | 0 |
| massive-F | refused | 2.1 | 0 |
| mbpp-D | refused | 14.5 | 0 |
| mbpp-F | refused | 11.6 | 0 |

B measured again last, from a freshly loaded model: the control that each arm's measurement starts from the same server state.

| Workflow | Identical outputs |
|---|---|
| arc | 100/100 |
| banking77 | 100/100 |
| gsm8k | 100/100 |
| massive | 100/100 |
| mbpp | 100/100 |
