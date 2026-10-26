---
name: g6-component-creator
description: Use when an agent is starting a new G6 component, wrapping a Python library as one, or about to call a component package done, and needs the library's own shape scaffolded (declarations, a typed input, refusing handlers, a failing test, a skill whose ops match) or a package checked against the library's standard first.
---

# G6 component_creator

The library's scaffolder. It drafts a spec from a module's interface or from a description, renders the spec in the
library's shape, and checks a package against the library's standard. A scaffold is never a finished component: its
ops refuse until they are written, its first test fails until it is replaced, and its skill holds placeholders until
it is written, so nothing it makes can look done.

## When to use it

- Before writing a new component by hand: render the shape, then write the ops.
- To wrap a Python library: `spec_from_module` maps each public function to an op that calls it.
- Before reporting a component finished: `check_conformance` finds a missing declaration, a skill whose ops differ
  from the block's, a missing example, a placeholder test or an input that accepts any field.

## When not to use it

- To write the ops themselves: it scaffolds; the logic, and the test that proves the failure mode is caught, are yours.
- As the conformance kit: folder checks read text; installed checks import the package and run its ops op. `tests/conformance` and the package's tests still decide.
- To decide which ops are sensitive: `spec_from_module` guesses from names; a read that reaches the network is
  sensitive too.

## Ops

- `spec_from_module`: a spec with one op per public function of a module (`source` with its import name `module`, or `path` to a `.py` file or a package folder under a declared root), read as text with `ast`; `__all__` decides what is public when it is a literal.
- `spec_from_text`: a spec drafted by the model caller you give the block from Python, checked against the spec schema; it never writes.
- `render`: the scaffold for a spec; `dry_run` (the default) returns the files, and `dry_run: false` with `dest` writes them into a new or empty folder under a declared root. Sensitive.
- `check_conformance`: an installed `component`, or a package folder at `path` under a declared root, checked for static issues; installed packages are imported and their ops op is invoked, recorded in evidence.

## Reading the result

Descriptions stay data in generated Python. Summary refuses triple double quotes, backslashes and control
characters; every does field refuses line breaks and control characters (G6_E_SCAFFOLD_SPEC_INVALID).
Docstrings and comments use one string escaper; comment text stays on prefixed comment lines.

The result field is static_clean, replacing conforms. It means only that these static checks found no problem.
complete is false when a file fails parsing, a file is skipped, or a walk is truncated; such a result is refused.
Every Python parse failure is listed. Evidence names imported packages and invoked ops, with ran_code true for
installed checks. Folder checks import and run nothing. Walks stop at 200 conformance files (50 for module specs),
50,000 entries or five seconds, with at most 1,000,000 bytes per file; read records the truncation reason.
UNC and device paths are refused before filesystem access; normalised containment is checked before resolving links.
Syntax processing that exhausts recursion or memory is G6_E_CODE_RESOURCE_LIMIT.

A spec has `name`, `family`, `summary`, `ops` (each `name`, `does`, `kind`, `sensitive`, `params` and, for a wrapped
function, `calls`), `fields` and the failure modes it `mitigates` and `introduces`. `problems` lists what stops it
becoming a component the library takes: no family, no failure mode guarded against, an unknown code. `render` refuses
such a spec (`G6_E_SCAFFOLD_SPEC_INVALID`). `spec_from_module` also returns the `interfaces` it read and what it
`left_out` (classes, async or variadic functions, names `__all__` re-exports).

The scaffold is `<name>/__init__.py`, `<name>/block.py`, `<name>/skill/SKILL.md` and `tests/test_<name>.py`. An op
with `calls` calls that function with the fields it reads and returns its value, saying nothing checked it; every
other op answers `blocked-escalated` with `G6_E_<NAME>_NOT_IMPLEMENTED`. The block is marked `stubbed`.

`check_conformance` returns `problems`, each with its `check` (`headers`, `declarations`, `failure_modes`, `block`,
`skill` or `tests`), `file` and `message`. No problem is `qualified-draft` (`G6_E_CONFORMANCE_CHECKED`); any problem is
`blocked-escalated` (`G6_E_CONFORMANCE_PROBLEMS`). A path outside the declared roots is `G6_E_CODE_OUTSIDE_ROOT`, and
with no root declared every path is refused (`G6_E_CODE_NO_ROOT`).

## Alone and composed

Over MCP: `g6_invoke("component_creator", "check_conformance", {"component": "<name>"})`, and `spec_from_module` with
`source`. `render` runs over MCP only when the person who runs the server lists it in `G6_MCP_SENSITIVE`;
`spec_from_text` uses the model the person set in `~/.g6/.env` (for example `G6_LLM_PROVIDER=claude_code`), and with
none it is refused (`G6_E_SCAFFOLD_NO_MODEL`): write the spec yourself and check it with `check_conformance`.

