# SPDX-License-Identifier: Apache-2.0
"""PR #8's fifth follow-up: the copy pass is the one read of a value.

Three rounds found a bound's read and the copier's read of one value disagreeing (a count read before a second
iteration, a size read under a budget of its own, a size read three to five times under the copier's budget). The
class is removed: no bound walks a value. A count is read from the native length of the walker's one take; a declared
size is measured by the copy pass as it copies. This file holds that as inventories from the code:

1. no admission function, and nothing handed to a copier as a bound, calls anything that walks a value;
2. under every walk (one budget) of every bounded entry, a planted leaf is reached at most as often as it stands in
   the value that walk was handed: nothing is read twice against one budget;
3. the number of times a whole public call reaches a planted model leaf is the measured one below, entry by entry:
   one more pass over a value anywhere fails here;
4. the declared sizes stand where the one declaration says, at every door, and the caps the bridge states are met.
"""
import ast
import dataclasses

import pytest
from pydantic import BaseModel

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAssuranceOrchestrator, MetacognitiveMethodSelector, MethodRouter, snapshot,
)
from g6.cognitive_bridge import mixed_methods
from g6.cognitive_bridge.block import CognitiveBridgeBlock, CognitiveInput
from g6.cognitive_bridge.codes import FrozenMap, NotPlainData
from tests.cognitive_bridge import test_followup4_repair2_inventory as repair2
from tests.cognitive_bridge import test_followup4_repair3_inventory as repair3

MARK = "the planted model leaf of follow-up 5"
#: Everything in the boundary that walks a value (a pass over it, or over a copy of it).
WALKERS = {"_walk", "view", "_bounded_view", "_admit_json", "canonical_json", "plain", "snapshot", "fresh",
           "record_data", "_not_json", "_encoded_size", "_taken", "_frozen", "_same", "_has_record", "_prompt_value",
           "returned", "digest", "publish"}


# --- 1. No bound walks a value ----------------------------------------------------------------------------------------

def _bounds():
    """Every function that admits a value before or while it is copied, from the code: each function named
    ``_admit...``, and each function handed to a copier as ``bound=`` or to ``bounded`` as ``admit=``."""
    functions = {(module, name): node for module, name, node in repair2._functions()}
    handed = {ast.unparse(keyword.value) for node in functions.values() for call in ast.walk(node)
              if isinstance(call, ast.Call) for keyword in call.keywords if keyword.arg in ("bound", "admit")}
    handed |= {ast.unparse(keyword.value) for tree in repair2.SOURCES.values() for call in ast.walk(tree)
               if isinstance(call, ast.Call) for keyword in call.keywords if keyword.arg in ("bound", "admit")}
    for (module, name), node in sorted(functions.items()):
        last = name.split(".")[-1]
        if last.startswith("_admit") or last in handed:
            yield module, name, node


#: An admission that makes a pass over a value, with why it is not a bound that reads a copy's value again.
PASSES = {
    ("snapshot.py", "_admit_json"): "the encoder's own exact size admission: one pass, under its own budget, over the "
                                    "value the encoder is about to write, once a call (canonical_json, a prompt)",
    ("block.py", "_admit_model_input"): "the copier of the direct-model door: it makes that door's one copy pass "
                                        "itself (_walk, handed the one declared shape), then the round trip",
    ("engine.py", "LLMProposer._admit_reply"): "a model's parsed reply, at most 12,000 characters and 12 probes: each "
                                               "probe's arguments by the encoder's admission; never a caller's value",
}


def test_no_bound_calls_anything_that_walks_a_value():
    found = {(module, name): sorted({call.func.id for call in ast.walk(node) if isinstance(call, ast.Call)
                                     and isinstance(call.func, ast.Name) and call.func.id in WALKERS})
             for module, name, node in _bounds()}
    walking = {key: calls for key, calls in found.items() if calls}
    assert set(walking) == set(PASSES), (
        f"a bound walks a value: {sorted(set(walking) - set(PASSES))}; stale: {sorted(set(PASSES) - set(walking))}")
    assert all(PASSES.values())
    names = {name.split(".")[-1] for _, name in found}
    assert {"_admit_claims", "_admit_claim", "_admit_hypotheses", "_admit_variants", "_admit_obligations",
            "_admit_input", "_admit_fields", "_admit_length", "_admit_held"} <= names
    assert not {"_admit_model", "_admit_models"} & names, "a model is measured by the copy pass, never walked to admit"


