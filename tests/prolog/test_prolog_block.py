# SPDX-License-Identifier: Apache-2.0
"""g6.prolog's block (owner, 2026-10-20): registered with ``ops`` and ``help`` reachable over MCP inside the library
envelope; ``query``, ``prove``, ``check_program`` and ``swipl_version`` run SWI-Prolog, a program on the caller's
machine, so they are refused over MCP by the registry's rule for sensitive ops unless the person running the server
lists them in ``G6_MCP_SENSITIVE``; unknown ops and unknown fields refused; no caller code run in process (no janus, no
mini-Prolog), no program started outside the runner, and importing the package starts nothing. Everything here runs
without SWI-Prolog."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import g6.prolog
from g6.core import discovery, skills
from g6.core.maturity import maturity_reason
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import invoke_reply
from g6.prolog.envelope import SWIPL_ENV, SWIPL_MISSING
from g6.prolog.prolog_block import PrologBlock

SRC = Path(g6.prolog.__file__).parent
FAMILY = "parent(tom, bob).\nancestor(X, Y) :- parent(X, Y).\n"
SENSITIVE = {"query": {"program": FAMILY, "goal": "ancestor(tom, X)"},
             "prove": {"program": FAMILY, "goal": "ancestor(tom, bob)"},
             "check_program": {"program": FAMILY}, "swipl_version": {}}


@pytest.fixture
def no_swipl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SWIPL_ENV, str(tmp_path / "no-swipl-here"))


def test_prolog_is_registered_with_a_block_and_every_swipl_op_sensitive() -> None:
    assert get_registry().get_class("prolog") is PrologBlock
    info = discovery.describe("prolog")
    assert info["block"] == "PrologBlock" and info["maturity"] == "beta"
    assert sorted(info["sensitive_ops"]) == sorted(SENSITIVE) == sorted(g6.prolog.__g6_sensitive_ops__)
    assert info["requires"] == [] and info["ops"] == list(SENSITIVE)
    assert info["summary"].endswith(".") and len(info["summary"]) <= 170
    words = ((g6.prolog.__doc__ or "") + maturity_reason(PrologBlock)).lower()
    assert "experimental" not in words and "winget" not in words and "poetry" not in words
    assert "swi-prolog.org" in words and "never installed by g6" in words and "fm-010" in words
    # R3 (owner, 2026-10-21): the card describes the restricted syntax and claims no sandbox.
    summary, reason = info["summary"].lower(), maturity_reason(PrologBlock).lower()
    assert "restricted syntax" in summary and "sandbox" not in summary and "sandbox" not in reason
    for text in (g6.prolog.__doc__ or "", reason, skills.skill_text("prolog") or "",
                 g6.prolog.prolog_block.__doc__ or ""):
        assert "not a security boundary" in " ".join(text.lower().split())
    assert not hasattr(g6.prolog, "__g6_family__")
    assert [p.name for p in g6.prolog.__g6_outside__] == ["SWI-Prolog"]
    (swipl,) = g6.prolog.__g6_outside__
    assert (swipl.command, swipl.env, swipl.licence) == ("swipl", SWIPL_ENV, "BSD-2-Clause")
    assert "swi-prolog.org" in swipl.install and "never installs" in swipl.install


def test_g6_find_ranks_prolog_first_for_a_prolog_task() -> None:
    assert discovery.find("query a Prolog program")[0]["component"] == "prolog"
    assert discovery.find("run a goal against Prolog facts and rules with SWI-Prolog")[0]["component"] == "prolog"


@pytest.mark.parametrize("op", ["ops", "help"])
def test_ops_and_help_answer_over_mcp_inside_the_envelope(op: str) -> None:
    reply = invoke_reply("prolog", op, json.dumps({"request_id": "req-1", "run_id": "run-1"}))
    assert check_envelope(reply) == [] and reply["ok"] is True and reply["completion_state"] == "qualified-draft"
    payload = reply["result"]
    assert (payload["component"], payload["op"]) == ("prolog", op) and len(payload[op]) == len(SENSITIVE)


def test_help_marks_every_swipl_op_sensitive_and_says_how_to_get_swi_prolog() -> None:
    lines = get_registry().invoke("prolog", "help").unwrap()["help"]
    marked = [line for line in lines if "G6_MCP_SENSITIVE" in line]
    assert [line.split("(")[0] for line in marked] == list(SENSITIVE)
    text = " ".join(lines)
    assert "Python only" not in text  # owner, 2026-10-20: the person running the server may allow them over MCP
    assert "swi-prolog.org" in text and SWIPL_ENV in text and "never installs" in text and "closed world" in text
    assert get_registry().invoke("prolog", "ops").unwrap()["sensitive"] == list(SENSITIVE)


@pytest.mark.parametrize("op", list(SENSITIVE))
def test_the_swipl_ops_are_refused_over_mcp_by_the_registrys_rule(op: str) -> None:
    reply = invoke_reply("prolog", op, json.dumps(SENSITIVE[op]), allow_sensitive=frozenset())
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated" and check_envelope(reply) == []
    assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"


@pytest.mark.parametrize("op", list(SENSITIVE))
def test_the_swipl_ops_reach_the_component_when_the_server_allows_them(op: str, no_swipl) -> None:
    reply = invoke_reply("prolog", op, json.dumps(SENSITIVE[op]), allow_sensitive=frozenset({"prolog"}))
    assert check_envelope(reply) == [] and reply["warning_card"]["code"] == SWIPL_MISSING
    assert reply["completion_state"] == "blocked-escalated" and reply["result"]["op"] == op


@pytest.mark.parametrize("op", list(SENSITIVE))
def test_the_swipl_ops_answer_from_python(op: str, no_swipl) -> None:
    payload = get_registry().invoke("prolog", op, SENSITIVE[op]).unwrap()
    assert check_envelope(payload) == [] and payload["code"] == SWIPL_MISSING and payload["op"] == op
    assert payload["completion_state"] == "blocked-escalated"


@pytest.mark.parametrize(("op", "params"), [
    ("consult", {"program": FAMILY}),
    ("assert_clause", {"clause": "p(1)."}),
    ("retract_clause", {"clause": "p(1)."}),
    ("meta_interpret", {"goal": "true"}),
    ("shell", {"command": "echo pwned"}),
    ("query", {**SENSITIVE["query"], "janus": True}),
    ("query", {**SENSITIVE["query"], "executable": "C:/somewhere/swipl.exe"}),
    ("query", {**SENSITIVE["query"], "swipl_args": ["-g", "shell('echo pwned')"]}),
    ("query", {**SENSITIVE["query"], "consult": True}),
    ("query", {**SENSITIVE["query"], "time_limit": -1}),
    ("query", {**SENSITIVE["query"], "max_solutions": 0}),
    ("query", {**SENSITIVE["query"], "inference_limit": 0}),
    ("query", {"program": ["parent(tom, bob)."], "goal": "parent(tom, X)"}),
    ("check_program", {"program": FAMILY, "file": "family.pl"}),
    ("swipl_version", {"executable": "C:/somewhere/swipl.exe"}),
])
def test_unknown_ops_unknown_fields_and_bad_parameters_are_refused(op: str, params: dict) -> None:
    result = get_registry().invoke("prolog", op, params)
    assert result.__class__.__name__ == "Failure"
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION] [PROLOG_ERROR]")
    reply = invoke_reply("prolog", op, json.dumps(params), allow_sensitive=frozenset({"prolog"}))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and check_envelope(reply) == []


_RUNNER = "runner.py"
_NETWORK = {"socket", "urllib", "http", "requests", "httpx", "ftplib", "asyncio"}
_NEVER = {"g6.core.sandbox", "g6.core.llm", "g6.research", "multiprocessing", "pickle", "marshal", "ctypes", "janus",
          "janus_swi", "pyswip"}
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
        found += [f"{path.name}: import {n}" for n in _imports(tree)
                  if any(n == m or n.startswith(m + ".") for m in _NETWORK | _NEVER)]
        found += [f"{path.name}:{node.lineno}: {node.func.id}()" for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS]
    return found


def test_no_caller_code_runs_in_process_and_programs_start_only_from_the_runner() -> None:
    """The caller's program and goal are text G6 writes to files for SWI-Prolog; nothing a caller sends is evaluated
    here, and there is no in-process bridge. Only the runner starts a program, through g6.core.component_process, and
    nothing reaches the network, so SWI-Prolog or a pack is never fetched."""
    assert _scan(sorted(SRC.rglob("*.py")), _RUNNER) == []
    runner = (SRC / _RUNNER).read_text(encoding="utf-8")
    assert "component_process" in runner and "shell=True" not in runner and "Popen(" not in runner
    assert '"-f", "none"' in runner and '"--no-packs"' in runner and '"-g"' not in runner


def test_the_guard_finds_what_it_looks_for(tmp_path: Path) -> None:
    """Survival control: the scan above reports each planted breach, so its empty answer means something."""
    planted = tmp_path / "planted.py"
    planted.write_text("import subprocess\nimport urllib.request\nimport janus_swi\nresult = eval('1')\nimport os\n"
                       "os.system('x')\nexec('y')\n", encoding="utf-8")
    found = _scan([planted], _RUNNER)
    assert any("eval(" in f for f in found) and any("os.system" in f for f in found) and any("exec(" in f for f in found)
    assert any("outside the runner" in f for f in found) and any("urllib.request" in f for f in found)
    assert any("janus_swi" in f for f in found)


_DRIVER_NEVER = ("consult(", "load_files(", "ensure_loaded(", "shell(", "process_create(", "open(program",
                 "use_module(program", "[program")


def _driver_breaches(text: str) -> list[str]:
    code = [line.split("%", 1)[0] for line in text.splitlines() if not line.lstrip().startswith("%")]
    return [word for word in _DRIVER_NEVER if any(word in line for line in code)]


def test_the_driver_never_consults_the_program_and_never_starts_a_program() -> None:
    """driver.pl reads the program with read_term/3; it has no consult, load or shell call of its own."""
    text = (SRC / "driver.pl").read_text(encoding="utf-8")
    assert text.startswith("% SPDX-License-Identifier: Apache-2.0\n")
    assert _driver_breaches(text) == []
    assert "read_term(" in text and "safe_goal(" in text and "set_prolog_flag(quasi_quotations, false)" in text
    assert "call_with_time_limit(" in text and "call_with_inference_limit(" in text
    assert "nb_setval" not in text and "b_setval" not in text  # the nonce is never where a goal could read it
    planted = text + "\nboot :- consult('program.pl').\n"
    assert _driver_breaches(planted) == ["consult("]  # survival control


#: The allow-list (``allowed/1`` in driver.pl): what a goal may call besides the program's own predicates, the control
#: constructs and the meta predicates whose goal arguments the driver checks (findall/3, bagof/3, setof/3, forall/2,
#: aggregate_all/3 and maplist/2 to maplist/4). A change to it is a change to the restricted syntax.
ALLOW_LIST = {
    "true/0", "fail/0", "false/0", "!/0",
    "=/2", "\\=/2", "==/2", "\\==/2", "@</2", "@>/2", "@=</2", "@>=/2", "compare/3",
    "is/2", "=:=/2", "=\\=/2", "</2", ">/2", "=</2", ">=/2", "between/3", "succ/2", "plus/3",
    "var/1", "nonvar/1", "atom/1", "number/1", "integer/1", "float/1", "atomic/1", "compound/1", "callable/1",
    "is_list/1", "ground/1",
    "functor/3", "arg/3", "=../2", "copy_term/2",
    "atom_codes/2", "atom_chars/2", "char_code/2", "atom_number/2", "number_codes/2", "number_chars/2",
    "atom_length/2",
    "member/2", "memberchk/2", "append/3", "length/2", "nth0/3", "nth1/3", "last/2", "reverse/2", "msort/2",
    "sort/2", "sort/4", "sum_list/2", "max_list/2", "min_list/2", "numlist/3", "list_to_set/2",
}


def _driver_code(text: str) -> list[str]:
    return [line.split("%", 1)[0] for line in text.splitlines() if not line.lstrip().startswith("%")]


def test_the_allow_list_is_pinned() -> None:
    text = (SRC / "driver.pl").read_text(encoding="utf-8")
    found = [re.sub(r"^\((.+)\)/(\d+)$", r"\1/\2", m) for m in re.findall(r"^allowed\((.+)\)\.$", text, re.MULTILINE)]
    assert len(found) == len(set(found)) and set(found) == ALLOW_LIST


_MESSAGE_NEVER = ("translate_message", "print_message", "message_to_codes", "~p", "~@", "~q", "portray(true)")


def _message_breaches(code: list[str]) -> list[str]:
    return [word for word in _MESSAGE_NEVER if any(word in line for line in code)]


def test_the_driver_writes_terms_with_no_message_translation_or_portray_hook() -> None:
    """R2: answers and errors are written with write_term, quoted, with no portray hook and attributes ignored; no
    term the caller built goes to print_message/2, message translation or format's ~p and ~@."""
    code = _driver_code((SRC / "driver.pl").read_text(encoding="utf-8"))
    assert _message_breaches(code) == []
    assert all(word in "\n".join(code) for word in ("write_term(", "portray(false)", "attributes(ignore)"))
    assert _message_breaches([*code, "x :- print_message(error, y)."]) == ["print_message"]  # survival control


