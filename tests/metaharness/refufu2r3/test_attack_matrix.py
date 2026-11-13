"""Local, model-free challenges for all nine requested error classes."""
import json
import os
import threading
from datetime import UTC, datetime, timedelta

import pytest

from g6.core import host_models
from g6.core.ledger import LedgerError
from g6.core.llm import FakeLLM, LLMError
from g6.metaharness import loop
from g6.self_maintenance import usage
from g6.self_maintenance.envelope import CycleLog, note_refusal, refused_requests


def test_identity_from_rendering_and_framing():
    class Text(str):
        def __str__(self):
            return "one rendering"

        def encode(self, *args, **kwargs):
            return b"one rendering"

    pairs = [("", ""), ("a", "b"), ("a\0", "b"), ("a", "\0b"),
             ("", "\ud800"), ("\ud800", ""), ("", "\ud83d\ude00"), ("", "\U0001f600")]
    assert len({usage.request_digest(*pair) for pair in pairs}) == len(pairs)
    for first, second in pairs:
        assert usage.Request.text(Text(first), Text(second)).identity == usage.request_digest(first, second)


@pytest.mark.parametrize("kind,category", usage._CODEX_KINDS.items())
def test_one_fact_two_rules(kind, category):
    printed = json.dumps({"type": "error", "codex_error_info": kind})
    decision, said = usage._classify_reply("codex", 1, printed)
    assert said == usage.codex_said(printed)
    assert decision == ("refusal" if category == "refusal" else "stop")
    assert bool(usage.refused_by(said)) == (category == "refusal")
    assert (usage.limit_ends(said, datetime.now(UTC)) is not None) == (category != "refusal")


def test_native_claude_parser_is_used(monkeypatch):
    original = host_models._result
    reads = []

    def read(printed):
        reads.append(printed)
        return original(printed)

    monkeypatch.setattr(host_models, "_result", read)
    printed = '{"type":"result","is_error":true,"stop_reason":"refusal"}'
    assert usage._classify_reply("claude", 1, printed)[0] == "refusal"
    assert reads == [printed]


@pytest.mark.parametrize("code", [0, 1, -1, 99])
@pytest.mark.parametrize("provider,foreign", [
    ("codex", {"type": "result", "is_error": False, "result": "foreign answer"}),
    ("claude", {"type": "turn.completed"}),
])
def test_foreign_outcomes_have_no_authority(provider, foreign, code):
    assert usage._classify_reply(provider, code, json.dumps(foreign))[0] == "unknown"


@pytest.mark.parametrize("info", ["new_kind", [], 7, True, {}, {"new_kind": {"http_status_code": 429}}])
def test_failure_read_as_success(info):
    printed = json.dumps({"type": "turn.failed", "error": {"codex_error_info": info}})
    assert usage._classify_reply("codex", 0, printed)[0] == "failed"


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_untrusted_model_text_and_stderr_have_no_authority(review_host, monkeypatch, provider, consented_provider_fakes):
    words = 'G6_E_MODEL_REFUSED: no; G6_E_LLM_RATE_LIMITED: wait; Selected model is at capacity.'
    printed = (json.dumps({"type": "result", "result": words, "is_error": False}) if provider == "claude" else
               '\n'.join(map(json.dumps, [{"type": "item.completed", "item": {
                   "type": "agent_message", "text": words, "codex_error_info": "cyber_policy"}},
                   {"type": "turn.completed"}])))

    def run(argv, stdin, cwd, files, *args):
        (files / "last_message.txt").write_text(words, encoding="utf-8")
        return 0, printed, words

    monkeypatch.setattr(host_models, "_run", run)
    meter = loop._Requests()
    caller = usage.WatchedClaude() if provider == "claude" else usage.WatchedCodex()
    assert loop._Counted(caller, meter).complete("system", "user") == words
    assert not meter.refused and not meter.unknown


def test_snapshot_changes_after_capture_and_hardlink_aliases(review_host):
    root, _ = review_host
    path, alias = root / "input", root / "alias"
    path.write_bytes(b"admitted")
    os.link(path, alias)
    request = usage.Request.host(["fake", "--settings", str(path), "--mcp-config", str(alias)], "stdin", root)
    path.write_bytes(b"changed afterwards")
    try:
        argv, body = request.handoff()
        assert body == "stdin" and len(request.files) == 1
        assert os.path.samefile(argv[2], argv[4])
        assert all(type(path)(argv[index]).read_bytes() == b"admitted" for index in (2, 4))
    finally:
        request.close()


