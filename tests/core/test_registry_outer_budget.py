# SPDX-License-Identifier: Apache-2.0
"""The registry and the checked door read the outer mapping of an op's parameters within a visit budget
(review-pr8fu3r4, finding 2; the certificate issuance window before 0.3.0).

``ComponentRegistry.invoke`` built ``{**params, "op": op}`` and the checked door ``dict(params)``. For anything but a
``dict``, Python lists every key of the mapping before it copies one, so a mapping that keeps answering keys was
listed whole before the block's own screen could count anything. Both now read the outer mapping through one reader
(``g6.core.registry.outer_copy``): a ``dict``'s length is read before it is copied, any other mapping's keys are
streamed and counted, and the read stops at the first key past the budget, before that key's value is looked up. A
mapping inside the budget is read exactly as before.
"""
from __future__ import annotations

from collections.abc import Mapping

import pytest

from g6.cognitive_bridge import snapshot
from g6.core import door, registry as registry_module
from g6.core.registry import ComponentRef, ComponentRegistry

PLAIN = '''
from g6.core.ai_block import AIBlock
from g6.core.result import Result

SEEN: list = []


class PlainBlock(AIBlock[dict, dict, dict]):
    def __init__(self, name: str = "plain_outer", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: dict) -> Result[dict]:
        SEEN.append(data)
        return Result.ok({"keys": list(data)})


__g6_block__ = "PlainBlock"
'''
SCREENED = PLAIN.replace("    def infer", '''    def screen_raw(self, raw):
        SEEN.append(("screen", raw))
        return None

    def infer''').replace("PlainBlock", "ScreenedBlock").replace("plain_outer", "screened_outer")

#: The most entries the budget lets through: the mapping is one visit, and each key and each value one more.
MOST = (snapshot.MAX_ITEMS - 1) // 2


class Stop(BaseException):
    """Ends a probe that would otherwise never end; no reader under test may swallow it."""


class Endless:
    """A mapping by protocol whose keys never end; it counts each key it hands over and each value looked up."""

    def __init__(self, stop_after: int = 4 * snapshot.MAX_ITEMS) -> None:
        self.keys_read, self.looked_up, self.stop_after = 0, 0, stop_after

    def keys(self):
        while True:
            self.keys_read += 1
            if self.keys_read > self.stop_after:
                raise Stop
            yield f"k{self.keys_read}"

    def __getitem__(self, key):
        self.looked_up += 1
        return 0


class EndlessMapping(Endless, Mapping):
    """The same, as a ``collections.abc.Mapping``, which the checked door takes."""

    def __iter__(self):
        return self.keys()

    def __len__(self):
        return 1


class Counted:
    """A mapping by protocol of ``count`` entries."""

    def __init__(self, count: int) -> None:
        self.count = count

    def keys(self):
        return (f"k{index}" for index in range(self.count))

    def __getitem__(self, key):
        return key.upper()


