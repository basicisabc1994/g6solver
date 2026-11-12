# SPDX-License-Identifier: Apache-2.0
"""The managed Elasticsearch record and reader, against a loopback double of the server: a canonical round trip, a
tampered record that fails the search, filters built by G6, no caller text read as JSON, the generic cluster search's
own identity untouched, and a readiness check that costs at most two seconds."""
from __future__ import annotations

import http.server
import json
import threading
import time

import pytest

from g6.knowledge._index import FAILED, IndexRefused
from g6.knowledge._types import Document, Source
from g6.knowledge.ctx_elastic import elastic, managed

PROJECT = "a" * 64
REGISTRATION = "src-" + "1" * 16


def passage(text: str, name: str = "a"):
    source = Source.of(f"g6-knowledge://{REGISTRATION}/{name}.md", text.encode(), retrieved_at="2026-11-11T12:00:00Z")
    document = Document(text="intro. " + text, source=source)
    return document.passage(7, 7 + len(text))


class Fake(http.server.BaseHTTPRequestHandler):
    """Answers as Elasticsearch does for the requests the managed reader sends."""

    docs: dict[str, dict] = {}
    seen: list[tuple[str, str, dict | None]] = []
    slow = 0.0
    shards = {"total": 1, "successful": 1, "skipped": 0, "failed": 0}

    def log_message(self, *args):
        pass

    def _send(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _answer(self, method):
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        type(self).seen.append((method, self.path, body))
        if self.slow:
            time.sleep(self.slow)
        index, _, rest = self.path.lstrip("/").partition("/")
        if not index:
            return self._send(200, {"version": {"number": "0.0.0-double"}})
        if rest == "_search":
            if "multi_match" not in json.dumps(body):
                return self._send(200, {"_shards": self.shards, "hits": {"total": {"value": 0}, "hits": []}})
            words = set(body["query"]["bool"]["must"][0]["multi_match"]["query"].lower().split())
            wanted = {k: v for f in body["query"]["bool"]["filter"] for k, v in (f.get("term") or f.get("terms")).items()}
            found = [{"_index": index, "_id": i, "_score": 2.0, "_source": d} for i, d in self.docs.items()
                     if words & set(str(d.get("text", "")).lower().split())
                     and ("generation" not in wanted or d.get("generation") == wanted["generation"])
                     and ("registration_id" not in wanted or d.get("registration_id") in wanted["registration_id"])]
            return self._send(200, {"_shards": self.shards, "hits": {"total": {"value": len(found)}, "hits": found}})
        if rest == "_mapping":
            return self._send(200, {index: {"mappings": managed.MAPPING}})
        return self._send(404, {"error": "no such route in the double"})

    def do_GET(self):
        self._answer("GET")

    def do_POST(self):
        self._answer("POST")


@pytest.fixture
def server(monkeypatch):
    Fake.docs, Fake.seen, Fake.slow = {}, [], 0.0
    Fake.shards = {"total": 1, "successful": 1, "skipped": 0, "failed": 0}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(elastic.ELASTICSEARCH.env, f"http://127.0.0.1:{httpd.server_address[1]}")
    yield Fake
    httpd.shutdown()
    httpd.server_close()


def put(fake, one, registration=REGISTRATION, generation=4):
    fake.docs[one.id] = managed.encode(one, registration, generation)
    return one


def test_round_trip_keeps_uri_hashes_and_offsets(server):
    one = put(server, passage("The quokka convention must be followed."))
    index = managed.ManagedElasticIndex(PROJECT, 4, registrations=[REGISTRATION])
    found = index.find("quokka convention")
    assert found.outcome == "ok" and found.backend == "elastic_managed" and found.score_scale == "es_bm25"
    back = found.hits[0].passage
    assert back == one and back.id == one.id and back.source.uri == one.source.uri
    assert (back.source.sha256, back.document_sha256, back.start, back.end) == (
        one.source.sha256, one.document_sha256, 7, one.end)
    assert managed.decode({"_id": one.id, "_source": managed.encode(one, REGISTRATION, 4)}) == (one, REGISTRATION, 4)
    assert index.index == managed.INDEX_PREFIX + __import__("hashlib").sha256(PROJECT.encode()).hexdigest()[:16]


@pytest.mark.parametrize("field, value", [("text", "The quokka convention may be ignored."), ("start", 8),
                                          ("document_sha256", "0" * 64), ("schema", "other"),
                                          ("source", {"uri": "g6-knowledge://x/other.md", "sha256": "0" * 64,
                                                      "retrieved_at": "2026-11-11T12:00:00Z"})])
def test_tampered_record_fails_the_search(server, field, value):
    one = put(server, passage("The quokka convention must be followed."))
    server.docs[one.id] = {**server.docs[one.id], field: value}
    found = managed.ManagedElasticIndex(PROJECT, 4).find("quokka convention")
    assert found.outcome == "failed" and found.hits == [] and found.evidence.get("code") == FAILED
    with pytest.raises(IndexRefused):
        managed.decode({"_id": one.id, "_source": server.docs[one.id]})
    with pytest.raises(IndexRefused):   # a document filed under another id is not that passage
        managed.decode({"_id": "f" * 64, "_source": managed.encode(one, REGISTRATION, 4)})


def test_reader_filters_by_registration_and_generation_in_the_query_it_builds(server):
    mine = put(server, passage("the quokka note", "mine"))
    put(server, passage("the quokka note of another source", "theirs"), registration="src-" + "2" * 16)
    put(server, passage("the quokka note of an older generation", "old"), generation=3)
    found = managed.ManagedElasticIndex(PROJECT, 4, registrations=[REGISTRATION]).find("quokka")
    assert [h.passage.id for h in found.hits] == [mine.id]
    method, path, body = server.seen[-1]
    assert (method, path) == ("POST", f"/{managed.index_name(PROJECT)}/_search")
    assert {"term": {"generation": 4}} in body["query"]["bool"]["filter"]
    assert {"terms": {"registration_id": [REGISTRATION]}} in body["query"]["bool"]["filter"]
    assert managed.ManagedElasticIndex(PROJECT, 4, registrations=[]).find("quokka").outcome == "empty"
    # a server that ignores the filters is not believed
    from g6.core import outside

    other = managed.encode(passage("the quokka note of another source", "theirs"), "src-" + "2" * 16, 4)

    def ignoring(program, method, path, **kwargs):
        reply = {"_shards": server.shards, "hits": {"total": {"value": 1}, "hits": [
            {"_id": other["passage_id"], "_score": 1.0, "_source": other}]}}
        return outside.ServiceReply(status=200, headers={}, body=json.dumps(reply).encode())

    lied_to = managed.ManagedElasticIndex(PROJECT, 4, registrations=[REGISTRATION], caller=ignoring).find("quokka")
    assert lied_to.outcome == "failed" and lied_to.hits == []

def test_no_caller_text_reaches_the_query_as_json(server):
    put(server, passage("the quokka note"))
    evil = '"}}], "filter": [], "must": [{"match_all": {}}]}}, "size": 9999, "x": {"y": "quokka'
    index = managed.ManagedElasticIndex(PROJECT, 4, registrations=[REGISTRATION])
    index.find(evil, top_k=3)
    _, _, body = server.seen[-1]
    assert body["size"] == 3 and set(body) == {"size", "track_total_hits", "query"}
    assert body["query"]["bool"]["must"] == [{"multi_match": {"query": evil, "fields": ["text"]}}]
    assert len(body["query"]["bool"]["filter"]) == 3
    with pytest.raises(TypeError):
        managed.ManagedElasticIndex(PROJECT, 4, index="handbook")   # no caller names the index


def test_generic_cluster_search_keeps_its_own_identity(server):
    one = put(server, passage("the quokka note"))
    generic = elastic.search(managed.index_name(PROJECT), "quokka", fields=["text"])
    assert generic.outcome == "ok"
    hit = generic.hits[0].passage
    assert hit.id != one.id
    assert hit.source.uri != one.source.uri, "a generic hit is a cluster document, never the canonical passage"


def test_ready_never_blocks_past_two_seconds_and_says_why(server, monkeypatch):
    index = managed.ManagedElasticIndex(PROJECT, 4)
    assert index.ready() == ""
    server.slow = 6.0
    started = time.monotonic()
    why = index.ready()
    assert 1.5 < time.monotonic() - started < 3.5 and "did not answer within 2 seconds" in why
    server.slow = 0.0
    monkeypatch.setenv(elastic.ELASTICSEARCH.env, "http://127.0.0.1:9")
    started = time.monotonic()
    assert "did not answer" in index.ready() and time.monotonic() - started < 3.5
    assert index.find("quokka").outcome == "unavailable"


def test_partial_shards_are_not_served_and_the_reader_never_writes(server):
    put(server, passage("the quokka note"))
    server.shards = {"total": 2, "successful": 1, "skipped": 0, "failed": 1}
    found = managed.ManagedElasticIndex(PROJECT, 4).find("quokka")
    assert found.outcome == "failed" and found.evidence.get("partial") is True and found.hits == []
    index = managed.ManagedElasticIndex(PROJECT, 4)
    with pytest.raises(IndexRefused):
        index.add([])
    with pytest.raises(IndexRefused):
        index.remove(["0" * 64])
    assert not [s for s in server.seen if s[0] not in ("GET", "POST")]