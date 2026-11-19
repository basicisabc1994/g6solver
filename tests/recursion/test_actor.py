# SPDX-License-Identifier: Apache-2.0
"""Offline actor episodes over public S1 task inputs."""
from __future__ import annotations

import importlib
import json
from dataclasses import asdict, fields, replace

import pytest

from g6.core.llm import FakeLLM
from g6.recursion.tasks import CheckSpec, TaskSpec, licence_for


@pytest.fixture
def api():
    class API:
        def __getattr__(self, name):
            return getattr(importlib.import_module("g6.recursion.ports"), name)

    return API()


@pytest.fixture
def task():
    return TaskSpec("apps:toy", "addition", "apps", licence_for("apps"), "Print input plus two.",
                    CheckSpec("stdin_stdout", inputs=("3\n",), outputs=("5\n",)),
                    "PRIVATE_SENTINEL:/secret/reference.py", "introductory", "", "train",
                    source_split="train", train_ok=True)


def response(content=None, calls=None, **changes):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}], **changes}


def call(arguments='{"value": 2}', name="echo", ident="c1"):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": arguments}}


def fake(*replies):
    stream = iter(replies)
    return FakeLLM(lambda _s, _u: json.dumps(next(stream)))


def echo(api, seen):
    return api.Tool("echo", "Return a public integer.",
                    {"type": "object", "properties": {"value": {"type": "integer"}},
                     "required": ["value"], "additionalProperties": False},
                    lambda value: seen.append(value) or {"value": value})


def run(api, task, caller, *, tools=(), spec=None, budget=None, recorder=None):
    recorder = recorder or api.EpisodeRecorder(episode_id="episode-1")
    actor = api.ActorPort(caller, model_id="gpt-oss:20b", adapter_id=None)
    spec = spec or {"tools": [t.name for t in tools]}
    attempt = api.run_procedure(task, spec, tools=api.ToolPort(tools), actor=actor,
                                recorder=recorder, seed=17, budget=budget or api.EpisodeBudget())
    return attempt, recorder


@pytest.mark.parametrize("arguments", ["{", "[]", "null", '{"value": NaN}',
                                        '{"value": 1, "value": 2}', '{"value": true}',
                                        '{"value": 2, "hidden_ref": "x"}'])
def test_malformed_tool_arguments_are_recorded(api, task, arguments):
    seen = []
    attempt, recorder = run(api, task, fake(response(calls=[call(arguments)])), tools=[echo(api, seen)])
    assert attempt.status == "failed"
    assert seen == []
    assert any(row["kind"] == "failure" and row["body"]["code"] == "G6_E_RSI_TOOL_CALL_INVALID"
               for row in recorder.entries())
    assert any(row["kind"] == "response" for row in recorder.entries())


@pytest.mark.parametrize("reply", [{}, {"choices": []}, {"choices": [None]}, response(1), response(""),
                                   response("x", calls={}), response(calls=[{}]),
                                   response("<|start|>assistant<|message|>bad"),
                                   response("partial", choices=[{"message": {"role": "assistant", "content": "x"},
                                                                  "finish_reason": "length"}])])
def test_bad_response_shapes_are_failures(api, task, reply):
    attempt, recorder = run(api, task, fake(reply))
    assert attempt.status == "failed"
    assert any(row["kind"] == "failure" for row in recorder.entries())


def test_tool_batch_is_validated_before_any_dispatch(api, task):
    seen = []
    attempt, _ = run(api, task, fake(response(calls=[call(ident="a"), call("{", ident="b")])),
                     tools=[echo(api, seen)])
    assert attempt.status == "failed" and seen == []


def test_tools_and_final_answer_keep_every_packet_lineage(api, task):
    seen = []
    caller = fake(response(calls=[call()]), response("print(int(input()) + 2)"))
    attempt, recorder = run(api, task, caller, tools=[echo(api, seen)])
    assert attempt.status == "completed" and attempt.answer == "print(int(input()) + 2)"
    assert seen == [2]
    packets = [r["body"] for r in recorder.entries() if r["kind"] == "packet"]
    assert len(packets) == 2
    expected = {"task_id": task.task_id, "source": task.source, "licence_id": task.licence_id,
                "content_digest": task.provenance.content_digest, "model_id": "gpt-oss:20b", "adapter_id": None}
    assert all(p["metadata"]["provenance"] == expected for p in packets)
    requests = [json.loads(user) for _system, user in caller.calls]
    assert any(m["role"] == "tool" and m["tool_call_id"] == "c1" for m in requests[-1]["messages"])
    assert any(r["kind"] == "tool" for r in recorder.entries())


