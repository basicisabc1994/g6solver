# SPDX-License-Identifier: Apache-2.0
"""Send snapshots and exception ownership, with the second review's cases kept intact."""
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from g6.core import host_models
from g6.core.consent_callers import CallerWrapper
from g6.core.llm import FakeLLM, LLMError
from g6.core.model_calls import note_model_call
from g6.metaharness.loop import _Counted, _Requests
from g6.self_maintenance import usage
from g6.self_maintenance.usage import MODEL_REFUSED, request_digest


def test_rethrowing_the_original_error_keeps_the_identity_of_its_send():
    """Review refu2 finding 1, closed in the 0.3.0 issuance window: ``core/model_calls`` records the boundary of each send
    as it is noted, and the error is read back to its own send by that record."""
    sent, transport_errors, observed = [], [], []

    # This transport names its input as one pair; the sender's frame names
    # system/user, just as the admission reader supports.
    def transport(pair):
        note_model_call()
        sent.append(pair)
        if pair[1] == "refused request":
            error = LLMError(f"{MODEL_REFUSED}: policy")
            transport_errors.append(error)
            raise error
        return "answer"

    # a wrapper by the published contract (``CallerWrapper``) over a local fake, as this file's other batch is: a subclass
    # of ``FakeLLM`` that replaces its completion is of no kind the sharing check knows, and is refused before it sends
    class Batch(CallerWrapper):
        def complete(self, system, user, *, max_tokens=1024):
            errors = []
            for user in ("refused request", "successful request"):
                try:
                    transport((system, user))
                except LLMError as exc:
                    errors.append(exc)
            # Preserve the actual transport exception and its traceback.
            if errors:
                raise errors[0]
            return "answer"

    meter = _Requests()
    counted = _Counted(Batch(FakeLLM()), meter)
    for _ in range(2):
        try:
            counted.complete("system", "outer request")
        except LLMError as error:
            observed.append(error)

    assert observed[0] is transport_errors[0], "the original refusal must reach the loop unchanged"
    assert sent.count(("system", "refused request")) == 1, "the original refused send left again"
    assert meter.refused == {request_digest("system", "refused request")}

@pytest.mark.usefixtures("consented_provider_fakes")
def test_a_host_file_changed_during_the_stop_read_is_the_request_recorded(monkeypatch, tmp_path):
    pending, sent, worker_errors = [], [], []
    change_now, changed = threading.Event(), threading.Event()
    original_command = host_models.ClaudeCodeCaller._command

    @contextmanager
    def folders():
        root = tmp_path / uuid4().hex
        work, files = root / "work", root / "files"
        work.mkdir(parents=True)
        (files / "tmp").mkdir(parents=True)
        yield work, files

    def command(self, *args):
        argv, stdin = original_command(self, *args)
        pending.append(args[-1] / "system_prompt.md")
        return argv, stdin

    def halted():
        # The first call-entry check has no input file yet. At the send's
        # admission the identity has already been read, before this stop read.
        if pending and not changed.is_set():
            change_now.set()
            assert changed.wait(5)
        return None

    def writer():
        try:
            assert change_now.wait(5)
            pending[0].write_text("changed system", encoding="utf-8")
        except BaseException as exc:
            worker_errors.append(exc)
        finally:
            changed.set()

    def run(argv, stdin, cwd, files, *rest):
        sent.append(usage.request_identity(argv, stdin, files.parent))
        return 1, json.dumps({"type": "result", "is_error": True, "stop_reason": "refusal", "result": ""}), ""

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models.ClaudeCodeCaller, "locate", lambda self: "fake-claude")
    monkeypatch.setattr(host_models.ClaudeCodeCaller, "_command", command)
    monkeypatch.setattr(host_models, "_run", run)
    meter = _Requests(halted=halted)
    caller = _Counted(host_models.ClaudeCodeCaller(), meter)
    worker = threading.Thread(target=writer)
    worker.start()
    try:
        for system in ("original system", "changed system"):
            try:
                caller.complete(system, "same user")
            except LLMError:
                pass
    finally:
        change_now.set()
        worker.join(10)
    assert not worker.is_alive()
    assert not worker_errors
    assert len(set(sent)) == 1, "the fake program must have received identical requests"
    assert len(sent) == 1, "the same refused host bytes were sent a second time"
    assert meter.refused == set(sent)