def test_no_walk_runs_under_a_budget_handed_from_outside_the_walker():
    """One budget per walk: only the walker's own recursion (and the lazy generator, which spends the walker's budget
    item by item) hands a budget on. No context variable carries one into another walk."""
    passing = sorted({f"{module}:{name}" for module, name, node in repair2._functions() for call in ast.walk(node)
                      if isinstance(call, ast.Call) and ast.unparse(call.func) == "_walk"
                      and any(keyword.arg == "budget" for keyword in call.keywords)})
    assert passing == ["snapshot.py:_walk", "snapshot.py:generated", "snapshot.py:generated.items"]
    assert not hasattr(snapshot, "_COPYING")


# --- 2 and 3. Reads of a planted leaf, per walk and per call --------------------------------------------------------

def _occurs(value, seen=None) -> int:
    """How often ``MARK`` stands in ``value`` (an independent count: lists, tuples, mappings, records, models)."""
    if type(value) is str:
        return int(value == MARK)
    if isinstance(value, (list, tuple)):
        return sum(_occurs(item) for item in value)
    if isinstance(value, dict):
        return sum(_occurs(key) + _occurs(item) for key, item in value.items())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return sum(_occurs(getattr(value, field.name, None)) for field in dataclasses.fields(value))
    if isinstance(value, BaseModel):
        return sum(_occurs(item) for item in vars(value).values())
    return 0


class _Spy:
    """Every walk (one budget) with how often it reached the planted leaf and how often the leaf stood in what it
    was handed; and the whole call's count."""

    def __init__(self, monkeypatch):
        self.real, self.live, self.walks, self.total = snapshot._walk, {}, [], 0
        monkeypatch.setattr(snapshot, "_walk", self)

    def __call__(self, value, where, depth=0, **given):
        budget = given.get("budget")
        root = budget is None or id(budget) not in self.live
        if root:
            budget = given["budget"] = [0, 0] if budget is None else budget
            self.live[id(budget)] = [0, _occurs(value), where]
        if type(value) is str and value == MARK:
            self.live[id(budget)][0] += 1
            self.total += 1
        try:
            return self.real(value, where, depth, **given)
        finally:
            if root:
                self.walks.append(tuple(self.live.pop(id(budget))))


def _marked_claim():
    return repair2._claim(models={"formal": {"formula": MARK}})


def _inputs():
    """A valid input holding the planted leaf once, for every entry that carries a declared size: from repair 2's
    table of bounded parameters (claims and model mappings) and repair 3's block entries."""
    for (entry, parameter), (kind, wrap) in sorted(repair2.CARRIES.items()):
        if kind == "claims":
            yield f"{entry}({parameter})", lambda entry=entry, parameter=parameter, wrap=wrap: (
                repair2.CALLS[entry, parameter], wrap((_marked_claim(),)))
        elif kind == "models":
            yield f"{entry}({parameter})", lambda entry=entry, parameter=parameter: (
                repair2.CALLS[entry, parameter], {"v": {"formula": MARK, "premises": ["A"]}})
    raw = {"op": "plan_methods", "claims": [{"id": "c", "statement": "s", "evidence_needs": ["formal"],
                                             "models": {"formal": {"formula": MARK}}}]}
    for entry, parameter in sorted(repair3.BLOCK):
        method = entry.split(".")[1]
        yield f"{entry}({parameter})", lambda method=method: (
            lambda value: getattr(CognitiveBridgeBlock(), method)(value), dict(raw))
    yield "CognitiveBridgeBlock.infer(typed)", lambda: (
        lambda value: CognitiveBridgeBlock().infer(value), CognitiveInput.model_validate(raw))


#: How often each public call reaches the planted model leaf, over all its walks: measured on this change. A call
#: that makes one more pass over a value fails here; the commit message states the largest.
REACHES: dict[str, int] = {
    'CognitiveAssuranceOrchestrator.assess(compiled)': 8,
    'CognitiveAssuranceOrchestrator.compile(claim)': 9,
    'CognitiveAssuranceOrchestrator.compile(variant_models)': 35,
    'CognitiveAssuranceOrchestrator.execute(compiled)': 14,
    'CompiledResearch.changed_since_compiled(plan)': 7,
    'MetacognitivePlanner.plan(claims)': 74,
    'MethodRouter.plan(claims)': 15,
    'MixedMethodExecutor.execute(plan)': 21,
    'CognitiveBridgeBlock.infer(data)': 70,
    'CognitiveBridgeBlock.screen_raw(raw)': 3,
    'CognitiveBridgeBlock.infer(typed)': 70,
}


def test_the_table_of_measured_reaches_names_every_entry_that_carries_a_declared_size():
    assert set(REACHES) == {name for name, _ in _inputs()}
    assert {"MethodRouter.plan(claims)", "CognitiveBridgeBlock.infer(data)",
            "CognitiveAssuranceOrchestrator.compile(variant_models)",
            "CognitiveAssuranceOrchestrator.execute(compiled)"} <= set(REACHES)


