# SPDX-License-Identifier: Apache-2.0
"""The class one_fact_two_rules, removed at its root for what counts as JSON data (review 1 of PR #8's second
follow-up, finding 2): ``codes.claims`` decided by its own type lists, so a number that is not finite passed it while
``snapshot.plain`` refused it, and the result makers took their fields through the claim rule alone, never through
``plain``. Now ``plain`` is the rule: the claim rule reads a value's kind through the same leaf test (``codes._read_as``)
that ``plain``'s walk uses, and every function that builds a result from a caller's fields (each one that writes a
mapping carrying ``completion_state``, found from the code, and ``codes.only_fields``) takes them through ``plain``
once and refuses, with the boundary's code, whatever ``plain`` refuses.

This lists the makers from the code, then runs every kind ``plain`` refuses (numbers that are not finite, an integer
longer than the encoder writes, a subclass of each type, a set, bytes, a key that is not text, data nested too deep, and
any other object) through each maker and through the claim rule, and every kind ``plain`` takes through both."""
from __future__ import annotations

import ast
import enum
import math
from pathlib import Path

import pytest
from returns.result import Success

import g6.cognitive_bridge.mixed_methods as mixed_methods
from g6.cognitive_bridge import ClaimIR, Hypothesis, MethodRouter, MixedMethodExecutor, codes
from g6.cognitive_bridge.codes import DEPTH
from g6.cognitive_bridge.snapshot import FrozenMap, NotPlainData, plain, snapshot

PACKAGE = Path(codes.__file__).resolve().parent


def _modules():
    return [(path.name, ast.parse(path.read_text(encoding="utf-8"))) for path in sorted(PACKAGE.glob("*.py"))]


def _functions(tree):
    """Every function in a module with its dotted name (``Class.method``)."""
    found = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((f"{prefix}{child.name}", child))
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def _own_returns(fn):
    parent = {child: node for node in ast.walk(fn) for child in ast.iter_child_nodes(node)}
    out = []
    for node in [n for n in ast.walk(fn) if isinstance(n, ast.Return)]:
        up = parent[node]
        while not isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            up = parent[up]
        if up is fn:
            out.append(node)
    return out


def _called(node) -> set[str]:
    return {c.func.id if isinstance(c.func, ast.Name) else c.func.attr for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, (ast.Name, ast.Attribute))}


# --- The makers, from the code -----------------------------------------------------------------------------------------

def _bound(fn, name) -> list[ast.AST]:
    """What ``fn`` assigns to ``name``."""
    return [n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]


#: Calls that hand on what they are given: a result wrapped for G6's registry.
WRAPPERS = {"ok", "Success", "publish"}


def _through(fn, value, names) -> bool:
    """Whether ``value`` is what one of ``names`` made: a call to one, a wrapper's first argument that is, or a name
    every assignment in ``fn`` binds to one (a call merely held inside a mapping is not)."""
    if isinstance(value, ast.Name):
        bound = _bound(fn, value.id)
        return bool(bound) and all(_through(fn, v, names) for v in bound)
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    callee = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if callee in names:
        return True
    return callee in WRAPPERS and bool(value.args) and _through(fn, value.args[0], names)


def _returns_through(fn, names) -> bool:
    """Whether every own return of ``fn`` is what one of ``names`` made."""
    returns = _own_returns(fn)
    return bool(returns) and all(ret.value is not None and _through(fn, ret.value, names) for ret in returns)


def _closure(names) -> set[str]:
    """``names``, and every function in the package whose every return is what one of them made, until nothing new
    is."""
    names, changed = set(names), True
    while changed:
        changed = False
        for _, tree in _modules():
            for name, fn in _functions(tree):
                short = name.split(".")[-1]
                if short not in names and _returns_through(fn, names):
                    names.add(short)
                    changed = True
    return names


def _result_functions() -> set[str]:
    """The names of the functions that return a result: the two makers, and every function that returns theirs."""
    return _closure({"refusal", "settled"})


def makers():
    """Every function that writes a mapping carrying ``completion_state`` (a result): one with ``completion_state`` as
    a key, or one that spreads a result (``{**out, ...}``, ``out`` a result) into a mapping with fields of its own:
    (module, dotted name)."""
    results, found = _result_functions(), set()
    for module, tree in _modules():
        for name, fn in _functions(tree):
            own = {id(n) for _, inner in _functions(fn) for n in ast.walk(inner)}

            def spreads_a_result(node) -> bool:
                for key, value in zip(node.keys, node.values):
                    if key is None and (_called(value) & results or isinstance(value, ast.Name) and any(
                            _called(v) & results for v in _bound(fn, value.id))):
                        return True
                return False

            if any(isinstance(n, ast.Dict) and id(n) not in own and (any(
                    isinstance(k, ast.Constant) and k.value == "completion_state" for k in n.keys)
                    or spreads_a_result(n)) for n in ast.walk(fn)):
                found.add((module, name))
    return found


