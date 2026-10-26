# SPDX-License-Identifier: Apache-2.0
"""g6.cogarch.cog_arch_gps: means-ends analysis over declared STRIPS operators, goals kept in the order given, a final
goal check on every conjunction, and every plan replayed against the operators before it is offered; a plan from any
source (a model's included) replayed on its own; a model's encoding a draft that must pass the replay."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import g6.cogarch.cog_arch_gps as package
from g6.cogarch.cog_arch_gps import CogArchGpsBlock, CogArchGpsInput, engine
from g6.core.failure_modes import FailureModeRegistry
from g6.core.llm import FakeLLM, LLMError
from g6.core.storage import state_path
from g6.mcp import describe_reply, invoke_reply


def op(name: str, pre: tuple[str, ...] = (), add: tuple[str, ...] = (), delete: tuple[str, ...] = ()) -> dict:
    return {"name": name, "preconditions": list(pre), "add_list": list(add), "delete_list": list(delete)}


def run(caller=None, **params) -> dict:
    return CogArchGpsBlock(caller=caller).infer(CogArchGpsInput(**params)).unwrap()


TEA = [op("boil", add=("kettle_boiled",)), op("pour", pre=("kettle_boiled",), add=("tea_made",),
                                               delete=("kettle_boiled",))]


# --- D1: a plan that does not reach the goal is never offered as one -------------------------------------------------

def test_d1_a_plan_that_undoes_an_earlier_goal_is_refused_with_the_step_that_undid_it() -> None:
    # a model's plan, a case the replay was not written around: pouring empties the kettle the goal still needs
    out = run(op="validate_plan", initial=[], goals=["kettle_boiled", "tea_made"], operators=TEA,
              plan=["boil", "pour"])
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_GPS_PLAN_FAILS"
    assert out["goals_missing"] == ["kettle_boiled"] and out["clobbered_by"] == {"kettle_boiled": 2}
    assert out["failed_at"] == 2 and out["final_state"] == ["tea_made"]


def test_d1_solve_replays_the_searchs_plan_before_offering_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # the search is replaced by one that skips a precondition: the replay, not the search, must catch it
    def careless(*args, **kwargs):
        return engine.Found(plan=("pour",), state=frozenset({"tea_made"}), nodes=1, orders_tried=1)

    monkeypatch.setattr(engine, "search", careless)
    out = run(op="solve", initial=[], goals=["tea_made"], operators=TEA)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_GPS_REPLAY_FAILED"
    assert out["failed_at"] == 1 and out["missing"] == ["kettle_boiled"] and out["plan"] == ["pour"]


# --- P0 and P1 ---------------------------------------------------------------------------------------------------------

def test_p1_gps02_the_clobbered_sibling_goal_is_not_reported_solved() -> None:
    # the card's example: op2 reaches b but deletes a; [op1, op2] ends in {b} and was answered "Solved"
    ops = [op("op1", add=("a",)), op("op2", add=("b",), delete=("a",))]
    out = run(op="solve", initial=[], goals=["a", "b"], operators=ops)
    assert out["completion_state"] == "qualified-draft" and out["code"] == "G6_E_GPS_PLANNED"
    assert out["plan"] == ["op2", "op1"] and {"a", "b"} <= set(out["final_state"])
    replay = next(e for e in out["evidence"] if e["kind"] == "replay")
    assert replay["goals_hold"] is True and replay["steps"] == 2


def test_p1_gps02_when_no_order_keeps_both_goals_it_is_refused_not_solved() -> None:
    ops = [op("op1", add=("a",), delete=("b",)), op("op2", add=("b",), delete=("a",))]
    out = run(op="solve", initial=[], goals=["a", "b"], operators=ops)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_GPS_NO_PLAN"
    assert "not proof that none exists" in out["warning_card"]["message"]


def test_p1_gps02_a_precondition_undone_by_its_sibling_is_achieved_again_before_the_step() -> None:
    ops = [op("make_p", add=("p",)), op("make_q", add=("q",), delete=("p",)), op("finish", pre=("p", "q"), add=("g",))]
    out = run(op="solve", initial=[], goals=["g"], operators=ops)
    assert out["plan"] == ["make_q", "make_p", "finish"] and out["completion_state"] == "qualified-draft"
    assert engine.replay(frozenset(), ("g",), engine.operators(ops), out["plan"]).ok


def test_p0_gps01_a_models_encoding_is_a_draft_never_verified_and_its_plan_is_replayed() -> None:
    reply = {"initial": [], "goals": ["tea_made"], "operators": TEA, "completion_state": "verified",
             "confidence": 0.99}
    model = FakeLLM(default=json.dumps(reply))
    out = run(model, op="encode", description="Make a cup of tea: boil the kettle, then pour.")
    assert out["completion_state"] == "qualified-draft" and out["code"] == "G6_E_GPS_ENCODED"
    assert out["plan"] == ["boil", "pour"] and out["plan_source"] == "means-ends analysis"
    assert next(e for e in out["evidence"] if e["kind"] == "replay")["goals_hold"] is True
    assert "verified" not in json.dumps(out["evidence"]) and set(out["problem"]) == {"initial", "goals", "operators"}
    assert "<<<DESCRIPTION" in model.calls[0][1]


@pytest.mark.parametrize("said", ["I would boil the kettle first.", json.dumps({"initial": [], "goals": ["x"]}),
                                  json.dumps({"initial": [], "goals": ["x"], "operators": [{"name": "a",
                                                                                              "adds": ["x"]}]})])
def test_p0_gps01_an_encoding_that_is_not_a_problem_is_refused_not_offered(said: str) -> None:
    out = run(FakeLLM(default=said), op="encode", description="Reach x.")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_GPS_ENCODE_MALFORMED"


def test_p0_gps01_a_models_own_plan_that_fails_its_replay_is_not_used() -> None:
    reply = {"initial": [], "goals": ["tea_made"], "operators": TEA, "plan": ["pour"]}
    out = run(FakeLLM(default=json.dumps(reply)), op="encode", description="Make tea.")
    assert out["plan"] == ["boil", "pour"] and out["plan_source"] == "means-ends analysis"
    model_plan = next(e for e in out["evidence"] if e["kind"] == "model_plan")
    assert model_plan["replayed"] is True and model_plan["failed_at"] == 1 and model_plan["used"] is False


def test_p0_gps01_a_models_own_plan_that_passes_its_replay_is_offered_as_the_models() -> None:
    reply = {"initial": [], "goals": ["tea_made"], "operators": TEA, "plan": ["boil", "pour"]}
    out = run(FakeLLM(default=json.dumps(reply)), op="encode", description="Make tea.")
    assert out["plan_source"] == "the model, replayed" and out["completion_state"] == "qualified-draft"


def test_p0_gps01_an_encoding_with_no_plan_is_refused_with_the_draft_for_review() -> None:
    reply = {"initial": [], "goals": ["tea_made"], "operators": [op("boil", add=("kettle_boiled",))]}
    out = run(FakeLLM(default=json.dumps(reply)), op="encode", description="Make tea.")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_GPS_ENCODED_NO_PLAN"
    assert out["problem"]["goals"] == ["tea_made"]


def test_encode_needs_a_caller_and_a_failed_call_is_refused() -> None:
    assert run(op="encode", description="Make tea.")["code"] == "G6_E_GPS_NO_MODEL"

    class Broken:
        def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
            raise LLMError("the model is down")

    out = run(Broken(), op="encode", description="Make tea.")
    assert out["code"] == "G6_E_GPS_MODEL_FAILED" and out["completion_state"] == "blocked-escalated"


def test_p2_gps03_goals_keep_the_order_given_and_the_same_problem_gives_the_same_plan() -> None:
    ops = [op("a1", add=("a",)), op("b1", add=("b",))]
    first = run(op="solve", initial=[], goals=["b", "a"], operators=ops)
    again = run(op="solve", initial=[], goals=["b", "a"], operators=ops)
    assert first["plan"] == again["plan"] == ["b1", "a1"] and first["goals"] == ["b", "a"]
    assert next(e for e in first["evidence"] if e["kind"] == "search")["orders_tried"] == 1


# --- validate_plan ------------------------------------------------------------------------------------------------------

def test_validate_plan_names_an_unknown_operator_and_a_missing_precondition() -> None:
    unknown = run(op="validate_plan", initial=[], goals=["tea_made"], operators=TEA, plan=["boil", "stir"])
    assert unknown["code"] == "G6_E_GPS_PLAN_FAILS" and unknown["failed_at"] == 2
    assert unknown["unknown_operator"] == "stir"
    missing = run(op="validate_plan", initial=[], goals=["tea_made"], operators=TEA, plan=["pour"])
    assert missing["failed_at"] == 1 and missing["missing"] == ["kettle_boiled"]


def test_validate_plan_passes_a_plan_that_holds_and_traces_each_step() -> None:
    out = run(op="validate_plan", initial=[], goals=["tea_made"], operators=TEA, plan=["boil", "pour"])
    assert out["completion_state"] == "qualified-draft" and out["code"] == "G6_E_GPS_PLAN_REPLAYED"
    assert [s["operator"] for s in out["steps"]] == ["boil", "pour"]
    assert out["steps"][1]["deleted"] == ["kettle_boiled"] and out["final_state"] == ["tea_made"]


# --- caps ---------------------------------------------------------------------------------------------------------------

def _chain(n: int) -> list[dict]:
    return [op(f"s{i}", pre=((f"f{i - 1}",) if i else ()), add=(f"f{i}",)) for i in range(n)]


def test_g4_a_plan_deeper_than_max_depth_is_refused_naming_the_cap() -> None:
    out = run(op="solve", initial=[], goals=["f9"], operators=_chain(10), max_depth=3)
    assert out["code"] == "G6_E_GPS_CAP" and out["cap"] == "max_depth" and "max_depth 3" in out["warning_card"]["message"]
    assert run(op="solve", initial=[], goals=["f9"], operators=_chain(10))["plan"] == [f"s{i}" for i in range(10)]


def test_g4_the_search_stops_at_max_nodes_and_at_its_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    out = run(op="solve", initial=[], goals=["f9"], operators=_chain(10), max_nodes=4)
    assert out["code"] == "G6_E_GPS_CAP" and out["cap"] == "max_nodes" and "max_nodes 4" in out["warning_card"]["message"]
    ticks = iter(range(0, 10_000, 5))
    monkeypatch.setattr(engine, "_clock", lambda: next(ticks))
    late = run(op="solve", initial=[], goals=["f9"], operators=_chain(10), time_limit_s=2)
    assert late["code"] == "G6_E_GPS_CAP" and late["cap"] == "time_limit_s"


def test_g4_too_many_goal_orders_to_try_is_refused_naming_max_orders() -> None:
    # every pair of goals clobbers each other, so no order works; only 2 of the 24 orders may be tried
    ops = [op(f"m{g}", add=(g,), delete=tuple(h for h in "wxyz" if h != g)) for g in "wxyz"]
    out = run(op="solve", initial=[], goals=list("wxyz"), operators=ops, max_orders=2)
    assert out["code"] == "G6_E_GPS_CAP" and out["cap"] == "max_orders"


@pytest.mark.parametrize("field", ["max_depth", "max_nodes", "max_orders", "time_limit_s"])
def test_g4_a_cap_cannot_be_raised_past_its_ceiling(field: str) -> None:
    with pytest.raises(ValidationError):
        CogArchGpsInput(op="solve", **{field: 10**9})


# --- kept operator sets ---------------------------------------------------------------------------------------------------

def test_operators_kept_under_the_state_folder_are_used_by_name() -> None:
    kept = run(op="keep_operators", name="tea-kit", operators=TEA)
    assert kept["code"] == "G6_E_GPS_KEPT" and kept["new"] is True
    assert state_path("cogarch", "cog_arch_gps", "tea-kit.json").is_file()
    out = run(op="solve", initial=[], goals=["tea_made"], operator_set="tea-kit")
    assert out["plan"] == ["boil", "pour"] and out["operator_source"] == "kept set tea-kit"
    again = run(op="keep_operators", name="tea-kit", operators=TEA)
    assert again["code"] == "G6_E_GPS_KEPT" and again["new"] is False
    other = run(op="keep_operators", name="tea-kit", operators=TEA[:1])
    assert other["code"] == "G6_E_GPS_SET_EXISTS" and other["completion_state"] == "blocked-escalated"


def test_an_unknown_set_and_two_sources_of_operators_are_refused() -> None:
    assert run(op="solve", initial=[], goals=["x"], operator_set="nowhere")["code"] == "G6_E_GPS_NO_SET"
    run(op="keep_operators", name="both", operators=TEA)
    both = run(op="solve", initial=[], goals=["tea_made"], operators=TEA, operator_set="both")
    assert both["code"] == "G6_E_GPS_INPUT"


def test_an_edited_kept_set_is_refused_not_used() -> None:
    run(op="keep_operators", name="edited", operators=TEA)
    path = state_path("cogarch", "cog_arch_gps", "edited.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["operators"][1]["preconditions"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    out = run(op="solve", initial=[], goals=["tea_made"], operator_set="edited")
    assert out["code"] == "G6_E_GPS_STORE" and out["completion_state"] == "blocked-escalated"


# --- the gates ------------------------------------------------------------------------------------------------------------

def test_g1_no_op_answers_verified() -> None:
    replies = [run(op="solve", initial=[], goals=["tea_made"], operators=TEA),
               run(op="validate_plan", initial=[], goals=["tea_made"], operators=TEA, plan=["boil", "pour"]),
               run(op="validate_plan", initial=[], goals=["tea_made"], operators=TEA, plan=["pour"])]
    assert all(r["completion_state"] in ("qualified-draft", "blocked-escalated") for r in replies)


def test_g3_a_callers_word_is_refused_as_a_field() -> None:
    for name in ("approved", "verified", "confidence", "reviewer"):
        with pytest.raises(ValidationError):
            CogArchGpsInput(op="solve", goals=["x"], **{name: True})


def test_operator_names_are_unique_and_fields_strict() -> None:
    with pytest.raises(ValidationError):
        CogArchGpsInput(op="solve", goals=["x"], operators=[op("a", add=("x",)), op("a", add=("y",))])
    with pytest.raises(ValidationError):
        CogArchGpsInput(op="solve", goals=["x"], operators=[{"name": "a", "add_list": ["x"], "cost": 2}])


def test_missing_goals_or_operators_are_refused_with_the_input_code() -> None:
    assert run(op="solve", initial=[], goals=[], operators=TEA)["code"] == "G6_E_GPS_INPUT"
    assert run(op="solve", initial=[], goals=["x"])["code"] == "G6_E_GPS_INPUT"
    assert run(op="keep_operators", name="", operators=TEA)["code"] == "G6_E_GPS_INPUT"


def test_g5_no_op_is_sensitive() -> None:
    assert package.__g6_sensitive_ops__ == ()


# --- D2, D6 ---------------------------------------------------------------------------------------------------------------

def test_d2_the_failure_modes_are_registered_and_no_longer_planned() -> None:
    assert package.__g6_mitigates__ == ("no_plan_to_goal", "unchecked_plan")
    assert all(FailureModeRegistry.is_registered(c) for c in (*package.__g6_mitigates__, *package.__g6_introduces__))
    assert "g6.cogarch.cog_arch_gps" not in FailureModeRegistry.planned()


def test_d6_over_mcp() -> None:
    assert describe_reply("cog_arch_gps")["skill"]
    solved = invoke_reply("cog_arch_gps", "solve", json.dumps({"initial": [], "goals": ["tea_made"], "operators": TEA}))
    assert solved["ok"] and solved["result"]["plan"] == ["boil", "pour"]
    assert solved["completion_state"] == "qualified-draft"
    encoded = invoke_reply("cog_arch_gps", "encode", json.dumps({"description": "Make tea."}))
    assert encoded["ok"] and encoded["result"]["code"] == "G6_E_GPS_NO_MODEL"
