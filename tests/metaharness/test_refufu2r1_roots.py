"""Data at the owned launch, and opened objects at every filesystem identity boundary."""
import ast
import inspect
import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import MethodType
from uuid import uuid4

import pytest

from g6.core import host_models
from g6.core.llm import LLMError
from g6.metaharness import loop
from g6.self_maintenance import usage


@pytest.fixture
def launch(monkeypatch, tmp_path):
    calls = []

    @contextmanager
    def folders():
        root = tmp_path / uuid4().hex
        work, files = root / "work", root / "files"
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    def run(argv, stdin, cwd, files, *rest):
        with usage._open_input(files) as stream:
            key = usage._file_identity(stream)
        record, _ = usage._SENT[key]
        assert record.charged
        expected, body = record.request.handoff()
        assert (list(argv), stdin) == (expected, body)
        captured = dict(record.request.files)
        for position, identity in record.request.aliases:
            assert Path(argv[position]).read_bytes() == captured[identity]
        calls.append((tuple(argv), stdin))
        if "exec" in argv:
            return 1, '{"type":"error","codex_error_info":"cyber_policy"}\n', ""
        return 1, json.dumps({"type": "result", "stop_reason": "refusal", "is_error": True}), ""

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_run", run)
    for cls in (host_models.ClaudeCodeCaller, host_models.CodexCaller):
        monkeypatch.setattr(cls, "locate", lambda self: "fake-host")
    return calls


@pytest.mark.parametrize("plant", ["dictionary", "object", "class", "object_class", "reply", "getattribute", "getattr",
                                    "descriptor", "metaclass"])
@pytest.mark.parametrize("base", [usage.WatchedClaude, usage.WatchedCodex])
def test_object_dispatch_cannot_authorize_a_second_launch(launch, plant, base):
    meter = loop._Requests()
    try:
        if plant in {"getattribute", "getattr"}:
            field = "__getattribute__" if plant == "getattribute" else "__getattr__"

            def lookup(self, name):
                if name == "complete":
                    return MethodType(host_models._HostCaller.complete, self)
                return object.__getattribute__(self, name)

            host = type("Lookup", (base,), {field: lookup})()
        elif plant == "descriptor":
            host = type("Descriptor", (base,), {"complete": property(
                lambda self: MethodType(host_models._HostCaller.complete, self))})()
        elif plant == "metaclass":
            class Meta(type):
                def __getattribute__(cls, name):
                    if name == "__mro__":
                        return (base, object)
                    return super().__getattribute__(name)
            host = Meta("MetaHost", (base,), {"complete": host_models._HostCaller.complete})()
        else:
            host = base()
            if plant == "dictionary":
                host.__dict__ = dict(vars(host), complete=MethodType(host_models._HostCaller.complete, host))
            elif plant == "object":
                object.__setattr__(host, "complete", MethodType(host_models._HostCaller.complete, host))
                object.__setattr__(host, "_observe_stops", False)
            elif plant == "reply":
                object.__setattr__(host, "_reply", lambda *args: "a hidden stop")
                object.__setattr__(host, "_observe_stops", False)
            elif plant == "object_class":
                object.__setattr__(host, "__class__", host_models.ClaudeCodeCaller)
            else:
                host.__class__ = host_models.ClaudeCodeCaller
        caller = loop._Counted(host, meter)
        for _ in range(2):
            try:
                caller.complete("fixed system", "fixed request")
            except LLMError:
                pass
    except LLMError:
        pass
    assert len(launch) <= 1
    assert not meter.in_flight and not usage._SENT


@pytest.mark.parametrize("constructed", [True, False])
def test_replacing_the_class_cannot_erase_a_constructed_hosts_owned_path(launch, monkeypatch, constructed):
    class ErasedHost:
        complete = host_models._HostCaller.complete
        _command = host_models.ClaudeCodeCaller._command
        argv = host_models.ClaudeCodeCaller.argv
        program = host_models.CLAUDE
        keep_env = ()

        def locate(self):
            return "fake-host"

        def _reply(self, *args):
            return "the refusal was hidden"

    def raw_run(argv, stdin, *args):
        launch.append(stdin)
        return 1, json.dumps({"type": "result", "is_error": True, "stop_reason": "refusal"}), ""

    monkeypatch.setattr(host_models, "_run", raw_run)
    if constructed:
        host = usage.WatchedClaude()
        object.__setattr__(host, "__class__", ErasedHost)
    else:
        host = ErasedHost()  # also challenge a borrowed native method without construction metadata
        host.model, host.timeout = "", 1.0
    meter = loop._Requests()
    for _ in range(2):
        try:
            loop._Counted(host, meter).complete("system", "request")
        except LLMError:
            pass
    assert len(launch) <= 1, "class replacement erased host ancestry and bypassed the owned send path"
    assert not meter.in_flight and not usage._SENT


