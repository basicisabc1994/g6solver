# SPDX-License-Identifier: Apache-2.0
"""g6.lean's block (owner, 2026-10-20): registered with ``ops`` and ``help`` reachable over MCP inside the library
envelope; ``check_proof`` and ``lean_version`` run Lean, a program that runs the caller's source, so they are callable
from Python and refused over MCP by the registry's rule for sensitive ops unless the person running the server lists
them in ``G6_MCP_SENSITIVE``; unknown ops and unknown fields refused; no
caller code run in process, no program started outside the runner, and importing the package starts nothing."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import g6.lean
from g6.core import discovery
from g6.core.maturity import maturity_reason
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.lean.envelope import LEAN_ENV, LEAN_MISSING
from g6.lean.lean_block import LeanBlock
from g6.mcp import invoke_reply

SRC = Path(g6.lean.__file__).parent
TWO = "theorem two_add : 2 + 2 = 4 := rfl\n"
SENSITIVE = {"check_proof": {"source": TWO, "theorem": "two_add"}, "lean_version": {},
             "prove": {"statement": "2 + 2 = 4"}}
OPS = [*SENSITIVE, "sketch"]  # sketch calls only the block's injected model, never Lean


@pytest.fixture
def no_lean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LEAN_ENV, str(tmp_path / "no-lean-here"))


def test_lean_is_registered_with_a_block_and_its_lean_ops_sensitive() -> None:
    assert get_registry().get_class("lean") is LeanBlock
    info = discovery.describe("lean")
    assert info["block"] == "LeanBlock" and info["maturity"] == "beta"
    assert info["sensitive_ops"] == ["check_proof", "lean_version", "prove"] == sorted(g6.lean.__g6_sensitive_ops__)
    assert info["requires"] == [] and info["ops"] == OPS
    assert info["summary"].endswith(".") and len(info["summary"]) <= 170
    words = ((g6.lean.__doc__ or "") + maturity_reason(LeanBlock)).lower()
    assert "experimental" not in words and "winget" not in words and "poetry" not in words
    assert "elan" in words and "never installs" in words and "fm-001" in words
    assert not hasattr(g6.lean, "__g6_family__")


def test_g6_find_ranks_lean_first_for_a_proof_task() -> None:
    assert discovery.find("check a Lean proof of a theorem")[0]["component"] == "lean"
    assert discovery.find("is this Lean 4 theorem proved without sorry or axioms")[0]["component"] == "lean"


@pytest.mark.parametrize("op", ["ops", "help"])
def test_ops_and_help_answer_over_mcp_inside_the_envelope(op: str) -> None:
    reply = invoke_reply("lean", op, json.dumps({"request_id": "req-1", "run_id": "run-1"}))
    assert check_envelope(reply) == [] and reply["ok"] is True and reply["completion_state"] == "qualified-draft"
    payload = reply["result"]
    assert (payload["component"], payload["op"]) == ("lean", op) and len(payload[op]) == len(OPS)


def test_help_marks_the_lean_ops_sensitive_and_says_how_to_get_lean() -> None:
    lines = get_registry().invoke("lean", "help").unwrap()["help"]
    marked = [line for line in lines if "G6_MCP_SENSITIVE" in line]
    assert [line.split("(")[0] for line in marked] == list(SENSITIVE)
    text = " ".join(lines)
    assert "Python only" not in text  # owner, 2026-10-20: the person running the server may allow them over MCP
    assert "elan" in text and LEAN_ENV in text and "never installs" in text and "#print axioms" in text
    assert get_registry().invoke("lean", "ops").unwrap()["sensitive"] == list(SENSITIVE)


@pytest.mark.parametrize("op", list(SENSITIVE))
def test_the_lean_ops_are_refused_over_mcp_by_the_registrys_rule(op: str) -> None:
    reply = invoke_reply("lean", op, json.dumps(SENSITIVE[op]))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated" and check_envelope(reply) == []
    assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"


@pytest.mark.parametrize("op", list(SENSITIVE))
def test_the_lean_ops_answer_from_python(op: str, no_lean) -> None:
    payload = get_registry().invoke("lean", op, SENSITIVE[op]).unwrap()
    assert check_envelope(payload) == [] and payload["code"] == LEAN_MISSING and payload["op"] == op
    assert payload["completion_state"] == "blocked-escalated"


@pytest.mark.parametrize(("op", "params"), [
    ("run_code", {"code": "#eval 1"}),
    ("lakefile", {"source": TWO}),
    ("mathlib", {}),
    ("download", {"url": "https://example.org/lean.zip"}),
    ("eval_expr", {"expr": "1 + 1"}),
    ("check_proof", {**SENSITIVE["check_proof"], "lakefile": "import Lake"}),
    ("check_proof", {**SENSITIVE["check_proof"], "mathlib": True}),
    ("check_proof", {**SENSITIVE["check_proof"], "download": True}),
    ("check_proof", {**SENSITIVE["check_proof"], "lean_args": ["--plugin=x.dll"]}),
    ("check_proof", {**SENSITIVE["check_proof"], "time_limit": -1}),
    ("check_proof", {"source": ["theorem"], "theorem": "t"}),
    ("lean_version", {"executable": "C:/somewhere/lean.exe"}),
])
def test_unknown_ops_unknown_fields_and_bad_parameters_are_refused(op: str, params: dict) -> None:
    result = get_registry().invoke("lean", op, params)
    assert result.__class__.__name__ == "Failure"
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION] [LEAN_ERROR]")
    if op not in SENSITIVE:
        reply = invoke_reply("lean", op, json.dumps(params))
        assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
        assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and check_envelope(reply) == []


_RUNNER = "runner.py"
_NETWORK = {"socket", "urllib", "http", "requests", "httpx", "ftplib", "asyncio"}
_NEVER = {"g6.core.sandbox", "g6.core.llm", "g6.research", "multiprocessing", "pickle", "marshal", "ctypes"}
#: The proof search takes an injected model caller (core run 7); only these two files may name its type.
_MODEL_FILES = {"search.py", "lean_block.py"}
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
            found.append(f"{path.name}: subprocess outside the runner")
        tree = ast.parse(text)
        never = _NEVER - {"g6.core.llm"} if path.name in _MODEL_FILES else _NEVER
        found += [f"{path.name}: import {n}" for n in _imports(tree)
                  if any(n == m or n.startswith(m + ".") for m in _NETWORK | never)]
        found += [f"{path.name}:{node.lineno}: {node.func.id}()" for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS]
    return found


def test_no_caller_code_runs_in_process_and_programs_start_only_from_the_runner() -> None:
    """The caller's source is text G6 screens and writes to a file for Lean; nothing a caller sends is evaluated here.
    Only the runner starts a program, through g6.core.component_process, and nothing reaches the network, so Lean, a
    toolchain or Mathlib is never fetched."""
    assert _scan(sorted(SRC.rglob("*.py")), _RUNNER) == []
    runner = (SRC / _RUNNER).read_text(encoding="utf-8")
    assert "component_process" in runner and "shell=True" not in runner and "Popen(" not in runner
    assert "--plugin" not in runner and "--load-dynlib" not in runner and "--run" not in runner


def test_the_guard_finds_what_it_looks_for(tmp_path: Path) -> None:
    """Survival control: the scan above reports each planted breach, so its empty answer means something."""
    planted = tmp_path / "planted.py"
    planted.write_text("import subprocess\nimport urllib.request\nresult = eval('1')\nimport os\nos.system('x')\n"
                       "exec('y')\n", encoding="utf-8")
    found = _scan([planted], _RUNNER)
    assert any("eval(" in f for f in found) and any("os.system" in f for f in found) and any("exec(" in f for f in found)
    assert any("outside the runner" in f for f in found) and any("urllib.request" in f for f in found)


def test_every_file_is_apache_2() -> None:
    for path in sorted([*SRC.rglob("*.py"), *Path(__file__).parent.glob("*.py")]):
        assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name


def test_no_committed_file_names_a_users_path() -> None:
    """The release scan flags user paths; Lean on a developer's machine is found through PATH or the environment."""
    user_path = re.compile(r"[a-z]:[\\/]+users[\\/]+|\.elan[\\/]+(?:bin|toolchains)|/home/\w+", re.IGNORECASE)
    for path in sorted([*SRC.rglob("*.py"), *Path(__file__).parent.glob("*.py")]):
        assert not user_path.search(path.read_text(encoding="utf-8")), path.name


def test_importing_the_package_starts_no_program_and_raises_no_deprecation(tmp_path: Path) -> None:
    subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", "import g6.lean"], cwd=tmp_path, check=True)
    probe = ("import subprocess\n"
             "def refuse(*a, **k):\n    raise AssertionError('importing g6.lean started a program')\n"
             "subprocess.Popen = refuse\n"
             "import g6.lean\n"
             "import g6.lean.lean_block\n")
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)
