"""Additional review probes covering every listed design-error class."""
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from g6.core import host_models
from g6.core.consent_callers import CallerWrapper
from g6.core.llm import FakeLLM, LLMError
from g6.metaharness import loop
from g6.self_maintenance import usage
from g6.self_maintenance.envelope import CycleLog, note_refusal, refused_requests


def test_identity_from_bytes_even_when_rendering_lies():
    class Render(str):
        def __str__(self):
            return "same rendering"

        def encode(self, *args, **kwargs):
            return b"same rendering"

    assert str(Render("first")) == str(Render("second"))
    assert usage.request_digest("s", Render("first")) != usage.request_digest("s", Render("second"))
    assert usage.request_digest("s", Render("first")) == usage.request_digest("s", "first")
    pairs = [("", ""), ("s", "u"), ("a\0b", "c"), ("a", "b\0c"),
             ("", "\ud800"), ("\ud800", ""), ("", "\ud83d\ude00"), ("", "\U0001f600")]
    assert len({usage.request_digest(*pair) for pair in pairs}) == len(pairs)
    assert usage.request_digest("s", "u") == hashlib.sha256(b"s\0u").hexdigest()


@pytest.mark.parametrize("kind,expected", list(usage._CODEX_KINDS.items()))
def test_one_stop_rule_for_each_codex_kind(kind, expected):
    printed = json.dumps({"type": "error", "codex_error_info": kind})
    said = usage.codex_said(printed)
    classification, classified_said = usage._classify_reply("codex", 1, printed)
    assert classified_said == said
    if expected == "refusal":
        assert classification == "refusal" and usage.refused_by(said)
    else:
        assert classification == "stop" and usage.limit_ends(said, datetime.now(UTC)) is not None


@pytest.mark.parametrize("info", ["new_kind", "cyber_policy_extra", {}, [], 1, True,
                                  {"response_too_many_failed_attempts": {"http_status_code": 429}}])
def test_unknown_classification_does_not_borrow_message_authority(info):
    printed = json.dumps({"type": "error", "codex_error_info": info,
                          "message": "This content was flagged for possible cybersecurity risk"})
    assert usage._classify_reply("codex", 1, printed)[0] == "failed"
    assert not usage.codex_said(printed)


@pytest.mark.parametrize("printed", ["", "[]", "null", '"text"', "{broken", "{}", '{"type":"turn.started"}'])
def test_missing_or_unreadable_success_is_unknown(printed):
    assert usage._classify_reply("codex", 0, printed)[0] == "unknown"


def test_refusal_wins_completion_and_limit_only_yields_to_completion():
    completed = {"type": "turn.completed"}
    for kind in ("cyber_policy", "usage_limit_exceeded", "server_overloaded"):
        error = {"type": "error", "codex_error_info": kind}
        events = "\n".join(map(json.dumps, [error, completed]))
        assert usage._classify_reply("codex", 0, events)[0] == ("refusal" if kind == "cyber_policy" else "answer")
        later_error = events + '\n{"type":"error","message":"unknown error"}'
        assert usage._classify_reply("codex", 0, later_error)[0] == ("refusal" if kind == "cyber_policy" else "failed")


@pytest.mark.parametrize("words", [usage.MODEL_REFUSED + ": no", usage.AT_CAPACITY + ": wait",
                                  "G6_E_LLM_RATE_LIMITED: wait"])
def test_model_text_and_stderr_cannot_decide_a_stop(fake_host, monkeypatch, words, consented_provider_fakes):
    def run(argv, stdin, cwd, files, *args):
        (files / "last_message.txt").write_text(words, encoding="utf-8")
        events = [{"type": "item.completed", "item": {"type": "agent_message", "text": words}},
                  {"type": "turn.completed"}]
        return 0, "\n".join(map(json.dumps, events)), words

    monkeypatch.setattr(host_models, "_run", run)
    meter = loop._Requests()
    assert loop._Counted(usage.WatchedCodex(), meter).complete("s", "u") == words
    assert not meter.refused and not meter.unknown


def test_claude_uses_the_producers_result_reader(monkeypatch):
    original = host_models._result
    reads = []

    def read(printed):
        reads.append(printed)
        return original(printed)

    monkeypatch.setattr(host_models, "_result", read)
    printed = '{"type":"result","is_error":true,"stop_reason":"refusal"}'
    assert usage._classify_reply("claude", 1, printed)[0] == "refusal"
    assert reads == [printed]


