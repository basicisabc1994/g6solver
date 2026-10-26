---
name: g6-adapt-healing
description: Use when one step of Python code fails with an error you have tests for, and an agent wants a model to repair it without a careless automatic fix; the error is triaged first, the patch is checked in the sandbox on tests the model sees, and it is kept only if it passes held-back tests the model never saw.
---

# G6 adapt_healing

Repairs one failing step. The original code first runs on the shown tests and its observed exception is classified, and only an error inside the code is repaired without a
person. A model you give the block proposes a patch; the patch runs with the tests it was shown in a bounded child
process, and the first that passes them is judged once on held-back tests whose expected answers never leave G6's
process. A repair is a `qualified-draft`; a patch that fits the shown tests and fails a held-back one is refused.

## When to use it

- A function raises, or returns wrong values, and you have a few tests that pin what it should do.
- An agent's loop would otherwise stop on a recoverable error in its own code.

## When not to use it

- With no held-back tests: it refuses, since a repair judged on tests the proposer saw proves nothing.
- For an error that touches files, the network or memory, or whose class is not a built-in one: it goes to a person.
- To run untrusted code safely: the sandbox bounds time, memory and network, and is not an operating-system sandbox.

## Ops

- `heal`: `code` and its `error` (a traceback, or a `Type: message` line), `tests_shown` (each one `assert` statement), `tests_held_back` (each `assert <expression> == <literal>`), `rounds` (1 to 3) and `timeout_s`; the repaired code, its rounds and the held-back verdicts. Sensitive.
- `check_tests`: whether the tests have the forms `heal` needs, whether a held-back test is also shown, and the error's triage; nothing runs.

## Reading the result

The supplied error is a claim. heal triages the original sandbox run, records source: original_run and its
reproduction evidence, and sends G6_E_HEAL_NEEDS_REVIEW when the failure is not reproduced or needs a person.
check_tests classifies only the claimed error; ready does not establish that it reproduces.

Each assertion is at most 16,000 UTF-8 bytes; shown and held-back tests together are at most 100,000 bytes.
Caps apply before parsing. RecursionError and MemoryError become G6_E_HEAL_BAD_TEST refusals.
Held-back results accept only exact None, bool, int, float, str, bytes, list, tuple, dict and set types,
with containers checked recursively. Other values fail the case and name their type; repr cannot establish a type.

A repair (`G6_E_HEAL_PATCHED`, `qualified-draft`) carries `patched`, `triage` (category, posture, reasons and hint
from `g6.triage`), `rounds` (each round's outcome: `shown_tests_pass`, `shown_tests_fail`, `does_not_parse` or
`too_large`, with the shown counts and the sandbox's limits), `held_back` (each test's index and `passed`) and
`spelled_answers`: literals the shown tests expect that the patch spells out and the original did not, which also
puts `overfit_to_examples` on the card.

Refusals, all `blocked-escalated`: `G6_E_HEAL_NEEDS_REVIEW` (the triage posture is not `auto_heal`; no model was
asked), `G6_E_HEAL_NO_HELD_BACK`, `G6_E_HEAL_HELD_BACK_SHOWN` (a held-back expression is also in a shown test),
`G6_E_HEAL_BAD_TEST`, `G6_E_HEAL_NO_MODEL` (no caller; over MCP, none configured), `G6_E_HEAL_MODEL_FAILED`,
`G6_E_HEAL_SHOWN_TESTS_FAIL` (no round passed the shown tests; the held-back tests were not run) and
`G6_E_HEAL_HELD_BACK_FAILED` (the patch, in `patched`, passed the shown tests and failed a held-back one).

## Alone and composed

From Python: `AdaptHealingBlock(caller=...)` with any `g6.core.llm` caller, then `heal`. Over MCP, `check_tests` always
runs; `heal` needs the server's owner to list it in `G6_MCP_SENSITIVE`, and then uses the model the person set in
`~/.g6/.env` (for example `G6_LLM_PROVIDER=claude_code`); with none it refuses with `G6_E_HEAL_NO_MODEL`.

