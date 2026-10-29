---
name: g6-algorithm-design
description: Use when a task needs an algorithm and you want catalogued textbook options that fit the stated problem, each with the preconditions it needs, the cost it claims and the proof obligations it leaves, bound to the requirements a change must keep, instead of a method invented on the spot or one called optimal without proof.
---

# G6 algorithm designer

`g6.algorithm_design` suggests algorithms from a small, inspectable catalogue for a problem you describe by its
representations, its wanted output and the properties you have established. A bounded search composes catalogue
patterns whose input and output types line up, and never assumes a precondition you did not state. `fuse` binds one
candidate to a change-impact map's frozen intent, so the check plan names both the algorithm's proof obligations and
your normative obligations. `estimate` says what each candidate would cost on a device you describe. And `run_kernel`
runs one of G6's 22 fixed algorithm kernels on your data, each checking its own answer a second way. Its best state is
`qualified-draft`: nothing it returns approves or runs a change, and a kernel's check covers that one answer.

## When to use it

- Before an agent writes an algorithm: `search` lists catalogued compositions that turn your representation into the
  output you want, with each step's claimed cost and the obligations a correct implementation must meet.
- When you want the textbook options for a named problem: `ontology` lists the bundled entries for it, with the
  features each needs and which of them you have not established.
- When a candidate must be checked against what a change must keep: `fuse` returns one check plan for both.
- When an input is within a kernel's bounds and you want the answer itself, such as a maximum flow, a shortest path
  or an exact knapsack: `run_kernel` returns it with its witness and how it was checked.

## When not to use it

- Proving an algorithm or an implementation correct: a catalogue entry and a claimed bound are not proof. Use `lean`
  or `formal` for a proof, and the repository's tests through `g6_review_patch` for an implementation.
- Problems outside the catalogue's nine patterns and the ontology's sixteen problems: the search finds nothing rather
  than guessing.

## Ops

- `primitives`: the nine catalogue patterns, each with its input and output representation, preconditions,
  postcondition, claimed cost and proof obligations.
- `ontology`: the bundled ontology's algorithms for `problem_id`, ranked by how many of their recognition features
  `established_properties` lacks (`limit`); metadata only.
- `search`: compositions of catalogue patterns from `representations` to `target`, within `max_depth`,
  `max_expansions` and `max_candidates`, with every pattern refused for a missing precondition listed.
  With `problem_id`, the catalogue gains the ontology's reviewed typed patterns for that problem.
- `estimate`: each of `algorithms` costed for `dimensions` (input sizes such as `n`, `V`, `E`, `W`) on `device`
  (`ram_bytes`, `scratch_bytes`, `max_operations` and what it allows), with the reasons one does not fit.
- `fuse`: the searched candidate `candidate_id`, bound to the frozen intent of `files`, `package`, `requirements`,
  `obligations` and `environment_digest`, and to the cone of `changed` when given.
- `list_kernels`: G6's 22 fixed kernels, all run through one entry point.
- `run_kernel`: the kernel `algorithm_id` on `parameters` (at most 16, plain data, named by the kernel's signature):
  its output, witness, complexity, limitations and `verification`, how that one answer was checked.

## Python API

The design mission, Python only and inert in this release: it stops before any test runs until an engineer is wired
in (the Claude Code and Codex engineers come with the mission over MCP).

- `autonomous.run_agent`: one bounded propose, test and repair loop over one file; nothing runs without a person's
  approval in the operator's queue bound to the mission's fingerprint, and every decision goes to a hash-chained
  ledger under the G6 home.
- `autonomous.DesignMission`: the frozen intent, scope, tests and limits; its `fingerprint` is what an approval binds.
- `autonomous.NoEngineer`: the engineer this release ships, which refuses (`G6_E_ALGO_NO_ENGINEER`).
- `autonomous.RepoTestsRunner`: every run asks for an operating-system boundary; the baseline is receipted, a
  candidate runs as a mutant and never is.

## Alone and composed

Alone over MCP: `g6_invoke("algorithm_design", "search", '{"representations": ["sequence"], "target":
"membership"}')`. No op is sensitive: nothing is written, nothing reaches the network, and the only code that runs is
G6's own kernels on plain data. From Python: `from g6.algorithm_design import DesignProblem, search_algorithms, fuse`
and `from g6.algorithm_design.invocation import invoke`.

How it is guarded:

- Your `established_properties` are your word: a pattern whose preconditions they do not cover is refused and
  listed under `rejected`, never assumed to hold.
- A kernel takes plain data only: a callable, an object or anything nested more than eight deep is refused before it
  runs. Input past a kernel's bound (for example a Held–Karp tour on more than 14 cities, or meet-in-the-middle
  subset sum on more than 32 weights) is refused at once with `G6_E_ALGO_KERNEL_REFUSED`.
- An algorithm with no hard-coded cost model is refused by `estimate` (`missing_hard_coded_cost_model`), never
  guessed; a device's limits are your word.
- Every candidate has `status: "candidate_only"`; `fuse` returns `approval: false` and
  `ready_for_unrestricted_action: false`, and lists `candidate_not_independently_verified` among its unknowns.
