---
name: g6-meta-programming
description: Use when an agent is about to rename a Python name or move statements into a new function and must change exactly the identifiers and statements meant, keeping every comment, instead of a text edit that also hits strings, attributes, keyword arguments and other scopes' names; or needs a module's functions, classes and imports read from its syntax tree.
---

# G6 meta_programming

Reads and edits Python through its syntax tree and Python's own scope rules, on the standard library alone. An edit
changes only the characters it must, so comments and layout stay as they were, and every edit is re-parsed, compiled
and compared with the tree it should give before it is returned. It never runs the code it reads; `run_tests` runs
your tests on it in a bounded child process.

## When to use it

- Renaming a variable, function, class, parameter or import binding in one module.
- Moving a few statements of a long function into a function of their own.
- Reading a module's functions, parameters, decision counts, classes and imports before changing it.

## When not to use it

- Across a package: it edits one module's text; other modules that import a renamed name are not seen.
- For attributes and methods (`obj.name`, a name bound in a class body): those are reached through objects, so it
  refuses rather than guess.
- As proof the code still works: the checks are structural. Run your tests (`run_tests`, or your own suite).

## Ops

- `parse`: whether `source` parses, and where and why it does not.
- `analyze`: `source`'s functions (qualified name, parameters, decorators, docstring, decisions and complexity), classes, imports, `__all__` and module-level names.
- `rename`: every identifier that means one binding of `old` renamed to `new`; `line` picks the binding where `old` is bound in more than one scope.
- `extract_function`: the whole statements on lines `start_line` to `end_line` of one function moved into a new module-level function `name`, called where they were.
- `run_tests`: your `tests` (Python with test functions that import the code as `module`, default `subject`) run on `source` in `g6.core.sandbox`. Sensitive.

## Reading the result

Async and generator test functions, and tests returning deferred work, are refused as
G6_E_SYNTAX_TEST_FORM_UNSUPPORTED. Only synchronous functions that complete when called can report passed.
Extraction refuses moved writes that need a global/nonlocal declaration outside the selection
(G6_E_SYNTAX_EXTRACT_REFUSED). Malformed __all__ literals are G6_E_SYNTAX_DOES_NOT_PARSE.
Recursion or memory exhaustion during syntax processing is G6_E_CODE_RESOURCE_LIMIT, blocked-escalated.

An edit returns `edited` (the whole new text), `changes` (each changed span: `line`, `column`, `end_column`, `before`,
`after`; for an extract, the lines replaced and the function inserted), `checks` (each check that ran, with what it
would have caught) and `left_alone` (what spells the name but is not the binding: attributes, strings such as
`__all__`, keyword arguments, comments, other modules). An extract also returns its `params` and `returns`. All are
`qualified-draft` (`G6_E_SYNTAX_EDITED`).

A refusal is `blocked-escalated`: `G6_E_SYNTAX_RENAME_REFUSED` (the name is not bound here, is bound in several scopes
and no `line` was given, would capture or be captured by another name, is bound in a class body or a `match` pattern,
or is a parameter a call passes by keyword), `G6_E_SYNTAX_EXTRACT_REFUSED` (the lines cut a statement, are not in a
function, hold a `return`, `yield`, `await`, a `break` of an outer loop, `global` or `nonlocal`, or bind a name that is
set on some paths only, or that a nested function outside them reads), `G6_E_SYNTAX_DOES_NOT_PARSE`, and
`G6_E_SYNTAX_EDIT_UNCHECKED` when an edit failed its own check and nothing is returned.

`run_tests` lists each test's `outcome`. All passing is `qualified-draft` (`G6_E_SYNTAX_TESTS_PASSED`): your tests'
claim about your code, which ran in the same child process as the runner. A failure is `G6_E_SYNTAX_TESTS_FAILED`; a
run that timed out, could not import, or reported nothing is `G6_E_SYNTAX_TESTS_DID_NOT_RUN`.

