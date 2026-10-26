# SPDX-License-Identifier: Apache-2.0
"""g6.model_checking's block (owner, 2026-10-20): the symbolic ops reachable through the registry and ``g6_invoke``,
each reply inside the library envelope and never ``verified``; the TLC and SANY ops callable from Python and refused
over MCP by the registry's rule for sensitive ops; unknown ops and unknown fields refused; no caller code run in
process and no program started outside the TLC runner; and without Z3 the package still imports and every symbolic op
is an honest refusal."""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.model_checking
from g6.core import discovery
from g6.core.maturity import maturity_reason
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import invoke_reply
from g6.model_checking.envelope import (
    BOUNDED,
    CERTIFICATE_REFUTED,
    JAR_MISSING,
    K_INDUCTION,
    NOT_CHECKED,
    SANY_ERRORS,
    SOLVER_UNAVAILABLE,
    TLC_UNRECOGNISED,
    TRACE_FOUND,
    VIOLATION,
    finish,
)
from g6.model_checking.model_checking_block import ModelCheckingBlock
from g6.model_checking.schema import ModelCheckResult
from g6.model_checking.tlc import DIGEST_ENV, JAR_ENV

HAS_Z3 = importlib.util.find_spec("z3") is not None
needs_z3 = pytest.mark.skipif(not HAS_Z3, reason="z3-solver is not installed (pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver')")
SRC = Path(g6.model_checking.__file__).parent
COUNTER = {"variables": {"x": "Int"}, "init": "(= x 0)", "transition": "(= next_x (+ x 1))",
           "invariants": {"small": "(<= x 3)"}}
SPEC = "---- MODULE Counter ----\nEXTENDS Naturals\nVARIABLE x\nInit == x = 0\nNext == x' = x + 1\n" \
       "Spec == Init /\\ [][Next]_x\nInv == x < 3\n====\n"
CALLS: dict[str, tuple[dict, str, str]] = {  # params, completion state, warning card
    "check_invariant": ({"model": COUNTER, "depth": 10}, "blocked-escalated", VIOLATION),
    "prove_invariant": ({"model": {**COUNTER, "invariants": {"natural": "(>= x 0)"}}}, "qualified-draft", K_INDUCTION),
    "check_deadlock": ({"model": COUNTER, "depth": 5}, "qualified-draft", BOUNDED),
    "reachable": ({"model": COUNTER, "goal": "(= x 2)"}, "qualified-draft", TRACE_FOUND),
}
SENSITIVE = {"tlc_check": {"spec": SPEC, "invariants": ["Inv"]}, "sany_parse": {"spec": SPEC}}


@pytest.fixture
def no_z3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "z3", None)  # ``import z3`` now raises ImportError


def test_model_checking_is_registered_with_a_block_and_the_tla_ops_sensitive() -> None:
    assert get_registry().get_class("model_checking") is ModelCheckingBlock
    info = discovery.describe("model_checking")
    assert info["block"] == "ModelCheckingBlock" and info["maturity"] == "beta"
    assert info["sensitive_ops"] == ["sany_parse", "tlc_check"] == sorted(g6.model_checking.__g6_sensitive_ops__)
    assert info["requires"] == ["formal"] and info["ops"] == [*CALLS, *SENSITIVE]
    assert info["summary"].endswith(".") and len(info["summary"]) <= 170
    words = ((g6.model_checking.__doc__ or "") + maturity_reason(ModelCheckingBlock)).lower()
    assert "experimental" not in words and "poetry" not in words and "winget" not in words
    assert "never downloads" in words and "sha-256" in words
    assert not hasattr(g6.model_checking, "__g6_family__")


def test_g6_find_ranks_model_checking_first_for_a_model_checking_task() -> None:
    assert discovery.find("model check a state machine invariant with k-induction")[0]["component"] == "model_checking"
    assert discovery.find("check a TLA+ spec with TLC")[0]["component"] == "model_checking"


@needs_z3
@pytest.mark.parametrize("op", list(CALLS))
def test_every_symbolic_op_answers_through_g6_invoke_inside_the_envelope(op: str) -> None:
    params, state, card = CALLS[op]
    reply = invoke_reply("model_checking", op, json.dumps({**params, "request_id": "req-1", "run_id": "run-1"}))
    assert check_envelope(reply) == [] and reply["ok"] is True, reply.get("error")
    result = reply["result"]
    assert check_envelope(result) == [] and (result["component"], result["op"]) == ("model_checking", op)
    assert result["completion_state"] == reply["completion_state"] == state, (result["code"], result["reason"])
    assert reply["warning_card"]["code"] == result["warning_card"]["code"] == card
    assert (result["request_id"], result["run_id"]) == ("req-1", "run-1")
    assert result["recheck"]["passed"] is True and result["evidence"][0]["recheck"]["passed"] is True


