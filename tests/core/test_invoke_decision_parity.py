# SPDX-License-Identifier: Apache-2.0
"""Decision summaries are additive on each checked invoke surface."""
import json

import pytest
from returns.result import Failure, Success

import g6
from g6.cli import main
from g6.core import door, registry as registry_module
from g6.mcp import invoke_reply


@pytest.mark.parametrize("outcome", [
    Success({"answer": 42}),
    Success({"completion_state": "verified", "evidence": [{"kind": "test", "ref": "t1"}]}),
    Success({"completion_state": "blocked-escalated", "answer": 42}),
    Failure("[G6_E_INPUT_VALIDATION] component refused the request"),
])
def test_custom_registry_envelopes_match_including_decision(monkeypatch, capsys, outcome):
    class Registry:
        refs = {}

        def invoke(self, component, op, params, **kwargs):
            return outcome

    monkeypatch.setattr(registry_module, "get_registry", Registry)
    monkeypatch.setattr(door, "person_model", lambda: None)
    checked = door.invoke_checked
    replies = []

    def capture(*args, **kwargs):
        reply = checked(*args, **kwargs)
        replies.append(reply)
        return reply

    monkeypatch.setattr(door, "invoke_checked", capture)
    python = g6.invoke("demo", "op", {})
    mcp = invoke_reply("demo", "op", "{}")
    code = main(["invoke", "demo", "op", "{}", "--json"])
    out, err = capsys.readouterr()
    cli = replies[-1]
    decided = [{k: v for k, v in reply.items() if k != "request_id"} for reply in (python, mcp, cli)]
    assert decided[0] == decided[1] == decided[2]
    assert python["decision"] == {"may_proceed": None, "declared": False, "evidence_status": "asserted",
                                  "action_authorization": "not_requested"}
    assert code == (0 if python["ok"] else 1)
    assert json.loads(out) == (mcp["result"] if "result" in mcp else cli)
    assert err == ""


def test_python_keeps_the_real_producers_declared_block(monkeypatch):
    monkeypatch.setattr(door, "person_model", lambda: None)
    params = {"action": {"action_type": "send_email", "target_resource": "customer@example.invalid"}}
    python = g6.invoke("action_gating", "evaluate", params)
    mcp = invoke_reply("action_gating", "evaluate", json.dumps(params))
    assert python["decision"] == mcp["decision"] == {
        "may_proceed": False, "declared": True, "evidence_status": "asserted", "action_authorization": "refused"}


def test_python_parameter_refusal_has_an_unknown_decision(monkeypatch):
    monkeypatch.setattr(door, "person_model", lambda: None)
    reply = g6.invoke("action_gating", "evaluate", ["not a mapping"])
    assert reply["ok"] is False
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert reply["decision"] == {"may_proceed": None, "declared": False, "evidence_status": "asserted",
                                 "action_authorization": "not_requested"}