def test_public_projection_has_no_back_reference(api, task):
    public = api.public_task(task)
    assert "hidden_ref" not in {f.name for f in fields(public)}
    assert "PRIVATE_SENTINEL" not in repr(public)
    assert "PRIVATE_SENTINEL" not in json.dumps(asdict(public))
    assert not hasattr(public, "__dict__")
    with pytest.raises(AttributeError):
        getattr(public, "hidden_ref")
    caller = fake(response("done"))
    _, recorder = run(api, task, caller)
    assert "PRIVATE_SENTINEL" not in json.dumps(recorder.entries())
    assert "PRIVATE_SENTINEL" not in repr(caller.calls)


@pytest.mark.parametrize("spec", [
    {"hidden_ref": "x"}, {"steps": [{"op": "read", "path": "hidden_ref"}]},
    {"steps": [{"op": "actor", "prompt": "{{task.hidden_ref}}"}]},
    {"tools": ["open"]}, {"search_width": 0}, {"repair_budget": -1},
    {"stop_rules": ["exec"]}, {"steps": [{"op": "tool", "tool": "open", "arguments": {}}]},
])
def test_procedure_cannot_request_hidden_paths_or_code(api, task, spec):
    caller = fake(response("should not run"))
    attempt, recorder = run(api, task, caller, spec=spec)
    assert attempt.status == "failed" and caller.calls == []
    assert any(r["kind"] == "failure" and r["body"]["code"] == "G6_E_RSI_PROCEDURE_INVALID"
               for r in recorder.entries())


def test_same_seed_same_request_bytes(api, task):
    callers = [fake(response("done")), fake(response("done"))]
    for caller in callers:
        run(api, task, caller)
    assert callers[0].calls == callers[1].calls
    request = json.loads(callers[0].calls[0][1])
    assert request["seed"] == 17 and request["temperature"] == 0
    assert request["reasoning_effort"] == "low"


def test_hidden_reference_changes_only_task_digest(api, task):
    changed = replace(task, hidden_ref="ANOTHER_PRIVATE_REFERENCE", provenance=None)
    callers = [fake(response("done")), fake(response("done"))]
    for item, caller in zip((task, changed), callers):
        run(api, item, caller)
    left, right = [json.loads(c.calls[0][1]) for c in callers]
    for request in (left, right):
        user = json.loads(request["messages"][1]["content"])
        user["provenance"].pop("content_digest")
        request["messages"][1]["content"] = json.dumps(user, sort_keys=True)
    assert left == right


def test_unknown_tool_and_duplicate_ids_do_not_execute(api, task):
    for calls in ([call(name="read_file")], [call(), call()]):
        seen = []
        attempt, recorder = run(api, task, fake(response(calls=calls)), tools=[echo(api, seen)])
        assert attempt.status == "failed" and seen == []
        assert any(r["kind"] == "failure" for r in recorder.entries())


@pytest.mark.parametrize("budget", [{"max_requests": 1}, {"max_tool_calls": 1}, {"max_output_tokens": 32}])
def test_episode_budget_cuts_before_excess_work(api, task, budget):
    seen = []
    replies = [response(calls=[call(ident="a"), call(ident="b")]), response("done")]
    caller = fake(*replies)
    attempt, recorder = run(api, task, caller, tools=[echo(api, seen)], budget=api.EpisodeBudget(**budget))
    assert attempt.status == "budget_exhausted"
    assert len(caller.calls) == 1
    if budget.get("max_tool_calls"):
        assert seen == []
    assert any(r["kind"] == "failure" and r["body"]["code"] == "G6_E_RSI_BUDGET_EXHAUSTED"
               for r in recorder.entries())


def test_data_steps_and_failure_stop_are_interpreted(api, task):
    seen = []
    spec = {"tools": ["echo"], "steps": [
        {"op": "tool", "tool": "echo", "arguments": {"value": 9}},
        {"op": "actor", "prompt": "Use the observation to submit."}]}
    attempt, _ = run(api, task, fake(response("9")), tools=[echo(api, seen)], spec=spec)
    assert attempt.status == "completed" and seen == [9]


def test_repair_budget_and_search_width_are_bounded(api, task):
    caller = fake(response(calls=[call("{")]), response("fixed"))
    attempt, recorder = run(api, task, caller, tools=[echo(api, [])],
                             spec={"tools": ["echo"], "repair_budget": 1, "stop_rules": ["final"]})
    assert attempt.status == "completed" and len(caller.calls) == 2
    assert sum(r["kind"] == "failure" for r in recorder.entries()) == 1
    caller = fake(response(calls=[call("{")]), response("second candidate"))
    attempt, _ = run(api, task, caller, tools=[echo(api, [])], spec={"tools": ["echo"], "search_width": 2})
    assert attempt.status == "completed" and len(caller.calls) == 2