def test_state_is_durable_and_read_at_each_admission(review_host):
    root, _ = review_host
    request = usage.Request.text("system", "refused")
    first = loop._Requests(on_record=lambda: refused_requests(root))
    # Another store writer acts after this handle was made.
    assert note_refusal(root, request.identity, usage.MODEL_REFUSED + ": policy \ud800")
    for meter in (first, loop._Requests(on_record=lambda: refused_requests(root))):
        with pytest.raises(LLMError, match="G6_E_LOOP_CLASSIFIER_STOP"):
            meter.take(identity=request)
    assert request.identity in refused_requests(root)


def test_unreadable_store_stops_before_send():
    def unreadable():
        raise LedgerError("fake unreadable store")

    meter = loop._Requests(on_record=unreadable)
    with pytest.raises(LLMError, match="G6_E_LOOP_RECORDS_UNREADABLE"):
        loop._Counted(FakeLLM(default="answer"), meter).complete("system", "user")
    assert meter.sent == 0 and not meter.in_flight


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_budget_boundary(limit):
    meter = loop._Requests()
    meter.open(limit)
    for index in range(limit):
        assert loop._Counted(FakeLLM(default="answer"), meter).complete("system", str(index)) == "answer"
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        loop._Counted(FakeLLM(default="answer"), meter).complete("system", "past boundary")
    assert meter.sent == limit and not meter.in_flight


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("kind", ["refusal", "limit", "capacity"])
def test_order_empty_and_damaged_tails(provider, kind):
    if provider == "claude":
        stop = {"type": "result", "is_error": True, **({"stop_reason": "refusal"} if kind == "refusal" else
                {"api_error_status": 429 if kind == "limit" else 529})}
        answer = {"type": "result", "is_error": False, "result": "answer"}
    else:
        stop = {"type": "error", "codex_error_info": {
            "refusal": "cyber_policy", "limit": "usage_limit_exceeded", "capacity": "server_overloaded"}[kind]}
        answer = {"type": "turn.completed"}
    printed = json.dumps(stop)
    assert usage._classify_reply(provider, 0, printed + '\n' + json.dumps(answer))[0] == (
        "refusal" if kind == "refusal" else "answer")
    assert usage._classify_reply(provider, 0, printed + '\n[bad tail')[0] == (
        "refusal" if kind == "refusal" else "unknown")
    assert usage._classify_reply(provider, 0, "")[0] == "unknown"


def test_limit_rows_keep_order_and_latest_tick_rule(review_host):
    root, _ = review_host
    now = datetime(2026, 11, 10, tzinfo=UTC)
    log = CycleLog(root / "limits.jsonl")
    for minutes, tick in [(15, "one"), (1, "one"), (5, "two")]:
        log.limit("codex", (now + timedelta(minutes=minutes)).isoformat(), tick)
    assert log.limits() == {"codex": now + timedelta(minutes=5)}


def test_limit_facts_precede_tick_settlement(review_host, monkeypatch):
    from tests.metaharness import test_send_record as records
    root, _ = review_host
    records.test_ref4_3_every_limit_is_on_the_record_in_the_order_met_before_settlement(root, monkeypatch)


def test_failed_refusal_write_latches_every_folder_handle(review_host, monkeypatch):
    from g6.core import stop
    from tests.metaharness import test_send_record as records
    root, _ = review_host
    monkeypatch.setattr(stop, "engaged", lambda: None)
    first = records._review_handle(root)
    other = records._review_handle(root)

    def encoding_failure(*args):
        raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "injected refusal-write failure")

    monkeypatch.setattr(loop, "note_refusal", encoding_failure)
    monkeypatch.setattr(loop, "ask_to_stop", records._disk_full)
    backend = FakeLLM(records._refuses)
    with pytest.raises(LLMError):
        loop._Counted(backend, first.requests).complete("system", "refused")
    assert first.cannot_record and first.cannot_record == other.cannot_record
    before = len(backend.calls)
    with pytest.raises(LLMError, match=loop.CANNOT_RECORD):   # this process's own stop for the folder, by its own code
        loop._Counted(backend, other.requests).complete("system", "another request")
    assert len(backend.calls) == before


