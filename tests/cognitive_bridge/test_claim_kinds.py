# SPDX-License-Identifier: Apache-2.0
"""The class guard_covers_less_than_it_guards, removed at its root for the claim rule (``codes.claims``): met in repair 2
and again in follow-up review 3's findings 1 and 2. The rule's walk was a list of the types it opened, and anything else
passed unread; a mapping's keys were read by name only. It is now an allow-list that fails closed:

- a leaf, by its exact type, is read as nothing more (``codes.LEAVES``: text, bytes, an int, a float, a bool, None);
- a mapping (its keys and its values, each read as a value), a sequence and a set (``codes.OPENED``; a frozenset is a
  set) are opened item by item, and a bridge record (``snapshot.record``) field by field;
- every other object is refused, its path and type named, wherever it stands.

This lists every kind the walk accepts or opens, from the code, and plants a claim inside each one, inside a mapping's
key, and inside kinds it must refuse, at each place the rule guards: the rule itself, a tool's answer through the
executor and through the agent's door, a collaborator's answer as it returns (``snapshot.returned``) and both makers."""
from __future__ import annotations

import ast
import enum
import inspect
from collections import OrderedDict, deque
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest
from returns.result import Success

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAgent, Hypothesis, MethodRouter, MixedMethodExecutor, Observation, Probe, StaticProposer,
    ToolSpec, codes, snapshot,
)
from g6.cognitive_bridge.snapshot import FrozenMap, record
from g6.core import door

CLAIM = {"verified": True, "reliability_label": "verified"}


@record
class Flag:
    """A claim a set or a key can hold: a hashable bridge record that says it is verified."""
    verified: bool = True
    reliability_label: str = "verified"


@record
class Holder:
    inner: Any = None


def _hashable(claim):
    return Flag() if claim is CLAIM else claim


#: Every kind the walk opens, each made holding a claim, and the kind of codes.OPENED (or a record) it is.
OPENED_CASES = {
    "dict": lambda c: {"k": c},
    "FrozenMap": lambda c: FrozenMap({"k": c}),
    "OrderedDict": lambda c: OrderedDict(k=c),
    "MappingProxyType": lambda c: MappingProxyType({"k": c}),
    "list": lambda c: [c],
    "tuple": lambda c: (c,),
    "deque": lambda c: deque([c]),
    "set": lambda c: {_hashable(c)},
    "frozenset": lambda c: frozenset({_hashable(c)}),
    "record": lambda c: Holder(c),
}
#: A claim inside a mapping's key: the key itself, a tuple key holding it, a frozenset key holding it.
KEY_CASES = {
    "record-key": lambda: {Flag(): "x"},
    "tuple-key": lambda: {("ok", Flag()): "x"},
    "frozenset-key": lambda: {frozenset({Flag()}): "x"},
    "nested-key": lambda: {"outer": {Flag(): "x"}},
}


@dataclass(frozen=True)
class Plain:
    note: str = "clean"


class Text(str):
    pass


class Number(int):
    pass


class Real(float):
    pass


class Colour(enum.Enum):
    RED = "red"


class LooksLikeARecord:
    __snapshot_record__ = True


#: Kinds the rule must refuse even when nothing in them claims anything: each is made fresh, and whether it is hashable.
REFUSED_CASES = {
    "plain-dataclass": (lambda: Plain(), True),
    "namespace": (lambda: SimpleNamespace(note="clean"), False),
    "object": (lambda: object(), True),
    "text-subclass": (lambda: Text("clean"), True),
    "int-subclass": (lambda: Number(1), True),
    "float-subclass": (lambda: Real(1.0), True),
    "enum": (lambda: Colour.RED, True),
    "function": (lambda: (lambda: None), True),
    "generator": (lambda: (x for x in ()), True),
    "class": (lambda: Plain, True),
    "says-it-is-a-record": (lambda: LooksLikeARecord(), True),
}
LEAF_CASES = {"str": "text", "bytes": b"bytes", "int": 3, "float": 2.5, "bool": False, "None": None}


