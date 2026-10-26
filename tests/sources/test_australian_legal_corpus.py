# SPDX-License-Identifier: Apache-2.0
"""g6.sources.australian_legal_corpus: the Open Australian Legal Corpus fetched at a pinned revision (hash-checked,
through g6.core.http and the action gate) or registered in place, indexed locally in SQLite FTS5, and searched with
each passage's jurisdiction, link and collection date. The corpus here is invented (no real legal text, no real
names) and the network is a transport double: no request leaves the test and the dataset is never read."""
from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.sources.australian_legal_corpus as package
from g6.core import http
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.mcp import describe_reply, invoke_reply
from g6.sources.australian_legal_corpus import (
    ATTRIBUTION,
    PINNED,
    AustralianLegalCorpusBlock,
    AustralianLegalCorpusInput,
    Pin,
    block as block_module,
    store,
)

WHEN, LATER = "2025-03-10T09:30:00+11:00", "2025-03-11T08:00:00+11:00"
LONG = "".join(f"Section {i}. The registrar keeps widget register entry {i} in the register book.\n"
               for i in range(300))


def _doc(vid: str, kind: str, place: str, source: str, citation: str, text: str, **over: object) -> dict:
    return {"version_id": vid, "type": kind, "jurisdiction": place, "source": source, "citation": citation,
            "mime": "text/html", "date": "2031-01-01", "url": f"https://example.org/invented/{vid}",
            "when_scraped": WHEN, "text": text, **over}


DOCS = [
    _doc("cth-1", "primary_legislation", "commonwealth", "federal_register_of_legislation",
         "Widget Labelling Act 2031 (Cth)", "1 Short title\nThis Act is the Widget Labelling Act 2031.\n2 Labels\nA "
         "widget maker must label each widget with its load limit and the maker's registered name.\n"),
    _doc("nsw-1", "secondary_legislation", "new_south_wales", "nsw_legislation",
         "Lantern Tenancy Regulation 2029 (NSW)", "3 Tenancy bond\nA lantern keeper who takes a tenancy bond must "
         "lodge the bond within ten days.\n4 Records\nThe keeper must keep a record of every bond.\n",
         when_scraped=LATER),
    _doc("qld-1", "bill", "queensland", "queensland_legislation", "Kite Safety Bill 2030 (Qld)",
         "A kite flyer must not fly a widget-laden kite near a lantern store."),
    _doc("nsw-2", "decision", "new_south_wales", "nsw_caselaw", "Placeholder Widgets v Fixture Lanterns [2099] NSWSC 1",
         "The tenancy bond was not lodged within ten days, so the claim for the bond succeeds."),
    _doc("tas-1", "primary_legislation", "tasmania", "tasmanian_legislation", "Widget Register Act 2032 (Tas)", LONG),
    _doc("vic-1", "primary_legislation", "victoria", "nsw_legislation", "Lantern Bond Act 2031 (Vic)",
         "A tenancy bond for a lantern must be lodged within ten days."),
    _doc("cth-1", "primary_legislation", "commonwealth", "federal_register_of_legislation", "Repeated", "repeated"),
]
LINES = [json.dumps(d) for d in DOCS[:6]] + ['{"version_id": "broken", ',
                                              json.dumps({k: v for k, v in DOCS[0].items() if k != "text"}),
                                              json.dumps(DOCS[6]),
                                              json.dumps(_doc("big-1", "primary_legislation", "commonwealth",
                                                              "federal_register_of_legislation", "Big Act",
                                                              "widget " * 6000))]
DATA = ("\n".join(LINES) + "\n").encode()
PIN = Pin(dataset="invented/legal-corpus", revision="f" * 40, filename="corpus.jsonl", size=len(DATA),
          sha256=hashlib.sha256(DATA).hexdigest(), version="0.0.0", collected="10 March 2025", revised="2099-01-01")
EVERYTHING = ("primary_legislation", "secondary_legislation", "bill", "decision")
ALLOW = {"kind": "action_gate", "decision": "allow", "reason_codes": [], "message": ""}


def allow(action: dict, key: str | None) -> dict:
    allow.seen.append(action)
    return ALLOW


