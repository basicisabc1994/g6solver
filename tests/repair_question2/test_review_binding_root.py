"""A saved review and its independent task binding must agree after every interruption."""
import json
import ast
import subprocess
import sys
from pathlib import Path

import pytest

from g6.mcp import behaviour, review_reply


@pytest.mark.parametrize("damage", ["none", "reply", "binding", "missing", "directory", "foreign"])
def test_saved_pair_is_read_only_when_complete(tmp_path, monkeypatch, damage):
    from g6.mcp import review_binding

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    original = {"ok": True, "artifact_digest": "b" * 64, "assessment": {"outcome": "insufficient_evidence"}}
    reply = behaviour.bound(original, "a" * 64)
    assert reply == original
    path = Path(review_reply.save(reply, "c" * 32))
    side = path.with_suffix(".binding.json")
    assert json.loads(path.read_text()) == original
    if damage == "reply":
        path.write_text('{}')
    elif damage == "binding":
        side.write_text('{')
    elif damage == "missing":
        side.unlink()
    elif damage == "directory":
        side.unlink()
        side.mkdir()
    elif damage == "foreign":
        record = json.loads(side.read_text())
        record['task_identity'] = 'd' * 64
        side.write_text(json.dumps(record))
    assert review_binding.matches(path, "a" * 64) is (damage == "none")


def test_interrupted_save_never_admits_a_reply(tmp_path, monkeypatch):
    from g6.mcp import review_binding

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    reply = behaviour.bound({"assessment": {}, "ok": True}, "a" * 64)
    monkeypatch.setattr(review_binding, "write", lambda *a: (_ for _ in ()).throw(OSError("stop")))
    assert review_reply.save(reply, "c" * 32) is None
    from g6.core.storage import state_path
    assert not review_binding.matches(state_path("reviews", "c" * 32 + ".json"), "a" * 64)


def test_binding_names_written_bytes_even_if_file_is_replaced(tmp_path, monkeypatch):
    from g6.mcp import review_binding

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    original = review_binding.write

    def replace_then_bind(path, reply, data):
        path.write_bytes(b'{"assessment":{},"ok":false}')
        original(path, reply, data)

    monkeypatch.setattr(review_binding, "write", replace_then_bind)
    path = Path(review_reply.save(behaviour.bound({"assessment": {}, "ok": True}, "a" * 64), "c" * 32))
    assert not review_binding.matches(path, "a" * 64)


def test_binding_checks_the_consumed_bytes(tmp_path, monkeypatch):
    from g6.mcp import review_binding

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    path = Path(review_reply.save(behaviour.bound({"assessment": {}, "ok": True}, "a" * 64), "c" * 32))
    assert not review_binding.matches(path, "a" * 64, data=b'{}')


def test_stop_before_sidecar_sync_never_publishes_custody(tmp_path, monkeypatch):
    from g6.mcp import review_binding
    from g6.core.storage import state_path

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    calls = 0
    original = review_binding.os.fsync

    def stop(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("stop before sidecar durability")
        return original(fd)

    monkeypatch.setattr(review_binding.os, "fsync", stop)
    assert review_reply.save(behaviour.bound({"assessment": {}, "ok": True}, "a" * 64), "c" * 32) is None
    assert not review_binding.matches(state_path("reviews", "c" * 32 + ".json"), "a" * 64)


def test_inventory_binding_checks_the_single_read_and_single_serialization():
    root = Path(__file__).resolve().parents[2] / "src/g6/mcp"
    tree = ast.parse((root / "behaviour.py").read_text())
    reader = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_review_candidates")
    text = ast.unparse(reader)
    assert text.count('.read_bytes()') == 1
    assert 'json.loads(saved_bytes)' in text and 'data=saved_bytes' in text
    tree = ast.parse((root / 'review_reply.py').read_text())
    writer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'save')
    text = ast.unparse(writer)
    assert text.count('json.dumps(') == 1
    assert 'out.write(data)' in text and 'review_binding.write(path, reply, data)' in text


def test_every_persisted_field_matches_the_plain_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    fixture = Path(__file__).resolve().parents[1] / 'core/data/worked_example_review_reply.json'
    reply = json.loads(fixture.read_bytes())
    plain = Path(review_reply.save(reply, 'a' * 32)).read_bytes()
    bound = Path(review_reply.save(behaviour.bound(reply, 'c' * 64), 'b' * 32)).read_bytes()
    assert bound == plain
    assert json.loads(bound) == reply


def test_complete_pair_is_read_after_a_process_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    path = review_reply.save(behaviour.bound({"assessment": {}, "ok": True}, 'a' * 64), 'c' * 32)
    code = ("import sys; from pathlib import Path; from g6.mcp.review_binding import matches; "
            "assert matches(Path(sys.argv[1]), sys.argv[2])")
    run = subprocess.run([sys.executable, '-c', code, path, 'a' * 64], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stdout + run.stderr
