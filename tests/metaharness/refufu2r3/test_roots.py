"""Inventories for reply lifetime, cleanup custody and total result states."""
import ast
import inspect
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from g6.core import host_models
from g6.core.llm import LLMError
from g6.harness.completion_loop import ToolSession
from g6.metaharness import loop
from g6.self_maintenance import usage


MISSING = object()
STATUSES = [True, False, None, MISSING, "false", 0, [], {}]
POSITIONS = ["first", "middle", "last", "alone"]
DEATHS = [KeyboardInterrupt, SystemExit, ValueError]


def result(status):
    event = {"type": "result", "result": "answer"}
    if status is not MISSING:
        event["is_error"] = status
    return event


def ordered(event, good, position):
    return {"first": [event, good], "middle": [good, event, good],
            "last": [good, event], "alone": [event]}[position]


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("position", POSITIONS)
def test_every_claude_status_at_every_position_and_host(provider, status, position):
    events = ordered(result(status), result(False), position)
    expected = ("unknown" if provider == "codex" or (status is not True and status is not False)
                else "failed" if status is True else "answer")
    assert usage._classify_reply(provider, 0, "\n".join(map(json.dumps, events)))[0] == expected


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("position", POSITIONS)
def test_every_native_codex_error_status_at_every_position(status, position):
    event = {"type": "turn.completed"}
    if status is not MISSING:
        event["error"] = status
    # Native Codex already declares failure through turn.failed or a non-null error field. Its error payload's shape
    # does not erase that recognised failure; the earlier reviewed failure/resend rule stands.
    expected = "answer" if status is MISSING or status is None else "failed"
    events = ordered(event, {"type": "turn.completed"}, position)
    printed = "\n".join(map(json.dumps, events))
    assert usage._classify_reply("codex", 0, printed)[0] == expected
    assert usage.codex_said(printed) == ""  # these payloads confer no provider-stop authority


def test_text_that_mentions_a_refusal_is_an_answer_only_under_a_stated_status():
    """An earlier review's case, whose fake result named no status. With the status Claude Code states, text that mentions
    a refusal is returned as the answer. With none stated the reply is unknown, and still is not read as a refusal."""
    text = 'a reply mentioning "stop_reason":"refusal"'
    stated = json.dumps({"type": "result", "is_error": False, "result": text})
    assert usage.WatchedClaude()._reply(0, stated, "", None, ()) == text
    unstated = json.dumps({"type": "result", "result": text})
    assert usage._classify_reply("claude", 0, unstated)[0] == "unknown"
    with pytest.raises(LLMError, match=usage.HOST_UNKNOWN) as raised:
        usage.WatchedClaude()._reply(0, unstated, "", None, ())
    assert usage.MODEL_REFUSED not in str(raised.value)


@pytest.mark.parametrize("host,reader", [
    ("claude", "transport"), ("codex", "transport"),
    ("claude", "classification"), ("codex", "classification"),
    ("claude", "native"), ("codex", "native"),
    ("session", "transport"), ("session", "classification"),
    ("session", "native"),
])
@pytest.mark.parametrize("death", DEATHS)
def test_every_reply_reader_bars_the_captured_admission(review_host, monkeypatch, host, reader, death):
    root, launches = review_host
    provider = "claude" if host == "session" else host
    admitted = []
    meter = loop._Requests()
    original_run, original_read = host_models._run, usage._read_reply
    original_native = host_models.ClaudeCodeCaller._reply if provider == "claude" else host_models.CodexCaller._reply
    counts = {"classification": 0, "native": 0}

    def interrupt(kind):
        counts[kind] += 1
        if kind == reader and counts[kind] == 1:
            raise death("interrupted reply read")

    def reading(*args):
        interrupt("classification")
        return original_read(*args)

    def native(*args):
        interrupt("native")
        return original_native(*args)

    def running(argv, stdin, cwd, files, *args):
        assert len(meter.in_flight) == 1
        admitted.append(next(iter(meter.in_flight)))
        if reader == "transport":
            launches.append((argv, stdin))
            raise death("interrupted transport read")
        return original_run(argv, stdin, cwd, files, *args)

    monkeypatch.setattr(usage, "_read_reply", reading)
    monkeypatch.setattr(host_models.ClaudeCodeCaller if provider == "claude" else host_models.CodexCaller,
                        "_reply", native)
    monkeypatch.setattr(host_models, "_run", running)
    if host == "session":
        session = ToolSession()
        usage.read_stops(session)

        class Caller:
            def complete(self, system, user, **kwargs):
                return session.run(user, root, root, 1)
        caller = Caller()
    else:
        caller = usage.WatchedClaude() if host == "claude" else usage.WatchedCodex()
    for _ in range(2):
        try:
            loop._Counted(caller, meter).complete("system", "identical input")
        except BaseException:
            pass
    assert len(launches) == 1
    assert meter.unknown == set(admitted)
    assert not meter.in_flight and not usage._SENT