allow.seen = []


class Streams:
    """A stream transport double: each URL answers a status, headers and body; every request is recorded."""

    def __init__(self, replies: dict[str, tuple[int, dict[str, str], bytes]]) -> None:
        self.replies, self.sent = replies, []

    def __call__(self, request: http.Request) -> http.Stream:
        self.sent.append(request.url)
        status, headers, body = self.replies[request.url]
        return http.Stream(status, headers, iter([body[i:i + 97] for i in range(0, len(body), 97)]), request.url)


def client(replies: dict[str, tuple[int, dict[str, str], bytes]]) -> tuple[http.Client, Streams]:
    streams = Streams(replies)
    return http.Client(resolver=lambda host, port: ["93.184.216.34"], stream_transport=streams, min_interval=0), streams


def make(tmp_path: Path, **kw: object) -> AustralianLegalCorpusBlock:
    kw.setdefault("free", lambda path: 10 ** 13)
    return AustralianLegalCorpusBlock(pin=PIN, folder=tmp_path / "state", max_line=30_000, **kw)


def run(block: AustralianLegalCorpusBlock, op: str, **params: object) -> dict:
    return block.infer(AustralianLegalCorpusInput(op=op, **params)).unwrap()


def download(tmp_path: Path, data: bytes = DATA) -> Path:
    path = tmp_path / "download" / "corpus.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def registered(tmp_path: Path, **kw: object) -> AustralianLegalCorpusBlock:
    block = make(tmp_path, **kw)
    assert run(block, "register", path=str(download(tmp_path)))["code"] == block_module.REGISTERED
    return block


def built(tmp_path: Path, **build: object) -> AustralianLegalCorpusBlock:
    block = registered(tmp_path)
    for _ in range(20):
        if run(block, "build", **build)["complete"]:
            return block
        build = {}
    raise AssertionError("the build did not finish")


def blocked(out: dict, code: str) -> None:
    assert out["code"] == code and out["completion_state"] == "blocked-escalated", out["warning_card"]


# --- D1: wrong_jurisdiction -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("named", ["Victoria", "VIC", "the Australian Capital Territory", "northern-territory", "ACT",
                                   "Ruritania"])
def test_d1_a_jurisdiction_the_corpus_lacks_is_refused_and_never_answered_with_another(tmp_path: Path,
                                                                                      named: str) -> None:
    block = built(tmp_path, types=EVERYTHING)
    # the words are in New South Wales documents: without the check they would be the answer
    assert run(block, "search", query="tenancy bond", jurisdiction="NSW")["hits"]
    out = run(block, "search", query="tenancy bond", jurisdiction=named)
    blocked(out, block_module.NOT_COVERED_CODE)
    assert "hits" not in out and "New South Wales" in out["warning_card"]["message"]
    assert "no other jurisdiction's law is given in its place" in out["warning_card"]["message"]


def test_d1_a_query_that_names_a_jurisdiction_the_corpus_lacks_is_refused_unless_a_covered_one_is_named(
        tmp_path: Path) -> None:
    block = built(tmp_path, types=EVERYTHING)
    out = run(block, "search", query="Victorian tenancy bond rules", match="any")
    blocked(out, block_module.NOT_COVERED_CODE)
    assert "Victoria" in out["warning_card"]["message"]
    named = run(block, "search", query="Victorian tenancy bond rules", match="any", jurisdiction="New South Wales")
    assert named["hits"] and {h["jurisdiction"] for h in named["hits"]} == {"new_south_wales"}


def test_d1_a_record_with_a_jurisdiction_outside_the_pinned_set_is_never_indexed(tmp_path: Path) -> None:
    block = registered(tmp_path)
    out = run(block, "build", types=EVERYTHING)
    assert out["this_call"]["skipped"]["unpinned_value"] == 1
    hits = run(block, "search", query="lantern bond lodged", match="any", k=50)["hits"]
    assert hits and "victoria" not in {h["jurisdiction"] for h in hits} and "vic-1" not in {h["version_id"] for h in hits}


# --- D1: stale_context_used -------------------------------------------------------------------------------------------

