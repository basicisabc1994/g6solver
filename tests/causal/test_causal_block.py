# SPDX-License-Identifier: Apache-2.0
"""g6.causal's block (owner, 2026-10-20): every op reachable through the registry and ``g6_invoke``, each reply inside
the library envelope and never ``verified``; unknown ops and unknown fields (``run_code``, a file ``path``) refused;
nothing runs caller code or reads a file; and the package imports without DoWhy, pandas or networkx, its graph ops
answering and estimation refusing honestly when DoWhy is absent."""
from __future__ import annotations

import ast
import importlib.util
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.causal
from g6.causal.causal_block import CausalBlock
from g6.causal.envelope import ENGINE_UNAVAILABLE, ESTIMATE, GRAPH_ANSWER, ONE_ENGINE
from g6.causal.schema import CausalResult
from g6.core import discovery
from g6.core.maturity import maturity_reason
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

HAS_DOWHY = importlib.util.find_spec("dowhy") is not None
SRC = Path(g6.causal.__file__).parent
CONFOUNDED = {"nodes": ["Z", "T", "Y"], "edges": [["Z", "T"], ["Z", "Y"], ["T", "Y"]]}
DOT = "digraph { U [observed=no]; W -> T; U -> T; U -> Y; T -> Y }"


def _rows(n: int = 400) -> list[dict]:
    rng = random.Random(3)
    rows = []
    for _ in range(n):
        z = rng.gauss(0, 1)
        t = 0.8 * z + rng.gauss(0, 1)
        rows.append({"Z": z, "T": t, "Y": 2.0 * t + 1.5 * z + rng.gauss(0, 1)})
    return rows


CALLS: dict[str, dict] = {
    "d_separated": {"graph": "digraph { A -> T; A -> M; B -> M; B -> Y; T -> Y }", "x": "A", "y": "B", "given": ["M"]},
    "backdoor_paths": {"graph": CONFOUNDED, "treatment": "T", "outcome": "Y"},
    "adjustment_sets": {"graph": CONFOUNDED, "treatment": "T", "outcome": "Y"},
    "instruments": {"graph": DOT, "treatment": "T", "outcome": "Y"},
    "identify": {"graph": DOT, "treatment": "T", "outcome": "Y"},
    "estimate_effect": {"graph": CONFOUNDED, "data": _rows(), "treatment": "T", "outcome": "Y", "simulations": 5},
}


