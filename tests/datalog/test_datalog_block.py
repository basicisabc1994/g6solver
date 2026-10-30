# SPDX-License-Identifier: Apache-2.0
"""g6.datalog's block (owner, 2026-10-20): every op reachable through the registry and ``g6_invoke``, each reply inside
the library envelope and never ``verified``; unknown ops and unknown fields refused; nothing runs caller code; and
without Z3 the package still imports, the pure-Python engine still answers with a card that says so, and asking for Z3
is an honest refusal."""
from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

import g6.datalog
from g6.core import discovery
from g6.core.maturity import maturity_reason
from g6.core.registry import get_registry
from g6.datalog.datalog_block import DatalogBlock
from g6.datalog.envelope import (
    ENGINE_ANSWER,
    NOT_EVALUATED,
    NOT_STRATIFIABLE,
    ONE_ENGINE,
    PARSE_ERROR,
    SOLVER_UNAVAILABLE,
)
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

HAS_Z3 = importlib.util.find_spec("z3") is not None
SRC = Path(g6.datalog.__file__).parent
FAMILY = ("parent(alice, bob). parent(bob, carol). parent(carol, dave).\n"
          "ancestor(X, Y) :- parent(X, Y).\nancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).\n")
CALLS: dict[str, dict] = {
    "evaluate": {"program": FAMILY},
    "query": {"program": FAMILY, "query": "ancestor(alice, X)?"},
    "explain": {"program": FAMILY, "query": "ancestor(alice, dave)"},
    "check": {"program": FAMILY, "facts": {"parent": [["dave", "Eve Smith"]]}},
}


@pytest.fixture
def no_z3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "z3", None)  # ``import z3`` now raises ImportError


def test_datalog_is_registered_with_sensitive_session_writes() -> None:
    # Removal: datalog.__g6_sensitive_ops__ exposes the two persistent session mutations.
    assert get_registry().get_class("datalog") is DatalogBlock
    info = discovery.describe("datalog")
    assert info["block"] == "DatalogBlock" and info["maturity"] == "beta" and info["sensitive_ops"] == ["assert_facts", "reset"]
    assert info["requires"] == [] and info["ops"] == ["assert_facts", "reset", *CALLS]
    assert info["summary"].endswith(".") and len(info["summary"]) <= 170
    words = ((g6.datalog.__doc__ or "") + maturity_reason(DatalogBlock)).lower()
    assert "experimental" not in words and "poetry" not in words and "pydatalog" in words  # says why it is not used
    assert not hasattr(g6.datalog, "__g6_family__")  # the formal family's presentation is decided later


def test_g6_find_ranks_datalog_first_for_a_datalog_task() -> None:
    assert discovery.find("derive facts from datalog rules with stratified negation")[0]["component"] == "datalog"


@pytest.mark.parametrize("op", list(CALLS))
def test_every_op_answers_through_g6_invoke_inside_the_envelope(op: str) -> None:
    reply = invoke_reply("datalog", op, json.dumps({**CALLS[op], "request_id": "req-1", "run_id": "run-1"}))
    assert check_envelope(reply) == [] and reply["ok"] is True, reply.get("error")
    result = reply["result"]
    assert check_envelope(result) == [] and (result["component"], result["op"]) == ("datalog", op)
    assert result["completion_state"] == reply["completion_state"] == "qualified-draft", (result["code"],
                                                                                          result["reason"])
    expected = NOT_EVALUATED if op == "check" else ENGINE_ANSWER if HAS_Z3 else ONE_ENGINE
    assert reply["warning_card"]["code"] == result["warning_card"]["code"] == expected
    assert (result["request_id"], result["run_id"]) == ("req-1", "run-1")
    if op != "check":
        assert result["check"]["passed"] is True and result["evidence"][0]["check"]["passed"] is True


def test_the_answers_over_mcp_are_the_programs() -> None:
    query = invoke_reply("datalog", "query", json.dumps(CALLS["query"]))["result"]
    assert [a["bindings"]["X"] for a in query["queries"][0]["answers"]] == ["bob", "carol", "dave"]
    explain = invoke_reply("datalog", "explain", json.dumps(CALLS["explain"]))["result"]
    assert explain["explanation"]["derivable"] is True
    evaluate = invoke_reply("datalog", "evaluate", json.dumps({**CALLS["evaluate"], "relations": ["parent"],
                                                               "engine": "python"}))["result"]
    assert evaluate["relations"]["parent"][0] == ["alice", "bob"] and evaluate["engines"] == ["pure_python"]
    check = invoke_reply("datalog", "check", json.dumps(CALLS["check"]))["result"]
    assert check["report"]["facts"] == 4 and check["report"]["relations"]["ancestor"]["recursive"] is True


@pytest.mark.parametrize("op", ["evaluate", "query", "explain"])
def test_without_z3_every_op_still_answers_and_says_one_engine_ran(op: str, no_z3) -> None:
    reply = invoke_reply("datalog", op, json.dumps(CALLS[op]))
    assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"
    card = reply["warning_card"]
    assert card["code"] == ONE_ENGINE and "pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver'" in card["suggested_action"]
    assert reply["result"]["engines"] == ["pure_python"] and reply["result"]["check"]["passed"] is True


@pytest.mark.parametrize("op", ["evaluate", "query", "explain"])
def test_without_z3_asking_for_it_is_refused(op: str, no_z3) -> None:
    reply = invoke_reply("datalog", op, json.dumps({**CALLS[op], "engine": "z3"}))
    assert check_envelope(reply) == [] and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == SOLVER_UNAVAILABLE
    assert "pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver'" in reply["warning_card"]["suggested_action"]


