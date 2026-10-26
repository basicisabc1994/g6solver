---
name: g6-wrong-name
description: Use when checking that the conformance kit fails a bad extension; never install it.
---

# bad_extension

The conformance kit's bad fixture. Its front matter names another skill, one op has no example, and its examples
answer badly: a state that is not one of the three, a block with no code, a traceback, and Python examples that
reach for the missing extra and say nothing about it.

## Ops

- `count`: count the words in `text`.
- `launch`: start something outside G6 (sensitive).
- `guess`: guess an answer.
- `lookup`: look `text` up in a heavy dependency.
- `silent`: pick an engine.
- `tally`: count the characters in `text`.
- `serve`: ask a local service (not declared sensitive).
- `spawn`: start a program (not declared sensitive).

## Examples

```g6-example op=count
{"text": "one two three"}
```

```g6-example op=guess
{}
```

```g6-example op=lookup
{"text": "anything"}
```

```g6-example op=silent
{}
```

```python
import fixture_heavy_dependency

assert fixture_heavy_dependency.lookup("anything")
```

```python
from g6.conformance_bad.bad_extension import BadBlock, BadInput

answer = BadBlock().infer(BadInput(op="silent")).unwrap()
assert answer["engine_used"] == "heavy"
```

```python
from g6.conformance_bad.bad_extension import BadBlock, BadInput

answer = BadBlock().infer(BadInput(op="silent")).unwrap()
assert answer["completion_state"] == "qualified-draft"
```