@pytest.mark.parametrize("step", ["before-open", "after-open", "before-rename", "after-rename",
                                  "after-recheck", "before-dispose"])
def test_cleanup_swap_at_every_custody_step(review_host, monkeypatch, step):
    root, _ = review_host
    source = root / "source"
    source.write_bytes(b"input")
    request = usage.Request.host(["fake", "--settings", str(source)], "", root)
    request.handoff()
    target, _ = request._delivery.folders[0]
    victim, saved = root / "victim", root / "saved"
    victim.mkdir()
    (victim / "keep").write_bytes(b"unrelated")
    request._delivery.anchors.close()
    attempted = []

    def swap(path):
        attempted.append(path)
        try:
            path.rename(saved)
            victim.rename(path)
        except PermissionError:
            pass  # Native custody may deny the attempted swap.

    original_open = usage._open_input
    rename = getattr(usage, "_rename_private", None)
    dispose = getattr(usage, "_dispose_private", None)

    @contextmanager
    def opening(path, **kwargs):
        if step == "before-open" and path == target:
            swap(path)
        with original_open(path, **kwargs) as stream:
            if step == "after-open" and path == target:
                swap(path)
            yield stream
        if step == "after-recheck" and path.name.startswith(".g6-clean-"):
            swap(path)

    def renaming(stream, parent, name):
        if step == "before-rename":
            swap(target)
        answer = rename(stream, parent, name)
        if step == "after-rename":
            swap(target.parent / name)
        return answer

    def disposing(stream):
        if step == "before-dispose" and Path(usage._final_path(stream)).name.startswith(".g6-clean-"):
            swap(Path(usage._final_path(stream)))
        return dispose(stream)

    monkeypatch.setattr(usage, "_open_input", opening)
    if rename is not None:
        monkeypatch.setattr(usage, "_rename_private", renaming)
    if dispose is not None:
        monkeypatch.setattr(usage, "_dispose_private", disposing)
    try:
        request.close()
    except LLMError as error:
        assert "G6_E_LOOP_INPUT_CHANGED" in str(error)
    assert attempted, "the requested cleanup step was not challenged"
    assert any((p / "keep").exists() for p in (victim, target, *root.glob(".g6-clean-*")))


def test_cleanup_checks_all_children_before_deleting_any(review_host):
    root, _ = review_host
    source = root / "source"
    source.write_bytes(b"input")
    request = usage.Request.host(["fake", "--settings", str(source)], "", root)
    argv, _ = request.handoff()
    private, _ = request._delivery.folders[0]
    (private / "unexpected").mkdir()
    (private / "unexpected" / "keep").write_bytes(b"unrelated")
    with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
        request.close()
    retained = next(root.glob(".g6-clean-*"))
    assert (retained / Path(argv[-1]).name).read_bytes() == b"input"
    assert (retained / "unexpected" / "keep").read_bytes() == b"unrelated"


