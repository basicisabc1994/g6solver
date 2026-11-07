"""Send ownership: review 3's cases and the construction/capture inventories."""
import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from g6.core import host_models, llm
from g6.core.llm import LLMError
from g6.metaharness.loop import _Counted, _Requests
from g6.self_maintenance import usage
from g6.self_maintenance.usage import MODEL_REFUSED


@pytest.mark.parametrize("kind", [host_models.CodexCaller, host_models.ClaudeCodeCaller])
def test_first_host_failure_releases_its_reply_record(monkeypatch, kind):
    tmp_path = Path(__file__).resolve().parents[2] / ".build" / "custody" / uuid4().hex
    tmp_path.mkdir(parents=True)
    monkeypatch.setattr(usage, "_SENT", {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(kind, "locate", lambda self: "fake-host")

    @contextmanager
    def folders():
        root = tmp_path / uuid4().hex
        work, files = root / "work", root / "files"
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    monkeypatch.setattr(host_models, "_folders", folders)
    launches = []

    def failed_run(argv, stdin, cwd, files, *args):
        launches.append(files)
        raise LLMError("fake host timed out before returning a reply")

    monkeypatch.setattr(host_models, "_run", failed_run)
    meter = _Requests()
    for _ in range(3):
        # A wrapper can construct a fresh supported host caller for each request.
        with pytest.raises(LLMError, match="fake host timed out"):
            _Counted(kind(), meter).complete("system", "user")
    assert len(launches) == 3
    assert not meter.in_flight
    assert not usage._SENT, "ended first host calls retain their send records and gates"

def test_rendering_does_not_give_the_same_refused_bytes_a_new_identity(monkeypatch):
    class RenderedText(str):
        def __format__(self, spec):
            return "a display label rather than the stored text"

    sent = []

    def open_request(request, timeout):
        # The real _post_json built this request, including its encoded body.
        # This fake transport never opens a socket.
        sent.append(request.data)
        raise LLMError(f"{MODEL_REFUSED}: policy")

    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    backend = llm.OpenAICompatibleCaller("fake", base_url="http://fake.invalid", api_key=None)
    caller = _Counted(backend, _Requests())
    for user in (RenderedText("same refused input"), "same refused input"):
        with pytest.raises(LLMError):
            caller.complete("system", user)
    assert len(set(sent)) == 1, "the transport must have received identical request bytes"
    assert len(sent) == 1, "the same refused bytes were sent again under their rendering's identity"

def test_aliased_input_files_do_not_send_a_snapshot_different_from_its_record(monkeypatch):
    root = Path(__file__).resolve().parents[2] / ".build" / "custody" / uuid4().hex
    root.mkdir(parents=True)
    old = b'{"mcpServers": {}, "label": "before"}'
    new = b'{"mcpServers": {}, "label": "after"}'

    @contextmanager
    def folders():
        call = root / uuid4().hex
        work, files = call / "work", call / "files"
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    class AliasedClaude(host_models.ClaudeCodeCaller):
        def _command(self, *args):
            argv, stdin = super()._command(*args)
            files = args[-1]
            # Both are valid inputs: the system prompt is JSON text, while
            # --mcp-config reads that same file as a JSON configuration.
            (files / "no_mcp_servers.json").unlink()
            os.link(files / "system_prompt.md", files / "no_mcp_servers.json")
            return argv, stdin

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(AliasedClaude, "locate", lambda self: "fake-claude")
    read_bytes = Path.read_bytes
    changed = []

    def read_then_change(path):
        data = read_bytes(path)
        if path.name == "system_prompt.md" and not changed:
            changed.append(True)
            # A deterministic writer interleaving between capture of the two
            # names, entirely before Request.handoff starts.
            path.write_bytes(new)
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_change)
    sent = []

    def run(argv, stdin, cwd, files, *args):
        # These are the actual inputs, independent of the identity reader.
        sent.append((stdin, read_bytes(files / "system_prompt.md"),
                     read_bytes(files / "no_mcp_servers.json"), read_bytes(files / "g6_settings.json")))
        return 1, json.dumps({"type": "result", "is_error": True, "stop_reason": "refusal"}), ""

    monkeypatch.setattr(host_models, "_run", run)
    caller = _Counted(AliasedClaude(), _Requests())
    for system in (old.decode(), new.decode()):
        with pytest.raises(LLMError):
            caller.complete(system, "same request")
    # A repair may reject aliases, capture one consistent version, or ensure
    # the recorded version is what leaves. None may launch identical refused
    # inputs a second time.
    assert len(sent) == len(set(sent)), "aliased snapshot files gave the same refused host bytes two identities"


def test_observing_a_caller_never_changes_its_class():
    for kind in (host_models.ClaudeCodeCaller, host_models.CodexCaller):
        caller = kind()
        constructed = type(caller)
        usage.read_stops(caller)
        assert type(caller) is constructed
        assert usage.watched(caller)


def test_text_admission_owns_builtin_values_and_their_encoding():
    class Display(str):
        def __str__(self):
            return "display"

        def __format__(self, spec):
            return "display"

        def encode(self, *args):
            return b"display"

    request = usage.Request.text(Display("system"), Display("user"))
    assert type(request.system) is type(request.user) is str
    assert request.encoded == usage.Request.text("system", "user").encoded
    import hashlib

    assert request.identity == hashlib.sha256(request.encoded).hexdigest()


def test_linked_inputs_are_captured_once_and_handed_off_as_private_copies(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"one snapshot")
    os.link(first, second)
    reads = []
    real_read, real_open = Path.read_bytes, Path.open

    class Reading:
        def __init__(self, stream, path):
            self.stream, self.path = stream, path

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self):
            reads.append(self.path)
            return self.stream.read()

    def open_file(path, mode="r", *args, **kwargs):
        stream = real_open(path, mode, *args, **kwargs)
        return Reading(stream, path) if mode == "rb" else stream

    monkeypatch.setattr(Path, "open", open_file)
    request = usage.Request.host(["fake", str(first), str(second)], "user", tmp_path)
    assert len(reads) == 1
    argv, _ = request.handoff()
    assert os.stat(argv[1]).st_ino == os.stat(argv[2]).st_ino
    assert os.stat(argv[1]).st_dev == os.stat(argv[2]).st_dev
    assert Path(argv[1]) not in (first, second)
    assert real_read(Path(argv[1])) == b"one snapshot"
    assert first.read_bytes() == second.read_bytes() == b"one snapshot"


