# SPDX-License-Identifier: Apache-2.0
"""g6.llm_router to READY (B23): the supervision chain climbs the committed table instead of stopping at a specialist
already in it (LLM_ROUTER-02); every answer that carries a price says how old it is and warns when it is older than a
stated age (LLM_ROUTER-03); the providers are those g6.core.llm builds, and keys are g6.core.llm's alone
(LLM_ROUTER-04); the block declares its contract (LLM_ROUTER-05); and every fetch goes through g6.core's checked HTTP
(the OpenRouter catalogue through g6.core.http, the local Ollama through g6.core.outside), so the package imports no
network module and reads no key (the conformance kit's G9 and G6). No test reaches the network."""
from __future__ import annotations

import ast
import http.server
import json
import re
import socket
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import g6.llm_router
from g6.core import http as core_http
from g6.core import llm, outside
from g6.core.contract_decorator import get_block_contract_attr
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.llm_router import TaskRouter, advice, providers
from g6.llm_router import envelope as env
from g6.llm_router.llm_router_block import LLMRouterBlock
from g6.llm_router.registry import CognitiveTask, CognitiveTaskRegistry, ModelProfile, default_registry, table
from g6.llm_router.supervision import Specialist, SupervisionPolicy, get_tier
from g6.mcp import invoke_reply

SRC = Path(g6.llm_router.__file__).parent
DATE = date.fromisoformat(table()["prices_as_of"])
REAL_TODAY = getattr(advice, "_today", None)   # taken before the autouse fixture pins the day


# ── LLM_ROUTER-02: the supervision chain (D1) ────────────────────────────


def _links_that_cannot_check(chain: list[str], reg: CognitiveTaskRegistry, task: CognitiveTask) -> list[tuple]:
    """Each link that is not able to check the one below it: a score for the task at least the worker's (a higher
    tier alone is not enough), and a higher cost tier or the same tier and a higher score."""
    bad = []
    for worker, sup in zip(chain, chain[1:]):
        w_tier, s_tier = get_tier(worker, reg), get_tier(sup, reg)
        w_score = reg.get_profile(worker).task_scores.get(task.value, 0.0)
        s_score = reg.get_profile(sup).task_scores.get(task.value, 0.0)
        if s_score <= 0 or s_score < w_score or not (s_tier > w_tier or (s_tier == w_tier and s_score > w_score)):
            bad.append((worker, sup, int(w_tier), int(s_tier), w_score, s_score))
    return bad


@pytest.mark.parametrize("allowed", [None, ("ollama", "openrouter"), ("openrouter",), ("ollama",)])
@pytest.mark.parametrize("task", list(CognitiveTask))
def test_on_the_committed_table_every_link_of_a_chain_can_check_the_one_below_it(task, allowed):
    reg = default_registry().filtered(providers=allowed)
    chain = SupervisionPolicy(reg).get_supervision_chain(task)
    assert chain and len(chain) == len(set(chain))
    assert _links_that_cannot_check(chain, reg, task) == []


def test_the_code_generation_chain_climbs_past_its_specialists_to_the_premium_tier():
    # the defect: qwen3-coder's first specialist rule named the local qwen2.5-coder, already in the chain, so the chain
    # ended at two links and never reached deepseek-v4-pro (0.88) or the premium tier. qwen3-coder (0.75) itself is no
    # longer a link: it scores lower than the local coder (0.78) it would check
    reg = default_registry()
    chain = SupervisionPolicy(reg).get_supervision_chain(CognitiveTask.CODE_GENERATION)
    assert chain[:2] == ["ollama/qwen2.5-coder:32b", "openrouter/deepseek/deepseek-v4-pro"]
    assert "openrouter/qwen/qwen3-coder" not in chain
    assert get_tier(chain[-1], reg) == 3 and len(chain) >= 3


