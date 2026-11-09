"""Customizable hooks must not disable the watched Codex output protocol."""
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from g6.core import host_models
from g6.core.llm import LLMError
from g6.metaharness.loop import _Counted, _Requests
from g6.self_maintenance import usage


def test_command_hook_cannot_disable_json_and_lose_a_codex_refusal(monkeypatch):
    tmp_path = Path(__file__).resolve().parents[2] / ".review" / "finding-3" / uuid4().hex
    tmp_path.mkdir(parents=True)
    class TextCodex(usage.WatchedCodex):
        def _command(self, *args):
            argv, stdin = super()._command(*args)
            return [arg for arg in argv if arg != "--json"], stdin

    @contextmanager
    def folders():
        root = tmp_path / uuid4().hex
        work, files = root / "work", root / "files"
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    launched = []

    def run(argv, stdin, cwd, files, *args):
        launched.append(stdin)
        if "--json" in argv:
            return 1, '{"type":"error","codex_error_info":"cyber_policy"}\n', ""
        return 1, "This content was flagged for possible cybersecurity risk", ""

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_run", run)
    monkeypatch.setattr(TextCodex, "locate", lambda self: "fake-codex")
    meter = _Requests()
    caller = _Counted(TextCodex(), meter)
    for _ in range(2):
        try:
            caller.complete("same system", "same user")
        except LLMError:
            pass
    assert not meter.in_flight and not usage._SENT
    assert len(launched) <= 1, "a command hook disabled structured observation and resent identical refused bytes"
