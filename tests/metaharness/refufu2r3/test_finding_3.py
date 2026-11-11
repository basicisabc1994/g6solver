"""An earlier valid result cannot authenticate an unrecognized final status."""
import json

from g6.core import host_models
from g6.core.llm import LLMError
from g6.metaharness.loop import _Counted, _Requests
from g6.self_maintenance import usage


def test_malformed_final_status_does_not_inherit_an_earlier_success(review_host, monkeypatch):
    _, launches = review_host
    last = {"type": "result", "is_error": None, "result": "unrecognized final result"}
    assert usage._classify_reply("claude", 0, json.dumps(last))[0] == "unknown"
    printed = "\n".join(map(json.dumps, [
        {"type": "result", "is_error": False, "result": "earlier answer"}, last,
    ]))

    def run(argv, stdin, cwd, files, *args):
        launches.append((list(argv), stdin))
        return 0, printed, ""

    monkeypatch.setattr(host_models, "_run", run)
    caller = _Counted(usage.WatchedClaude(), _Requests())
    answers = []
    for _ in range(2):
        try:
            answers.append(caller.complete("system", "request"))
        except LLMError:
            pass
    assert not answers, "a result with an unrecognized final status was returned as a successful answer"
    assert len(launches) == 1, "the unknown final result did not bar its actual inputs"