#: What the code lists, compared whole: a new maker is listed here and run below. (``CognitiveBridgeBlock._refused``:
#: the block's one refusal of its input, which its raw screen at G6's door and ``infer`` both return, since repair 2 of
#: follow-up 2.)
EXPECTED_MAKERS = {("block.py", "CognitiveBridgeBlock._refused"), ("block.py", "CognitiveBridgeBlock.infer"),
                   ("codes.py", "refusal"), ("codes.py", "settled"),
                   ("mixed_methods.py", "MixedMethodExecutor._run"),
                   ("orchestration.py", "CognitiveAssuranceOrchestrator.execute")}
#: The boundary's conversion, by the names a maker may call it by.
CONVERSION = {"plain", "_plain", "snapshot", "publish"}


def test_the_makers_are_listed_from_the_code():
    assert makers() == EXPECTED_MAKERS


@pytest.mark.parametrize("module,name", sorted(EXPECTED_MAKERS | {("codes.py", "only_fields")}))
def test_every_maker_returns_what_the_boundary_made(module, name):
    fn = next(f for n, f in _functions(dict(_modules())[module]) if n == name)
    # each return is what the boundary's conversion made (directly, through a function that returns it, a wrapper, or
    # the name it was bound to); a maker's call held inside a new mapping is not
    assert _returns_through(fn, _closure(CONVERSION)), \
        f"{name} returns a value the boundary did not make: " + "; ".join(
            ast.unparse(r)[:100] for r in _own_returns(fn))


def test_one_leaf_test_decides_what_is_json_data():
    # plain's walk (snapshot._not_json) and the claim rule read a value's kind through codes._read_as; no other function
    # in either module keeps a leaf list or a number test of its own
    trees = dict(_modules())
    seen = {}
    for module in ("codes.py", "snapshot.py"):
        for name, fn in _functions(trees[module]):
            names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
            attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
            seen[(module, name)] = names | attrs
            if name != "_read_as":
                assert "LEAVES" not in names, f"{module}:{name} keeps its own list of leaves"
                assert "isfinite" not in attrs, f"{module}:{name} keeps its own test of a number"
    assert "_read_as" in seen[("snapshot.py", "_not_json")] and "_read_as" in seen[("codes.py", "claims")]


# --- Every kind, through each maker and through the claim rule --------------------------------------------------------

class _Text(str):
    pass


class _Int(int):
    pass


class _Float(float):
    pass


class _Dict(dict):
    pass


class _List(list):
    pass


class _Tuple(tuple):
    pass


class _Frozen(FrozenMap):
    pass


class _Colour(enum.Enum):
    RED = "red"


def nested(depth, leaf="leaf"):
    for _ in range(depth):
        leaf = [leaf]
    return leaf


#: Every kind plain refuses, each made fresh, standing at ``x`` (one level inside what a maker returns).
REFUSED = {
    "nan": lambda: math.nan, "inf": lambda: math.inf, "-inf": lambda: -math.inf,
    "integer-longer-than-the-encoder-writes": lambda: 10 ** 5000,
    "str-subclass": lambda: _Text("x"), "int-subclass": lambda: _Int(1), "float-subclass": lambda: _Float(1.5),
    "dict-subclass": lambda: _Dict(k="x"), "list-subclass": lambda: _List(["x"]),
    "tuple-subclass": lambda: _Tuple(("x",)), "FrozenMap-subclass": lambda: _Frozen({"k": "x"}),
    "set": lambda: {1}, "frozenset": lambda: frozenset({1}),
    "bytes": lambda: b"x", "bytearray": lambda: bytearray(b"x"),
    "int-key": lambda: {1: "x"}, "tuple-key": lambda: {("a",): "x"},
    "nan-inside": lambda: {"rows": [{"score": math.nan}]},
    # x stands at depth 1, so its leaf at DEPTH + 1
    "too-deep": lambda: nested(DEPTH),
    "object": object, "function": lambda: len, "enum": lambda: _Colour.RED, "record": lambda: Hypothesis("A", "a", .5),
}
#: Every kind plain takes, standing at ``x``.
TAKEN = {
    "text": lambda: "x", "int": lambda: 3, "float": lambda: 2.5, "negative-zero": lambda: -0.0, "bool": lambda: True,
    "None": lambda: None, "dict": lambda: {"k": "x"}, "list": lambda: ["x", 1], "tuple": lambda: ("x", 1),
    "FrozenMap": lambda: FrozenMap({"k": ("x",)}), "empty": lambda: {}, "integer-the-encoder-writes": lambda: 10 ** 4000,
    "at-the-depth-bound": lambda: nested(DEPTH - 1),
}

#: Each maker, called with ``x`` among the caller's fields; what it returns.
MAKERS = {
    "refusal": lambda x: codes.refusal(codes.INPUT, "a reason", x=x),
    "settled": lambda x: codes.settled(None, x=x),
    "settled-refused": lambda x: codes.settled(codes.refusal(codes.INPUT, "a reason"), x=x),
    "settled-draft-card": lambda x: codes.settled(None, draft_card=(codes.NOT_PROMOTED, "a card"), x=x),
    "only_fields": lambda x: codes.only_fields({"x": x}, allowed=("x",), where="a format"),
}