- The ontology is read as metadata (`implementation_status: "metadata_only"`), never as code to run.
- Requirements and obligations you send to `fuse` come back under `evidence[0]` with `declared_by: "caller"`; they
  decide which obligations the check plan names and never raise the state.

Composed:

- `complexity`: `fuse` builds its change-impact map and cone from the same file map.
- `workforce` and `coding_agent_gate`: the check plan becomes part of a coding mission's standard of done.

## Failure modes it catches

- `reinvented_method` (reimplementing a method that already exists): `search` and `ontology` name the catalogued
  algorithm for the stated problem before an agent writes one of its own.
- `unproven_optimum` (a plan called the best when nobody proved it): every candidate keeps its claimed bound as a
  claim, with the proof obligations a correct implementation must meet, and is never promoted past a proposal.

## Reading the result

- An answer is `qualified-draft`. Its `warning_card.code` says what it is: `G6_E_ALGO_CATALOGUE` (the patterns),
  `G6_E_ALGO_ONTOLOGY` (ontology entries), `G6_E_ALGO_PROPOSAL` (search candidates or a fused check plan),
  `G6_E_ALGO_ESTIMATE` (cost estimates) or `G6_E_ALGO_KERNEL_CHECKED` (one kernel's answer and how it was checked).
- `G6_E_ALGO_NO_CANDIDATE` is `blocked-escalated`: no composition reaches the target under the stated properties;
  `rejected` says which preconditions were missing.
- A refusal is `blocked-escalated` with `G6_E_ALGO_INPUT_INVALID`, `G6_E_ALGO_UNKNOWN_CANDIDATE`,
  `G6_E_ALGO_KERNEL_REFUSED` (no such kernel, input past its bound, or its own check refused its answer) or
  `G6_E_INPUT_VALIDATION`.

## Blind spots

- `hidden_assumption`: composition matches representation names only. That one step's output means what the next
  step expects is a proof obligation the answer lists, not something it checked.
- The catalogue is small and fixed; a better algorithm outside it is never suggested.
- Claimed costs are textbook bounds under their own models, and `estimate` counts abstract operations; neither is a
  measurement on your inputs or your machine.
- A kernel's check covers the answer it gave on the input it was given, not every input, and not the Python source.

## What it needs

Nothing outside G6: the standard library, the bundled metadata files and `complexity`; the design mission's Python
API also uses `workforce` and `coding_agent_gate`. No extra, no outside program, no model.

## Examples

The catalogue's patterns:

```g6-example op=primitives
{}
```

The bundled entries for interval scheduling, with nothing established yet:

```g6-example op=ontology
{"problem_id": "interval_scheduling"}
```

Search for membership in a sequence:

```g6-example op=search
{"representations": ["sequence"], "target": "membership"}
```

Bind that search's candidate (its `id`, a digest of the problem and its steps) to a small package's frozen intent and
the cone of a change to `shop.state`:

```g6-example op=fuse
{"representations": ["sequence"], "target": "membership", "candidate_id": "06e149b1966e29ebc10717adb4128dd1b823455f631ca46be5a57e5e2503c8bf", "files": {"shop/__init__.py": "", "shop/api.py": "from shop import state\ndef read():\n    return state.value\n", "shop/state.py": "value = 1\ndef update():\n    global value\n    value = 2\n"}, "package": "shop", "requirements": [{"id": "r1", "text": "Preserve authorization", "authority": "person_confirmed", "source": "person", "kind": "preserve"}], "obligations": [{"id": "o1", "requirement_id": "r1", "expectation": "No cross-account revocation", "oracle_source": "person", "normative": true}], "environment_digest": "env-v1", "changed": ["shop.state"]}
```

Cost two algorithms for 100 items and a weight of 1,000 on a small device that refuses pseudo-polynomial time:

```g6-example op=estimate
{"algorithms": ["knapsack_dp", "sort_comparison"], "dimensions": {"n": 100, "W": 1000}, "device": {"ram_bytes": 8000000, "max_operations": 1000000, "allow_pseudopolynomial": false}}
```

G6's fixed kernels, and one of them run on a small flow network:

```g6-example op=list_kernels
{}
```

```g6-example op=run_kernel
{"algorithm_id": "edmonds_karp", "parameters": {"n": 4, "edges": [[0, 1, 3], [0, 2, 2], [1, 3, 2], [2, 3, 3], [1, 2, 1]], "sink": 3}}
```

## The four open problems

- **Learning signals**: every candidate carries a digest of its problem and steps, so a later run can say which one
  it implemented. Not yet: nothing records whether an implemented candidate passed its checks.
- **Evaluators the model cannot influence**: the catalogue and the search are fixed code; a model can only choose
  among candidates, never add a pattern or a precondition. Not yet: the catalogue's entries are reviewed by hand.
- **Transfer by failure mode**: the guard is bound to `reinvented_method` and `unproven_optimum`, not to one problem:
  any problem stated in the catalogue's representations is searched the same way. Not yet: how often a suggested
  candidate was the right one is not measured.
- **Enough held-out cases**: Not yet: the search is checked by fixed tests, not by G6's sequential test on held-out
  tasks.
