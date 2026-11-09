"""A reply that exceeds the JSON reader's nesting depth must stay unknown."""
import sys

import pytest

from g6.core import host_models
from g6.core.llm import LLMError
from g6.metaharness import loop
from g6.self_maintenance import usage


@pytest.mark.parametrize("host", [usage.WatchedClaude, usage.WatchedCodex])
def test_unreadable_deep_reply_cannot_be_sent_again(fake_host, monkeypatch, host):
    launches = []
    depth = max(sys.getrecursionlimit() + 100, 20_000)
    printed = '{"type":"item.completed","item":' + '[' * depth + '0' + ']' * depth + '}\n'

    def run(argv, stdin, *args):
        launches.append(stdin)
        return 0, printed, ""

    monkeypatch.setattr(host_models, "_run", run)
    meter = loop._Requests()
    caller = loop._Counted(host(), meter)
    for _ in range(2):
        try:
            caller.complete("system", "same request")
        except (RecursionError, LLMError):
            pass

    assert not meter.in_flight and not usage._SENT
    assert len(launches) == 1, "an unreadable deeply nested reply allowed identical inputs to launch twice"
