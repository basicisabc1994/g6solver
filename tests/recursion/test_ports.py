# SPDX-License-Identifier: Apache-2.0
"""Caller wire format and trusted tool adapters, with no external services."""
from __future__ import annotations

import importlib
import json
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from g6.core.llm import OpenAICompatibleCaller


@pytest.fixture
def api():
    class API:
        def __getattr__(self, name):
            return getattr(importlib.import_module("g6.recursion.ports"), name)

    return API()


def test_openai_subclass_sends_tools_effort_and_seed_to_loopback(api):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "done"},
                                           "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        caller = api.ActorCaller(base_url=f"http://127.0.0.1:{server.server_port}/v1")
        assert isinstance(caller, OpenAICompatibleCaller)
        request = {"model": "gpt-oss:20b", "messages": [{"role": "user", "content": "x"}],
                   "tools": [], "reasoning_effort": "low", "temperature": 0, "seed": 3, "max_tokens": 32}
        assert caller.complete_request(request)["choices"][0]["message"]["content"] == "done"
        assert seen == [("/v1/chat/completions", request)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_default_tool_registry_uses_existing_public_engines(api, monkeypatch):
    from g6.formal import facade

    seen = []
    monkeypatch.setattr(facade, "solve", lambda query: seen.append(query) or {"answer": "fixture"})
    tools = api.ToolPort.standard()
    assert {"run_tests", "formal", "contract", "datalog", "cegis", "check_plan"} == set(tools.names)
    assert tools.invoke("formal", {"query": {"formula": "true"}}) == {"answer": "fixture"}
    assert seen == [{"formula": "true"}]


def test_procedure_version_is_read_as_spec_data(api):
    spec = api.ProcedureSpec.from_content('{"tools": [], "steps": [{"op": "actor", "prompt": "Solve."}]}')
    assert spec.steps[0].op == "actor"
    with pytest.raises(ValueError):
        api.ProcedureSpec.from_content('import os\nos.open("hidden_ref")')


def test_tools_reject_extra_fields_and_keep_engine_refusals(api):
    tools = api.ToolPort.standard()
    with pytest.raises(api.ActorError, match="G6_E_RSI_TOOL_CALL_INVALID"):
        tools.invoke("formal", {"query": {}, "path": "hidden_ref"})
    with pytest.raises(api.ActorError, match="G6_E_RSI_TOOL_FAILED"):
        tools.invoke("formal", {"query": {"formula": "", "solver": "not-an-engine"}})


def test_cegis_adapter_uses_public_table_oracle(api, monkeypatch):
    from g6.cegis import loop

    def synthesize(sketch, oracle, *, domain, **kwargs):
        assert oracle(x=1) == 3
        assert domain.inputs() == [{"x": 1}]
        assert kwargs == {"max_iterations": 2, "timeout_s": 1}

        class Answer:
            def to_envelope(self):
                return {"completion_state": "qualified-draft", "program": "fixture"}

        return Answer()

    monkeypatch.setattr(loop, "cegis", synthesize)
    assert api.ToolPort.standard().invoke("cegis", {
        "source": "def f(x):\n    return x + ??\n", "examples": [{"inputs": {"x": 1}, "output": 3}],
        "domain": {"x": [1]}, "max_iterations": 2, "timeout_s": 1})["program"] == "fixture"


def test_plan_adapter_preserves_static_report(api, monkeypatch):
    from g6.navigator import plan

    class Report:
        def to_dict(self):
            return {"completion_state": "qualified-draft", "valid": True}

    monkeypatch.setattr(plan, "check_plan", lambda steps: Report())
    assert api.ToolPort.standard().invoke("check_plan", {"steps": []})["valid"] is True


@pytest.mark.parametrize("result", [{"verdict": "failed", "passed": 0, "failed": 1},
                                    {"verdict": "tests_did_not_run", "passed": 0, "failed": 0}])
def test_public_test_adapter_keeps_runner_failures(api, monkeypatch, result):
    from g6.recursion import tasks
    from g6.recursion.tasks import CheckSpec, TaskSpec, licence_for

    repo = str(Path(__file__).resolve().parents[2])
    task = TaskSpec("apps:tool", "arithmetic", "apps", licence_for("apps"), "Add two.",
                    CheckSpec("stdin_stdout", inputs=("1\n",), outputs=("3\n",)),
                    str(Path(repo).parent / "evaluator-only" / "reference.py"), "", "", "train",
                    source_split="train", train_ok=True)
    seen = []

    def checks(visible, solution, **kwargs):
        seen.append(visible)
        assert visible.hidden_ref == ""
        assert solution == "print(3)" and kwargs == {"repo": repo}
        return result

    monkeypatch.setattr(tasks, "run_checks", checks)
    tools = api.ToolPort.standard(task=task, repo=repo)
    with pytest.raises(api.ActorError, match="G6_E_RSI_TOOL_FAILED"):
        tools.invoke("run_tests", {"solution": "print(3)"})
    assert len(seen) == 1


@pytest.mark.parametrize("reference", ["opaque-reference", "inside-repository"])
def test_test_tool_refuses_a_reference_it_cannot_exclude_from_mount(api, monkeypatch, reference):
    from g6.recursion import tasks
    from g6.recursion.tasks import CheckSpec, TaskSpec, licence_for

    repo = Path(__file__).resolve().parents[2]
    hidden = str(repo / "private-reference.py") if reference == "inside-repository" else reference
    task = TaskSpec("apps:mount", "arithmetic", "apps", licence_for("apps"), "Add two.",
                    CheckSpec("stdin_stdout", inputs=("1\n",), outputs=("3\n",)),
                    hidden, "", "", "train", source_split="train", train_ok=True)
    calls = []
    monkeypatch.setattr(tasks, "run_checks", lambda *a, **kw: calls.append(a) or {"verdict": "passed"})
    tools = api.ToolPort.standard(task=task, repo=str(repo))
    with pytest.raises(api.ActorError, match="G6_E_RSI_TOOL_FAILED"):
        tools.invoke("run_tests", {"solution": "print(3)"})
    assert calls == []


def test_test_tool_requires_original_boundary_check_not_a_public_projection(api, monkeypatch):
    from g6.recursion import tasks
    from g6.recursion.tasks import CheckSpec, TaskSpec, licence_for

    task = TaskSpec("apps:projection", "arithmetic", "apps", licence_for("apps"), "Add two.",
                    CheckSpec("stdin_stdout", inputs=("1\n",), outputs=("3\n",)),
                    "PRIVATE_REFERENCE", "", "", "train", source_split="train", train_ok=True)
    calls = []
    monkeypatch.setattr(tasks, "run_checks", lambda *a, **kw: calls.append(a) or {"verdict": "passed"})
    tools = api.ToolPort.standard(task=api.public_task(task), repo=str(Path(__file__).resolve().parents[2]))
    with pytest.raises(api.ActorError, match="G6_E_RSI_TOOL_FAILED"):
        tools.invoke("run_tests", {"solution": "print(3)"})
    assert calls == []


def test_reference_in_runner_interpreter_mount_is_refused(api, monkeypatch):
    from g6.core import confine
    from g6.recursion import tasks
    from g6.recursion.tasks import CheckSpec, TaskSpec, licence_for
    from g6.workforce import repo_tests

    repo = Path(__file__).resolve().parents[2]
    extra = repo.parent / "public-interpreter"
    task = TaskSpec("apps:interpreter", "arithmetic", "apps", licence_for("apps"), "Add two.",
                    CheckSpec("stdin_stdout", inputs=("1\n",), outputs=("3\n",)),
                    str(extra / "private.py"), "", "", "train", source_split="train", train_ok=True)
    calls = []
    monkeypatch.setattr(repo_tests, "interpreter", lambda root: "fixture-python")
    monkeypatch.setattr(repo_tests, "_environment_of", lambda python: ())
    monkeypatch.setattr(confine, "interpreter_paths", lambda python: (str(extra),))
    monkeypatch.setattr(tasks, "run_checks", lambda *a, **kw: calls.append(a) or {"verdict": "passed"})
    tools = api.ToolPort.standard(task=task, repo=str(repo))
    with pytest.raises(api.ActorError, match="G6_E_RSI_TOOL_FAILED"):
        tools.invoke("run_tests", {"solution": "print(3)"})
    assert calls == []