def test_every_class_or_send_replacement_is_at_construction_and_every_capture_uses_file_identity():
    import ast
    import g6

    root = Path(g6.__file__).parent
    replacements, captures, data_fields, constructors = set(), set(), set(), set()

    def visit(node, file, scope=""):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            scope = f"{scope}.{node.name}" if scope else node.name
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "__new__":
                    constructors.add((file, scope, ast.unparse(target)))
                if isinstance(target, ast.Attribute) and target.attr in {"__class__", "complete", "_command", "_launch"}:
                    if (file, scope, target.attr) == ("sources/australian_legal_corpus/store.py", "Index.build", "complete"):
                        data_fields.add((file, scope, target.attr))
                    else:
                        replacements.add((file, scope, target.attr))
        if isinstance(node, ast.Call):
            called = ast.unparse(node.func).rsplit(".", 1)[-1]
            if called in {"setattr", "__setattr__"} and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                if node.args[1].value in {"__class__", "complete", "_command", "_launch"}:
                    replacements.add((file, scope, node.args[1].value))
            if file == "self_maintenance/usage.py" and not scope.startswith("read_limits"):
                if called in {"read", "readinto", "read_bytes", "read_text", "open"}:
                    # The only non-reading file open is the exclusive creation of an owned delivery copy.
                    mode = node.args[0] if isinstance(node.func, ast.Attribute) and node.args else None
                    if called != "open" or not (isinstance(mode, ast.Constant) and mode.value == "xb"):
                        captures.add((file, scope))
        for child in ast.iter_child_nodes(node):
            visit(child, file, scope)

    for path in root.rglob("*.py"):
        visit(ast.parse(path.read_text(encoding="utf-8")), path.relative_to(root).as_posix())
    assert replacements == set(), replacements
    assert constructors == {("self_maintenance/usage.py", "", "host_models._HostCaller.__new__")}, constructors
    assert data_fields == {("sources/australian_legal_corpus/store.py", "Index.build", "complete")}
    assert captures == {("self_maintenance/usage.py", "_host_input")}, captures
    source = (root / "self_maintenance/usage.py").read_text(encoding="utf-8")
    capture = ast.unparse(next(node for node in ast.parse(source).body
                              if isinstance(node, ast.FunctionDef) and node.name == "_host_input"))
    assert "st_dev" in capture and "st_ino" in capture
    assert "by_file" in capture
    assert "os.fstat" in capture and "stream.read()" in capture
    hashes = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
              and ast.unparse(node.func) == "hashlib.sha256"]
    assert len(hashes) == 1 and ast.unparse(hashes[0].args[0]) == "encoded"
    for site in sorted(captures | constructors):
        print(site)