def test_host_construction_identity_does_not_retain_the_caller():
    import gc
    import weakref
    host = usage.WatchedClaude()
    key, reference = id(host), weakref.ref(host)
    assert usage._HOSTS[key][0]() is host
    del host
    gc.collect()
    assert reference() is None and key not in usage._HOSTS


@pytest.mark.parametrize("base,option", [(usage.WatchedClaude, "--output-format"),
                                         (usage.WatchedClaude, "-p"), (usage.WatchedCodex, "--json"),
                                         (usage.WatchedCodex, "exec"),
                                         (usage.WatchedCodex, "--output-last-message")])
@pytest.mark.parametrize("change", ["remove", "duplicate", "reorder", "joined"])
def test_final_observation_options_are_checked_after_hooks(launch, base, option, change):
    class Hook(base):
        def _command(self, *args):
            argv, stdin = super()._command(*args)
            at = argv.index(option)
            width = 2 if option in {"--output-format", "--output-last-message"} else 1
            removed = argv[at:at + width]
            if change == "remove":
                del argv[at:at + width]
            elif change == "duplicate":
                argv[at:at] = removed
            elif change == "reorder":
                del argv[at:at + width]
                argv.extend(["--", *removed])
            else:
                argv[at:at + width] = [option + "=json"]
            return argv, stdin

    meter = loop._Requests()
    for _ in range(2):
        with pytest.raises(LLMError, match="G6_E_"):
            loop._Counted(Hook(), meter).complete("system", "request")
    assert launch == [], "an unobservable final command reached the launcher"
    assert not meter.in_flight and not usage._SENT


@pytest.mark.parametrize("printed", ["", "policy refusal", "{malformed", '{"type":"item.completed","item":{"text":"hi"}}',
                                     '{"type":"result","is_error":"false","result":"hi"}'])
@pytest.mark.parametrize("base", [usage.WatchedClaude, usage.WatchedCodex])
def test_unknown_reply_never_permits_identical_bytes_again(launch, monkeypatch, base, printed):
    def unknown(argv, stdin, cwd, files, *rest):
        launch.append(stdin)
        (files / "last_message.txt").write_text("unclassified", encoding="utf-8")
        return 0, printed, ""

    monkeypatch.setattr(host_models, "_run", unknown)
    meter = loop._Requests()
    for _ in range(2):
        try:
            loop._Counted(base(), meter).complete("system", "request")
        except LLMError:
            pass
    assert len(launch) == 1
    assert not meter.in_flight and not usage._SENT


def alias_name(root, kind, is_folder=False):
    path = root if is_folder else root / "long_input_name.json"
    if kind == "dot":
        return str(path.parent) + os.sep + "." + os.sep + path.name
    if kind == "forward-dot":
        return str(path.parent).replace("\\", "/") + "/./" + path.name
    if kind == "case":
        return str(path).swapcase() if os.name == "nt" else str(path)
    if kind == "trailing":
        return str(path) + os.sep
    if kind == "forward":
        return str(path).replace("\\", "/")
    if kind == "extended":
        return "\\\\?\\" + str(path) if os.name == "nt" else str(path)
    if kind == "short":
        if os.name != "nt":
            pytest.skip("Windows short names")
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer)):
            pytest.skip("short names unavailable")
        return buffer.value
    if kind in {"junction", "symlink", "hardlink"}:
        alias = root.parent / ("folder_alias" if is_folder else "input_alias")
        try:
            if kind == "junction":
                if os.name != "nt":
                    pytest.skip("Windows junction")
                import _winapi
                _winapi.CreateJunction(str(root), str(alias))
                return str(alias if is_folder else alias / path.name)
            if kind == "hardlink":
                if is_folder:
                    pytest.skip("directory hard links are not supported by the filesystem")
                alias = root / "hard_alias.json"
                os.link(path, alias)
            else:
                alias = root / "symbolic_alias" if not is_folder else alias
                alias.symlink_to(path, target_is_directory=is_folder)
        except OSError as error:
            pytest.skip(f"sandbox-limited {kind}: {error.errno}")
        return str(alias)
    return str(path)


