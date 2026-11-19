# SPDX-License-Identifier: Apache-2.0
"""Public boundary checks using synthetic tasks and transactional memory storage."""
import html
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
from itertools import product
from pathlib import Path
from urllib.parse import quote, quote_plus

import pytest

from g6.metaharness.schema import SuiteCase
from g6.recursion.contamination_gate import CanaryProbe, MembershipProbe, canonical_views, screen
from g6.recursion.evaluator_entry import evaluator_capability
from g6.recursion.held_out_slicer import FROZEN_COMPONENTS, FreezeRecord, HeldOutSlicer, partition_pool
from g6.recursion.tasks import TaskSpec
from g6.rsi.licences import licence_for
from g6.rsi.records import CheckSpec


@pytest.fixture
def stores(monkeypatch):
    databases = []

    def make():
        tasks = tuple(TaskSpec(f"livecodebench:family{family}-{i}", f"family{family}", "livecodebench",
                               licence_for("livecodebench"), f"Private question {family}-{i}",
                               CheckSpec("stdin_stdout", inputs=("1",), outputs=("2",)),
                               "private-ref", "medium", "2024-10-01", "held_out")
                      for family in range(1, 7) for i in range(family))
        parts = partition_pool(tasks, 5)
        db = sqlite3.connect(":memory:", isolation_level=None)
        db.row_factory = sqlite3.Row
        databases.append(db)
        db.executescript("""
            CREATE TABLE registration (record TEXT NOT NULL);
            CREATE TABLE freezes (digest TEXT PRIMARY KEY, generation INTEGER UNIQUE, record TEXT NOT NULL);
            CREATE TABLE slices (id TEXT PRIMARY KEY, generation INTEGER UNIQUE, status TEXT NOT NULL,
                freeze TEXT, summary TEXT, excluded TEXT NOT NULL DEFAULT '[]', screened INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE events (sequence INTEGER PRIMARY KEY, record TEXT NOT NULL);
            CREATE TABLE attempts (slice TEXT, key TEXT, result TEXT, PRIMARY KEY(slice, key));
        """)
        store = object.__new__(HeldOutSlicer)
        store._capability = evaluator_capability(BytesIO(b"a" * 32))
        store._root = Path(".build/memory-privacy-store")
        data = {"tasks": [t.to_record() for t in tasks],
                "partitions": [[tasks.index(t) for t in part] for part in parts],
                "salt": "private-salt", "canaries": [f"{g + 10:032x}" for g in range(6)],
                "authority_binding": store._capability.binding(store._root)}

        @contextmanager
        def transaction():
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

        monkeypatch.setattr(store, "_db", transaction)
        monkeypatch.setattr(store, "_data", lambda db: data)
        registration = {"rule": "cut_pool-strata-six-sha256-v1", "detectors": ["clean"], "count": len(tasks)}
        db.execute("INSERT INTO registration VALUES (?)", (json.dumps(registration),))
        store._event(db, "registered", count=len(tasks), partition_digest="c" * 64)
        for generation in range(6):
            db.execute("INSERT INTO slices (id,generation,status) VALUES (?,?,'sealed')",
                       (f"{generation + 1:064x}", generation))
        return store, db

    yield make
    for db in databases:
        db.close()


def cut(store, generation=0):
    freeze = FreezeRecord(generation, "a" * 40, {name: "b" * 64 for name in FROZEN_COMPONENTS})
    return store.cut(generation, store.record_freeze(freeze))


def probes(store, item):
    return {t.task_id: MembershipProbe(1, 1, (1, 1), (1, 1))
            for t in store._tasks(store._data(None), item.generation, item.freeze_digest)}


def test_distinct_family_sizes_stay_out_of_shared_summaries_and_events(stores):
    store, db = stores()
    items = [cut(store, g) for g in range(6)]
    assert [item.count for item in items] == [6, 5, 3, 1, 2, 4]
    public = [store.registration(), *store.records(), *(item.to_record() for item in items)]
    assert all("count" not in record and "total" not in record for record in public)
    assert all("count=" not in repr(item) for item in items)
    assert json.loads(db.execute("SELECT record FROM registration").fetchone()[0])["count"] == 21


