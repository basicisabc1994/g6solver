"""A stop reader cannot exclude the refusal that must end its request."""

import ast
import inspect
import contextvars
import sys
import threading

import pytest

from g6.core.llm import FakeLLM, LLMError
from g6.metaharness.loop import _Counted, _Refused, _Requests
from g6.metaharness import loop
from g6.self_maintenance import envelope


def test_durable_refusal_is_locked_before_publication_but_its_hook_is_not(tmp_path, monkeypatch):
    identity = "d" * 64
    observed = []
    meter = _Requests()
    meter.open(10)
    original_fsync = envelope.os.fsync

    def keeping(request, said):
        acquired = loop._LOCK.acquire(blocking=False)
        assert acquired, "caller-supplied persistence hook holds admission"
        loop._LOCK.release()
        observed.append("hook")
        assert envelope.note_refusal(tmp_path, request, said)
        assert identity not in meter.refused

    def fsync(fd):
        assert loop._LOCK.locked(), "durable refusal write must hold admission"
        assert identity not in meter.refused, "publication must follow durable write"
        original_fsync(fd)
        assert identity in envelope.refused_requests(tmp_path)
        observed.append("durable")

    meter.keeping = keeping
    monkeypatch.setattr(envelope.os, "fsync", fsync)
    with pytest.raises(_Refused):
        meter.at_a_refusal("G6_E_MODEL_REFUSED: refusal", identity)
    assert observed == ["hook", "durable"] and identity in meter.refused


@pytest.mark.parametrize("death", [OSError, ValueError, KeyboardInterrupt, SystemExit])
def test_interrupted_durable_write_releases_admission_for_a_later_write(tmp_path, monkeypatch, death):
    meter = _Requests(keeping=lambda identity, said: envelope.note_refusal(tmp_path, identity, said))
    meter.open(10)
    original_fsync = envelope.os.fsync
    observed = []

    def failed_fsync(fd):
        observed.append(loop._LOCK.locked())
        raise death("interrupted durable write")

    monkeypatch.setattr(envelope.os, "fsync", failed_fsync)
    with pytest.raises(_Refused if issubclass(death, (OSError, ValueError)) else death):
        meter.at_a_refusal("G6_E_MODEL_REFUSED: refusal", "a" * 64)
    assert observed == [True] and not loop._LOCK.locked()
    assert not loop._RECORD.writing and "a" * 64 in meter.refused

    def later_fsync(fd):
        assert loop._LOCK.locked(), "every native refusal write must take admission, including a later direct write"
        original_fsync(fd)

    monkeypatch.setattr(envelope.os, "fsync", later_fsync)
    assert envelope.note_refusal(tmp_path, "b" * 64, "G6_E_MODEL_REFUSED: later")
    assert not loop._LOCK.locked(), "an ended write leaked admission"


def test_a_hook_can_delegate_the_native_write_to_a_fresh_thread(tmp_path, monkeypatch):
    seen, results, failed = [], [], []
    meter = _Requests()
    meter.open(10)
    original_fsync = envelope.os.fsync

    def fsync(fd):
        seen.append((loop._LOCK.locked(), "e" * 64 in meter.refused))
        original_fsync(fd)

    def keeping(identity, said):
        def write():
            try:
                results.append(envelope.note_refusal(tmp_path, identity, said))
            except BaseException as exc:
                failed.append(exc)

        writer = threading.Thread(target=write)
        writer.start()
        writer.join(5)
        assert not writer.is_alive(), "native writer waited on a lock held by its calling hook"

    meter.keeping = keeping
    monkeypatch.setattr(envelope.os, "fsync", fsync)
    with pytest.raises(_Refused):
        meter.at_a_refusal("G6_E_MODEL_REFUSED: refusal", "e" * 64)
    assert not failed and results == [True]
    assert seen == [(True, False)], "a delegated native write lost the admission guard"


