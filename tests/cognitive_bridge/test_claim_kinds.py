# SPDX-License-Identifier: Apache-2.0
"""The class guard_covers_less_than_it_guards, removed at its root for the claim rule (``codes.claims``): met in repair 2,
again in follow-up review 3's findings 1 and 2, and again in follow-up review 4's finding 3, each time a kind of object
the rule opened kept something it did not read (a set's attributes, a deque subclass's, a record used as a key).

Since follow-up 2 of PR #8 the bridge holds plain JSON data alone, taken at the boundary (``snapshot.plain``), so the
rule walks JSON alone and opens nothing else:

- a leaf, by its exact type, is read as itself (``codes.LEAVES``: text, an int, a float, a bool, None);
- an object (``codes.OBJECTS``: a dict, or the read-only ``FrozenMap`` a record holds), whose every key is text, and an
  array (``codes.ARRAYS``: a list, or the tuple a record holds) are read item by item;
- everything else is refused, its path and type named, wherever it stands: the kinds the rule used to open (a set, a
  frozenset, a deque, an ``OrderedDict``, a read-only mapping proxy, a bridge record, bytes) and a key that is not text
  among them.

This lists every kind the walk reads, from the code, and plants a claim inside each one, and every other kind, at each
place the rule guards: the rule itself, a tool's answer through the executor and through the agent's door, a
collaborator's answer as it returns (``snapshot.returned``) and both makers."""
from __future__ import annotations

import ast
import enum
import inspect
from collections import OrderedDict, deque
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
    """A hashable bridge record that says it is verified: what a set or a key could hold."""
    verified: bool = True
    reliability_label: str = "verified"


@record
class Holder:
    inner: Any = None


#: Every kind the walk reads as JSON, each made holding a claim.
JSON_CASES = {
    "dict": lambda c: {"k": c},
    "FrozenMap": lambda c: FrozenMap({"k": c}),
    "list": lambda c: [c],
    "tuple": lambda c: (c,),
}
#: The kinds the rule opened before follow-up 2 of PR #8, now refused as what they are, whatever they hold.
FORMERLY_OPENED = {
    "OrderedDict": lambda: OrderedDict(k=CLAIM),
    "MappingProxyType": lambda: MappingProxyType({"k": CLAIM}),
    "deque": lambda: deque([CLAIM]),
    "set": lambda: {Flag()},
    "frozenset": lambda: frozenset({Flag()}),
    "record": lambda: Flag(),
    "bytes": lambda: b"bytes",
}
#: A key that is not text: the key itself a record, a tuple, a frozenset, a number; and one nested inside a value.
KEY_CASES = {
    "record-key": lambda: {Flag(): "x"},
    "tuple-key": lambda: {("ok", "x"): "x"},
    "frozenset-key": lambda: {frozenset({"x"}): "x"},
    "int-key": lambda: {1: "x"},
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


#: Kinds the rule refuses even when nothing in them claims anything: each is made fresh, and whether it is hashable.
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
    "bytes": (lambda: b"clean", True),
    "set": (lambda: {1}, False),
    "deque": (lambda: deque([1]), False),
}
LEAF_CASES = {"str": "text", "int": 3, "float": 2.5, "bool": False, "None": None}


def _placed(value, where):
    """``value`` standing at ``where``: alone, as an object's value, as a list's item."""
    return {"top": lambda: value, "value": lambda: {"k": value}, "item": lambda: ["ok", value]}[where]()


PLACES = ("top", "value", "item")


# --- The inventory, from the code ------------------------------------------------------------------------------------

def test_the_walk_reads_json_alone():
    assert codes.LEAVES == (str, int, float, bool, type(None))
    assert codes.OBJECTS == (dict, FrozenMap)
    assert codes.ARRAYS == (list, tuple)
    assert {type(v) for v in LEAF_CASES.values()} == set(codes.LEAVES)
    # every kind the walk reads has a case here that plants a claim in it
    assert {type(make(CLAIM)) for make in JSON_CASES.values()} == set(codes.OBJECTS + codes.ARRAYS)


def _source(function):
    return ast.parse(inspect.getsource(function).lstrip())