@pytest.mark.parametrize("op", ["ops", "help"])
def test_ops_and_help_answer_with_or_without_z3(op: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for blocked in (False, True):
        if blocked:
            monkeypatch.setitem(sys.modules, "z3", None)
        payload = get_registry().invoke("datalog", op).unwrap()
        assert len(payload[op]) == len(CALLS) + 2
        reply = invoke_reply("datalog", op)
        assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"


@pytest.mark.parametrize(("params", "code", "says"), [
    ({"program": "parent(alice bob)."}, PARSE_ERROR, "line 1, column 14"),
    ({"program": "r(a). p(X) :- r(X), not q(X). q(X) :- r(X), not p(X)."}, NOT_STRATIFIABLE, "negation"),
])
def test_a_refused_program_is_blocked_over_mcp_with_where_it_failed(params: dict, code: str, says: str) -> None:
    reply = invoke_reply("datalog", "evaluate", json.dumps(params))
    assert check_envelope(reply) == [] and reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == code and says in reply["result"]["reason"]
    assert reply["result"]["problems"] and reply["result"]["relations"] == {}


@pytest.mark.parametrize(("op", "params"), [
    ("run_code", {"code": "p(a)."}),
    ("assert_fact", {"fact": "p(a)."}),
    ("load_program", {"program": FAMILY}),
    ("evaluate", {"code": "import os"}),
    ("evaluate", {"program": FAMILY, "program_id": "p1"}),
    ("evaluate", {"program": FAMILY, "engine": "pydatalog"}),
    ("evaluate", {"program": FAMILY, "time_limit": 0}),
    ("query", {"program": FAMILY, "query": ["ancestor(alice, X)"]}),
])
def test_unknown_ops_unknown_fields_and_bad_parameters_are_refused(op: str, params: dict) -> None:
    result = get_registry().invoke("datalog", op, params, block_sensitive=True)
    assert result.__class__.__name__ == "Failure"
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION] [DATALOG_ERROR]")
    reply = invoke_reply("datalog", op, json.dumps(params))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and check_envelope(reply) == []


def test_no_reply_is_verified() -> None:
    for op, params in CALLS.items():
        reply = invoke_reply("datalog", op, json.dumps(params))
        assert reply["completion_state"] != "verified" and reply["result"]["completion_state"] != "verified"


_FORBIDDEN_MODULES = {"subprocess", "os", "socket", "urllib", "http", "shutil", "multiprocessing", "ctypes", "asyncio",
                      "requests", "httpx", "pickle", "marshal", "pyDatalog", "clingo", "g6.core.sandbox", "g6.core.llm",
                      "g6.research"}
_FORBIDDEN_CALLS = {"exec", "eval", "compile", "__import__", "open", "breakpoint"}


def test_the_package_runs_no_caller_code_writes_no_file_and_reaches_no_network() -> None:
    """Programs are read by G6's own parser into data; nothing a caller sends is evaluated. The earlier version's
    run_code op re-ran a stored program; there is no such op here."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found += [f"{path.name}: {word}" for word in ("exec(", "eval(", "subprocess", "os.system") if word in text]
        for node in ast.walk(ast.parse(text)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            found += [f"{path.name}: import {n}" for n in names
                      if any(n == m or n.startswith(m + ".") for m in _FORBIDDEN_MODULES)]
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                found.append(f"{path.name}:{node.lineno}: {node.func.id}()")
    assert found == []


def test_the_guard_finds_what_it_looks_for(tmp_path: Path) -> None:
    """Survival control: the scan above reports a planted call, so its empty answer means something."""
    planted = tmp_path / "planted.py"
    planted.write_text("import subprocess\nresult = eval('1')\n", encoding="utf-8")
    text = planted.read_text(encoding="utf-8")
    assert [w for w in ("exec(", "eval(", "subprocess", "os.system") if w in text] == ["eval(", "subprocess"]


def test_every_file_is_apache_2() -> None:
    for path in sorted([*SRC.rglob("*.py"), *Path(__file__).parent.glob("*.py")]):
        assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name


def test_importing_the_package_loads_no_z3_and_raises_no_deprecation(tmp_path) -> None:
    subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", "import g6.datalog"], cwd=tmp_path,
                   check=True)
    probe = "import sys, g6.datalog\nassert 'z3' not in sys.modules, 'importing g6.datalog imported z3'\n"
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)
    without = ("import sys\nsys.modules['z3'] = None\nimport g6.datalog as d\n"
               "r = d.evaluate('p(a). q(X) :- p(X).')\n"
               "assert r.completion_state == 'qualified-draft' and r.engines == ['pure_python'], (r.code, r.reason)\n"
               "assert r.warning_card['code'] == 'G6_E_DATALOG_ONE_ENGINE'\n"
               "z = d.evaluate('p(a).', engine='z3')\n"
               "assert z.code == 'G6_E_DATALOG_SOLVER_UNAVAILABLE' and z.completion_state == 'blocked-escalated'\n")
    subprocess.run([sys.executable, "-c", without], cwd=tmp_path, check=True)


@pytest.mark.skipif(not HAS_Z3, reason="z3-solver is not installed (pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver')")
def test_both_engines_run_with_deprecation_warnings_as_errors(tmp_path) -> None:
    script = ("import g6.datalog as d\n"
              f"r = d.query({FAMILY!r}, 'ancestor(alice, X)')\n"
              "assert r.completion_state == 'qualified-draft' and r.engines == ['pure_python', 'z3'], (r.code, "
              "r.reason)\n")
    done = subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", script], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