@pytest.mark.parametrize("op", list(SENSITIVE))
def test_the_tla_ops_are_refused_over_mcp_by_the_registrys_rule(op: str) -> None:
    reply = invoke_reply("model_checking", op, json.dumps(SENSITIVE[op]))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated" and check_envelope(reply) == []
    assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"


@pytest.mark.parametrize("op", list(SENSITIVE))
def test_the_tla_ops_answer_from_python(op: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(JAR_ENV, raising=False)
    monkeypatch.delenv(DIGEST_ENV, raising=False)
    payload = get_registry().invoke("model_checking", op, SENSITIVE[op]).unwrap()
    assert check_envelope(payload) == [] and payload["code"] == JAR_MISSING and payload["op"] == op


@pytest.mark.parametrize("op", ["ops", "help"])
def test_ops_and_help_answer_with_or_without_z3(op: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for blocked in (False, True):
        if blocked:
            monkeypatch.setitem(sys.modules, "z3", None)
        payload = get_registry().invoke("model_checking", op).unwrap()
        assert len(payload[op]) == len(CALLS) + len(SENSITIVE)
        reply = invoke_reply("model_checking", op)
        assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"
    help_lines = get_registry().invoke("model_checking", "help").unwrap()["help"]
    marked = [line for line in help_lines if "G6_MCP_SENSITIVE" in line]
    assert [line.split("(")[0] for line in marked] == list(SENSITIVE)
    assert not any("Python only" in line for line in help_lines)
    assert any("next_x" in line for line in help_lines) and any("G6_TLA2TOOLS_SHA256" in line for line in help_lines)


@pytest.mark.parametrize("op", list(CALLS))
def test_without_z3_every_symbolic_op_is_blocked_and_says_what_to_install(op: str, no_z3) -> None:
    reply = invoke_reply("model_checking", op, json.dumps(CALLS[op][0]))
    assert check_envelope(reply) == [] and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == SOLVER_UNAVAILABLE
    assert "pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver'" in reply["warning_card"]["suggested_action"]
    assert reply["result"]["trace"] == [] and reply["result"]["engine"] == ""


@pytest.mark.parametrize(("op", "params"), [
    ("run_code", {"code": "import os"}),
    ("download", {"url": "https://example.org/tla2tools.jar"}),
    ("simulate", {"spec": SPEC}),
    ("check_invariant", {"model": COUNTER, "code": "print(1)"}),
    ("check_invariant", {"model": COUNTER, "download": True}),
    ("check_invariant", {"model": {**COUNTER, "solver": "nusmv"}}),
    ("check_invariant", {"model": COUNTER, "depth": 0}),
    ("check_invariant", {"model": COUNTER, "time_limit": -1}),
    ("reachable", {"model": COUNTER, "goal": ["(= x 1)"]}),
    ("tlc_check", {"spec": SPEC, "download": "https://example.org/tla2tools.jar"}),
    ("tlc_check", {"spec": SPEC, "jar": "C:/somewhere/tla2tools.jar"}),
    ("tlc_check", {"spec": SPEC, "java_args": ["-javaagent:x.jar"]}),
])
def test_unknown_ops_unknown_fields_and_bad_parameters_are_refused(op: str, params: dict) -> None:
    result = get_registry().invoke("model_checking", op, params)
    assert result.__class__.__name__ == "Failure"
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION] [MODEL_CHECKING_ERROR]")
    if op not in SENSITIVE:
        reply = invoke_reply("model_checking", op, json.dumps(params))
        assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
        assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and check_envelope(reply) == []


@pytest.mark.parametrize(("fields", "code"), [
    ({"engine": "z3", "verdict": "no_violation_within_bound", "depth": 3}, NOT_CHECKED),
    ({"engine": "z3", "verdict": "k_inductive", "k": 2, "recheck": {"passed": False, "failures": ["step"]}},
     CERTIFICATE_REFUTED),
    ({"engine": "z3", "verdict": "proved", "k": 2, "recheck": {"passed": True, "checks": [{}], "failures": []}},
     NOT_CHECKED),
    ({"engine": "z3", "verdict": "verified", "recheck": {"passed": True, "checks": [{}], "failures": []}}, NOT_CHECKED),
    ({"engine": "tlc", "verdict": "looks fine"}, TLC_UNRECOGNISED),
    ({"engine": "sany", "verdict": ""}, SANY_ERRORS),
    ({"verdict": "no_error"}, NOT_CHECKED),
])
def test_finish_blocks_an_answer_without_its_check(fields: dict, code: str) -> None:
    """Survival control for the label: an answer built without G6's re-check, with a failed one, or with a verdict
    no engine gives is blocked, whoever built it."""
    result = finish("check_invariant", **fields)
    assert result.code == code and result.completion_state == "blocked-escalated"
    assert check_envelope(result.model_dump(mode="json")) == []


def test_no_result_can_be_verified() -> None:
    with pytest.raises(ValidationError):
        ModelCheckResult(operation="check_invariant", completion_state="verified")
    for op, (params, _, _) in CALLS.items():
        reply = invoke_reply("model_checking", op, json.dumps(params))
        assert reply["completion_state"] != "verified" and reply["result"]["completion_state"] != "verified"


_RUNNER = "tlc.py"
_NETWORK = {"socket", "urllib", "http", "requests", "httpx", "ftplib", "asyncio"}
_NEVER = {"g6.core.sandbox", "g6.core.llm", "g6.research", "multiprocessing", "pickle", "marshal", "ctypes"}
_FORBIDDEN_CALLS = {"exec", "eval", "compile", "__import__", "breakpoint"}


def _imports(tree: ast.AST) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def _scan(paths: list[Path], runner: str) -> list[str]:
    found = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        found += [f"{path.name}: {word}" for word in ("exec(", "eval(", "os.system") if word in text]
        if path.name != runner and "subprocess" in text:
            found.append(f"{path.name}: subprocess outside the TLC runner")
        tree = ast.parse(text)
        found += [f"{path.name}: import {n}" for n in _imports(tree)
                  if any(n == m or n.startswith(m + ".") for m in _NETWORK | _NEVER)]
        found += [f"{path.name}:{node.lineno}: {node.func.id}()" for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS]
    return found


def test_no_caller_code_runs_in_process_and_programs_start_only_from_the_tlc_runner() -> None:
    """Models are SMT-LIB terms read by g6.formal's closed grammar, and specs are files TLC reads: nothing a caller sends
    is evaluated here. Only the TLC runner starts a program, through g6.core.component_process, and nothing reaches the
    network, so the jar is never downloaded (the earlier version fetched it unpinned on first use)."""
    assert _scan(sorted(SRC.rglob("*.py")), _RUNNER) == []
    runner = (SRC / _RUNNER).read_text(encoding="utf-8")
    assert "component_process" in runner and "shell=True" not in runner and "Popen(" not in runner


def test_the_guard_finds_what_it_looks_for(tmp_path: Path) -> None:
    """Survival control: the scan above reports each planted breach, so its empty answer means something."""
    planted = tmp_path / "planted.py"
    planted.write_text("import subprocess\nimport urllib.request\nresult = eval('1')\nimport os\nos.system('x')\n",
                       encoding="utf-8")
    found = _scan([planted], _RUNNER)
    assert any("eval(" in f for f in found) and any("os.system" in f for f in found)
    assert any("outside the TLC runner" in f for f in found) and any("urllib.request" in f for f in found)


def test_every_file_is_apache_2() -> None:
    for path in sorted([*SRC.rglob("*.py"), *Path(__file__).parent.glob("*.py")]):
        assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name


def test_no_committed_file_names_a_users_path() -> None:
    """The release scan flags user paths; the jar on a developer's machine is named only through the environment."""
    user_path = re.compile(r"[a-z]:[\\/]+users[\\/]+|\.g6[\\/]+tla", re.IGNORECASE)
    for path in sorted([*SRC.rglob("*.py"), *Path(__file__).parent.glob("*.py")]):
        assert not user_path.search(path.read_text(encoding="utf-8")), path.name


def test_importing_the_package_loads_no_z3_starts_no_program_and_raises_no_deprecation(tmp_path) -> None:
    subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", "import g6.model_checking"],
                   cwd=tmp_path, check=True)
    probe = ("import subprocess, sys\n"
             "def refuse(*a, **k):\n    raise AssertionError('importing g6.model_checking started a program')\n"
             "subprocess.Popen = refuse\n"
             "import g6.model_checking\n"
             "assert 'z3' not in sys.modules, 'importing g6.model_checking imported z3'\n")
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)
    without = ("import sys\nsys.modules['z3'] = None\nimport g6.model_checking as m\n"
               "r = m.check_invariant({'variables': {'x': 'Int'}, 'init': '(= x 0)', 'transition': '(= next_x x)', "
               "'invariants': {'zero': '(= x 0)'}})\n"
               "assert r.code == 'G6_E_MODEL_CHECK_SOLVER_UNAVAILABLE' and r.completion_state == 'blocked-escalated'\n")
    subprocess.run([sys.executable, "-c", without], cwd=tmp_path, check=True)


@needs_z3
def test_the_symbolic_ops_run_with_deprecation_warnings_as_errors(tmp_path) -> None:
    script = ("import g6.model_checking as m\n"
              f"r = m.check_invariant({COUNTER!r}, depth=6)\n"
              "assert r.verdict == 'violated' and r.recheck['passed'], (r.code, r.reason)\n"
              f"p = m.prove_invariant({{**{COUNTER!r}, 'invariants': {{'natural': '(>= x 0)'}}}})\n"
              "assert p.verdict == 'k_inductive', (p.code, p.reason)\n")
    done = subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", script], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