@pytest.mark.parametrize("name", [name for name, _ in _inputs()])
def test_every_walk_reads_a_value_once_and_every_call_as_often_as_measured(name, monkeypatch):
    call, value = dict(_inputs())[name]()
    spy = _Spy(monkeypatch)
    try:
        call(value)
    except ValueError:
        pass
    assert spy.total >= 1, "the planted leaf never reached the walker: the instrument is blind"
    twice = [walk for walk in spy.walks if walk[0] > walk[1]]
    assert twice == [], f"{name}: a walk reached the leaf more often than it stood in its value: {twice}"
    assert spy.total == REACHES.get(name), f"{name}: the call reached the planted leaf {spy.total} times"


def test_one_detachment_of_plain_data_is_three_passes_each_under_its_own_budget(monkeypatch):
    """What one ``snapshot`` of plain data costs: the copy pass, the round trip's pass over the copy, and the
    encoder's projection of it; a bare walk is one."""
    spy = _Spy(monkeypatch)
    snapshot._walk({"formula": MARK}, "a model")
    assert (spy.total, len(spy.walks)) == (1, 1)
    spy = _Spy(monkeypatch)
    snapshot.snapshot({"formula": MARK})
    assert spy.total == 3 and [walk[:2] for walk in spy.walks] == [(1, 1)] * 3


def test_one_detachment_of_a_claim_reads_its_model_as_often_as_measured(monkeypatch):
    """A record is made again where it is detached, so its data field is read by the copy pass and then by the
    record's own making (its walk, the field's round trip and claim rule): measured, and never twice under one
    budget."""
    claim = _marked_claim()
    spy = _Spy(monkeypatch)
    snapshot.snapshot(claim)
    assert all(reached <= held for reached, held, _ in spy.walks)
    assert spy.total == CLAIM_READS, spy.total


#: How often one detachment of a ClaimIR reaches a leaf of its model (see the test above).
CLAIM_READS = 5


# --- 4. The declared sizes, where the one declaration says ----------------------------------------------------------

def test_the_model_cap_is_declared_once_and_read_at_every_door():
    assert snapshot._declared(ClaimIR)[3] is mixed_methods.CLAIM_SHAPE
    assert mixed_methods.CLAIM_SHAPE == ("field", {"models": ("each", mixed_methods.MODEL_CAP)})
    literal = [f"{module}:{name}" for module, name, node in repair2._functions() for constant in ast.walk(node)
               if isinstance(constant, ast.Constant) and constant.value in (250_000, 250000)]
    assert literal == [], "the cap is the one declaration's, never written again"
    handed = sorted({f"{module}:{name}: {ast.unparse(keyword.value)}" for module, name, node in repair2._functions()
                     for call in ast.walk(node) if isinstance(call, ast.Call)
                     for keyword in call.keywords if keyword.arg == "shape" and module != "snapshot.py"})
    assert handed == [
        "block.py:_admit_model_input: _shape(base)",
        "block.py:_as_given: _shape(CognitiveInput)",
        "orchestration.py:CognitiveAssuranceOrchestrator.compile: MODELS",
    ]


def _exactly(size):
    """A model whose canonical encoding is exactly ``size`` bytes."""
    return {"formula": "x" * (size - len('{"formula":""}'))}


@pytest.mark.parametrize("over", [0, 1])
@pytest.mark.parametrize("door", ["record", "list", "plan", "compile", "block", "typed block", "artifact"])
def test_a_declared_size_is_exact_at_every_door(door, over, monkeypatch):
    cap = 2_000_000 if door == "artifact" else 250_000
    model = _exactly(cap + over)
    assert len(snapshot.canonical_json(model, cap=cap + 1)) == cap + over
    claim = ClaimIR("c", "s", ("formal",), {"formal": model})
    raw = {"op": "plan_methods", "claims": [{"id": "c", "statement": "s", "evidence_needs": ["formal"],
                                             "models": {"formal": model}}]}
    calls = {
        "record": lambda: snapshot.snapshot(claim),
        "list": lambda: snapshot.fresh([claim]),
        "plan": lambda: snapshot.snapshot(repair2._plan((claim,))),
        "compile": lambda: repair2._compile(variant_models={"v": model}),
        "block": lambda: CognitiveBridgeBlock().infer(raw).unwrap(),
        "typed block": lambda: CognitiveBridgeBlock().infer(CognitiveInput.model_construct(**raw)).unwrap(),
        "artifact": lambda: CognitiveAssuranceOrchestrator.execution_artifact(model, actor="a", claim_id="c"),
    }
    with repair2.observed(monkeypatch, None, model["formula"]) as seen:
        try:
            out = calls[door]()
            refused = out.get("reason", "") if type(out) is dict and out.get("code") else ""
        except ValueError as exc:
            refused = str(exc)
        if over:
            assert ("artifact exceeds 2MB" if door == "artifact" else "250 kB") in refused
            assert seen == [], f"{door}: a value one byte past its declared size was encoded before the refusal"
    if not over:
        assert "250 kB" not in refused and "artifact exceeds" not in refused, refused