def test_shared_folder_refusal_is_published_after_durable_write(tmp_path, monkeypatch):
    identity = "c" * 64
    shadow = object.__new__(loop.ShadowLoop)
    shadow.root = tmp_path
    folder = shadow._here
    meter = _Requests(keeping=shadow._keep_refusal)
    meter.open(10)
    original_fsync = envelope.os.fsync
    seen = []

    def fsync(fd):
        seen.append((loop._LOCK.locked(), identity in folder.refused, identity in meter.refused))
        original_fsync(fd)

    monkeypatch.setattr(envelope.os, "fsync", fsync)
    with pytest.raises(_Refused):
        meter.at_a_refusal("G6_E_MODEL_REFUSED: refusal", identity)
    assert seen == [(True, False, False)], "shared folder published the refusal before fsync"
    assert identity in envelope.refused_requests(tmp_path)
    assert identity in meter.refused and identity in folder.refused


def test_lost_cycle_resolves_folder_custody_before_taking_admission(tmp_path, monkeypatch):
    shadow = object.__new__(loop.ShadowLoop)
    shadow.root = tmp_path
    original_folder = loop._RECORD.folder
    reads = []

    def folder(root):
        acquired = loop._LOCK.acquire(blocking=False)
        assert acquired, "folder custody may wait on another lock and must resolve outside admission"
        loop._LOCK.release()
        reads.append(root)
        return original_folder(root)

    monkeypatch.setattr(loop._RECORD, "folder", folder)
    assert shadow.lost_cycle(ValueError("lost log"))["warning_card"]["code"] == loop.CANNOT_RECORD
    assert reads


def test_every_call_under_admission_is_an_internal_helper_or_a_named_primitive():
    """Inventory the protected bodies, their helper closure and property reads, including the durable writer."""
    tree = ast.parse(inspect.getsource(loop))
    definitions, protected, properties = {}, [], set()

    def visit(node, scope="", locked=False):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            scope = (scope + "." if scope else "") + node.name
            if isinstance(node, ast.FunctionDef):
                definitions[scope] = node
                if any(isinstance(d, ast.Name) and d.id == "property" for d in node.decorator_list):
                    properties.add(node.name)
        if isinstance(node, ast.With) and any(ast.unparse(i.context_expr) in {"self._lock", "_LOCK"}
                                              for i in node.items):
            locked = True
        if locked:
            protected.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child, scope, locked)

    visit(tree)
    # Constructors below hold plain module-owned data; the other external names are standard-library primitives.
    primitives = {
        "_ACTIVE.get", "_AROUND.get", "_AROUND.set", "any", "tuple", "zip", "next", "list", "max", "isinstance",
        # No instant is converted or added to under the lock (``utc`` asks a zone its offset): a week's end is compared
        # outside it, and the protected step only finds that very end still held (``admit``, ``ends_by``).
        "_Call", "_Ended", "_Book", "Send", "LLMError", "_Refused", "_AtLimit", "uuid4",
        "_RECORD.books.get", "_RECORD.books.pop", "_RECORD.out.get", "_RECORD.out.items",
        "_RECORD.out.setdefault", "_RECORD.out.setdefault(known, []).append", "_RECORD.taken.get",
        "_RECORD.taken.get(self, {}).get", "_RECORD.writing.add", "_RECORD.writing.discard",
        "book.sends.append", "external.copy", "unknown.add", "meter.refused.add", "said.startswith",
        "held.get", "self.taken.setdefault", "found.append", "self.ends.isoformat",
        # Condition.wait releases the already-held admission lock; it calls no reader or user hook.
        "self._settled.wait", "self._settled.notify_all",
    }
    helpers = {
        "self._await_writes": "_Requests._await_writes", "self._reset": "_Requests._reset",
        "self._out_elsewhere": "_Requests._out_elsewhere", "meter._full": "_Requests._full",
        "self._release_locked": "_Requests._release_locked", "self._targets": "_Requests._targets",
        "self._around": "_Requests._around", "_RECORD.charge": "_Record.charge", "_meters": "_meters",
        "_not_sent": "_not_sent", "_marked": "_marked", "_unread": "_unread",
    }
    expanded = set()
    while True:
        calls = {ast.unparse(n.func) for n in protected if isinstance(n, ast.Call)}
        more = {helpers[name] for name in calls & helpers.keys()} - expanded
        if not more:
            break
        expanded |= more
        protected.extend(n for name in sorted(more) for n in ast.walk(definitions[name]))
    assert expanded == set(helpers.values()), "classify the full protected helper closure"
    # sent is the sole property safe in this scope; inspect its body as part of the inventory too.
    protected.extend(ast.walk(definitions["_Requests.sent"]))
    calls = {ast.unparse(n.func) for n in protected if isinstance(n, ast.Call)}
    assert calls == primitives | helpers.keys(), f"unclassified protected call: {calls - primitives - helpers.keys()}"
    property_reads = {n.attr for n in protected if isinstance(n, ast.Attribute)
                      and not (isinstance(n.value, ast.Name) and n.value.id == "here")} & properties
    assert property_reads == {"sent"}, f"protected properties can call out or wait: {property_reads - {'sent'}}"
    lost_cycle = definitions["ShadowLoop.lost_cycle"]
    assert ast.unparse(lost_cycle.body[1]) == "here = self._here"
    folder_type = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Folder")
    assert not any(isinstance(n, ast.FunctionDef) for n in folder_type.body)

    # The only durable-write scope contains no reader or hook; argument/path conversion precedes the lock.
    writer = ast.parse(inspect.getsource(envelope.note_refusal))
    locked = [n for n in ast.walk(writer) if isinstance(n, ast.With)
              and ast.unparse(n.items[0].context_expr) == "_REFUSAL_WRITE_LOCK"]
    assert len(locked) == 1
    actual = {ast.unparse(n.func) for n in ast.walk(locked[0]) if isinstance(n, ast.Call)}
    assert actual == {"open", "kept.seek", "kept.tell", "kept.read",
                      "kept.write", "kept.flush", "kept.fileno", "os.fsync"}
    assert envelope._REFUSAL_WRITE_LOCK is loop._LOCK


