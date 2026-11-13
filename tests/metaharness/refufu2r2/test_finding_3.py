"""A failed cleanup lookup must not retain the ended host's global record."""
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from g6.core import host_models
from g6.metaharness import loop
from g6.self_maintenance import usage


@pytest.mark.parametrize("host", [usage.WatchedClaude, usage.WatchedCodex])
@pytest.mark.parametrize("outcome", ["answer", "refusal"])
def test_cleanup_open_failure_still_releases_owned_send(fake_host, monkeypatch, host, outcome, consented_provider_fakes):
    original_open = usage._open_input
    failed_folder = None
    mine = []
    launches = []

    @contextmanager
    def open_input(path, **kwargs):
        if failed_folder is not None and Path(path) == failed_folder:
            raise PermissionError("injected failure reopening the reply folder during cleanup")
        with original_open(path, **kwargs) as stream:
            yield stream

    def run(argv, stdin, cwd, files, *args):
        nonlocal failed_folder
        launches.append(stdin)
        mine.extend(usage._SENT)
        (files / "last_message.txt").write_text("answer", encoding="utf-8")
        failed_folder = files
        if outcome == "refusal":
            printed = (json.dumps({"type": "result", "stop_reason": "refusal", "is_error": True})
                       if host is usage.WatchedClaude else '{"type":"error","codex_error_info":"cyber_policy"}\n')
        else:
            printed = (json.dumps({"type": "result", "result": "answer", "is_error": False})
                       if host is usage.WatchedClaude else '{"type":"turn.completed"}\n')
        return 0, printed, ""

    monkeypatch.setattr(usage, "_open_input", open_input)
    monkeypatch.setattr(host_models, "_run", run)
    meter = loop._Requests()
    try:
        caller = loop._Counted(host(), meter)
        for _ in range(2 if outcome == "refusal" else 1):
            try:
                caller.complete("system", "request")
            except (PermissionError, usage.LLMError):
                pass
        assert not meter.in_flight
        if outcome == "refusal":
            assert len(launches) == 1, "reply-folder lookup failure discarded a read refusal and allowed an identical resend"
        assert not any(key in usage._SENT for key in mine), "an ended host retained its global send and gate after cleanup lookup failed"
    finally:
        failed_folder = None
        usage._released(mine)
