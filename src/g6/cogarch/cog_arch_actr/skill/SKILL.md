---
name: g6-cog-arch-actr
description: Use when an agent keeps many memories and needs them ranked the way human memory ranks them, by how recently and how often each was used and by the current context, with a recall refused when nothing is strong enough, or when a small rule set must consult that memory step by step and be judged by a goal test.
---

# G6 cog_arch_actr

ACT-R declarative memory for agents, with a small procedural module. A memory keeps chunks (a type and slots). Each
chunk's activation comes from every presentation of it (its entry, the same chunk added again, each successful
retrieval), decaying with time, plus spreading from a context and optional seeded noise. A retrieval returns the
matching chunk with the highest activation and the time it took, or refuses when the best match is below the
threshold. Every presentation and utility is written to the memory's file, so learning accumulates across calls.
Each call locks its named store across load, change and save, then replaces it from a unique temporary file in the
same folder. The exclusive lock waits at most 5 seconds; locks older than 300 seconds are cleared as stale.
An expired or replaced lease refuses the write with `G6_E_ACTR_STORE_BUSY`; retry the call from a fresh load.

## When to use it

- Ranking an agent's notes by recency, frequency and context, with an explicit "nothing recalled" below a threshold
  instead of always returning something.
- Modelling forgetting and the fan effect: the more facts share a cue, the less each is recalled from it.
- Small production models (counting, look-ups, step-by-step procedures) that ask memory for facts and are judged by a
  goal test.

## When not to use it

- As a validated cognitive model of a person: the parameters are hand-set and fitted to no data. For full ACT-R
  models use the reference Lisp ACT-R or pyactr.
- As a search engine: it matches slots exactly; for relevance to free text use `ctx_ace` or `g6.grounding`.
- As evidence that a recalled memory is true: activation says how strongly it is remembered, not whether it is right.

## Ops

- `add_chunk`: keep a chunk (`name`, `isa`, `slots`); the same chunk again, by name or by content, is another presentation of it.
- `retrieve`: the matching chunk with the highest activation and its breakdown and latency, or a refusal below the threshold; a retrieval is recorded as a presentation.
- `add_production`: keep a production: `=goal` and `=retrieval` tests with `=variables` and `-negated` slots, `?retrieval` `error`, `empty` or `full`; actions `=goal`, `-goal`, `-retrieval` and `+retrieval`.
- `run`: fire productions from `goal` until `goal_test` passes against the goal buffer, with a trace of every cycle.
- `reward`: utility learning for each production used since the last reward.

## The equations

From the ACT-R 7.30+ Reference Manual; `source_register.json` in the package holds each clause with its page.

- Activation `A = B + S + noise`. Base level `B = ln(sum of t_j^-d)`, `t_j` the age in seconds of each presentation
  (at least 0.05 s, one production firing), `decay` `d` 0.5.
- Spreading (off unless `max_strength` `S` is set, as in ACT-R): each `context` value `j` adds
  `W/n x (S - ln fan)`, `fan = (1 + slots holding j in memory) / (this chunk's slots holding j)`; a negative strength
  counts as 0 and is marked. `context_activation` `W` is 1.0; in a run the goal buffer is the context, with
  `goal_activation` 0.0 by default.
- Noise: logistic with scale `noise` (0, off), seeded: the memory's draw count and `seed` fix each draw.
- Retrieval: the highest activation at or above `threshold` (0.0) is recalled, in `latency_factor x e^(-latency_exponent x A)`
  seconds (both 1.0); a failure takes `F x e^(-f x threshold)`.
- Utility: `U + alpha (R - U)`, `alpha` 0.2, `R` the reward less the time since the production was selected; among
  matching productions the highest utility fires (ties by name), with optional `utility_noise`.

## Reading the result

`retrieve` gives `chunk`, `breakdown` (`base_level`, `spreading` with its terms, `noise`, `activation`,
`presentations`), `latency_s` and the top `competitors`, `G6_E_ACTR_RETRIEVED`, `qualified-draft`. Refusals:
`G6_E_ACTR_BELOW_THRESHOLD` (with the best match's breakdown), `G6_E_ACTR_NO_MATCH`. `run` gives `trace` (each cycle's
production, bindings, conflict set, goal and retrieval) and `G6_E_ACTR_GOAL_REACHED`; or `G6_E_ACTR_STUCK`,
`G6_E_ACTR_GOAL_CLEARED`, `G6_E_ACTR_CAP` (`cap` `max_cycles` 100, at most 10,000, or `time_limit_s` 10, at most 60).
Also `G6_E_ACTR_KEPT`, `G6_E_ACTR_PRODUCTION_KEPT`, `G6_E_ACTR_PRODUCTION_INVALID` (with `problems`),
`G6_E_ACTR_PRODUCTION_EXISTS`, `G6_E_ACTR_CHUNK_EXISTS`, `G6_E_ACTR_REWARDED`, `G6_E_ACTR_NOTHING_TO_REWARD`,
`G6_E_ACTR_FULL`, `G6_E_ACTR_STORE` (read or write failure), `G6_E_ACTR_STORE_BUSY` (lock timeout),
`G6_E_ACTR_NUMERIC` (latency overflow or non-finite latency) and `G6_E_ACTR_INPUT`.