@pytest.mark.parametrize("paused_read", [1, 2])
def test_refusal_finishes_while_the_stop_reader_is_paused(paused_read):
    reading = threading.Event()
    published = threading.Event()
    release = threading.Event()
    reads = []
    ended = []
    failures = []

    def halted():
        reads.append(None)
        if len(reads) == paused_read:
            reading.set()
            assert release.wait(5), "stop reader waited for refusal publication"

    requests = _Requests(halted=halted)
    requests.open(10)
    backend = FakeLLM(default="sent after the refusal")

    def pending():
        try:
            ended.append(_Counted(backend, requests).complete("system", "second request"))
        except _Refused as refused:
            ended.append(refused)
        except BaseException as failed:
            failures.append(failed)

    def refuse():
        try:
            requests.at_a_refusal(LLMError("G6_E_MODEL_REFUSED: refusal"), "first request")
        except _Refused:
            published.set()
            release.set()
        except BaseException as failed:
            failures.append(failed)

    worker = threading.Thread(target=pending)
    publisher = threading.Thread(target=refuse)
    worker.start()
    try:
        assert reading.wait(5)
        publisher.start()
        assert published.wait(5), "refusal publication blocked behind the stop reader"
    finally:
        release.set()
        worker.join(5)
        if publisher.ident is not None:
            publisher.join(5)
    assert not worker.is_alive() and not publisher.is_alive()
    assert not failures
    assert backend.calls == [] and len(ended) == 1 and isinstance(ended[0], _Refused)