def test_the_meter_writes_what_the_encoder_writes(monkeypatch):
    """The walker's measure of a value is the encoder's own size of it, for every kind of leaf and every escape."""
    value = {"text": 'a"\\\n\t\x00é 😀', "numbers": [0, -17, 2 ** 70, 1.5, -0.0, 1e300], "flags": [True, False, None],
             "nested": {"empty": {}, "list": [], "tuple": ("x", ("y",))}, "": ""}
    size = len(snapshot.canonical_json(value))
    assert snapshot.plain(value, where="a value", cap=size, error="past") is not None
    with pytest.raises(NotPlainData, match="past"):
        snapshot.plain(value, where="a value", cap=size - 1, error="past")
    assert snapshot.snapshot(value, shape=("size", size, "past")) is not None
    with pytest.raises(NotPlainData, match="past"):
        snapshot.snapshot(value, shape=("size", size - 1, "past"))


def test_the_stated_caps_are_met_as_far_as_the_walkers_own_budget_allows():
    """Twenty claims plan, and a model of 250 kB plans, from Python and through the block. Twenty claims of 250 kB
    each (5 MB) are past the walker's own 4 MB budget, which stood before this change and is not this change's to
    raise: a plan holds each model twice (in its claim and in its step), so about 2 MB of models plan."""
    def claims(count, size, raw=False):
        model = _exactly(size)
        if raw:
            return [{"id": f"c{i}", "statement": "s", "evidence_needs": ["formal"], "models": {"formal": model}}
                    for i in range(count)]
        return [ClaimIR(f"c{i}", "s", ("formal",), {"formal": model}) for i in range(count)]

    for count, size in ((1, 250_000), (7, 250_000), (20, 90_000)):
        assert len(MethodRouter().plan(claims(count, size)).steps) == count
        answer = CognitiveBridgeBlock().infer({"op": "plan_methods", "claims": claims(count, size, raw=True)}).unwrap()
        assert answer["completion_state"] == "qualified-draft" and len(answer["steps"]) == count, answer.get("reason")
    with pytest.raises(NotPlainData, match="size exceeds"):
        MethodRouter().plan(claims(20, 250_000))


def test_every_compilation_compile_issues_is_taken_by_its_own_execute():
    """A model at its cap, compiled, is taken by execute and by assess: the copy of a compilation reads each model
    once, and a compilation holds it three times (its claim, its plan's claim, its plan's step)."""
    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            return {"answer": {"ran": False}}

    model = {**_exactly(249_000), "premises": []}
    orchestrator = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=Registry())
    compiled = orchestrator.compile(claim=repair2._claim(models={}), context=repair2._CONTEXT,
                                    standard=repair2._STANDARD, variants=[repair2._VARIANT],
                                    variant_models={"v": model})
    answer = orchestrator.execute(compiled, approved=True)
    assert "cannot be taken" not in answer.get("reason", "") and answer.get("code") != (
        "G6_E_COGNITIVE_EXECUTION_NOT_APPROVED"), answer.get("reason")
    assert type(snapshot.snapshot(compiled)) is type(compiled)


COLLECTIONS = {"list": lambda: [None] * 400_000, "tuple": lambda: (None,) * 400_000,
               "dict": lambda: dict.fromkeys(map(str, range(200_000))),
               "FrozenMap": lambda: FrozenMap(dict.fromkeys(map(str, range(200_000))))}


@pytest.mark.parametrize("kind", sorted(COLLECTIONS))
def test_a_collection_is_refused_by_its_native_length_before_it_is_taken(kind):
    """The length is read natively against the budget before the take: a collection past the budget is refused in
    the memory of a refusal, not of a copy of it (3.2 MB to 6.4 MB here)."""
    import tracemalloc
    value = COLLECTIONS[kind]()
    tracemalloc.start()
    try:
        with pytest.raises(NotPlainData, match="size exceeds"):
            snapshot._walk(value, "a collection")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 200_000, f"{peak:,} bytes were allocated before the refusal"


if __name__ == "__main__":  # prints the measured table
    import sys

    class _Patch:
        def setattr(self, target, name, value):
            setattr(target, name, value)

    for name, make in _inputs():
        call, value = make()
        real = snapshot._walk
        spy = _Spy(_Patch())
        try:
            call(value)
        except ValueError:
            pass
        snapshot._walk = real
        sys.stdout.write(f"    {name!r}: {spy.total},\n")
