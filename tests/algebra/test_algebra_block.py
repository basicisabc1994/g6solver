# SPDX-License-Identifier: Apache-2.0
"""g6.algebra's block (owner, 2026-10-20): registered, every SymPy op reachable over MCP (``g6_invoke``) inside the
library envelope and never ``verified``; the SageMath ops sensitive (they run a program on your machine), so they run
from Python, or over MCP only when the person running the server lists them in G6_MCP_SENSITIVE; unknown ops and
fields refused; SymPy's string readers never called, no caller code run in process, programs started only from the
runner, and importing the package loads no SymPy and starts nothing."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.algebra
from g6.algebra import ops
from g6.algebra.algebra_block import SENSITIVE_OPS, AlgebraBlock
from g6.algebra.envelope import CAS_UNAVAILABLE, INSTALL_CAS, RECHECKED, SAGE_ENV, SAGE_MISSING
from g6.algebra.schema import AlgebraResult
from g6.core import discovery
from g6.core.maturity import maturity_reason
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

SRC = Path(g6.algebra.__file__).parent
needs_sympy = pytest.mark.skipif(not ops.sympy_installed(), reason="SymPy is not installed (pip install 'g6[cas] @ git+https://github.com/basicisabc1994/g6solver')")
SENSITIVE_WORDS = "sensitive: runs from Python, or over MCP only when the person running the server lists it in " \
                  "G6_MCP_SENSITIVE"
NOTHING_ALLOWED: frozenset[str] = frozenset()
CALLS: dict[str, tuple[dict, str]] = {  # each SymPy op, and its answer
    "simplify": ({"expression": "sin(x)^2 + cos(x)^2", "symbols": ["x"]}, "1"),
    "expand": ({"expression": "(x + 1)^2", "symbols": ["x"]}, "x**2 + 2*x + 1"),
    "factor": ({"expression": "x^2 - 5*x + 6", "symbols": {"x": ["real"]}}, ""),
    "differentiate": ({"expression": "sin(x)^2", "variable": "x"}, "2*sin(x)*cos(x)"),
    "integrate": ({"expression": "x*E^x", "variable": "x"}, "(x - 1)*exp(x)"),
    "solve": ({"equations": ["x^2 - 5*x + 6 = 0"], "unknowns": ["x"]}, "{2, 3}"),
    "limit": ({"expression": "sin(x)/x", "variable": "x", "point": 0}, "1"),
    "series": ({"expression": "exp(x)", "variable": "x", "order": 3}, "1 + x + x**2/2 + O(x**3)"),
    "determinant": ({"matrix": [[1, 2], [3, 4]]}, "-2"),
    "inverse": ({"matrix": [[1, 2], [3, 4]]}, "Matrix([[-2, 1], [3/2, -1/2]])"),
    "eigenvalues": ({"matrix": [[2, 1], [1, 2]]}, "{1: 1, 3: 1}"),
    "factor_integer": ({"n": 360}, "2^3 * 3^2 * 5"),
    "is_prime": ({"n": 97}, "True"),
    "gcd": ({"numbers": [12, 18, 30]}, "6"),
}
SAGE_CALLS = {"sage_evaluate": {"expression": "factor(x^2 - 1)", "symbols": ["x"]}, "sage_version": {}}


@pytest.fixture
def no_sage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SAGE_ENV, str(tmp_path / "no-sage-here"))


def test_algebra_is_registered_with_a_block_and_its_sage_ops_sensitive() -> None:
    assert get_registry().get_class("algebra") is AlgebraBlock
    info = discovery.describe("algebra")
    assert info["block"] == "AlgebraBlock" and info["maturity"] == "beta"
    assert info["sensitive_ops"] == ["sage_evaluate", "sage_version"] == sorted(g6.algebra.__g6_sensitive_ops__)
    assert sorted(SENSITIVE_OPS) == info["sensitive_ops"]
    assert info["requires"] == [] and info["ops"] == [*CALLS, *SAGE_CALLS]
    assert info["summary"].endswith(".") and len(info["summary"]) <= 170
    words = ((g6.algebra.__doc__ or "") + maturity_reason(AlgebraBlock)).lower()
    assert "experimental" not in words and "winget" not in words and "poetry" not in words and "docker" not in words
    assert "gpl" in words and "never installs" in words and "python only" not in words
    assert not hasattr(g6.algebra, "__g6_family__")


def test_g6_find_ranks_algebra_first_for_algebra_tasks() -> None:
    for query in ("differentiate and integrate a formula with SymPy", "factor a polynomial and solve an equation",
                  "the eigenvalues and inverse of a matrix", "computer algebra: the limit and series of a function"):
        assert discovery.find(query)[0]["component"] == "algebra", (query, discovery.find(query)[:3])


@pytest.mark.parametrize("op", ["ops", "help"])
def test_ops_and_help_answer_over_mcp_inside_the_envelope(op: str) -> None:
    reply = invoke_reply("algebra", op, json.dumps({"request_id": "req-1", "run_id": "run-1"}))
    assert check_envelope(reply) == [] and reply["ok"] is True and reply["completion_state"] == "qualified-draft"
    payload = reply["result"]
    assert (payload["component"], payload["op"]) == ("algebra", op) and len(payload[op]) == len(CALLS) + 2


def test_help_marks_the_sage_ops_sensitive_and_says_how_to_get_each_engine() -> None:
    lines = get_registry().invoke("algebra", "help").unwrap()["help"]
    marked = [line for line in lines if SENSITIVE_WORDS in line]
    assert [line.split("(")[0] for line in marked] == list(SAGE_CALLS)
    text = " ".join(lines)
    assert INSTALL_CAS in text and "GPL" in text and SAGE_ENV in text and "never installs" in text
    assert "re-check" in text and "Python only" not in text
    assert get_registry().invoke("algebra", "ops").unwrap()["sensitive"] == list(SAGE_CALLS)


@needs_sympy
@pytest.mark.parametrize("op", list(CALLS))
def test_every_sympy_op_answers_over_mcp_rechecked_inside_the_envelope(op: str) -> None:
    params, answer = CALLS[op]
    reply = invoke_reply("algebra", op, json.dumps({**params, "request_id": "req-1", "run_id": "run-1"}))
    assert check_envelope(reply) == [] and reply["ok"] is True, reply.get("error")
    result = reply["result"]
    assert check_envelope(result) == [] and (result["component"], result["op"]) == ("algebra", op)
    assert result["completion_state"] == reply["completion_state"] == "qualified-draft", (result["code"],
                                                                                          result["recheck"])
    assert reply["warning_card"]["code"] == result["warning_card"]["code"] == RECHECKED
    assert result["recheck"]["status"] == "passed" and result["evidence"][0]["recheck"]["status"] == "passed"
    assert (result["request_id"], result["run_id"]) == ("req-1", "run-1")
    if answer:
        assert result["answer"] == answer
    assert result["evidence"][0]["engine"] == "SymPy" and result["evidence"][0]["external_program_ran"] is False


@pytest.mark.parametrize("op", ["simplify", "solve", "factor_integer"])
def test_without_sympy_the_mcp_reply_says_what_to_install(op: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sympy", None)
    reply = invoke_reply("algebra", op, json.dumps(CALLS[op][0]))
    assert check_envelope(reply) == [] and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == CAS_UNAVAILABLE and INSTALL_CAS in reply["warning_card"]["suggested_action"]


@pytest.mark.parametrize("op", ["ops", "help"])
def test_ops_and_help_answer_without_sympy(op: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sympy", None)
    assert len(get_registry().invoke("algebra", op).unwrap()[op]) == len(CALLS) + 2


@pytest.mark.parametrize("op", list(SAGE_CALLS))
def test_the_sage_ops_are_refused_over_mcp_unless_the_server_allows_them(op: str) -> None:
    reply = invoke_reply("algebra", op, json.dumps(SAGE_CALLS[op]), allow_sensitive=NOTHING_ALLOWED)
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated" and check_envelope(reply) == []
    assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
    refused = get_registry().invoke("algebra", op, SAGE_CALLS[op], block_sensitive=True)
    assert str(refused.failure()).startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")


@pytest.mark.parametrize("op", list(SAGE_CALLS))
def test_a_sage_op_the_server_allows_runs_over_mcp_and_keeps_its_own_state(op: str, no_sage) -> None:
    reply = invoke_reply("algebra", op, json.dumps(SAGE_CALLS[op]), allow_sensitive=frozenset({f"algebra.{op}"}))
    assert check_envelope(reply) == [] and reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == SAGE_MISSING


@pytest.mark.parametrize("op", list(SAGE_CALLS))
def test_the_sage_ops_answer_from_python(op: str, no_sage) -> None:
    payload = get_registry().invoke("algebra", op, SAGE_CALLS[op]).unwrap()
    assert check_envelope(payload) == [] and payload["code"] == SAGE_MISSING and payload["op"] == op
    assert payload["completion_state"] == "blocked-escalated"


def test_the_sympy_ops_are_not_sensitive() -> None:
    assert not set(CALLS) & set(g6.algebra.__g6_sensitive_ops__)


@pytest.mark.parametrize(("op", "params"), [
    ("run_code", {"code": "import os"}),
    ("python", {"source": "1"}),
    ("exec", {}),
    ("sympify", {"expression": "x"}),
    ("simplify", {"expression": "x", "code": "import os"}),
    ("simplify", {"expression": "x", "python": "print(1)"}),
    ("simplify", {"expression": "x", "run_code": "x"}),
    ("simplify", {"expression": ["x"]}),
    ("simplify", {"expression": "x", "time_limit": -1}),
    ("simplify", {"expression": "x", "memory_mb": 99999}),
    ("solve", {"equations": ["x = 1"], "domain": "quaternions"}),
    ("limit", {"expression": "x", "variable": "x", "direction": "up"}),
    ("sage_evaluate", {"expression": "x", "sage_args": ["-c", "print(1)"]}),
    ("sage_version", {"executable": "/usr/local/bin/sage"}),
])
def test_unknown_ops_unknown_fields_and_bad_parameters_are_refused(op: str, params: dict) -> None:
    result = get_registry().invoke("algebra", op, params)
    assert result.__class__.__name__ == "Failure"
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION] [ALGEBRA_ERROR]")
    reply = invoke_reply("algebra", op, json.dumps(params), allow_sensitive=NOTHING_ALLOWED)
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated" and check_envelope(reply) == []
    assert reply["warning_card"]["code"] in ("G6_E_INPUT_VALIDATION", "G6_E_SENSITIVE_COMPONENT_DENIED")


@pytest.mark.parametrize(("op", "params", "field"), [
    ("differentiate", {"expression": "x", "variable": "x", "order": 0}, "order"),
    ("series", {"expression": "x", "variable": "x", "order": 13}, "order"),
    ("factor_integer", {"n": 1}, "factor_integer"),
    ("gcd", {"numbers": [4]}, "numbers"),
    ("inverse", {"matrix": [[1, 2]]}, "square"),
    ("solve", {"equations": ["x + y = 1"], "symbols": ["x", "y"]}, "unknowns"),
    ("solve", {"equations": ["x + y = 1"]}, "symbols"),
])
def test_out_of_range_parameters_are_blocked_inside_the_envelope(op: str, params: dict, field: str) -> None:
    reply = invoke_reply("algebra", op, json.dumps(params))
    assert check_envelope(reply) == [] and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"].startswith("G6_E_ALGEBRA_") and field in reply["result"]["reason"]
    assert reply["result"]["evidence"][0]["engine_ran"] is False


def test_no_result_can_be_verified() -> None:
    with pytest.raises(ValidationError):
        AlgebraResult(operation="simplify", completion_state="verified")


# --- the guard -----------------------------------------------------------------------------------------------------

_RUNNER = "runner.py"
_NETWORK = {"socket", "urllib", "http", "requests", "httpx", "ftplib", "asyncio"}
_NEVER = {"g6.core.sandbox", "g6.core.llm", "g6.research", "multiprocessing", "pickle", "marshal", "ctypes",
          "sympy.parsing", "sympy.utilities.lambdify"}
_FORBIDDEN_BUILTINS = {"exec", "eval", "compile", "__import__", "breakpoint"}       # called by name
_FORBIDDEN_CALLS = {"sympify", "parse_expr", "lambdify", "system", "popen", "Popen", "S"}  # by name or attribute
_FORBIDDEN_TEXT = ("exec(", "eval(", "os.system", "sympify", "parse_expr", "lambdify", "shell=True")


def _scan(paths: list[Path], runner: str) -> list[str]:
    found = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        found += [f"{path.name}: {word}" for word in _FORBIDDEN_TEXT if word in text]
        if path.name != runner and "subprocess" in text:
            found.append(f"{path.name}: subprocess outside the runner")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            found += [f"{path.name}: import {n}" for n in names
                      if any(n == m or n.startswith(m + ".") for m in _NETWORK | _NEVER)]
            if isinstance(node, ast.Call):
                by_name = node.func.id if isinstance(node.func, ast.Name) else ""
                called = by_name or (node.func.attr if isinstance(node.func, ast.Attribute) else "")
                if called in _FORBIDDEN_CALLS or by_name in _FORBIDDEN_BUILTINS:
                    found.append(f"{path.name}:{node.lineno}: {called}()")
    return found


def test_sympys_string_readers_are_never_called_and_programs_start_only_from_the_runner() -> None:
    """Caller text reaches nothing but G6's parser: no sympify, parse_expr, S() on a string, lambdify, eval or exec
    anywhere in the package, and only the runner starts a program (G6's own worker or SageMath), through
    g6.core.component_process, never with a shell."""
    assert _scan(sorted(SRC.rglob("*.py")), _RUNNER) == []
    runner = (SRC / _RUNNER).read_text(encoding="utf-8")
    assert "component_process" in runner and "Popen(" not in runner
    symbols = [p.name for p in sorted(SRC.rglob("*.py")) if "Symbol(" in p.read_text(encoding="utf-8")]
    assert set(symbols) <= {"build.py", "engine.py", "checks.py"}, symbols


def test_the_guard_finds_what_it_looks_for(tmp_path: Path) -> None:
    """Survival control: the scan above reports each planted breach, so its empty answer means something."""
    planted = tmp_path / "planted.py"
    planted.write_text("import subprocess\nimport sympy\nfrom sympy.parsing.sympy_parser import parse_expr\n"
                       "a = sympy.sympify('x')\nb = sympy.S('x')\nc = eval('1')\nimport os\nos.system('x')\n"
                       "f = sympy.lambdify(a, a)\ng = compile('1', 'f', 'eval')\n", encoding="utf-8")
    found = " ".join(_scan([planted], _RUNNER))
    for word in ("sympify", "parse_expr", "S()", "eval(", "os.system", "lambdify", "outside the runner",
                 "sympy.parsing", "compile()", "system()"):
        assert word in found, word


def test_every_file_is_apache_2() -> None:
    for path in sorted([*SRC.rglob("*.py"), *Path(__file__).parent.glob("*.py")]):
        assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name


def test_no_committed_file_names_a_users_path() -> None:
    user_path = re.compile(r"[a-z]:[\\/]+users[\\/]+|/home/\w+", re.IGNORECASE)
    for path in sorted([*SRC.rglob("*.py"), *Path(__file__).parent.glob("*.py")]):
        assert not user_path.search(path.read_text(encoding="utf-8")), path.name


def test_importing_the_package_loads_no_sympy_starts_no_program_and_raises_no_deprecation(tmp_path: Path) -> None:
    subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", "import g6.algebra"], cwd=tmp_path,
                   check=True)
    probe = ("import subprocess, sys\n"
             "def refuse(*a, **k):\n    raise AssertionError('importing g6.algebra started a program')\n"
             "subprocess.Popen = refuse\n"
             "import g6.algebra, g6.algebra.algebra_block, g6.algebra.ops, g6.algebra.sage, g6.algebra.runner\n"
             "loaded = [m for m in sys.modules if m.split('.')[0] in ('sympy', 'mpmath')]\n"
             "assert not loaded, f'importing g6.algebra loaded {loaded[:3]}'\n")
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)