def test_every_kind_here_is_refused_or_taken_by_plain_itself():
    for kind, make in REFUSED.items():
        with pytest.raises(NotPlainData):
            plain({"x": make()}, where="a value")
    for kind, make in TAKEN.items():
        assert plain({"x": make()}, where="a value") is not None, kind


@pytest.mark.parametrize("maker", sorted(MAKERS))
@pytest.mark.parametrize("kind", sorted(REFUSED))
def test_every_maker_refuses_what_plain_refuses_with_the_boundarys_code(kind, maker):
    with pytest.raises(NotPlainData) as excinfo:
        MAKERS[maker](REFUSED[kind]())
    assert excinfo.value.code == codes.INPUT


@pytest.mark.parametrize("maker", sorted(MAKERS))
@pytest.mark.parametrize("kind", sorted(TAKEN))
def test_every_maker_takes_what_plain_takes_and_shares_nothing(kind, maker):
    given = TAKEN[kind]()
    out = MAKERS[maker](given)
    # a result is plain data; what only_fields hands back stays read-only, as the boundary holds it
    expected = snapshot(given, where="x") if maker == "only_fields" else plain(given, where="x")
    assert out["x"] == expected and type(out["x"]) is type(expected)
    if isinstance(given, (dict, list)):
        assert out["x"] is not given


@pytest.mark.parametrize("kind", sorted(REFUSED))
def test_the_claim_rule_refuses_what_plain_refuses(kind):
    found = codes.claims({"x": REFUSED[kind]()})
    assert found and all(place.startswith("x") for place in found), found
    with pytest.raises(NotPlainData):
        codes.unclaimed({"x": REFUSED[kind]()}, where="a value")


@pytest.mark.parametrize("kind", sorted(TAKEN))
def test_the_claim_rule_takes_what_plain_takes(kind):
    assert codes.claims({"x": TAKEN[kind]()}) == []


def test_a_result_shares_nothing_with_the_callers_fields():
    given = {"rows": [{"a": 1}]}
    for make in (MAKERS["refusal"], MAKERS["settled"], MAKERS["settled-refused"]):
        out = make(given)
        given["rows"][0]["verified"] = True  # written after the maker checked it
        assert codes.claims(out) == [], out
        del given["rows"][0]["verified"]


def _containers(value):
    """The container types in ``value``, counted here, not by the code."""
    found, stack = set(), [value]
    while stack:
        item = stack.pop()
        if isinstance(item, (dict, list, tuple)):
            found.add(type(item))
            stack += list(item.values()) if isinstance(item, dict) else list(item)
    return found


@pytest.mark.parametrize("data", [
    {"op": "ops"}, {"op": "help"}, {"op": "assurance_schema"},
    {"op": "rank", "budget": 1, "hypotheses": [{"id": "A", "statement": "a", "prior": .5},
                                               {"id": "B", "statement": "b", "prior": .5}],
     "probes": [{"id": "p", "question": "q", "tool": "read", "positive_if": {"A": True, "B": False},
                 "assumed_match_probability": .9}]},
    {"op": "rank", "hypotheses": [{"id": "A", "statement": "a", "prior": .5}]},
])
def test_the_blocks_answer_is_what_the_boundary_made(data):
    from g6.cognitive_bridge import CognitiveBridgeBlock

    out = CognitiveBridgeBlock().infer(data).value
    assert out["component"] == "cognitive_bridge" and codes.claims(out) == []
    assert _containers(out) <= {dict, list}, _containers(out)
    assert type(out) is dict and out == plain(out, where="the block's answer")


def _executor_run(request_id, monkeypatch):
    """The executor's step, with the door's ``request_id`` (a field ``_run`` publishes beside the answer) replaced."""
    real = mixed_methods.invoke_checked

    def planted(*args, **kwargs):
        return {**real(*args, **kwargs), "request_id": request_id}

    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            return Success({"completion_state": "qualified-draft", "answer": {"valid": True}})

    monkeypatch.setattr(mixed_methods, "invoke_checked", planted)
    plan = MethodRouter().plan([ClaimIR("c", "check A", ("formal",), {"formal": {"formula": "A"}})])
    return MixedMethodExecutor(Registry()).execute(plan, approved=True)


@pytest.mark.parametrize("kind", sorted(REFUSED))
def test_the_executors_step_refuses_what_plain_refuses_in_a_field_it_publishes(kind, monkeypatch):
    out = _executor_run(REFUSED[kind](), monkeypatch)
    step = out["steps"][0]
    assert out["code"] == codes.EXECUTION_INCOMPLETE and step["code"] == codes.STEP_FAILED, step
    assert "result" not in step


@pytest.mark.parametrize("kind", sorted(set(TAKEN) - {"at-the-depth-bound"}))
def test_the_executors_step_takes_what_plain_takes(kind, monkeypatch):
    given = TAKEN[kind]()
    out = _executor_run(given, monkeypatch)
    step = out["steps"][0]
    assert out["completion_state"] == "qualified-draft", step.get("reason")
    assert step["request_id"] == plain(given, where="request_id")
