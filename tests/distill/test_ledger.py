# SPDX-License-Identifier: Apache-2.0
import json

import pytest

from g6.core.ledger import ChainLedger, LedgerCorrupt, LedgerError, digest
from g6.distill.ledger import Stores
from g6.distill.library import Library
from .test_records import bodies


def populated():
    stores = Stores()
    values = []
    for cls, body in bodies():
        value = cls(**body)
        stores.for_model(cls).put(value)
        values.append(value)
    return stores, values


def test_all_kinds_persist_under_state_path(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    stores, values = populated()
    for value in values:
        store = Stores().for_model(type(value))
        assert store.get(value.content_id) == value
        assert store.list() == [value]
        assert store.path.parent == tmp_path / "home" / "distill"
        assert isinstance(store.chain, ChainLedger)
        obj = store.path.parent / "objects" / f"{value.content_id}.json"
        assert json.loads(obj.read_text(encoding="utf-8")) == value.model_dump(mode="json")


@pytest.mark.parametrize("target", ["ledger", "object", "forged"])
def test_corruption_rejected_even_by_open_reader(monkeypatch, tmp_path, target):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    stores, values = populated()
    store = stores.for_model(type(values[0]))
    if target == "object":
        path = store.path.parent / "objects" / f"{values[0].content_id}.json"
        path.write_text('{}', encoding="utf-8")
    else:
        row = json.loads(store.path.read_text(encoding="utf-8"))
        row["body"]["family"] = "forged"
        if target == "forged":
            row["body_sha256"] = digest(row["body"])
            row["entry_sha256"] = digest({k: row[k] for k in ("seq", "kind", "id", "body_sha256", "prev")})
        store.path.write_text(json.dumps(row) + '\n', encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        store.list()
    with pytest.raises(LedgerCorrupt):
        Stores().for_model(type(values[0])).get(values[0].content_id)


def test_nontraining_or_self_observation_cannot_seed(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    stores = Stores()
    fc, fb = bodies()[0]
    cc, cb = bodies()[1]
    for change in ({"split": "dev"}, {"observed_by": "self"}):
        failure = fc(**(fb | change))
        stores.for_model(fc).put(failure)
        with pytest.raises(LedgerError, match="training|independent"):
            stores.for_model(cc).put(cc(**(cb | {"from_failures": [failure.record_id]})))


def test_bypassed_validation_refused_and_missing_reference(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    stores = Stores()
    fc, fb = bodies()[0]
    with pytest.raises(LedgerError):
        stores.for_model(fc).put(fc.model_construct(**(fb | {"split": "held_out"})))
    cc, cb = bodies()[1]
    with pytest.raises(LedgerError):
        stores.for_model(cc).put(cc(**cb))


def test_candidate_references_case_digest_and_refuses_nontraining(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    stores, values = populated()
    cls, body = bodies()[3]
    failure_cls, failure_body = bodies()[0]
    failure = failure_cls(**(failure_body | {"split": "monitor", "case_ref": digest("monitor")}))
    stores.for_model(failure_cls).put(failure)
    with pytest.raises(LedgerError, match="training"):
        stores.for_model(cls).put(cls(**(body | {"training_case_ids": [failure.case_ref]})))


def test_orphaned_references_fail_on_read(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    stores, values = populated()
    concept_store = stores.for_model(type(values[1]))
    # A new syntactically valid ledger with no referenced failure history is still invalid domain data.
    stores.for_model(type(values[0])).path.write_text("", encoding="utf-8")
    with pytest.raises(LedgerCorrupt, match="reference"):
        concept_store.list()


def test_library_read_only_scope_and_latest_withdrawal(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("G6_HOME", str(home))
    library = Library()
    assert library.list() == []
    assert not home.exists()
    stores, values = populated()
    entry = values[4]
    assert library.by_status("admitted") == [entry]
    assert library.by_scope(family="test_weakening", language="python", framework="pytest") == [entry]
    assert library.by_scope(family="other") == []
    body = entry.model_dump(mode="json", exclude={"entry_id"})
    withdrawn = type(entry)(**(body | {"version": 2, "status": "withdrawn", "authority": "A0"}))
    stores.for_model(type(entry)).put(withdrawn)
    assert library.by_status("admitted") == []
    assert library.list() == [withdrawn]
    assert library.get(entry.entry_id) is None
    assert library.get(withdrawn.entry_id) == withdrawn