def test_snapshot_is_private_and_hardlink_capture_is_once(review_dir):
    source, alias = review_dir / "input", review_dir / "alias"
    source.write_bytes(b"admitted")
    os.link(source, alias)
    request = usage.Request.host(["fake", "--settings", str(source), "--mcp-config", str(alias)], "stdin", review_dir)
    source.write_bytes(b"changed after admission")
    try:
        argv, body = request.handoff()
        assert body == "stdin" and len(request.files) == 1
        assert os.path.samefile(argv[2], argv[4])
        assert all(opened.read_bytes() == b"admitted" for opened in map(type(source), [argv[2], argv[4]]))
    finally:
        request.close()


def test_input_containment_rejects_directory_junction(review_dir):
    root, outside = review_dir / "root", review_dir / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "input").write_bytes(b"outside")
    junction = root / "alias"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(junction))
    else:
        junction.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(LLMError, match="G6_E_LOOP_INPUT_CHANGED"):
            usage.Request.host(["fake", "--settings", str(junction / "input")], "", root)
    finally:
        junction.rmdir() if os.name == "nt" else junction.unlink()


def test_refusal_is_durable_and_store_is_reread(review_dir):
    request = usage.Request.text("s", "u")
    assert note_refusal(review_dir, request.identity, usage.MODEL_REFUSED + ": policy")
    meter = loop._Requests(on_record=lambda: refused_requests(review_dir))
    with pytest.raises(LLMError):
        meter.take(identity=request)
    seen = []

    class Refuses(CallerWrapper):     # hands each request to G6's local test caller, by the published wrapper contract
        def __init__(self):
            super().__init__(FakeLLM(self.refuse))

        def refuse(self, system, user):     # FakeLLM notes the model call before it asks this
            seen.append(user)
            raise LLMError(usage.MODEL_REFUSED + ": policy")

    for _ in range(2):
        restarted = loop._Requests(keeping=lambda identity, said: note_refusal(review_dir, identity, said),
                                   on_record=lambda: refused_requests(review_dir))
        with pytest.raises(LLMError):
            loop._Counted(Refuses(), restarted).complete("s", "new request")
    assert seen == ["new request"]


def test_limits_keep_the_furthest_per_tick_and_replace_on_new_tick(review_dir):
    now = datetime(2026, 11, 9, tzinfo=UTC)
    log = CycleLog(review_dir / "limits.jsonl")
    for minutes, tick in [(15, "one"), (1, "one"), (5, "two")]:
        log.limit("codex", (now + timedelta(minutes=minutes)).isoformat(), tick)
    assert log.limits()["codex"] == now + timedelta(minutes=5)


def test_nested_meters_and_retry_after_are_not_double_charged(monkeypatch, consented_provider_fakes):
    from tests.metaharness import test_send_record as inventory
    inventory.test_ref4_1_a_nested_calls_retry_after_resend_takes_one_place(monkeypatch)
    inventory.test_ref4_2_a_nested_call_does_not_charge_child_totals_twice()


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_reservation_boundary(limit):
    meter = loop._Requests()
    meter.open(limit)
    for _ in range(limit):
        assert loop._Counted(FakeLLM(default="answer"), meter).complete("s", "u") == "answer"
    with pytest.raises(LLMError):
        loop._Counted(FakeLLM(default="answer"), meter).complete("s", "u")
    assert meter.sent == limit and not meter.in_flight


@pytest.mark.parametrize("name", ["complete", "run", "_invocation", "_launch", "_reply", "__getattribute__",
                                  "__getattr__", "__setattr__", "_observe_stops", "__class__", "__init_subclass__"])
def test_every_final_method_is_guarded(name):
    with pytest.raises(LLMError, match="G6_E_LOOP_UNWATCHED_HOST"):
        type("Override", (usage.WatchedClaude,), {name: lambda *args: None})()


def test_code_derived_inventories():
    from tests.metaharness import test_send_record as records
    from tests.metaharness import test_send_custody as custody
    from tests.metaharness import test_send_identity as identities
    from tests.metaharness import test_send_boundaries as boundaries
    from tests.metaharness import test_refufu2r1_reply_classes as replies
    records.test_every_place_a_request_can_leave_reaches_the_one_admission()
    records.test_every_meter_reads_what_it_has_taken_from_the_one_record()
    records.test_every_path_that_ends_a_tick_reads_what_it_latched_as_it_closes()
    records.test_every_fact_a_handle_holds_is_the_processs_the_folders_files_or_its_own()
    custody.test_every_class_or_send_replacement_is_at_construction_and_every_capture_uses_file_identity()
    custody.test_every_host_command_option_has_an_input_output_or_nonfile_role()
    identities.test_every_send_identity_producer_and_reader_is_classified_from_the_code()
    boundaries.test_send_path_inventory_has_no_unclassified_dispatch_or_acquisition()
    replies.test_the_code_lists_every_reply_classification_site()
