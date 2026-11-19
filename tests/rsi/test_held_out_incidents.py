# SPDX-License-Identifier: Apache-2.0
"""Unit checks for incident ordering and the sealed commitment primitive."""
import sqlite3
from collections import OrderedDict, UserList
from contextlib import contextmanager
from io import BytesIO
from types import MappingProxyType

import pytest

from g6.recursion.contamination_gate import CanaryProbe
from g6.recursion.evaluator_entry import evaluator_capability
from g6.recursion.held_out_slicer import HeldOutSlicer
from g6.metaharness.schema import SuiteCase


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


@pytest.mark.parametrize("mode", ["newline", "double_unicode"])
def test_json_escape_incident_commits_before_later_refusal(incident_store, mode):
    import json

    store, db = incident_store
    marker = "d" * 32
    encoded = (marker[:16] + r"\n" + marker[16:] if mode == "newline" else
               json.dumps("".join(f"\\u{ord(c):04x}" for c in marker))[1:-1])
    store._commit_incidents("a" * 64, model_outputs=(encoded, None))
    with pytest.raises(ValueError), store._db():
        raise ValueError("later refusal")
    assert list(db.execute("SELECT status FROM slices")) == [("void",), ("void",)]


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


@pytest.mark.parametrize("mapping", [dict, OrderedDict, MappingProxyType])
@pytest.mark.parametrize("bad_first", [False, True])
def test_mapping_marker_commits_both_slices_before_schema_refusal(incident_store, mapping, bad_first):
    from g6.recursion.contamination_gate import ContaminationError, scan_texts

    store, db = incident_store
    pairs = [("marker", "d" * 32), ("bad", set())]
    case = SuiteCase("t", "other", mapping(dict(reversed(pairs) if bad_first else pairs)))
    store._commit_incidents("a" * 64, (case,))
    with pytest.raises(ContaminationError, match="SCREEN_INVALID"), store._db():
        scan_texts((case,))
    assert list(db.execute("SELECT status FROM slices")) == [("void",), ("void",)]
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 2


def test_incident_and_main_entry_points_visit_same_mixed_leaves():
    from g6.recursion.contamination_gate import incident_scan, scan_texts

    class Text(str):
        pass

    value = MappingProxyType(OrderedDict([
        ("first", UserList([Text("alpha"), ("beta",)])),
        ("malformed", set()),
        ("last", {"nested": "gamma"}),
    ]))
    case = SuiteCase("t", "other", value)
    texts, probes, limited = incident_scan((case,))
    assert texts == scan_texts((case,), strict=False)
    assert all(s in texts for s in ("alpha", "beta", "gamma"))
    assert not probes and not limited


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


def test_registration_rule_publishes_only_keyed_partition_seed(monkeypatch):
    from io import StringIO
    from pathlib import Path
    import json
    import g6.recursion.held_out_slicer as module
    from g6.recursion.tasks import TaskSpec
    from g6.rsi.licences import licence_for
    from g6.rsi.records import CheckSpec

    class RuleCapture(StringIO):
        def close(self):
            pass

    class StopAfterRule(Exception):
        pass

    stream = RuleCapture()
    task = TaskSpec("livecodebench:synthetic", "family", "livecodebench", licence_for("livecodebench"),
                    "prompt", CheckSpec("stdin_stdout", inputs=("in",), outputs=("out",)),
                    "ref", "medium", "2024-10-01", "held_out")
    capability = evaluator_capability(BytesIO(b"a" * 32))
    monkeypatch.setattr(HeldOutSlicer, "_check_root", lambda self: None)
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: stream)

    def stop_partition(*args):
        raise StopAfterRule

    monkeypatch.setattr(module, "partition_pool", stop_partition)
    with pytest.raises(StopAfterRule):
        HeldOutSlicer.register("unused-unit-store", (task,), seed=96, detectors=("clean",),
                              role="evaluator", capability=capability)
    rule = json.loads(stream.getvalue())
    assert "seed" not in rule
    assert rule["partition_seed_commitment"] == capability.commitment("partition-seed", 96)


def test_legacy_public_seed_registration_is_refused():
    from g6.recursion.held_out_slicer import HeldOutError

    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE registration (record TEXT NOT NULL)")
        db.execute('INSERT INTO registration VALUES (\'{"seed":96}\')')
        with pytest.raises(HeldOutError, match="^G6_E_RSI_REGISTRATION_INVALID$"):
            HeldOutSlicer._registration(db)