def test_d1_every_passage_carries_its_collection_date_and_is_never_called_current_law(tmp_path: Path) -> None:
    block = built(tmp_path, types=EVERYTHING)
    out = run(block, "search", query="tenancy bond")
    assert out["code"] == block_module.FOUND and out["completion_state"] == "qualified-draft"
    assert {h["when_scraped"] for h in out["hits"]} == {WHEN, LATER}
    card = out["warning_card"]["message"]
    for said in ("Collected between 2025-03-10 and 2025-03-11", "this is not current law",
                 "Amendments, repeals and later decisions are not included", "Not legal advice", ATTRIBUTION,
                 "https://legislation.nsw.gov.au", "https://www.caselaw.nsw.gov.au"):
        assert said in card, said
    (snapshot,) = [e for e in out["evidence"] if e["kind"] == "corpus_snapshot"]
    assert snapshot["current_law"] is False and snapshot["later_changes_included"] is False
    assert snapshot["legal_advice"] is False and snapshot["when_scraped"] == ["2025-03-10", "2025-03-11"]
    text = json.dumps(out).lower()
    assert "in force" not in text and "up to date" not in text and '"verified": true' not in text


def test_d1_one_document_says_the_same_and_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    block = built(tmp_path)
    monkeypatch.setattr(store, "GET_CHARS", 1000)
    first = run(block, "get", version_id="tas-1")
    doc = first["document"]
    assert doc["truncated"] is True and doc["returned_chars"] == 1000 and doc["chars"] == len(LONG)
    assert "Collected on 2025-03-10" in first["warning_card"]["message"]
    assert "this is not current law" in first["warning_card"]["message"]
    pieces, start = [doc["text"]], 1000
    while True:
        more = run(block, "get", version_id="tas-1", start=start)["document"]
        pieces.append(more["text"])
        start += more["returned_chars"]
        if not more["truncated"]:
            break
    assert "".join(pieces) == LONG   # the passages joined give the document back exactly


# --- D1: answered_from_memory -----------------------------------------------------------------------------------------

def test_d1_with_no_index_search_and_get_are_refused_never_an_empty_answer(tmp_path: Path) -> None:
    block = make(tmp_path)
    for op, params in (("search", {"query": "tenancy bond"}), ("get", {"version_id": "nsw-1"})):
        out = run(block, op, **params)
        blocked(out, block_module.NO_INDEX)
        assert "fetch it (or download it yourself and register it), then build" in out["warning_card"]["message"]


def test_d1_a_jurisdiction_or_type_left_out_of_the_index_is_refused_not_answered_with_nothing(tmp_path: Path) -> None:
    block = built(tmp_path, jurisdictions=("Cth", "NSW"))
    blocked(run(block, "search", query="tenancy bond", types=("decision",)), block_module.NOT_INDEXED)
    blocked(run(block, "search", query="kite", jurisdiction="Queensland"), block_module.NOT_INDEXED)
    found = run(block, "search", query="tenancy bond")
    assert {h["version_id"] for h in found["hits"]} == {"nsw-1"}     # legislation only: the decision is not there


def test_no_match_says_it_is_not_evidence_that_the_law_says_nothing(tmp_path: Path) -> None:
    out = run(built(tmp_path), "search", query="submarine licence")
    assert out["code"] == block_module.NO_MATCH and out["hits"] == []
    assert "not evidence that the law says nothing" in out["warning_card"]["message"]
    assert "Do not answer from memory" in out["warning_card"]["suggested_action"]


# --- fetch ------------------------------------------------------------------------------------------------------------

def test_a_fetch_dry_run_sends_nothing_and_names_what_it_would_do(tmp_path: Path) -> None:
    http_client, streams = client({})
    out = run(make(tmp_path, client=http_client, gate=allow), "fetch")
    would = out["would_fetch"]
    assert out["code"] == block_module.DRY_RUN and streams.sent == [] and not (tmp_path / "state").exists()
    assert (would["url"], would["revision"], would["bytes"], would["sha256"]) == (PIN.url, PIN.revision, PIN.size,
                                                                                  PIN.sha256)
    assert would["needs_free_bytes"] == 2 * PIN.size and would["free_bytes"] == 10 ** 13
    assert "CC BY 4.0" in would["licence"] and out["person_can_run"].startswith("hf download invented/legal-corpus")
    assert allow.seen[-1]["verification_contract"]["expected_result"].startswith(f"a file of exactly {PIN.size}")


