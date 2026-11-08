"""Handle containment, final admission, and unconditional send release inventories."""
import ast
import inspect
import os
import stat
from pathlib import Path
from uuid import uuid4

import pytest

from g6.core.llm import FakeLLM, LLMError
from g6.metaharness.loop import _Counted, _Requests
from g6.self_maintenance import usage

FINAL_METHODS = {"complete", "run", "_invocation", "_launch", "_reply", "__getattribute__", "__getattr__",
                 "__setattr__", "_observe_stops", "__class__", "__init_subclass__"}


@pytest.mark.parametrize("kind", ["junction", "symlink", "hardlink", "dotdot", "case", "short", "extended"])
def test_input_membership_comes_from_the_open_file(tmp_path, kind):
    root, outside = tmp_path / "owned", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    source = outside / "input"
    source.write_bytes(b"outside")
    inside = root / "input"
    inside.write_bytes(b"inside")
    allowed = False
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction case")
        import _winapi
        _winapi.CreateJunction(str(outside), str(root / "alias"))
        candidate = root / "alias" / "input"
    elif kind == "symlink":
        try:
            (root / "alias").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"sandbox-limited symbolic link: {exc.winerror if os.name == 'nt' else exc.errno}")
        candidate = root / "alias" / "input"
    elif kind == "hardlink":
        candidate = root / "hardlink"
        os.link(source, candidate)
        allowed = True  # this file has an admitted directory entry; its other names confer no authority
    elif kind == "dotdot":
        candidate = root / ".." / "outside" / "input"
    elif kind == "case":
        if os.name != "nt":
            pytest.skip("case insensitive filesystem case")
        candidate = Path(str(inside).swapcase())
        allowed = True
    elif kind == "short":
        if os.name != "nt":
            pytest.skip("Windows short name case")
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(inside), buf, len(buf)):
            pytest.skip("short names unavailable")
        candidate = Path(buf.value)
        allowed = True
    else:
        if os.name != "nt":
            pytest.skip("Windows UNC spelling case")
        # Extended DOS names exercise the same handle path normalization as extended UNC names.
        candidate = Path("\\\\?\\" + str(inside))
        allowed = True
    try:
        if allowed:
            request = usage.Request.host(["fake", "--settings", str(candidate)], "", root)
            try:
                argv, _ = request.handoff()
                assert Path(argv[-1]).read_bytes() == (b"outside" if kind == "hardlink" else b"inside")
                assert Path(argv[-1]).resolve().is_relative_to(root.resolve())
            finally:
                request.close()
        else:
            with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
                usage.Request.host(["fake", "--settings", str(candidate)], "", root)
    finally:
        if kind == "junction":
            (root / "alias").rmdir()


def test_private_copies_do_not_follow_a_replaced_input_parent(tmp_path):
    root, outside = tmp_path / "owned", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    parent = root / "files"
    parent.mkdir()
    source = parent / "input"
    source.write_bytes(b"admitted")
    request = usage.Request.host(["fake", "--settings", str(source)], "", root)
    parent.rename(root / "moved")
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(parent))
        else:
            parent.symlink_to(outside, target_is_directory=True)
        try:
            argv, _ = request.handoff()
            assert Path(argv[-1]).read_bytes() == b"admitted"
            assert Path(argv[-1]).resolve().is_relative_to(root.resolve())
            assert not list(outside.iterdir()), "copy creation followed an input parent's new target"
        finally:
            request.close()
    finally:
        parent.rmdir() if os.name == "nt" else parent.unlink()


@pytest.mark.parametrize("death", [KeyboardInterrupt, SystemExit, ValueError])
@pytest.mark.parametrize("step", ["settle", "recheck", "noted"])
def test_release_survives_every_call_finish_step(monkeypatch, step, death):
    meter = _Requests()
    original = getattr(meter, step)

    def interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        if step != "noted" or meter.in_flight:
            raise death("after " + step)
        return result

    monkeypatch.setattr(meter, step, interrupt)
    try:
        with pytest.raises(death):
            _Counted(FakeLLM(default="answer"), meter).complete("system", uuid4().hex)
        assert not meter.in_flight, "a finish step left a send acquired"
    finally:
        for records in list(meter.in_flight.values()):
            for record in records:
                meter.release(record)


