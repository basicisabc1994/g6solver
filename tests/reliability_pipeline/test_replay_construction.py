# SPDX-License-Identifier: Apache-2.0
"""Task-conditioned construction, independent of the HIC experiment fixture."""
from __future__ import annotations

import hashlib
import json
import sys
from types import ModuleType

import pytest
from g6.reliability_pipeline import premortem

TASK = ("Make this Python event handler apply each stable event ID at most once, "
        "even when another event arrives between identical retries. Preserve existing tests.")
EXAMPLE = """from stock import receive

def test_update():
    assert receive({"count": 2, "seen": []}, {"id": "delivery-7", "delta": 3}) == {"count": 5, "seen": ["delivery-7"]}
"""


def construct(task=TASK, module="stock.py", function="receive", sources=None, executed=None):
    compiler = getattr(premortem, "construct_replay_check", None)
    assert callable(compiler), "premortem has no fact-conditioned executable check constructor"
    sources = {"tests/test_stock.py": EXAMPLE} if sources is None else sources
    return compiler(task, module=module, function=function, test_sources=sources,
                    executed_tests=tuple(f"{path}::test_update" for path in sources) if executed is None else executed)


def test_construction_binds_task_example_falsifiable_hypothesis_and_executable_check():
    pack = construct()
    check = pack["checks"][0]
    assert pack["hypotheses"][0]["failure_id"] == "nonadjacent_event_replay"
    assert pack["policy_version"] == "event-replay-v1"
    assert pack["task_sha256"] == hashlib.sha256(TASK.encode()).hexdigest()
    assert check["inputs"]["event"] == {"id": "delivery-7", "delta": 3}
    assert check["inputs"]["intervening"]["id"] != "delivery-7"
    assert check["inputs"]["intervening"]["delta"] == 3
    assert check["example"]["source_sha256"] == hashlib.sha256(EXAMPLE.encode()).hexdigest()
    assert check["example"]["path"] == "tests/test_stock.py"
    assert check["oracle"]["relation"] == "fold([a,b,a]) == fold([a,b])"
    assert check["oracle"]["independent"] is False
    assert check["source_sha256"] == hashlib.sha256(check["source"].encode()).hexdigest()
    assert check["nodeid"].endswith("::test_nonadjacent_event_replay")
    assert check["selection"]["capability"] == ["workforce", "run_tests"]
    assert "distinct" in check["input_derivation"]
    compile(check["source"], check["nodeid"], "exec")
    assert construct() == pack
    assert json.loads(json.dumps(pack)) == pack
    assert pack["hypotheses"][0]["triggering_task_facts"]["requirement_source"] == "task_text"


def test_a_confirmed_relation_does_not_pass_its_constant_off_as_a_task_fact():
    """Audit 009 RP-009: 'Fix a label.' says nothing about replay; the requirement came from the relation."""
    pack = premortem.construct_replay_check(
        "Fix a label.", module="stock.py", function="receive", test_sources={"tests/test_stock.py": EXAMPLE},
        executed_tests=("tests/test_stock.py::test_update",), relation="event-replay-v1")
    assert pack["hypotheses"][0]["triggering_task_facts"]["requirement_source"] == "confirmed_relation"


@pytest.mark.parametrize("task", [
    "Fix a label.", "Support duplicate events.",
    "Make this handler apply each stable event ID at most once.",  # ordering and payload ambiguity
    "Do not apply each stable event ID at most once; another event arrives between identical retries.",
])
def test_unrelated_or_ambiguous_requirement_does_not_construct(task):
    assert construct(task=task) is None


@pytest.mark.parametrize("module,function,sources", [
    ("../stock.py", "receive", None), ("stock.py", "x;print(1)", None),
    ("pkg/stock.py", "receive", None),
    ("stock.py", "receive", {"tests/test_x.py": "this is not Python!"}),
    ("stock.py", "receive", {"tests/test_x.py": "def test_x(): assert True"}),
    ("stock.py", "receive", {"tests/test_x.py": EXAMPLE.replace('{"id": "delivery-7", "delta": 3}', "get_event()")}),
    ("stock.py", "receive", {"tests/test_x.py": EXAMPLE.replace('from stock import receive', 'from other import receive')}),
    ("stock.py", "receive", {"tests/test_x.py": EXAMPLE.replace('"id": "delivery-7"', '"id": 7')}),
])
def test_unsupported_or_nonliteral_material_does_not_invent_examples(module, function, sources):
    assert construct(module=module, function=function, sources=sources) is None


def test_actual_example_and_module_change_the_constructed_artifact():
    first = construct()
    second_source = EXAMPLE.replace("stock", "warehouse").replace("receive", "apply").replace("delivery-7", "new-key")
    second = construct(module="warehouse.py", function="apply", sources={"tests/test_second.py": second_source})
    assert second["checks"][0]["source_sha256"] != first["checks"][0]["source_sha256"]
    assert second["checks"][0]["inputs"]["event"]["id"] == "new-key"
    assert "from warehouse import apply" in second["checks"][0]["source"]


