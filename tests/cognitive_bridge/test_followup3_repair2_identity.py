# SPDX-License-Identifier: Apache-2.0
"""Kind decisions are identity decisions, inventoried across the whole package."""
import ast
import dataclasses
import inspect
import textwrap
from pathlib import Path

import pytest

from g6.cognitive_bridge import ClaimIR, codes, snapshot
from tests.cognitive_bridge.test_boundary_inventory import MADE


def kind_sites():
    """Include boundary internals and annotations, rather than only the JSON leaf gate."""
    for path in sorted(Path(codes.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        def aliases_for(node):
            owner = node
            while id(owner) in parents and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents[id(owner)]
            aliases = {"hint", "origin"} if getattr(owner, "name", "") == "_kind" else set()
            aliases.update(n.targets[0].id for n in ast.walk(owner) if isinstance(n, ast.Assign)
                           and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                           and isinstance(n.value, ast.Call) and ast.unparse(n.value.func) == "type")
            return aliases
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in (
                    "isinstance", "issubclass", "callable"):
                yield path.name, node.lineno, "protocol", ast.unparse(node)
            if isinstance(node, ast.Call) and ast.unparse(node.func) in (
                    "_is_type", "_has_member", "_code", "_record_type", "_is_record", "_protocol_kind", "_type_mro"):
                yield path.name, node.lineno, "identity", ast.unparse(node)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "getattr" and len(node.args) >= 2:
                if isinstance(node.args[1], ast.Constant) and node.args[1].value == "_is_protocol":
                    yield path.name, node.lineno, "protocol-flag", ast.unparse(node)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "hasattr" and len(node.args) == 2:
                member = ast.literal_eval(node.args[1]) if isinstance(node.args[1], ast.Constant) else ""
                if member == "tolist":
                    yield path.name, node.lineno, "protocol-attribute", ast.unparse(node)
                elif (path.name, member) not in {("xai.py", "tree_"), ("xai.py", "n_features_in_"),
                                               ("xai.py", "classes_")}:
                    yield path.name, node.lineno, "unclassified-presence", ast.unparse(node)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "mro":
                yield path.name, node.lineno, "caller-mro", ast.unparse(node)
            if not isinstance(node, ast.Compare):
                continue
            parts = [node.left, *node.comparators]
            aliases = aliases_for(node)
            typed = any(isinstance(p, ast.Call) and ast.unparse(p.func) == "type"
                        or isinstance(p, ast.Name) and p.id in aliases for p in parts)
            typed |= any(isinstance(p, ast.Name) and p.id in {"LEAVES", "OBJECTS", "ARRAYS", "_NESTED"}
                         for p in parts)
            typed |= ast.unparse(node.left) == "args"  # tuple annotation decisions include their elements
            typed |= any(isinstance(p, ast.Attribute) and p.attr == "annotation" for p in parts)
            typed |= any(ast.unparse(p) == "typing.Protocol" for p in parts)
            if typed:
                yield path.name, node.lineno, (
                    "identity" if all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops) else "equality"
                ), ast.unparse(node)


def test_every_kind_decision_is_by_identity():
    sites = list(kind_sites())
    assert sites
    assert not [site for site in sites if site[2] != "identity"]


HOOKS = ("__eq__", "__hash__", "__instancecheck__", "__subclasscheck__", "mro")


def foreign(base, hook):
    calls = []

    class Hostile(type):
        pass

    cls = Hostile("Foreign", (base,), {})

    def trap(*args):
        calls.append(hook)
        raise AssertionError(f"kind admission invoked {hook}")

    # Install after class creation: building a Python class itself invokes mro().
    setattr(Hostile, hook, trap)
    return cls, calls


@pytest.mark.parametrize("base,value", [(dict, {}), (list, []), (tuple, ()), (str, "x"), (int, 1), (float, 1.)])
@pytest.mark.parametrize("hook", HOOKS)
@pytest.mark.parametrize("depth", range(codes.DEPTH + 2))
def test_foreign_metaclasses_are_never_consulted(base, value, hook, depth):
    cls, calls = foreign(base, hook)
    item = cls(value)
    for _ in range(depth):
        item = [item]
    for read in (lambda: snapshot.plain(item, where="input"),
                 lambda: snapshot.snapshot(item), lambda: snapshot.canonical_json(item),
                 lambda: snapshot.number(item, where="number"),
                 lambda: snapshot.text(item, where="text"),
                 lambda: ClaimIR("c", "s", ("formal",), {"payload": item}),
                 lambda: codes.refusal(codes.INPUT, "input", payload=item),
                 lambda: codes.settled(None, payload=item)):
        with pytest.raises(codes.NotPlainData):
            read()
    assert calls == []


@pytest.mark.parametrize("hook", HOOKS)
def test_record_subclasses_cannot_claim_a_bridge_record_kind(hook):
    base = type(MADE["TreeNode"]())
    cls, calls = foreign(base, hook)
    try:
        value = cls(value=1)
    except codes.NotPlainData:
        assert calls == []
        return
    with pytest.raises(codes.NotPlainData):
        snapshot.snapshot(value)
    with pytest.raises(codes.NotPlainData):
        dataclasses.replace(MADE["ExplicitTree"](), root=value)
    assert calls == []


def test_identity_gate_itself_does_not_hash_compare_or_ask_for_an_mro():
    # This is also challenged directly: call sites must not hide an equality gate.
    from g6.cognitive_bridge.codes import _is_type
    for hook in HOOKS:
        cls, calls = foreign(dict, hook)
        assert _is_type(cls, (dict, list, int)) is False
        assert calls == []


def test_kind_helpers_are_identity_and_exact_type_mro_readers():
    from g6.cognitive_bridge.codes import _code, _has_member, _is_type, _type_mro
    def source(fn):
        return ast.parse(textwrap.dedent(inspect.getsource(fn)))
    compares = [n for n in ast.walk(source(_is_type)) if isinstance(n, ast.Compare)]
    assert [ast.unparse(n) for n in compares] == ["kind is allowed"]
    calls = [ast.unparse(n.func) for n in ast.walk(source(_has_member)) if isinstance(n, ast.Call)]
    assert "_type_mro" in calls
    assert "type.__dict__['__dict__'].__get__" in calls
    assert "type.__getattribute__" not in calls
    assert ast.unparse(source(_type_mro).body[0].body[-1].value) == "type.__dict__['__mro__'].__get__(kind)"
    assert "__dict__" in inspect.getsource(_has_member)
    assert ast.unparse(source(_code).body[0].body[-1].value) == "_has_member(value, '__call__')"
    record_kind = inspect.getsource(snapshot._record_type)
    assert "is kind" in record_kind
    assert "is not None" in record_kind
    assert ast.unparse(source(snapshot._protocol_kind).body[0].body[-1].value) == (
        "any((base is typing.Protocol for base in bases))")


@pytest.mark.parametrize("hook", HOOKS)
@pytest.mark.parametrize("depth", range(codes.DEPTH + 2))
@pytest.mark.parametrize("entry", ["direct", "screen", "registry"])
@pytest.mark.parametrize("base,value", [(dict, {}), (list, []), (tuple, ()), (str, "x"), (int, 1), (float, 1.)])
def test_all_raw_block_entries_refuse_foreign_nested_kinds_without_hooks(hook, depth, entry, base, value):
    from tests.cognitive_bridge.test_followup3_repair1_inventory import invoke
    cls, calls = foreign(base, hook)
    item = cls(value)
    for _ in range(depth):
        item = [item]
    request = {"op": "plan_methods", "claims": [{
        "id": "c", "statement": "s", "evidence_needs": ["formal"], "models": {"formal": item}}]}
    answer = invoke(entry, request)
    assert answer.get("code") == codes.INPUT
    assert codes.claims(answer) == []
    assert calls == []


@pytest.mark.parametrize("hook", HOOKS)
def test_code_capability_uses_actual_mro_not_metaclass_hooks(hook):
    class Callable:
        def __call__(self, question, arguments):
            return MADE["Observation"]()
    cls, calls = foreign(Callable, hook)
    callback = cls()
    tool = dataclasses.replace(MADE["ToolSpec"](), call=callback)
    assert tool.call is callback
    assert snapshot.snapshot(tool).call is callback
    assert calls == []


def test_metaclass_descriptors_cannot_replace_the_actual_mro_or_namespace():
    from g6.cognitive_bridge.codes import _code
    calls = []

    class ClaimedCallable:
        def __call__(self):
            pass

    class Spoof(type):
        @property
        def __mro__(cls):
            calls.append("__mro__")
            return (ClaimedCallable,)

        @property
        def __dict__(cls):
            calls.append("__dict__")
            return {"__call__": lambda: None}

    class Foreign(metaclass=Spoof):
        pass

    assert _code(Foreign()) is False
    assert calls == []


def test_absent_record_registration_is_not_an_admitted_kind():
    assert snapshot._record_type(None) is False
    assert snapshot._record_type(type(None)) is False


@pytest.mark.parametrize("hook", HOOKS)
def test_bytes_fields_use_exact_kinds_before_conversion(hook):
    for base in (bytes, bytearray):
        cls, calls = foreign(base, hook)
        value = cls(b"GOOD")
        for read in (lambda: snapshot._bytes(value, "content"),
                     lambda: dataclasses.replace(MADE["EvidenceArtifact"](), content=value),
                     lambda: snapshot._field(("bytes*",), [value], "controls", 1)):
            with pytest.raises(codes.NotPlainData):
                read()
        assert calls == []


@pytest.mark.parametrize("hook", HOOKS)
def test_annotation_kinds_do_not_compare_or_hash_foreign_types(hook):
    calls = []

    class Hostile(type):
        pass

    cls = Hostile("Foreign", (bytes,), {})
    hints = (tuple[cls, ...], cls | None)

    def trap(*args):
        calls.append(hook)
        raise AssertionError(hook)

    setattr(Hostile, hook, trap)
    for hint in hints:
        assert snapshot._kind(hint) == ("data",)
    assert calls == []


def test_protocol_annotation_kind_comes_from_actual_mro_not_a_claimed_flag():
    from typing import Protocol

    class Declared(Protocol):
        def __call__(self):
            ...

    class Claimed:
        _is_protocol = True

    calls = []

    class Spoof(type):
        @property
        def _is_protocol(cls):
            calls.append("_is_protocol")
            return True

        @property
        def __mro__(cls):
            calls.append("__mro__")
            return (Protocol,)

    class Foreign(metaclass=Spoof):
        pass

    assert snapshot._kind(Declared) == ("code",)
    assert snapshot._kind(Claimed) == ("data",)
    assert snapshot._kind(Foreign) == ("data",)
    assert calls == []


def test_estimator_array_conversion_requires_a_declared_type_capability():
    from g6.cognitive_bridge.xai import _native

    class DeclaredArray:
        def tolist(self):
            return [1, 2]

    class ClaimedArray:
        def __init__(self):
            self.tolist = lambda: [1, 2]

    # Owner ruling: a declared converter without a measurable size is refused
    # before conversion; declaration alone cannot establish a size bound.
    with pytest.raises(codes.NotPlainData, match="size"):
        _native(DeclaredArray(), where="array")
    with pytest.raises(codes.NotPlainData):
        _native(ClaimedArray(), where="array")
