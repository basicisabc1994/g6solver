# SPDX-License-Identifier: Apache-2.0
"""Review 2 of claude/wire-typed-fix: three trust paths, each a case that failed on efa43a2a."""
from pathlib import Path
from types import SimpleNamespace

from g6.core import cloud_consent as cloud


def test_withheld_review_preserves_sent_count(monkeypatch):
    from g6.core.door import blocked_reply
    from g6.core.ledger import digest
    from g6.harness.workflow.obstacle import obstacles
    from g6.mcp import review_loop
    from g6.mcp.cloud_consent import withheld_reply

    result = {
        **blocked_reply(f"[{cloud.REQUIRED}] sharing refused after one request"),
        "code": cloud.REQUIRED,
        "action": "stop",
        "ending": "untested",
        "report": "One request went out before sharing was refused.",
        "requests_sent": 1,
    }
    monkeypatch.setattr(review_loop, "_step", lambda *args: result)
    with cloud.collect_sources() as capture:
        reply = review_loop.step(str(Path.cwd()), "synthetic task", "s1")
        _, refs, metadata = cloud.freeze_output(reply)
        assert reply["requests_sent"] == 1
        assert capture.review_unsent is False
        job = {
            "job_id": "synthetic-job",
            "status": "completed",
            "digest": digest(reply),
            "metadata": metadata,
            "review_unsent": capture.review_unsent,
        }
        shown = withheld_reply(reply, job=job, sources=refs)["result"]
    kinds = [one.kind for one in obstacles(None, shown, ())]
    assert kinds == ["refused_run"], (
        f"a sent review became {kinds}; requests_sent={shown.get('requests_sent')}"
    )
    assert shown["requests_sent"] == 1


def test_exception_instance_keeps_message_based_review_gate():
    from g6.triage import classify

    error = ValueError("permission denied: /etc/shadow")
    explicit = classify(type(error), str(error))
    assert explicit.posture == "review"
    actual = classify(error)
    assert actual.posture == explicit.posture, (
        f"typed exception lost its message: posture={actual.posture}, reasons={actual.reasons}"
    )


def test_sent_fallback_consent_refusal_returns_stable_loop_code(monkeypatch):
    from g6.core.llm import FallbackCaller, LLMError
    from g6.core.model_calls import note_model_call
    from g6.harness import completion_loop as loop

    root, state = Path.cwd(), {}

    def first(*args, **kwargs):
        note_model_call()
        raise LLMError("HTTP 500")

    def second(*args, **kwargs):
        raise cloud._refusal(LLMError(f"{cloud.REQUIRED}: source not shared"))

    critic = FallbackCaller(
        [SimpleNamespace(complete=first), SimpleNamespace(complete=second)],
        names=["shared-provider", "unshared-fallback"],
    )
    monkeypatch.setattr(loop, "_load", lambda *args: state)
    monkeypatch.setattr(loop.repo_tests, "checked_repo", lambda *args: root)
    monkeypatch.setattr(loop.repo_tests, "change",
                        lambda *args: SimpleNamespace(repo=root, sources=("code.py",)))
    monkeypatch.setattr(loop.repo_tests, "snapshot", lambda *args: {})
    monkeypatch.setattr(loop.proposer, "boundary", lambda *args: "")
    monkeypatch.setattr(loop, "_scouts_chosen", lambda: False)
    monkeypatch.setattr(loop, "harness", lambda: SimpleNamespace())
    monkeypatch.setattr(loop, "_patch_now", lambda *args: "patch")
    monkeypatch.setattr(loop, "_owe_row", lambda *args: None)
    monkeypatch.setattr(loop, "_write_row", lambda *args: None)
    monkeypatch.setattr(
        loop, "_finish",
        lambda session, state, text, code="", ending="", **kwargs:
            loop.Step(loop.STOP, text, code, ending=ending),
    )
    monkeypatch.setattr(
        loop, "review_round",
        lambda change, task, caller, *args: caller.complete("system", "user"),
    )
    step = loop._advance_inner(root, "task", "base", root, None, critic, "", True, "")
    assert step.code == cloud.REQUIRED
    assert step.ending == "untested"
    assert state["requests_sent"] == 1
    assert "not sent" not in step.text.lower()


# --- recognising a consent refusal never depends on the count; the count decides only the wording -------------------

def _watch(counted):
    from g6.core.refusals import ConsentWatch

    watch = ConsentWatch((cloud.REQUIRED, cloud.STATE_UNAVAILABLE))
    watch.counted = lambda: counted
    return watch


def test_a_told_refusal_is_recognised_after_a_counted_send_and_is_not_withheld():
    from g6.core.llm import LLMError

    watch = _watch(1)
    watch.tell(LLMError(f"{cloud.REQUIRED}: not shared"))
    opaque = LLMError("providers unavailable")
    assert watch.refused(opaque) == cloud.REQUIRED and watch.withheld(opaque) == ""
    assert _watch(0).refused(opaque) == ""


def test_a_fallback_summary_or_a_wrapped_cause_names_the_consent_refusal():
    from g6.core.llm import LLMError

    summary = LLMError(f"every caller failed: shared: HTTP 500; fallback: {cloud.STATE_UNAVAILABLE}: state unread")
    assert _watch(1).refused(summary) == cloud.STATE_UNAVAILABLE
    try:
        try:
            raise LLMError(f"[{cloud.REQUIRED}] not shared")
        except LLMError as inner:
            raise LLMError("providers unavailable") from inner
    except LLMError as wrapped:
        assert _watch(2).refused(wrapped) == cloud.REQUIRED
    assert _watch(0).refused(LLMError(f"a provider's words name {cloud.REQUIRED} in passing")) == ""


def test_an_exception_and_a_message_both_feed_the_hints():
    from g6.triage import classify

    assert classify(ValueError("bad value"), "permission denied: /etc/shadow").posture == "review"
    assert classify(ValueError("permission denied: /etc/shadow"), "while saving").posture == "review"
    assert classify(ValueError("bad value")).posture == "auto_heal"