def test_mathematical_reasoning_a_case_the_fix_was_not_written_around_climbs_too():
    reg = default_registry()
    chain = SupervisionPolicy(reg).get_supervision_chain(CognitiveTask.MATHEMATICAL_REASONING)
    assert chain[:2] == ["ollama/deepseek-r1:32b", "openrouter/deepseek/deepseek-v4-pro"]
    assert len(chain) >= 3 and get_tier(chain[-1], reg) == 3


def _p(model_id: str, tier: int, score: float, complexity: int = 2) -> ModelProfile:
    return ModelProfile(model_id=model_id, display_name=model_id, provider="openrouter", family="t", cost_tier=tier,
                        task_scores={"translation": score}, max_tool_complexity=complexity,
                        pricing_prompt_per_m=float(tier), pricing_completion_per_m=float(tier))


def test_a_users_specialist_that_points_down_or_back_falls_through_to_the_general_rule():
    reg = CognitiveTaskRegistry({p.model_id: p for p in (_p("openrouter/w", 1, 0.5), _p("openrouter/low", 0, 0.9),
                                                         _p("openrouter/top", 3, 0.8))})
    down = Specialist("openrouter/low", CognitiveTask.TRANSLATION, max_worker_tier=3)  # a cheaper tier than the worker
    found = SupervisionPolicy(reg, specialists=(down,)).get_supervisor("openrouter/w", CognitiveTask.TRANSLATION)
    assert found is not None and found.supervisor == "openrouter/top" and found.specialist_override is False
    back = Specialist("openrouter/w", CognitiveTask.TRANSLATION, max_worker_tier=3)    # already in the chain
    chain = SupervisionPolicy(reg, specialists=(back, down)).get_supervision_chain(CognitiveTask.TRANSLATION)
    assert chain == ["openrouter/low", "openrouter/top"] or _links_that_cannot_check(chain, reg,
                                                                                    CognitiveTask.TRANSLATION) == []


def test_a_model_with_no_score_for_the_task_supervises_nothing():
    # an embedding model's work is not checked by chat models that have no embedding score
    out = advice.supervision_chain("embedding").model_dump(mode="json")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == env.NO_SUPERVISOR


def test_the_advice_marks_the_specialist_links_the_chain_really_took():
    links = advice.supervision_chain("code_generation").model_dump(mode="json")["chain"]
    assert [link["specialist"] for link in links[:3]] == [False, True, False]
    assert all(link["role"] == "supervisor" for link in links[1:])


# ── LLM_ROUTER-03: the price's age and a stale-price warning ─────────────

PRICED = {
    "models_for_task": lambda: advice.models_for_task("code_review"),
    "route": lambda: advice.route("summarization"),
    "supervision_chain": lambda: advice.supervision_chain("code_generation"),
    "model_profile": lambda: advice.model_profile("ollama/qwen2.5:7b"),
    "estimate_cost": lambda: advice.estimate_cost("openrouter/openai/gpt-5", 2000, 500),
}