def test_the_pinned_revision_is_the_publishers(tmp_path: Path) -> None:
    assert (PINNED.dataset, PINNED.revision, PINNED.size) == (
        "isaacus/open-australian-legal-corpus", "ef45e3fec41a960919a31149eee6dab9aa39f725", 9_401_179_433)
    assert PINNED.sha256 == "5abc64a3ad35a44eca65706babd63d1095c29ebebf3f5c588beb56612097b758"
    assert PINNED.url == ("https://huggingface.co/datasets/isaacus/open-australian-legal-corpus/resolve/"
                          "ef45e3fec41a960919a31149eee6dab9aa39f725/corpus.jsonl")
    out = run(AustralianLegalCorpusBlock(folder=tmp_path, free=lambda p: 1), "fetch")
    blocked(out, block_module.DISK_SHORT)   # 18.8 GB needed: the file and as much again for the index
    assert out["would_fetch"]["needs_free_bytes"] == 2 * 9_401_179_433 and "doi:10.57967/hf/2784" in ATTRIBUTION


def test_too_little_disk_is_refused_by_name_for_a_dry_run_and_a_real_fetch(tmp_path: Path) -> None:
    http_client, streams = client({})
    block = make(tmp_path, client=http_client, gate=allow, free=lambda path: 2 * PIN.size - 1)
    for dry in (True, False):
        out = run(block, "fetch", dry_run=dry)
        blocked(out, block_module.DISK_SHORT)
        assert "as much again for the index" in out["warning_card"]["message"]
    assert streams.sent == []


def test_today_the_action_gate_holds_the_download_for_a_person(tmp_path: Path) -> None:
    http_client, streams = client({})
    out = run(make(tmp_path, client=http_client), "fetch", dry_run=False)
    blocked(out, block_module.HELD)
    assert streams.sent == [] and "register" in out["warning_card"]["message"]
    assert out["person_can_run"] == PIN.command(str(tmp_path / "state"))


def test_g4_a_gate_that_cannot_decide_holds_the_download(tmp_path: Path) -> None:
    def broken(action: dict, key: str | None) -> dict:
        raise RuntimeError("the gate broke")

    http_client, streams = client({})
    out = run(make(tmp_path, client=http_client, gate=broken), "fetch", dry_run=False)
    blocked(out, block_module.HELD)
    assert out["gate"]["decision"] == "block" and streams.sent == []


def test_an_allowed_fetch_follows_the_cdn_redirect_checks_the_hash_and_the_corpus_is_searchable(tmp_path: Path) -> None:
    cdn = "https://cdn.example/blob?sig=1"
    http_client, streams = client({PIN.url: (302, {"location": cdn}, b""), cdn: (200, {}, DATA)})
    block = make(tmp_path, client=http_client, gate=allow)
    out = run(block, "fetch", dry_run=False)
    assert out["code"] == block_module.FETCHED and streams.sent == [PIN.url, cdn]
    assert out["file"]["how"] == "fetched" and (tmp_path / "state" / "corpus.jsonl").read_bytes() == DATA
    assert run(block, "fetch", dry_run=False)["code"] == block_module.PRESENT and len(streams.sent) == 2
    assert run(block, "build")["complete"] is True
    assert run(block, "search", query="widget label")["hits"][0]["citation"] == "Widget Labelling Act 2031 (Cth)"


def test_a_fetch_whose_hash_differs_keeps_nothing(tmp_path: Path) -> None:
    http_client, _ = client({PIN.url: (200, {}, DATA[:-2] + b"X\n")})
    out = run(make(tmp_path, client=http_client, gate=allow), "fetch", dry_run=False)
    blocked(out, block_module.FETCH_FAILED)
    assert out["http_code"] == http.MISMATCH and sorted(p.name for p in (tmp_path / "state").iterdir()) == []