@pytest.mark.parametrize("callback", ["halted", "on_record", "identify", "live", "now", "keeping", "counted", "barring"])
def test_meter_callbacks_can_take_the_admission_lock(callback):
    seen = []

    def checked(value):
        acquired = loop._LOCK.acquire(blocking=False)
        if acquired:
            loop._LOCK.release()
        seen.append(acquired)
        assert acquired, f"{callback} ran with the admission lock held"
        return value

    meter = _Requests(**({callback: lambda *args: checked(None if callback in {"halted", "keeping", "barring"} else
                                                        set() if callback == "on_record" else loop.datetime.now(loop.UTC))}
                         if callback in {"halted", "on_record", "now", "keeping", "barring"} else {}))
    meter.open(10, loop.datetime.now(loop.UTC) + loop.timedelta(days=1))
    if callback == "keeping":
        with pytest.raises(_Refused):
            meter.at_a_refusal("G6_E_MODEL_REFUSED: refusal", "first request")
    elif callback == "counted":
        call = loop._Call(object(), meter, meter.generation, lambda: True)
        meter.begin(call)
        meter.settle((call,), lambda: checked(0))
    elif callback == "barring":
        meter.from_sender(object())
    else:
        record = meter.take(identity="request", **({"identify": lambda: checked("request")} if callback == "identify"
                                                  else {"live": lambda: checked(True)} if callback == "live" else {}))
        meter.release(record)
    assert seen and all(seen)


def test_callback_inventory_is_read_from_the_meter_code():
    tree = ast.parse(inspect.getsource(_Requests))
    callbacks = {"halted", "on_record", "identify", "live", "now", "keeping", "counted", "barring"}
    declared = {argument.arg for function in ast.walk(tree) if isinstance(function, ast.FunctionDef)
                for argument in (*function.args.args, *function.args.kwonlyargs)
                if argument.annotation is not None and "Callable[" in ast.unparse(argument.annotation)}
    assert declared == callbacks, "classify every new meter callback"
    found = set()
    guarded = []

    def visit(node, locked=False):
        if isinstance(node, ast.With) and any(ast.unparse(item.context_expr) == "self._lock" for item in node.items):
            locked = True
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func).rsplit(".", 1)[-1]
            if name in callbacks:
                found.add(name)
                if locked:
                    guarded.append(ast.unparse(node.func))
        for child in ast.iter_child_nodes(node):
            visit(child, locked)

    visit(tree)
    assert found == callbacks, "classify every new meter callback"
    assert not guarded, f"callbacks under the admission lock: {guarded}"
    publication = next(node for node in tree.body[0].body
                       if isinstance(node, ast.FunctionDef) and node.name == "at_a_refusal")
    assert any(isinstance(node, ast.Try) and "_RECORD.writing.add(token)" in ast.unparse(node.body)
               and "finish()" in ast.unparse(node.finalbody) for node in ast.walk(publication)), (
                   "pending publication acquisition needs an unconditional release")
    finish = next(node for node in ast.walk(publication) if isinstance(node, ast.FunctionDef) and node.name == "finish")
    assert any(isinstance(node, ast.Try) and "_RECORD.writing.discard(token)" in ast.unparse(node.finalbody)
               for node in ast.walk(finish)), "a failed latch must still release its pending publication"


@pytest.mark.parametrize("operation", ["admit", "close", "recheck", "settle", "open"])
@pytest.mark.parametrize("death", [None, KeyboardInterrupt, SystemExit, ValueError])
def test_pending_refusal_keeps_admission_and_finalization_behind_persistence(monkeypatch, operation, death):
    writing, release, waiting = threading.Event(), threading.Event(), threading.Event()
    persisted, outcomes, failures = [], [], []
    meter = _Requests()
    meter.open(10)
    generation = meter.generation
    call = loop._Call(object(), meter, generation, lambda: True)
    meter.begin(call)

    def keep(*args):
        writing.set()
        assert release.wait(5)
        if death is not None:
            raise death("interrupted persistence")
        persisted.append(True)

    meter.keeping = keep
    original_wait = meter._settled.wait

    def wait(*args, **kwargs):
        waiting.set()
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(meter._settled, "wait", wait)

    def publish():
        try:
            meter.at_a_refusal("G6_E_MODEL_REFUSED: refusal", "first request", generation)
        except BaseException as result:
            failures.append(result)

    def observe():
        try:
            if operation == "admit":
                meter.take(identity="second request", generation=generation)
            elif operation == "settle":
                meter.settle((call,), lambda: 0)
            elif operation == "open":
                meter.open(10)
            elif operation == "recheck":
                meter.recheck(generation)
            else:
                meter.close()
            outcomes.append(persisted[:])
        except BaseException as result:
            outcomes.append(result)

    publisher = threading.Thread(target=publish)
    observer = threading.Thread(target=observe)
    publisher.start()
    try:
        assert writing.wait(5)
        observer.start()
        assert waiting.wait(5), "operation cannot wait while persistence owns the admission lock"
        assert not outcomes
    finally:
        release.set()
        publisher.join(5)
        if observer.ident is not None:
            observer.join(5)
        meter.release(call.owner)
    assert not publisher.is_alive() and not observer.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], death or _Refused)
    assert "first request" in meter.refused
    if operation in {"admit", "recheck"}:
        assert len(outcomes) == 1 and isinstance(outcomes[0], _Refused)
    else:
        assert outcomes == [[True] if death is None else []]
    assert meter.sent == 0 and not meter.in_flight