@pytest.mark.parametrize("depth", [0, 1, 2])
def test_a_metered_sender_raises_its_record_even_after_the_traceback_is_removed(depth):
    meter = _Requests()
    error = LLMError(f"{MODEL_REFUSED}: policy")

    def refuses(system, user):
        raise error

    caller = FakeLLM(refuses)
    for _ in range(depth + 1):
        caller = _Counted(caller, meter)
    with pytest.raises(usage.ProviderStop) as raised:
        caller.complete("system", "refused request")
    raised.value.__traceback__ = None
    record = raised.value.record
    assert isinstance(record, usage.Send)
    assert record.request.system == "system"
    assert record.request.user == "refused request"
    assert record.identity == request_digest("system", "refused request")
    assert raised.value.__cause__ is error
    with pytest.raises(LLMError, match="G6_E_"):
        caller.complete("system", "refused request")
    assert len(caller.inner.calls if depth == 0 else []) == (1 if depth == 0 else 0)


def test_a_batch_rethrowing_an_owned_transport_error_cannot_bar_its_successful_send():
    sent, meter = [], _Requests()

    def respond(system, user):
        sent.append((system, user))
        if user == "refused request":
            raise LLMError(f"{MODEL_REFUSED}: policy")
        return "answer"

    transport = _Counted(FakeLLM(respond), meter)

    # a wrapper by the published contract (``CallerWrapper``), so the sharing check knows it sends only through its
    # transport, a local fake; a class of no known kind is a recipient no grant covers
    class Batch(CallerWrapper):
        def complete(self, system, user, *, max_tokens=1024):
            errors = []
            for user in ("refused request", "successful request"):
                try:
                    self.inner.complete(system, user)
                except LLMError as error:
                    errors.append(error)
            errors[0].__traceback__ = None
            raise errors[0]

    caller = _Counted(Batch(transport), meter)
    for _ in range(2):
        with pytest.raises(LLMError):
            caller.complete("system", "outer request")
    assert sent.count(("system", "refused request")) == 1
    assert sent.count(("system", "successful request")) == 2
    assert meter.refused == {request_digest("system", "refused request")}


def test_a_text_snapshot_cannot_be_changed_and_is_the_pair_passed_to_the_sender():
    from dataclasses import FrozenInstanceError

    request = usage.Request.text("system", "user")
    with pytest.raises(FrozenInstanceError):
        request.user = "changed"
    assert request.identity == request_digest(request.system, request.user)


def test_a_snapshot_is_taken_after_the_stop_read_and_rechecked_before_admission(monkeypatch):
    meter, order = _Requests(), []
    monkeypatch.setattr(meter, "halted", lambda: order.append("stop"))

    def identify():
        order.append("snapshot")
        return usage.Request.text("system", "user")

    record = meter.take(identify=identify)
    try:
        assert order == ["stop", "snapshot", "stop"]
        assert record.request.system == "system"
    finally:
        meter.release(record)


def test_a_wait_discards_the_candidate_and_admits_one_final_snapshot(monkeypatch):
    meter, current, candidates = _Requests(), ["first"], []

    def identify():
        request = usage.Request.text("system", current[0])
        candidates.append(request)
        return request

    monkeypatch.setattr(meter, "_out_elsewhere", lambda *args: len(candidates) == 1)
    monkeypatch.setattr(meter._settled, "wait", lambda *args: current.__setitem__(0, "after wait"))
    record = meter.take(identify=identify)
    try:
        assert len(candidates) == 2
        assert record.request is candidates[1]
        assert record.identity == request_digest("system", "after wait")
        assert meter.sent == 1
    finally:
        meter.release(record)


@pytest.mark.parametrize("kind", [host_models.ClaudeCodeCaller, host_models.CodexCaller])
@pytest.mark.usefixtures("consented_provider_fakes")
def test_each_owned_host_raises_the_snapshot_record_without_reading_a_traceback(kind, tmp_path, monkeypatch):
    @contextmanager
    def folders():
        root = tmp_path / uuid4().hex
        work, files = root / "work", root / "files"
        work.mkdir(parents=True)
        (files / "tmp").mkdir(parents=True)
        yield work, files

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(kind, "locate", lambda self: "fake-host")
    sent = []

    def run(argv, stdin, cwd, files, *rest):
        sent.append(usage.request_identity(argv, stdin, files.parent))
        result = ({"type": "result", "is_error": True, "stop_reason": "refusal"}
                  if kind is host_models.ClaudeCodeCaller else
                  {"type": "turn.failed", "error": {"codex_error_info": "cyber_policy"}})
        return 1, json.dumps(result), ""

    monkeypatch.setattr(host_models, "_run", run)
    meter = _Requests()
    caller = _Counted(kind(), meter)
    with pytest.raises(usage.ProviderStop) as failed:
        caller.complete("system", "user")
    failed.value.__traceback__ = None
    assert failed.value.record.request.identity == sent[0]
    assert failed.value.record.request.stdin
    assert meter.refused == set(sent)
    with pytest.raises(LLMError, match="G6_E_"):
        caller.complete("system", "user")
    assert len(sent) == 1


