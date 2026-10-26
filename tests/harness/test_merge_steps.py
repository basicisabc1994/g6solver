# SPDX-License-Identifier: Apache-2.0
"""Step composition uses the harness's checks and receipts, never archive verdicts."""
from g6.harness import Check, Memory, Standard, run_steps

GOAL = "Summarise the refund policy for customers"


def test_harness_d1_stops_at_the_first_block_and_reports_each_run():
    # Removal makes this fail: `if result["completion_state"] == "blocked-escalated": break` in run_steps.
    seen = []
    standard = Standard(GOAL, (Check("has the correct total", lambda out: out == "42"),))
    memory = Memory(":memory:")
    try:
        reply = run_steps([(str(i), standard, lambda c, i=i: seen.append(i) or str(42 - i)) for i in range(3)],
                          memory=memory)
        assert seen == [0, 1]
        assert reply["completion_state"] == "blocked-escalated"
        assert [s["name"] for s in reply["steps"]] == ["0", "1"]
        assert reply["steps"][1]["result"]["results"]["has-the-correct-total"] == "fail"
    finally:
        memory.close()


def test_harness_01_passes_are_drafts_with_real_receipts():
    # Removal makes this fail: `reports.append({"name": name, "result": result})` in run_steps.
    memory = Memory(":memory:")
    try:
        reply = run_steps([("summary", Standard(GOAL, (Check("has a total", lambda o: o == "42"),)),
                            lambda c: "42")], memory=memory)
        assert reply["completion_state"] == "qualified-draft"
        assert reply["steps"][0]["result"]["evidence"][0]["result"] == "pass"
    finally:
        memory.close()


def test_harness_02_declared_review_does_not_lift_a_step():
    # Removal makes this fail: `result = run(standard, executor, decisions=decisions, memory=memory)` in run_steps.
    check = Check("has a total", lambda o: o == "42", author="expert", kind="independent_executable",
                  known_bad=("41",), known_good="42")
    memory = Memory(":memory:")
    try:
        reply = run_steps([("summary", Standard(GOAL, (check,)), lambda c: "42")], memory=memory)
        assert reply["steps"][0]["result"]["completion_state"] == "qualified-draft"
    finally:
        memory.close()


def test_harness_03_uses_the_predicate_instead_of_keywords():
    # Removal makes this fail: `result = run(standard, executor, decisions=decisions, memory=memory)` in run_steps.
    memory = Memory(":memory:")
    try:
        check = Check("states the parameter", lambda o: o == "parameter: 42", known_bad=("parameter: 41",))
        reply = run_steps([("summary", Standard(GOAL, (check,)), lambda c: "parameter: 42")], memory=memory)
        assert reply["steps"][0]["result"]["results"] == {"states-the-parameter": "pass"}
    finally:
        memory.close()


def test_harness_04_only_executed_checks_get_receipts():
    # Removal makes this fail: `reports.append({"name": name, "result": result})` in run_steps.
    memory = Memory(":memory:")
    try:
        check = Check("has a total", lambda o: False)
        reply = run_steps([("summary", Standard(GOAL, (check,)), lambda c: "42")], memory=memory)
        assert [e["check"] for e in reply["steps"][0]["result"]["evidence"]] == [check.key]
    finally:
        memory.close()


def test_harness_05_no_placeholder_output_when_executor_is_missing():
    # Removal makes this fail: `result = run(standard, executor, decisions=decisions, memory=memory)` in run_steps.
    memory = Memory(":memory:")
    try:
        reply = run_steps([("summary", Standard(GOAL, (Check("has a total", lambda o: True),)), None)], memory=memory)
        result = reply["steps"][0]["result"]
        assert result["warning_card"]["code"] == "G6_E_HARNESS_NO_EXECUTOR"
        assert "output" not in result
    finally:
        memory.close()


def test_empty_steps_are_refused():
    # Removal makes this fail: `if not steps:` in run_steps.
    assert run_steps([])["warning_card"]["code"] == "G6_E_HARNESS_STEPS"


def test_stopped_steps_do_not_open_the_database(monkeypatch):
    # Removal makes this fail: `if reply.get("stage") == "stop":` in run.
    monkeypatch.setenv("G6_STOP", "pause")
    memory = Memory(":memory:")
    reply = run_steps([("summary", Standard(GOAL, (Check("has a total", lambda o: True),)), lambda c: "42")],
                      memory=memory)
    assert reply["completion_state"] == "blocked-escalated" and memory._conn is None