def test_send_path_inventory_has_no_unclassified_dispatch_or_acquisition():
    source = Path(usage.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    capture = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_host_input")
    assert "_final_path" in ast.unparse(capture)
    assert "_inside" in ast.unparse(capture)
    assert "_open_input" in ast.unparse(capture)
    complete = inspect.getsource(usage._Watched.complete)
    assert "_complete_host" in complete
    owned = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_complete_host")
    calls = [ast.unparse(n.func) for n in ast.walk(owned) if isinstance(n, ast.Call)]
    assert {c for c in calls if c.startswith("caller.")} == {"caller.locate", "caller._command"}
    assert "host_models._run" in calls and "_sending" in calls
    assert "_check_seam" in inspect.getsource(usage._new_host)
    from g6.metaharness import loop
    counted = ast.parse(inspect.getsource(loop._Counted))
    finish = next(n for n in ast.walk(counted) if isinstance(n, ast.FunctionDef) and n.name == "finish")
    assert any(isinstance(n, ast.Try) and any("release" in ast.unparse(s) for s in n.finalbody)
               for n in ast.walk(finish))
    # Every acquisition entry point in the two send modules must be classified here.
    acquired = set()
    for module in (usage, loop):
        def visit(node, scope=""):
            if isinstance(node, ast.FunctionDef | ast.ClassDef):
                scope = scope + "." + node.name if scope else node.name
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"take", "admit", "begin", "sending", "acquire"}:
                    acquired.add((module.__name__, scope, node.func.attr))
            for child in ast.iter_child_nodes(node):
                visit(child, scope)
        visit(ast.parse(Path(module.__file__).read_text(encoding="utf-8")))
    assert acquired == {
        (usage.__name__, "_sending", "sending"),
        (loop.__name__, "_Requests.take", "admit"),
        (loop.__name__, "_Requests.begin", "admit"),
        (loop.__name__, "_Gate.sending", "admit"),
        (loop.__name__, "_Resends.append", "take"),
        (loop.__name__, "_call_scope", "begin"),
        (loop.__name__, "_Counted.complete.next_request", "take"),
        (loop.__name__, "_Counted.complete.resend", "take"),
    }, acquired
    print("Acquisitions:", sorted(acquired))


@pytest.mark.parametrize("method", sorted(FINAL_METHODS))
def test_each_final_host_seam_rejects_subclass_replacement(method):
    with pytest.raises(LLMError, match="G6_E_LOOP_UNWATCHED_HOST"):
        type("Override", (usage.WatchedClaude,), {method: lambda *a, **k: None})()


def test_construction_rechecks_a_class_changed_after_creation():
    class Changed(usage.WatchedClaude):
        pass
    Changed.complete = lambda *args, **kwargs: "bypassed"
    with pytest.raises(LLMError, match="G6_E_LOOP_UNWATCHED_HOST"):
        Changed()


@pytest.mark.parametrize("method", ["complete", "_launch", "_reply", "_observe_stops", "__class__"])
def test_an_instance_cannot_replace_a_final_method(method):
    with pytest.raises(LLMError, match="G6_E_LOOP_UNWATCHED_HOST"):
        setattr(usage.WatchedClaude(), method, lambda *args: None)


@pytest.mark.parametrize("exists", [True, False])
def test_output_parent_is_judged_by_its_handle(tmp_path, exists):
    root, outside = tmp_path / "owned", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    alias = root / "alias"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(alias))
    else:
        alias.symlink_to(outside, target_is_directory=True)
    if exists:
        (outside / "out").write_bytes(b"outside")
    try:
        with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
            usage.Request.host(["fake", "--output-last-message", str(alias / "out")], "", root)
    finally:
        alias.rmdir() if os.name == "nt" else alias.unlink()


