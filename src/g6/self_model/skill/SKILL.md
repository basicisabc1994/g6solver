---
name: g6-self-model
description: Use when a runner needs inspectable measured state with freshness limits, discrepancy observations, and pre-action risk predictions that grant no authority.
---

# G6 self-model

## When to use it

Represent a runner's measured capabilities, limits, state, goals, commitments,
environment and authority with explicit sources and clocks. Compare observations
with expectations and simulate actions before the host decides what to execute.

## When not to use it

This package cannot observe the machine, execute an action, call a model or keep
a durable log. Do not use a prediction to clear a gate or as measured acceptance
evidence. Readers outside the package must gather actual measurements.

## Python API

- `model.Field`: a measured or predicted value, its source, timestamp, lifetime
  and body digest. Stale and future readings are unknown.
- `model.NamedField`: a named field in a snapshot section.
- `model.SelfModel`: immutable snapshot records with explicit-clock readings.
- `monitor.Observation`: a measured signal for comparison with expectations.
- `risk_gate.assess`: conservative routing from measured inputs and protected
  path information; it grants no action authority.
- `simulate.simulate`: pure forward predictions from a snapshot and explicit
  measured context, without executing an action.
- `simulate.score_after`: scores a recorded prediction using separate measured
  outcomes bound to the action and a recording receipt.

## Alone and composed

Use these Python records and functions without a block or `g6_invoke` op. The
distill runner gathers inputs, records simulation rows and owns execution.
Action-gate and navigator readouts must come from their existing APIs. The MCP
self-model route reads host-supplied snapshots; simulation refuses absent trusted
snapshot and context inputs.

## Failure modes it catches

- `stale_context_used`: explicit-clock reads return unknown outside a field's
  lifetime, and evidence reads reject self-predictions.
- `risk_management_failure`: risk routing refuses missing measured inputs and
  sends protected or consequential changes through the appropriate gate.

## Blind spots

- `overstated_certainty`: forecasts and historical comparisons can be wrong;
  the result describes supplied inputs, not an observed future.
- Provenance is declared, not authenticated. A caller can invent a source name
  or provide inaccurate measurements. The host must enforce trustworthy readers.
- Frozen records contain no independent sensor, permission service or custody
  boundary. A digest binds data and does not authenticate its author.

## What it needs

The installed G6 dependencies, explicit measurements and a finite clock. Risk
routing also needs a complete protected manifest and measured gate readouts.
Persistence and external observations belong to the host; this package does no
filesystem, model or network I/O.

## Examples

Read a snapshot offline and see a stale field become unknown:

```python
from g6.self_model.model import Field, NamedField, SelfModel

field = Field.measured(True, source="host.sensor", observed_at=10.0, ttl_s=5.0)
snapshot = SelfModel(state=(NamedField(name="ready", field=field),))
assert snapshot.read(now=12.0)["state"]["ready"] is True
assert snapshot.read(now=16.0)["state"]["ready"] == "unknown"
prediction = Field.predicted(True, source="host.forecast", observed_at=10.0, ttl_s=5.0)
assert prediction.read(now=12.0, evidence=True) == "unknown"
```

## The four open problems

- Learning signals: discrepancies and separately scored predictions provide
  observations; they confer no acceptance authority.
- Evaluators the model cannot influence: measurements and gate readouts must
  come from trusted external readers; predictions never clear the risk gate.
- Transfer by failure mode: historical comparisons describe their supplied
  action scope, without establishing performance on other tasks.
- Enough held-out cases: this package establishes no held-out performance;
  independent admission belongs to the acceptance evaluator.