def test_a_host_handoff_uses_the_snapshot_without_opening_the_input_again(tmp_path):
    path = tmp_path / "system_prompt.md"
    path.write_bytes(b"admitted system")
    argv = ["fake", str(path)]
    request = usage.Request.host(argv, "admitted user", tmp_path)
    path.write_bytes(b"changed later")
    argv.append("changed argument")
    frozen_argv, stdin = request.handoff()
    assert frozen_argv[0] == "fake" and frozen_argv[1] != str(path)
    assert stdin == "admitted user"
    assert path.read_bytes() == b"changed later"
    assert Path(frozen_argv[1]).read_bytes() == b"admitted system"
    assert usage.request_identity(frozen_argv, stdin, tmp_path) == request.identity
    request.close()


def test_an_unreadable_host_input_is_not_identified_as_an_absent_output(tmp_path, monkeypatch):
    path = tmp_path / "system_prompt.md"
    path.write_bytes(b"system")
    real_open = usage._open_input

    def read(where, *args, **kwargs):
        if where == path:
            raise PermissionError("input cannot be read")
        return real_open(where, *args, **kwargs)

    monkeypatch.setattr(usage, "_open_input", read)
    with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
        usage.Request.host(["fake", str(path)], "user", tmp_path)


@pytest.mark.parametrize("failure", [OSError("handoff failed"), BaseException("process ended")])
def test_a_failed_or_interrupted_handoff_releases_its_record_without_a_send(tmp_path, monkeypatch, failure):
    from g6.metaharness.loop import _Call, _Gate

    meter = _Requests()
    call = _Call(object(), meter, meter.generation, lambda: True)
    meter.begin(call)
    path = tmp_path / "files"
    path.mkdir()
    before = set(usage._SENT)

    def handoff(self):
        raise failure

    monkeypatch.setattr(usage.Request, "handoff", handoff)
    with usage.telling_stops(_Gate(meter, call)), pytest.raises(type(failure), match=str(failure)):
        usage._sending(object(), ["fake"], "user", path)
    assert set(usage._SENT) == before
    assert meter.in_flight == {}
    assert meter.sent == 1  # The place is held conservatively even though the launcher was never reached.
    meter.settle((call,), lambda: 0)


