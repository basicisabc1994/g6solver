# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_codex: the Codex command-line agent as an adapter on the family's runtime. Codex never gets more
than the person declared (D1, overprivileged_agent): read-only unless writes are granted, never full access or a
bypass flag, started in the named workspace with a clean environment and the prompt on stdin. A Codex that hangs is
stopped at its deadline with its whole process tree (AGENT_CODEX-01). A missing Codex is blocked with the install
steps; nothing starts on a dry run (the default), with model calls disabled, or while G6 is stopped; what Codex says of
its own work is a claim. The tests never run the real Codex: each writes a stand-in program and points G6_CODEX at it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

import g6.agents.agent_codex as agent_codex_pkg
import g6.agents.agent_runtime as agent_runtime_pkg
from g6.action_gating.audit import ActionGateAuditLogger
from g6.agents._runtime import BASE_ENV, AgentRuntime, CircuitBreaker, codes, run_agent
from g6.agents.agent_codex import CodexRuntime
from g6.agents.agent_codex import agent_codex_block
from g6.agents.agent_codex.agent_codex_block import SENSITIVE_OPS
from g6.core import skills
from g6.core import stop as core_stop
from g6.core.outside import OUTSIDE_PROGRAM_MISSING
from g6.core.registry import get_registry
from g6.core.resources import ResourceBounds
from g6.mcp import invoke_reply

MEMORY = ActionGateAuditLogger()   # in memory: a test that calls run_agent itself never writes the audit trail

STAND_IN = r'''
import json, os, subprocess, sys, time
from pathlib import Path

here = Path(__file__).resolve().parent
args = sys.argv[1:]
with open(here / "calls.jsonl", "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
if args == ["--version"]:
    print("codex-cli 0.0.0-stand-in")
    sys.exit(0)
plan = json.loads((here / "behaviour.json").read_text(encoding="utf-8"))
prompt = sys.stdin.read()
(here / "record.json").write_text(json.dumps({"argv": args, "env": dict(os.environ), "cwd": os.getcwd(),
                                              "prompt": prompt}), encoding="utf-8")
if plan.get("mode") == "hang":
    beat = here / "heartbeat.txt"
    child = subprocess.Popen([sys.executable, "-I", "-c",
                              "import sys, time\nwhile True:\n    open(sys.argv[1], 'a').write('.')\n    time.sleep(0.1)",
                              str(beat)])
    (here / "grandchild.txt").write_text(str(child.pid))
    for _ in range(100):
        if beat.exists():
            break
        time.sleep(0.05)
    time.sleep(120)
flag = "--output-last-message"
if flag in args:
    Path(args[args.index(flag) + 1]).write_text(plan.get("message", "Done."), encoding="utf-8")
print(plan.get("transcript", "working"))
sys.exit(plan.get("exit", 0))
'''