def test_singleton_training_candidate_does_not_change_any_public_record(stores):
    left, left_db = stores()
    right, right_db = stores()
    a, b = cut(left), cut(right)
    task = left._tasks(left._data(None), 0, a.freeze_digest)[0]
    present = left.screen(a.slice_id, (SuiteCase(task.task_id, "unrelated", "other"),), membership=probes(left, a))
    absent = right.screen(b.slice_id, (SuiteCase("absent", "unrelated", "other"),), membership=probes(right, b))
    assert present.excluded_count == 1 and absent.excluded_count == 0
    assert present.to_record() == absent.to_record()
    assert left.registration() == right.registration()
    assert a.to_record() == b.to_record()
    assert left.records() == right.records()
    for store, item in ((left, a), (right, b)):
        store.evaluate_pass(item.slice_id, task.task_id, "output", arm="G0", attempt="one",
                            anchor_pass=True, detectors={"clean": lambda t, o: False})
    assert left.records() == right.records()
    assert left_db.execute("SELECT result FROM attempts").fetchone()[0] != right_db.execute(
        "SELECT result FROM attempts").fetchone()[0]


def test_same_training_candidate_in_equal_size_slices_has_identical_public_screen_records(stores):
    left, _ = stores()
    right, _ = stores()
    index = left._data(None)["partitions"][0][0]
    candidate = TaskSpec.from_record(left._data(None)["tasks"][index])
    right._data(None)["tasks"][index] = replace(candidate, task_id="livecodebench:absent", provenance=None).to_record()
    a, b = cut(left), cut(right)
    assert a.count == b.count
    # Issuance commitments bind different content; neither is a screen response.
    assert a.opened_digest != b.opened_digest
    before_left, before_right = left.records(), right.records()
    training = (SuiteCase(candidate.task_id, "unrelated", "other"),)
    present = left.screen(a.slice_id, training, membership=probes(left, a))
    absent = right.screen(b.slice_id, training, membership=probes(right, b))
    assert present.excluded_count == 1 and absent.excluded_count == 0
    assert present.to_record() == absent.to_record()
    assert left.records()[len(before_left):] == right.records()[len(before_right):] == []


@pytest.mark.parametrize("cause", ["identity", "ngram", "membership", "canary", "probe"])
def test_public_screen_serialization_contains_no_slice_outcomes(cause):
    passage = " ".join(f"word{i}" for i in range(13))
    held = (SuiteCase("held", passage + " held", "secret"),)
    training = ((SuiteCase("held" if cause == "identity" else "train", passage if cause == "ngram" else "other",
                           "answer"),) if cause in ("identity", "ngram") else ())
    clean = screen((), held, canaries=("a" * 32,), membership={"held": MembershipProbe(1, 1, (1, 1), (1, 1))})
    result = screen(training, held, canaries=("a" * 32,),
                    membership={"held": MembershipProbe(10, 1 if cause == "membership" else 10, (1, 1), (1, 1))},
                    model_outputs=("a" * 32,) if cause == "canary" else (),
                    canary_probes=(CanaryProbe(.2, .1),) if cause == "probe" else ())
    assert result.excluded_count or result.voided
    assert result.to_record() == clean.to_record()
    assert "excluded" not in repr(result) and "total=" not in repr(result)


@pytest.mark.parametrize("incident", ["canary", "probe", "malformed"])
def test_incident_events_and_their_presence_stay_private(stores, incident):
    store, db = stores()
    item = cut(store)
    before = store.records()
    marker = store._data(None)["canaries"][0]
    store._commit_incidents(item.slice_id, model_outputs=(marker, None) if incident == "malformed" else
                            (marker,) if incident == "canary" else (),
                            canary_probes=(CanaryProbe(.2, .1),) if incident == "probe" else ())
    assert store.status(item.slice_id) == "void"
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] > len(before)
    assert store.records() == before


def test_public_event_projection_refuses_unknown_fields_and_kinds(stores):
    store, _ = stores()
    item = cut(store)
    with store._db() as db:
        store._event(db, "future_screen_outcome", slice_id=item.slice_id, found=True)
        record = json.loads(db.execute("SELECT record FROM events WHERE sequence=1").fetchone()[0])
        record["future_slice_size"] = 21
        db.execute("UPDATE events SET record=? WHERE sequence=1", (json.dumps(record),))
        registration = json.loads(db.execute("SELECT record FROM registration").fetchone()[0])
        registration["future_slice_size"] = 21
        db.execute("UPDATE registration SET record=?", (json.dumps(registration),))
    public = json.dumps([store.registration(), store.records()])
    assert "future_slice_size" not in public and "future_screen_outcome" not in public


