# SPDX-License-Identifier: Apache-2.0
"""g6.llm_router's block: every op that reads only the committed table is reachable through the registry and
``g6_invoke`` inside the library envelope, never ``verified``; the ops that reach the network or change the machine are
refused over MCP and run from Python against a fake server; unknown ops and fields are refused; and the package runs
no code, reads no ``.env`` file, writes nothing into the environment and has no hardware-fit code."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

import g6.llm_router
from g6.core import discovery, http, outside
from g6.core.maturity import maturity_reason
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.llm_router import providers
from g6.llm_router.envelope import COMMITTED_TABLE, COST_ESTIMATE, LIVE_ANSWER, RECORDED_OUTCOMES
from g6.llm_router.llm_router_block import SENSITIVE_OPS, LLMRouterBlock
from g6.mcp import invoke_reply

SRC = Path(g6.llm_router.__file__).parent
CALLS: dict[str, tuple[dict, str]] = {
    "list_tasks": ({}, COMMITTED_TABLE),
    "models_for_task": ({"task": "code_review", "local_only": True, "limit": 3}, COMMITTED_TABLE),
    "route": ({"task": "summarization", "fallbacks": 2}, COMMITTED_TABLE),
    "supervision_chain": ({"task": "code_generation"}, COMMITTED_TABLE),
    "model_profile": ({"model": "ollama/qwen2.5:7b"}, COMMITTED_TABLE),
    "estimate_cost": ({"model": "openrouter/openai/gpt-5", "input_tokens": 2000, "output_tokens": 500}, COST_ESTIMATE),
    "record_outcome": ({"task": "summarization", "model": "ollama/qwen2.5:7b", "success": True}, RECORDED_OUTCOMES),
    "outcome_stats": ({"task": "summarization"}, RECORDED_OUTCOMES),
}
SENSITIVE: dict[str, dict] = {
    "list_local_models": {"timeout": 5},
    "pull_model": {"model": "qwen2.5:7b"},
    "delete_model": {"model": "qwen2.5:7b"},
    "fetch_openrouter_catalogue": {},
}


class FakeServer:
    """Stands in for Ollama (in place of ``g6.core.outside.call_service``) and for OpenRouter (the transport of the
    ``g6.core.http`` client the catalogue is fetched with)."""

    ROUTES = {("GET", "/api/tags"): {"models": [{"name": "qwen2.5:7b", "size": 1, "details": {}}]},
              ("POST", "/api/pull"): {"status": "success"}, ("DELETE", "/api/delete"): {},
              ("GET", "/api/v1/models"): {"data": [{"id": "openai/gpt-5",
                                                    "pricing": {"prompt": "0.00000125", "completion": "0.00001"}}]}}

    def __init__(self) -> None:
        self.requests: list = []

    def call(self, program, method, path, **kwargs):  # noqa: ANN001, ANN201
        self.requests.append((method, path))
        return outside.ServiceReply(200, {}, json.dumps(self.ROUTES[(method, path)]).encode())

    def transport(self, request: http.Request) -> http.Response:
        self.requests.append((request.method, request.url))
        body = json.dumps(self.ROUTES[(request.method, "/api/v1/models")]).encode()
        return http.Response(200, {}, body, request.url)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    fake = FakeServer()
    monkeypatch.setattr(outside, "call_service", fake.call)
    monkeypatch.setattr(providers, "catalogue_client", lambda timeout: http.Client(
        resolver=lambda host, port: ["104.18.2.115"], transport=fake.transport, min_interval=0))
    return fake


def test_llm_router_is_registered_as_an_integrations_extension_with_its_sensitive_ops():
    assert get_registry().get_class("llm_router") is LLMRouterBlock
    assert g6.llm_router.__g6_component__ is True and g6.llm_router.__g6_family__ == "integrations"
    info = discovery.describe("llm_router")
    assert info["block"] == "LLMRouterBlock" and info["maturity"] == "beta" and info["requires"] == []
    assert info["sensitive_ops"] == sorted(SENSITIVE) == sorted(g6.llm_router.__g6_sensitive_ops__)
    assert set(SENSITIVE_OPS) == set(SENSITIVE) and sorted(info["ops"]) == sorted([*CALLS, *SENSITIVE])
    assert info["summary"].endswith(".") and len(info["summary"]) <= 170
    words = ((g6.llm_router.__doc__ or "") + maturity_reason(LLMRouterBlock)).lower()
    assert "experimental" not in words and maturity_reason(LLMRouterBlock)


@pytest.mark.parametrize("op", list(CALLS))
def test_every_table_op_answers_through_g6_invoke_inside_the_envelope(op: str, server: FakeServer):
    params, card = CALLS[op]
    reply = invoke_reply("llm_router", op, json.dumps({**params, "request_id": "req-1", "run_id": "run-1"}))
    assert check_envelope(reply) == [] and reply["ok"] is True, reply.get("error")
    result = reply["result"]
    assert check_envelope(result) == [] and (result["component"], result["op"]) == ("llm_router", op)
    assert result["completion_state"] == reply["completion_state"] == "qualified-draft", result["reason"]
    assert reply["warning_card"]["code"] == result["warning_card"]["code"] == card
    assert (result["request_id"], result["run_id"]) == ("req-1", "run-1")
    assert result["evidence"][0]["network"] is False and server.requests == []   # read only the committed table


def test_route_over_mcp_is_local_first_by_default():
    result = invoke_reply("llm_router", "route", json.dumps({"task": "code_generation"}))["result"]
    assert result["providers"] == ["ollama"] and all(m.startswith("ollama/") for m in result["chain"])
    wider = invoke_reply("llm_router", "route", json.dumps({"task": "code_generation", "providers":
                                                             ["openrouter"], "fallbacks": 1}))["result"]
    assert wider["chain"] and all(m.startswith("openrouter/") for m in wider["chain"])


def test_a_blocked_answer_over_mcp_keeps_its_own_code():
    reply = invoke_reply("llm_router", "model_profile", json.dumps({"model": "openrouter/acme/none"}))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_ROUTER_UNKNOWN_MODEL" and check_envelope(reply) == []


@pytest.mark.parametrize("op", ["ops", "help"])
def test_ops_and_help_answer(op: str):
    payload = get_registry().invoke("llm_router", op).unwrap()
    assert len(payload[op]) == len(CALLS) + len(SENSITIVE)
    reply = invoke_reply("llm_router", op)
    assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"
    if op == "help":
        marked = [line for line in payload["help"] if "G6_MCP_SENSITIVE" in line]
        assert len(marked) == len(SENSITIVE) and all(line.split("(")[0] in SENSITIVE for line in marked)
        assert not any("Python only" in line for line in payload["help"])


@pytest.mark.parametrize("op", list(SENSITIVE))
def test_a_sensitive_op_is_refused_over_mcp_and_reaches_nothing(op: str, server: FakeServer):
    reply = invoke_reply("llm_router", op, json.dumps(SENSITIVE[op]))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated" and check_envelope(reply) == []
    assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED" and server.requests == []


@pytest.mark.parametrize("op", list(SENSITIVE))
def test_a_sensitive_op_runs_from_python_against_a_fake_server(op: str, server: FakeServer):
    payload = get_registry().invoke("llm_router", op, SENSITIVE[op]).unwrap()
    assert check_envelope(payload) == [] and payload["completion_state"] == "qualified-draft", payload["reason"]
    assert payload["warning_card"]["code"] == LIVE_ANSWER and len(server.requests) == 1


@pytest.mark.parametrize(("op", "params"), [
    ("run_code", {"code": "import os"}),
    ("detect_hardware", {}),
    ("fit_model", {"model": "ollama/qwen2.5:7b"}),
    ("route_completion", {"prompt": "hi"}),
    ("system_status", {}),
    ("route", {"task": "summarization", "code": "print(1)"}),
    ("route", {"task": "astrology"}),
    ("route", {"task": "summarization", "providers": ["acme"]}),
    ("route", {"task": "summarization", "fallbacks": 99}),
    ("route", {"task": "summarization", "providers": []}),   # allowing nobody is refused, not read as the default
    ("route", {}),
    ("models_for_task", {"task": "summarization", "min_score": 2}),
    ("model_profile", {}),
    ("estimate_cost", {"model": "ollama/qwen2.5:7b", "input_tokens": -1}),
    ("estimate_cost", {"model": "ollama/qwen2.5:7b", "input_tokens": 1.5}),
])
def test_unknown_ops_unknown_fields_and_bad_parameters_are_refused(op: str, params: dict):
    result = get_registry().invoke("llm_router", op, params, block_sensitive=True)
    assert result.__class__.__name__ == "Failure"
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION] [LLM_ROUTER_ERROR]")
    reply = invoke_reply("llm_router", op, json.dumps(params))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and check_envelope(reply) == []


@pytest.mark.parametrize("params", [{"model": "a b"}, {"model": "qwen2.5:7b", "base_url": "http://127.0.0.1:1"}, {},
                                    {"model": "qwen2.5:7b", "timeout": -1}])
def test_a_sensitive_op_with_bad_fields_is_refused_from_python_too(params: dict, server: FakeServer):
    result = get_registry().invoke("llm_router", "pull_model", params)
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION]") and server.requests == []


def test_no_result_is_verified_on_any_op(server: FakeServer):
    for op, (params, _) in CALLS.items():
        assert get_registry().invoke("llm_router", op, params).unwrap()["completion_state"] != "verified"
    for op, params in SENSITIVE.items():
        assert get_registry().invoke("llm_router", op, params).unwrap()["completion_state"] != "verified"


# ── what the package must never do ───────────────────────────────────────

_FORBIDDEN_MODULES = {"subprocess", "socket", "shutil", "multiprocessing", "ctypes", "asyncio", "pickle", "marshal",
                      "requests", "httpx", "litellm", "psutil", "GPUtil", "dotenv", "fastmcp", "llmfit",
                      "urllib.request", "http.client", "g6.research", "g6.core.sandbox"}
_FORBIDDEN_CALLS = {"exec", "eval", "compile", "__import__", "open", "breakpoint"}
_ENV_WRITES = {"setdefault", "update", "pop", "popitem", "clear", "__setitem__", "__delitem__"}


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _code_findings(text: str) -> list[str]:
    """Running code, shelling out, or importing what does either (or a hardware probe, or an HTTP library)."""
    found = [word for word in ("exec(", "eval(", "subprocess", "os.system", "os.popen") if word in text]
    for node in ast.walk(ast.parse(text)):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        found += [f"import {n}" for n in names if any(n == m or n.startswith(m + ".") for m in _FORBIDDEN_MODULES)]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
            found.append(f"{node.lineno}: {node.func.id}()")
    return found


def _env_findings(text: str) -> list[str]:
    """Naming a .env file, or changing the process environment."""
    found = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip().replace("\\", "/").split("/")[-1] == ".env" or "dotenv" in node.value:
                found.append(f"{node.lineno}: names a .env file")
        if isinstance(node, ast.Attribute) and node.attr in ("putenv", "unsetenv"):
            found.append(f"{node.lineno}: os.{node.attr}")
        if (isinstance(node, ast.Attribute) and node.attr in _ENV_WRITES and isinstance(node.value, ast.Attribute)
                and node.value.attr == "environ"):
            found.append(f"{node.lineno}: os.environ.{node.attr}")
        targets = (node.targets if isinstance(node, ast.Assign | ast.Delete)
                   else [node.target] if isinstance(node, ast.AugAssign | ast.AnnAssign) else [])
        found += [f"{node.lineno}: writes os.environ" for target in targets if isinstance(target, ast.Subscript)
                  and isinstance(target.value, ast.Attribute) and target.value.attr == "environ"]
    return found


def test_the_package_runs_no_code_and_shells_out_to_nothing():
    assert [f"{p.name}: {f}" for p in _sources() for f in _code_findings(p.read_text(encoding="utf-8"))] == []


def test_the_package_never_reads_a_dot_env_file_and_never_writes_the_environment():
    assert [f"{p.name}: {f}" for p in _sources() for f in _env_findings(p.read_text(encoding="utf-8"))] == []


@pytest.mark.parametrize(("scan", "planted"), [
    (_code_findings, "import subprocess\n"), (_code_findings, "x = eval('1')\n"), (_code_findings, "exec('x')\n"),
    (_code_findings, "import requests\n"), (_code_findings, "from llmfit.fit import recommend\n"),
    (_code_findings, "import urllib.request\n"), (_code_findings, "from http.client import HTTPConnection\n"),
    (_code_findings, "import psutil\n"), (_code_findings, "f = open('x')\n"), (_code_findings, "import os\nos.system('x')\n"),
    (_env_findings, "from pathlib import Path\nPath('.env').read_text()\n"), (_env_findings, "import dotenv\n'dotenv'\n"),
    (_env_findings, "import os\nos.environ['K'] = 'v'\n"), (_env_findings, "import os\nos.environ.setdefault('K', 'v')\n"),
    (_env_findings, "import os\nos.putenv('K', 'v')\n"), (_env_findings, "import os\ndel os.environ['K']\n"),
])
def test_the_scans_fire_on_what_they_look_for(scan, planted: str):
    # a survival control: each scan finds its planted line, and a clean line gives nothing, so a pass is not a blind scan
    assert scan(planted)
    assert scan("import os\nkey = os.environ.get('OPENROUTER_API_KEY')\n") == []


def test_no_hardware_fit_code_or_hosted_service_code_is_ported():
    text = "\n".join(p.read_text(encoding="utf-8") for p in _sources()).lower()
    for word in ("psutil", "gputil", "nvidia-smi", "wmic", "detect_hardware", "fit_model", "best_quant",
                 "estimate_memory", "key_custody", "spend_policy", "byo_", "tier cap", "claude -p", "bonsai_"):
        assert word not in text, word
    assert not list(SRC.rglob("llmfit*")) and not list(SRC.rglob("*.yaml"))


def test_every_file_is_apache_2_and_no_source_file_keeps_the_earlier_licence_line():
    for path in [*_sources(), *Path(__file__).parent.glob("*.py")]:
        assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name
    for path in [*_sources(), SRC / "data" / "model_profiles.json"]:
        text = path.read_text(encoding="utf-8").lower()
        assert "proprietary" not in text and "all rights reserved" not in text, path.name


def test_the_committed_data_ships_inside_the_package():
    assert (SRC / "data" / "model_profiles.json").is_file()
    assert [p.name for p in (SRC / "data").iterdir() if p.is_file()] == ["model_profiles.json"]


def test_importing_the_package_raises_no_deprecation_and_reaches_no_network(tmp_path):
    subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", "import g6.llm_router"], cwd=tmp_path,
                   check=True)
    probe = ("import socket\n"
             "def refuse(*a, **k):\n    raise AssertionError('importing g6.llm_router reached the network')\n"
             "socket.socket.connect = refuse\n"
             "import g6.llm_router as r\n"
             "assert r.advice.route('summarization').completion_state == 'qualified-draft'\n")
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)