@pytest.mark.parametrize("kind", [host_models.CodexCaller, host_models.ClaudeCodeCaller])
@pytest.mark.parametrize("outcome", [None, LLMError("timeout"), TimeoutError("deadline"),
                                    ValueError("bad reply"), KeyboardInterrupt("stop"), SystemExit("end")])
def test_each_host_outcome_releases_the_record_and_private_copies(tmp_path, monkeypatch, kind, outcome):
    monkeypatch.setattr(usage, "_SENT", {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(kind, "locate", lambda self: "fake-host")

    @contextmanager
    def folders():
        work, files = tmp_path / "work", tmp_path / "files"
        work.mkdir()
        files.mkdir()
        yield work, files

    monkeypatch.setattr(host_models, "_folders", folders)

    def run(argv, stdin, cwd, files, *args):
        if outcome is not None:
            raise outcome
        (files / "last_message.txt").write_text("answer", encoding="utf-8")
        return 0, json.dumps({"type": "result", "result": "answer"}), ""

    monkeypatch.setattr(host_models, "_run", run)
    meter = _Requests()
    caller = kind()
    constructed = type(caller)
    if outcome is None:
        assert _Counted(caller, meter).complete("system", "user") == "answer"
    else:
        with pytest.raises(type(outcome), match=str(outcome)):
            _Counted(caller, meter).complete("system", "user")
    assert type(caller) is constructed
    assert not usage._SENT and not meter.in_flight
    assert not list(tmp_path.rglob(".g6-input-*"))


@pytest.mark.parametrize("failure", [OSError("copy failed"), BaseException("stop during copy")])
def test_a_stop_between_private_copy_writes_releases_every_owned_resource(tmp_path, monkeypatch, failure):
    from g6.metaharness.loop import _Call, _Gate

    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    meter = _Requests()
    call = _Call(object(), meter, meter.generation, lambda: True)
    meter.begin(call)
    real_open = Path.open

    def open_file(path, mode="r", *args, **kwargs):
        if mode == "xb" and path.name == "second":
            raise failure
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    before = set(usage._SENT)
    with usage.telling_stops(_Gate(meter, call)), pytest.raises(type(failure), match=str(failure)):
        usage._sending(object(), ["fake", str(first), str(second)], "user", tmp_path / "files")
    assert set(usage._SENT) == before and not meter.in_flight
    assert not list(tmp_path.rglob(".g6-input-*"))
    assert first.read_bytes() == b"first" and second.read_bytes() == b"second"
    meter.settle((call,), lambda: 0)


def test_cleanup_releases_every_gate_even_when_a_gate_raises(tmp_path):
    released = []

    class Gate:
        def sending(self, identify, sender):
            request = identify()
            return usage.Send(request.identity, True, None, 0, 0, request=request)

        def done(self, record):
            released.append(record)
            if len(released) == 1:
                raise BaseException("interrupted cleanup")

    before = set(usage._SENT)
    with usage.telling_stops(Gate()), pytest.raises(BaseException, match="interrupted cleanup"):
        with usage.WatchedClaude()._invocation():
            usage._sending(object(), ["fake"], "first", tmp_path / "first")
            usage._sending(object(), ["fake"], "second", tmp_path / "second")
    assert len(released) == 2
    assert set(usage._SENT) == before


def test_constructed_caller_kinds_do_not_outlive_their_callers():
    import gc
    import weakref

    class Fresh(host_models.ClaudeCodeCaller):
        pass

    caller = Fresh()
    original, constructed = weakref.ref(Fresh), weakref.ref(type(caller))
    del caller, Fresh
    gc.collect()
    assert constructed() is None and original() is None


@pytest.mark.parametrize("step", ["register", "track"])
@pytest.mark.parametrize("failure", [OSError("record interrupted"), BaseException("record stopped")])
def test_an_interruption_between_record_writes_releases_the_record(tmp_path, monkeypatch, step, failure):
    from g6.metaharness.loop import _Call, _Gate

    class Records(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            if step == "register":
                raise failure

    class Opened(list):
        def append(self, value):
            super().append(value)
            if step == "track":
                raise failure

    monkeypatch.setattr(usage, "_SENT", Records())
    meter = _Requests()
    call = _Call(object(), meter, meter.generation, lambda: True)
    meter.begin(call)
    token = usage._OPEN.set(Opened())
    try:
        with usage.telling_stops(_Gate(meter, call)), pytest.raises(type(failure), match=str(failure)):
            usage._sending(object(), ["fake"], "user", tmp_path / "files")
        assert not usage._SENT and not meter.in_flight
    finally:
        usage._OPEN.reset(token)
        meter.release(call.owner)
        meter.settle((call,), lambda: 0)


def test_a_file_changed_while_its_handle_is_read_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "input"
    path.write_bytes(b"before")
    real_open = Path.open

    class Reading:
        def __enter__(self):
            self.stream = real_open(path, "rb")
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self):
            data = self.stream.read()
            path.write_bytes(b"changed during read")
            return data

    monkeypatch.setattr(Path, "open", lambda where, mode="r", *args, **kwargs:
                        Reading() if where == path and mode == "rb" else real_open(where, mode, *args, **kwargs))
    with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
        usage.Request.host(["fake", str(path)], "user", tmp_path)


def test_a_caller_that_bypassed_construction_is_refused_without_changing_its_class():
    caller = object.__new__(host_models.ClaudeCodeCaller)
    with pytest.raises(LLMError, match="G6_E_LOOP_UNWATCHED_HOST"):
        usage.read_stops(caller)
    assert type(caller) is host_models.ClaudeCodeCaller


def test_linked_inputs_keep_the_existing_digest_for_each_logical_argument(tmp_path):
    import hashlib

    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"same captured bytes")
    os.link(first, second)
    content = hashlib.sha256(b"same captured bytes").hexdigest()
    encoded = json.dumps(["host", [["file", "first", content], ["file", "second", content]], "user"]).encode("ascii")
    request = usage.Request.host(["fake", str(first), str(second)], "user", tmp_path)
    assert request.identity == hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize("flag", ["--system-prompt-file", "--mcp-config", "--settings"])
@pytest.mark.parametrize("state", ["missing", "directory", "outside"])
def test_required_host_inputs_cannot_be_mistaken_for_output_paths(tmp_path, monkeypatch, flag, state):
    owned = tmp_path / "owned"
    owned.mkdir()
    path = (owned if state != "outside" else tmp_path) / "input"
    if state == "directory":
        path.mkdir()
    if state == "outside":
        path.write_bytes(b"outside the admitted folder")
        real_open = Path.open

        def open_file(where, *args, **kwargs):
            assert where != path, "an input outside the admitted folder was opened"
            return real_open(where, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
        usage.Request.host(["fake", flag, str(path)], "user", owned)


@pytest.mark.parametrize("flag", ["--system-prompt-file", "--mcp-config", "--settings"])
def test_an_input_flag_without_a_path_is_refused(tmp_path, flag):
    with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
        usage.Request.host(["fake", flag], "user", tmp_path)


def test_every_host_command_option_has_an_input_output_or_nonfile_role():
    import ast
    import inspect
    import re
    import textwrap

    from g6.harness.completion_loop import ToolSession

    roles = {
        "--system-prompt-file": "input", "--mcp-config": "input", "--settings": "input",
        "--output-last-message": "output", "-C": "directory", "--add-dir": "directory",
        "-p": "option", "-m": "option", "-c": "option", "-": "stdin",
        "--disable": "option", "--output-format": "option", "--tools": "option",
        "--disallowedTools": "option", "--strict-mcp-config": "option", "--setting-sources": "option",
        "--no-session-persistence": "option", "--max-turns": "option", "--model": "option",
        "--sandbox": "option", "--skip-git-repo-check": "option", "--ephemeral": "option",
        "--ignore-user-config": "option", "--color": "option", "--permission-mode": "option",
        "--max-budget-usd": "option", "--json": "option", "--listen": "account reading only",
    }
    sources = [Path(host_models.__file__).read_text(encoding="utf-8"),
               Path(usage.__file__).read_text(encoding="utf-8"), textwrap.dedent(inspect.getsource(ToolSession.run))]
    found = {node.value for source in sources for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and re.fullmatch(r"--[A-Za-z][A-Za-z0-9-]*|-[A-Za-z]|-", node.value)}
    assert found == set(roles), f"unclassified: {found - roles.keys()}; missing: {roles.keys() - found}"
    assert {flag for flag, role in roles.items() if role == "input"} == usage._INPUT_FLAGS
    for flag in sorted(roles):
        print(flag, roles[flag], sep="\t")
