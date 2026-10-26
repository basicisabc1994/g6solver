# SPDX-License-Identifier: Apache-2.0
"""g6.cogarch.cog_arch_actr: ACT-R declarative memory for agents. Activation from every presentation (kept in the
memory's file, so learning accumulates across calls), spreading from a context, seeded noise, latency, a refusal below
the threshold; productions with variables and retrieval requests, judged by the caller's goal test; utility learning.

Known values are worked by hand from the equations of the ACT-R 7.30+ Reference Manual (Bothell), whose clauses are in
the package's source_register.json: base level B = ln(sum t_j^-d), retrieval time F e^(-f A), failure time F e^(-f tau),
spreading W/n (S - ln fan) with fan = (1 + slots_j) / slotsof_ji, and utility U + alpha (R - U), R less the time since
the selection."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.cogarch.cog_arch_actr as package
from g6.cogarch.cog_arch_actr import CogArchActrBlock, CogArchActrInput, equations, memory
from g6.core.failure_modes import FailureModeRegistry
from g6.core.storage import state_path
from g6.mcp import describe_reply, invoke_reply


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Clock:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    clock = Clock()
    monkeypatch.setattr(memory, "_clock", clock)
    return clock


def act(**params) -> dict:
    return CogArchActrBlock().infer(CogArchActrInput(**params)).unwrap()


def mcp(op: str, **params) -> dict:
    reply = invoke_reply("cog_arch_actr", op, json.dumps(params))
    assert reply["ok"], reply
    return reply["result"]


# --- D1: recall that fades with time and is refused once it has faded ------------------------------------------------

def test_d1_recency_beats_old_practice_and_a_faded_memory_is_refused(home: Clock) -> None:
    # a case the equations were not written around: five old presentations against one recent one
    for t in range(5):
        home.t = 1000.0 + t
        act(op="add_chunk", name="drawer", isa="location", slots={"object": "keys", "place": "drawer"})
    home.t = 2000.0
    act(op="add_chunk", name="hook", isa="location", slots={"object": "keys", "place": "hook"})
    home.t = 2010.0
    recent = act(op="retrieve", request={"isa": "location", "object": "keys"}, threshold=-5)
    assert recent["chunk"]["place"] == "hook" and recent["completion_state"] == "qualified-draft"
    home.t = 1_000_000.0
    faded = act(op="retrieve", request={"isa": "location", "object": "keys"}, threshold=-3)
    assert faded["completion_state"] == "blocked-escalated" and faded["code"] == "G6_E_ACTR_BELOW_THRESHOLD"
    assert faded["breakdown"]["activation"] < -3 and faded["latency_s"] == pytest.approx(20.085537, abs=1e-6)  # e^3


# --- P1 ------------------------------------------------------------------------------------------------------------------

def test_p1_actr02_retrievals_accumulate_across_mcp_calls(home: Clock) -> None:
    # retrieve one twin five times, add another chunk, then ask for either twin: the rehearsed one must lead
    mcp("add_chunk", name="rehearsed", isa="fact", slots={"kind": "twin", "id": "r"})
    mcp("add_chunk", name="idle", isa="fact", slots={"kind": "twin", "id": "i"})
    for t in range(1, 6):
        home.t = 1000.0 + t
        assert mcp("retrieve", request={"isa": "fact", "id": "r"})["chunk"]["name"] == "rehearsed"
    home.t = 1006.0
    mcp("add_chunk", name="other", isa="fact", slots={"kind": "other", "id": "o"})
    home.t = 1010.0
    out = mcp("retrieve", request={"isa": "fact", "kind": "twin"}, threshold=-5)
    assert out["chunk"]["name"] == "rehearsed" and out["breakdown"]["presentations"] == 6
    idle = next(c for c in out["competitors"] if c["name"] == "idle")
    assert out["breakdown"]["base_level"] > idle["base_level"] and idle["presentations"] == 1


def test_p1_actr02_a_reward_reaches_the_next_runs_choice(home: Clock) -> None:
    for name in ("a-first", "b-second"):
        act(op="add_production", name=name, conditions={"=goal": {"isa": "pick", "choice": None}},
            actions={"=goal": {"choice": name}})
    goal, test = {"isa": "pick"}, {"choice": "=chosen"}     # passes once choice holds any value
    first = act(op="run", goal=goal, goal_test=test)
    assert first["trace"][0]["production"] == "a-first" and first["code"] == "G6_E_ACTR_GOAL_REACHED"
    paid = act(op="reward", reward=-10)
    assert paid["updates"][0]["utility_after"] == pytest.approx(0.2 * (-10 - 0.05))
    second = mcp("run", goal=goal, goal_test=test)
    assert second["trace"][0]["production"] == "b-second"


def test_p1_actr03_productions_bind_variables_and_ask_memory_for_the_next_number(home: Clock) -> None:
    # the counting model: count-order facts, and productions with =variables, a -negated slot and +retrieval requests
    for first in range(1, 6):
        act(op="add_chunk", name=f"order-{first}", isa="count-order", slots={"first": first, "second": first + 1})
    act(op="add_production", name="start", conditions={"=goal": {"isa": "count-from", "start": "=n", "count": None}},
        actions={"=goal": {"count": "=n"}, "+retrieval": {"isa": "count-order", "first": "=n"}})
    act(op="add_production", name="increment",
        conditions={"=goal": {"isa": "count-from", "count": "=n", "-end": "=n"},
                    "=retrieval": {"isa": "count-order", "first": "=n", "second": "=m"}},
        actions={"=goal": {"count": "=m"}, "+retrieval": {"isa": "count-order", "first": "=m"}})
    act(op="add_production", name="stop",
        conditions={"=goal": {"isa": "count-from", "count": "=n", "end": "=n", "-state": "done"}},
        actions={"=goal": {"state": "done"}})
    home.t = 1000.5
    out = act(op="run", goal={"isa": "count-from", "start": 2, "end": 4}, goal_test={"count": 4, "state": "done"},
              threshold=-5)
    assert out["code"] == "G6_E_ACTR_GOAL_REACHED" and out["completion_state"] == "qualified-draft"
    assert [s["production"] for s in out["trace"]] == ["start", "increment", "increment", "stop"]
    assert [s["retrieval"]["retrieved"] for s in out["trace"][:3]] == ["order-2", "order-3", "order-4"]
    assert out["trace"][1]["bindings"] == {"n": 2, "m": 3} and out["goal"]["count"] == 4


def test_p1_actr03_a_failed_retrieval_is_a_state_a_production_can_test(home: Clock) -> None:
    act(op="add_production", name="ask", conditions={"=goal": {"isa": "q", "state": None}},
        actions={"=goal": {"state": "asked"}, "+retrieval": {"isa": "answer"}})
    act(op="add_production", name="give-up", conditions={"=goal": {"isa": "q", "state": "asked"},
                                                          "?retrieval": "error"},
        actions={"=goal": {"state": "unknown"}})
    out = act(op="run", goal={"isa": "q"}, goal_test={"state": "unknown"})
    assert [s["production"] for s in out["trace"]] == ["ask", "give-up"]
    assert out["trace"][0]["retrieval"]["retrieved"] is None and out["code"] == "G6_E_ACTR_GOAL_REACHED"


def test_p1_actr01_no_op_reaches_the_network_or_a_file_outside_the_state_folder() -> None:
    assert package.__g6_sensitive_ops__ == () and "realtime_bridge" not in mcp("ops")["ops"]
    refused = invoke_reply("cog_arch_actr", "realtime_bridge", json.dumps({"endpoint": "http://127.0.0.1/"}))
    assert not refused["ok"] and refused["completion_state"] == "blocked-escalated"
    escape = act(op="add_chunk", memory="../outside", name="x", isa="t")
    assert escape["code"] == "G6_E_ACTR_INPUT"
    act(op="add_chunk", memory="kept", name="x", isa="t")
    assert state_path("cogarch", "cog_arch_actr", "kept.json").is_file()


# --- the equations' known values, worked by hand --------------------------------------------------------------------------

def test_base_level_and_latency_known_values(home: Clock) -> None:
    # presentations at 1000, 1002 and 1005; asked at 1010, so the ages are 10, 8 and 5 seconds, d = 0.5:
    # 10^-0.5 = 0.316228, 8^-0.5 = 0.353553, 5^-0.5 = 0.447214; sum 1.116995; B = ln 1.116995 = 0.110642
    # RT = F e^(-f A) = 1 x e^-0.110642 = 0.895259 s
    for t in (1000.0, 1002.0, 1005.0):
        home.t = t
        act(op="add_chunk", name="fact", isa="f", slots={"v": 1})
    home.t = 1010.0
    out = act(op="retrieve", request={"isa": "f"})
    assert out["breakdown"]["base_level"] == pytest.approx(0.110642, abs=1e-6)
    assert out["latency_s"] == pytest.approx(0.895259, abs=1e-6)
    assert out["breakdown"]["noise"] == 0.0 and out["breakdown"]["presentations"] == 3
    assert equations.base_level([10, 8, 5], 0.5) == pytest.approx(0.110642, abs=1e-6)


def test_failure_time_known_values() -> None:
    # F e^(-f tau): tau = 0 gives 1 s; tau = -1 gives e = 2.718282 s; F = 0.5, f = 2, tau = 1 gives 0.5 e^-2 = 0.067668
    assert equations.failure_time(0.0, 1.0, 1.0) == 1.0
    assert equations.failure_time(-1.0, 1.0, 1.0) == pytest.approx(2.718282, abs=1e-6)
    assert equations.failure_time(1.0, 0.5, 2.0) == pytest.approx(0.067668, abs=1e-6)
    out = act(op="retrieve", request={"isa": "nothing"}, threshold=-1)
    assert out["code"] == "G6_E_ACTR_NO_MATCH" and out["latency_s"] == pytest.approx(2.718282, abs=1e-6)


def test_the_fan_effect_known_values(home: Clock) -> None:
    # three facts about Fred, one about Sam (the textbook's Fred example). Source fred: slots_j = 3, slotsof_ji = 1,
    # fan = (1 + 3) / 1 = 4, S_ji = S - ln 4 = 2 - 1.386294 = 0.613706; one source, so W = 1 / 1 = 1.
    # Source sam: fan = (1 + 1) / 1 = 2, S_ji = 2 - ln 2 = 1.306853. More facts, less spreading: slower recall.
    for attr in ("tall", "beard", "hospital"):
        act(op="add_chunk", name=f"fred-{attr}", isa="fact", slots={"person": "fred", "attr": attr})
    act(op="add_chunk", name="sam-tall", isa="fact", slots={"person": "sam", "attr": "tall"})
    fred = act(op="retrieve", request={"isa": "fact", "attr": "tall", "person": "fred"}, context={"person": "fred"},
               max_strength=2)
    sam = act(op="retrieve", request={"isa": "fact", "attr": "tall", "person": "sam"}, context={"person": "sam"},
              max_strength=2)
    assert fred["breakdown"]["spreading"] == pytest.approx(0.613706, abs=1e-6)
    assert sam["breakdown"]["spreading"] == pytest.approx(1.306853, abs=1e-6)
    assert fred["latency_s"] > sam["latency_s"]
    off = act(op="retrieve", request={"isa": "fact", "person": "fred", "attr": "beard"}, context={"person": "fred"})
    assert off["breakdown"]["spreading"] == 0.0       # spreading is off unless max_strength is set, as in ACT-R


def test_a_negative_strength_counts_as_zero_and_is_marked() -> None:
    for i in range(12):
        act(op="add_chunk", name=f"n{i}", isa="fact", slots={"tag": "common", "i": i})
    out = act(op="retrieve", request={"isa": "fact", "i": 0}, context={"tag": "common"}, max_strength=1)
    terms = out["breakdown"]["spreading_terms"]
    assert out["breakdown"]["spreading"] == 0.0 and terms[0]["negative_counted_as_zero"] is True
    assert terms[0]["strength"] == pytest.approx(1 - 2.564949, abs=1e-6)   # fan (1 + 12) / 1 = 13, ln 13 = 2.564949


def test_utility_learning_known_values() -> None:
    # U = 0, alpha 0.2, R = 10: 0 + 0.2 (10 - 0) = 2.0; again: 2 + 0.2 (10 - 2) = 3.6
    assert equations.utility_update(0.0, 10.0, 0.2) == pytest.approx(2.0)
    assert equations.utility_update(2.0, 10.0, 0.2) == pytest.approx(3.6)


def test_the_effective_reward_is_less_the_time_since_selection() -> None:
    # one production selected at t, fired at t + 0.05, the run ends there: R = 10 - 0.05 = 9.95, U = 0.2 x 9.95 = 1.99
    act(op="add_production", name="go", conditions={"=goal": {"isa": "g", "done": None}},
        actions={"=goal": {"done": "yes"}})
    act(op="run", goal={"isa": "g"}, goal_test={"done": "yes"})
    out = act(op="reward", reward=10)
    assert out["updates"][0]["effective_reward"] == pytest.approx(9.95)
    assert out["updates"][0]["utility_after"] == pytest.approx(1.99)
    assert act(op="reward", reward=10)["code"] == "G6_E_ACTR_NOTHING_TO_REWARD"


def test_the_source_register_holds_every_rule_with_its_hash() -> None:
    data = json.loads((Path(package.__file__).parent / "source_register.json").read_text(encoding="utf-8"))
    assert set(data["sources"]) == {"actr-manual", "braisby-gellatly-2005"}
    for source in data["sources"].values():
        assert len(source["file_sha256"]) == 64 and source["licence"]
    assert data["sources"]["actr-manual"]["url"].startswith("http://act-r.psy.cmu.edu/")
    ids = {r["id"] for r in data["rules"]}
    assert {"base_level_terms", "decay", "retrieval_time", "failure_time", "fan_terms", "utility_reward"} <= ids
    for rule in data["rules"]:
        assert rule["source"] in data["sources"] and rule["where"] and rule["used_as"]
        assert hashlib.sha256(rule["clause"].encode("utf-8")).hexdigest() == rule["clause_sha256"], rule["id"]


# --- seeded noise ----------------------------------------------------------------------------------------------------------

def test_noise_is_seeded_per_memory_and_one_draw_per_chunk() -> None:
    def noisy(name: str, seed: int) -> float:
        act(op="add_chunk", memory=name, name="a", isa="t")
        return act(op="retrieve", memory=name, request={"isa": "t"}, noise=0.5, seed=seed,
                   threshold=-50)["breakdown"]["noise"]

    assert noisy("m1", 7) == noisy("m2", 7) != 0.0
    assert noisy("m3", 8) != noisy("m4", 7)
    again = act(op="retrieve", memory="m1", request={"isa": "t"}, noise=0.5, seed=7, threshold=-50)
    assert again["breakdown"]["noise"] != noisy("m5", 7)            # the second draw of m1, not its first


# --- caps and refusals --------------------------------------------------------------------------------------------------

def test_g4_a_looping_run_stops_at_max_cycles_and_at_its_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    act(op="add_production", name="spin", conditions={"=goal": {"isa": "loop"}}, actions={"=goal": {"turns": 1}})
    out = act(op="run", goal={"isa": "loop"}, goal_test={"turns": 2}, max_cycles=3)
    assert out["code"] == "G6_E_ACTR_CAP" and out["cap"] == "max_cycles" and out["cycles"] == 3
    ticks = iter(range(0, 10_000, 5))
    monkeypatch.setattr(memory, "_wall", lambda: next(ticks))
    late = act(op="run", goal={"isa": "loop"}, goal_test={"turns": 2}, time_limit_s=2)
    assert late["code"] == "G6_E_ACTR_CAP" and late["cap"] == "time_limit_s"


def test_g4_a_run_with_no_goal_test_or_no_matching_production_is_refused() -> None:
    act(op="add_production", name="only", conditions={"=goal": {"isa": "x"}}, actions={"=goal": {"s": 1}})
    assert act(op="run", goal={"isa": "y"}, goal_test={})["code"] == "G6_E_ACTR_INPUT"
    stuck = act(op="run", goal={"isa": "y"}, goal_test={"s": 1})
    assert stuck["code"] == "G6_E_ACTR_STUCK" and stuck["completion_state"] == "blocked-escalated"


def test_g4_an_invalid_production_is_refused_with_its_reasons() -> None:
    out = act(op="add_production", name="bad", conditions={"=goal": {"isa": "x", "-end": "=n"}},
              actions={"=goal": {"count": "=m"}, "+imaginal": {"isa": "y"}})
    assert out["code"] == "G6_E_ACTR_PRODUCTION_INVALID"
    said = " ".join(out["problems"])
    assert "+imaginal" in said and "variable =n is not bound" in said and "=m" in said


def test_g4_an_unreadable_memory_is_refused_not_emptied() -> None:
    act(op="add_chunk", memory="broken", name="a", isa="t")
    state_path("cogarch", "cog_arch_actr", "broken.json").write_text("{not json", encoding="utf-8")
    out = act(op="retrieve", memory="broken", request={"isa": "t"})
    assert out["code"] == "G6_E_ACTR_STORE" and out["completion_state"] == "blocked-escalated"


def test_a_different_chunk_under_a_kept_name_is_refused_and_an_equivalent_one_merges() -> None:
    act(op="add_chunk", name="a", isa="t", slots={"v": 1})
    assert act(op="add_chunk", name="a", isa="t", slots={"v": 2})["code"] == "G6_E_ACTR_CHUNK_EXISTS"
    merged = act(op="add_chunk", name="b", isa="t", slots={"v": 1})
    assert merged["how"] == "merged" and merged["chunk"]["name"] == "a" and merged["presentations"] == 2


# --- the gates ------------------------------------------------------------------------------------------------------------

def test_g1_no_op_answers_verified() -> None:
    replies = [act(op="add_chunk", name="a", isa="t"), act(op="retrieve", request={"isa": "t"}),
               act(op="retrieve", request={"isa": "t"}, threshold=50)]
    assert all(r["completion_state"] in ("qualified-draft", "blocked-escalated") for r in replies)


def test_g3_a_callers_word_is_refused_as_a_field() -> None:
    for name in ("approved", "verified", "confidence", "fitted"):
        with pytest.raises(ValidationError):
            CogArchActrInput(op="retrieve", request={"isa": "t"}, **{name: True})


def test_slot_names_and_values_are_strict() -> None:
    with pytest.raises(ValidationError):
        CogArchActrInput(op="add_chunk", name="a", isa="t", slots={"isa": "other"})
    with pytest.raises(ValidationError):
        CogArchActrInput(op="add_chunk", name="a", isa="t", slots={"flag": True})
    with pytest.raises(ValidationError):
        CogArchActrInput(op="retrieve", request={"isa": "t"}, decay=float("nan"))


def test_g5_no_op_is_sensitive() -> None:
    assert package.__g6_sensitive_ops__ == ()


# --- D2, D6 ---------------------------------------------------------------------------------------------------------------

def test_d2_the_failure_modes_are_registered_and_no_longer_planned() -> None:
    assert package.__g6_mitigates__ == ("unmodelled_recall",) and package.__g6_introduces__ == ("under_retrieval",)
    assert all(FailureModeRegistry.is_registered(c) for c in (*package.__g6_mitigates__, *package.__g6_introduces__))
    assert "g6.cogarch.cog_arch_actr" not in FailureModeRegistry.planned()


def test_d6_over_mcp() -> None:
    assert describe_reply("cog_arch_actr")["skill"]
    mcp("add_chunk", name="refund", isa="policy", slots={"topic": "refund", "days": 14})
    out = mcp("retrieve", request={"isa": "policy", "topic": "refund"})
    assert out["chunk"]["days"] == 14 and out["code"] == "G6_E_ACTR_RETRIEVED"


@pytest.mark.parametrize("surface", ["registry", "mcp"])
def test_latency_overflow_is_a_stable_refusal(surface: str) -> None:
    # Fix: cog_arch_actr/equations.py:83 (overflow conversion); removing it leaks exp(10000) through the door.
    from g6.core.registry import get_registry

    params = {"request": {"isa": "absent"}, "threshold": -100, "latency_exponent": 100}
    out = (get_registry().invoke("cog_arch_actr", "retrieve", params).unwrap() if surface == "registry"
           else mcp("retrieve", **params))
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_ACTR_NUMERIC"
    assert "chunk" not in out and "latency_s" not in out


def test_fan_counts_scan_memory_once_per_retrieval() -> None:
    # Fix: cog_arch_actr/memory.py:226 (fan_counts argument); removing it loses the shared fan counts.
    import random
    from collections import Counter

    scans = Counter()

    class Slots(dict):
        def values(self):
            scans[self["id"]] += 1
            return super().values()

    state = memory.State()
    for i in range(12):
        state.chunks[f"c{i}"] = memory.Chunk.model_construct(
            isa="fact", slots=Slots(id=i, person="fred", place="office"), presentations=[999.0], dropped=0)
    got = memory.retrieve(state, {"isa": "fact"}, {"person": "fred", "place": "office"}, 1,
                          memory.Params(max_strength=5, threshold=-10), 1000, random.Random(0))
    assert got.ok and got.matched == 12
    assert set(scans.values()) == {2}  # one global fan pass, one local count per matching chunk
    assert [term["fan"] for term in got.breakdown["spreading_terms"]] == [13, 13]


@pytest.mark.parametrize("op", ["retrieve", "run"])
def test_retrieval_deadline_checked_mid_scan(monkeypatch: pytest.MonkeyPatch, op: str) -> None:
    # Fix: cog_arch_actr/memory.py:220 (_check_deadline); removing it scans all candidates after expiry.
    for i in range(6):
        act(op="add_chunk", name=f"c{i}", isa="fact", slots={"i": i})
    act(op="add_production", name="ask", conditions={"=goal": {"isa": "q"}},
        actions={"=goal": {"done": 1}, "+retrieval": {"isa": "fact"}})
    clock, calls = [0.0], []
    real_matches = memory.matches

    def match(request, chunk):
        if chunk.get("isa") == "fact":
            calls.append(chunk["name"])
            clock[0] = 2.0
        return real_matches(request, chunk)

    monkeypatch.setattr(memory, "_wall", lambda: clock[0])
    monkeypatch.setattr(memory, "matches", match)
    params = ({"request": {"isa": "fact"}} if op == "retrieve" else
              {"goal": {"isa": "q"}, "goal_test": {"done": 1}})
    out = act(op=op, time_limit_s=1, **params)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_ACTR_CAP"
    assert out["cap"] == "time_limit_s" and len(calls) == 1
    assert "chunk" not in out
