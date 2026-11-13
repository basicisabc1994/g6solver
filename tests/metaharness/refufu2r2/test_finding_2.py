"""A Claude result envelope is not a completed Codex JSONL turn."""
import json

from g6.core import host_models
from g6.core.llm import LLMError
from g6.metaharness import loop
from g6.self_maintenance import usage


def test_codex_without_a_codex_terminal_event_stays_unknown(fake_host, monkeypatch, consented_provider_fakes):
    launches = []
    printed = json.dumps({"type": "result", "is_error": False, "result": "foreign result envelope"})

    def run(argv, stdin, cwd, files, *args):
        launches.append(stdin)
        (files / "last_message.txt").write_text("answer", encoding="utf-8")
        return 0, printed, ""

    monkeypatch.setattr(host_models, "_run", run)
    meter = loop._Requests()
    caller = loop._Counted(usage.WatchedCodex(), meter)
    answers = []
    for _ in range(2):
        try:
            answers.append(caller.complete("system", "same request"))
        except LLMError:
            pass

    assert not answers, "a Claude result envelope authorized an answer without a completed Codex turn"
    assert len(launches) == 1
    assert len(meter.unknown) == 1
