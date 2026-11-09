"""A later error does not turn an earlier provider limit into a failed case."""
import json

import pytest

from g6.core import host_models
from g6.metaharness import loop
from g6.self_maintenance import usage


@pytest.mark.parametrize("status", [429, 529])
def test_claude_limit_then_another_error_still_interrupts_tick(fake_host, monkeypatch, status):
    events = [
        {"type": "result", "is_error": True, "api_error_status": status, "result": "provider stop"},
        {"type": "result", "is_error": True, "result": "another failure, no completed answer"},
    ]
    printed = "\n".join(json.dumps(event) for event in events)
    monkeypatch.setattr(host_models, "_run", lambda *args: (1, printed, ""))
    meter = loop._Requests()
    meter.open(10)
    failed = None
    try:
        loop._Counted(usage.WatchedClaude(), meter).complete("system", "request")
    except BaseException as exc:
        failed = exc
    assert isinstance(failed, loop._AtLimit), f"a read provider stop became a failed case: {failed!r}"
    assert meter.limited is not None