def test_every_send_identity_producer_and_reader_is_classified_from_the_code():
    """The inventory includes the bound seams and labels the remaining frame fallback explicitly. A new site fails."""
    import ast
    from pathlib import Path

    import g6

    # Every identity-making API and every reader of a record's identity, across the product source.
    classified = {
        ("metaharness/loop.py", "_request_digest", "request_digest"): "compatibility digest API",
        ("metaharness/loop.py", "_named", "Request.text"): "external sender's admission snapshot",
        ("metaharness/loop.py", "_sender", "_named"): "bound note API fallback at admission",
        ("metaharness/loop.py", "_Requests.admit", ".identity"): "admission reads the snapshot",
        ("metaharness/loop.py", "_Requests.admit", ".request"): "interrupted admission closes its snapshot",
        ("metaharness/loop.py", "_Requests.admit", "Send"): "one admitted record",
        ("metaharness/loop.py", "_Requests.at_a_refusal", ".identity"): "refusal reads its record",
        ("metaharness/loop.py", "_raised_by", "_named"): "external multi-send traceback gap: issuance required",
        ("metaharness/loop.py", "_raised_by", ".identity"): "external multi-send traceback gap: issuance required",
        ("metaharness/loop.py", "_Counted.complete.next_request", "_sender"): "external note admission",
        ("metaharness/loop.py", "_Counted.complete.resend", "_sender"): "bound Retry-After seam",
        ("metaharness/loop.py", "_Counted.complete", "_raised_by"): "external multi-send errors only",
        ("metaharness/loop.py", "_Counted.complete", "ProviderStop"): "owned single-send error carries its record",
        ("metaharness/loop.py", "_Counted.complete", ".record"): "owned exception's record is read directly",
        ("metaharness/loop.py", "_Counted.complete.next_request", ".request"): "a note re-admits its exact launch idempotently",
        ("self_maintenance/usage.py", "Request.text", "_text_bytes"): "text encoding made at admission",
        ("self_maintenance/usage.py", "Request.text", "_digest"): "digest of the one text encoding",
        ("self_maintenance/usage.py", "request_digest", "_text_bytes"): "compatibility text encoding",
        ("self_maintenance/usage.py", "request_digest", "_digest"): "compatibility encoded-bytes digest",
        ("self_maintenance/usage.py", "request_identity", "_digest"): "digest of the one host encoding",
        ("self_maintenance/usage.py", "request_identity", ".encoded"): "captured host encoding, no rereading",
        ("self_maintenance/usage.py", "Request.host", ".encoded"): "record retains the encoding it names",
        ("self_maintenance/usage.py", "_host_input", "_digest"): "file content digest in the encoded host manifest",
        ("self_maintenance/usage.py", "Request.host", "request_identity"): "immutable host snapshot",
        ("self_maintenance/usage.py", "ProviderStop.identity", ".identity"): "exception reads its record",
        ("self_maintenance/usage.py", "ProviderStop.__init__", ".record"): "exception carries its record",
        ("self_maintenance/usage.py", "ProviderStop.identity", ".record"): "exception's compatibility identity property",
        ("self_maintenance/usage.py", "_sending", "Request.host"): "host admission captures input once",
        ("self_maintenance/usage.py", "_sending", "Send"): "host record outside a meter",
        ("self_maintenance/usage.py", "_sending", ".identity"): "host record outside a meter",
        ("self_maintenance/usage.py", "_sending", ".request"): "handoff uses only the admitted snapshot",
        ("self_maintenance/usage.py", "_finish_record", ".request"): "private delivery copies released with their record",
        ("self_maintenance/usage.py", "_received", "request_digest"): "reply without admission: compatibility only",
        ("self_maintenance/usage.py", "_stopped", "_received"): "reply without admission: compatibility only",
        ("self_maintenance/usage.py", "_stopped", "ProviderStop"): "owned host error carries its record",
    }
    apis = {**{f"g6.self_maintenance.usage.{name}": name for name in (
        "request_digest", "request_identity", "_digest", "_text_bytes", "Request.text", "Request.host", "Send", "_received", "ProviderStop")},
        **{f"g6.metaharness.loop.{name}": name for name in ("_request_digest", "_named", "_sender", "_raised_by")}}
    found = set()

    def resolve(node, module, imports):
        name, *rest = ast.unparse(node).split(".")
        return ".".join([imports.get(name, f"{module}.{name}"), *rest])

    def walk(node, file, module, imports, where=""):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            where = f"{where}.{node.name}" if where else node.name
        if isinstance(node, ast.Call):
            name = resolve(node.func, module, imports)
            if name in apis:
                found.add((file, where, apis[name]))
        holds_records = module in {"g6.metaharness.loop", "g6.self_maintenance.usage"} or any(
            name in apis for name in imports.values())
        if isinstance(node, ast.Attribute) and node.attr in {"identity", "record", "request", "encoded"} and holds_records:
            found.add((file, where, f".{node.attr}"))
        for child in ast.iter_child_nodes(node):
            walk(child, file, module, imports, where)

    root = Path(g6.__file__).parent
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(root).with_suffix("")
        module = "g6." + ".".join(relative.parts)
        imports = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                parent = node.module or ""
                if node.level:
                    parent = ".".join(module.split(".")[:-node.level] + ([parent] if parent else []))
                imports.update({alias.asname or alias.name: f"{parent}.{alias.name}" for alias in node.names})
            elif isinstance(node, ast.Import):
                imports.update({alias.asname or alias.name.split(".")[0]:
                                alias.name if alias.asname else alias.name.split(".")[0] for alias in node.names})
        walk(tree, path.relative_to(root).as_posix(), module, imports)
    assert found == set(classified), f"unclassified: {found - classified.keys()}; missing: {classified.keys() - found}"
    for site, reason in sorted(classified.items()):
        print(*site, reason, sep="\t")