## Alone and composed

Over MCP: `g6_invoke("meta_programming", "rename", {"source": "...", "old": "count", "new": "limit", "line": 2})`;
`run_tests` only when the person who runs the server lists it in `G6_MCP_SENSITIVE`. From Python:
`MetaProgrammingBlock().infer(MetaProgrammingInput(op="extract_function", ...))`.

Composed: `analyze` to find the function, `rename` or `extract_function` to edit, `run_tests` on `edited`, then
`coding_agent_gate` on the change before reporting it done.

## Failure modes it catches

- `fragile_text_edit` (code changed by text edits that break its structure): a rename follows Python's scopes, so a
  parameter, a shadowing local, an attribute, a keyword argument, a string and a comment that spell the same name are
  left alone; an extract moves whole statements with the names they need; and every edit is refused unless its
  re-parsed tree is the tree the edit should give and its comments are unchanged.

## Blind spots

- `outside_scope`: a name reached through a string (`getattr`, `__all__`, `globals()`), `exec`, or another module is
  not seen, so behaviour there can change; `left_alone` names what it saw.
- The checks prove the edit is the structural change asked for, not that the program behaves the same; an extract's
  moved statements run in a new frame.
- Type parameters, type alias statements and `match` captures of the name are refused, not read.

## What it needs

Nothing outside G6 and no extra: `ast`, `tokenize` and pydantic. `run_tests` uses G6's own interpreter through
`g6.core.sandbox`; no model is called.

## Examples

```g6-example op=parse
{"source": "def broken(:\n    pass\n"}
```

`parses` is false, with the line and the parser's message.

```g6-example op=analyze
{"source": "def area(w, h):\n    if w < 0 or h < 0:\n        raise ValueError('negative')\n    return w * h\n"}
```

One function, `area`, with two decisions, so complexity 3.

```g6-example op=rename
{"source": "count = 10  # the module's count\n\ndef tally(items, count=0):\n    return count + len(items)\n\ndef report():\n    return dict(count=count)\n", "old": "count", "new": "limit", "line": 1}
```

The module's `count` becomes `limit` on lines 1 and 7; `tally`'s parameter, the keyword `count=` and the comment are
left alone.

```g6-example op=extract_function
{"source": "def total_price(items, tax):\n    subtotal = 0\n    for item in items:\n        subtotal += item\n    return round(subtotal * (1 + tax), 2)\n", "start_line": 2, "end_line": 4, "name": "sum_items"}
```

`sum_items(items)` returns `subtotal`, and the call takes the three lines' place.

```g6-example op=run_tests
{"source": "def add(a, b):\n    return a + b\n", "tests": "from subject import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"}
```

Sensitive: it runs the code, so over MCP only when the server's owner allows it.

```python
from g6.code.meta_programming import MetaProgrammingBlock, MetaProgrammingInput

source = "def f(old):\n    return old  # old stays here\n"
out = MetaProgrammingBlock().infer(MetaProgrammingInput(op="rename", source=source, old="old", new="count")).unwrap()
assert out["edited"] == "def f(count):\n    return count  # old stays here\n"
assert out["completion_state"] == "qualified-draft" and out["checks"][1]["check"] == "tree_is_the_rename"
```

## The four open problems

- **Learning signals**: each edit records its changed spans, the checks it passed and what it left alone; each
  refusal names its reason. Not yet: whether a returned edit later broke a test is not recorded.
- **Evaluators the model cannot influence**: Python's own parser and compiler, and a tree comparison computed from
  the original source; no model is called. Behaviour is judged only by your tests.
- **Transfer by failure mode**: bound to `fragile_text_edit` in Python; the scope rules are the language's own, so
  they hold for any module.
- **Enough held-out cases**: Not yet: the tests cover shadowing, parameters, closures, globals, imports, except names,
  attributes, keywords, strings and comments, not a frozen corpus of real refactors.