@pytest.mark.parametrize("write_fails", [False, True])
def test_pending_persistence_excludes_another_meter_until_shared_stop_is_known(monkeypatch, write_fails):
    writing, release, decided = threading.Event(), threading.Event(), threading.Event()
    persisted, failed, sent, outcomes = [], [], [], []
    first = _Requests()
    other = _Requests(halted=lambda: loop._Halted("G6_E_LOOP_CANNOT_RECORD", "shared refusal write failed")
                      if failed else None)
    first.open(10)
    other.open(10)

    def keep(*args):
        writing.set()
        assert release.wait(5)
        if write_fails:
            failed.append(True)
            raise ValueError("shared refusal write failed")
        persisted.append(True)

    first.keeping = keep
    original_wait = other._settled.wait
    original_charge = loop._RECORD.charge

    def wait(*args, **kwargs):
        decided.set()
        return original_wait(*args, **kwargs)

    def charge(meter, generation, places):
        if meter is other and places:
            sent.append(persisted[:])
            decided.set()
        return original_charge(meter, generation, places)

    monkeypatch.setattr(other._settled, "wait", wait)
    monkeypatch.setattr(loop._RECORD, "charge", charge)

    def publish():
        try:
            first.at_a_refusal("G6_E_MODEL_REFUSED: refusal", "first request")
        except (_Refused, ValueError):
            pass

    def observe():
        try:
            record = other.take(identity="different request")
            other.release(record)
            outcomes.append("sent")
        except loop._Halted as result:
            outcomes.append(result.code)

    publisher, observer = threading.Thread(target=publish), threading.Thread(target=observe)
    publisher.start()
    try:
        assert writing.wait(5)
        observer.start()
        assert decided.wait(5)
        assert not sent, "another handle sent while a shared refusal write could still fail"
    finally:
        release.set()
        publisher.join(5)
        if observer.ident is not None:
            observer.join(5)
    assert not publisher.is_alive() and not observer.is_alive()
    assert outcomes == (["G6_E_LOOP_CANNOT_RECORD"] if write_fails else ["sent"])
    assert sent == ([] if write_fails else [[True]])


