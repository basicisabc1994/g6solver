# SPDX-License-Identifier: Apache-2.0
"""Review-3 consent scenarios with real session storage and OS mutexes."""
import io
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from g6.core import cloud_consent as cloud
from g6.core import llm
from g6.harness import completion_loop as loop


def _disk_loop(monkeypatch, tmp_path):
    root = Path.cwd()
    session = tmp_path / "session-review3"
    session.mkdir()
    state = {}
    monkeypatch.setattr(loop, "_load", lambda *args: state)
    monkeypatch.setattr(loop, "_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop.repo_tests, "checked_repo", lambda *args: root)
    monkeypatch.setattr(loop.repo_tests, "change",
                        lambda *args: SimpleNamespace(repo=root, sources=("code.py",)))
    monkeypatch.setattr(loop.repo_tests, "snapshot", lambda *args: {})
    monkeypatch.setattr(loop.repo_tests, "diff_read", lambda *args: SimpleNamespace(text="patch"))
    monkeypatch.setattr(loop.proposer, "boundary", lambda *args: "")
    monkeypatch.setattr(loop, "_scouts_chosen", lambda: False)
    monkeypatch.setattr(loop, "harness", lambda: SimpleNamespace(critic="system", sources=()))
    monkeypatch.setattr(loop, "_patch_now", lambda *args: "patch")
    monkeypatch.setattr(loop, "_owe_row", lambda *args: None)
    monkeypatch.setattr(loop, "_write_row", lambda *args: None)
    monkeypatch.setattr(loop, "_final_review", lambda *args: "")
    monkeypatch.setattr(loop, "_reviews_report", lambda state, text, *args: text)
    monkeypatch.setattr(loop, "_shown", lambda *args: SimpleNamespace(masked="patch", hidden=False))
    monkeypatch.setattr(loop, "_pack", lambda *args: "pack")
    monkeypatch.setattr(loop, "_masked_pack", lambda *args: ("pack", False))
    return session, state


def test_provider_mention_is_a_failed_review_not_an_uncaught_error(monkeypatch, tmp_path):
    session, state = _disk_loop(monkeypatch, tmp_path)

    def transport(*args, **kwargs):
        raise llm.LLMError(f"HTTP 500: diagnostic quotes {cloud.REQUIRED}")

    monkeypatch.setattr(llm, "_post_json", transport)
    caller = llm.OllamaCaller("synthetic", base_url="http://127.0.0.1:11434")
    step = loop._advance_inner(Path.cwd(), "task", "base", session, None, caller, "", True, "")
    assert step.code == loop.NO_CLEAN_REVIEW
    assert step.ending == "untested"


def test_sent_fallback_keeps_consent_code_after_long_first_error(monkeypatch, tmp_path):
    from g6.core import consent_callers

    session, state = _disk_loop(monkeypatch, tmp_path)
    sent = []

    def open_request(request, **kwargs):
        sent.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 500, "server error", {}, io.BytesIO(b"x" * 300))

    @contextmanager
    def admission(provider, sources, payload, **kwargs):
        if provider == "cloud:unknown":
            cloud._refuse_unshared({}, provider, sources, ())
        yield

    monkeypatch.setattr(llm.urllib.request, "build_opener",
                        lambda *args: SimpleNamespace(open=open_request, handlers=[]))
    monkeypatch.setattr(consent_callers, "admission", admission)
    first = llm.OpenAICompatibleCaller(
        "synthetic", base_url="https://shared.invalid/" + "a" * 160 + "/v1", api_key=None)
    caller = llm.FallbackCaller(
        [first, SimpleNamespace(complete=lambda *args, **kwargs: "must not run")],
        names=["shared-provider", "unshared-fallback"],
    )
    step = loop._advance_inner(Path.cwd(), "task", "base", session, None, caller, "", True, "")
    assert len(sent) == 1, (sent, caller.failures)
    assert len(caller.failures) == 2
    assert cloud.REQUIRED in caller.failures[-1][1]
    assert step.code == cloud.REQUIRED, (step.code, state["reviews"])
    assert state["requests_sent"] == 1
