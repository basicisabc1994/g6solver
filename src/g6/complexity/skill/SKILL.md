---
name: g6-complexity
description: Use when a coding agent is about to change a Python package and you need to know, before it runs anything, which modules, shared state and side effects the change can reach, which of your stated requirements it must keep, and which pieces of state you marked avoidable could be simplified.
---

# G6 change-impact map

`g6.complexity` reads a Python package's source from a file map you send, without importing or running it, and
builds a map of it: its modules and functions, the imports and calls between them, the module-level and instance
state they share, and the reads, writes and input or output they may perform. From that map it lists everything a
change could reach and the requirements the change must keep. Its best state is `qualified-draft`: the map is a
static reading of the text you sent, never a test of the code.

## When to use it

- Before a coding agent edits a package: ask `impact` which modules a change to one of them can reach, and which of
  your normative obligations every candidate patch must still meet.
- When a premortem needs to know which parts of a repository are risky: `atlas` lists shared mutable state, global
  writes and every call or import it could not resolve.
- When you have marked a piece of state as avoidable: `simplify` proposes a narrower owner or a derived view for it,
  with the checks a change must pass.

## When not to use it

- Deciding whether a patch is correct: use `g6_review_patch`, which runs the repository's tests in confinement.
- Code that is not Python: the map reads Python source only; other files count toward the tree's digest and are
  otherwise skipped.
- Dynamic code (reflection, `eval`, plugins loaded by name): each such call is listed as an unknown, not followed.

## Ops

- `atlas`: the map of `package` in `files` (`files`, `package`, `requirements`, `obligations`,
  `environment_digest`): components, dependencies, state, effects, findings, unknowns, and the fingerprint that binds
  them to the exact tree, intent and environment.
- `impact`: the reverse cone of `changed` (module or function names): every component that imports, calls or shares
  written state with them, the normative obligations the change must keep, and what widens the uncertainty.
- `simplify`: proposals for state you marked `accidental_avoidable` in `state_annotations`, each with its required
  checks; proposals only, never applied.

## Alone and composed

Alone over MCP: `g6_invoke("complexity", "impact", '{"files": {...}, "package": "shop", "changed": ["shop.state"],
"environment_digest": "env-v1"}')`. No op is sensitive: nothing runs, nothing is written. From Python:
`from g6.complexity import build_atlas, impact_cone, propose_simplifications`.

How it is guarded:

- A requirement's `authority` and an obligation's `normative` flag are your word. They are returned under
  `evidence[0]` with `declared_by: "caller"` and decide which obligations a change must keep; they never raise the
  state. A `model_hypothesis` or `adopted_unconfirmed` requirement cannot carry a normative obligation: that input
  is refused (`G6_E_COMPLEXITY_INPUT_INVALID`).
- Missing edges widen uncertainty; they never prove isolation. `parallel_safe` is always false, because nothing
  here proves two changes independent, and `complete` is true only when nothing was left unknown.
- With no scope map, `impact` keeps every normative obligation rather than dropping one outside the cone.
- At most 500 files, 1,000,000 characters a file and 4,000,000 characters in all; more is refused before anything is
  parsed.

Composed:

- `algorithm_design`: `fuse` binds a searched algorithm to this map's frozen intent and its cone.
- `workforce` and `coding_agent_gate`: the cone and the obligations become a mission's standard of done; the
  map itself never approves a change.

## Failure modes it catches

- `outside_scope` (a change that alters behaviour outside the part it was meant to touch): `impact` names every
  module and function a change can reach through imports, calls and shared writable state, so a patch's reach is
  known before it runs.
- `unmanaged_complexity` (code growing more complex than anyone can safely change): `atlas` lists shared mutable
  globals, instance state and unresolved calls, and `simplify` proposes a narrower owner for state you marked
  avoidable, each with the checks it must pass.

## Reading the result

- An answer is `qualified-draft`. Its `warning_card.code` says which op answered: `G6_E_COMPLEXITY_ATLAS`,
  `G6_E_COMPLEXITY_IMPACT` or `G6_E_COMPLEXITY_SIMPLIFY`.
- A refusal is `blocked-escalated` with `G6_E_COMPLEXITY_INPUT_INVALID` (and why), or `G6_E_INPUT_VALIDATION`
  when a field is the wrong type or too large.
- `unknowns` lists every parse failure, external import, dynamic or input-output call and unresolved call; read it
  before trusting a short cone.

## Blind spots

- `false_confidence`: a short cone or an empty list of findings reads as reassurance, yet it is a static reading of
  the text sent. Calls through variables, attributes of other objects, decorators and imports done at run time are
  not followed; they show only as unknowns.
- It reads the files you send. A module left out of the map is invisible to it.
- `simplify` proposes; whether a simplification keeps behaviour is for the tests it names.

## What it needs

Nothing outside G6: the standard library's `ast` and `design_governance`'s import graph. No extra, no outside
program, no model.

## Examples

Map a small package, with one person-confirmed requirement:

```g6-example op=atlas
{"files": {"shop/__init__.py": "", "shop/api.py": "from shop import state\ndef read():\n    return state.value\n", "shop/state.py": "value = 1\ndef update():\n    global value\n    value = 2\n"}, "package": "shop", "requirements": [{"id": "r1", "text": "Preserve authorization", "authority": "person_confirmed", "source": "person", "kind": "preserve"}], "obligations": [{"id": "o1", "requirement_id": "r1", "expectation": "No cross-account revocation", "oracle_source": "person", "normative": true}], "environment_digest": "env-v1"}
```

What a change to `shop.state` can reach:

```g6-example op=impact
{"files": {"shop/__init__.py": "", "shop/api.py": "from shop import state\ndef read():\n    return state.value\n", "shop/state.py": "value = 1\ndef update():\n    global value\n    value = 2\n"}, "package": "shop", "requirements": [{"id": "r1", "text": "Preserve authorization", "authority": "person_confirmed", "source": "person", "kind": "preserve"}], "obligations": [{"id": "o1", "requirement_id": "r1", "expectation": "No cross-account revocation", "oracle_source": "person", "normative": true}], "environment_digest": "env-v1", "changed": ["shop.state"]}
```

A proposal for state you marked avoidable:

```g6-example op=simplify
{"files": {"shop/__init__.py": "", "shop/api.py": "from shop import state\ndef read():\n    return state.value\n", "shop/state.py": "value = 1\ndef update():\n    global value\n    value = 2\n"}, "package": "shop", "environment_digest": "env-v1", "state_annotations": {"shop.state.value": {"essentiality": "accidental_avoidable", "derivation": "read through api", "justification": "cache"}}}
```

## The four open problems

- **Learning signals**: every answer carries the fingerprint of the tree, intent and environment it read, so a later
  run can be compared with the map it was planned from. Not yet: nothing records whether a cone turned out too short.
- **Evaluators the model cannot influence**: the map is fixed code reading the source; a model's text reaches it only
  as files you send, never as an instruction. Not yet: a person still decides which obligations are normative.
- **Transfer by failure mode**: the guard is bound to `outside_scope` and `unmanaged_complexity`, not to a project:
  any Python package can be mapped. Not yet: how often a cone misses a real effect is not measured.
- **Enough held-out cases**: Not yet: the map is checked by fixed tests, not by G6's sequential test on held-out
  repositories.
