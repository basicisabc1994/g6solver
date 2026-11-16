# SPDX-License-Identifier: Apache-2.0
"""A component runtime error becomes a short, masked CLI failure."""
from __future__ import annotations

import json

import pytest

from g6.cli import main


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("error", [RuntimeError, ValueError, OSError])
def test_invoked_op_failure_is_masked_and_exits_nonzero(monkeypatch, capsys, registry_reset, json_mode, error):
    from g6.core.registry import get_registry

    cls = get_registry().get_class("grounding")

    def fail(self, data):
        raise error("private exception detail /private/example.py")

    monkeypatch.setattr(cls, "infer", fail)
    argv = ["invoke", "grounding", "check_claim", "{}"] + (["--json"] if json_mode else [])
    assert main(argv) != 0
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "grounding" in text and "check_claim" in text and "failed while running" in text
    assert "private" not in text and "Traceback" not in text and error.__name__ not in text
    assert "blocked-escalated" in text
    if json_mode:
        reply = json.loads(captured.out)
        assert reply["completion_state"] == "blocked-escalated"
        assert reply["warning_card"]["code"] == "G6_E_INVOKE_FAILED"
        assert "failed while running" in reply["warning_card"]["message"]
        assert reply["request_id"]
        assert captured.err == ""


@pytest.mark.parametrize("code", [0, "private exception detail"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_systemexit_from_an_op_is_a_masked_failure(monkeypatch, capsys, registry_reset, code, json_mode):
    from g6.core.registry import get_registry

    def fail(self, data):
        raise SystemExit(code)

    monkeypatch.setattr(get_registry().get_class("grounding"), "infer", fail)
    argv = ["invoke", "grounding", "check_claim", "{}"] + (["--json"] if json_mode else [])
    assert main(argv) == 1
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "failed while running" in text and "G6_E_INVOKE_FAILED" in text
    assert "private" not in text
    if json_mode:
        assert json.loads(captured.out)["request_id"]


def test_json_refusal_prints_the_door_envelope(monkeypatch, capsys, registry_reset):
    assert main(["invoke", "no_such_component", "run", "{}", "--json"]) == 1
    captured = capsys.readouterr()
    reply = json.loads(captured.out)
    assert reply["ok"] is False
    assert reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_UNKNOWN_COMPONENT"
    assert reply["request_id"]
    assert captured.err == ""


@pytest.mark.parametrize("json_mode", [False, True])
def test_keyboard_interrupt_still_propagates(monkeypatch, registry_reset, json_mode):
    from g6.core.registry import get_registry

    def interrupt(self, data):
        raise KeyboardInterrupt

    monkeypatch.setattr(get_registry().get_class("grounding"), "infer", interrupt)
    argv = ["invoke", "grounding", "check_claim", "{}"] + (["--json"] if json_mode else [])
    with pytest.raises(KeyboardInterrupt):
        main(argv)


@pytest.mark.parametrize("json_mode", [False, True])
def test_unserializable_result_is_masked_and_keeps_door_identity(monkeypatch, capsys, registry_reset, json_mode):
    from g6.core.registry import get_registry
    from g6.core.result import Result

    def infer(self, data):
        return Result.ok({"completion_state": "qualified-draft", "run_id": "test-run", ("tuple",): "private detail"})

    monkeypatch.setattr(get_registry().get_class("grounding"), "infer", infer)
    argv = ["invoke", "grounding", "check_claim", "{}"] + (["--json"] if json_mode else [])
    assert main(argv) == 1
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "G6_E_INVOKE_FAILED" in text and "the result could not be written as JSON" in text
    assert "private" not in text and "Traceback" not in text
    if json_mode:
        reply = json.loads(captured.out)
        assert reply["completion_state"] == "blocked-escalated"
        assert reply["warning_card"]["code"] == "G6_E_INVOKE_FAILED"
        assert reply["request_id"] and reply["run_id"] == "test-run"
        assert captured.err == ""