From Python: `ComponentCreatorBlock(caller=..., roots=...)`; `caller` is any `g6.core.llm` caller, and `roots` the
folders it may read and write (by default the person's declaration in `G6_CODE_CONFIG` or `code/config.json` under the
G6 home, as `{"roots": ["<absolute folder>"]}`).

Composed: `spec_from_module` or `spec_from_text`, then `render`, then write the ops, then `check_conformance`, then the
conformance kit. `g6.metaharness` decides whether a change to a profile stays; this decides nothing about behaviour.

## Failure modes it catches

- `incomplete_component` (a new component missing its tests or standard parts): every scaffold carries the standard
  parts and refuses to look done, and `check_conformance` reports a missing declaration, an unknown failure-mode code
  (through `g6.core.failure_modes`), an input model that accepts extra fields, a skill section, op or example that is
  missing or differs from the block's (through `g6.core.skills`), a placeholder line left in the skill, and a test
  file that is missing or still the scaffold's placeholder.

## Blind spots

- `wrong_problem`: a spec a model drafts can describe a different component from the one you meant; the schema checks
  its shape only.
- It reads text: whether an op does what its line says, or whether a test proves anything, is not checked.
- `spec_from_module` sees what the source says: names made at run time, a function's real effects and its exceptions
  are not seen, and a library function it wraps runs with whatever that library does.
- A sensitive-op list it cannot read without running the code (built by a function) is reported, not followed.

## What it needs

Nothing outside G6 and no extra: the standard library's `ast` and pydantic. `spec_from_text` needs a model caller you
pass from Python (no model is called otherwise). Paths need a declared root (`G6_CODE_CONFIG`).

## Examples

```g6-example op=spec_from_module
{"source": "__all__ = ['get_total', 'delete_item']\n\ndef get_total(a: int, b: int = 0) -> int:\n    return a + b\n\ndef delete_item(name: str) -> bool:\n    return True\n", "module": "tinylib"}
```

Two ops: `get_total` (a read) and `delete_item` (a delete, marked sensitive); `problems` asks for a family and a
failure mode before `render`.

```g6-example op=check_conformance
{"component": "ctx_library_mapper"}
```

An installed extension held to the standard: no problem, so `qualified-draft`, with what was read.

```g6-example op=spec_from_text
{"text": "a component that counts lines of code per folder"}
```

With no model configured, this is refused with `G6_E_SCAFFOLD_NO_MODEL`, which says how to configure one.

```g6-example op=render
{"spec": {"name": "unit_tracker", "family": "code", "summary": "Track units of work.", "ops": [{"name": "count_units", "does": "count the units", "kind": "read"}], "mitigates": ["incomplete_component"]}}
```

Sensitive: a dry run that returns the four files and writes nothing.

```python
import json
from g6.code.component_creator import ComponentCreatorBlock, ComponentCreatorInput
from g6.core.llm import FakeLLM

draft = {"name": "unit_tracker", "family": "code", "summary": "Track units of work.",
         "ops": [{"name": "count_units", "does": "count the units", "kind": "read"}],
         "mitigates": ["incomplete_component"]}
block = ComponentCreatorBlock(caller=FakeLLM(default=json.dumps(draft)))  # stands in for your model
spec = block.infer(ComponentCreatorInput(op="spec_from_text", text="count units of work")).unwrap()
assert spec["completion_state"] == "qualified-draft" and spec["problems"] == []
files = block.infer(ComponentCreatorInput(op="render", spec=spec["spec"])).unwrap()["files"]
assert "G6_E_UNIT_TRACKER_NOT_IMPLEMENTED" in files["unit_tracker/block.py"]
```

## The four open problems

- **Learning signals**: each reply records what was read or drafted (the modules, whether anything was imported, the
  caller that drafted a spec) and every problem found. Not yet: which scaffolds became components that pass the kit is
  not recorded, and nothing learns from it.
- **Evaluators the model cannot influence**: the schema, the failure-mode registry and the skill rules are fixed code;
  a drafted spec cannot mark itself checked, and a scaffold's failing test and refusing ops stand until a person
  replaces them.
- **Transfer by failure mode**: bound to `incomplete_component`; the same checks hold any family's package, installed
  or in a folder.
- **Enough held-out cases**: Not yet: the checks are tested on the library's own packages with one standard part
  removed at a time, not on a frozen set of packages written elsewhere.