_SANDBOX_CLAIMS = ("checked by swi-prolog's sandbox", "checked by its sandbox", "under the same sandbox",
                   "the sandbox refuses", "the sandbox refused", "the sandbox below")


def test_nothing_in_the_package_says_g6_sandboxes_prolog() -> None:
    """R3 (owner, 2026-10-21): containing a program you do not trust is your own sandbox to manage."""
    for path in sorted([*SRC.rglob("*.py"), SRC / "driver.pl", SRC / "skill" / "SKILL.md"]):
        text = " ".join(path.read_text(encoding="utf-8").lower().split())
        assert [claim for claim in _SANDBOX_CLAIMS if claim in text] == [], path.name


def test_every_file_is_apache_2() -> None:
    for path in sorted([*SRC.rglob("*.py"), *Path(__file__).parent.glob("*.py")]):
        assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name


def test_no_committed_file_names_a_users_path() -> None:
    """The release scan flags user paths; SWI-Prolog on a developer's machine is found through PATH or G6_SWIPL."""
    user_path = re.compile(r"[a-z]:[\\/]+users[\\/]+|program files[\\/]+swipl|/home/\w+", re.IGNORECASE)
    for path in sorted([*SRC.rglob("*.py"), SRC / "driver.pl", *Path(__file__).parent.glob("*.py")]):
        assert not user_path.search(path.read_text(encoding="utf-8")), path.name


def test_importing_the_package_starts_no_program_and_raises_no_deprecation(tmp_path: Path) -> None:
    subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", "import g6.prolog"], cwd=tmp_path,
                   check=True)
    probe = ("import subprocess\n"
             "def refuse(*a, **k):\n    raise AssertionError('importing g6.prolog started a program')\n"
             "subprocess.Popen = refuse\n"
             "import g6.prolog\n"
             "import g6.prolog.prolog_block\n")
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)