@pytest.mark.parametrize("function", ["first", "second", "event", "deepcopy", "test_nonadjacent_event_replay"])
def test_generated_names_cannot_turn_a_correct_handler_into_a_failure(function, monkeypatch):
    module = ModuleType("stock")

    def correct(state, event):
        return ({"count": state["count"] + event["delta"], "seen": state["seen"] + [event["id"]]}
                if event["id"] not in state["seen"] else state)

    setattr(module, function, correct)
    monkeypatch.setitem(sys.modules, "stock", module)
    check = construct(function=function, sources={"tests/test_stock.py": EXAMPLE.replace("receive", function)})["checks"][0]
    namespace = {}
    exec(check["source"], namespace)  # noqa: S102 - authored constructor control with an in-memory fixture module
    namespace["test_nonadjacent_event_replay"]()


@pytest.mark.parametrize("source", [
    EXAMPLE.replace("def test_update():", "def dead_helper():") + "\ndef test_update():\n    assert True\n",
    EXAMPLE.replace("    assert receive", "    receive = lambda *args: {}\n    assert receive"),
    EXAMPLE.replace("def test_update():", "def test_update(receive):"),
    EXAMPLE.replace("def test_update():", "def test_update():\n    if False:").replace("    assert", "        assert"),
    EXAMPLE + "\nreceive = lambda *args: {}\n",
    EXAMPLE.replace("from stock import receive", "from stock import receive\nfrom other import receive"),
    EXAMPLE.replace("    assert receive", "    return\n    assert receive"),
    EXAMPLE + "\ndef test_update():\n    assert True\n",
])
def test_dead_nested_or_shadowed_assertions_are_not_positive_target_examples(source):
    assert construct(sources={"tests/test_stock.py": source}) is None


def test_unexecuted_test_is_not_an_example():
    assert construct(executed=()) is None


def adjacent(task="Make this Python event handler ignore an identical adjacent duplicate event. Preserve existing tests.",
             sources=None, executed=None, **kwargs):
    return premortem.construct_replay_check(task, module="stock.py", function="receive",
        test_sources={"tests/test_stock.py": EXAMPLE} if sources is None else sources,
        executed_tests=("tests/test_stock.py::test_update",) if executed is None else executed, **kwargs)


def test_adjacent_constructs_distinct_bound_relation_and_runs_positive_and_negative_controls(monkeypatch):
    pack = adjacent(relation="adjacent-replay.v1")
    assert pack is not None
    check = pack["checks"][0]
    assert pack["policy_version"] == "adjacent-replay.v1"
    assert check["oracle"]["relation"] == "fold([a,a]) == fold([a])"
    assert "intervening" not in check["inputs"]
    assert check["source_sha256"] == hashlib.sha256(check["source"].encode()).hexdigest()
    assert check["source_sha256"] != construct()["checks"][0]["source_sha256"]
    module = ModuleType("stock")
    monkeypatch.setitem(sys.modules, "stock", module)
    for dedupe in (True, False):
        def receive(state, event, dedupe=dedupe):
            if dedupe and state["seen"] and state["seen"][-1] == event["id"]:
                return state
            return {"count": state["count"] + event["delta"], "seen": state["seen"] + [event["id"]]}
        module.receive = receive
        namespace = {}
        exec(check["source"], namespace)  # noqa: S102 - authored in-memory constructor control
        test = namespace[check["nodeid"].split("::")[1]]
        if dedupe:
            test()
        else:
            with pytest.raises(AssertionError):
                test()
    assert adjacent(relation="adjacent-replay.v1") == pack
    assert json.loads(json.dumps(pack)) == pack


def test_adjacent_can_be_selected_by_confirmed_relation_or_requirement():
    pack = adjacent("Ambiguous retry", relation="adjacent-replay.v1")
    assert pack and pack["checks"][0]["oracle"]["relation"] == "fold([a,a]) == fold([a])"
    assert adjacent("Fix a label.") is None
    assert adjacent("Ambiguous retry", clarified_requirement="Make this Python event handler ignore an identical adjacent duplicate event. Preserve existing tests.")
    assert adjacent(relation="invented") is None


def test_raw_adjacent_task_does_not_select_a_relation():
    assert adjacent() is None


@pytest.mark.parametrize("source,executed", [
    (EXAMPLE, ()),
    (EXAMPLE.replace('"id": "delivery-7"', '"id": 7'), None),
    (EXAMPLE.replace("    assert receive", "    return\n    assert receive"), None),
    (EXAMPLE.replace("from stock import receive", "from other import receive"), None),
    (EXAMPLE.replace('"delta": 3}', '"delta": get_delta()}'), None),
])
def test_adjacent_rejects_unsupported_or_unexecuted_literal_examples(source, executed):
    assert "relation" in __import__("inspect").signature(premortem.construct_replay_check).parameters
    assert adjacent(sources={"tests/test_stock.py": source}, executed=executed, relation="adjacent-replay.v1") is None
