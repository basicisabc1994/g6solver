# SPDX-License-Identifier: Apache-2.0
"""The managed Elasticsearch writer against a loopback double: the document id is the passage id, an HTTP success
does not stand for every item, a dropped connection is retried idempotently, the target is pinned, a caller cannot
choose what is removed, and the gate's block writes nothing."""
from __future__ import annotations

import pytest

from g6.knowledge._index import IndexRefused
from g6.knowledge._types import Document, Source
from g6.knowledge.ctx_elastic import managed, writer
from tests.knowledge.corpus import fake_elastic

PROJECT = "b" * 64
REG = "src-" + "3" * 16
ALLOW = lambda action: {"kind": "action_gate", "decision": "allow"}  # noqa: E731
BLOCK = lambda action: {"kind": "action_gate", "decision": "block", "message": "held"}  # noqa: E731


def passages(count, prefix="p"):
    out = []
    for n in range(count):
        text = f"the quokka note number {n} of {prefix}"
        source = Source.of(f"g6-knowledge://{REG}/{prefix}{n}.md", text.encode(), retrieved_at="2026-11-11T12:00:00Z")
        out.append(Document(text=text, source=source).passage(0, len(text)))
    return out


def records(items, generation=1):
    return [managed.encode(p, REG, generation) for p in items]


@pytest.fixture
def es(monkeypatch):
    state, stop = fake_elastic.serve(monkeypatch)
    yield state
    stop()


def test_id_is_the_canonical_passage_id(es):
    target = writer.pin(PROJECT)
    assert target.alias == managed.index_name(PROJECT) and target.concrete_index == target.alias + "-000001"
    items = passages(5)
    acks = writer.write(target, records(items), gate=ALLOW)
    assert [a.state for a in acks] == ["acknowledged"] * 5 and [a.passage_id for a in acks] == [p.id for p in items]
    assert set(es.docs(target.concrete_index)) == {p.id for p in items}
    assert writer.pin(PROJECT) == target, "pinning again finds the same index and creates nothing"
    assert len(es.indices) == 1
    with pytest.raises(IndexRefused):
        writer.write(target, [{"passage_id": "x", "schema": "other"}], gate=ALLOW)


def test_http_200_with_one_failed_item_is_not_success(es):
    target = writer.pin(PROJECT)
    items = passages(4)
    es.fail_ids = {items[2].id}
    acks = writer.write(target, records(items), gate=ALLOW)
    assert [a.state for a in acks] == ["acknowledged", "acknowledged", "failed", "acknowledged"]
    assert writer.read_back(target, [p.id for p in items]) == {
        items[0].id: True, items[1].id: True, items[2].id: False, items[3].id: True}


def test_connection_dropped_mid_bulk_is_unknown_and_retried_idempotently(es):
    target = writer.pin(PROJECT)
    items = passages(6)
    es.drop_bulk = 1   # the server applies the batch, then dies mid-reply
    acks = writer.write(target, records(items), gate=ALLOW)
    assert [a.state for a in acks] == ["acknowledged"] * 6, "the unknown outcome was sent again to the same ids"
    assert len(es.docs(target.concrete_index)) == 6
    bulks = [s for s in es.seen if s[1].endswith("/_bulk")]
    assert len(bulks) == 2 and {s[1] for s in bulks} == {f"/{target.concrete_index}/_bulk"}
    es.drop_bulk = writer.MAX_ATTEMPTS
    lost = writer.write(target, records(passages(2, "q")), gate=ALLOW)
    assert {a.state for a in lost} == {"unknown"}, "an outcome nobody saw is never reported as written"


