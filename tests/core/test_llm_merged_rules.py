# SPDX-License-Identifier: Apache-2.0
"""The rules g6.core.llm took from the archive's LangChain and OpenAI agents (merged, not ported): a key from the
person's key file goes only to the provider's own address or to one written beside it in that file, never to a base URL
from the environment or a caller's mapping; no provider is picked or swapped by which keys are present; a model's reply
that calls its own answer verified never lifts a label; a 429 or 503 with Retry-After is waited on within a stated cap,
the wait recorded; and a model call follows no redirect and reads its reply within a cap and the call's timeout. All
offline: transport doubles under urllib's own handlers, no socket."""
from __future__ import annotations

import email.utils
import http.client
import io
import json
import sys
import time
import types
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from g6.core import llm

_OK = {"choices": [{"message": {"content": "ok"}}], "message": {"content": "ok"}}


@pytest.fixture
def no_base_url(monkeypatch):
    for name in ("OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def _recording_post(monkeypatch) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(llm, "_post_json", lambda url, body, headers, timeout: seen.append((url, headers)) or _OK)
    return seen


# -- a caller-supplied base URL never receives an operator key -------------------------------------------------------

def test_an_operator_key_never_goes_to_a_base_url_it_was_not_written_beside(monkeypatch, key_file, no_base_url):
    key_file("OPENAI_API_KEY=placeholder\n")
    seen = _recording_post(monkeypatch)
    named = llm.provider_from_env({"G6_LLM_PROVIDER": "openai", "G6_LLM_MODEL": "m",
                                   "OPENAI_BASE_URL": "https://elsewhere.example/v1"})   # a caller's mapping
    assert named.complete("s", "u") == "ok"
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost.attacker.example/v1")      # set for another program
    monkeypatch.setenv("G6_LLM_PROVIDER", "openai")
    monkeypatch.setenv("G6_LLM_MODEL", "m")
    assert llm.provider_from_env().complete("s", "u") == "ok"
    assert [url for url, _ in seen] == ["https://elsewhere.example/v1/chat/completions",
                                        "http://localhost.attacker.example/v1/chat/completions"]
    assert [headers for _, headers in seen] == [{}, {}]   # sent, but without the person's key


def test_the_key_goes_to_openais_own_address_or_the_one_the_key_file_names(key_file, no_base_url) -> None:
    key_file("OPENAI_API_KEY=placeholder\n")
    assert llm.provider_from_env({"G6_LLM_PROVIDER": "openai", "G6_LLM_MODEL": "m"})._inner.api_key == "placeholder"
    key_file("OPENAI_API_KEY=placeholder\nOPENAI_BASE_URL=https://mine.example/v1\n")
    filed = llm.provider_from_env({"G6_LLM_PROVIDER": "openai", "G6_LLM_MODEL": "m"})._inner
    assert (filed.base_url, filed.api_key) == ("https://mine.example/v1", "placeholder")
    moved = llm.provider_from_env({"G6_LLM_PROVIDER": "openai", "G6_LLM_MODEL": "m",
                                   "OPENAI_BASE_URL": "https://mine.example/v1/"})._inner   # the same address
    assert moved.api_key == "placeholder"
    other = llm.provider_from_env({"G6_LLM_PROVIDER": "openai", "G6_LLM_MODEL": "m",
                                   "OPENAI_BASE_URL": "https://other.example/v1"})._inner
    assert (other.base_url, other.api_key) == ("https://other.example/v1", None)
    own = llm.provider_from_env({"G6_LLM_PROVIDER": "openai", "G6_LLM_MODEL": "m",
                                 "OPENAI_BASE_URL": "https://other.example/v1"}, keys={"OPENAI_API_KEY": "mine"})
    assert own._inner.api_key == "mine"   # a key given in Python is the caller's own, for the caller's own address


def test_the_anthropic_key_never_follows_an_address_set_in_the_environment(monkeypatch, key_file) -> None:
    built: list[dict] = []
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda **kw: built.append(kw) or SimpleNamespace()
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")   # the SDK reads this when no address is given
    key_file("ANTHROPIC_API_KEY=placeholder\n")
    llm.provider_from_env({"G6_LLM_PROVIDER": "anthropic"})._inner._get_client()
    key_file("ANTHROPIC_API_KEY=placeholder\nANTHROPIC_BASE_URL=https://mine.example\n")
    llm.provider_from_env({"G6_LLM_PROVIDER": "anthropic"})._inner._get_client()
    assert [b.get("base_url") for b in built] == ["https://api.anthropic.com", "https://mine.example"]
    assert all(b.get("api_key") == "placeholder" for b in built)


# -- no provider is added or swapped silently (already held; these show it) -------------------------------------------

def test_no_provider_is_picked_or_swapped_by_which_keys_are_in_the_file(monkeypatch, key_file, no_base_url) -> None:
    monkeypatch.setattr(llm, "_post_json", lambda *a, **k: pytest.fail("a provider nobody chose was called"))
    key_file("ANTHROPIC_API_KEY=a\nOPENAI_API_KEY=b\nOPENROUTER_API_KEY=c\n")
    assert isinstance(llm.provider_from_env({}), llm.NoModel)   # keys alone choose nothing
    key_file("OPENROUTER_API_KEY=c\n")
    for provider, key in (("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")):
        caller = llm.provider_from_env({"G6_LLM_PROVIDER": provider, "G6_LLM_MODEL": "m"})
        with pytest.raises(llm.LLMError, match=f"{key} is not in "):
            caller.complete("s", "u")   # it fails by name; it is not sent to OpenRouter instead
    with pytest.raises(llm.LLMError, match="unknown G6_LLM_PROVIDER"):
        llm.provider_from_env({"G6_LLM_PROVIDER": "langchain"})


# -- a model's word on its own answer never lifts a label --------------------------------------------------------------

@pytest.mark.parametrize("word", ["verified", "Verified", " PROVED ", "proven", "certified", "safe", "cleared",
                                  "compliant"])
def test_a_model_calling_its_own_answer_verified_never_raises_a_label(word: str) -> None:
    reply = llm.FakeLLM(default=json.dumps({"answer": 42, "completion_state": word})).complete("s", "u")
    claimed = llm.extract_json(reply)["completion_state"]
    assert llm.cap_model_label(claimed) == "qualified-draft"
    assert llm.cap_model_label(claimed, ceiling="blocked-escalated") == "blocked-escalated"


def test_a_model_may_lower_a_label_and_anything_else_it_says_is_no_claim() -> None:
    assert llm.cap_model_label("blocked-escalated") == "blocked-escalated"
    assert llm.cap_model_label("qualified-draft", ceiling="verified") == "qualified-draft"
    assert [llm.cap_model_label(x) for x in (None, "", "great!", 1, ["verified"])] == ["qualified-draft"] * 5
    with pytest.raises(ValueError, match="ceiling"):
        llm.cap_model_label("verified", ceiling="done")


@pytest.mark.parametrize("ceiling", ["verified", "qualified-draft", "blocked-escalated"])
@pytest.mark.parametrize("claimed", ["verified", "Verified", "qualified-draft", "blocked-escalated", "proved", None])
def test_no_ceiling_lets_a_models_own_label_reach_verified(ceiling: str, claimed: object) -> None:
    # a verifier's own result path gives verified; a model's word read through this rule never does
    label = llm.cap_model_label(claimed, ceiling=ceiling)
    assert label in ("qualified-draft", "blocked-escalated")
    assert label == ("blocked-escalated" if "blocked-escalated" in (ceiling, claimed) else "qualified-draft")


# -- a 429 or 503 with Retry-After is waited on within a stated cap, and the wait is recorded ---------------------------

# The transport double sits under urllib's own opener: http.client's connection classes are replaced by ones that open
# no socket and answer from a script, so urllib's handlers (redirects, errors, proxies) run exactly as they would.

@dataclass
class _Answer:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    drip: int = 0          # body bytes handed over per read (0: as many as asked for)
    pause: float = 0.0     # seconds before each read of the body


class _Raw(io.RawIOBase):
    """One answer on the wire: its status line and headers at once, then its body as the answer says."""

    def __init__(self, answer: _Answer) -> None:
        lines = [f"HTTP/1.1 {answer.status} Status", *(f"{k}: {v}" for k, v in answer.headers.items()),
                 f"Content-Length: {len(answer.body)}", "", ""]
        self._head, self._body, self._answer = "\r\n".join(lines).encode("latin-1"), answer.body, answer
        self.served = 0   # body bytes read off the wire

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:  # noqa: ANN001
        if self._head:
            n = min(len(buffer), len(self._head))
            buffer[:n], self._head = self._head[:n], self._head[n:]
            return n
        if not self._body:
            return 0
        if self._answer.pause:
            time.sleep(self._answer.pause)
        n = min(len(buffer), len(self._body), self._answer.drip or len(self._body))
        buffer[:n], self._body = self._body[:n], self._body[n:]
        self.served += n
        return n


class _Socket:
    def __init__(self, raw: _Raw) -> None:
        self._raw = raw

    def makefile(self, *args: object, **kwargs: object) -> io.BufferedReader:
        return io.BufferedReader(self._raw)


class _Wire:
    """Every connection urllib opens, answered in order; what each request carried, and to which host, recorded."""

    def __init__(self, answers: list) -> None:
        self.answers = [a if isinstance(a, _Answer) else _Answer(body=json.dumps(a).encode()) for a in answers]
        self.sent: list[dict] = []
        self.urls: list[str] = []
        self.raws: list[_Raw] = []

    def connection(self, scheme: str, real: type) -> type:
        wire = self

        class Connection(real):   # the real class's settings, none of its sockets
            def __init__(self, host: str, timeout: float | None = None, **kwargs: object) -> None:
                self.host, self.timeout, self.sock = host, timeout, None

            def set_debuglevel(self, level: int) -> None:
                pass

            def request(self, method, url, body=None, headers=None, **kwargs):  # noqa: ANN001, ANN202
                wire.urls.append(f"{scheme}://{self.host}{url}")
                wire.sent.append({"host": self.host, "method": method, "headers": dict(headers or {})})

            def getresponse(self) -> http.client.HTTPResponse:
                raw = _Raw(wire.answers.pop(0))
                wire.raws.append(raw)
                reply = http.client.HTTPResponse(_Socket(raw), method=wire.sent[-1]["method"])
                reply.begin()
                return reply

            def close(self) -> None:
                pass

        return Connection


def _install(monkeypatch, answers: list) -> _Wire:
    wire = _Wire(answers)
    monkeypatch.setattr(http.client, "HTTPConnection", wire.connection("http", http.client.HTTPConnection))
    monkeypatch.setattr(http.client, "HTTPSConnection", wire.connection("https", http.client.HTTPSConnection))
    monkeypatch.setattr(urllib.request, "getproxies", dict)   # no proxy from this machine's settings
    return wire


@pytest.fixture
def wire(monkeypatch):
    return lambda *answers: _install(monkeypatch, list(answers))


def _refusal(url: str, status: int, retry_after: str | None = None) -> _Answer:
    return _Answer(status, b"slow down", {"Retry-After": retry_after} if retry_after is not None else {})


def _transport(monkeypatch, answers: list) -> tuple[list[str], list[float]]:
    slept: list[float] = []
    monkeypatch.setattr(llm, "_sleep", slept.append)
    return _install(monkeypatch, answers).urls, slept


BASE = "http://127.0.0.1:9/v1"
CHAT = BASE + "/chat/completions"


def test_a_429_with_retry_after_is_waited_on_and_the_wait_is_recorded(monkeypatch) -> None:
    sent, slept = _transport(monkeypatch, [_refusal(CHAT, 429, "2"), _OK])
    with llm.waits() as waited:
        out = llm.OpenAICompatibleCaller("m", base_url=BASE, api_key=None).complete("s", "u")
    assert out == "ok" and slept == [2.0] and sent == [CHAT, CHAT]
    assert waited == [{"url": CHAT, "status": 429, "retry_after_s": 2.0, "waited_s": 2.0,
                       "cap_s": llm.RETRY_AFTER_CAP_S}]


def test_a_503_with_a_date_is_waited_on_too(monkeypatch) -> None:
    when = email.utils.format_datetime(datetime.now(timezone.utc) + timedelta(seconds=3), usegmt=True)
    sent, slept = _transport(monkeypatch, [_refusal("http://127.0.0.1:9/api/chat", 503, when), _OK])
    with llm.waits() as waited:
        assert llm.OllamaCaller("m", base_url="http://127.0.0.1:9").complete("s", "u") == "ok"
    assert len(sent) == 2 and len(slept) == 1 and 0 <= slept[0] <= 3 and waited[0]["status"] == 503


def test_a_wait_past_the_cap_is_refused_by_name_and_nothing_sleeps(monkeypatch) -> None:
    sent, slept = _transport(monkeypatch, [_refusal(CHAT, 429, "120")])
    with pytest.raises(llm.LLMError, match="G6_E_LLM_RATE_LIMITED") as caught:
        llm.OpenAICompatibleCaller("m", base_url=BASE, api_key=None).complete("s", "u")
    assert "120" in str(caught.value) and f"{llm.RETRY_AFTER_CAP_S:g}" in str(caught.value)
    assert slept == [] and sent == [CHAT]


def test_the_cap_holds_over_every_wait_of_one_call(monkeypatch) -> None:
    sent, slept = _transport(monkeypatch, [_refusal(CHAT, 429, "20"), _refusal(CHAT, 429, "20"), _OK])
    with pytest.raises(llm.LLMError, match="G6_E_LLM_RATE_LIMITED"):
        llm.OpenAICompatibleCaller("m", base_url=BASE, api_key=None).complete("s", "u")
    assert slept == [20.0] and len(sent) == 2


@pytest.mark.parametrize(("status", "retry_after"), [(429, None), (503, "soon"), (500, "1"), (401, "1")])
def test_no_hint_or_another_status_is_not_retried(monkeypatch, status: int, retry_after: str | None) -> None:
    sent, slept = _transport(monkeypatch, [_refusal(CHAT, status, retry_after), _OK])
    with pytest.raises(llm.LLMError, match=f"HTTP {status}"):
        llm.OpenAICompatibleCaller("m", base_url=BASE, api_key=None).complete("s", "u")
    assert slept == [] and sent == [CHAT]


def test_a_server_that_keeps_refusing_is_tried_a_bounded_number_of_times(monkeypatch) -> None:
    sent, slept = _transport(monkeypatch, [_refusal(CHAT, 429, "1") for _ in range(10)])
    with pytest.raises(llm.LLMError, match="G6_E_LLM_RATE_LIMITED"):
        llm.OpenAICompatibleCaller("m", base_url=BASE, api_key=None).complete("s", "u")
    assert slept == [1.0] * llm.MAX_RATE_LIMIT_WAITS and len(sent) == llm.MAX_RATE_LIMIT_WAITS + 1


# -- a model call follows no redirect, and reads its reply within a cap and the call's timeout ---------------------------

KEYED = "https://models.example/v1"
_MIB = 1024 * 1024


def _outcome(call):  # noqa: ANN001, ANN202
    try:
        return call()
    except llm.LLMError as exc:
        return exc


@pytest.mark.parametrize(("status", "location"), [
    (302, "https://collector.example/steal"), (302, "http://127.0.0.1:8080/steal"),
    (301, "https://collector.example/steal"), (303, "//collector.example/steal"),
    (307, "https://collector.example/steal"), (308, "http://127.0.0.1:8080/steal")])
def test_a_redirect_is_refused_by_name_and_the_key_goes_nowhere_else(wire, status: int, location: str) -> None:
    found = wire(_Answer(status, b"moved", {"Location": location}), _OK, _OK)
    caller = llm.OpenAICompatibleCaller("m", base_url=KEYED, api_key="placeholder")
    outcome = _outcome(lambda: caller.complete("s", "u"))
    assert [(s["host"], s["headers"].get("Authorization")) for s in found.sent] == [
        ("models.example", "Bearer placeholder")]   # one request, to the address the key belongs to
    host = urllib.parse.urlsplit(urllib.parse.urljoin(KEYED, location)).hostname
    assert isinstance(outcome, llm.LLMError) and str(outcome).startswith(llm.REDIRECT_REFUSED)
    assert f"HTTP {status}" in str(outcome) and host in str(outcome)


def test_a_reply_past_its_cap_is_refused_and_read_no_further(wire) -> None:
    huge = b'{"choices": [{"message": {"content": "' + b"a" * (16 * _MIB) + b'"}}]}'
    found = wire(_Answer(200, huge), _Answer(500, b"e" * (16 * _MIB)))
    caller = llm.OpenAICompatibleCaller("m", base_url=BASE, api_key=None)
    answers = [_outcome(lambda: caller.complete("s", "u")) for _ in range(2)]
    served = [raw.served for raw in found.raws]
    assert served[0] <= 8 * _MIB + 128 * 1024 and served[1] <= 64 * 1024   # neither body was read whole
    assert isinstance(answers[0], llm.LLMError) and str(answers[0]).startswith(llm.REPLY_TOO_LARGE)
    assert isinstance(answers[1], llm.LLMError) and "HTTP 500" in str(answers[1])


def test_a_reply_that_drips_is_cut_off_at_the_calls_timeout(wire) -> None:
    body = json.dumps({"message": {"content": "ok"}}).encode()   # 30 bytes, one every 0.05 s: 1.5 s in all
    found = wire(_Answer(200, body, drip=1, pause=0.05))
    started = time.monotonic()
    outcome = _outcome(lambda: llm.OllamaCaller("m", base_url="http://127.0.0.1:11434", timeout=0.3).complete("s", "u"))
    elapsed = time.monotonic() - started
    assert found.raws[0].served < len(body) and elapsed < 1.0   # the drip was not waited out
    assert isinstance(outcome, llm.LLMError) and str(outcome).startswith(llm.REPLY_TIMEOUT)
