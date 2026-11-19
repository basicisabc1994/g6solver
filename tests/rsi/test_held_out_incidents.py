# SPDX-License-Identifier: Apache-2.0
"""Unit checks for incident ordering and the sealed commitment primitive."""
import sqlite3
from contextlib import contextmanager
from io import BytesIO

import pytest

from g6.recursion.contamination_gate import CanaryProbe
from g6.recursion.evaluator_entry import evaluator_capability
from g6.recursion.held_out_slicer import HeldOutSlicer


@pytest.fixture
def incident_store(monkeypatch):
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("CREATE TABLE slices (id TEXT, generation INTEGER, status TEXT)")
    db.execute("CREATE TABLE events (record TEXT)")
    db.executemany("INSERT INTO slices VALUES (?,?,?)", [("a" * 64, 0, "open"), ("b" * 64, 1, "sealed")])
    store = object.__new__(HeldOutSlicer)

    @contextmanager
    def transaction():
        db.execute("BEGIN")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise

    monkeypatch.setattr(store, "_db", transaction)
    monkeypatch.setattr(store, "_data", lambda db: {"canaries": ["c" * 32, "d" * 32]})
    yield store, db
    db.close()


def test_raw_marker_prepass_commits_before_later_transaction_rolls_back(incident_store):
    store, db = incident_store
    store._commit_incidents("a" * 64, ({"input": ["d" * 16, "d" * 16]},))
    with pytest.raises(ValueError), store._db():
        raise ValueError("unrelated schema refusal")
    assert list(db.execute("SELECT status FROM slices")) == [("void",), ("void",)]
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 2


def test_positive_probe_prepass_commits_despite_malformed_siblings(incident_store):
    store, db = incident_store
    store._commit_incidents("a" * 64, model_outputs=(None,),
                            canary_probes=(None, CanaryProbe(.2, .1)))
    assert db.execute("SELECT status FROM slices WHERE generation=0").fetchone()[0] == "void"
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 2


def test_sealed_commitments_bind_secret_and_domain():
    first = evaluator_capability(BytesIO(b"a" * 32))
    second = evaluator_capability(BytesIO(b"b" * 32))
    record = [{"input": "candidate"}]
    digest = first.commitment("slice-content", record)
    assert len(digest) == 64
    assert digest == first.commitment("slice-content", record)
    assert digest != second.commitment("slice-content", record)
    assert digest != first.commitment("item-input", record)


def test_raw_fragment_values_are_scanned_without_dictionary_keys(incident_store):
    store, db = incident_store
    store._commit_incidents("a" * 64, ({"first": "d" * 16, "second": "d" * 16},))
    assert db.execute("SELECT status FROM slices WHERE generation=1").fetchone()[0] == "void"


def test_raw_limit_refuses_after_committing_available_marker(incident_store):
    from g6.recursion.held_out_slicer import HeldOutError

    store, db = incident_store
    cycle = []
    cycle.append(cycle)
    with pytest.raises(HeldOutError, match="SCREEN_LIMIT"):
        store._commit_incidents("a" * 64, ({"marker": "d" * 32, "cycle": cycle},))
    assert list(db.execute("SELECT status FROM slices")) == [("void",), ("void",)]


def test_uninitialized_probe_does_not_discard_positive_sibling(incident_store):
    store, db = incident_store
    store._commit_incidents("a" * 64, canary_probes=(object.__new__(CanaryProbe), CanaryProbe(.2, .1)))
    assert db.execute("SELECT status FROM slices WHERE generation=0").fetchone()[0] == "void"


def test_positive_probe_records_evidence_even_without_a_valid_slice_id(incident_store):
    import json

    store, db = incident_store
    store._commit_incidents("invalid", canary_probes=(CanaryProbe(.2, .1),))
    events = [json.loads(row[0]) for row in db.execute("SELECT record FROM events")]
    assert events == [{"kind": "canary_probe", "slice_id": None, "canary_probe_flags": 1}]


def test_public_vault_fields_are_keyed_and_keep_private_integrity_seam(monkeypatch):
    from g6.core.vault import Vault
    from g6.recursion.held_out_slicer import _keyed_vault

    capability = evaluator_capability(BytesIO(b"a" * 32))
    raw = Vault("vault", (), "b" * 64, {"0/": "c" * 64})
    monkeypatch.setattr(Vault, "seal", lambda *args, **kwargs: raw)
    vault = _keyed_vault(capability, (), "vault")
    assert vault.digest == capability.commitment("vault", raw.digest)
    assert vault.manifest["0/"] == capability.commitment("vault-file", "0/", raw.manifest["0/"])
    assert raw.digest not in repr(vault) and raw.manifest["0/"] not in repr(vault)
    assert not hasattr(vault, "_capability")

    def integrity_check(self, *, ledger):
        assert self.manifest == raw.manifest and self.digest == vault.digest
        return ()

    monkeypatch.setattr(Vault, "verify_unchanged", integrity_check)
    assert vault.verify_unchanged() == ()


@pytest.mark.parametrize("raw", [b"changed", b"[]", b"\xff"])
def test_changed_pool_bytes_keep_content_free_integrity_refusal(monkeypatch, raw):
    from pathlib import Path
    from g6.recursion.held_out_slicer import HeldOutError

    store = object.__new__(HeldOutSlicer)
    store._root = Path("unused-unit-store")
    store._capability = evaluator_capability(BytesIO(b"a" * 32))
    registered = {"pool_sha256": store._capability.commitment("pool-file", b"{}".hex()),
                  "rule_sha256": store._capability.commitment("rule-file", raw.hex())}
    monkeypatch.setattr(store, "_registration", lambda db: registered)
    monkeypatch.setattr(Path, "read_bytes", lambda path: raw)
    with pytest.raises(HeldOutError, match="^G6_E_RSI_POOL_CHANGED$"):
        store._data(None)