def test_a_call_ending_after_its_live_read_invalidates_the_pending_admission():
    read_live, ready, release, returned = (threading.Event() for _ in range(4))
    late_answers, failures, owners = [], [], []
    meter = _Requests()
    meter.open(10)
    native_lock = meter._lock

    class AtAdmission:
        def __enter__(self):
            if (threading.current_thread().name == "late-admission" and read_live.is_set()
                    and sys._getframe(1).f_code is _Requests.admit.__code__):
                ready.set()
                assert release.wait(5)
            return native_lock.__enter__()

        def __exit__(self, *args):
            return native_lock.__exit__(*args)

    meter._lock = AtAdmission()

    def answer(system, user):
        if user == "late request":
            late_answers.append(returned.is_set())
        return "answer"

    backend = FakeLLM(answer)

    class CopiesContext:
        def complete(self, system, user, *, max_tokens=1024):
            first = backend.complete(system, user, max_tokens=max_tokens)
            call = loop._ACTIVE.get()[-1]
            owners.append(call.owner)
            original_live = call.live

            def live():
                value = original_live()
                if threading.current_thread().name == "late-admission":
                    read_live.set()
                return value

            # Instrument the existing liveness reader without changing the value it returns.
            object.__setattr__(call, "live", live)
            kept = contextvars.copy_context()

            def later():
                try:
                    kept.run(backend.complete, "system", "late request")
                except BaseException as result:
                    failures.append(result)
                finally:
                    ready.set()

            self.worker = threading.Thread(target=later, name="late-admission")
            self.worker.start()
            assert ready.wait(5)
            return first

    caller = CopiesContext()
    try:
        assert _Counted(caller, meter).complete("system", "first request") == "answer"
        returned.set()
        release.set()
        caller.worker.join(5)
        assert not caller.worker.is_alive()
        assert not any(late_answers), "copied context sent after its call returned"
        assert not meter.in_flight, "the ended call cannot own a new send"
        assert all(isinstance(result, LLMError) for result in failures)
    finally:
        release.set()
        if hasattr(caller, "worker"):
            caller.worker.join(5)
        for owner in owners:
            meter.release(owner)


def test_a_refusal_published_after_the_store_was_read_is_read_again_before_the_send(tmp_path):
    """Another handle of the folder keeps a refusal between this admission's store read and its protected step."""
    identity = "f" * 64
    refusing, admitting = (object.__new__(loop.ShadowLoop) for _ in range(2))
    refusing.root = admitting.root = tmp_path
    read, release = threading.Event(), threading.Event()
    reads, outcomes = [], []

    def on_record():
        held = envelope.refused_requests(tmp_path) | admitting._here.refused
        reads.append(identity in held)
        if len(reads) == 1:
            read.set()
            assert release.wait(5), "the refusal waited for this store reader"
        return held

    other = _Requests(keeping=refusing._keep_refusal)
    meter = _Requests(on_record=on_record)

    def admit():
        try:
            outcomes.append(meter.take(identity=identity))
        except BaseException as result:
            outcomes.append(result)

    worker = threading.Thread(target=admit)
    worker.start()
    try:
        assert read.wait(5)
        other.at_a_refusal("G6_E_MODEL_REFUSED: refusal", identity)
        assert identity in envelope.refused_requests(tmp_path)
    finally:
        release.set()
        worker.join(5)
        for record in outcomes:
            if isinstance(record, loop.Send):
                meter.release(record)
    assert not worker.is_alive()
    assert len(outcomes) == 1 and isinstance(outcomes[0], LLMError), "a refused request left on a store read made before"
    assert str(outcomes[0]).startswith(loop.CLASSIFIER_STOP) and reads == [False, True]


@pytest.mark.parametrize("latch", ["setter", "lost_cycle"])
def test_a_folder_stop_latched_after_the_stop_was_read_is_read_again_before_the_send(tmp_path, latch):
    """This process stops the folder between an admission's stop read and its protected step."""
    shadow = object.__new__(loop.ShadowLoop)
    shadow.root = tmp_path
    read, release = threading.Event(), threading.Event()
    reads, outcomes = [], []

    def halted():
        why = shadow.cannot_record
        reads.append(bool(why))
        if len(reads) == 1:
            read.set()
            assert release.wait(5), "the stop waited for this stop reader"
        return loop._Halted(loop.STOPPED, why) if why else None

    meter = _Requests(halted=halted)

    def admit():
        try:
            outcomes.append(meter.take(identity="request"))
        except BaseException as result:
            outcomes.append(result)

    worker = threading.Thread(target=admit)
    worker.start()
    try:
        assert read.wait(5)
        if latch == "setter":
            shadow.cannot_record = "its log would not take a cycle"
        else:
            shadow.lost_cycle(ValueError("lost log"), refusal={"said": "refused"})
    finally:
        release.set()
        worker.join(5)
        for record in outcomes:
            if isinstance(record, loop.Send):
                meter.release(record)
    assert not worker.is_alive()
    assert len(outcomes) == 1 and isinstance(outcomes[0], LLMError), "a send left on a stop read made before the stop"
    assert str(outcomes[0]).startswith(loop.STOPPED) and reads[0] is False and all(reads[1:])