def test_an_unresolvable_handle_is_refused_before_reading(tmp_path, monkeypatch):
    path = tmp_path / "input"
    path.write_bytes(b"not read")
    original = usage._final_path

    def unresolved(stream):
        if not stat.S_ISDIR(os.fstat(stream.fileno()).st_mode):
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: unresolved handle")
        return original(stream)

    monkeypatch.setattr(usage, "_final_path", unresolved)
    monkeypatch.setattr(usage._InputHandle, "read", lambda *args: pytest.fail("read unresolved input"))
    with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
        usage.Request.host(["fake", "--settings", str(path)], "", tmp_path)


@pytest.mark.parametrize("death", [KeyboardInterrupt, SystemExit, ValueError])
@pytest.mark.parametrize("step", ["begin", "registration", "book", "context"])
def test_every_record_registration_step_releases_on_interruption(monkeypatch, death, step):
    from g6.metaharness import loop
    meter = _Requests()
    original_begin = meter.begin

    class Entries(list):
        def append(self, record):
            super().append(record)
            raise death("registration stopped")

    class Records(dict):
        def setdefault(self, key, default):
            if key not in self:
                self[key] = Entries()
            return self[key]

    class Context:
        def get(self):
            return original_context.get()

        def set(self, value):
            result = original_context.set(value)
            if value:
                raise death("context stopped")
            return result

        def reset(self, token):
            return original_context.reset(token)

    def begin(call):
        original_begin(call)
        if step == "begin":
            raise death("book opened")
        if step == "book":
            loop._RECORD.books[call.owner].sends = Entries()

    monkeypatch.setattr(meter, "begin", begin)
    if step == "registration":
        monkeypatch.setattr(loop._RECORD, "out", Records())
    original_context = loop._AROUND
    if step == "context":
        monkeypatch.setattr(loop, "_AROUND", Context())
    before = set(loop._RECORD.books)
    with pytest.raises(death):
        _Counted(FakeLLM(default="answer"), meter).complete("system", uuid4().hex)
    assert not meter.in_flight
    assert set(loop._RECORD.books) == before


def test_every_path_decision_uses_handle_containment_or_an_owned_destination():
    sites = set()
    names = {"Path", "abspath", "realpath", "resolve", "relpath", "_open_input", "_final_path", "_inside",
             "_output_place", "_create_copy", "mkdir", "link", "open", "directory", "_host_input",
             "CreateFileW", "GetFinalPathNameByHandleW", "readlink", "rmtree"}

    def walk(node, scope=""):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            scope = scope + "." + node.name if scope else node.name
        if isinstance(node, ast.Call) and not scope.startswith("read_limits"):
            name = ast.unparse(node.func)
            if name.rsplit(".", 1)[-1] in names:
                sites.add((scope, name))
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    walk(ast.parse(Path(usage.__file__).read_text(encoding="utf-8")))
    classified = {
        "Request.handoff": {"Path", "_create_copy", "_final_path", "_inside", "_open_input", "folder.mkdir", "os.link",
                            "os.mkdir", "self._delivery.directory"},
        "Request.host": {"_host_input"}, "request_identity": {"_host_input"},
        "Request.close": {"shutil.rmtree"},
        "_Delivery.directory": {"_final_path", "_open_input", "os.mkdir", "path.mkdir"},
        "_create_copy": {"os.open", "path.open"},
        "_host_input": {"Path", "_final_path", "_inside", "_open_input", "_output_place", "os.path.abspath",
                        "os.path.relpath"},
        "_open_input": {"os.open", "api.CreateFileW"},
        "_final_path": {"_windows_files().GetFinalPathNameByHandleW", "os.readlink"},
        "_output_place": {"_final_path", "_inside", "_open_input"},
        "_sending": {"Path"},
    }
    expected = {(scope, call) for scope, calls in classified.items() for call in calls}
    assert sites == expected, f"unclassified: {sites - expected}; missing: {expected - sites}"
    assert usage._FINAL_HOST_METHODS == FINAL_METHODS
    for site in sorted(sites):
        print(*site, sep="\t")


