# SPDX-License-Identifier: Apache-2.0
"""g6.graph_reasoning's block (owner, 2026-10-20): every op reachable through the registry and ``g6_invoke``, each
reply inside the library envelope and never ``verified``; unknown ops and unknown fields refused; nothing runs caller
code or reads a file; and importing the package loads neither PyReason, numba nor networkx."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

import g6.graph_reasoning
from g6.core import discovery
from g6.core.maturity import maturity_reason
from g6.core.registry import get_registry
from g6.graph_reasoning import pyreason_engine
from g6.graph_reasoning.envelope import CONFLICT, NOT_EVALUATED, ONE_ENGINE, PARSE_ERROR
from g6.graph_reasoning.graph_reasoning_block import GraphReasoningBlock
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

SRC = Path(g6.graph_reasoning.__file__).parent
SOCIAL = ("friend(alice, bob). friend(bob, carol). friend(carol, dave) : [0.9, 1].\npopular(dave).\n"
          "popular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].\n")
CALLS: dict[str, dict] = {
    "reason": {"program": SOCIAL},
    "query": {"program": SOCIAL, "atom": "popular(alice)"},
    "explain": {"program": SOCIAL, "atom": "popular(alice)"},
    "check": {"program": SOCIAL, "facts": [{"label": "popular", "node": "eve", "bound": [0, 0.2]}]},
}


@pytest.fixture(autouse=True)
def _one_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever is installed, these tests see the pure-Python engine alone; tests/graph_reasoning/test_pyreason.py
    covers PyReason."""
    monkeypatch.setattr(pyreason_engine, "unavailable_reason", lambda: "PyReason is not installed here")


def test_graph_reasoning_is_registered_with_a_block_and_no_sensitive_op() -> None:
    assert get_registry().get_class("graph_reasoning") is GraphReasoningBlock
    info = discovery.describe("graph_reasoning")
    assert info["block"] == "GraphReasoningBlock" and info["maturity"] == "beta" and info["sensitive_ops"] == []
    assert info["requires"] == [] and info["ops"] == list(CALLS)
    assert info["summary"].endswith(".") and len(info["summary"]) <= 170, len(info["summary"])
    words = ((g6.graph_reasoning.__doc__ or "") + maturity_reason(GraphReasoningBlock)).lower()
    assert "experimental" not in words and "poetry" not in words and "pkg_resources" in words  # says why not used
    assert not hasattr(g6.graph_reasoning, "__g6_family__")


def test_g6_find_ranks_graph_reasoning_first_for_a_graph_reasoning_task() -> None:
    found = discovery.find("truth intervals for labels on graph nodes and edges from annotated rules like pyreason")
    assert found[0]["component"] == "graph_reasoning", found


@pytest.mark.parametrize("op", list(CALLS))
def test_every_op_answers_through_g6_invoke_inside_the_envelope(op: str) -> None:
    reply = invoke_reply("graph_reasoning", op, json.dumps({**CALLS[op], "request_id": "req-1", "run_id": "run-1"}))
    assert check_envelope(reply) == [] and reply["ok"] is True, reply.get("error")
    result = reply["result"]
    assert check_envelope(result) == [] and (result["component"], result["op"]) == ("graph_reasoning", op)
    assert result["completion_state"] == reply["completion_state"] == "qualified-draft", (result["code"],
                                                                                          result["reason"])
    expected = NOT_EVALUATED if op == "check" else ONE_ENGINE
    assert reply["warning_card"]["code"] == result["warning_card"]["code"] == expected
    assert (result["request_id"], result["run_id"]) == ("req-1", "run-1")
    if op != "check":
        assert result["check"]["passed"] is True and result["evidence"][0]["check"]["passed"] is True


def test_the_answers_over_mcp_are_the_models() -> None:
    reason = invoke_reply("graph_reasoning", "reason", json.dumps(CALLS["reason"]))["result"]
    assert {r["atom"]: r["bounds"] for r in reason["interpretation"]}["popular(alice)"] == [0.7, 1.0]
    query = invoke_reply("graph_reasoning", "query", json.dumps(CALLS["query"]))["result"]
    assert query["answer"]["status"] == "between" and query["answer"]["derivation"]["by"][0]["by"] == "rule"
    explain = invoke_reply("graph_reasoning", "explain", json.dumps(CALLS["explain"]))["result"]
    assert explain["explanation"]["bounds"] == [0.7, 1.0] and not explain["explanation"]["truncated"]
    check = invoke_reply("graph_reasoning", "check", json.dumps(CALLS["check"]))["result"]
    assert check["report"]["atoms_given"] == 5 and check["report"]["labels"]["popular"]["recursive"] is True


@pytest.mark.parametrize("op", ["ops", "help"])
def test_ops_and_help_answer(op: str) -> None:
    payload = get_registry().invoke("graph_reasoning", op).unwrap()
    assert len(payload[op]) == len(CALLS)
    reply = invoke_reply("graph_reasoning", op)
    assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"


@pytest.mark.parametrize(("params", "code", "says"), [
    ({"program": "popular(alice"}, PARSE_ERROR, "line 1, column 14"),
    ({"program": "trusted(a) : [0.8, 1]. flagged(a).\ntrusted(x) : [0, 0.3] <- flagged(x)."}, CONFLICT, "empty"),
])
def test_a_refused_model_is_blocked_over_mcp_with_what_went_wrong(params: dict, code: str, says: str) -> None:
    reply = invoke_reply("graph_reasoning", "reason", json.dumps(params))
    assert check_envelope(reply) == [] and reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == code and says in reply["result"]["reason"]
    assert reply["result"]["interpretation"] == []