def test_spending_after_private_screen_decision_cannot_disclose_status(stores):
    store, _ = stores()
    item = cut(store)
    before = store.records()
    store.spend(item.slice_id)
    assert store.status(item.slice_id) == "spent"
    assert store.records() == before


def test_complete_audit_requires_bound_evaluator_authority(stores):
    from g6.recursion.held_out_slicer import HeldOutError

    store, _ = stores()
    item = cut(store)
    store.screen(item.slice_id, (), membership=probes(store, item))
    for kwargs, code in (({"role": "learner", "capability": store._capability}, "EVALUATOR_ONLY"),
                         ({"role": "evaluator"}, "EVALUATOR_CAPABILITY_REQUIRED"),
                         ({"role": "evaluator", "capability": evaluator_capability(BytesIO(b"b" * 32))},
                          "EVALUATOR_CAPABILITY_REQUIRED")):
        with pytest.raises(HeldOutError, match=code):
            store.evaluator_records(**kwargs)
    audit = store.evaluator_records(role="evaluator", capability=store._capability)
    assert next(r for r in audit if r["kind"] == "cut")["count"] == 6
    assert next(r for r in audit if r["kind"] == "screen")["total"] == 6


@pytest.mark.parametrize("mode", ["newline", "double_unicode"])
@pytest.mark.parametrize("boundary", ["screen", "evaluate"])
def test_json_canary_void_is_durable_across_later_transactions(stores, mode, boundary):
    store, _ = stores()
    item = cut(store)
    marker = store._data(None)["canaries"][0]
    output = (marker[:16] + r"\n" + marker[16:] if mode == "newline" else
              json.dumps("".join(f"\\u{ord(c):04x}" for c in marker))[1:-1])
    if boundary == "screen":
        assert store.screen(item.slice_id, (), membership={}, model_outputs=(output,)).voided
    else:
        result = store.evaluate_pass(item.slice_id, "unused", output, arm="G0", attempt="one",
                                     anchor_pass=True, detectors=None)
        assert not result.passed and result.reason == "contamination"
    with pytest.raises(ValueError), store._db():
        raise ValueError("later transaction")
    assert store.status(item.slice_id) == "void"


SERIALISERS = {
    "json_ascii": lambda s: json.dumps(s, ensure_ascii=True),
    "json_unicode": lambda s: json.dumps(s, ensure_ascii=False),
    "repr": repr,
    "ascii": ascii,
    "str_list": lambda s: str([s]),
    "str_tuple": lambda s: str((s,)),
    "str_dict": lambda s: str({"text": s}),
    "quote": quote,
    "quote_plus": quote_plus,
    "html_escape": html.escape,
    "html_no_quotes": lambda s: html.escape(s, quote=False),
    "html_decimal": lambda s: "".join(f"&#{ord(c)};" for c in s),
    "html_hex": lambda s: "".join(f"&#x{ord(c):x};" for c in s),
    "html_named": lambda s: "".join(f"&{html.entities.codepoint2name[ord(c)]};"
                                   if ord(c) in html.entities.codepoint2name else c for c in s),
}


MALFORMED_SIBLINGS = r"C:\Users\someone | \Uwrong | https://example.invalid/%E9/%FF/% | "


def serialised_forms(text, depth):
    for names in product(SERIALISERS, repeat=depth):
        encoded = text
        for name in names:
            encoded = SERIALISERS[name](encoded)
        yield names, encoded


@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.parametrize("source", ["training", "output", "held_out"])
@pytest.mark.parametrize("sibling", ["", MALFORMED_SIBLINGS], ids=["plain", "malformed_siblings"])
def test_serialiser_compositions_exclude_thirteen_word_passage(depth, source, sibling):
    words = [f"caf\u00e9\U00010400word{i}" + ("&<>'\"" if i == 6 else "") for i in range(13)]
    passage = " \n\t\u2003".join(words)
    for names, encoded in serialised_forms(passage, depth):
        encoded = sibling + encoded
        held = SuiteCase("held", "private prompt", encoded if source == "held_out" else passage)
        training = ((SuiteCase("train", "other", encoded if source == "training" else passage),)
                    if source != "output" else ())
        result = screen(training, (held,), canaries=("a" * 32,),
                        membership={"held": MembershipProbe(1, 1, (1, 1), (1, 1))},
                        model_outputs=(encoded,) if source == "output" else ())
        assert result.ngram_excluded == result.excluded_count == 1, names