def test_g9_a_redirect_to_this_machine_is_refused_and_a_403_is_an_error(tmp_path: Path) -> None:
    http_client, streams = client({PIN.url: (302, {"location": "http://127.0.0.1/corpus.jsonl"}, b"")})
    out = run(make(tmp_path, client=http_client, gate=allow), "fetch", dry_run=False)
    blocked(out, block_module.FETCH_FAILED)
    assert out["http_code"] == http.REFUSED and streams.sent == [PIN.url]
    http_client, _ = client({PIN.url: (403, {}, b"denied")})
    out = run(make(tmp_path, client=http_client, gate=allow), "fetch", dry_run=False)
    blocked(out, block_module.FETCH_FAILED)
    assert out["http_code"] == http.STATUS and not (tmp_path / "state" / "corpus.jsonl").exists()


# --- register ---------------------------------------------------------------------------------------------------------

def test_register_records_the_file_in_place_without_copying_it(tmp_path: Path) -> None:
    block = make(tmp_path)
    path = download(tmp_path)
    out = run(block, "register", path=str(path))
    assert out["code"] == block_module.REGISTERED and out["file"]["path"] == str(path.resolve())
    assert sorted(p.name for p in (tmp_path / "state").iterdir()) == ["manifest.json"]
    status = run(block, "status")
    assert status["file"]["how"] == "registered" and status["file"]["size_and_time_unchanged"] is True


def test_register_refuses_any_other_file_and_says_nothing_about_it(tmp_path: Path) -> None:
    block = make(tmp_path)
    same_size = download(tmp_path, DATA[:-2] + b"X\n")
    other = tmp_path / "other.jsonl"
    other.write_bytes(b"{}\n")
    said = set()
    for path in (same_size, other, tmp_path / "missing.jsonl", tmp_path):
        out = run(block, "register", path=str(path))
        blocked(out, block_module.NOT_PINNED)
        said.add(out["warning_card"]["message"])
    assert len(said) == 1 and not (tmp_path / "state").exists()
    blocked(run(block, "register", path="corpus.jsonl"), block_module.INPUT)   # a relative path


# --- build ------------------------------------------------------------------------------------------------------------

def test_build_is_bounded_by_records_and_resumes_from_its_offset(tmp_path: Path) -> None:
    block = registered(tmp_path)
    first = run(block, "build", max_records=3)
    assert first["complete"] is False and first["this_call"]["records_read"] == 3 and first["this_call"]["stopped"] == "records"
    offsets, outs = [first["index"]["offset"]], [first]
    while not outs[-1]["complete"]:
        outs.append(run(block, "build", max_records=3))
        offsets.append(outs[-1]["index"]["offset"])
    assert offsets == sorted(offsets) and offsets[-1] == PIN.size
    once = built(tmp_path / "again")
    assert outs[-1]["index"]["documents"] == run(once, "status")["index"]["documents"] == 3


def test_build_is_bounded_by_seconds(tmp_path: Path) -> None:
    ticks = iter(range(10_000))
    block = registered(tmp_path, clock=lambda: float(next(ticks)))
    out = run(block, "build", max_seconds=2)
    assert out["this_call"]["stopped"] == "seconds" and out["this_call"]["records_read"] < len(LINES)


def test_a_bad_line_is_counted_and_skipped_never_fatal(tmp_path: Path) -> None:
    out = run(registered(tmp_path), "build")
    assert out["complete"] is True and out["this_call"]["skipped"] == {
        "too_long": 1, "not_json": 1, "bad_record": 1, "unpinned_value": 1, "filtered": 2, "repeated": 1}
    assert out["this_call"]["documents"] == 3


def test_decisions_are_opt_in_and_the_filters_cannot_change_mid_index(tmp_path: Path) -> None:
    block = registered(tmp_path)
    run(block, "build", max_records=2)
    blocked(run(block, "build", types=EVERYTHING), block_module.FILTERS_DIFFER)
    assert run(block, "build")["index"]["types"] == ["primary_legislation", "secondary_legislation"]
    opted = built(tmp_path / "decisions", types=EVERYTHING)
    hits = run(opted, "search", query="claim succeeds", types=("decision",))["hits"]
    assert [h["citation"] for h in hits] == ["Placeholder Widgets v Fixture Lanterns [2099] NSWSC 1"]


