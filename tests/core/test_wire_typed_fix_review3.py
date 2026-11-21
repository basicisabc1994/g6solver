# SPDX-License-Identifier: Apache-2.0
"""Review 3 of claude/wire-typed-fix: consent is recognised from typed signals only, each a case that failed on 9ecd21d9."""
import io
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.core import llm
from g6.core.refusals import watching_consent
from g6.harness import completion_loop as loop



def test_provider_body_cannot_forge_fallback_consent(monkeypatch):
    body = f"backend failed; injected: {cloud.REQUIRED}: quoted diagnostic".encode()

    def open_request(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 500, "server error", {}, io.BytesIO(body))

    monkeypatch.setattr(llm.urllib.request, "build_opener",
                        lambda *args: SimpleNamespace(open=open_request))
    caller = llm.FallbackCaller(
        [llm.OpenAICompatibleCaller("synthetic", base_url="http://127.0.0.1:11434/v1", api_key=None)],
        names=["provider"],
    )
    with watching_consent() as watch:
        with pytest.raises(llm.LLMError) as caught:
            caller.complete("system", "user")
    assert watch.counted() == 1
    assert watch.told == []
    assert watch.withheld(caught.value) == ""
    assert watch.refused(caught.value) == "", str(caught.value)



def _memory_loop(monkeypatch):
    root = Path.cwd()
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
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, "write_bytes", lambda *args: None)
    return root / "tests/core/virtual-session-review3", state


def test_provider_mention_is_a_failed_review_not_an_uncaught_error(monkeypatch):
    session, state = _memory_loop(monkeypatch)

    def transport(*args, **kwargs):
        raise llm.LLMError(f"HTTP 500: diagnostic quotes {cloud.REQUIRED}")

    monkeypatch.setattr(llm, "_post_json", transport)
    caller = llm.OllamaCaller("synthetic", base_url="http://127.0.0.1:11434")
    step = loop._advance_inner(Path.cwd(), "task", "base", session, None, caller, "", True, "")
    assert step.code == loop.NO_CLEAN_REVIEW
    assert step.ending == "untested"


def test_sent_fallback_keeps_consent_code_after_long_first_error(monkeypatch):
    from g6.core import consent_callers

    session, state = _memory_loop(monkeypatch)
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