def test_alias_resolving_elsewhere_or_to_two_indexes_is_refused(es):
    target = writer.pin(PROJECT)
    es.indices["other-index"] = {"mappings": managed.MAPPING, "docs": {}}
    es.aliases[target.alias] = ["other-index"]
    with pytest.raises(IndexRefused):
        writer.write(target, records(passages(1)), gate=ALLOW)
    assert es.docs("other-index") == {}
    es.aliases[target.alias] = [target.concrete_index, "other-index"]
    with pytest.raises(IndexRefused):
        writer.pin(PROJECT)
    es.aliases[target.alias] = [target.concrete_index]
    es.indices[target.concrete_index]["mappings"] = {"properties": {"text": {"type": "keyword"}}}
    with pytest.raises(IndexRefused):
        writer.pin(PROJECT)
    with pytest.raises(IndexRefused):
        writer.write(target, records(passages(1)), gate=ALLOW)


def test_caller_cannot_choose_a_deletion_target(es):
    target = writer.pin(PROJECT)
    mine, theirs = passages(3), passages(2, "other")
    writer.write(target, records(mine + theirs), gate=ALLOW)
    consent = writer.DeletionConsent(registration_id=REG, attribution_sha256="a" * 64,
                                     passage_ids=tuple(p.id for p in mine))
    with pytest.raises(IndexRefused):
        writer.remove(target, [theirs[0].id], consent=consent, gate=ALLOW)
    with pytest.raises(IndexRefused):
        writer.remove(target, [mine[0].id], consent=None, gate=ALLOW)
    with pytest.raises(TypeError):
        writer.remove(target, [mine[0].id], gate=ALLOW)   # a key or a gate decision is not deletion consent
    assert len(es.docs(target.concrete_index)) == 5
    acks = writer.remove(target, [p.id for p in mine], consent=consent, gate=ALLOW)
    assert {a.state for a in acks} == {"acknowledged"} and set(es.docs(target.concrete_index)) == {p.id for p in theirs}
    assert not hasattr(writer, "delete_index") and "index" not in writer.remove.__kwdefaults__


def test_gate_block_writes_nothing_and_returns_the_dry_run(es):
    target = writer.pin(PROJECT)
    before = len(es.seen)
    for gate in (BLOCK, None, lambda action: (_ for _ in ()).throw(RuntimeError("gate down"))):
        with pytest.raises(writer.Held) as held:
            writer.write(target, records(passages(3)), gate=gate)
        assert held.value.dry_run == {"would_send": 3, "to": target.concrete_index, "action": "upsert"}
        assert held.value.gate["decision"] == "block"
    assert es.docs(target.concrete_index) == {} and len(es.seen) == before
    asked = []
    writer.write(target, records(passages(1)), gate=lambda action: asked.append(action) or {"decision": "allow"})
    assert asked[0]["action_type"] == "call_api" and asked[0]["verification_contract"]["expected_result"]


def test_removed_ids_cannot_be_read_by_search_get_or_count(es):
    target = writer.pin(PROJECT)
    items = passages(3)
    writer.write(target, records(items), gate=ALLOW)
    reader = managed.ManagedElasticIndex(PROJECT, 1, registrations=[REG])
    assert len(reader.find("quokka").hits) == 3
    consent = writer.DeletionConsent(registration_id=REG, attribution_sha256="a" * 64,
                                     passage_ids=tuple(p.id for p in items))
    writer.remove(target, [items[0].id], consent=consent, gate=ALLOW)
    assert {h.passage.id for h in reader.find("quokka").hits} == {items[1].id, items[2].id}
    assert writer.read_back(target, [items[0].id]) == {items[0].id: False}
    assert writer.remove(target, [items[0].id], consent=consent, gate=ALLOW)[0].state == "acknowledged", "idempotent"


def test_a_batch_is_bounded(es):
    target = writer.pin(PROJECT)
    items = passages(writer.MAX_BATCH_ITEMS * 2 + 5)
    acks = writer.write(target, records(items), gate=ALLOW)
    assert {a.state for a in acks} == {"acknowledged"}
    assert len([s for s in es.seen if s[1].endswith("/_bulk")]) == 3