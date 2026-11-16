# SPDX-License-Identifier: Apache-2.0
"""Op prints cannot corrupt the stdio protocol or capture another caller's output."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from g6.mcp import invoke_reply
from g6.mcp.composition import compose_reply


@pytest.mark.parametrize("route", ["invoke", "compose"])
@pytest.mark.parametrize("fails", [False, True])
def test_mcp_op_stdout_is_captured(monkeypatch, capsys, registry_reset, route, fails):
    from g6.core.registry import get_registry
    from g6.core.result import Result

    def infer(self, data):
        print("OP_SECRET")
        if fails:
            raise ValueError("operation failed")
        return Result.ok({"check": {"status": "quoted"}, "completion_state": "qualified-draft"})

    monkeypatch.setattr(get_registry().get_class("grounding"), "infer", infer)
    if route == "invoke":
        reply = invoke_reply("grounding", "check_claim")
    else:
        reply = compose_reply({"stages": [{"component": "grounding", "op": "check_claim"}],
                               "inputs": {"claim": "x", "sources": {"manual": "x"}}})
    assert reply["ok"] is (not fails)
    assert capsys.readouterr().out == ""
    print("AFTER_CALL")
    assert capsys.readouterr().out == "AFTER_CALL\n"


def test_concurrent_mcp_calls_leave_unrelated_stdout_alone(monkeypatch, capsys, registry_reset):
    from g6.core.registry import get_registry
    from g6.core.result import Result

    entered = threading.Barrier(3)
    release = threading.Event()

    def infer(self, data):
        entered.wait(timeout=5)
        assert release.wait(timeout=5)
        print("OP_SECRET")
        return Result.ok({"answer": 42})

    monkeypatch.setattr(get_registry().get_class("grounding"), "infer", infer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke_reply, "grounding", "check_claim") for _ in range(2)]
        try:
            entered.wait(timeout=5)
            print("PROTOCOL_SENTINEL")
        finally:
            release.set()
        assert all(future.result(timeout=10)["ok"] for future in futures)
    print("AFTER_CALL")
    assert capsys.readouterr().out == "PROTOCOL_SENTINEL\nAFTER_CALL\n"