@pytest.mark.parametrize("where", ["root", "child"])
def test_missing_cleanup_objects_are_reported_before_deletion(review_host, monkeypatch, where):
    root, _ = review_host
    source = root / "source"
    source.write_bytes(b"input")
    request = usage.Request.host(["fake", "--settings", str(source)], "", root)
    request.handoff()
    private, _ = request._delivery.folders[0]
    original = usage._open_input
    missing = []

    @contextmanager
    def opening(path, **kwargs):
        if (where == "root" and path == private) or (where == "child" and kwargs.get("delete") and path.name == "source"):
            missing.append(path)
            raise FileNotFoundError("interrupted cleanup lookup")
        with original(path, **kwargs) as stream:
            yield stream

    monkeypatch.setattr(usage, "_open_input", opening)
    with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
        request.close()
    assert missing
    assert any((p / "source").read_bytes() == b"input" for p in (*root.glob(".g6-input-*"), *root.glob(".g6-clean-*")))


def test_cleanup_removes_owned_nested_copies_and_hardlink_aliases(review_host):
    import os

    root, _ = review_host
    (root / "nested").mkdir()
    first, other = root / "first", root / "nested" / "other"
    first.write_bytes(b"same")
    os.link(first, other)
    request = usage.Request.host(["fake", "--settings", str(first), "--mcp-config", str(other)], "", root)
    request.handoff()
    request.close()
    request.close()
    assert not list(root.glob(".g6-input-*")) and not list(root.glob(".g6-clean-*"))
    assert first.read_bytes() == other.read_bytes() == b"same"


@pytest.mark.parametrize("step", ["rename", "recheck", "dispose"])
@pytest.mark.parametrize("death", DEATHS)
def test_interrupted_cleanup_and_retry_never_delete_a_replacement(review_host, monkeypatch, step, death):
    root, _ = review_host
    source = root / "source"
    source.write_bytes(b"input")
    request = usage.Request.host(["fake", "--settings", str(source)], "", root)
    request.handoff()
    target, _ = request._delivery.folders[0]
    original_open = usage._open_input
    rename = getattr(usage, "_rename_private", None)
    dispose = getattr(usage, "_dispose_private", None)
    assert rename is not None and dispose is not None
    interrupted = []

    def fail():
        if not interrupted:
            interrupted.append(True)
            raise death("cleanup interruption")

    def renaming(*args):
        result = rename(*args)
        if step == "rename":
            fail()
        return result

    def disposing(*args):
        if step == "dispose":
            fail()
        return dispose(*args)

    @contextmanager
    def opening(path, **kwargs):
        with original_open(path, **kwargs) as stream:
            if step == "recheck" and path.name.startswith(".g6-clean-"):
                fail()
            yield stream

    monkeypatch.setattr(usage, "_rename_private", renaming)
    monkeypatch.setattr(usage, "_dispose_private", disposing)
    monkeypatch.setattr(usage, "_open_input", opening)
    with pytest.raises(death, match="cleanup interruption"):
        request.close()
    assert interrupted
    # A restart may find another directory at the old name. Identity mismatch must keep it intact.
    if not target.exists():
        target.mkdir()
    (target / "keep").write_bytes(b"unrelated")
    try:
        request.close()
    except LLMError:
        pass
    assert (target / "keep").read_bytes() == b"unrelated"


def test_reply_reader_inventory_from_the_code():
    calls = {}

    def walk(node, scope=""):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            scope = f"{scope}.{node.name}".lstrip(".")
        if isinstance(node, ast.Call):
            calls.setdefault(ast.unparse(node.func), set()).add(scope)
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    walk(ast.parse(Path(usage.__file__).read_text(encoding="utf-8")))
    assert calls["host_models._run"] == {"_complete_host", "WatchedClaude._launch"}
    assert calls["host_models._result"] == {"_read_reply"}
    assert calls["_read_reply"] == {"_classify_reply", "_codex_turn"}
    assert calls["_event_outcome"] == {"_reply_outcome"}
    assert calls["_observe_reply"] == {"_complete_host", "WatchedClaude._launch",
                                       "WatchedClaude._reply", "WatchedCodex._reply"}
    session = inspect.getsource(ToolSession.run)
    assert "self._launch(" in session and "self._reply(" not in session
    # A session now returns its extracted answer, so there is no later raw-reply reader after release.
    assert "self._reply(" in inspect.getsource(ToolSession._launch)
    assert "return reply(" in inspect.getsource(usage.WatchedClaude._launch)
    # The earlier inventory names each bound transport/native reader; no new hidden reader is permitted.
    from tests.metaharness.refufu2r2.test_roots import test_reader_and_ownership_inventory_from_the_code
    test_reader_and_ownership_inventory_from_the_code()


