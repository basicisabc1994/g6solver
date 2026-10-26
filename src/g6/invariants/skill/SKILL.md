---
name: g6-invariants
description: Use when a result envelope (from a G6 component, your own tool or another agent) is about to be trusted, passed on or asserted in a test, and you need to know whether its completion_state, warning_card, evidence, request_id and run_id agree with one another, or whether a set of checks passed only because none of them ran.
---

# G6 invariants

Checks that a result envelope says only what its own fields support. `check_envelope` lists every rule an envelope
breaks, `assert_envelope` raises with that list for tests, and `nonvacuous_pass` refuses a pass from checks that
checked nothing. It checks consistency, not truth.

## When to use it

- Writing tests for a component or a tool that returns an envelope: `assert_envelope(reply)`.
- A result from another tool or agent says `verified`: run `check_envelope` before passing it on. A `verified` with
  no evidence is caught.
- Summing a list of checks into one pass or fail: `nonvacuous_pass`, so an empty list, or one with only advisory
  checks, is not a pass.

## When not to use it

- Whether the evidence is real, or supports the claim: `evidence` (the certificate issuer), `coding_agent_gate`
  or `grounding`.
- Building an envelope: `g6.core.reliability_envelope.envelope_fields` and `warning_card`.

## Python API

- `check_envelope`: every rule a payload breaks, as `Violation` rows; empty when it breaks none.
- `assert_envelope`: raise `AssertionError` naming every rule a payload breaks.
- `nonvacuous_pass`: from `(passed, blocking)` pairs, true only when at least one blocking check ran and every blocking check passed.
- `Violation`: one broken rule: `rule`, `field` and `message`.
- `RELIABILITY_LABELS`: the three states a `completion_state` may take: `verified`, `qualified-draft`, `blocked-escalated`.
- `ERROR_CODE_PATTERN`: the pattern of a stable error code, `G6_E_` and capitals, digits and underscores.

## Reading the result

The rules, by name:

- `not_an_envelope`: the payload is not a mapping.
- `unknown_state`: `completion_state` is not one of the three states.
- `label_mismatch`: `reliability_label` disagrees with `completion_state`.
- `missing_warning_card`, `unstable_code`: a result that is not `verified` needs a `warning_card` whose `code`
  matches `ERROR_CODE_PATTERN`.
- `verified_without_evidence`: a `verified` result needs non-empty `evidence`.
- `missing_id`: the `request_id` or the `run_id` key is absent.
- `degraded_mismatch`: `degraded`, when present, must be true exactly when the result is not `verified`.

## Alone and composed

From Python only: `from g6.invariants import check_envelope, assert_envelope, nonvacuous_pass`. It has no block and
no MCP op.

Composed:

- The MCP server keeps a component's own warning card only when its code matches `ERROR_CODE_PATTERN`; any other
  card is replaced with one of its own.
- Run it on any component's envelope: `safety_engine`'s `TestWeakeningReport.to_envelope()` and the envelope fields
  of a `coding_agent_gate` decision both pass it. A result dictionary with a `completion_state` but no envelope
  fields (for example `GateDecision.to_dict()`) fails with `missing_warning_card` and `missing_id`; one without a
  `completion_state` fails with `unknown_state` and `missing_id`.
- `evidence` decides a certificate's state; this checks that the envelope carrying a state is consistent with it.

## Failure modes it catches

- `overstated_verification` (work called verified when the evidence does not support it): a `verified` with no
  evidence, a label that disagrees with the state, a result below `verified` without a `G6_E_*` warning card, and a
  `degraded` flag that contradicts the state are each named; `nonvacuous_pass` refuses a pass when no blocking check
  ran.

## Blind spots

- Consistency, not truth: a `verified` envelope with fabricated evidence passes.
- It checks that `request_id` and `run_id` keys exist, not that their values mean anything (empty strings pass).
- It does not look inside the evidence items.
- It declares no failure mode of its own.

## What it needs

Nothing outside G6: pure standard-library Python.

## Examples

```python
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.invariants import assert_envelope, check_envelope, nonvacuous_pass

draft = envelope_fields(reliability_label="qualified-draft", request_id="q1", run_id="r1",
                        warning_card=warning_card("G6_E_CHECK_NOT_RUN", "the check did not run"))
assert check_envelope(draft) == []
assert_envelope(draft)

claimed = {"completion_state": "verified", "evidence": [], "request_id": "q1", "run_id": "r1"}
assert [v.rule for v in check_envelope(claimed)] == ["verified_without_evidence"]

assert nonvacuous_pass([]) is False  # nothing ran
assert nonvacuous_pass([(True, False)]) is False  # only advisory checks ran
assert nonvacuous_pass([(True, True), (False, False)]) is True  # an advisory failure does not block
```

## The four open problems

- **Learning signals**: Not yet: it writes nothing. Each violation is a named rule on a named field, a labelled
  record a caller can keep.
- **Evaluators the model cannot influence**: the rules are fixed code with no model call, and they read only the
  envelope. Not yet: the code is not digest-pinned.
- **Transfer by failure mode**: it is bound to `overstated_verification` and to the envelope's fields, not to a
  task, so it applies unchanged to any surface that returns the envelope. Not yet: no measurement on unseen task
  families.
- **Enough held-out cases**: Not yet: it checks one envelope at a time; nothing measures how often a broken
  envelope reaches it.