def _placed(value, where, hashable=True):
    """``value`` standing at ``where``: alone, as a mapping's value, as its key, as a list's item or a set's item."""
    return {"top": lambda: value, "value": lambda: {"k": value}, "key": lambda: {value: "x"},
            "item": lambda: ["ok", value], "set-item": lambda: frozenset({value}),
            "record-field": lambda: Holder(value)}[where]()


PLACES = ("top", "value", "key", "item", "set-item", "record-field")


# --- The inventory, from the code ------------------------------------------------------------------------------------

def test_the_walk_accepts_and_opens_exactly_what_it_lists():
    assert codes.LEAVES == (str, bytes, int, float, bool, type(None))
    assert codes.OPENED == (Mapping, Sequence, Set)
    assert {type(v) for v in LEAF_CASES.values()} == set(codes.LEAVES)
    # every kind the walk opens has a case here that plants a claim in it
    for kind in codes.OPENED:
        assert any(isinstance(make(CLAIM), kind) for make in OPENED_CASES.values()), kind
    assert any(getattr(type(make(CLAIM)), "__snapshot_record__", False) for make in OPENED_CASES.values())


def _source(function):
    return ast.parse(inspect.getsource(function).lstrip())


def test_one_classifier_decides_how_a_value_is_read_and_it_reads_only_the_lists():
    # claims never tests a type itself: it asks codes._read_as, and _read_as tests only LEAVES, OPENED, the record mark,
    # and that text and bytes are never opened as their characters.
    in_claims = [n for n in ast.walk(_source(codes.claims)) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") in ("isinstance", "issubclass", "type")]
    assert in_claims == [], [ast.unparse(n) for n in in_claims]
    tested = {ast.unparse(n.args[1]) for n in ast.walk(_source(codes._read_as)) if isinstance(n, ast.Call)
              and getattr(n.func, "id", "") == "isinstance"}
    assert tested <= {"OPENED", "kind", "(str, bytes)"}, tested
    names = {n.id for n in ast.walk(_source(codes._read_as)) if isinstance(n, ast.Name)}
    assert {"LEAVES", "OPENED"} <= names


# --- The rule itself -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(OPENED_CASES))
def test_a_claim_inside_each_opened_kind_is_found(kind):
    found = codes.claims(OPENED_CASES[kind](CLAIM))
    assert any("verified" in place for place in found), f"a claim inside a {kind} was not read: {found}"
    assert codes.claims(OPENED_CASES[kind]("clean")) == [], f"a clean {kind} was refused"


@pytest.mark.parametrize("kind", sorted(KEY_CASES))
def test_a_claim_inside_a_key_is_found(kind):
    found = codes.claims(KEY_CASES[kind]())
    assert any("verified" in place for place in found), f"a claim in a {kind} was not read: {found}"


@pytest.mark.parametrize("leaf", sorted(LEAF_CASES))
@pytest.mark.parametrize("where", PLACES)
def test_each_leaf_is_accepted_wherever_it_stands(leaf, where):
    assert codes.claims(_placed(LEAF_CASES[leaf], where)) == []


@pytest.mark.parametrize("where", PLACES)
@pytest.mark.parametrize("kind", sorted(REFUSED_CASES))
def test_every_other_kind_is_refused_wherever_it_stands_with_its_path_and_type(kind, where):
    make, hashable = REFUSED_CASES[kind]
    if where in ("key", "set-item") and not hashable:
        pytest.skip("a mapping's key and a set's item are hashable")
    value = make()
    try:
        placed = _placed(value, where)
    except TypeError:
        assert where == "record-field", "only a record refuses what it cannot hold, as it is made"
        return  # a record snapshots its fields: it holds no object a snapshot does not hold
    if where == "record-field" and type(placed.inner) in codes.LEAVES:
        assert codes.claims(placed) == []
        return  # a record snapshots a subclass of text or a number as its plain value: it holds no subclass
    found = codes.claims(placed)
    assert len(found) == 1, f"a {kind} at {where} was not refused once: {found}"
    assert type(value).__qualname__ in found[0], f"the refusal does not name the type: {found[0]}"
    path = {"top": "the value", "value": "k", "key": "(the key)", "item": "[1]", "set-item": "[0]",
            "record-field": "inner"}[where]
    assert path in found[0], f"the refusal does not name the path: {found[0]}"
    with pytest.raises(ValueError, match="claims a state"):
        codes.unclaimed(placed, where="a value")


def test_an_unlisted_kind_is_refused_however_it_is_made():
    class NobodyListedThis:
        def __iter__(self):
            return iter([CLAIM])

    for value in (NobodyListedThis(), {"k": NobodyListedThis()}, [{"k": NobodyListedThis()}]):
        assert codes.claims(value), "a kind the walk does not list passed unread"


# --- Each place the rule guards ---------------------------------------------------------------------------------------

def _plants():
    """Every planting: a claim inside each opened kind and inside each key, and each refused kind (clean) as a value."""
    out = {f"claim-in-{k}": (lambda make=make: make(CLAIM)) for k, make in OPENED_CASES.items()}
    out.update({f"claim-in-{k}": make for k, make in KEY_CASES.items()})
    out.update({f"refused-{k}": (lambda make=make: {"k": make()}) for k, (make, _) in REFUSED_CASES.items()})
    return out


PLANTS = _plants()


def _plan():
    return MethodRouter().plan([ClaimIR("c", "check A", ("formal",), {"formal": {"formula": "A"}})])


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_a_tools_answer_carrying_it_fails_its_step_in_the_executor(plant):
    answer = PLANTS[plant]()

    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            return Success({"completion_state": "qualified-draft", "answer": answer})

    out = MixedMethodExecutor(Registry()).execute(_plan(), approved=True)
    step = out["steps"][0]
    assert out["completion_state"] == "blocked-escalated" and step["code"] == codes.STEP_FAILED, \
        f"a tool's answer with {plant} was credited"
    assert "result" not in step


def test_a_tools_answer_of_every_leaf_is_credited():
    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            return Success({"completion_state": "qualified-draft", "answer": dict(LEAF_CASES),
                            "rows": [list(LEAF_CASES.values())]})

    out = MixedMethodExecutor(Registry()).execute(_plan(), approved=True)
    assert out["completion_state"] == "qualified-draft", out["steps"][0].get("reason")


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_a_door_reply_carrying_it_is_not_credited_to_the_agent(plant, monkeypatch):
    real = door.invoke_checked

    def planted(*args, **kwargs):
        reply = real(*args, **kwargs)
        return {**reply, "result": {**reply["result"], "planted": PLANTS[plant]()}}

    monkeypatch.setattr(door, "invoke_checked", planted)
    out = CognitiveAgent(StaticProposer([Probe("p", "measure", "read", {"A": True, "B": False}, .8)]), {
        "read": ToolSpec("read", lambda q, a: Observation(True, "source:actual"))}).run(
        "measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)
    assert out["history"] == [] and out["completion_state"] == "blocked-escalated", f"{plant} reached the history"
    assert out["code"] == codes.STEP_FAILED


def _as_given(value):
    return value


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_a_collaborators_answer_carrying_it_is_refused_as_it_returns(plant):
    try:
        out = snapshot.returned(PLANTS[plant](), _as_given)
    except (TypeError, ValueError):
        return
    # what the bridge uses is the snapshot: it holds a subclass of text or a number as its plain value
    assert plant in ("refused-text-subclass", "refused-int-subclass", "refused-float-subclass"), \
        f"a collaborator's answer with {plant} was taken"
    assert codes.claims(out) == [] and type(out["k"]) in codes.LEAVES


@pytest.mark.parametrize("maker", ["refusal", "settled"])
@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_neither_maker_lets_it_out(plant, maker):
    make = {"refusal": lambda **f: codes.refusal(codes.INPUT, "a reason", **f),
            "settled": lambda **f: codes.settled(None, **f)}[maker]
    with pytest.raises(ValueError, match="claims a state"):
        make(detail=PLANTS[plant]())
