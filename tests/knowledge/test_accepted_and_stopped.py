# SPDX-License-Identifier: Apache-2.0
"""What the person accepted is what happens, and a stop stops: a backend is written to only when the accepted preview
lists it; the listing, the digest and the dialog's sentence say where the text goes; a deletion says what it leaves
in a server; and a source revoked or expired while a job, a graph build or an index write runs gets nothing more."""
from __future__ import annotations

import ast
import inspect
from datetime import timedelta
from pathlib import Path

import pytest

import g6
from g6._context_engine import _engine as engine
from g6._context_engine import _graph, _jobs
from g6._context_engine._registry import ProjectStore, Refused
from g6.knowledge.ctx_colbert import readiness
from g6.knowledge.ctx_colbert.persistent import PersistentColbertIndex
from g6.knowledge.ctx_elastic import writer
from tests.knowledge.corpus import fake_elastic
from tests.knowledge.corpus.rig import NOW, make

ALLOW = lambda action: {"kind": "action_gate", "decision": "allow"}  # noqa: E731
SRC = Path(g6.__file__).parent


@pytest.fixture
def es(monkeypatch):
    state, stop = fake_elastic.serve(monkeypatch)
    yield state
    stop()


def written(es) -> int:
    return sum(len(index["docs"]) for index in es.indices.values())


def test_a_backend_the_accepted_preview_does_not_list_is_never_written_to(tmp_path, monkeypatch, es):
    rig = make(tmp_path, monkeypatch)
    source = rig.add(backends=())["source"]
    store = ProjectStore(rig.binding)
    assert store.registration(source).backends == ()
    with pytest.raises(Refused) as refused:
        _jobs.start(rig.binding, source, ["elastic_managed"], wait=True, gate=ALLOW)
    assert refused.value.code == _jobs.NOT_ACCEPTED and written(es) == 0 and store.jobs() == []
    # nor through a job record made some other way: the run itself asks what was accepted
    left = _jobs.Job(id="job-" + "7" * 16, registration_id=source, generation=1, state="queued",
                     backends={"elastic_managed": {"state": "queued"}})
    store.put_job(left.id, left.model_dump_json())
    done, = _jobs.resume(rig.binding, gate=ALLOW)
    assert done.state == "failed" and done.backends["elastic_managed"] == {"state": "refused", "reason": _jobs.NOT_ACCEPTED}
    assert written(es) == 0 and _jobs.held(store, source) == []
    # accepted: the same call writes
    other = rig.add(str(rig.folder / "docs"))["source"]
    assert store.registration(other).backends == ("elastic_managed",)
    assert _jobs.start(rig.binding, other, ["elastic_managed"], wait=True, gate=ALLOW).state == "published" and written(es) > 0


@pytest.mark.parametrize("backend", sorted(engine.OPTIONAL_BACKENDS))
def test_the_preview_its_digest_and_its_listing_say_every_backend(tmp_path, monkeypatch, backend):
    rig = make(tmp_path, monkeypatch)
    plain = engine.preview(rig.binding, str(rig.folder))
    listed = engine.preview(rig.binding, str(rig.folder), backends=[backend])
    assert plain.backends == ("sqlite",) and listed.backends == ("sqlite", backend) and listed.destinations == ("local", backend)
    assert engine.OPTIONAL_BACKENDS[backend] in listed.effects and plain.effects != listed.effects
    assert engine.copies_sentence(plain.backends) == "Indexed on this machine only; nothing is sent to a model."
    sentence = engine.copies_sentence(listed.backends)
    assert "only" not in sentence and engine.OPTIONAL_BACKENDS[backend] in sentence
    for shown in (plain, listed):
        assert f"Written to: {', '.join(shown.backends)}. {engine.copies_sentence(shown.backends)}" in Path(
            shown.listing_path).read_text(encoding="utf-8")
    # the list is under the digest: a preview whose backends were changed after it was shown is not the accepted one
    forged = plain.model_copy(update={"backends": listed.backends})
    with pytest.raises(Refused) as refused:
        engine.commit(rig.binding, forged, "Person")
    assert refused.value.code == engine.PREVIEW_CHANGED
    for bad in (["no_such_backend"], ["sqlite"], "elastic_managed", [7]):
        with pytest.raises(Refused):
            engine.preview(rig.binding, str(rig.folder), backends=bad)


def test_an_agents_argument_reaches_a_write_only_through_the_accepted_preview():
    """Read from the code: every place a backend name enters a write."""
    tool = next(n for n in ast.walk(ast.parse((SRC / "mcp" / "knowledge.py").read_text(encoding="utf-8")))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "g6_knowledge_add")
    uses = [n for n in ast.walk(tool) if isinstance(n, ast.Name) and n.id == "backends"]
    previews = [c for c in ast.walk(tool) if isinstance(c, ast.Call) and "engine.preview" in ast.unparse(c)]
    assert uses and all(any(use in ast.walk(call) for call in previews) for use in uses), \
        "the tool's backends argument is read anywhere but in the preview the person is shown"
    source = ast.unparse(tool)
    assert "engine.copies_sentence(shown.backends)" in source and "this machine only" not in source
    starts = []
    for path in SRC.rglob("*.py"):
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(call, ast.Call) and ast.unparse(call.func) in ("_jobs.start", "writer.write", "writer.pin"):
                starts.append((path.relative_to(SRC).as_posix(), ast.unparse(call.func), ast.unparse(call)))
    assert {(where, what) for where, what, _ in starts} == {
        ("mcp/knowledge.py", "_jobs.start"), ("_context_engine/_jobs.py", "writer.write"), ("_context_engine/_jobs.py", "writer.pin")}
    start, = [text for _, what, text in starts if what == "_jobs.start"]
    assert start.startswith("_jobs.start(binding, done['source'], accepted,")
    assert "accepted = ProjectStore(binding).registration(done['source']).backends" in source
    run = inspect.getsource(_jobs.run)
    assert run.index("if name not in registration.backends") < run.index("writer.pin(") < run.index("writer.write(")
    assert "still=still" in run and run.count("still()") >= 3, "asked before the files, before every batch, at the end"
    assert "name not in registration.backends" in inspect.getsource(_jobs.start)


