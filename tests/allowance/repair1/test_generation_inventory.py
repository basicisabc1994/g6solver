"""Binding generations fence every cache writer, including capture publication."""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import inspect
import json
import sqlite3
import threading

import pytest

from g6._allowance.readers import codex_reading
from g6._allowance.store import Store, start_refresh

NOW = datetime(2026, 11, 8, tzinfo=UTC)
PAYLOAD = {"rate_limits": {"five_hour": {"used_percentage": 100, "resets_at": 2000000000}}}


def reading(profile="p", used=100, at=NOW):
    return codex_reading({"rateLimits": {"primary": {
        "usedPercent": used, "windowDurationMins": 300, "resetsAt": 2000000000}}}, profile, at)


@pytest.mark.parametrize("writer", ["save", "claim_refresh", "observe_claude"])
@pytest.mark.parametrize("rotation", [("q",), ("q", "p")])
def test_all_binding_writers_drop_obsolete_generation_after_restart(tmp_path, writer, rotation):
    old = Store(tmp_path)
    old.bind("codex", "p")
    old.bind("claude", "p")
    cg = old.generation("codex", "p")
    ag = old.generation("claude", "p")
    for profile in rotation:
        current = Store(tmp_path)
        current.bind("codex", profile)
        current.bind("claude", profile)
    if writer == "save":
        old.save(reading(), generation=cg)
    elif writer == "claim_refresh":
        assert not old.claim_refresh("codex", "p", NOW, 60, generation=cg)
    else:
        old.observe_claude(PAYLOAD, "p", NOW, "2.1.319", generation=ag)
        assert not old.capture_path("p").exists()
    assert Store(tmp_path).load("codex", "p", NOW).windows == ()
    assert Store(tmp_path).load("claude", "p", NOW).windows == ()


@pytest.mark.parametrize("step", ["before-save", "before-publish", "after-publish"])
def test_claude_generation_at_every_transaction_boundary(tmp_path, monkeypatch, step):
    s = Store(tmp_path)
    s.bind("claude", "p")
    generation = s.generation("claude", "p")
    original = s._db
    calls = 0
    def rotate():
        other = Store(tmp_path)
        other.bind("claude", "q")
        other.bind("claude", "p")
    @contextmanager
    def interleave():
        nonlocal calls
        calls += 1
        n = calls
        if n == (1 if step == "before-save" else 2) and step != "after-publish":
            rotate()
        with original() as db:
            yield db
        if n == 2 and step == "after-publish":
            rotate()
    monkeypatch.setattr(s, "_db", interleave)
    s.observe_claude(PAYLOAD, "p", NOW, "2.1.319", generation=generation)
    assert Store(tmp_path).load("claude", "p", NOW).windows == ()
    if step != "after-publish":
        assert not s.capture_path("p").exists()


def test_refresh_carries_claim_generation_across_thread_start(tmp_path, monkeypatch):
    from g6._allowance import store
    from g6.self_maintenance import usage
    s = Store(tmp_path)
    s.bind("codex", "p")
    class DelayedThread:
        def __init__(self, *, target, args, kwargs, **unused):
            self.target, self.args, self.kwargs = target, args, kwargs
        def start(self):
            other = Store(tmp_path)
            other.bind("codex", "q")
            other.bind("codex", "p")
            self.target(*self.args, **self.kwargs)
    monkeypatch.setattr(store.threading, "Thread", DelayedThread)
    monkeypatch.setattr(usage, "codex_limits", lambda: {"rateLimits": {"primary": {"usedPercent": 100}}})
    start_refresh(s, "p", NOW, 60)
    assert Store(tmp_path).load("codex", "p", NOW).windows == ()


def test_stale_rebind_is_compare_and_set_and_generation_survives_restart(tmp_path):
    initial = Store(tmp_path)
    initial.bind("codex", "p")
    old = Store(tmp_path)
    current = Store(tmp_path)
    before = current.generation("codex", "p")
    current.bind("codex", "q")
    current.save(reading("q"))
    assert old.bind("codex", "p") is None
    assert Store(tmp_path).generation("codex", "q") > before
    assert len(Store(tmp_path).load("codex", "q", NOW).windows) == 2


def test_note_writer_cannot_publish_from_an_obsolete_binding_snapshot(tmp_path):
    s = Store(tmp_path)
    s.bind("codex", "p")
    old = Store(tmp_path)
    current = Store(tmp_path)
    current.bind("codex", "q")
    assert current.changed_note("advice", "new")
    assert not old.changed_note("advice", "old")
    assert not Store(tmp_path).changed_note("advice", "new")


def test_unbound_work_is_fenced_by_the_first_binding(tmp_path):
    old = Store(tmp_path)
    generation = old.generation("codex", "p")
    Store(tmp_path).bind("codex", "p")
    old.save(reading(), generation=generation)
    assert Store(tmp_path).load("codex", "p", NOW).windows == ()


def test_removing_and_restoring_a_setting_is_also_a_new_binding(tmp_path, monkeypatch):
    from g6.allowance import report
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    root = tmp_path / "allowances"
    s = Store(root)
    path = root / "settings.json"
    def settings(profiles):
        path.write_text(json.dumps({"profiles": profiles}), encoding="utf-8")
        report(now=NOW, refresh=False)
    settings({"codex": "p"})
    generation = s.generation("codex", "p")
    settings({})
    settings({"codex": "p"})
    s.save(reading(), generation=generation)
    assert Store(root).load("codex", "p", NOW).windows == ()


def test_legacy_database_migration_is_durable_and_idempotent(tmp_path):
    path = tmp_path / "readings.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE bindings (provider TEXT PRIMARY KEY, profile TEXT NOT NULL)")
        db.execute("INSERT INTO bindings VALUES ('codex','p')")
    s = Store(tmp_path)
    before = s.generation("codex", "p")
    s.bind("codex", "q")
    assert Store(tmp_path).generation("codex", "q") > before