@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.parametrize("boundary", ["training_screen", "output_screen", "evaluate"])
@pytest.mark.parametrize("sibling", ["", MALFORMED_SIBLINGS], ids=["plain", "malformed_siblings"])
def test_serialiser_compositions_void_canary_durably(stores, depth, boundary, sibling):
    store, db = stores()
    item = cut(store)
    marker = store._data(None)["canaries"][0]
    spaced = marker[:8] + " \n\t\u2003" + marker[8:16] + "\r\f\v" + marker[16:]
    for names, encoded in serialised_forms(spaced, depth):
        encoded = sibling + encoded
        db.execute("UPDATE slices SET status='open' WHERE id=?", (item.slice_id,))
        if boundary == "evaluate":
            result = store.evaluate_pass(item.slice_id, "unused", encoded, arm="G0", attempt="one",
                                         anchor_pass=True, detectors=None)
            assert not result.passed and result.reason == "contamination", names
        else:
            training = (SuiteCase("train", encoded, "other"),) if boundary == "training_screen" else ()
            outputs = (encoded,) if boundary == "output_screen" else ()
            assert store.screen(item.slice_id, training, membership={}, model_outputs=outputs).voided, names
        with pytest.raises(ValueError), store._db():
            raise ValueError("later transaction")
        assert store.status(item.slice_id) == "void", names
        assert db.execute("SELECT count(*) FROM events WHERE record LIKE '%contamination%'").fetchone()[0] > 0


def review_sibling_form(text, sibling):
    if sibling == "windows_json":
        return json.dumps({"path": r"C:\Users\someone", "text": json.dumps(text)})
    return json.dumps({"url": "https://example.invalid/" + sibling, "text": quote(text)})


@pytest.mark.parametrize("sibling", ["windows_json", "%E9", "%FF"])
@pytest.mark.parametrize("source", ["training", "output", "held_out"])
def test_review_malformed_siblings_do_not_hide_passage(sibling, source):
    passage = "\n".join(f"word{i}" for i in range(13))
    encoded = review_sibling_form(passage, sibling)
    held = SuiteCase("held", "private", encoded if source == "held_out" else passage)
    training = ((SuiteCase("train", "other", encoded if source == "training" else passage),)
                if source != "output" else ())
    result = screen(training, (held,), canaries=("a" * 32,),
                    membership={"held": MembershipProbe(1, 1, (1, 1), (1, 1))},
                    model_outputs=(encoded,) if source == "output" else ())
    assert result.ngram_excluded == result.excluded_count == 1


@pytest.mark.parametrize("sibling", ["windows_json", "%E9", "%FF"])
@pytest.mark.parametrize("boundary", ["training_screen", "output_screen", "evaluate"])
def test_review_malformed_siblings_do_not_prevent_durable_void(stores, sibling, boundary):
    store, db = stores()
    item = cut(store)
    marker = store._data(None)["canaries"][0]
    encoded = review_sibling_form(marker[:16] + "\n" + marker[16:], sibling)
    if boundary == "evaluate":
        store.screen(item.slice_id, (), membership=probes(store, item))
        task = store._tasks(store._data(None), 0, item.freeze_digest)[0]
        result = store.evaluate_pass(item.slice_id, task.task_id, encoded, arm="G0", attempt="one",
                                     anchor_pass=True, detectors={"clean": lambda t, o: False})
        assert not result.passed and result.reason == "contamination"
    else:
        training = (SuiteCase("train", encoded, "other"),) if boundary == "training_screen" else ()
        outputs = (encoded,) if boundary == "output_screen" else ()
        assert store.screen(item.slice_id, training, membership={}, model_outputs=outputs).voided
    with pytest.raises(ValueError), store._db():
        raise ValueError("later transaction")
    assert store.status(item.slice_id) == "void"
    assert db.execute("SELECT count(*) FROM events WHERE record LIKE '%contamination%'").fetchone()[0] > 0