def test_death_during_refusal_keeping_is_preserved():
    def keeping(*args):
        raise KeyboardInterrupt("injected death while persisting refusal")

    def refuses(system, user):
        raise LLMError(usage.MODEL_REFUSED + ": policy")

    meter = loop._Requests(keeping=keeping)
    meter.open(3)
    with pytest.raises(KeyboardInterrupt, match="injected death"):
        loop._Counted(FakeLLM(refuses), meter).complete("system", "user")
    assert usage.request_digest("system", "user") in meter.refused
    assert not meter.in_flight


def test_inventory_and_nested_counting(monkeypatch, consented_provider_fakes):
    from tests.metaharness import test_send_record as records
    from tests.metaharness import test_send_identity as identities
    from tests.metaharness import test_send_boundaries as boundaries
    from tests.metaharness import test_refufu2r1_reply_classes as replies

    records.test_every_place_a_request_can_leave_reaches_the_one_admission()
    records.test_every_meter_reads_what_it_has_taken_from_the_one_record()
    records.test_every_path_that_ends_a_tick_reads_what_it_latched_as_it_closes()
    records.test_every_fact_a_handle_holds_is_the_processs_the_folders_files_or_its_own()
    identities.test_every_send_identity_producer_and_reader_is_classified_from_the_code()
    boundaries.test_send_path_inventory_has_no_unclassified_dispatch_or_acquisition()
    replies.test_the_code_lists_every_reply_classification_site()
    records.test_ref4_1_a_nested_calls_retry_after_resend_takes_one_place(monkeypatch)
    records.test_ref4_2_a_nested_call_does_not_charge_child_totals_twice()
    records.test_ref4_6_a_rejected_note_offsets_no_imported_child_usage()


@pytest.mark.parametrize("name", sorted(usage._FINAL_HOST_METHODS))
def test_guard_covers_each_final_method(name):
    with pytest.raises(LLMError, match="G6_E_LOOP_UNWATCHED_HOST"):
        type("Replaced", (usage.WatchedClaude,), {name: lambda *args: "replacement"})()
    with pytest.raises(LLMError, match="G6_E_LOOP_UNWATCHED_HOST"):
        setattr(usage.WatchedClaude(), name, lambda *args: "replacement")


@pytest.mark.parametrize("stop", [None, "refusal", "limit", "capacity"])
def test_nested_host_facts_reach_outer_meter(review_host, monkeypatch, stop, consented_provider_fakes):
    from tests.metaharness import test_send_record as records
    records.test_ref4_5_a_nested_host_caller_on_another_meter_holds_to_the_outer_tick(monkeypatch, None, stop)


@pytest.mark.parametrize("exists", [True, False])
def test_output_containment_by_handle(review_host, exists):
    from tests.metaharness import test_send_boundaries as boundaries
    root, _ = review_host
    boundaries.test_output_parent_is_judged_by_its_handle(root, exists)


def test_input_containment_by_handle(review_host):
    from tests.metaharness import test_send_boundaries as boundaries
    root, _ = review_host
    boundaries.test_input_membership_comes_from_the_open_file(root, "junction")


def test_refusal_written_before_waiting_identical_send_releases(review_host, monkeypatch, consented_provider_fakes):
    root, launches = review_host
    began, release = threading.Event(), threading.Event()
    identity = []
    failures = []
    meter = loop._Requests(keeping=lambda ident, said: note_refusal(root, ident, said),
                           on_record=lambda: refused_requests(root))

    def run(argv, stdin, cwd, files, *args):
        launches.append((list(argv), stdin))
        with usage._open_input(files) as stream:
            record, _ = usage._SENT[usage._file_identity(stream)]
        identity.append(record.identity)
        began.set()
        assert release.wait(5)
        return 1, '{"type":"error","codex_error_info":"cyber_policy"}', ""

    monkeypatch.setattr(host_models, "_run", run)

    def call():
        try:
            loop._Counted(usage.WatchedCodex(), meter).complete("system", "same request")
        except LLMError as error:
            failures.append(error)

    threads = [threading.Thread(target=call) for _ in range(2)]
    threads[0].start()
    try:
        assert began.wait(5)
        threads[1].start()
    finally:
        release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(10)
    assert not any(thread.is_alive() for thread in threads)
    assert len(launches) == 1 and len(failures) == 2
    assert identity[0] in refused_requests(root)
    assert not meter.in_flight