FORMS = ["plain", "dot", "forward-dot", "case", "short", "trailing", "forward", "extended", "junction", "symlink", "hardlink"]


@pytest.mark.parametrize("kind", FORMS)
def test_input_identity_owns_capture_and_copy(tmp_path, kind):
    root = tmp_path / "owned"
    root.mkdir()
    path = root / "long_input_name.json"
    path.write_bytes(b"one input")
    candidate = alias_name(root, kind)
    request = None
    try:
        request = usage.Request.host(["fake", "--settings", str(path), "--mcp-config", candidate], "", root)
        argv, _ = request.handoff()
        assert len(request.files) == 1
        assert os.path.samefile(argv[2], argv[4])
        assert Path(argv[2]).read_bytes() == b"one input"
    except LLMError as error:
        assert str(error).startswith("G6_E_LOOP_INPUT_CHANGED:")
    finally:
        if request is not None:
            request.close()
        if kind == "junction":
            (root.parent / "input_alias").rmdir()


@pytest.mark.parametrize("kind", FORMS)
def test_folder_latch_uses_opened_identity(tmp_path, kind):
    root = tmp_path / "owned"
    root.mkdir()
    record = loop._Record()
    first = record.folder(root)
    first.cannot_record = "cannot keep refusal"
    candidate = alias_name(root, kind, is_folder=True)
    try:
        assert record.folder(candidate) is first
    except LLMError as error:
        assert str(error).startswith("G6_E_LOOP_INPUT_CHANGED:")
    finally:
        if kind == "junction":
            (root.parent / "folder_alias").rmdir()