`time_limit_s` also bounds retrieval scanning, including retrievals inside a production run. Whole-memory fan
counts are computed once per retrieval, with deadline checks during counting and candidate scoring. Exceeding the
deadline gives `G6_E_ACTR_CAP`, `cap: time_limit_s`; an interrupted retrieval is not saved. Extreme combinations of
`threshold` and `latency_exponent` can exceed floating-point range and are refused without returning a chunk.

## Alone and composed

Alone: `memory` names a memory (a file under G6's state folder; `default` if omitted). Times are seconds on the
computer's clock, so a fact added hours ago has a low base level: set `threshold` to suit (a few presentations an hour
apart stay above about -4). Composed: an agent keeps what it learns with `add_chunk`, asks with `retrieve`, and on a
refusal looks the fact up at its source (`knowledge_fetcher`, `g6.grounding`) and adds it again.

## Failure modes it catches

- `unmodelled_recall`: a recall model that ignores fading and interference. Activation falls with time since each
  presentation, rises with use, and is shared out among the facts a cue points to, and a recall below the threshold
  is refused rather than returned.

## Blind spots

- `under_retrieval`: a relevant memory below the threshold is refused; the refusal shows its activation, but it is
  not returned.
- The parameters are ACT-R's defaults or the caller's, fitted to nothing.
- No partial matching: a request matches slots exactly. A retrieved chunk stays in the retrieval buffer until the next
  request (no strict harvesting), and a retrieval completes before the next cycle.
- A chunk keeps its latest 5,000 presentations; older ones are counted but no longer add to its base level.
- A reward is the caller's number, kept as given, and is taken as given at the memory's latest time.

## What it needs

Nothing outside G6: no key, no outside program, no extra, no network. Each memory is a file under G6's state folder.

## Examples

```g6-example op=add_chunk
{"memory": "skill-example", "name": "refund-days", "isa": "policy", "slots": {"topic": "refund", "days": 14}}
```

```g6-example op=retrieve
{"memory": "skill-example", "request": {"isa": "policy", "topic": "refund"}, "threshold": -10}
```

The breakdown shows a base level from its presentations; each retrieval adds one.

```g6-example op=add_production
{"memory": "skill-example", "name": "ask", "conditions": {"=goal": {"isa": "question", "topic": "=t", "state": null}}, "actions": {"=goal": {"state": "asked"}, "+retrieval": {"isa": "policy", "topic": "=t"}}}
```

```g6-example op=add_production
{"memory": "skill-example", "name": "read", "conditions": {"=goal": {"isa": "question", "state": "asked"}, "=retrieval": {"isa": "policy", "days": "=d"}}, "actions": {"=goal": {"answer": "=d", "state": "done"}}}
```

```g6-example op=run
{"memory": "skill-example", "goal": {"isa": "question", "topic": "refund"}, "goal_test": {"answer": 14}, "threshold": -10}
```

`ask` asks memory for the refund policy, `read` copies its days into the goal, and the goal test passes.

```g6-example op=reward
{"memory": "skill-example", "reward": 5}
```

```python
from g6.cogarch.cog_arch_actr import CogArchActrBlock, CogArchActrInput

block = CogArchActrBlock()
block.infer(CogArchActrInput(op="add_chunk", memory="python-example", name="code", isa="door", slots={"pin": 4721}))
out = block.infer(CogArchActrInput(op="retrieve", memory="python-example", request={"isa": "door"},
                                   threshold=50)).unwrap()
assert out["code"] == "G6_E_ACTR_BELOW_THRESHOLD" and out["completion_state"] == "blocked-escalated"
```

## The four open problems

- **Learning signals**: retrieval outcomes (each success is a presentation) and rewards to productions; both are
  written to the memory's file. Not yet: nothing fits the parameters from them.
- **Evaluators the model cannot influence**: a run is judged only by the caller's goal test against the final goal
  buffer; no model takes part.
- **Transfer by failure mode**: bound to `unmodelled_recall` ("recall of a stale or wrong memory").
- **Enough held-out cases**: Not yet: the tests are recall tasks with known answers and known values worked by hand
  from the equations, not human recall data.