@pytest.fixture(params=["plain", "screened"])
def component(request, tmp_path, monkeypatch):
    name = f"{request.param}_outer"
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text(PLAIN if request.param == "plain" else SCREENED, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = ComponentRegistry()
    registry._refs = {name: ComponentRef(name, name, "builtin")}
    module = registry.get_module(name)
    module.SEEN.clear()
    return registry, name, module


def test_the_budget_is_the_bridges_visit_budget():
    assert registry_module.OUTER_VISITS == snapshot.MAX_ITEMS


def test_the_registry_stops_reading_keys_at_the_budget_and_the_block_sees_nothing(component):
    registry, name, module = component
    params = Endless()
    try:
        outcome = registry.invoke(name, "run", params)
    except Stop:
        outcome = None
    assert params.keys_read <= snapshot.MAX_ITEMS, (
        f"the registry read {params.keys_read} outer keys: the visit budget is {snapshot.MAX_ITEMS}")
    assert params.keys_read == MOST + 1 and params.looked_up == MOST, "the key past the budget is never looked up"
    assert outcome.__class__.__name__ == "Failure" and str(outcome.failure()).startswith("[G6_E_INPUT_VALIDATION]")
    assert module.SEEN == []


def test_the_checked_door_stops_reading_keys_at_the_budget(component):
    registry, name, module = component
    params = EndlessMapping()
    try:
        reply = door.invoke_checked(name, "run", params, allow=frozenset(), how_to_allow="", registry=registry)
    except Stop:
        reply = None
    assert params.keys_read == MOST + 1 and params.looked_up == MOST
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and "result" not in reply
    assert module.SEEN == []


@pytest.mark.parametrize("kind", ["dict", "subclass"])
def test_a_dict_past_the_budget_is_refused_by_its_length_before_it_is_copied(component, kind):
    registry, name, module = component

    class Foreign(dict):
        def keys(self):
            raise AssertionError("a dict's own storage is read, never this")

    params = dict.fromkeys(range(MOST + 1), 0)
    params = Foreign(params) if kind == "subclass" else params
    with pytest.raises(registry_module.OuterBudgetExceeded):
        registry_module.outer_copy(params)
    outcome = registry.invoke(name, "run", params)
    assert str(outcome.failure()).startswith("[G6_E_INPUT_VALIDATION]") and module.SEEN == []
    reply = door.invoke_checked(name, "run", params, allow=frozenset(), how_to_allow="", registry=registry)
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and module.SEEN == []


def test_the_most_entries_the_budget_allows_are_read(component):
    registry, name, _ = component
    assert len(registry_module.outer_copy(dict.fromkeys(range(MOST), 0))) == MOST
    assert len(registry_module.outer_copy(Counted(MOST))) == MOST
    with pytest.raises(registry_module.OuterBudgetExceeded):
        registry_module.outer_copy(Counted(MOST + 1))
    assert registry.invoke(name, "run", Counted(3)).unwrap() == {"keys": ["k0", "k1", "k2", "op"]}


def test_a_mapping_inside_the_budget_is_read_as_unpacking_reads_it():
    """The copy is ``{**params}``: the same entries in the same order, from a ``dict``'s own storage when its
    iteration is ``dict``'s, by ``keys()`` and a lookup otherwise, with any key a ``dict`` takes."""

    class Renamed(dict):
        def __iter__(self):
            return iter(["shown"])

        def keys(self):
            return ["shown"]

        def __getitem__(self, key):
            return "by protocol"

    class Native(dict):
        def keys(self):
            raise AssertionError("not read")

        def __getitem__(self, key):
            raise AssertionError("not read")

    for params in ({}, {"b": 1, "a": [2]}, {1: "one", ("t",): None}, Counted(5), Renamed(hidden=1), Native(a=1, b=2)):
        copied = registry_module.outer_copy(params)
        assert type(copied) is dict and copied == {**params} and list(copied) == list({**params})
        assert copied is not params
    with pytest.raises(TypeError, match="not a mapping"):
        registry_module.outer_copy([("a", 1)])


def test_parameters_that_are_no_mapping_are_refused_as_before(component):
    registry, name, module = component
    if name.startswith("screened"):   # a screen that cannot be handed its parameters refuses the call
        assert str(registry.invoke(name, "run", [1]).failure()).startswith("[G6_E_SCREEN_RAW_FAILED]")
    else:
        with pytest.raises(TypeError, match="not a mapping"):
            registry.invoke(name, "run", [1])
    assert module.SEEN == []
    assert registry.invoke(name, "run", None).unwrap() == {"keys": ["op"]}


# --- every other way to a component reads a caller's mapping through the same reader (issuance review, finding 5) ----
# ``GatedCalls.call`` (how an agent framework's adapter calls a tool or a component's op) expanded its arguments with
# ``dict(...)`` before anything counted them, and a composition's stage did the same with its parameters and its
# input links. Each now reads the mapping through ``outer_copy`` first.


class NeverAsked:
    """A gate that must not be reached: the arguments are read, and refused, before any decision is asked for."""

    def decide(self, call, changes=False):   # pragma: no cover - reaching it is the failure
        raise AssertionError("the gate was asked")


class Allows:
    def decide(self, call, changes=False):
        from types import SimpleNamespace

        self.call = call
        return SimpleNamespace(runs=True, verdict="run", code="", reason="")


@pytest.mark.parametrize("mapping", [Endless, EndlessMapping], ids=["protocol", "Mapping"])
@pytest.mark.parametrize("name", ["plain_outer.run", "a_tool"])
def test_an_agents_tool_call_stops_reading_its_arguments_at_the_budget_before_the_gate_is_asked(mapping, name):
    from g6.agents._runtime.tools import GatedCalls

    ran: list = []
    arguments = mapping()
    try:
        reply = GatedCalls(NeverAsked(), {"a_tool": lambda **given: ran.append(given)}).call(name, arguments)
    except Stop:
        reply = None
    assert arguments.keys_read == MOST + 1 and arguments.looked_up == MOST, (
        f"the call read {arguments.keys_read} keys of its arguments: the visit budget is {snapshot.MAX_ITEMS}")
    assert reply["verdict"] == "refused" and reply["code"] == "G6_E_INPUT_VALIDATION" and ran == []
    assert str(snapshot.MAX_ITEMS) in reply["reason"]


def test_an_agents_tool_call_refuses_a_dict_past_the_budget_and_reads_one_inside_it_as_before():
    from g6.agents._runtime import codes
    from g6.agents._runtime.tools import GatedCalls

    gate, given = Allows(), {"text": "hello", "times": 2}
    calls = GatedCalls(gate, {"a_tool": lambda **kwargs: kwargs})
    assert calls.call("a_tool", given) == {"verdict": "run", "code": codes.TOOL_ALLOWED, "result": given}
    assert gate.call.arguments == given and gate.call.arguments is not given
    assert calls.call("a_tool", Counted(3))["result"] == {"k0": "K0", "k1": "K1", "k2": "K2"}
    assert calls.call("a_tool", None)["result"] == {} and calls.call("a_tool")["result"] == {}
    past = GatedCalls(NeverAsked(), {"a_tool": lambda **kwargs: kwargs}).call(
        "a_tool", dict.fromkeys((f"k{index}" for index in range(MOST + 1)), 0))
    assert past["verdict"] == "refused" and past["code"] == "G6_E_INPUT_VALIDATION"


@pytest.mark.parametrize("field", ["params", "inputs"])
def test_a_compositions_stage_stops_reading_its_parameters_and_its_links_at_the_budget(field):
    from g6.core.ai_block import AIBlock
    from g6.core.block_contract import BlockContract
    from g6.core.compose import CompositionError, Stage, compose

    class Contracted(AIBlock):
        __g6_block_contract__ = BlockContract(name="contracted", verification_method="unit_tests_only",
                                              outputs_per_op={"run": ("keys",)})

        def __init__(self) -> None:
            super().__init__("contracted")

    given = EndlessMapping()
    try:
        with pytest.raises(CompositionError, match=r"\[G6_E_INPUT_VALIDATION\] stage 0: "):
            compose(Stage(Contracted(), "run", **{field: given}), caller_inputs=())
    except Stop:
        pass
    assert given.keys_read == MOST + 1 and given.looked_up == MOST, (
        f"the composition read {given.keys_read} keys of a stage's {field}: the visit budget is {snapshot.MAX_ITEMS}")
    built = compose(Stage(Contracted(), "run", params=Counted(2)), caller_inputs=())
    assert built.manifest[0]["params"] == {"k0": "K0", "k1": "K1"}