def test_every_change_this_process_makes_to_what_admission_reads_unlocked_moves_the_counter():
    """Read from the code: admission compares the counter in its protected step, and each writer moves it under the lock."""
    tree = ast.parse(inspect.getsource(loop))

    def function(qualified):
        node = tree
        for name in qualified.split("."):
            node = next(n for n in ast.walk(node) if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name == name)
        return node

    def locked(node):
        return [n for n in ast.walk(node) if isinstance(n, ast.With)
                and any(ast.unparse(i.context_expr) in {"self._lock", "_LOCK"} for i in n.items)]

    admit = function("_Requests.admit")
    captured, decision = sorted(locked(admit), key=lambda node: node.lineno)
    assert ast.unparse(captured.body[-1]) == "seen = _RECORD.moved", "the counter is read before any reader runs"
    assert [ast.unparse(s.test) if isinstance(s, ast.If) else "" for s in decision.body[:2]] == [
        "_RECORD.writing", "_RECORD.moved != seen"]
    again = ["seen = _RECORD.moved", "continue"]    # read under the lock, before the readers run once more
    assert [[ast.unparse(s) for s in branch.body[-2:]] for branch in decision.body[:2]] == [again, again]

    finish = function("_Requests.at_a_refusal.finish")
    assert "_RECORD.moved += 1" in ast.unparse(locked(finish)[0]), "the end of each refusal write moves the counter"

    # What admission reads outside its lock is whatever the readers a handle wires into its meter read. Both are derived:
    # the callbacks ``admit`` calls outside the lock, and what ``ShadowLoop.__init__`` hands the meter for each.
    protected = {id(n) for block in locked(admit) for n in ast.walk(block)}
    wired = function("_Requests.__init__").args.args[1:]
    outside = {ast.unparse(n.func).rsplit(".", 1)[-1] for n in ast.walk(admit)
               if isinstance(n, ast.Call) and id(n) not in protected} & {a.arg for a in wired}
    assert outside == {"halted", "on_record", "now"}, "classify every reader admission calls outside its lock"
    meter = next(n for n in ast.walk(function("ShadowLoop.__init__"))
                 if isinstance(n, ast.Call) and ast.unparse(n.func) == "_Requests")
    handed = {**{a.arg: value for a, value in zip(wired, meter.args)}, **{k.arg: k.value for k in meter.keywords}}
    assert outside <= set(handed), "a reader the handle does not wire reads nothing of the handle"

    shadow = function("ShadowLoop")
    members = [n for n in shadow.body if isinstance(n, ast.FunctionDef)]
    setters = {n.name for n in members if any(ast.unparse(d).endswith(".setter") for d in n.decorator_list)}
    bodies = {n.name: n for n in members if not any(ast.unparse(d).endswith(".setter") for d in n.decorator_list)}
    assert setters <= set(bodies)

    # Every member of the handle a reader reaches through ``self``, however deep, and what each reads.
    reached, pending = [], [handed[name] for name in sorted(outside)]
    folder, handle, calls, written = set(), set(), set(), set()
    while pending:
        node = pending.pop()
        reached.append(node)
        for n in ast.walk(node):
            if (isinstance(n, ast.Call) and ast.unparse(n.func) == "getattr" and n.args
                    and ast.unparse(n.args[0]) == "self" and isinstance(n.args[1], ast.Constant)):
                handle.add(n.args[1].value)       # ``getattr(self, "<name>", ...)`` reads the handle as ``self.<name>`` does
                continue
            if isinstance(n, ast.Call) and not ast.unparse(n.func).startswith("self."):
                calls.add(ast.unparse(n.func))
            if not isinstance(n, ast.Attribute):
                continue
            base = ast.unparse(n.value)
            if isinstance(n.ctx, ast.Store) and base.startswith("self"):
                written.add(ast.unparse(n))
            elif base in {"self._here", "here"}:
                folder.add(n.attr)
            elif base == "self" and n.attr in bodies:
                if bodies[n.attr] not in reached and bodies[n.attr] not in pending:
                    pending.append(bodies[n.attr])
            elif base == "self":
                handle.add(n.attr)
    fields = {n.target.id for n in function("_Folder").body if isinstance(n, ast.AnnAssign)}
    assert folder and folder <= fields, f"a reader reads of the folder's record what it does not hold: {folder - fields}"

    # Each fact of the folder's record a reader reads: every store to it is under the lock with the counter moved, or listed.
    records = {"self._here"} | {target.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for target in n.targets
                                if isinstance(target, ast.Name)
                                and any(found in ast.unparse(n.value) for found in ("self._here", "self._found()"))}
    records |= {a.arg for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) for a in n.args.args
                if a.annotation is not None and "_Folder" in ast.unparse(a.annotation)}
    changes = {"add", "update", "discard", "remove", "pop", "clear", "append", "extend", "setdefault", "insert"}
    counted = {id(n) for block in locked(tree) if "_RECORD.moved += 1" in ast.unparse(block) for n in ast.walk(block)}
    listed = {
        # made inside a pending refusal write, which no admission passes, and whose end (``finish``, above) moves the counter
        "here.refused.add(request)": "ShadowLoop._keep_refusal",
    }
    uncounted = set()
    for n in ast.walk(tree):
        store = (isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del)) and n.attr in folder
                 and ast.unparse(n.value) in records)
        change = (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in changes
                  and isinstance(n.func.value, ast.Attribute) and n.func.value.attr in folder
                  and ast.unparse(n.func.value.value) in records)
        if (store or change) and id(n) not in counted:
            uncounted.add(ast.unparse(n))
    assert uncounted == set(listed), f"a store to what the stop or store reader reads moves no counter: {uncounted - set(listed)}"
    assert all(text in ast.unparse(function(where)) for text, where in listed.items())

    # What a reader reads of the handle itself, each with why no counter is owed, and where it may be stored.
    given = {
        "root": "__init__",        # the folder a handle is of: set once
        "log": "__init__",         # the handle of the folder's cycle log: set once; what it reads is the folder's files
        "requests": "__init__",    # the handle's meter: set once; its sets are read again under the lock (below)
        "_now": "__init__",        # the clock a handle was given: set once
        "unreadable": "__init__ _halted",    # the stop reader's own note of a store it could not read, set as it reads
        # the stop reader's own parse of the folder's records, set as it reads: ``stop_in_force`` takes it only when the
        # records' bytes it reads again now have the very digest it was parsed from, so the decision is the files' each time
        "_records_known": "__init__ _stop",
    }
    assert handle == set(given), f"classify what a reader reads of its handle: {handle ^ set(given)}"
    for name, where in given.items():
        stores = {m.name for m in members for n in ast.walk(m) if isinstance(n, ast.Attribute) and n.attr == name
                  and isinstance(n.ctx, ast.Store) and ast.unparse(n.value) == "self"}
        assert stores <= set(where.split()), f"{name} is stored outside {where}: {stores}"
    # What a reader writes: its own note, its own parse of the records (which decides nothing on bytes it did not read
    # again), and the meter's two sets, which the protected step reads again itself.
    assert written == {"self.unreadable", "self._records_known", "self.requests.refused", "self.requests.unknown"}, written
    assert "barred |= meter.refused | meter.unknown" in ast.unparse(decision)
    # What a reader reads that is no attribute of the handle: files, which another process or a person changes and no
    # counter of this process can follow; the folder's record, made once under its own lock and never replaced; and
    # helpers that read nothing.
    # ``stop_in_force`` reads the stop's files and the records, and ``admission`` asks for the stop lock beside them.
    files = {"refusal_rows", "stop_in_force", "admission", "stop.engaged"}
    record = {"_RECORD.folder"}
    helpers = {"_Halted", "_Stopped", "Stop", "_kinds", "_marked", "str", "kept.get", "c.get"}
    assert calls == files | record | helpers, f"classify what a reader calls: {calls ^ (files | record | helpers)}"