def test_distinct_open_files_never_share_a_delivery_name(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    request = usage.Request.host(["fake", str(first), str(second)], "", tmp_path)
    try:
        argv, _ = request.handoff()
        assert not os.path.samefile(argv[1], argv[2])
        assert [Path(arg).read_bytes() for arg in argv[1:]] == [b"first", b"second"]
    finally:
        request.close()


def test_cleanup_does_not_delete_a_replacement_with_the_same_name(tmp_path):
    source = tmp_path / "input"
    source.write_bytes(b"captured")
    request = usage.Request.host(["fake", "--settings", str(source)], "", tmp_path)
    argv, _ = request.handoff()
    private = Path(argv[-1]).parent
    request._delivery.anchors.close()  # deterministic interleaving at cleanup's first step
    private.rename(tmp_path / "moved_copy")
    private.mkdir()
    kept = private / "keep"
    kept.write_bytes(b"not owned")
    with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
        request.close()
    assert kept.read_bytes() == b"not owned"


def test_an_output_cannot_alias_an_input_under_another_name(tmp_path):
    source, output = tmp_path / "input", tmp_path / "output"
    source.write_bytes(b"input")
    os.link(source, output)
    with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
        usage.Request.host(["fake", "--settings", str(source), "--output-last-message", str(output)], "", tmp_path)


def test_reply_folder_records_share_identity_across_spellings(tmp_path):
    files = tmp_path / "reply"
    files.mkdir()
    alternate = "\\\\?\\" + str(files) if os.name == "nt" else str(files / ".")
    record = None
    try:
        usage._sending(object(), ["fake"], "user", files)
        record, gate = usage._forget(alternate)
        assert record is not None
        usage._finish_record(record, gate)
    finally:
        leftover, gate = usage._forget(files)
        usage._finish_record(leftover, gate)
    assert not usage._SENT


def test_unknown_is_durable_before_a_new_meter_can_send(launch, tmp_path, monkeypatch):
    from g6.self_maintenance.envelope import note_refusal, refused_requests
    root = tmp_path / "state"
    root.mkdir()

    def keep(identity, said):
        assert note_refusal(root, identity, said)

    def unknown(argv, stdin, *rest):
        launch.append(stdin)
        return 0, "unclassified", ""

    monkeypatch.setattr(host_models, "_run", unknown)
    for _ in range(2):
        meter = loop._Requests(keeping=keep, on_record=lambda: refused_requests(root))
        with pytest.raises(LLMError):
            loop._Counted(usage.WatchedClaude(), meter).complete("system", "request")
        assert not meter.in_flight and not usage._SENT
    assert len(launch) == len(refused_requests(root)) == 1


def test_every_folder_overlap_comparison_uses_opened_objects(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    alias = "\\\\?\\" + str(source) if os.name == "nt" else str(source / ".")
    assert loop._overlap(str(source), alias)
    assert loop._overlap(str(source), alias + os.sep + "not_created")
    assert not loop._overlap(str(source / "one"), alias + os.sep + "two")


def test_future_folder_slots_conservatively_overlap_under_windows_case_aliases(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows future slot comparison")
    assert loop._overlap(str(tmp_path / "FUTURE"), str(tmp_path / "future" / "child"))


def test_install_identity_is_the_opened_folder_not_its_spelling(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(loop, "_SOURCE", source)
    first = loop.envelope_sha256()
    alias = Path("\\\\?\\" + str(source)) if os.name == "nt" else source / "."
    monkeypatch.setattr(loop, "_SOURCE", alias)
    assert loop.envelope_sha256() == first


@pytest.mark.parametrize("available", [True, False])
def test_windows_identity_uses_the_native_volume_and_full_file_id(tmp_path, monkeypatch, available):
    if os.name != "nt":
        pytest.skip("Windows handle information")
    import ctypes
    import struct
    from types import SimpleNamespace

    source = tmp_path / "input"
    source.write_bytes(b"data")
    with usage._open_input(source) as stream:
        def information(handle, kind, output, size):
            assert kind == 18 and size == 24
            ctypes.memmove(output, struct.pack("<Q16s", 0x123456789ABC, (2 ** 100 + 73).to_bytes(16, "little")), size)
            return available

        monkeypatch.setattr(usage, "_windows_files", lambda: SimpleNamespace(GetFileInformationByHandleEx=information))
        if available:
            assert usage._file_identity(stream) == (0x123456789ABC, 2 ** 100 + 73)
        else:
            with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
                usage._file_identity(stream)


@pytest.mark.parametrize("folder", [False, True])
def test_localhost_share_spelling_is_resolved_by_the_open_handle_without_network(tmp_path, monkeypatch, folder):
    if os.name != "nt":
        pytest.skip("Windows share spelling")
    source = tmp_path if folder else tmp_path / "input"
    if not folder:
        source.write_bytes(b"local fixture")
    spelling = "\\\\localhost\\" + source.drive[0] + "$" + str(source)[2:]
    original = usage._open_input

    def local_handle(path, **kwargs):
        # Do not connect to a share: return the very same local object's handle for this OS spelling.
        return original(source if str(path) == spelling else path, **kwargs)

    monkeypatch.setattr(usage, "_open_input", local_handle)
    monkeypatch.setattr(loop, "_open_input", local_handle, raising=False)
    realpath = os.path.realpath
    monkeypatch.setattr(os.path, "realpath", lambda path, *args, **kwargs:
                        realpath(source if str(path) == spelling else path, *args, **kwargs))
    if folder:
        record = loop._Record()
        assert record.folder(spelling) is record.folder(source)
    else:
        request = usage.Request.host(["fake", "--settings", spelling, "--mcp-config", str(source)], "", tmp_path)
        try:
            argv, _ = request.handoff()
            assert len(request.files) == 1 and os.path.samefile(argv[2], argv[4])
        finally:
            request.close()


def test_repeated_admission_of_one_launch_is_idempotent():
    meter = loop._Requests()
    request = usage.Request.text("system", "user")
    record = meter.take(identity=request)
    try:
        assert meter.admit(identity=request, record=record, places=1) is record
        assert meter.sent == 1
        assert list(meter.in_flight.values()) == [[record]]
        with pytest.raises(LLMError, match="G6_E_"):
            meter.admit(identity=usage.Request.text("system", "different"), record=record, places=1)
    finally:
        meter.release(record)


@pytest.mark.parametrize("failure", [TimeoutError("deadline"), LLMError("transport ended"),
                                     KeyboardInterrupt("stop"), SystemExit("end")])
def test_an_unread_reply_after_launch_never_permits_a_resend(launch, monkeypatch, failure):
    def no_reply(argv, stdin, *args):
        launch.append(stdin)
        raise failure

    monkeypatch.setattr(host_models, "_run", no_reply)
    meter = loop._Requests()
    caller = loop._Counted(usage.WatchedClaude(), meter)
    with pytest.raises(type(failure), match=str(failure)):
        caller.complete("system", "request")
    with pytest.raises(BaseException) as second:
        caller.complete("system", "request")
    assert isinstance(second.value, LLMError) and str(second.value).startswith("G6_E_")
    assert len(launch) == 1 and not meter.in_flight and not usage._SENT


def test_a_host_note_without_a_launch_record_never_authorizes_a_send(launch):
    with pytest.raises(LLMError, match="G6_E_LOOP_UNWATCHED_HOST"):
        usage.WatchedClaude().complete("system", "user")
    assert launch == [] and not usage._SENT


@pytest.mark.parametrize("death", [KeyboardInterrupt, SystemExit, ValueError])
def test_unknown_is_kept_before_record_release_even_when_interrupted(launch, monkeypatch, death):
    meter = loop._Requests()
    original = meter.release

    def release(record):
        original(record)
        raise death("after release")

    monkeypatch.setattr(meter, "release", release)
    monkeypatch.setattr(host_models, "_run", lambda *args: (0, "unknown", ""))
    try:
        with pytest.raises(death):
            loop._Counted(usage.WatchedClaude(), meter).complete("system", "user")
        assert len(meter.unknown) == 1 and not meter.refused
        assert not meter.in_flight and not usage._SENT
    finally:
        monkeypatch.setattr(meter, "release", original)


def test_source_inventory_removes_sender_notes_and_path_identity_authority():
    # The inventory is intentionally tied to the modules, so a new dispatch or path map needs classification here.
    complete = inspect.getsource(usage._complete_host)
    launch = inspect.getsource(usage.WatchedClaude._launch)
    counted = inspect.getsource(loop._Counted)
    for source in (complete, launch):
        assert "watched(" not in source
    assert "if not watched(sender)" not in counted
    capture = inspect.getsource(usage._host_input)
    assert "by_path" not in capture
    assert "os.path.realpath" not in inspect.getsource(loop._Record.folder)
    assert "_file_identity" in inspect.getsource(loop._Record.folder)
    assert "_file_identity" in inspect.getsource(usage._Delivery.directory)
    assert "_file_identity" in inspect.getsource(usage._forget)
    assert "_check_observation" in complete and "_observe_reply" in complete
    tree = ast.parse(Path(usage.__file__).read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    final_calls = {ast.unparse(node.func) for node in ast.walk(functions["_observe_reply"])
                   if isinstance(node, ast.Call)}
    assert not any(name.startswith("caller.") for name in final_calls)


def test_every_instance_read_on_the_owned_completion_finishes_before_admission():
    tree = ast.parse(inspect.getsource(usage._complete_host))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    body = functions["_complete_host"]
    reads = {ast.unparse(node) for node in ast.walk(body) if isinstance(node, ast.Attribute)
             and isinstance(node.value, ast.Name) and node.value.id == "caller"}
    assert reads == {"caller.locate", "caller._command", "caller.keep_env", "caller.timeout"}
    admission = next(node.lineno for node in ast.walk(body) if isinstance(node, ast.Call)
                     and ast.unparse(node.func) == "_sending")
    assert all(node.lineno < admission for node in ast.walk(body) if isinstance(node, ast.Attribute)
               and isinstance(node.value, ast.Name) and node.value.id == "caller")
    assert usage._OBSERVATION_OPTIONS == {"claude": ("-p", "--output-format"),
                                          "codex": ("exec", "--json", "--output-last-message")}


def test_all_path_comparisons_and_state_maps_have_an_identity_or_layout_role(tmp_path):
    names = {"Path", "abspath", "realpath", "resolve", "relpath", "normcase", "commonpath", "relative_to",
             "_inside", "_overlap", "_file_identity", "_output_identity", "_final_path", "_open_input"}
    inventory = {}
    for label, module in (("usage", usage), ("loop", loop)):
        sites = set()

        def visit(node, scope=""):
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                scope = scope + "." + node.name if scope else node.name
            if isinstance(node, ast.Call) and ast.unparse(node.func).rsplit(".", 1)[-1] in names:
                sites.add((scope, ast.unparse(node.func)))
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

        visit(ast.parse(Path(module.__file__).read_text(encoding="utf-8")))
        inventory[label] = sites
    expected = {
        "usage": {
            "Request.handoff": {"Path", "_file_identity", "_final_path", "_inside", "_open_input", "source.relative_to"},
            "Request.close": {"_file_identity", "_open_input"},
            "_Delivery.directory": {"_file_identity", "_final_path", "_inside", "_open_input"},
            "_check_observation": {"Path", "_output_identity"},
            "_final_path": {"os.path.normcase"},
            "_forget": {"Path", "_file_identity", "_open_input"},
            "_host_input": {"Path", "_file_identity", "_final_path", "_inside", "_open_input", "os.path.abspath",
                            "os.path.normcase", "os.path.relpath"},
            "_inside": {"os.path.commonpath"},
            "_output_identity": {"_file_identity", "_open_input"},
            "_output_place": {"_final_path", "_inside", "_open_input"},
            "_sending": {"Path", "_file_identity", "_open_input"},
        },
        "loop": {
            # G6's own package, and (the freeze follow-up) the installation's prefixes, as places to read and pin
            "": {"Path", "Path(g6.__file__).resolve", "Path(place).resolve"},
            "ShadowLoop.__init__": {"Path", "Path(root).resolve"},
            "_Record.folder": {"Path", "_file_identity", "_open_input"},
            "_folder_place": {"Path", "_file_identity", "_final_path", "_open_input", "os.path.normcase"},
            "_overlap": set(),
            "_importable_caches": {"path.relative_to"},
            # The freeze follow-up's reading of what judges: files to read and digest, and a link's target to compare;
            # whether two folders are one is decided by _same_place, through _overlap (identity, _folder_place) and
            # _within (os.path.samestat), never by a spelling.
            "_importable": {"os.path.normcase"},
            "_in_reach": {"Path", "os.path.realpath"},
            "_same_place": {"_overlap", "os.path.normcase"},
            "_code_file": {"Path", "Path(sys.base_prefix).resolve", "Path(where).resolve", "source.resolve"},
            "_imported_files": {"Path", "Path(where).resolve"},
            "_interpreter_caches": {"Path", "os.path.normcase"},
            "_links_under": {"Path"},
            "_listing": {"Path", "os.path.normcase", "path.relative_to"},
            "_loaded_outside": {"os.path.abspath", "os.path.normcase"},
            "_pinned": {"os.path.normcase"},
            "_roots": {"Path", "Path(root).resolve"},
            "_unpinned": {"Path"},
            # the folder's lock, keyed by the folder a handle was made with (resolved once, as ShadowLoop.__init__ does)
            "folder_lock": {"Path", "Path(root).resolve"},
            "envelope_sha256": {"_file_identity", "_open_input"},
        },
    }
    assert inventory == {label: {(scope, call) for scope, calls in scopes.items() for call in calls}
                         for label, scopes in expected.items()}
    # Locations and argv positions are layout syntax. Every physical capture, role, copy and held directory uses file IDs.
    source = tmp_path / "input"
    source.write_bytes(b"one object")
    request = usage.Request.host(["fake", "--settings", str(source), "--mcp-config", str(source)], "", tmp_path)
    try:
        info = source.stat()
        identity = (info.st_dev, info.st_ino)
        assert request.files == ((identity, b"one object"),)
        assert request.aliases == ((2, identity), (4, identity))
        assert {index for index, _ in request.locations} == {2, 4}
        request.handoff()
        assert all(isinstance(key, tuple) and len(key) == 2 for key in request._delivery.directories)
        assert all(isinstance(key, tuple) and len(key) == 2 for _, key in request._delivery.folders)
    finally:
        request.close()
    # These keys name import addresses in a frozen layout, rather than filesystem objects or process latches.
    assert "path.relative_to(root).as_posix()" in inspect.getsource(loop._importable_caches)
    assert "_file_identity" in inspect.getsource(loop.envelope_sha256)
