# The extension conformance kit

The definition of done for one extension (gates G1 to G9, depth checks D1 to D10) as tests. `kit.py` holds each check
as a plain function over one component (its name, package and files) that returns the problems it found: file, line,
gate, message, and the flagged text. `test_conformance.py` runs every check on every component the registry finds;
`test_bad_fixture.py` runs them on `fixtures/bad_extension`, a family member that breaks every check, so a check that
stops reporting fails there.

```
pytest tests/conformance          # about 20 seconds; the D9 child processes run side by side
```

## Required, reported, waived

- Every check runs on every component. A check marked `extensions_only` in `kit.CHECKS` (G1, G7, D4's "has a block",
  "no import of another family's member or a sibling" and "no home-folder state", D6's examples, D9's imports) is
  required of family members and entry-point extensions and only reported for the core; the run's summary prints those
  hits.
- A family's shared code (its `__init__.py`, `_`-prefixed modules and folders, and anything else outside its members'
  folders) belongs to no member, so it is its own target, `g6.<family>`, even while the family has no member: every
  check marked `shared` (the ones that read only source) runs on it and is required of it.
- A required hit passes only where `waivers.toml` names it: component (`g6.<family>` for a family's shared code), gate,
  file (a folder ending in `/` covers the files under it), `match` (a substring of the flagged line; a runtime hit's
  line is its message), a reason, and the finding that tracks it. A waiver that no longer matches a hit fails, and
  `MAX_WAIVERS` in `test_conformance.py` is the number of waivers: removing one means lowering it, and adding one
  fails.

## What each check does

| Check | Gate | How |
|---|---|---|
| `g1_labels` | G1 | AST: a label word (`verified`, `proved`, `compliant`, `certified`, `safe`, `cleared`) given as an assignment, return, keyword, dict value or default, also as `STATES[0]`; a number given to a `*confidence*` name, key or keyword, also through a name bound once to one |
| `g2_g9_processes_and_network` | G2, G9 | AST, through every import, alias, `getattr(module, "name")` and module imported by name: network imports (and a network module reached through another import); `asyncio` and event-loop connections; `pty`; `exec`/`eval`; `os.system`/`popen`/`exec*`/`spawn*`; `shell=`; any `subprocess` use but a `run`/`check_output`/`call` with both `timeout=` and a clean `env=` (not the parent's environment, a copy of it, or a name bound to one) |
| `g5_sensitive_ops` | G5 | Each sensitive op is a real op and the registry refuses it with `block_sensitive=True`; and (`d4_refusals`, `d6_examples`) a non-sensitive op that connects, fetches, calls a local service or starts a program other than through `g6.core` for a declared one fails |
| `g6_models_and_keys` | G6 | AST: provider SDK imports, by name too; environment reads of `*_KEY`/`*_TOKEN`/`*_SECRET`, and of an upper-case key name from anything, through any name assigned it; `*API_KEY` constants, except as the name of a `g6.core.keys.Key` declaration (keys come only from the person's key file) |
| `g7_doubles` | G7 | AST: identifiers and strings matching mock, fake or simulat outside tests |
| `g8_headers`, `g8_vendored`, `g8_extras` | G8 | The SPDX line first; a licence beside vendored code; each declared extra is in `pyproject.toml` and the closure file |
| `closure_problems`, `licence_problems` | G8 | `tools/data/extra_closures.json` covers every extra and is current; every pinned package's licence is allowed by `tools/data/licence_table.json` |
| `d2_failure_modes` | D2 | `g6.core.failure_modes.declaration_problems`, the check `tests/core` uses |
| `d4_*` | D4 | A block; its input forbids extra fields; it answers `ops` and `help`; refusals are `blocked-escalated` with a code of their own; no cross-family imports; no home-folder state |
| `d6_find`, `d6_describe`, `d6_examples` | D6 | `g6_find` ranks it by each mode it guards; `g6_describe` returns its skill; its examples round-trip with no model and no socket |
| `d9_extras` | D9 | A child process per component and extra runs the examples with the extra's imports blocked; what a reply says counts only in its message fields, and a Python example that reaches for the extra must say it is missing |
| `d9_imports` | D9 | AST: every import is the standard library, `g6`, a core dependency or an import name of an extra the component declares |

D7 (a current skill whose ops, failure modes, needs and examples agree with the code) is enforced by
`tests/core/test_component_skills.py` and is not repeated here. D8 (the page tells the truth) lands in 0.7 and is a
named skip until then. D1, D3, D5 and D10 are reviewer checks against each batch card.

The closure file is networked to make: `python tools/gen_extra_closures.py` (it needs `uv`). The weekly `closures`
workflow resolves again and fails only on requirement drift or a licence the kit does not allow or cannot place;
versions that moved are printed as information.
