"""An instance replacement must not bypass a watched host's final admission."""
import json
from contextlib import contextmanager
from pathlib import Path
from types import MethodType
from uuid import uuid4

from g6.core import host_models
from g6.core.llm import LLMError
from g6.metaharness.loop import _Counted, _Requests
from g6.self_maintenance import usage


def test_instance_dictionary_cannot_replace_the_final_host_completion(monkeypatch):
    tmp_path = Path(__file__).resolve().parents[2] / ".review" / "finding-1" / uuid4().hex
    tmp_path.mkdir(parents=True)
    @contextmanager
    def folders():
        root = tmp_path / uuid4().hex
        work, files = root / "work", root / "files"
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    launched = []

    def run(argv, stdin, cwd, files, *args):
        launched.append((stdin, Path(argv[argv.index("--system-prompt-file") + 1]).read_bytes()))
        return 1, json.dumps({"type": "result", "is_error": True, "stop_reason": "refusal"}), ""

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_run", run)
    monkeypatch.setattr(host_models.ClaudeCodeCaller, "locate", lambda self: "fake-claude")
    meter = _Requests()
    host = usage.WatchedClaude()
    # This is ordinary instance state replacement, without changing a class or module.
    try:
        host.__dict__ = dict(vars(host), complete=MethodType(host_models._HostCaller.complete, host))
    except LLMError:
        return  # Rejecting the replacement is an acceptable repair.
    caller = _Counted(host, meter)
    try:
        for _ in range(2):
            try:
                caller.complete("same system", "same user")
            except LLMError:
                pass
        assert all(item == launched[0] for item in launched)
        assert len(launched) <= 1, "instance state replacement bypassed final admission and resent identical refused bytes"
    finally:
        for records in meter.in_flight.values():
            for record in records:
                meter.release(record)
        assert not usage._SENT