def test_a_file_changed_after_its_hash_was_checked_is_not_built_from(tmp_path: Path) -> None:
    block = registered(tmp_path)
    path = tmp_path / "download" / "corpus.jsonl"
    path.write_bytes(DATA + b"\n")
    out = run(block, "build")
    blocked(out, block_module.NO_CORPUS)
    assert "has changed since its hash was checked" in out["warning_card"]["message"]


def test_low_disk_stops_the_build_and_keeps_its_progress(tmp_path: Path) -> None:
    blocked(run(registered(tmp_path, free=lambda path: store.BUILD_FLOOR - 1), "build"), block_module.DISK_SHORT)


# --- search -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("query", ['tenancy OR "bond', "NEAR(tenancy bond)", "bond*", "text: bond", "-lantern ^bond",
                                   "{jurisdiction}: commonwealth", "'; DROP TABLE passages; --"])
def test_the_callers_words_are_quoted_terms_never_fts5_syntax(tmp_path: Path, query: str) -> None:
    out = run(built(tmp_path), "search", query=query, match="any")
    assert out["code"] in (block_module.FOUND, block_module.NO_MATCH), out["warning_card"]


def test_each_hit_carries_its_fields_ranked_by_bm25(tmp_path: Path) -> None:
    out = run(built(tmp_path, types=EVERYTHING), "search", query="widget register kite", match="any", k=4)
    hits = out["hits"]
    assert 0 < len(hits) <= 4 and [h["rank"] for h in hits] == list(range(1, len(hits) + 1))
    assert [h["bm25"] for h in hits] == sorted(h["bm25"] for h in hits)
    for h in hits:
        assert {"passage", "citation", "jurisdiction", "type", "url", "version_id", "date", "when_scraped",
                "register", "terms"} <= set(h) and h["passage"]
    bill = [h for h in run(built(tmp_path / "b", types=EVERYTHING), "search", query="kite")["hits"]]
    assert bill[0]["type"] == "bill" and bill[0]["note"] == "a bill: proposed law, not law"