def test_one_classifier_decides_how_a_value_is_read_and_it_reads_only_the_lists():
    # claims never tests a type itself: it asks codes._read_as, and _read_as tests a value's exact type against LEAVES,
    # OBJECTS and ARRAYS only (no isinstance, so no subclass is read as what it extends).
    for function in (codes.claims, codes._read_as):
        tested = [n for n in ast.walk(_source(function)) if isinstance(n, ast.Call)
                  and getattr(n.func, "id", "") in ("isinstance", "issubclass")]
        assert tested == [], [ast.unparse(n) for n in tested]
    in_claims = [n for n in ast.walk(_source(codes.claims)) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "type"]
    assert in_claims == [], [ast.unparse(n) for n in in_claims]
    names = {n.id for n in ast.walk(_source(codes._read_as)) if isinstance(n, ast.Name)}
    assert {"LEAVES", "OBJECTS", "ARRAYS"} <= names


# --- The rule itself -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(JSON_CASES))
def test_a_claim_inside_each_json_kind_is_found(kind):
    found = codes.claims(JSON_CASES[kind](CLAIM))
    assert any("verified" in place for place in found), f"a claim inside a {kind} was not read: {found}"
    assert codes.claims(JSON_CASES[kind]("clean")) == [], f"a clean {kind} was refused"


@pytest.mark.parametrize("kind", sorted(FORMERLY_OPENED))
@pytest.mark.parametrize("where", PLACES)
def test_a_kind_the_rule_used_to_open_is_refused_as_what_it_is(kind, where):
    value = FORMERLY_OPENED[kind]()
    found = codes.claims(_placed(value, where))
    assert len(found) == 1 and type(value).__qualname__ in found[0] and "not JSON data" in found[0], found


@pytest.mark.parametrize("kind", sorted(KEY_CASES))
def test_a_key_that_is_not_text_is_refused_with_its_type(kind):
    found = codes.claims(KEY_CASES[kind]())
    assert len(found) == 1 and "(the key, which is not text)" in found[0], found


@pytest.mark.parametrize("leaf", sorted(LEAF_CASES))
@pytest.mark.parametrize("where", PLACES)
def test_each_leaf_is_accepted_wherever_it_stands(leaf, where):
    assert codes.claims(_placed(LEAF_CASES[leaf], where)) == []


@pytest.mark.parametrize("leaf", sorted(LEAF_CASES))
def test_only_text_is_a_key(leaf):
    found = codes.claims({LEAF_CASES[leaf]: "x"})
    assert (found == []) == (leaf == "str"), found


@pytest.mark.parametrize("where", PLACES)
@pytest.mark.parametrize("kind", sorted(REFUSED_CASES))
def test_every_other_kind_is_refused_wherever_it_stands_with_its_path_and_type(kind, where):
    make, _ = REFUSED_CASES[kind]
    value = make()
    placed = _placed(value, where)
    found = codes.claims(placed)
    assert len(found) == 1, f"a {kind} at {where} was not refused once: {found}"
    assert type(value).__qualname__ in found[0], f"the refusal does not name the type: {found[0]}"
    path = {"top": "the value", "value": "k", "item": "[1]"}[where]
    assert path in found[0], f"the refusal does not name the path: {found[0]}"
    with pytest.raises(ValueError, match="claims a state"):
        codes.unclaimed(placed, where="a value")


@pytest.mark.parametrize("kind", sorted(REFUSED_CASES))
def test_a_record_refuses_it_where_it_is_made(kind):
    with pytest.raises(ValueError):
        Holder(REFUSED_CASES[kind][0]())


def test_an_unlisted_kind_is_refused_however_it_is_made():
    class NobodyListedThis:
        def __iter__(self):
            return iter([CLAIM])

    for value in (NobodyListedThis(), {"k": NobodyListedThis()}, [{"k": NobodyListedThis()}]):
        assert codes.claims(value), "a kind the walk does not list passed unread"


# --- Each place the rule guards ---------------------------------------------------------------------------------------

def _plants():
    """Every planting: a claim inside each JSON kind, each kind the rule used to open, each key that is not text, and
    each refused kind (clean) as a value."""
    out = {f"claim-in-{k}": (lambda make=make: make(CLAIM)) for k, make in JSON_CASES.items()}
    out.update({f"formerly-opened-{k}": make for k, make in FORMERLY_OPENED.items()})
    out.update({f"key-{k}": make for k, make in KEY_CASES.items()})
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
    with pytest.raises(ValueError):
        snapshot.returned(PLANTS[plant](), _as_given)


@pytest.mark.parametrize("maker", ["refusal", "settled"])
@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_neither_maker_lets_it_out(plant, maker):
    make = {"refusal": lambda **f: codes.refusal(codes.INPUT, "a reason", **f),
            "settled": lambda **f: codes.settled(None, **f)}[maker]
    with pytest.raises(ValueError, match="claims a state"):
        make(detail=PLANTS[plant]())
