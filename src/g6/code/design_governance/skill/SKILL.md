---
name: g6-design-governance
description: Use when an agent has changed a codebase's structure (moved code, added imports, split a module) and must check before reporting it done that it added no import cycle, broke no layer rule and added no wrapper that only forwards its arguments; it reads a file map you give for any package and refuses rather than calling a check with nothing to check clean.
---

# G6 design_governance

Checks a package's import structure from a file map you give it (path to source): the imports between its modules,
the cycles among them, the imports that break a layer contract, the functions that only forward their arguments,
and the cycles a change introduced. It parses each file with `ast` and runs none of it. A check that found nothing to
check is refused, never read as clean.

## When to use it

- Before reporting a refactor done: `diff_cycles` on the files before and after.
- To hold a codebase to its layers (an api over a domain over a store): `layer_violations`.
- To find the cycles already there, or wrappers that add nothing.

## When not to use it

- For complexity inside a function (long functions, deep branching): `meta_programming`'s `analyze` gives decision
  counts.
- For imports made at run time (`importlib.import_module`, `__import__`): they are not seen.
- To enforce the contract in continuous integration: import-linter does that over an installed package.

## Ops

- `import_graph`: the imports between the modules of `package` in `files`, grouped by their first `depth` dotted parts (default 2), each edge with how many imports and an example file and line.
- `cycles`: the import cycles, each with its modules, one cycle through them and the imports that close it.
- `layer_violations`: imports that break `contract` (`layers`, highest first, each a dotted module prefix, and `forbidden` pairs of `source` and `forbidden`).
- `pass_through`: functions in `files` whose one statement returns a call forwarding their parameters unchanged and in order.
- `diff_cycles`: the cycles introduced and removed between `before` and `after`, and the new imports inside the introduced ones.

## Reading the result

Each module belongs to its longest matching layer prefix. A layer with no assigned module is refused as
G6_E_DESIGN_CONTRACT_UNMATCHED, including a layer hidden by more specific prefixes.
Only the true branch of if TYPE_CHECKING is guarded; its else imports remain runtime imports.
Recursion or memory exhaustion is G6_E_CODE_RESOURCE_LIMIT. A forwarding return with trailing statements is
G6_E_DESIGN_INCOMPLETE rather than a clean pass-through result.

Paths are relative to the folder that holds the package (`shop/cart/model.py` is `shop.cart.model`). Imports inside a
function and under `if TYPE_CHECKING` count unless `include_lazy` or `include_type_checking` is false. Each result
carries `modules`, `imports_inside`, `external_imports`, `unparsed` (files that did not parse) and `not_python`.

A finding is `qualified-draft` with `clean: false` (`G6_E_DESIGN_FINDINGS`, a red card); none found is
`qualified-draft` with `clean: true` (`G6_E_DESIGN_NONE_FOUND`), in structure only. Refused, `blocked-escalated`:
`G6_E_DESIGN_NO_MODULES` (no file is a module of `package`), `G6_E_DESIGN_NO_EDGES` (no import between its modules,
so a clean result would mean nothing), `G6_E_DESIGN_CONTRACT_UNMATCHED` (a layer or pair names no module, as a typo
would), `G6_E_DESIGN_INCOMPLETE` (nothing found while a file did not parse), `G6_E_DESIGN_BAD_PATH` and
`G6_E_DESIGN_INPUT`. At most 2,000 files, 500,000 characters each.

## Alone and composed

Over MCP: `g6_invoke("design_governance", "diff_cycles", {"before": {...}, "after": {...}, "package": "shop"})`;
every op is open there. From Python: `DesignGovernanceBlock().infer(DesignGovernanceInput(op="cycles", ...))`.

Composed: `swe_diagnostics`' `patch_check` names the changed files; read them before and after, then `diff_cycles` and
`layer_violations`, and give the findings to `coding_agent_gate` as evidence about the diff.

## Failure modes it catches

- `unmanaged_complexity` (code growing more complex than anyone can safely change): an import cycle a change
  introduced, with the import that closed it; an import that breaks the layers; a wrapper that adds nothing; for any
  package, with a check that matched nothing refused rather than passed.

## Blind spots

- Dynamic imports and imports through strings are not seen; neither is coupling through shared data or globals.
- Complexity inside a function, and design that is not structural, are not measured.
- Grouping by depth decides what a cycle is: at depth 2, modules of one subpackage importing each other is no cycle.
- Its own use causes no failure mode it can name, and a clean result is structure only.

## What it needs

Nothing outside G6 and no extra: `ast` and pydantic. No model; no file is read from a disk.

## Examples

```g6-example op=cycles
{"files": {"shop/__init__.py": "", "shop/cart.py": "from shop import price", "shop/price.py": "from shop import cart"}, "package": "shop"}
```

One cycle: `shop.cart`, `shop.price`, `shop.cart`, with the imports that close it.

```g6-example op=import_graph
{"files": {"shop/__init__.py": "", "shop/api/__init__.py": "", "shop/api/routes.py": "from shop.domain import cart", "shop/domain/__init__.py": "", "shop/domain/cart.py": "import sqlite3"}, "package": "shop"}
```

One edge, `shop.api` to `shop.domain`, and one external import.

```g6-example op=layer_violations
{"files": {"shop/__init__.py": "", "shop/api/__init__.py": "", "shop/api/routes.py": "from shop.store import db", "shop/store/__init__.py": "", "shop/store/db.py": "from shop.api import routes"}, "package": "shop", "contract": {"layers": ["shop.api", "shop.store"]}}
```

One breach: `shop.store.db` imports `shop.api.routes`, a higher layer.

```g6-example op=pass_through
{"files": {"m.py": "def save(record, path):\n    return write(record, path)\n"}}
```

`save` forwards its parameters to `write` unchanged.

```g6-example op=diff_cycles
{"before": {"shop/__init__.py": "", "shop/a.py": "from shop import b", "shop/b.py": ""}, "after": {"shop/__init__.py": "", "shop/a.py": "from shop import b", "shop/b.py": "from shop import a"}, "package": "shop"}
```

One cycle introduced, and the new import in `shop/b.py` that closed it.

```python
from g6.code.design_governance import DesignGovernanceBlock, DesignGovernanceInput

files = {"shop/__init__.py": "", "shop/a.py": "import shop.b\n", "shop/b.py": "x = 1\n"}
out = DesignGovernanceBlock().infer(DesignGovernanceInput(op="layer_violations", files=files, package="shop",
                                                          contract={"layers": ["shop.a", "shop.c"]})).unwrap()
assert out["code"] == "G6_E_DESIGN_CONTRACT_UNMATCHED"  # shop.c names no module: refused, never clean
```

## The four open problems

- **Learning signals**: each reply records what it read (modules, imports, external imports, unparsed files) and each
  finding with its file and line. Not yet: whether a flagged cycle was later removed is not tracked.
- **Evaluators the model cannot influence**: Python's parser and Tarjan's algorithm over the files given; no model
  is called.
- **Transfer by failure mode**: bound to `unmanaged_complexity`; it reads any package's layout, with the package and
  the contract as input.
- **Enough held-out cases**: Not yet: tested on small packages made in the tests, not on a frozen set of real
  refactors with known cycles.