Composed: `g6.triage` decides whether a fix may be tried; `meta_programming`'s `run_tests` runs your fuller suite on
`patched`; `coding_agent_gate` checks the change before it is reported done.

## Failure modes it catches

- `cannot_self_repair` (a recoverable error that stops the whole workflow): an error inside the code is repaired in up
  to three rounds, each patch checked on the shown tests and fed back their failures.
- `careless_auto_fix` (an automatic fix applied without checking what it changes): no fix is tried for an error that
  needs a person, every patch runs only in the sandbox, and a patch is kept only if it passes held-back tests whose
  answers it never saw, compared outside the code.

## Blind spots

- The checks ran in the patched code's own process; a patch that alters the runner can forge a pass.
  This limitation is carried in reply evidence and the warning card, even when all comparisons pass.
  The ceiling remains qualified-draft.

- `overfit_to_examples`: a patch can pass every held-back test you wrote and still be wrong on cases nobody wrote;
  `spelled_answers` names one that spells out the shown answers.
- The code under test can see the held-back expressions it is asked to compute, though never their answers.
- Shown tests and held-back expressions run as your code does, with your permissions, in the sandbox.
- A held-back value that is not a literal (an object) cannot be compared, so that test fails.

## What it needs

A model caller you pass from Python (`g6.core.llm`); nothing outside G6 and no extra. Tests run with G6's own
interpreter through `g6.core.sandbox`.

## Examples

```g6-example op=check_tests
{"tests_shown": ["assert mean([2, 4]) == 3"], "tests_held_back": ["assert mean([]) == 0", "assert mean([1, 2, 3, 6]) == 3"], "error": "ZeroDivisionError: division by zero"}
```

`ready` is true: one shown test, two held-back ones, and the error's posture is `auto_heal`.

```g6-example op=heal
{"code": "def mean(xs):\n    return sum(xs) / len(xs)\n", "error": "ZeroDivisionError: division by zero", "tests_shown": ["assert mean([]) == 0"], "tests_held_back": ["assert mean(()) == 0"]}
```

Sensitive; and with no model configured it is refused with `G6_E_HEAL_NO_MODEL`.

```python
from g6.code.adapt_healing import AdaptHealingBlock, AdaptHealingInput
from g6.core.llm import FakeLLM

fitted = "```python\ndef mean(xs):\n    return {(2, 4): 3}.get(tuple(xs), 0)\n```"  # a patch that learns the example
block = AdaptHealingBlock(caller=FakeLLM(default=fitted))  # stands in for your model
out = block.infer(AdaptHealingInput(op="heal", code="def mean(xs):\n    return sum(xs) / len(xs)\n",
                                    error="ZeroDivisionError: division by zero",
                                    tests_shown=("assert mean([2, 4]) == 3", "assert mean([]) == 0"),
                                    tests_held_back=("assert mean([1, 2, 3, 6]) == 3",))).unwrap()
assert out["code"] == "G6_E_HEAL_HELD_BACK_FAILED" and out["completion_state"] == "blocked-escalated"
```

## The four open problems

- **Learning signals**: each reply records the triage, every round's outcome, the held-back verdicts and any spelled
  answers. Not yet: incidents are not written to `g6.core.ledger`, so nothing learns which errors heal by class.
- **Evaluators the model cannot influence**: the held-back tests; the model never sees them, the patch never sees
  their answers, and the comparison happens in G6's own process. The returned values still come from a runner the patch can alter.
- **Transfer by failure mode**: bound to `cannot_self_repair` and `careless_auto_fix`; `g6.triage`'s classes carry
  the same posture for any code.
- **Enough held-out cases**: Not yet: one held-back test is accepted; how many make a repair trustworthy is not
  measured, so hold back several that cover the edge cases.