@pytest.fixture
def no_dowhy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [m for m in sys.modules if m == "dowhy" or m.startswith("dowhy.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "dowhy", None)


def test_causal_is_registered_with_a_block_and_no_sensitive_op() -> None:
    assert get_registry().get_class("causal") is CausalBlock
    info = discovery.describe("causal")
    assert info["block"] == "CausalBlock" and info["maturity"] == "beta" and info["sensitive_ops"] == []
    assert info["requires"] == [] and info["ops"] == list(CALLS)
    assert info["summary"].endswith(".") and len(info["summary"]) <= 170
    words = ((g6.causal.__doc__ or "") + maturity_reason(CausalBlock)).lower()
    assert "experimental" not in words and "poetry" not in words and "run_code" in words  # says why it is not an op
    assert not hasattr(g6.causal, "__g6_family__")


def test_g6_find_ranks_causal_first_for_a_causal_question() -> None:
    assert discovery.find("which variables should I control for to estimate a causal effect")[0]["component"] == \
        "causal"


@pytest.mark.parametrize("op", list(CALLS))
def test_every_op_answers_through_g6_invoke_inside_the_envelope(op: str) -> None:
    if op == "estimate_effect" and not HAS_DOWHY:
        pytest.skip("DoWhy is not installed (pip install 'g6[causal] @ git+https://github.com/basicisabc1994/g6solver')")
    reply = invoke_reply("causal", op, json.dumps({**CALLS[op], "request_id": "req-1", "run_id": "run-1"}))
    assert check_envelope(reply) == [] and reply["ok"] is True, reply.get("error")
    result = reply["result"]
    assert check_envelope(result) == [] and (result["component"], result["op"]) == ("causal", op)
    assert result["completion_state"] == reply["completion_state"] == "qualified-draft", (result["code"],
                                                                                          result["reason"])
    expected = ESTIMATE if op == "estimate_effect" else GRAPH_ANSWER if HAS_DOWHY else ONE_ENGINE
    assert reply["warning_card"]["code"] == result["warning_card"]["code"] == expected
    assert (result["request_id"], result["run_id"]) == ("req-1", "run-1")


def test_the_answers_over_mcp_are_the_graphs() -> None:
    sets = invoke_reply("causal", "adjustment_sets", json.dumps(CALLS["adjustment_sets"]))["result"]
    assert sets["answer"]["sets"] == [["Z"]]
    found = invoke_reply("causal", "identify", json.dumps(CALLS["identify"]))["result"]
    assert found["answer"]["route"] == "iv" and found["answer"]["iv"]["instruments"] == ["W"]
    separated = invoke_reply("causal", "d_separated", json.dumps({**CALLS["d_separated"], "engine": "python"}))
    assert separated["result"]["answer"]["open_path"] == "A -> M <- B" and separated["result"]["engines"] == [
        "pure_python"]


@pytest.mark.parametrize("op", [o for o in CALLS if o != "estimate_effect"])
def test_without_dowhy_every_graph_op_still_answers_over_mcp(op: str, no_dowhy) -> None:
    reply = invoke_reply("causal", op, json.dumps(CALLS[op]))
    assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"
    assert reply["warning_card"]["code"] == ONE_ENGINE and "pip install 'g6[causal] @ git+https://github.com/basicisabc1994/g6solver'" in reply["warning_card"][
        "suggested_action"]


def test_without_dowhy_estimation_is_blocked_over_mcp_and_says_what_to_install(no_dowhy) -> None:
    reply = invoke_reply("causal", "estimate_effect", json.dumps(CALLS["estimate_effect"]))
    assert check_envelope(reply) == [] and reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == ENGINE_UNAVAILABLE
    assert "pip install 'g6[causal] @ git+https://github.com/basicisabc1994/g6solver'" in reply["warning_card"]["suggested_action"]
    assert reply["result"]["estimate"] is None and check_envelope(reply["result"]) == []


@pytest.mark.parametrize("op", ["ops", "help"])
def test_ops_and_help_answer_with_or_without_dowhy(op: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for blocked in (False, True):
        if blocked:
            monkeypatch.setitem(sys.modules, "dowhy", None)
        payload = get_registry().invoke("causal", op).unwrap()
        assert len(payload[op]) == len(CALLS)
        reply = invoke_reply("causal", op)
        assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"


@pytest.mark.parametrize(("op", "params"), [
    ("run_code", {"code": "import os"}),
    ("refute_estimate", {"graph": CONFOUNDED}),
    ("load_data", {"path": "data.csv"}),
    ("identify", {"graph": CONFOUNDED, "treatment": "T", "outcome": "Y", "code": "print(1)"}),
    ("estimate_effect", {"graph": CONFOUNDED, "treatment": "T", "outcome": "Y", "path": "rows.csv"}),
    ("estimate_effect", {"graph": CONFOUNDED, "treatment": "T", "outcome": "Y", "file": "rows.csv"}),
    ("estimate_effect", {"graph": CONFOUNDED, "treatment": "T", "outcome": "Y", "method": "backdoor.econml.dml.DML"}),
    ("estimate_effect", {"graph": CONFOUNDED, "treatment": "T", "outcome": "Y", "data": "rows.csv"}),
    ("identify", {"graph": CONFOUNDED, "treatment": "T", "outcome": "Y", "engine": "pydot"}),
    ("identify", {"graph": CONFOUNDED, "treatment": "T", "outcome": "Y", "time_limit": 0}),
])
def test_unknown_ops_unknown_fields_and_bad_parameters_are_refused(op: str, params: dict) -> None:
    result = get_registry().invoke("causal", op, params, block_sensitive=True)
    assert result.__class__.__name__ == "Failure"
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION] [CAUSAL_ERROR]")
    reply = invoke_reply("causal", op, json.dumps(params))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and check_envelope(reply) == []


def test_a_refused_graph_is_blocked_over_mcp_with_its_own_code() -> None:
    reply = invoke_reply("causal", "identify", json.dumps({"graph": "digraph { T -> Y -> T }", "treatment": "T",
                                                           "outcome": "Y"}))
    assert check_envelope(reply) == [] and reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_CAUSAL_GRAPH_CYCLE" and reply["result"]["answer"] == {}


def test_no_result_can_be_verified() -> None:
    with pytest.raises(ValidationError):
        CausalResult(operation="identify", completion_state="verified")
    for op, params in CALLS.items():
        if op == "estimate_effect" and not HAS_DOWHY:
            continue
        reply = invoke_reply("causal", op, json.dumps(params))
        assert reply["completion_state"] != "verified" and reply["result"]["completion_state"] != "verified"


_FORBIDDEN_MODULES = {"subprocess", "os", "io", "socket", "urllib", "http", "shutil", "tempfile", "glob",
                      "multiprocessing", "ctypes", "asyncio", "requests", "httpx", "pickle", "marshal", "pydot",
                      "pygraphviz", "g6.core.sandbox", "g6.core.llm", "g6.research"}
_FORBIDDEN_CALLS = {"exec", "eval", "compile", "__import__", "open", "breakpoint"}
_FILE_METHODS = {"open", "read_text", "read_bytes", "write_text", "write_bytes", "read_csv", "read_json",
                 "read_parquet", "read_excel", "to_csv", "to_json"}
_WORDS = ("exec(", "eval(", "subprocess", "os.system")


def _scan(text: str, name: str) -> list[str]:
    found = [f"{name}: {word}" for word in _WORDS if word in text]
    for node in ast.walk(ast.parse(text)):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        found += [f"{name}: import {n}" for n in names
                  if any(n == m or n.startswith(m + ".") for m in _FORBIDDEN_MODULES)]
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                found.append(f"{name}:{node.lineno}: {node.func.id}()")
            if isinstance(node.func, ast.Attribute) and node.func.attr in _FILE_METHODS:
                found.append(f"{name}:{node.lineno}: .{node.func.attr}()")
            if (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and str(node.args[0].value).split(".")[0] in {"os", "subprocess", "io", "pydot", "shutil"}):
                found.append(f"{name}:{node.lineno}: import_module({node.args[0].value!r})")
    return found


def test_the_package_runs_no_caller_code_reads_no_file_and_reaches_no_network() -> None:
    """Graphs are read by G6's own parser into data and handed to DoWhy as a networkx graph, never as text DoWhy would
    read as a path; rows arrive inline. The earlier version's run_code op executed caller Python; there is no such
    op."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        found += _scan(path.read_text(encoding="utf-8"), path.name)
    assert found == []


def test_the_guard_finds_what_it_looks_for() -> None:
    """Survival control: the scan above reports each planted call, so its empty answer means something."""
    planted = ("import subprocess\nimport importlib\nfrom pathlib import Path\nresult = eval('1')\n"
               "rows = Path('x').read_text()\nf = open('y')\nimportlib.import_module('os')\nos.system('dir')\n")
    found = _scan(planted, "planted.py")
    assert {"planted.py: eval(", "planted.py: subprocess", "planted.py: os.system", "planted.py: import subprocess",
            "planted.py:4: eval()", "planted.py:5: .read_text()", "planted.py:6: open()",
            "planted.py:7: import_module('os')"} <= set(found)


def test_every_file_is_apache_2() -> None:
    for path in sorted([*SRC.rglob("*.py"), *Path(__file__).parent.glob("*.py")]):
        assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name


def test_importing_the_package_loads_no_dowhy_pandas_or_networkx_and_raises_no_deprecation(tmp_path) -> None:
    subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", "import g6.causal"], cwd=tmp_path,
                   check=True)
    probe = ("import sys, g6.causal\n"
             "heavy = ('dowhy', 'pandas', 'networkx', 'numpy', 'scipy')\n"
             "heavy = [m for m in sys.modules if m.split('.')[0] in heavy]\n"
             "assert not heavy, heavy\n")
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)
    without = ("import sys\nsys.modules['dowhy'] = None\nimport g6.causal as c\n"
               "r = c.identify('digraph { Z -> T; Z -> Y; T -> Y }', 'T', 'Y')\n"
               "assert r.completion_state == 'qualified-draft' and r.engines == ['pure_python'], (r.code, r.reason)\n"
               "assert r.warning_card['code'] == 'G6_E_CAUSAL_ONE_ENGINE'\n"
               "rows = [{'Z': float(i % 7), 'T': float(i % 5), 'Y': float(i)} for i in range(40)]\n"
               "e = c.estimate_effect('digraph { Z -> T; Z -> Y; T -> Y }', rows, 'T', 'Y')\n"
               "assert e.code == 'G6_E_CAUSAL_ENGINE_UNAVAILABLE' and e.completion_state == 'blocked-escalated'\n")
    subprocess.run([sys.executable, "-c", without], cwd=tmp_path, check=True)


@pytest.mark.skipif(not HAS_DOWHY, reason="DoWhy is not installed (pip install 'g6[causal] @ git+https://github.com/basicisabc1994/g6solver')")
def test_graph_ops_with_dowhy_run_with_deprecation_warnings_as_errors(tmp_path) -> None:
    script = ("import g6.causal as c\n"
              "r = c.identify('digraph { U [observed=no]; W -> T; U -> T; U -> Y; T -> Y }', 'T', 'Y')\n"
              "assert r.completion_state == 'qualified-draft' and r.engines == ['pure_python', 'dowhy'], (r.code, "
              "r.reason)\n")
    done = subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", script], cwd=tmp_path,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