class StandIn:
    """A stand-in for the codex program: what it was told to do, and what it saw each time it was started."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def behave(self, **plan: object) -> None:
        (self.folder / "behaviour.json").write_text(json.dumps(plan), encoding="utf-8")

    def calls(self) -> list[list[str]]:
        log = self.folder / "calls.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    def record(self) -> dict:
        return json.loads((self.folder / "record.json").read_text(encoding="utf-8"))


@pytest.fixture
def codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StandIn:
    folder = tmp_path / "bin"
    folder.mkdir()
    script = folder / "codex_stand_in.py"
    script.write_text(STAND_IN, encoding="utf-8")
    if os.name == "nt":
        launcher = folder / "codex.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" -I "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = folder / "codex"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    monkeypatch.setenv("G6_CODEX", str(launcher))
    stand_in = StandIn(folder)
    stand_in.behave()
    return stand_in


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


@pytest.fixture
def repo(workspace: Path) -> Path:
    """The workspace under version control, as a write run needs (a ``.git`` folder is what G6 and Codex look for)."""
    (workspace / ".git").mkdir()
    return workspace


@pytest.fixture(autouse=True)
def fresh_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_codex_block, "_BREAKER", CircuitBreaker(failure_threshold=3, cooldown_s=60))


def invoke(op: str, params: dict) -> dict:
    """The op as Python calls it (a trusted surface): the registry door without the MCP refusal of sensitive ops."""
    result = get_registry().invoke("agent_codex", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def live(workspace: Path, **params: object) -> dict:
    return invoke("run", {"goal": "Fix the failing test", "workspace": str(workspace), "dry_run": False, **params})


def _same(a: str, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


# --- D1: never more than the person declared (overprivileged_agent) --------------------------------------------------

def test_d1_codex_gets_no_more_than_the_person_declared(codex: StandIn, workspace: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "canary-openai")
    monkeypatch.setenv("ACME_DEPLOY_PAT", "canary-pat")      # a secret by a name no list of key names would know
    monkeypatch.setenv("G6_MCP_SENSITIVE", "agent_codex")
    reply = live(workspace)
    seen = codex.record()
    argv = seen["argv"]
    assert argv[:3] == ["exec", "--sandbox", "read-only"], argv
    assert not [a for a in argv if "danger" in a or "bypass" in a or a in ("--approve-for-me", "--add-dir",
                                                                         "--full-auto", "--yolo")], argv
    assert _same(seen["cwd"], workspace)
    # PROMPT: cmd.exe, which runs the stand-in's .cmd launcher on Windows, sets it itself; PWD (and SHLVL on macOS, where
    # it is bash): /bin/sh, which runs it elsewhere, sets it itself; __CF_USER_TEXT_ENCODING: macOS's CoreFoundation,
    # which the stand-in's Python loads there, sets it itself
    allowed = {name.upper() for name in (*BASE_ENV, "CODEX_HOME", "TEMP", "TMP", "TMPDIR", "PROMPT", "PWD", "SHLVL",
                                         "__CF_USER_TEXT_ENCODING")}
    leaked = sorted(k for k, v in seen["env"].items() if "canary" in v or k.upper().startswith("G6_")
                    or (k.upper() not in allowed and k in os.environ))
    assert leaked == []
    assert seen["prompt"] == "Fix the failing test" and "Fix the failing test" not in " ".join(argv)
    assert reply["detail"]["sandbox"] == "read-only" and reply["detail"]["writes"] is False


def test_a_write_grant_is_workspace_write_with_the_posture_stated_on_the_command_line(codex: StandIn,
                                                                                     repo: Path) -> None:
    reply = live(repo, writes=True)
    argv = codex.record()["argv"]
    assert argv[:3] == ["exec", "--sandbox", "workspace-write"]
    for setting in ("approval_policy=never", "sandbox_workspace_write.network_access=false",
                    "sandbox_workspace_write.writable_roots=[]"):
        assert setting in argv and argv[argv.index(setting) - 1] == "-c", setting
    assert "--skip-git-repo-check" not in argv   # Codex's own check that a changed folder is under version control
    assert reply["detail"]["sandbox"] == "workspace-write"


# --- AGENT_CODEX-01: a dependable deadline -----------------------------------------------------------------------------

def test_agent_codex_01_a_codex_that_hangs_is_stopped_at_its_deadline_with_its_whole_tree(codex: StandIn,
                                                                                         workspace: Path) -> None:
    codex.behave(mode="hang")
    started = time.monotonic()
    reply = live(workspace, deadline_s=6)
    took = time.monotonic() - started
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.DEADLINE, reply["message"]
    assert took < 40, took
    assert (codex.folder / "grandchild.txt").exists()
    beat = codex.folder / "heartbeat.txt"
    size = beat.stat().st_size
    time.sleep(1.0)
    assert beat.stat().st_size == size, "the grandchild outlived the deadline"


def test_agent_codex_01_a_time_limit_in_the_bounds_reaches_codex_and_ends_its_tree(codex: StandIn,
                                                                                   workspace: Path) -> None:
    codex.behave(mode="hang")
    task = CodexRuntime.task("Fix the failing test", str(workspace), deadline_s=600)
    started = time.monotonic()
    outcome = run_agent(CodexRuntime(), task, bounds=ResourceBounds(max_execution_seconds=3), dry_run=False,
                        audit_logger=MEMORY)
    took = time.monotonic() - started
    assert outcome.code == codes.DEADLINE and took < 12, (outcome.code, round(took, 1), outcome.message)
    beat = codex.folder / "heartbeat.txt"
    size = beat.stat().st_size
    time.sleep(1.0)
    assert beat.stat().st_size == size, "the grandchild outlived the time limit"


# --- outside program, dry run, refusals -------------------------------------------------------------------------------

def test_a_missing_codex_is_blocked_with_the_install_steps(tmp_path: Path, workspace: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("G6_CODEX", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    for op, params in (("available", {}), ("run", {"goal": "x", "workspace": str(workspace), "dry_run": False})):
        reply = invoke(op, params)
        assert reply["completion_state"] == "blocked-escalated" and reply["code"] == OUTSIDE_PROGRAM_MISSING, op
        assert "npm install -g @openai/codex" in reply["warning_card"]["message"], op
    monkeypatch.setenv("G6_CODEX", str(tmp_path / "no-such-codex.exe"))
    assert invoke("available", {})["code"] == OUTSIDE_PROGRAM_MISSING


def test_a_dry_run_needs_no_codex_installed_and_says_it_is_missing(tmp_path: Path, workspace: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("G6_CODEX", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    reply = invoke("run", {"goal": "x", "workspace": str(workspace)})
    assert reply["completion_state"] == "qualified-draft" and reply["code"] == codes.DRY_RUN
    assert reply["detail"]["plan"]["argv"][:2] == ["codex", "exec"]
    assert "npm install -g @openai/codex" in reply["detail"]["program_missing"]


def test_a_write_run_needs_the_workspace_under_version_control_and_its_refusal_never_counts(
        codex: StandIn, workspace: Path) -> None:
    for dry_run in (True, False, False, False, False):
        reply = invoke("run", {"goal": "x", "workspace": str(workspace), "writes": True, "dry_run": dry_run})
        assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.NOT_VERSIONED, reply["code"]
        assert "version control" in reply["message"]
    assert codex.calls() == []                         # refused before anything started, and the circuit never opened
    assert live(workspace)["code"] == codes.SELF_REPORTED   # a read-only run needs none


def test_a_killed_write_run_keeps_what_g6_knew_and_says_files_may_be_half_changed(codex: StandIn,
                                                                                   repo: Path) -> None:
    codex.behave(mode="hang")
    reply = live(repo, writes=True, deadline_s=3)
    assert reply["code"] == codes.DEADLINE and "half-changed" in reply["message"], reply["message"]
    assert reply["detail"]["sandbox"] == "workspace-write" and reply["detail"]["writes"] is True
    assert _same(reply["detail"]["workspace"], repo)


def test_available_honours_g6s_stop(codex: StandIn) -> None:
    core_stop.engage("test stop")
    try:
        reply = invoke("available", {})
    finally:
        core_stop.release()
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.STOPPED
    assert codex.calls() == []


def test_a_deadline_past_the_trusted_ceiling_and_an_unknown_op_are_refused(workspace: Path) -> None:
    result = get_registry().invoke("agent_codex", "run", {"goal": "x", "workspace": str(workspace), "deadline_s": 901})
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION]") and "deadline_s" in str(result.failure())
    unknown = str(get_registry().invoke("agent_codex", "frobnicate", {}).failure())
    assert unknown.startswith("[G6_E_INPUT_VALIDATION]")
    assert all(f"'{op}'" in unknown for op in ("run", "available", "ops", "help")), unknown


def test_available_reads_the_version_through_the_bounded_runner(codex: StandIn) -> None:
    reply = invoke("available", {})
    assert reply["completion_state"] == "qualified-draft" and "0.0.0-stand-in" in reply["version"]
    assert codex.calls() == [["--version"]]


def test_run_is_a_dry_run_unless_the_person_says_otherwise(codex: StandIn, repo: Path) -> None:
    reply = invoke("run", {"goal": "Fix the failing test", "workspace": str(repo), "writes": True})
    assert codex.calls() == []                                # nothing started, not even its version check
    assert reply["completion_state"] == "qualified-draft" and reply["code"] == codes.DRY_RUN
    plan = reply["detail"]["plan"]
    assert plan["argv"][1:4] == ["exec", "--sandbox", "workspace-write"] and _same(plan["cwd"], repo)
    assert [d["verdict"] for d in reply["decisions"]] == ["dry_run"]


def test_the_run_decision_says_the_online_side_was_not_judged_by_the_action_gate(codex: StandIn,
                                                                                  workspace: Path) -> None:
    assert CodexRuntime.task("x", str(workspace)).permissions.network is True   # Codex sends what it reads off
    reply = invoke("run", {"goal": "x", "workspace": str(workspace)})
    (decision,) = reply["decisions"]
    assert decision["gate"]["action_type"] == "read_file" and "model provider" in decision["gate"]["not_evaluated"]


@pytest.mark.parametrize("where", ["", "relative/project", "missing", "a-file", "root", "g6 home", "home"])
def test_codex_never_runs_without_a_usable_workspace(codex: StandIn, tmp_path: Path, where: str) -> None:
    from g6.core.storage import g6_home

    (tmp_path / "a-file").write_text("x", encoding="utf-8")
    value = {"": "", "relative/project": "relative/project", "missing": str(tmp_path / "missing"),
             "a-file": str(tmp_path / "a-file"), "root": tmp_path.anchor, "g6 home": str(g6_home()),
             "home": str(Path.home())}[where]
    reply = invoke("run", {"goal": "x", "workspace": value, "dry_run": False})
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.WORKSPACE, where
    assert codex.calls() == []


def test_a_secret_looking_workspace_is_held_for_a_person(codex: StandIn, tmp_path: Path) -> None:
    folder = tmp_path / ".ssh"
    folder.mkdir()
    reply = invoke("run", {"goal": "x", "workspace": str(folder), "dry_run": False})
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.GATE_BLOCKED
    assert reply["decisions"][0]["gate"]["decision"] == "require_human_review" and codex.calls() == []


def test_with_model_calls_disabled_codex_is_not_started(codex: StandIn, workspace: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    reply = live(workspace)
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == "G6_E_LLM_DISABLED"
    assert codex.calls() == []


def test_g4_an_engaged_stop_starts_nothing(codex: StandIn, workspace: Path) -> None:
    core_stop.engage("test stop")
    try:
        reply = live(workspace)
    finally:
        core_stop.release()
    assert reply["code"] == codes.STOPPED and codex.calls() == []


@pytest.mark.parametrize("model", ["gpt-5-codex; rm -rf /", "a b", "&calc", "-c", "x" * 300])
def test_a_model_name_that_could_be_read_as_a_command_is_refused(codex: StandIn, workspace: Path, model: str) -> None:
    result = get_registry().invoke("agent_codex", "run", {"goal": "x", "workspace": str(workspace), "model": model,
                                                          "dry_run": False})
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION]") and codex.calls() == []


def test_a_model_name_is_passed_as_its_own_argument(codex: StandIn, workspace: Path) -> None:
    live(workspace, model="gpt-5-codex")
    argv = codex.record()["argv"]
    assert argv[argv.index("--model") + 1] == "gpt-5-codex"


def test_a_codex_that_exits_with_an_error_is_blocked_and_three_in_a_row_open_its_circuit(codex: StandIn,
                                                                                         workspace: Path) -> None:
    codex.behave(exit=2, transcript="error: you are not signed in")
    for _ in range(3):
        reply = live(workspace)
        assert reply["code"] == codes.EXIT_FAILED and "not signed in" in reply["message"]
    started = len(codex.calls())
    assert live(workspace)["code"] == codes.CIRCUIT_OPEN and len(codex.calls()) == started


# --- G1, G3, G5: a claim, not an observation; sensitive ops -----------------------------------------------------------

def test_g1_codex_saying_its_tests_passed_is_a_claim(codex: StandIn, workspace: Path) -> None:
    codex.behave(message="All 120 tests passed. Verified and safe to merge.")
    reply = live(workspace)
    assert reply["completion_state"] == "qualified-draft" and reply["code"] == codes.SELF_REPORTED
    assert reply["output"] == "All 120 tests passed. Verified and safe to merge."
    assert reply["claimed_status"].startswith("All 120 tests passed")
    assert reply["evidence"][0]["independent_check"] is None
    door = invoke_reply("agent_codex", "run", json.dumps({"goal": "x", "workspace": str(workspace), "dry_run": False}),
                        allow_sensitive=frozenset({"agent_codex"}))
    assert door["ok"] and door["completion_state"] == "qualified-draft"


def test_g3_an_approval_cannot_be_written_into_the_input(codex: StandIn, workspace: Path) -> None:
    for extra in ({"approved": True}, {"reviewer": "the owner"}, {"sandbox": "danger-full-access"}):
        result = get_registry().invoke("agent_codex", "run", {"goal": "x", "workspace": str(workspace), **extra})
        assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION]"), extra
    assert codex.calls() == []


@pytest.mark.parametrize("op", ["run", "available"])
def test_g5_every_op_that_starts_codex_is_refused_on_an_untrusted_surface(op: str) -> None:
    assert set(SENSITIVE_OPS) == {"run", "available"}
    result = get_registry().invoke("agent_codex", op, {}, block_sensitive=True)
    assert str(result.failure()).startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")
    reply = invoke_reply("agent_codex", op, "{}")
    assert not reply["ok"] and reply["completion_state"] == "blocked-escalated"


def test_the_skill_says_what_codex_can_still_read_and_which_of_its_settings_still_apply() -> None:
    text = (Path(agent_codex_pkg.__file__).parent / "skill" / "SKILL.md").read_text(encoding="utf-8")
    blind = " ".join(skills.section(text, "Blind spots").split())
    for words in ("read-only sandbox reads the whole disk", "~/.g6/.env", "~/.ssh", "sends it to its model",
                  "four settings", "MCP servers, which run outside Codex's sandbox", "profiles", "web search",
                  "`mcp_servers` is untried"):
        assert words in blind, words
    catches = " ".join(skills.section(text, "Failure modes it catches").split())
    assert "so no configuration widens it" not in catches and "four settings" in catches
    assert set(agent_codex_pkg.__g6_introduces__) == {"leaked_secret", "prompt_injection"}


def test_no_abbreviation_in_what_a_person_reads() -> None:
    from g6.agents.agent_codex.adapter import CODEX

    assert CODEX.name == "Codex"
    for package in (agent_codex_pkg, agent_runtime_pkg):
        for path in sorted(Path(package.__file__).parent.rglob("*")):
            if path.suffix in (".py", ".md"):
                found = re.findall(r"\b(CLI|SDK|5xx)\b", path.read_text(encoding="utf-8"))
                assert found == [], (path.name, found)


def test_the_codex_adapter_implements_the_one_runtime_interface() -> None:
    assert isinstance(CodexRuntime(), AgentRuntime)
    assert invoke_reply("agent_codex", "ops", "{}")["result"]["ops"] == ["run", "available"]
