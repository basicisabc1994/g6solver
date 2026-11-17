## Select route

- **select.** Not sure which G6 step fits, or a call was refused? `g6_select` with the task, your phase and the
  refusal returns checked calls with templates; nothing runs. When a G6 reply carries `repair`, follow its step; if
  `by` is person, tell the person and stop.

## Find

A tool named here that your server does not list is absent: say so, use `g6_find`, and do not call it again.

Read `g6_allowances` before optional fan-out, and declare useful independent work honestly. Its advice is pacing
only: stale or missing data never justifies more use.

Call `g6_help`, then `g6_find` with the task or failure mode. Read `g6_describe` before choosing fields or an operation.
`g6_components` lists every installed component. These three answer without any sharing grant for G6's built-in
components; an installed extension's text, and the person's own data, are withheld or left out until the person
grants that source. `g6_select` needs no grant either. Use grounding for source support, coding_agent_gate for declared
coding evidence, action_gating before consequential actions, and the meta-harness for repeated failures.
A direct call serializes its parameter object into `params_json`:

```text
g6_invoke("grounding", "check_claim", {"claim": "The limit is 42 units.", "sources": sources})
g6_invoke("grounding", "check_claims", {"text": "The limit is 42 units.", "sources": sources})
g6_invoke("grounding", "coverage", {"text": "The limit is 42 units.", "sources": sources})
```

`sources` maps names to supplied text. These lexical checks do not establish factual truth.
