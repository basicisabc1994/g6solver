# SPDX-License-Identifier: Apache-2.0
"""The scoped dispatcher and the indexing jobs, with a loopback double of Elasticsearch: a dead server leaves the
lexical generation published and the route unavailable, never empty; a count is a structured answer; a job reports
what it could not read; a restart never half-publishes; and an installed server alone is not searchable."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import g6.knowledge
from g6.knowledge import _dispatch, _jobs
from g6.knowledge import _engine as engine
from g6.knowledge._envelope import SearchRequest
from g6.knowledge._registry import ProjectStore, Refused
from tests.knowledge.corpus import SUBJECTS, fake_elastic
from tests.knowledge.corpus.rig import NOW, make

DOCS = SUBJECTS[0]
ALLOW = lambda action: {"kind": "action_gate", "decision": "allow"}  # noqa: E731
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def es(monkeypatch):
    state, stop = fake_elastic.serve(monkeypatch)
    yield state
    stop()


def search(rig, query, routes=None, acc=None):
    response, _ = engine.search(acc or rig.local(), SearchRequest(query=query, top_k=10, routes=routes), clock=lambda: NOW)
    return response


def test_dead_elasticsearch_leaves_lexical_published_with_partial_readiness(tmp_path, monkeypatch):
    from g6.knowledge.ctx_elastic import elastic

    monkeypatch.setenv(elastic.ELASTICSEARCH.env, "http://127.0.0.1:9")
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    job = _jobs.start(rig.binding, source, ["elastic_managed"], wait=True, gate=ALLOW)
    assert job.state == "partial" and job.backends["elastic_managed"]["state"] == "unavailable"
    store = ProjectStore(rig.binding)
    assert store.published().number == 1 and store.published().backends["sqlite"]["state"] == "answered"
    assert store.published().backends["elastic_managed"]["state"] == "unavailable"
    ready = {r.name: why for r, why in _dispatch.routes(store)}
    assert ready["exact"] == "" and "elastic_managed" in ready["structure"]
    assert rig.items(DOCS), "the local result is untouched"


def test_route_unavailable_is_not_empty(tmp_path, monkeypatch, es):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    both = search(rig, DOCS, routes=("exact", "structure"))
    states = {b.route: b for b in both.backends}
    assert states["exact"].state == "answered" and states["structure"].state == "unavailable"
    assert "not selected" in states["structure"].reason and both.payload["items"]
    assert search(rig, DOCS, routes=("structure",)).payload["items"] == []
    assert search(rig, DOCS, routes=("structure",)).backends[0].state == "unavailable"
    unknown = search(rig, DOCS, routes=("exact", "telepathy"))
    assert {b.route: b.state for b in unknown.backends} == {"exact": "answered", "telepathy": "unsupported"}
    assert _jobs.start(rig.binding, source, ["elastic_managed"], wait=True, gate=ALLOW).state == "published"
    fused = search(rig, DOCS, routes=("exact", "structure"))
    assert {b.route: b.state for b in fused.backends} == {"exact": "answered", "structure": "answered"}
    top = fused.payload["items"][0]
    assert {c["backend"] for c in top["contributions"]} == {"sqlite", "elastic_managed"} and top["checked"]["outcome"] == "intact"
    es.down = True   # the server takes the request and dies
    broken = search(rig, DOCS, routes=("exact", "structure"))
    assert {b.route: b.state for b in broken.backends} == {"exact": "answered", "structure": "failed"}
    assert broken.payload["items"], "a server that died costs the route, not the answer"
    from g6.knowledge.ctx_elastic import elastic

    monkeypatch.setenv(elastic.ELASTICSEARCH.env, "http://127.0.0.1:9")   # nothing listens at all
    gone = search(rig, DOCS, routes=("exact", "structure"))
    assert {b.route: b.state for b in gone.backends} == {"exact": "answered", "structure": "unavailable"}
    assert gone.payload["items"]


def test_count_is_a_structured_answer_and_unsupported_on_sqlite(tmp_path, monkeypatch, es):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    before = _dispatch.count(rig.local("count"), query=DOCS)
    assert before["kind"] == "structured" and before["state"] == "unsupported" and "answer" not in before
    _jobs.start(rig.binding, source, ["elastic_managed"], wait=True, gate=ALLOW)
    total = sum(len(m.passage_ids) for m in ProjectStore(rig.binding).members(1))
    everything = _dispatch.count(rig.local("count"))
    assert everything["state"] == "answered" and everything["answer"] == {"count": total}
    assert everything["sources_counted"] == [source] and "items" not in everything
    assert 0 < _dispatch.count(rig.local("count"), query=DOCS)["answer"]["count"] < total
    assert _dispatch.count(rig.local("count", sources=[]))["answer"] == {"count": 0}
    from g6.knowledge._scope import access

    cloud = _dispatch.count(access(rig.binding, "count", "anthropic:claude_code"))
    assert cloud["answer"] == {"count": 0} and cloud["sources_counted"] == [], "an ungranted source is not counted"
    terms = _dispatch.aggregate(rig.local("count"), "registration_id", "terms")
    assert terms["answer"]["buckets"] == [{"key": source, "count": total}]
    assert _dispatch.aggregate(rig.local("count"), "document_sha256", "value_count")["answer"] == {"value": total}
    for bad in (("text", "terms"), ("registration_id", "script")):
        with pytest.raises(Refused):
            _dispatch.aggregate(rig.local("count"), *bad)
    with pytest.raises(Refused):
        _dispatch.count(rig.local("count"), filters={"text": "x"})
    engine.revoke(rig.local("revoke"), source)
    assert _dispatch.count(rig.local("count"))["answer"] == {"count": 0}, "a revoked source is not counted"


def test_partial_shards_count_is_refused(tmp_path, monkeypatch, es):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    _jobs.start(rig.binding, source, ["elastic_managed"], wait=True, gate=ALLOW)
    es.shards = {"total": 2, "successful": 1, "skipped": 0, "failed": 1}
    refused = _dispatch.count(rig.local("count"))
    assert refused["state"] == "refused" and "answer" not in refused
    assert _dispatch.aggregate(rig.local("count"), "registration_id", "terms")["state"] == "refused"
    assert search(rig, DOCS, routes=("structure",)).backends[0].state == "partial"


def test_indexer_block_still_refuses_network_backends(tmp_path, monkeypatch):
    import json

    from g6.mcp import invoke_reply

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_MCP_SENSITIVE", "knowledge_indexer")
    for backend in ("elastic", "elastic_managed"):
        reply = invoke_reply("knowledge_indexer", "search", json.dumps({"backend": backend, "query": "quokka"}))
        assert reply["ok"] is False or reply.get("completion_state") == "blocked-escalated", backend
    opened = [p.name for p in Path(g6.knowledge.__file__).parent.rglob("*.py")
              if "ManagedElasticIndex(" in p.read_text(encoding="utf-8") and p.name not in ("managed.py",)]
    assert opened == ["_dispatch.py"], "the scoped dispatcher is the only place the managed backend is opened"


def test_job_reports_unread_unsupported_and_per_backend_readiness(tmp_path, monkeypatch, es):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    path = rig.folder / "docs" / f"{DOCS}.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nchanged after indexing\n", encoding="utf-8")
    job = _jobs.start(rig.binding, source, ["elastic_managed"], wait=True, gate=ALLOW)
    assert job.state == "partial" and [e.relative for e in job.unread] == [f"docs/{DOCS}.md"]
    report = job.backends["elastic_managed"]
    assert report["state"] == "partial" and report["written"] == report["acknowledged"] == report["read_back"] > 0
    assert _jobs.status(rig.binding, job.id) == job
    with pytest.raises(Refused):
        _jobs.start(rig.binding, source, ["sqlite"], wait=True)
    with pytest.raises(Refused):
        _jobs.status(rig.binding, "job-0000000000000000")
    held = _jobs.start(rig.binding, source, ["elastic_managed"], wait=True, gate=None)
    assert held.backends["elastic_managed"]["state"] == "refused" and held.backends["elastic_managed"]["gate"] == "block"
    # the real action gate, with no key for the server in the person's key file: the write is held for a person
    real = _jobs.start(rig.binding, source, ["elastic_managed"], wait=True, gate=_jobs.elastic_gate())
    assert real.state == "partial" and real.backends["elastic_managed"]["state"] == "refused"
    assert real.backends["elastic_managed"]["gate"] in ("require_human_review", "block")


def test_restart_resumes_or_abandons_never_half_publishes(tmp_path, monkeypatch, es):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    store = ProjectStore(rig.binding)
    es.drop_bulk = 99   # the server dies during every bulk reply
    first = _jobs.start(rig.binding, source, ["elastic_managed"], wait=True, gate=ALLOW)
    assert first.state == "partial" and store.published().backends["elastic_managed"]["state"] == "partial"
    assert {r.name: bool(why) for r, why in _dispatch.routes(store)}["structure"], "a partial copy is not a ready route"
    assert search(rig, DOCS, routes=("structure",)).backends[0].state == "unavailable"
    # a server process that stopped left a job queued; the next process runs it again and it completes
    es.drop_bulk = 0
    queued = _jobs.Job(id="job-" + "1" * 16, registration_id=source, generation=1, state="running",
                       backends={"elastic_managed": {"state": "queued"}})
    store.put_job(queued.id, queued.model_dump_json())
    resumed = _jobs.resume(rig.binding, gate=ALLOW)
    assert [j.state for j in resumed] == ["published"] and _jobs.resume(rig.binding, gate=ALLOW) == []
    assert store.published().backends["elastic_managed"]["state"] == "answered"
    # a job for a generation that is no longer the published one changes nothing
    stale = _jobs.Job(id="job-" + "2" * 16, registration_id=source, generation=7, state="queued",
                      backends={"elastic_managed": {"state": "queued"}})
    store.put_job(stale.id, stale.model_dump_json())
    assert [j.state for j in _jobs.resume(rig.binding, gate=ALLOW)] == ["failed"]
    assert store.published().backends["elastic_managed"]["state"] == "answered"


def test_install_alone_is_not_searchable(tmp_path, monkeypatch, es):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    store = ProjectStore(rig.binding)
    from g6.knowledge.ctx_elastic.managed import ManagedElasticIndex

    assert ManagedElasticIndex(rig.binding.project, 1).ready() == "", "the server is installed and answers"
    ready = {r.name: why for r, why in _dispatch.routes(store)}
    assert "not selected" in ready["structure"]
    found = search(rig, DOCS, routes=("structure",))
    assert found.backends[0].state == "unavailable" and found.payload["items"] == []
    assert not [s for s in es.seen if s[1].endswith("/_search")], "an unready route is not even asked"


def test_no_jvm_or_network_in_tests():
    """No file under tests/ or src/g6/knowledge/ starts java or elasticsearch, or names a non-loopback address."""
    spawn = re.compile(r"""(?:Popen|run|call|check_output|system|spawn\w*)\(\s*\[?\s*["'](?:java|elasticsearch|bin[\\/]elasticsearch)""")
    address = re.compile(r"https?://(?!127\.0\.0\.1|localhost|\[::1\])[A-Za-z0-9.-]+\.[a-z]{2,}(?::\d+)?/?_(?:bulk|search|mget|count)")
    for folder in (ROOT / "tests" / "knowledge", ROOT / "src" / "g6" / "knowledge"):
        for path in folder.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert not spawn.search(text), path
            assert not address.search(text), path
            tree = ast.parse(text)
            for node in ast.walk(tree) if path != Path(__file__).resolve() else ():
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lower() in ("java", "java.exe"):
                    raise AssertionError(f"{path} names the java program")