@pytest.mark.parametrize("raw,plain", [
    (r"C:\Users\someone | caf\xe9", "C:\\Users\\someone | café"),
    (r"\Uwrong | \U00010400", "\\Uwrong | \U00010400"),
    (r"\U00110000 | \ud801\udc00", "\\U00110000 | \U00010400"),
    (r"\N{bad} | \N{LATIN SMALL LETTER E WITH ACUTE}", "\\N{bad} | é"),
    ("%E9%61%FF%C3%A9%62% | %FF%63", "%E9a%FFéb% | %FFc"),
    ("%e9%61%ff%C3%A9", "%e9a%ffé"),
    ("%E2%82%61%C3%A9%F0%9F%92", "%E2%82aé%F0%9F%92"),
])
def test_valid_sequences_decode_beside_malformed_sequences(raw, plain):
    from g6.recursion.contamination_gate import _literal_text, _percent_text

    decoded = _percent_text(raw) if raw.startswith("%") else _literal_text(raw)[0]
    assert decoded == plain
    assert canonical_views(raw) == canonical_views(plain)


@pytest.mark.parametrize("passage", [" ".join(["caf\u00e9"] * 13), " ".join(["\U00010400"] * 13)],
                         ids=["review_ascii_latin1", "review_ascii_eight_digit_U"])
@pytest.mark.parametrize("source", ["training", "output", "held_out"])
def test_review_ascii_passage_reproductions(passage, source):
    encoded = ascii(passage)
    held = SuiteCase("held", "private", encoded if source == "held_out" else passage)
    training = ((SuiteCase("train", "other", encoded if source == "training" else passage),)
                if source != "output" else ())
    result = screen(training, (held,), canaries=("a" * 32,),
                    membership={"held": MembershipProbe(1, 1, (1, 1), (1, 1))},
                    model_outputs=(encoded,) if source == "output" else ())
    assert result.ngram_excluded == result.excluded_count == 1


@pytest.mark.parametrize("boundary", ["screen", "evaluate"])
def test_review_repr_x85_canary_void_is_durable(stores, boundary):
    store, _ = stores()
    item = cut(store)
    marker = store._data(None)["canaries"][0]
    encoded = repr(marker[:16] + "\x85" + marker[16:])
    if boundary == "screen":
        assert store.screen(item.slice_id, (), membership={}, model_outputs=(encoded,)).voided
    else:
        result = store.evaluate_pass(item.slice_id, "unused", encoded, arm="G0", attempt="one",
                                     anchor_pass=True, detectors=None)
        assert not result.passed and result.reason == "contamination"
    with pytest.raises(ValueError), store._db():
        raise ValueError("later transaction")
    assert store.status(item.slice_id) == "void"


@pytest.mark.parametrize("raw,plain", [
    (r"caf\xe9", "caf\u00e9"), (r"\U00010400", "\U00010400"),
    (r"caf\351", "caf\u00e9"), (r"\ud801\udc00", "\U00010400"),
    ("caf\u00e9\U00010400" + r"\tword", "caf\u00e9\U00010400 word"),
    ("&Eacute;", "\u00c9"), ("caf%C3%A9+word", "caf\u00e9 word"),
])
def test_family_decoders_preserve_unicode_and_escape_case(raw, plain):
    assert canonical_views(raw) == canonical_views(plain)


@pytest.mark.parametrize("raw", ["\\", r"\x", r"\xgg", r"\u12", r"\U00110000", r"\N{bad}",
                                 "%", "%zz", "&missing;", "\ud800", "\udfff"])
def test_malformed_serialised_text_never_raises_and_reaches_fixed_views(raw):
    views = canonical_views(raw)
    assert views[0] == raw
    assert canonical_views(views[0]) == views


@pytest.mark.parametrize("malformed", [r"\x", r"\U00110000", r"\N{bad}"])
def test_malformed_literal_does_not_disable_casefold_for_sibling_text(malformed):
    assert canonical_views("TEXT " + malformed)[0] == "text " + malformed
    result = screen((), (SuiteCase("held", "private", "answer"),), canaries=("a" * 32,),
                    membership={}, model_outputs=(malformed + " | " + "A" * 32,))
    assert result.voided and result.canary_hits == 1