def test_every_pathname_mutation_is_classified_from_the_code():
    found = set()
    base = Path(usage.__file__).parents[1]
    for package in ("self_maintenance", "metaharness"):
        for path in (base / package).rglob("*.py"):
            def visit(node, scope=""):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    scope = f"{scope}.{node.name}".lstrip(".")
                if isinstance(node, ast.Call):
                    name = ast.unparse(node.func)
                    if name in {"os.replace", "os.rename", "os.remove", "os.unlink", "os.rmdir", "shutil.rmtree",
                                "shutil.move"} or name.endswith((".unlink", ".rename", ".rmdir")):
                        found.add((path.relative_to(base).as_posix(), scope, name))
                for child in ast.iter_child_nodes(node):
                    visit(child, scope)
            visit(ast.parse(path.read_text(encoding="utf-8")))
    # Atomic publication moves replace one file entry; none recursively deletes a directory. unlink cannot traverse one.
    assert found == {
        ("metaharness/api.py", "_write_state", "os.replace"),
        ("metaharness/api.py", "_write_state", "Path(name).unlink"),
        ("self_maintenance/digest.py", "write", "os.replace"),
        ("self_maintenance/envelope.py", "beat", "os.replace"),
    }
    assert "SetFileInformationByHandle" in inspect.getsource(usage._rename_private)
    assert "SetFileInformationByHandle" in inspect.getsource(usage._dispose_private)
    assert "delete=True" in inspect.getsource(usage.Request.close)


@pytest.mark.parametrize("site", ["digest", "beat", "state", "state-unlink"])
def test_file_publication_mutations_cannot_remove_a_planted_directory(review_host, monkeypatch, site):
    from datetime import date
    import os
    from g6.metaharness import api
    from g6.self_maintenance import digest, envelope

    root, _ = review_host
    victim = root / "victim"
    victim.mkdir()
    (victim / "keep").write_bytes(b"unrelated")
    original = os.replace
    planted = []

    def replace(source, target):
        if site == "state-unlink":
            original(source, target)
            victim.rename(source)
            planted.append(Path(source))
        else:
            victim.rename(target)
            planted.append(Path(target))
            original(source, target)

    monkeypatch.setattr(os, "replace", replace)
    try:
        if site == "digest":
            digest.write(root, date(2026, 1, 1), "page")
        elif site == "beat":
            monkeypatch.setattr(envelope, "BEAT_TRIES", 1)
            envelope.beat(root, alive=True)
        else:
            api._write_state(root / "state.json", {"alive": True})
    except OSError:
        pass
    assert planted and (planted[0] / "keep").read_bytes() == b"unrelated"


@pytest.mark.parametrize("site", ["digest", "beat", "state"])
def test_publication_cannot_move_an_unchecked_source_directory(review_host, monkeypatch, site):
    from datetime import date
    import os
    from g6.metaharness import api
    from g6.self_maintenance import digest, envelope

    root, _ = review_host
    victim = root / "victim"
    victim.mkdir()
    (victim / "keep").write_bytes(b"unrelated")
    original = os.replace
    planted = []

    def replace(source, target):
        source = Path(source)
        source.rename(source.with_name(source.name + ".saved"))
        victim.rename(source)
        planted.append(source)
        original(source, target)

    monkeypatch.setattr(os, "replace", replace)
    if site == "digest":
        digest.write(root, date(2026, 1, 1), "page")
    elif site == "beat":
        envelope.beat(root, alive=True)
    else:
        api._write_state(root / "state.json", {"alive": True})
    assert planted and (planted[0] / "keep").exists(), "publication moved an unchecked directory planted at its source"