def test_a_search_that_runs_too_long_is_refused_as_too_broad(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    block = built(tmp_path)
    monkeypatch.setattr(store, "PROGRESS_TICKS", 1)
    block.search_seconds = -1.0
    blocked(run(block, "search", query="widget"), store.TOO_BROAD)


# --- remove and status ------------------------------------------------------------------------------------------------

def test_remove_is_a_dry_run_first_and_never_deletes_a_registered_file(tmp_path: Path) -> None:
    block = built(tmp_path)
    dry = run(block, "remove")
    assert dry["code"] == block_module.REMOVE_DRY_RUN and (tmp_path / "state" / "index.sqlite3").exists()
    out = run(block, "remove", dry_run=False)
    assert out["code"] == block_module.REMOVED and out["kept"] == [str((tmp_path / "download" / "corpus.jsonl").resolve())]
    assert (tmp_path / "download" / "corpus.jsonl").exists() and list((tmp_path / "state").iterdir()) == []
    blocked(run(block, "search", query="widget"), block_module.NO_INDEX)


def test_remove_deletes_a_file_g6_downloaded_and_what_index_keeps_it(tmp_path: Path) -> None:
    http_client, _ = client({PIN.url: (200, {}, DATA)})
    block = make(tmp_path, client=http_client, gate=allow)
    run(block, "fetch", dry_run=False)
    run(block, "build")
    run(block, "remove", what="index", dry_run=False)
    assert sorted(p.name for p in (tmp_path / "state").iterdir()) == ["corpus.jsonl", "manifest.json"]
    run(block, "remove", dry_run=False)
    assert list((tmp_path / "state").iterdir()) == []


def test_status_and_search_reach_nothing_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    block = built(tmp_path)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("no connection may be opened")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    status = run(block, "status")
    assert status["index"]["complete"] is True and status["next_step"] == "search"
    assert status["dataset"]["revision"] == PIN.revision and run(block, "search", query="widget")["hits"]


# --- the gates --------------------------------------------------------------------------------------------------------

def test_g1_nothing_it_says_is_verified(tmp_path: Path) -> None:
    http_client, _ = client({PIN.url: (200, {}, DATA)})
    block = make(tmp_path, client=http_client, gate=allow)
    replies = [run(block, "status"), run(block, "fetch"), run(block, "fetch", dry_run=False), run(block, "build"),
               run(block, "search", query="widget"), run(block, "get", version_id="cth-1"), run(block, "remove")]
    assert {r["completion_state"] for r in replies} == {"qualified-draft"}


@pytest.mark.parametrize("field", ["approved", "verified", "reviewer", "authority", "current"])
def test_g3_a_callers_word_is_refused_as_a_field(field: str) -> None:
    with pytest.raises(ValidationError):
        AustralianLegalCorpusInput(op="fetch", dry_run=False, **{field: True})


def test_g5_fetch_and_remove_are_sensitive() -> None:
    assert package.__g6_sensitive_ops__ == ("fetch", "remove")
    for op in ("fetch", "remove"):
        said = str(get_registry().invoke("australian_legal_corpus", op, {}, block_sensitive=True).failure())
        assert said.startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")


def test_an_unknown_op_is_the_shared_refusal() -> None:
    said = str(get_registry().invoke("australian_legal_corpus", "serch", {"query": "x"}).failure())
    assert said.startswith("[G6_E_UNKNOWN_OP]") and "did you mean 'search'" in said


# --- D2, D6 -----------------------------------------------------------------------------------------------------------

def test_d2_the_failure_modes_are_registered_and_no_longer_planned() -> None:
    assert package.__g6_mitigates__ == ("answered_from_memory", "stale_context_used", "wrong_jurisdiction")
    assert all(FailureModeRegistry.is_registered(c) for c in (*package.__g6_mitigates__, *package.__g6_introduces__))
    assert "g6.sources.australian_legal_corpus" not in FailureModeRegistry.planned()


def test_d6_over_mcp() -> None:
    info = describe_reply("australian_legal_corpus")
    assert info["skill"] and info["sensitive_ops"] == ["fetch", "remove"]
    assert info["outside"][0]["name"] == "Open Australian Legal Corpus" and info["outside"][0]["online"] is True
    reply = invoke_reply("australian_legal_corpus", "status", "{}")
    assert reply["ok"] and reply["result"]["dataset"]["revision"] == PINNED.revision
    assert invoke_reply("australian_legal_corpus", "fetch", "{}")["ok"] is False
    searched = invoke_reply("australian_legal_corpus", "search", json.dumps({"query": "bond", "jurisdiction": "Vic"}))
    assert searched["result"]["code"] == block_module.NOT_COVERED_CODE


@pytest.mark.parametrize("path", [
    r"\\host\share\corpus.jsonl", "//host/share/corpus.jsonl",
    r"\\?\UNC\host\share\corpus.jsonl", r"\\?\C:\corpus.jsonl", r"\\.\C:\corpus.jsonl",
    r"C:\download\NUL", r"C:\download\con.txt",
])
def test_register_refuses_network_and_device_paths_before_filesystem(tmp_path: Path, monkeypatch, path: str) -> None:
    # RED: remove block.py's path = confined_path(d.path.strip(), (path,)) guard.
    import builtins
    import os
    from unittest.mock import Mock

    block = make(tmp_path)
    spies = [Mock(side_effect=AssertionError("filesystem touched")) for _ in range(5)]
    with monkeypatch.context() as patch:
        for (owner, name), spy in zip(
                [(os, "stat"), (Path, "stat"), (Path, "is_file"), (Path, "resolve"), (builtins, "open")], spies):
            patch.setattr(owner, name, spy)
        out = run(block, "register", path=path)
    blocked(out, block_module.INPUT)
    assert all(spy.call_count == 0 for spy in spies)


@pytest.mark.parametrize("op", ["register", "build"])
@pytest.mark.parametrize("dry", [True, False])
def test_register_and_build_reject_the_inapplicable_dry_run_field(op: str, dry: bool) -> None:
    # RED: remove block.py's raise ValueError("dry_run is only accepted by fetch and remove").
    with pytest.raises(ValidationError, match="dry_run"):
        AustralianLegalCorpusInput(op=op, dry_run=dry)


def test_missing_fts5_refuses_with_install_steps_and_closes_setup(tmp_path: Path, monkeypatch) -> None:
    # RED: remove store.py's conn.close() in _open's setup-error handler.
    import sqlite3

    block = registered(tmp_path)
    real_open = store.open_db
    opened = []

    class WithoutFTS5:
        def __init__(self, conn):
            self.conn, self.closed = conn, False

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def execute(self, sql, *args):
            if "CREATE VIRTUAL TABLE" in sql:
                raise sqlite3.OperationalError("no such module: fts5")
            return self.conn.execute(sql, *args)

        def close(self):
            self.closed = True
            self.conn.close()

    def open_without_fts5(*args, **kwargs):
        conn = WithoutFTS5(real_open(*args, **kwargs))
        opened.append(conn)
        return conn

    monkeypatch.setattr(store, "open_db", open_without_fts5)
    out = run(block, "build", request_id="req-fts", run_id="run-fts")
    blocked(out, "G6_E_LEGAL_CORPUS_FTS5_UNAVAILABLE")
    assert "Python" in out["warning_card"]["message"] and "FTS5" in out["warning_card"]["message"]
    assert out["request_id"] == "req-fts" and out["run_id"] == "run-fts"
    assert len(opened) == 1 and opened[0].closed


@pytest.mark.parametrize("failure", ["sqlite", "os"])
def test_build_disk_full_keeps_only_committed_progress_and_can_resume(tmp_path: Path, monkeypatch, failure) -> None:
    # RED: remove block.py's except (OSError, sqlite3.Error) storage-refusal handler.
    import sqlite3

    block = registered(tmp_path)
    take = store.Index._take
    monkeypatch.setattr(store, "_BATCH", 1)
    calls = 0

    def disk_full(index, conn, line, kinds, places, progress):
        nonlocal calls
        calls += 1
        take(index, conn, line, kinds, places, progress)
        if calls == 2:
            if failure == "os":
                raise OSError(block_module.errno.ENOSPC, "disk full")
            raise sqlite3.OperationalError("database or disk is full")

    with monkeypatch.context() as patch:
        patch.setattr(store.Index, "_take", disk_full)
        out = run(block, "build", request_id="req-disk", run_id="run-disk")
    blocked(out, block_module.DISK_SHORT)
    assert out["request_id"] == "req-disk" and out["run_id"] == "run-disk"
    meta = store.Index(block.folder).meta()
    assert meta["lines"] == meta["indexed"] == 1 and meta["complete"] is False
    assert meta["offset"] == len((LINES[0] + "\n").encode())
    assert run(block, "search", query="tenancy bond")["code"] == block_module.NO_MATCH
    assert run(block, "build")["complete"] is True
    assert run(block, "search", query="tenancy bond")["hits"]


def test_build_read_failure_keeps_committed_progress(tmp_path: Path, monkeypatch) -> None:
    # RED: remove block.py's except (OSError, sqlite3.Error) storage-refusal handler.
    import builtins

    block = registered(tmp_path)
    real_open = builtins.open
    monkeypatch.setattr(store, "_BATCH", 1)

    class BrokenReader:
        def __init__(self, file):
            self.file, self.reads = file, 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def seek(self, *args):
            return self.file.seek(*args)

        def tell(self):
            return self.file.tell()

        def readline(self, *args):
            self.reads += 1
            if self.reads == 2:
                raise OSError("corpus read failed")
            return self.file.readline(*args)

    def open_file(path, mode="r", *args, **kwargs):
        file = real_open(path, mode, *args, **kwargs)
        return BrokenReader(file) if mode == "rb" else file

    with monkeypatch.context() as patch:
        patch.setattr(store, "open", open_file, raising=False)
        out = run(block, "build")
    blocked(out, "G6_E_LEGAL_CORPUS_STORAGE_FAILED")
    assert "read" in out["warning_card"]["message"]
    meta = store.Index(block.folder).meta()
    assert meta["lines"] == meta["indexed"] == 1
    assert meta["offset"] == len((LINES[0] + "\n").encode())
    assert run(block, "build")["complete"] is True
