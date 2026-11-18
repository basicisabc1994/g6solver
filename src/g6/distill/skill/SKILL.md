---
name: g6-distill
description: Use when repeated failures need training-only method proposals, static observations of test changes, and a library whose admission is decided by a separate acceptance gate.
---

# G6 distill

## When to use it

Keep reported failures, inspect changes in explicitly supplied Python sources,
and propose bounded test-review methods from independent training failures.
Admission, promotion and withdrawal belong to the acceptance gate. The research
runner requires trusted host capabilities and owner-configured authority.

## When not to use it

A source summary alone cannot decide test adequacy. Do not supply held-out
content to synthesis, use self-observations as independent labels, or treat a
proposal as an admitted method. Fix a single known defect directly.

## Python API

- `facts.extract_facts`: summarises syntax, assertions, literals, parse errors
  and diffs from explicit before/after mappings, with an allowed split.
- `detectors.run_template`: runs a named static seed over explicit mappings;
  seeds have A0 authority and inherit the weakening scanner's limitations.
- `ledger.Stores`: typed append-only record stores under the configured G6 home.
- `synthesis.Synthesizer`: bounded proposals with an injected model caller;
  no implicit provider discovery.
- `acceptance_gate.decide`: independent acceptance against a frozen registration.
- `library.Library`: reads library versions and authority history.

## Alone and composed

Use the Python API for source observations without execution. There is no block
or `g6_invoke` op. Dedicated MCP routes record failures, start distillation jobs,
read the library and ask the person about library changes. The acceptance gate
reuses the meta-harness statistics and the existing confined repository runner.
The research runner joins synthesis, a self-model and independent admission;
public request fields cannot supply host capabilities or acceptance receipts.

## Failure modes it catches

- `test_suite_missing_or_weak`: static seeds flag the existing weakening
  scanner's categories, including changed checks and skip additions.
- `unhelpful_lesson`: a candidate earns library authority only through independent
  registered acceptance, regression checks and the gate's statistical bars.

## Blind spots

- `overfit_to_examples`: training examples and static heuristics can miss new
  forms of weakening. Static seeds can also raise false alarms.
- Record provenance is supplied data. Hashes detect changed bodies, not the
  truth of a source or the independence of its author.
- Python module checks do not isolate hostile code running as the same OS user.
  The host must protect evaluator custody and enforce execution boundaries.

## What it needs

Static observations need only the installed G6 dependencies and explicit source
text. Persistent APIs need writable G6 state. Acceptance needs frozen owner
registrations, custody, Git and the existing OS confinement backend. Synthesis
needs a separately supplied caller; any model consent and budget still apply.
Research is off without owner authority and trusted host capabilities.

## Examples

Inspect an explicit training change offline, without a model or execution:

```python
from g6.distill.facts import extract_facts

before = {"test_sum.py": "def test_sum():\n    assert 1 + 1 == 2\n"}
after = {"test_sum.py": "def test_sum():\n    pass\n"}
facts = extract_facts(before, after, split="training")
assert facts["diff_hunks"]["test_sum.py"]
assert facts["parse_errors"] == {}
```

## The four open problems

- Learning signals: independent outcomes and labelled field observations, not
  a proposer's claimed success, supply credit.
- Evaluators the model cannot influence: the acceptance gate and host custody
  own evaluation; proposals contain methods rather than labels or policies.
- Transfer by failure mode: methods are scoped to a family and contract;
  applicability outside that scope is not established.
- Enough held-out cases: the acceptance policy refuses insufficient samples;
  no amount of training success replaces the required independent evidence.
