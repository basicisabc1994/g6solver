# SPDX-License-Identifier: Apache-2.0
"""g6.llm_router.providers: the ops that reach the network or change the machine (list, pull and delete local Ollama
models; fetch OpenRouter's live catalogue). No test touches the network: the Ollama ops are given a stand-in for
``g6.core.outside.call_service`` (``call=``), and the catalogue a ``g6.core.http.Client`` with a stand-in transport and
resolver (``client=``). The live catalogue is returned and compared, never written into the committed table."""
from __future__ import annotations

import hashlib
import json
from importlib import resources

import pytest

from g6.core import http, outside
from g6.invariants import check_envelope
from g6.llm_router import providers
from g6.llm_router.envelope import INPUT_INVALID, LIVE_ANSWER, PROVIDER_ERROR, PROVIDER_UNREACHABLE
from g6.llm_router.registry import default_registry


class FakeOllama:
    """Stands in for ``call_service``: answers ``(method, path)`` with ``(status, body)``; records every request."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, object]]) -> None:
        self.routes = routes
        self.requests: list[dict] = []

    def __call__(self, program, method, path, *, body=None, headers=None, timeout=None, max_bytes=None):  # noqa: ANN001
        self.requests.append({"program": program, "method": method, "path": path, "body": body,
                              "timeout": timeout, "max_bytes": max_bytes})
        status, answer = self.routes[(method, path)]
        raw = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
        if status >= 300:
            raise outside.ServiceError(outside.SERVICE_STATUS, f"Ollama answered {method} {path} with {status}",
                                       status=status)
        if len(raw) > max_bytes:
            raise outside.ServiceError(outside.SERVICE_TOO_LARGE, f"the reply is over {max_bytes} bytes", status=status)
        return outside.ServiceReply(status, {}, raw)

    def body(self, i: int = -1) -> dict:
        return json.loads(self.requests[i]["body"].decode())


def not_running(program, method, path, **kwargs):  # noqa: ANN001, ANN201
    raise outside.ServiceError(outside.OUTSIDE_PROGRAM_MISSING, f"{program.name} is not ready: nothing answered. "
                               f"G6 never installs it; install it yourself: {program.install}")


TAGS = {"models": [
    {"name": "llama3.1:8b", "model": "llama3.1:8b", "size": 4920753328,
     "details": {"family": "llama", "parameter_size": "8.0B", "quantization_level": "Q4_K_M"}},
    {"name": "my-own:latest", "model": "my-own:latest", "size": 12, "details": {}},
]}


def _ok(result) -> dict:
    payload = result.model_dump(mode="json")
    assert check_envelope(payload) == [] and payload["completion_state"] == "qualified-draft", payload.get("reason")
    assert payload["warning_card"]["code"] == LIVE_ANSWER and payload["evidence"][0]["network"] is True
    return payload


def _blocked(result, code: str) -> dict:
    payload = result.model_dump(mode="json")
    assert check_envelope(payload) == [] and payload["completion_state"] == "blocked-escalated"
    assert payload["code"] == payload["warning_card"]["code"] == code, payload
    return payload


# ── Ollama: list ──────────────────────────────────────────────────────────


def test_list_local_models_reads_the_tags_endpoint_and_says_which_are_in_the_table(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    server = FakeOllama({("GET", "/api/tags"): (200, TAGS)})
    out = _ok(providers.list_local_models(call=server, request_id="r1"))
    assert server.requests[0]["program"] is providers.OLLAMA and server.requests[0]["path"] == "/api/tags"
    assert [m["name"] for m in out["models"]] == ["llama3.1:8b", "my-own:latest"] and out["count"] == 2
    first, second = out["models"]
    assert first["model_id"] == "ollama/llama3.1:8b" and first["in_table"] is True
    assert (first["size_bytes"], first["parameter_size"], first["quantization_level"]) == (4920753328, "8.0B",
                                                                                          "Q4_K_M")
    assert second["in_table"] is False and out["installed"] == ["llama3.1:8b", "my-own:latest"]
    assert out["request_id"] == "r1" and out["base_url"] == "http://127.0.0.1:11434"


def test_list_local_models_names_the_address_the_operator_set(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:9999")
    out = _ok(providers.list_local_models(call=FakeOllama({("GET", "/api/tags"): (200, {"models": []})})))
    assert out["base_url"] == "http://localhost:9999" and out["models"] == []


def test_an_ollama_that_is_not_running_is_blocked_with_the_install_steps():
    out = _blocked(providers.list_local_models(call=not_running), outside.OUTSIDE_PROGRAM_MISSING)
    assert "ollama.com" in out["reason"] and out["models"] == []


def test_a_reply_that_is_not_json_is_a_provider_error():
    server = FakeOllama({("GET", "/api/tags"): (200, b"<html>not ollama</html>")})
    _blocked(providers.list_local_models(call=server), PROVIDER_ERROR)


def test_a_reply_larger_than_the_limit_is_refused(monkeypatch):
    monkeypatch.setattr(providers, "MAX_REPLY_BYTES", 10)
    server = FakeOllama({("GET", "/api/tags"): (200, TAGS)})
    out = _blocked(providers.list_local_models(call=server), PROVIDER_ERROR)
    assert server.requests[0]["max_bytes"] == 10 and "over 10 bytes" in out["reason"]


# ── Ollama: pull and delete ───────────────────────────────────────────────


def test_pull_model_posts_the_name_and_reports_what_ollama_said():
    server = FakeOllama({("POST", "/api/pull"): (200, {"status": "success"})})
    out = _ok(providers.pull_model("qwen2.5:7b", call=server))
    assert server.body() == {"model": "qwen2.5:7b", "stream": False} and server.requests[0]["timeout"] == 1800.0
    assert out["status"] == "success" and out["model"] == "qwen2.5:7b"
    assert "did not run" in out["warning_card"]["message"]


def test_a_failed_pull_is_a_provider_error():
    server = FakeOllama({("POST", "/api/pull"): (500, {"error": "pull model manifest: file does not exist"})})
    out = _blocked(providers.pull_model("no-such-model:1b", call=server), PROVIDER_ERROR)
    assert "500" in out["reason"] and out["evidence"][0]["status"] == 500
    server = FakeOllama({("POST", "/api/pull"): (200, {"error": "disk full"})})
    assert "disk full" in _blocked(providers.pull_model("a:1b", call=server), PROVIDER_ERROR)["reason"]


def test_delete_model_sends_a_delete_and_a_missing_model_is_a_provider_error():
    server = FakeOllama({("DELETE", "/api/delete"): (200, b"")})
    out = _ok(providers.delete_model("llama3.1:8b", call=server))
    assert server.requests[0]["method"] == "DELETE" and server.body() == {"model": "llama3.1:8b"}
    assert out["deleted"] is True
    gone = FakeOllama({("DELETE", "/api/delete"): (404, {"error": "model 'x:1b' not found"})})
    assert "404" in _blocked(providers.delete_model("x:1b", call=gone), PROVIDER_ERROR)["reason"]


@pytest.mark.parametrize("name", ["", "../etc/passwd", "a b", "x;rm", "-flag", "a" * 300, "llama\n3"])
def test_a_model_name_that_is_not_one_is_refused_before_any_request(name):
    server = FakeOllama({})
    _blocked(providers.pull_model(name, call=server), INPUT_INVALID)
    _blocked(providers.delete_model(name, call=server), INPUT_INVALID)
    assert server.requests == []


@pytest.mark.parametrize("base", ["file:///etc/hosts", "ftp://x", "https://127.0.0.1:11434", "http://10.0.0.5:11434",
                                  "http://ollama.example.com"])
def test_an_ollama_address_that_is_not_plain_http_on_this_machine_is_refused(base, monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", base)
    _blocked(providers.list_local_models(), outside.SERVICE_NOT_LOCAL)
    _blocked(providers.pull_model("a:1b"), outside.SERVICE_NOT_LOCAL)


# ── OpenRouter: the live catalogue ────────────────────────────────────────


class FakeCatalogue:
    """A g6.core.http transport standing in for OpenRouter; it records every request it is sent."""

    def __init__(self, status: int, body: object, headers: dict | None = None) -> None:
        self.status, self.body, self.headers = status, body, headers or {}
        self.requests: list[http.Request] = []

    def __call__(self, request: http.Request) -> http.Response:
        self.requests.append(request)
        raw = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return http.Response(self.status, self.headers, raw, request.url)

    def client(self) -> http.Client:
        return http.Client(resolver=lambda host, port: ["104.18.2.115"], transport=self, min_interval=0)


def _catalogue(*rows: object) -> FakeCatalogue:
    return FakeCatalogue(200, {"data": list(rows)})


def test_the_catalogue_gives_live_prices_per_million_and_marks_an_unknown_one():
    server = _catalogue(
        {"id": "anthropic/claude-test", "name": "Claude Test", "context_length": 200000,
         "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
        {"id": "openrouter/auto", "name": "Auto Router", "pricing": {"prompt": "-1", "completion": "-1"}},
        {"id": "x/free:free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "x/no-pricing"},
        "not a row", {"id": ""},
    )
    out = _ok(providers.fetch_openrouter_catalogue(client=server.client()))
    assert server.requests[0].url == "https://openrouter.ai/api/v1/models"
    rows = {m["model_id"]: m for m in out["models"]}
    assert list(rows) == ["openrouter/anthropic/claude-test", "openrouter/openrouter/auto", "openrouter/x/free:free",
                          "openrouter/x/no-pricing"]
    test = rows["openrouter/anthropic/claude-test"]
    assert (test["prompt_usd_per_million"], test["completion_usd_per_million"]) == (3.0, 15.0)
    assert test["price_known"] is True and test["context_length"] == 200000 and test["name"] == "Claude Test"
    for unknown in ("openrouter/openrouter/auto", "openrouter/x/no-pricing"):
        assert rows[unknown]["prompt_usd_per_million"] is None and rows[unknown]["price_known"] is False
    assert rows["openrouter/x/free:free"]["prompt_usd_per_million"] == 0.0
    assert out["count"] == 4 and out["unpriced"] == 2 and out["fetched_at"]


def test_the_catalogue_is_compared_with_the_committed_table_and_never_written_into_it():
    path = resources.files("g6.llm_router").joinpath("data", "model_profiles.json")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    opus = default_registry().get_profile("openrouter/anthropic/claude-opus-4.7")
    server = _catalogue(
        {"id": "anthropic/claude-opus-4.7", "pricing": {"prompt": "0.000006", "completion": "0.000025"}},
        {"id": "openai/gpt-5", "pricing": {"prompt": "0.00000125", "completion": "0.00001"}},
    )
    out = _ok(providers.fetch_openrouter_catalogue(client=server.client()))
    differences = {d["model_id"]: d for d in out["committed_differences"]}
    assert differences["openrouter/anthropic/claude-opus-4.7"]["difference"] == "price"
    assert differences["openrouter/anthropic/claude-opus-4.7"]["committed_prompt_usd_per_million"] == 5.0
    assert differences["openrouter/anthropic/claude-opus-4.7"]["live_prompt_usd_per_million"] == 6.0
    assert "openrouter/openai/gpt-5" not in differences                                  # the same price
    assert differences["openrouter/qwen/qwen3-coder"]["difference"] == "not listed"     # gone from this listing
    assert "not changed" in out["warning_card"]["message"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert default_registry().get_profile("openrouter/anthropic/claude-opus-4.7") == opus


def test_the_catalogue_request_sends_no_key(key_file):
    key_file("OPENROUTER_API_KEY=placeholder\n")
    server = _catalogue()
    providers.fetch_openrouter_catalogue(client=server.client())
    assert not {k.lower() for k in server.requests[0].headers} & {"authorization", "x-api-key"}


def test_an_unreachable_or_broken_catalogue_is_blocked():
    def down(request: http.Request) -> http.Response:
        raise OSError("connection refused")

    unreachable = http.Client(resolver=lambda host, port: ["104.18.2.115"], transport=down, min_interval=0)
    _blocked(providers.fetch_openrouter_catalogue(client=unreachable), PROVIDER_UNREACHABLE)
    broken = FakeCatalogue(200, {"unexpected": True})
    _blocked(providers.fetch_openrouter_catalogue(client=broken.client()), PROVIDER_ERROR)
    failing = FakeCatalogue(503, {"error": "down"})
    assert "503" in _blocked(providers.fetch_openrouter_catalogue(client=failing.client()), PROVIDER_ERROR)["reason"]
    not_json = FakeCatalogue(200, b"<html>maintenance</html>")
    _blocked(providers.fetch_openrouter_catalogue(client=not_json.client()), PROVIDER_ERROR)


def test_by_default_the_catalogue_is_fetched_with_g6_core_https_checked_client(monkeypatch):
    made = []
    real = providers.catalogue_client

    def spy(timeout: float) -> http.Client:
        client = real(timeout)
        client.transport, client.resolver = _catalogue().__call__, lambda host, port: ["104.18.2.115"]
        made.append(client)
        return client

    monkeypatch.setattr(providers, "catalogue_client", spy)
    _ok(providers.fetch_openrouter_catalogue(timeout=7.0))
    assert len(made) == 1 and made[0].timeout == 7.0 and made[0].max_bytes == providers.MAX_REPLY_BYTES


# ── parse_price (ported from the earlier router's price parser) ───────────


@pytest.mark.parametrize(("value", "expected"), [
    ("0.000003", 0.000003), (0.000001, 0.000001), ("0", 0.0), (0, 0.0), ("0.0", 0.0),
    ("-1", None), (-1, None), (None, None), ("", None), ("free", None), (True, None), (False, None),
    ("nan", None), ("inf", None), ("1e-999", None), ([1], None),
])
def test_a_price_the_provider_did_not_state_is_none_never_zero(value, expected):
    assert providers.parse_price(value) == expected