def _on(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    monkeypatch.setattr(advice, "_today", lambda: day, raising=False)


@pytest.mark.parametrize("op", list(PRICED))
def test_a_price_older_than_the_stated_age_is_flagged_on_every_answer_that_carries_one(op, monkeypatch):
    _on(monkeypatch, DATE + timedelta(days=66))
    out = PRICED[op]().model_dump(mode="json")
    assert check_envelope(out) == [] and out["completion_state"] == "qualified-draft"
    assert (out["price_age_days"], out["stale_price"], out["stale_after_days"]) == (66, True, advice.STALE_AFTER_DAYS)
    assert out["warning_card"]["code"] == env.STALE_PRICE and "66 days old" in out["warning_card"]["message"]
    assert out["evidence"][0]["price_age_days"] == 66


@pytest.mark.parametrize("op", list(PRICED))
def test_a_price_within_the_stated_age_carries_its_age_and_no_warning(op, monkeypatch):
    _on(monkeypatch, DATE + timedelta(days=10))
    out = PRICED[op]().model_dump(mode="json")
    assert (out["price_age_days"], out["stale_price"]) == (10, False)
    assert out["warning_card"]["code"] in (env.COMMITTED_TABLE, env.COST_ESTIMATE)


def test_the_stated_age_is_the_limit_and_the_day_after_it_is_stale(monkeypatch):
    assert advice.STALE_AFTER_DAYS == 30
    _on(monkeypatch, DATE + timedelta(days=30))
    assert PRICED["estimate_cost"]().stale_price is False
    _on(monkeypatch, DATE + timedelta(days=31))
    assert PRICED["estimate_cost"]().stale_price is True


def test_a_clock_before_the_tables_date_is_stale_never_fresh(monkeypatch):
    _on(monkeypatch, DATE - timedelta(days=3))
    out = PRICED["estimate_cost"]().model_dump(mode="json")
    assert (out["price_age_days"], out["stale_price"], out["warning_card"]["code"]) == (-3, True, env.STALE_PRICE)


def test_the_age_is_counted_from_todays_date_in_utc():
    assert REAL_TODAY is not None and REAL_TODAY() == datetime.now(UTC).date()


def test_over_mcp_a_stale_price_is_flagged_and_a_caller_cannot_declare_the_date(monkeypatch):
    _on(monkeypatch, DATE + timedelta(days=66))
    params = {"model": "openrouter/openai/gpt-5", "input_tokens": 1000, "output_tokens": 10}
    reply = invoke_reply("llm_router", "estimate_cost", json.dumps(params))
    assert reply["ok"] and reply["warning_card"]["code"] == env.STALE_PRICE and reply["result"]["stale_price"] is True
    for field in ("today", "prices_as_of", "stale_after_days"):
        refused = invoke_reply("llm_router", "estimate_cost", json.dumps({**params, field: "2026-08-17"}))
        assert refused["ok"] is False and refused["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


# ── LLM_ROUTER-04: the providers are g6.core.llm's ────────────────────────


def test_the_providers_are_those_g6_core_llm_builds():
    assert advice.KNOWN_PROVIDERS == llm.PROVIDERS == ("ollama", "anthropic", "openai", "openrouter")


def test_a_users_profile_for_a_provider_the_table_lacks_is_routed_and_called_through_g6_core_llm(key_file):
    key_file("ANTHROPIC_API_KEY=placeholder\n")
    reg = default_registry()
    reg.register_model(ModelProfile(model_id="anthropic/claude-opus-5-5", display_name="Claude Opus 5.5",
                                    provider="anthropic", family="claude", cost_tier=3,
                                    task_scores={"code_review": 0.99}, pricing_prompt_per_m=5.0,
                                    pricing_completion_per_m=25.0))
    found = advice.route("code_review", providers=["anthropic"], registry=reg)
    assert found.completion_state == "qualified-draft" and found.chain == ["anthropic/claude-opus-5-5"]
    caller = TaskRouter(["anthropic"], registry=reg, env={}).caller_for("code_review")
    inner = caller._callers[0]._inner
    assert isinstance(inner, llm.AnthropicCaller) and inner.model == "claude-opus-5-5"


def test_over_mcp_every_provider_g6_core_llm_builds_is_accepted():
    for provider in llm.PROVIDERS:
        reply = invoke_reply("llm_router", "route", json.dumps({"task": "code_review", "providers": [provider]}))
        assert reply["ok"], (provider, reply.get("error"))
    none = invoke_reply("llm_router", "route", json.dumps({"task": "code_review", "providers": ["openai"]}))
    assert none["result"]["code"] == env.NO_MODEL      # the committed table holds no OpenAI profile


def test_the_router_builds_each_caller_through_g6_core_llm_and_never_handles_a_key(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "provider_from_env", lambda env=None, **kw: seen.append((env, kw)) or llm.FakeLLM())
    import g6.llm_router.router as router

    monkeypatch.setattr(router, "provider_from_env", llm.provider_from_env)
    TaskRouter().caller_for("summarization")
    assert seen and all(env is None and set(kw) == {"provider", "model"} for env, kw in seen)
    assert all(kw["provider"] == "ollama" and "/" not in kw["model"] for _, kw in seen)


# ── G6 and G9: no key and no network module in the package ───────────────

NETWORK = ("socket", "urllib.request", "http.client", "requests", "httpx", "aiohttp", "urllib3", "websockets", "ftplib",
           "smtplib")
API_KEY = re.compile(r"[A-Z0-9_]*API_KEY")


def _modules(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            found += [(node.lineno, f"{node.module}.{a.name}") for a in node.names]
    return found


def _network_and_keys(text: str) -> list[str]:
    tree = ast.parse(text)
    found = [f"{line}: imports {name}" for line, name in _modules(tree)
             if any(name == m or name.startswith(m + ".") for m in NETWORK)]
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and API_KEY.fullmatch(node.value):
            found.append(f"{node.lineno}: names {node.value}")
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
            found.append(f"{node.lineno}: reads the environment ({node.attr})")
    return found


def test_the_package_imports_no_network_module_names_no_key_and_reads_no_environment():
    found = [f"{p.name}:{f}" for p in sorted(SRC.rglob("*.py")) for f in _network_and_keys(p.read_text("utf-8"))]
    assert found == []


@pytest.mark.parametrize("planted", ["import urllib.request\n", "from http.client import HTTPConnection\n",
                                     "import socket\n", "K = 'OPENROUTER_API_KEY'\n",
                                     "import os\nos.environ.get('X')\n", "import os\nos.getenv('X')\n"])
def test_the_scan_finds_what_it_looks_for(planted: str):
    assert _network_and_keys(planted) and _network_and_keys("import json\nx = 'API_KEYS are named here'\n") == []


PUBLIC = "104.18.2.115"


def _client(*replies: core_http.Response) -> tuple[core_http.Client, list[core_http.Request]]:
    sent: list[core_http.Request] = []

    def transport(request: core_http.Request) -> core_http.Response:
        sent.append(request)
        return replies[len(sent) - 1]

    return core_http.Client(resolver=lambda host, port: [PUBLIC], transport=transport, min_interval=0), sent


def _reply(status: int, body: object, headers: dict | None = None) -> core_http.Response:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return core_http.Response(status, headers or {}, raw, providers.OPENROUTER_MODELS_URL)


def test_the_catalogue_is_fetched_through_g6_core_http_with_a_size_cap_and_no_key(key_file):
    key_file("OPENROUTER_API_KEY=placeholder\n")
    client, sent = _client(_reply(200, {"data": [{"id": "openai/gpt-5", "pricing": {"prompt": "0.00000125",
                                                                                    "completion": "0.00001"}}]}))
    out = providers.fetch_openrouter_catalogue(client=client).model_dump(mode="json")
    assert out["completion_state"] == "qualified-draft" and out["count"] == 1
    assert (sent[0].method, sent[0].url, sent[0].ip) == ("GET", providers.OPENROUTER_MODELS_URL, PUBLIC)
    assert not {k.lower() for k in sent[0].headers} & {"authorization", "x-api-key"}
    assert providers.catalogue_client(15.0).max_bytes == providers.MAX_REPLY_BYTES


def test_a_redirect_to_this_machine_is_refused_before_it_is_followed():
    client, sent = _client(_reply(302, b"", {"location": "http://127.0.0.1:11434/api/tags"}))
    out = providers.fetch_openrouter_catalogue(client=client).model_dump(mode="json")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == core_http.REFUSED
    assert len(sent) == 1 and out["models"] == [] and check_envelope(out) == []


def test_a_403_is_an_error_never_an_empty_catalogue():
    client, _ = _client(_reply(403, {"error": "forbidden"}))
    out = providers.fetch_openrouter_catalogue(client=client).model_dump(mode="json")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == env.PROVIDER_ERROR
    assert "403" in out["reason"] and out["models"] == []


def test_the_ollama_ops_reach_only_the_service_the_package_declares():
    calls = []

    def call(program, method, path, **kw):  # noqa: ANN001, ANN202
        calls.append((program, method, path, kw))
        return outside.ServiceReply(200, {}, b'{"models": [], "status": "success"}')

    providers.list_local_models(call=call)
    providers.pull_model("qwen2.5:7b", call=call)
    providers.delete_model("qwen2.5:7b", call=call)
    assert [(c[0], c[1], c[2]) for c in calls] == [(providers.OLLAMA, "GET", "/api/tags"),
                                                   (providers.OLLAMA, "POST", "/api/pull"),
                                                   (providers.OLLAMA, "DELETE", "/api/delete")]
    assert all(c[3]["max_bytes"] == providers.MAX_REPLY_BYTES for c in calls)
    assert g6.llm_router.__g6_outside__ == (providers.OLLAMA,) and providers.OLLAMA.env == "OLLAMA_BASE_URL"


def _no_socket(monkeypatch: pytest.MonkeyPatch) -> list:
    tried: list = []

    def refuse(self, address):  # noqa: ANN001, ANN202
        tried.append(address)
        raise OSError("no network in this test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    return tried


def test_an_ollama_address_off_this_machine_is_refused_and_nothing_is_sent(monkeypatch):
    tried = _no_socket(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://169.254.169.254:80")
    for out in (providers.list_local_models(), providers.pull_model("a:1b"), providers.delete_model("a:1b")):
        payload = out.model_dump(mode="json")
        assert payload["completion_state"] == "blocked-escalated" and payload["code"] == outside.SERVICE_NOT_LOCAL
    assert tried == []


def test_with_nothing_answering_the_refusal_gives_the_install_steps(monkeypatch):
    with socket.socket() as probe:              # a port nothing listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("OLLAMA_BASE_URL", f"http://127.0.0.1:{port}")
    out = providers.list_local_models(timeout=5).model_dump(mode="json")
    assert out["code"] == outside.OUTSIDE_PROGRAM_MISSING and "ollama.com" in out["reason"] and out["models"] == []


def test_list_local_models_against_a_real_local_server(monkeypatch):
    class Tags(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"models": [{"name": "llama3.1:8b", "size": 1, "details": {}}]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: ANN002
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Tags)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("OLLAMA_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
        out = providers.list_local_models(timeout=5).model_dump(mode="json")
    finally:
        server.shutdown()
        server.server_close()
    assert out["completion_state"] == "qualified-draft" and out["installed"] == ["llama3.1:8b"]
    assert out["models"][0]["in_table"] is True


def test_a_caller_cannot_name_the_ollama_address():
    params = {"base_url": "http://169.254.169.254"}
    result = get_registry().invoke("llm_router", "list_local_models", params)
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION]")


# ── LLM_ROUTER-05: the block contract ────────────────────────────────────


def test_the_block_declares_its_contract_with_its_failure_modes_and_refusal_codes():
    contract = get_block_contract_attr(LLMRouterBlock)
    assert contract is not None and not contract.is_permissive_default()
    assert set(contract.mitigates_failure_modes) == set(g6.llm_router.__g6_mitigates__)
    assert {"unchosen_model", "stale_price"} <= set(contract.mitigates_failure_modes)
    assert {env.NO_MODEL, env.UNKNOWN_MODEL, env.PRICE_UNKNOWN, env.STALE_PRICE, env.NO_SUPERVISOR,
            env.PROVIDER_UNREACHABLE, env.PROVIDER_ERROR} <= set(contract.escalation_triggers)
    assert "verified" in contract.forbidden_claims and contract.known_failure_modes