def test_unreadable_reading_does_not_turn_into_available_data(tmp_path):
    s = Store(tmp_path)
    s.bind("codex", "p")
    with sqlite3.connect(s.path) as db:
        db.execute("INSERT INTO readings VALUES ('codex','p','{')")
    assert s.load("codex", "p", NOW).failure == "cache unavailable"


@pytest.mark.parametrize("writer,stop", [("bind", 1), ("bind", 2), ("bind", 3),
    ("save", 1), ("claim_refresh", 1), ("changed_note", 1), *[("migration", n) for n in range(1, 6)]])
def test_interruption_after_every_sql_write_rolls_back_on_restart(tmp_path, monkeypatch, writer, stop):
    import g6._allowance.store as module
    real_connect = sqlite3.connect
    if writer == "migration":
        with real_connect(tmp_path / "readings.sqlite3") as db:
            db.execute("CREATE TABLE bindings (provider TEXT PRIMARY KEY, profile TEXT NOT NULL)")
            db.execute("INSERT INTO bindings VALUES ('codex','p')")
    else:
        s = Store(tmp_path)
        s.bind("codex", "p")
        s.save(reading())
        assert s.claim_refresh("codex", "p", NOW, 60)
        assert s.changed_note("advice", "old")
    count = 0
    class Interrupted(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            nonlocal count
            result = super().execute(sql, *args, **kwargs)
            prefixes = ("CREATE ", "ALTER ") if writer == "migration" else ("INSERT ", "DELETE ", "UPDATE ")
            if sql.startswith(prefixes):
                count += 1
                if count == stop:
                    raise OSError("synthetic interruption before commit")
            return result
    def connect(*args, **kwargs):
        return real_connect(*args, **kwargs, factory=Interrupted)
    monkeypatch.setattr(module.sqlite3, "connect", connect)
    with pytest.raises(OSError, match="synthetic interruption"):
        if writer == "migration":
            Store(tmp_path)
        elif writer == "bind":
            s.bind("codex", "q")
        elif writer == "save":
            s.save(reading(used=10, at=NOW + timedelta(seconds=1)))
        elif writer == "claim_refresh":
            s.claim_refresh("codex", "p", NOW + timedelta(seconds=61), 60)
        else:
            s.changed_note("advice", "new")
    monkeypatch.setattr(module.sqlite3, "connect", real_connect)
    current = Store(tmp_path)
    assert current.generation("codex", "p") == 1
    if writer != "migration":
        assert current.load("codex", "p", NOW).windows[0].quota_blocked is True
        assert not current.changed_note("advice", "old")
        assert current.claim_refresh("codex", "p", NOW + timedelta(seconds=61), 60)


def test_writer_inventory_is_complete():
    # A new writer must be assigned a generation/interleaving case above.
    import ast
    import g6._allowance.store as module
    tree = ast.parse(inspect.getsource(module))
    writers = set()
    for method in ast.walk(tree):
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(method):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith((
                        "INSERT ", "DELETE ", "UPDATE ", "ALTER ", "CREATE ")):
                    writers.add(method.name)
    assert writers == {"__init__", "bind", "_save", "claim_refresh", "changed_note"}


WRITERS = ("bind", "unbind", "save", "claim_refresh", "observe_claude", "changed_note")


@pytest.mark.parametrize("first,transaction", [(kind, n) for kind in WRITERS for n in range(1, {
    "save": 2, "claim_refresh": 2, "observe_claude": 3}.get(kind, 1) + 1)])
@pytest.mark.parametrize("second", WRITERS)
@pytest.mark.parametrize("step", ["before", "inside", "after"])
def test_writer_pairs_are_serialised_or_generation_fenced(tmp_path, monkeypatch, first, transaction, second, step):
    a = Store(tmp_path)
    a.bind("claude", "p")
    a.bind("codex", "p")
    b = Store(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = a._db
    count = 0
    @contextmanager
    def pause():
        nonlocal count
        count += 1
        if count == transaction and step == "before":
            entered.set()
            assert release.wait(5)
        with original() as db:
            if count == transaction and step == "inside":
                entered.set()
                assert release.wait(5)
            yield db
        if count == transaction and step == "after":
            entered.set()
            assert release.wait(5)
    monkeypatch.setattr(a, "_db", pause)
    def write(s, kind):
        if kind == "bind":
            s.bind("claude", "q")
        elif kind == "unbind":
            s.bind("claude", None)
        elif kind == "save":
            from g6._allowance.readers import claude_reading
            s.save(claude_reading(PAYLOAD, "p", NOW, version="2.1.319"))
        elif kind == "claim_refresh":
            s.claim_refresh("claude", "p", NOW, 60)
        elif kind == "observe_claude":
            s.observe_claude(PAYLOAD, "p", NOW, "2.1.319")
        else:
            s.changed_note("test", "digest")
    errors = []
    def first_write():
        try:
            write(a, first)
        except Exception as exc:
            errors.append(exc)
        finally:
            entered.set()
    thread = threading.Thread(target=first_write)
    thread.start()
    try:
        assert entered.wait(5)
        assert count >= transaction
        if step == "inside":
            # BEGIN IMMEDIATE owns the lock across both check and write.
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                write(b, second)
        else:
            write(b, second)
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    current = Store(tmp_path)
    completed = {first} if step == "inside" else {first, second}
    if {"bind", "unbind"} & completed:
        assert current.load("claude", "p", NOW).windows == ()
    elif {"save", "observe_claude"} & completed:
        assert current.load("claude", "p", NOW).windows[0].quota_blocked is True
    else:
        assert current.load("claude", "p", NOW).windows == ()
