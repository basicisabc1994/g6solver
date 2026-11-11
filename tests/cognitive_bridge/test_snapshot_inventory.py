# SPDX-License-Identifier: Apache-2.0
"""The check-then-act class, removed at its root (review 3): this lists, from the code, every class in
``g6.cognitive_bridge`` that stores a caller's value, and fails on any store that does not go through the boundary
(``snapshot.snapshot``, ``snapshot.text`` for an identity or ``snapshot.plain``, or ``snapshot.record`` for a record
type), and on any ``vars()`` view handed out. Follow-up 2 of PR #8 made the boundary one data model: a snapshot is plain
JSON data and bridge records, and anything else (a set, a buffer, code outside a record's code field) is refused."""
from __future__ import annotations

import ast
import builtins
import dataclasses
from pathlib import Path

import pytest

from g6.cognitive_bridge import snapshot as snap
from g6.cognitive_bridge.snapshot import FrozenMap, record, snapshot

PACKAGE = Path(snap.__file__).resolve().parent
#: Code a caller hands in (a model, a registry, a proposer, a callback), kept by reference: a callable is not data.
COLLABORATORS = {"llm", "registry", "proposer", "selector", "updater", "predictor", "admit_pair", "admit_control",
                 "admit_trial", "admit_assertion", "reviewer_authenticator"}


def _modules():
    return [(path.name, ast.parse(path.read_text(encoding="utf-8"))) for path in sorted(PACKAGE.glob("*.py"))]


def _calls(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


#: The boundary's functions a store may take a caller's value through.
BOUNDARY = ("snapshot", "text", "plain")


def taken(node: ast.AST) -> bool:
    """Whether a stored value is taken through the boundary."""
    return any(_calls(node, name) for name in BOUNDARY)


def _bound(node: ast.AST) -> set[str]:
    """The names a comprehension binds inside ``node``."""
    return {target.id for comp in ast.walk(node) if isinstance(comp, ast.comprehension)
            for target in ast.walk(comp.target) if isinstance(target, ast.Name)}


def _own_state(value: ast.AST) -> bool:
    """A literal, or a value computed from this object's own (already snapshotted) attributes only."""
    if isinstance(value, ast.Constant) or (isinstance(value, (ast.Dict, ast.List, ast.Tuple, ast.Set))
                                           and not any(True for _ in ast.iter_child_nodes(value))):
        return True
    names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)} - _bound(value) - set(dir(builtins))
    return names <= {"self"}


def stores():
    """Every ``self.<attr> = value`` in an ``__init__`` in the package: (module, class, attr, value node)."""
    found = []
    for module, tree in _modules():
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for init in [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"]:
                for node in ast.walk(init):
                    targets = (node.targets if isinstance(node, ast.Assign)
                               else [node.target] if isinstance(node, ast.AnnAssign) and node.value else [])
                    for target in targets:
                        pairs = (list(zip(target.elts, node.value.elts)) if isinstance(target, ast.Tuple)
                                 and isinstance(node.value, ast.Tuple) else [(target, node.value)])
                        for t, v in pairs:
                            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                                found.append((module, cls.name, t.attr, v))
    return found


def test_the_inventory_finds_the_stores():
    listed = {(m, c, a) for m, c, a, _ in stores()}
    assert ("engine.py", "CognitiveAgent", "tools") in listed
    assert ("metacognition.py", "MetacognitiveMethodSelector", "trials") in listed
    assert ("assurance.py", "InProcessAssuranceEvaluator", "evaluator_id") in listed


def test_every_store_of_a_callers_value_goes_through_the_snapshot():
    bad = [f"{m}:{c}.{a} = {ast.unparse(v)}" for m, c, a, v in stores()
           if not (taken(v) or a in COLLABORATORS or _own_state(v))]
    assert bad == [], "stored without a snapshot: " + "; ".join(bad)


def test_every_record_type_is_a_snapshot_record():
    bad = []
    for module, tree in _modules():
        if module == "snapshot.py":
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            names = [ast.unparse(d) for d in cls.decorator_list]
            if any("dataclass" in name for name in names):
                bad.append(f"{module}:{cls.name} {names}")
    assert bad == [], "a record type that is not a snapshot record: " + "; ".join(bad)
    records = [cls for module, tree in _modules() for cls in ast.walk(tree)
               if isinstance(cls, ast.ClassDef) and [ast.unparse(d) for d in cls.decorator_list] == ["record"]]
    assert len(records) >= 25


def test_no_vars_view_is_handed_out():
    bad = [f"{module}:{node.lineno}" for module, tree in _modules() for node in ast.walk(tree) if _calls(node, "vars")]
    assert bad == [], "vars() hands out an object's own __dict__: " + ", ".join(bad)


def test_a_snapshot_is_deep_and_immutable_and_refuses_what_it_cannot_freeze():
    value = {"a": [1, {"b": ["x"]}], "t": (3,)}
    frozen = snapshot(value)
    assert frozen == {"a": (1, {"b": ("x",)}), "t": (3,)}
    assert isinstance(frozen, FrozenMap) and isinstance(frozen["a"][1], FrozenMap)
    value["a"][1]["b"].append("y")
    value["a"].append(2)
    assert frozen["a"] == (1, {"b": ("x",)})
    # what is not plain JSON data (a buffer, a set) is refused, not converted (follow-up 2 of PR #8)
    for refused in ({"b": bytearray(b"x")}, {"s": {1, 2}}):
        with pytest.raises(TypeError):
            snapshot(refused)
    for write in (lambda: frozen.__setitem__("a", 1), lambda: frozen.update(a=1), lambda: frozen.pop("a"),
                  lambda: frozen.setdefault("z", 1), frozen.clear, frozen.popitem, lambda: frozen.__delitem__("a")):
        with pytest.raises(TypeError):
            write()
    with pytest.raises(TypeError):
        frozen |= {"z": 1}
    with pytest.raises(TypeError):
        snapshot(object())
    with pytest.raises(TypeError):  # code is kept only in a record's code field
        snapshot(len)


def test_a_record_holds_snapshots_before_its_own_checks_run():
    seen = []

    @record
    class Sample:
        items: list
        mapping: dict = dataclasses.field(default_factory=dict)

        def __post_init__(self):
            seen.append((type(self.items), type(self.mapping)))

    given = [1, [2]]
    made = Sample(given, {"k": [3]})
    given.append(4)
    assert made.items == (1, (2,)) and made.mapping == {"k": (3,)}
    assert seen == [(tuple, FrozenMap)]
    # a snapshot of a record is the record made again: equal, and sharing nothing (follow-up 2 of PR #8)
    assert snapshot(made) == made and snapshot(made) is not made
    assert dataclasses.replace(made, items=[5]).items == (5,)
    with pytest.raises(dataclasses.FrozenInstanceError):
        made.items = ()