def test_recorder_reopens_and_detaches_rows(api, tmp_path):
    path = tmp_path / "episode.jsonl"
    recorder = api.EpisodeRecorder(episode_id="e", path=path)
    payload = {"answer": [1]}
    recorder.record("response", payload)
    payload["answer"].append(2)
    reopened = api.EpisodeRecorder(episode_id="e", path=path)
    assert reopened.entries()[0]["body"] == {"answer": [1]}
    reopened.record("failure", {"code": "fixture"})
    assert len(api.EpisodeRecorder(episode_id="e", path=path).entries()) == 2


def test_transport_errors_are_recorded_without_exception_text(api, task):
    def fail(_s, _u):
        raise RuntimeError("PRIVATE_SENTINEL")

    attempt, recorder = run(api, task, FakeLLM(fail))
    assert attempt.status == "failed"
    rows = recorder.entries()
    assert any(r["kind"] == "failure" and r["body"]["code"] == "G6_E_RSI_ACTOR_FAILED" for r in rows)
    assert "PRIVATE_SENTINEL" not in json.dumps(rows)


def test_tool_failure_records_the_engine_observation(api, task):
    tool = api.Tool("echo", "Return a refusal.", {"type": "object", "properties": {}},
                    lambda: {"completion_state": "blocked-escalated", "reason": "fixture refusal"})
    attempt, recorder = run(api, task, fake(response(calls=[call("{}")])), tools=[tool])
    assert attempt.status == "failed"
    assert any(r["kind"] == "tool" and r["body"]["result"]["reason"] == "fixture refusal"
               for r in recorder.entries())


def test_non_json_transport_reply_is_a_recorded_failure(api, task, monkeypatch):
    caller = api.ActorCaller()
    monkeypatch.setattr(caller, "complete_request", lambda request: {"choices": [], "usage": float("nan")})
    attempt, recorder = run(api, task, caller)
    assert attempt.status == "failed"
    assert any(r["kind"] == "failure" for r in recorder.entries())


def test_packet_pruning_keeps_recent_complete_turns_and_adapter(api, task):
    packets = importlib.import_module("g6.recursion.packets")
    observations = tuple(({"role": "user", "content": f"turn-{i}:" + "x" * 300},) for i in range(12))
    packet = packets.build_actor_packet(api.public_task(task), model_id="gpt-oss:20b", adapter_id="adapter-7",
                                        run_id="pruning", budget=512, observations=observations)
    assert packet.used_tokens <= 512
    assert packet.metadata["provenance"]["adapter_id"] == "adapter-7"
    assert packet.metadata["context_truncated"] is True
    kept = " ".join(item.value for item in packet.tool)
    assert "turn-11:" in kept and "turn-0:" not in kept


def test_oversize_task_stops_before_model(api, task):
    task = replace(task, prompt="x" * 10000, provenance=None)
    caller = fake(response("done"))
    attempt, _ = run(api, task, caller, spec={"packet_tokens": 256})
    assert attempt.status == "budget_exhausted" and caller.calls == []


def test_recorder_detaches_in_memory_rows(api):
    recorder = api.EpisodeRecorder(episode_id="memory")
    payload = {"items": [1]}
    recorder.record("response", payload)
    payload["items"].append(2)
    rows = recorder.entries()
    assert rows[0]["body"] == {"items": [1]}
    rows[0]["body"]["items"].append(3)
    assert recorder.entries()[0]["body"] == {"items": [1]}


def test_malformed_response_json_is_retained_for_diagnosis(api, task):
    attempt, recorder = run(api, task, FakeLLM(default='{"choices":'))
    assert attempt.status == "failed" and attempt.code == "G6_E_RSI_RESPONSE_INVALID"
    assert any(r["kind"] == "response" and r["body"]["raw_response"] == '{"choices":'
               for r in recorder.entries())


def test_failed_public_tool_observation_reaches_repair_packet(api, task):
    tool = api.Tool("echo", "Run a public check.", {"type": "object", "properties": {}},
                    lambda: {"verdict": "failed", "failure_messages": ["public assertion: expected 5"]})
    caller = fake(response(calls=[call("{}")]), response("repaired"))
    attempt, _ = run(api, task, caller, tools=[tool],
                     spec={"tools": ["echo"], "repair_budget": 1, "stop_rules": ["final"]})
    assert attempt.status == "completed"
    messages = json.loads(caller.calls[1][1])["messages"]
    assert any(m["role"] == "tool" and "public assertion: expected 5" in m["content"] for m in messages)


def test_no_answer_step_records_failure(api, task):
    attempt, recorder = run(api, task, fake(), tools=[echo(api, [])],
                             spec={"tools": ["echo"], "steps": [
                                 {"op": "tool", "tool": "echo", "arguments": {"value": 1}}]})
    assert attempt.status == "failed"
    assert any(r["kind"] == "failure" for r in recorder.entries())