@pytest.mark.parametrize(("op", "params"), [
    ("run_code", {"code": "import os"}),
    ("add_rule", {"rule": "p(x) <- q(x)."}),
    ("temporal_reason", {"program": SOCIAL}),
    ("reason", {"program": SOCIAL, "run_code": "print(1)"}),
    ("reason", {"program": SOCIAL, "path": "graph.graphml"}),
    ("reason", {"program": SOCIAL, "file": "rules.txt"}),
    ("reason", {"program": SOCIAL, "timesteps": 3}),
    ("reason", {"program": SOCIAL, "engine": "numba"}),
    ("reason", {"program": SOCIAL, "time_limit": 0}),
    ("query", {"program": SOCIAL, "atom": ["popular(alice)"]}),
])
def test_unknown_ops_unknown_fields_and_bad_parameters_are_refused(op: str, params: dict) -> None:
    result = get_registry().invoke("graph_reasoning", op, params, block_sensitive=True)
    assert result.__class__.__name__ == "Failure"
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION] [GRAPH_REASONING_ERROR]")
    reply = invoke_reply("graph_reasoning", op, json.dumps(params))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and check_envelope(reply) == []


def test_no_reply_is_verified() -> None:
    for op, params in CALLS.items():
        reply = invoke_reply("graph_reasoning", op, json.dumps(params))
        assert reply["completion_state"] != "verified" and reply["result"]["completion_state"] != "verified"


_WORDS = ("exec(", "eval(", "subprocess", "os.system")
_FORBIDDEN_MODULES = {"subprocess", "os", "socket", "urllib", "http", "shutil", "multiprocessing", "ctypes", "asyncio",
                      "requests", "httpx", "pickle", "marshal", "pathlib", "tempfile", "glob", "g6.core.sandbox",
                      "g6.core.llm", "g6.research"}
_FORBIDDEN_CALLS = {"exec", "eval", "compile", "__import__", "open", "breakpoint", "input"}
_FILE_METHODS = {"read_text", "read_bytes", "write_text", "write_bytes", "load_graphml", "add_rules_from_file",
                 "add_fact_from_json", "add_fact_from_csv", "add_rule_from_json", "add_rule_from_csv", "save_rule_trace",
                 "load_inconsistent_predicate_list"}


def _scan(text: str, name: str) -> list[str]:
    found = [f"{name}: {word}" for word in _WORDS if word in text]
    for node in ast.walk(ast.parse(text)):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        found += [f"{name}: import {n}" for n in names
                  if any(n == m or n.startswith(m + ".") for m in _FORBIDDEN_MODULES)]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
            found.append(f"{name}:{node.lineno}: {node.func.id}()")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _FILE_METHODS:
            found.append(f"{name}:{node.lineno}: .{node.func.attr}()")
    return found


def test_the_package_runs_no_caller_code_reads_no_file_and_reaches_no_network() -> None:
    """Models are read by G6's own parser into data, and PyReason is handed a graph object and text G6 writes, never a
    file; nothing a caller sends is evaluated. The earlier version's run_code op executed caller Python; there is no
    such op here."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        found += _scan(path.read_text(encoding="utf-8"), path.name)
    assert found == []


def test_the_guard_finds_what_it_looks_for() -> None:
    """Survival control: the scan above reports each planted kind, so its empty answer means something."""
    planted = ("import subprocess\nimport pathlib\nresult = eval('1')\nopen('x')\n"
               "Path('x').read_text()\npr.load_graphml('g.graphml')\n")
    found = _scan(planted, "planted.py")
    assert any("eval(" in f for f in found) and any("subprocess" in f for f in found)
    assert any("import pathlib" in f for f in found) and any("open()" in f for f in found)
    assert any(".read_text()" in f for f in found) and any(".load_graphml()" in f for f in found)


def test_every_file_is_apache_2() -> None:
    for path in sorted([*SRC.rglob("*.py"), *Path(__file__).parent.glob("*.py")]):
        assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name


def test_importing_the_package_loads_no_pyreason_numba_or_networkx_and_raises_no_deprecation(tmp_path) -> None:
    subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", "import g6.graph_reasoning"],
                   cwd=tmp_path, check=True)
    probe = ("import sys, g6.graph_reasoning\n"
             "loaded = [m for m in ('pyreason', 'numba', 'networkx') if m in sys.modules]\n"
             "assert not loaded, loaded\n")
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)
    without = ("import sys\nsys.modules['pyreason'] = None\nimport g6.graph_reasoning as g\n"
               "r = g.reason('p(a). q(x) <- p(x).')\n"
               "assert r.completion_state == 'qualified-draft' and r.engines == ['pure_python'], (r.code, r.reason)\n"
               "assert r.warning_card['code'] == 'G6_E_GRAPH_REASONING_ONE_ENGINE'\n"
               "z = g.reason('p(a). q(x) <- p(x).', engine='pyreason')\n"
               "assert z.code == 'G6_E_GRAPH_REASONING_ENGINE_UNAVAILABLE' and z.completion_state == "
               "'blocked-escalated'\n")
    subprocess.run([sys.executable, "-c", without], cwd=tmp_path, check=True)