def test_a_deletion_says_what_it_leaves_in_every_backend_recorded_as_holding_the_source(tmp_path, monkeypatch, es):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    store = ProjectStore(rig.binding)
    assert _jobs.held(store, source) == [] and engine.held_sentence([]) == ""
    job = _jobs.start(rig.binding, source, ["elastic_managed"], wait=True, gate=ALLOW)
    held = _jobs.held(store, source)
    assert held == [{"backend": "elastic_managed", "index": job.backends["elastic_managed"]["index"],
                     "records_sent": job.backends["elastic_managed"]["acknowledged"], "state": "still held"}]
    sentence = engine.held_sentence(held)
    assert "does not remove" in sentence and "Elasticsearch" in sentence and str(held[0]["records_sent"]) in sentence
    receipt = engine.delete(rig.local("delete"), source, complete=True, attribution="Person")
    assert receipt["audit_copies"] == "removed" and receipt["held_elsewhere"] == held and sentence in receipt["note"]
    assert written(es) == held[0]["records_sent"], "and that is true: the copy is still there"
    assert _jobs.held(store, source) == held, "a deletion keeps the record of what it left"
    # read from the code: every sentence of the dialog and the receipt that says something was removed or kept says
    # where, and what is held elsewhere comes from the one function
    tool = next(n for n in ast.walk(ast.parse((SRC / "mcp" / "knowledge.py").read_text(encoding="utf-8")))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "g6_knowledge_delete")
    for function in (tool, ast.parse(inspect.getsource(engine.delete))):
        texts = [n.value for n in ast.walk(function) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        said = [text for text in texts if any(word in text for word in ("removed too", "are kept", "is removed from"))]
        assert said, "control: the function does say what is removed"
        for text in said:
            assert "this machine" in text.casefold() or "from search" in text or "G6's search" in text, text
        assert "held_sentence(" in ast.unparse(function)


def test_a_source_that_expires_while_its_job_runs_gets_no_further_batch(tmp_path, monkeypatch, es):
    rig = make(tmp_path, monkeypatch)
    shown = engine.preview(rig.binding, str(rig.folder), expires_days=1, backends=("elastic_managed",), clock=lambda: NOW)
    source = engine.commit(rig.binding, shown, "Person")["source"]
    monkeypatch.setattr(writer, "MAX_BATCH_ITEMS", 10)
    real, sent, at = writer._bulk, [], [NOW]

    def bulk(target, batch, verb, caller):
        sent.append(len(batch))
        at[0] = NOW + timedelta(days=2)   # the expiry passes while the first batch is on its way
        return real(target, batch, verb, caller)

    monkeypatch.setattr(writer, "_bulk", bulk)
    job = _jobs.start(rig.binding, source, ["elastic_managed"], wait=True, gate=ALLOW, clock=lambda: at[0])
    assert sent == [10] and job.state == "failed" and "expired" in job.backends["elastic_managed"]["reason"]
    held = ProjectStore(rig.binding).published().backends["elastic_managed"]["registrations"][source]
    assert held["state"] == "unavailable" and held["receipt"] == {"stopped": "expired", "sent": 10}
    assert _jobs.held(ProjectStore(rig.binding), source)[0]["records_sent"] == 10, "what was sent is listed as held"


def test_a_graph_build_and_a_vector_write_stop_when_the_source_stops_being_usable(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    store = ProjectStore(rig.binding)
    real, read = engine._reread, []

    def reread(binding, registration, member):
        read.append(member.relative)
        if len(read) == 3:
            engine.revoke(rig.local("revoke"), source)   # revoked while the build reads
        return real(binding, registration, member)

    monkeypatch.setattr(engine, "_reread", reread)
    report = _graph.build(store, 1)
    assert len(read) == 3 and report["nodes"] == 0 and report["edges"] == 0 and store.graph(1) == (None, [])
    monkeypatch.setattr(engine, "_reread", real)

    class StandIn:
        def ready(self):
            return ""

        def encode(self, texts, *, query=False):
            return [[[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for _ in text.split()[:8]] for text in texts]

    pin = readiness.ModelPin(name="stand-in", revision="r1", tokenizer_sha256="1" * 64, config_sha256="2" * 64,
                             weights_sha256="3" * 64, dimension=8, max_tokens=8, precision="float32",
                             chunker="words:200:20", schema_version="v1")
    passages = [h.passage for h in engine._lexical(store, 1).find("convention", 20).hits]
    assert len(passages) == 20
    index = PersistentColbertIndex(tmp_path / "vectors.db", pin, max_ram_bytes=10**9, batch=4, encoder=StandIn())
    asked = []
    added = index.add(passages, registration_id=source, generation=1,
                      still=lambda: asked.append(1) or ("revoked" if len(asked) > 2 else ""))
    assert added.added == 8 and added.evidence == {"stopped": "revoked", "not_written": 12}
    assert index.status()["passages"] == 8, "whole batches before the stop stay; nothing after it is encoded or kept"