@pytest.mark.parametrize("base", [usage.WatchedClaude, usage.WatchedCodex])
@pytest.mark.parametrize("method", ["_command", "argv", "locate"])
def test_each_customizable_host_hook_finishes_before_admission(tmp_path, monkeypatch, base, method):
    from contextlib import contextmanager
    import json
    from g6.core import host_models

    admitted, hooks = [], []
    meter = _Requests()
    original = meter.admit

    def admit(*args, **kwargs):
        if kwargs.get("identify") is not None:
            admitted.append(True)
        return original(*args, **kwargs)

    parent = getattr(base, method)

    def hook(self, *args, **kwargs):
        assert not admitted, "a subclass hook ran after admission"
        hooks.append(method)
        return "fake-host" if method == "locate" else parent(self, *args, **kwargs)

    kind = type("Hook", (base,), {method: hook})
    if method != "locate":
        monkeypatch.setattr(kind, "locate", lambda self: "fake-host")

    @contextmanager
    def folders():
        work, files = tmp_path / "work", tmp_path / "files"
        work.mkdir()
        files.mkdir()
        yield work, files

    def run(argv, stdin, cwd, files, *args):
        assert admitted and hooks == [method]
        (files / "last_message.txt").write_text("answer", encoding="utf-8")
        return 0, json.dumps({"type": "result", "result": "answer"}), ""

    monkeypatch.setattr(meter, "admit", admit)
    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_run", run)
    assert _Counted(kind(), meter).complete("system", "user") == "answer"
    assert not meter.in_flight and not usage._SENT


@pytest.mark.parametrize("form", ["\\\\?\\UNC\\server\\share\\owned\\input", "\\\\?\\C:\\owned\\input"])
def test_native_final_names_normalize_unc_and_extended_forms_without_network(tmp_path, monkeypatch, form):
    if os.name != "nt":
        pytest.skip("Windows handle names")
    from types import SimpleNamespace

    path = tmp_path / "input"
    path.write_bytes(b"local only")

    def final(handle, buffer, size, flags):
        buffer.value = form
        return len(form)

    with usage._open_input(path) as stream:
        monkeypatch.setattr(usage, "_windows_files", lambda: SimpleNamespace(GetFinalPathNameByHandleW=final))
        name = usage._final_path(stream)
    expected = "\\\\server\\share\\owned\\input" if "UNC" in form else "C:\\owned\\input"
    assert name == os.path.normcase(expected)
    assert usage._inside(name, os.path.normcase(str(Path(expected).parent)))
    assert not usage._inside(name, os.path.normcase("\\\\other\\share\\owned"))


def test_repeated_input_arguments_open_once_and_share_one_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "input"
    path.write_bytes(b"snapshot")
    original, opened = usage._open_input, []

    def opening(where, **kwargs):
        if where == path:
            opened.append(where)
        return original(where, **kwargs)

    monkeypatch.setattr(usage, "_open_input", opening)
    request = usage.Request.host(["fake", "--settings", str(path), "--mcp-config", str(path)], "", tmp_path)
    assert opened == [path]
    assert len(request.files) == 1


def test_an_admitted_directory_alias_keeps_its_existing_refusal_identity(tmp_path):
    import hashlib
    import json
    inside, alias = tmp_path / "inside", tmp_path / "alias"
    inside.mkdir()
    (inside / "input").write_bytes(b"admitted bytes")
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(inside), str(alias))
    else:
        alias.symlink_to(inside, target_is_directory=True)
    try:
        request = usage.Request.host(["fake", "--settings", str(alias / "input")], "", tmp_path)
        parts = [["arg", "--settings"], ["file", "alias/input", hashlib.sha256(b"admitted bytes").hexdigest()]]
        before = hashlib.sha256(json.dumps(["host", parts, ""]).encode("ascii")).hexdigest()
        assert request.identity == before, "handle containment changed an already-admitted alias's refusal identity"
    finally:
        alias.rmdir() if os.name == "nt" else alias.unlink()


@pytest.mark.parametrize("flag", ["--settings", "--mcp-config", "--system-prompt-file", "--output-last-message"])
def test_a_file_role_cannot_hide_its_path_in_an_unsupported_option_form(tmp_path, flag):
    outside = tmp_path / "outside"
    outside.write_bytes(b"not admitted")
    root = tmp_path / "owned"
    root.mkdir()
    with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
        usage.Request.host(["fake", flag + "=" + str(outside)], "", root)
