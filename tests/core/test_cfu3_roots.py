# SPDX-License-Identifier: Apache-2.0
"""Challenge written record identities without live services."""
import ast
from pathlib import Path
import pytest

from g6.mcp import improve
from g6.self_maintenance import envelope
from g6.self_maintenance.envelope import CYCLE_LOG, CycleLog, enrolment_digest


def test_identical_enrolment_bodies_have_distinct_written_ids(tmp_path):
    log = CycleLog(tmp_path / CYCLE_LOG)
    first = log.enrol("Ada", {"domain_file": "abc"}, at="same")
    digest = enrolment_digest(log)
    second = log.enrol("Ada", {"domain_file": "abc"}, at="same")
    reopened = CycleLog(log.path)
    assert first != second
    assert reopened.enrolments()[0] == reopened.enrolments()[1]
    assert enrolment_digest(reopened) == digest  # existing persisted digest keeps its meaning
    assert envelope.enrolment_id(reopened) == second
    assert reopened.record("enrolled", first) == reopened.record("enrolled", second)


def test_stop_writer_returns_only_its_written_id(tmp_path):
    log = CycleLog(tmp_path / CYCLE_LOG)
    first = log.stop("same", at="same")
    second = log.stop("same", at="same")
    reopened = CycleLog(log.path)
    assert first != second
    assert reopened.record("stopped", first) == reopened.record("stopped", second)
    assert reopened.record("stopped", "never-written") is None


def test_identity_reader_refuses_malformed_ledger(tmp_path):
    path = tmp_path / CYCLE_LOG
    path.parent.mkdir(parents=True)
    path.write_text('{"broken": true}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        envelope.enrolment_id(CycleLog(path))


def test_stop_id_survives_reopen_and_a_later_row(tmp_path):
    log = CycleLog(tmp_path / CYCLE_LOG)
    ident = log.stop("stop", at="same")
    log.pause("between write and read")
    assert CycleLog(log.path).record("stopped", ident)["reason"] == "stop"


def test_a_stop_written_then_raising_can_be_read_by_its_id(tmp_path, monkeypatch):
    log = CycleLog(tmp_path / CYCLE_LOG)
    append = log._chain.append

    def written_then_raises(*args):
        append(*args)
        raise OSError("lost return after durable append")

    monkeypatch.setattr(log._chain, "append", written_then_raises)
    with pytest.raises(OSError):
        log.stop("stop", record_id="this-stop")
    assert CycleLog(log.path).record("stopped", "this-stop")["reason"] == "stop"


@pytest.mark.parametrize("kind", ["enrolled", "stopped"])
@pytest.mark.parametrize("landed", [False, True])
def test_each_identity_write_can_be_read_after_interruption(tmp_path, monkeypatch, kind, landed):
    log = CycleLog(tmp_path / CYCLE_LOG)
    append = log._chain.append

    def interrupted(*args):
        if landed:
            append(*args)
        raise OSError("interrupted at append")

    monkeypatch.setattr(log._chain, "append", interrupted)
    with pytest.raises(OSError):
        if kind == "enrolled":
            log.enrol("Ada", {"domain_file": "abc"}, record_id="own-row")
        else:
            log.stop("stop", record_id="own-row")
    assert (CycleLog(log.path).record(kind, "own-row") is not None) is landed


def test_equal_enrolment_ends_start_by_id_while_digest_keeps_its_meaning(tmp_path):
    log = CycleLog(tmp_path / CYCLE_LOG)
    log.enrol("Ada", {"domain_file": "abc"}, at="same")
    log.start("Ada", enrolment=enrolment_digest(log), enrolment_id=envelope.enrolment_id(log))
    start = log.started()
    assert not envelope.enrolment_changed(tmp_path, start)
    log.enrol("Ada", {"domain_file": "abc"}, at="same")
    assert envelope.enrolment_changed(tmp_path, start)


def test_written_id_cannot_be_reused(tmp_path):
    log = CycleLog(tmp_path / CYCLE_LOG)
    log.stop("first", record_id="once")
    with pytest.raises(ValueError):
        log.stop("second", record_id="once")
    assert CycleLog(log.path).record("stopped", "once")["reason"] == "first"


def test_start_readback_does_not_match_a_repeated_time(tmp_path):
    log = CycleLog(tmp_path / CYCLE_LOG)
    log.start("Ada", at="same", record_id="first-start", stops=0)
    assert improve._start_on_record(tmp_path, "first-start") == {"the start": True}
    log.start("Ada", at="same", record_id="second-start", stops=0)
    assert improve._start_on_record(tmp_path, "first-start") == {"the start": False}
    assert improve._start_on_record(tmp_path, "second-start") == {"the start": True}


@pytest.mark.parametrize("act", ["enrol", "resume", "demote", "start"])
def test_identical_new_enrolment_moves_each_open_dialog(tmp_path, monkeypatch, act):
    import importlib.util

    path = Path(__file__).with_name("test_consent_followup.py")
    spec = importlib.util.spec_from_file_location("cfu3_binding_inventory", path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    rig = helpers.Rig(tmp_path, monkeypatch)
    helpers._ready(rig, act)
    bound = helpers._binding(rig, act)
    row = rig.log().enrolled()
    fields = {key: value for key, value in row.items() if key not in {"by", "frozen"}}
    rig.log().enrol(row["by"], row["frozen"], **fields)
    before = (rig.root / CYCLE_LOG).read_bytes()
    monkeypatch.setattr(improve, "_spawn", lambda *a: pytest.fail("spawn after another enrolment"))
    if act == "start":
        reply = improve.start(rig.root, "Ada", bound)
    else:
        reply = improve.act(act, rig.root, "Ada", bound, generation=1 if act == "demote" else None)
    assert reply["code"] == improve.TERMS_CHANGED
    assert (rig.root / CYCLE_LOG).read_bytes() == before


def test_every_enrolment_digest_consumer_also_reads_the_written_identity():
    """List consumers from code, so a new body-only recognition rule fails here."""
    source = Path(envelope.__file__).resolve().parents[1]
    expected = {
        ("mcp/improve.py", "_terms"),
        ("metaharness/runner.py", "_admission"),
        ("metaharness/runner.py", "person_terms"),
        ("self_maintenance/envelope.py", "enrolment_changed"),
    }
    found = set()
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            calls = {ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
            if "enrolment_digest" in calls:
                key = (path.relative_to(source).as_posix(), node.name)
                found.add(key)
                assert "enrolment_id" in calls, key
    assert found == expected


def test_enrolment_and_stop_readbacks_read_their_own_written_id():
    """Recognised rows are fetched by ledger id, with no time or body search."""
    for function, kind in [(improve._stop_on_record, "stopped"), (improve._start_on_record, "started")]:
        import inspect

        tree = ast.parse(inspect.getsource(function))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        assert any(ast.unparse(n.func) == "log.record" and n.args[0].value == kind for n in calls)
        assert not any(ast.unparse(n.func) == "log._rows" for n in calls)
    import inspect
    from g6.metaharness import runner

    text = inspect.getsource(runner.on_record)
    tree = ast.parse(text)
    assert any(isinstance(n, ast.Call) and ast.unparse(n.func) == "log.record"
               and n.args[0].value == "enrolled" for n in ast.walk(tree))
    assert 'bound.get("Enrolment it replaces")' not in text


