# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_claude: Anthropic's Claude Code, the command-line coding agent, as an adapter on the family's
runtime. Claude Code never gets more than the person declared (D1, overprivileged_agent and unguarded_tool_use): its
own tools are named on every run (read tools only, unless writes are granted; never its shell, web or sub-agent tools,
never an MCP server or a project's settings), in default permission mode, never a flag that skips its permission
checks; it starts in the named workspace with a clean environment, so no key of G6's or the environment's reaches it,
and the prompt on stdin. A Claude Code that hangs is stopped at its deadline with its whole process tree
(AGENT_CLAUDE-04). What it says of its own work is a claim (AGENT_CLAUDE-01). The tests never run the real Claude Code:
each writes a stand-in program and points G6_CLAUDE at it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

import g6.agents.agent_claude as agent_claude_pkg
from g6.action_gating.audit import ActionGateAuditLogger
from g6.agents._runtime import BASE_ENV, AgentRuntime, CircuitBreaker, codes, run_agent
from g6.agents.agent_claude import ClaudeCodeRuntime, agent_claude_block
from g6.agents.agent_claude.adapter import HOOKS_OFF, NEVER_TOOLS, READ_TOOLS, WRITE_TOOLS
from g6.agents.agent_claude.agent_claude_block import SENSITIVE_OPS
from g6.core import skills
from g6.core import stop as core_stop
from g6.core.outside import OUTSIDE_PROGRAM_MISSING
from g6.core.registry import get_registry
from g6.core.resources import ResourceBounds
from g6.mcp import invoke_reply

MEMORY = ActionGateAuditLogger()   # in memory: a test that calls run_agent itself never writes the audit trail
#: Claude Code's tools G6 never grants: its shells (Bash, and PowerShell on Windows), its background-shell and monitor
#: tools, its web tools and its sub-agent tool under both its names.
NINE = {"Bash", "BashOutput", "KillShell", "PowerShell", "Monitor", "WebFetch", "WebSearch", "Task", "Agent"}

STAND_IN = r'''
import json, os, subprocess, sys, time
from pathlib import Path

here = Path(__file__).resolve().parent
args = sys.argv[1:]
with open(here / "calls.jsonl", "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
if args == ["--version"]:
    print("2.0.0 (Claude Code stand-in)")
    sys.exit(0)
plan = json.loads((here / "behaviour.json").read_text(encoding="utf-8"))
prompt = sys.stdin.read()
mcp = settings = ""
if "--mcp-config" in args:
    mcp = Path(args[args.index("--mcp-config") + 1]).read_text(encoding="utf-8")
if "--settings" in args:
    settings = Path(args[args.index("--settings") + 1]).read_text(encoding="utf-8")
(here / "record.json").write_text(json.dumps({"argv": args, "env": dict(os.environ), "cwd": os.getcwd(),
                                              "prompt": prompt, "mcp_config": mcp, "settings": settings}),
                                  encoding="utf-8")
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
print("note: a line Claude Code writes before its result", file=sys.stderr, flush=True)
if plan.get("mode") == "plain":
    print("I did it, trust me.")
    sys.exit(0)
result = {"type": "result", "subtype": plan.get("subtype", "success"), "is_error": plan.get("is_error", False),
          "num_turns": 3, "result": plan.get("message", "Done."), "session_id": "stand-in",
          "total_cost_usd": 0.0125, "usage": {"input_tokens": 1200, "output_tokens": 340},
          "permission_denials": plan.get("denials", [])}
print(json.dumps(result), flush=True)
sys.exit(plan.get("exit", 0))
'''


class StandIn:
    """A stand-in for the claude program: what it was told to do, and what it saw each time it was started."""

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
def claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StandIn:
    folder = tmp_path / "bin"
    folder.mkdir()
    script = folder / "claude_stand_in.py"
    script.write_text(STAND_IN, encoding="utf-8")
    if os.name == "nt":
        launcher = folder / "claude.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" -I "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = folder / "claude"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    monkeypatch.setenv("G6_CLAUDE", str(launcher))
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
    """The workspace under version control, as a write run needs."""
    (workspace / ".git").mkdir()
    return workspace


@pytest.fixture(autouse=True)
def fresh_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_claude_block, "_BREAKER", CircuitBreaker(failure_threshold=3, cooldown_s=60))


def invoke(op: str, params: dict) -> dict:
    """The op as Python calls it (a trusted surface): the registry door without the MCP refusal of sensitive ops."""
    result = get_registry().invoke("agent_claude", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def live(workspace: Path, **params: object) -> dict:
    return invoke("run", {"goal": "Fix the failing test", "workspace": str(workspace), "dry_run": False, **params})


def _same(a: str, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _values(argv: list[str], flag: str) -> list[str]:
    """The values after ``flag`` on a command line, up to the next flag."""
    if flag not in argv:
        return []
    out = []
    for arg in argv[argv.index(flag) + 1:]:
        if arg.startswith("-"):
            break
        out.append(arg)
    return out


# --- D1: never more than the person declared (overprivileged_agent, unguarded_tool_use) ------------------------------

def test_d1_claude_code_gets_no_more_than_the_person_declared(claude: StandIn, workspace: Path, key_file,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (("ANTHROPIC_API_KEY", "canary-env-key"), ("ANTHROPIC_AUTH_TOKEN", "canary-token"),
                        ("ANTHROPIC_BASE_URL", "http://canary.invalid"), ("ACME_DEPLOY_PAT", "canary-pat")):
        monkeypatch.setenv(name, value)   # the last by a name no list of key names would know
    key_file("ANTHROPIC_API_KEY=canary-file-key\n")    # a key in the person's key file is G6's, never Claude Code's
    monkeypatch.setenv("G6_MCP_SENSITIVE", "agent_claude")
    reply = live(workspace)
    seen = claude.record()
    argv = seen["argv"]
    assert argv[0] == "-p" and _values(argv, "--output-format") == ["json"]
    assert _values(argv, "--permission-mode") == ["default"]
    assert _values(argv, "--allowedTools") == list(READ_TOOLS)
    denied = set(_values(argv, "--disallowedTools"))
    assert {"Bash", "WebFetch", "WebSearch", "Task", "Edit", "MultiEdit", "Write", "NotebookEdit"} <= denied
    assert _values(argv, "--setting-sources") == ["user"] and "--strict-mcp-config" in argv
    assert json.loads(seen["mcp_config"]) == {"mcpServers": {}}
    assert _values(argv, "--tools") == [",".join(READ_TOOLS)]          # the closed list: nothing else is available
    assert NINE <= denied and "mcp__*" in denied
    assert json.loads(seen["settings"]) == {"disableAllHooks": True}   # the person's own hooks are off for the run
    assert "managed settings" in reply["warning_card"]["message"]      # what the flags cannot turn off is said
    assert not [a for a in argv if re.search(r"danger|bypass|acceptEdits|skip-permissions", a)
                or a in ("--add-dir", "--permission-prompt-tool", "--continue", "--resume")], argv
    assert _same(seen["cwd"], workspace)
    # PROMPT: cmd.exe, which runs the stand-in's .cmd launcher on Windows, sets it itself; PWD (and SHLVL on macOS, where
    # it is bash): /bin/sh, which runs it elsewhere, sets it itself; __CF_USER_TEXT_ENCODING: macOS's CoreFoundation,
    # which the stand-in's Python loads there, sets it itself
    allowed = {name.upper() for name in (*BASE_ENV, "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_GIT_BASH_PATH", "TEMP", "TMP",
                                         "TMPDIR", "PROMPT", "PWD", "SHLVL", "__CF_USER_TEXT_ENCODING")}
    leaked = sorted(k for k, v in seen["env"].items() if "canary" in v or k.upper().startswith(("G6_", "ANTHROPIC"))
                    or (k.upper() not in allowed and k in os.environ))
    assert leaked == []
    assert seen["prompt"] == "Fix the failing test" and "Fix the failing test" not in " ".join(argv)
    assert reply["detail"]["writes"] is False and reply["detail"]["allowed_tools"] == list(READ_TOOLS)


def test_d1_a_write_grant_adds_the_edit_tools_and_never_the_shell_or_the_web(claude: StandIn, repo: Path) -> None:
    reply = live(repo, writes=True)
    argv = claude.record()["argv"]
    assert _values(argv, "--allowedTools") == [*READ_TOOLS, *WRITE_TOOLS]
    assert _values(argv, "--tools") == [",".join((*READ_TOOLS, *WRITE_TOOLS))]
    denied = set(_values(argv, "--disallowedTools"))
    assert set(NEVER_TOOLS) <= denied and not denied & set(WRITE_TOOLS)
    assert {"Bash", "WebFetch", "WebSearch"} <= set(NEVER_TOOLS)
    assert _values(argv, "--permission-mode") == ["default"]
    assert reply["detail"]["writes"] is True


def test_the_tools_g6_names_cover_one_another_exactly() -> None:
    assert not set(READ_TOOLS) & set(WRITE_TOOLS) and not set(NEVER_TOOLS) & (set(READ_TOOLS) | set(WRITE_TOOLS))
    argv = ClaudeCodeRuntime().argv("claude", writes=False, mcp_config="none.json", settings="hooks_off.json")
    assert set(_values(argv, "--disallowedTools")) == set(NEVER_TOOLS) | set(WRITE_TOOLS)


@pytest.mark.parametrize("writes", [False, True])
def test_d1_the_closed_tool_list_is_the_granted_tools_the_deny_list_holds_all_nine_and_hooks_are_off(
        writes: bool) -> None:
    runtime = ClaudeCodeRuntime()
    argv = runtime.argv("claude", writes=writes, mcp_config="none.json", settings="hooks_off.json")
    allowed, _ = runtime.tools(writes)
    (closed,) = _values(argv, "--tools")
    assert closed.split(",") == list(allowed) == _values(argv, "--allowedTools")
    assert NINE <= set(_values(argv, "--disallowedTools")) and NINE <= set(NEVER_TOOLS)
    assert _values(argv, "--settings") == ["hooks_off.json"] and json.loads(HOOKS_OFF) == {"disableAllHooks": True}


# --- AGENT_CLAUDE-04: an explicit workspace, a clean environment, a dependable deadline -----------------------------

def test_agent_claude_04_a_claude_code_that_hangs_is_stopped_at_its_deadline_with_its_whole_tree(
        claude: StandIn, workspace: Path) -> None:
    claude.behave(mode="hang")
    started = time.monotonic()
    reply = live(workspace, deadline_s=4)
    took = time.monotonic() - started
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.DEADLINE, reply["message"]
    assert took < 40, took
    assert (claude.folder / "grandchild.txt").exists()
    beat = claude.folder / "heartbeat.txt"
    size = beat.stat().st_size
    time.sleep(1.0)
    assert beat.stat().st_size == size, "the grandchild outlived the deadline"


def test_agent_claude_04_a_time_limit_in_the_bounds_reaches_claude_code(claude: StandIn, workspace: Path) -> None:
    claude.behave(mode="hang")
    task = ClaudeCodeRuntime.task("Fix the failing test", str(workspace), deadline_s=600)
    started = time.monotonic()
    outcome = run_agent(ClaudeCodeRuntime(), task, bounds=ResourceBounds(max_execution_seconds=3), dry_run=False,
                        audit_logger=MEMORY)
    assert outcome.code == codes.DEADLINE and time.monotonic() - started < 15, outcome.message


@pytest.mark.parametrize("where", ["", "relative/project", "missing", "home"])
def test_agent_claude_04_claude_code_never_runs_without_a_usable_workspace(claude: StandIn, tmp_path: Path,
                                                                           where: str) -> None:
    value = {"": "", "relative/project": "relative/project", "missing": str(tmp_path / "missing"),
             "home": str(Path.home())}[where]
    reply = invoke("run", {"goal": "x", "workspace": value, "dry_run": False})
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.WORKSPACE, where
    assert claude.calls() == []


# --- AGENT_CLAUDE-01: what Claude Code says is a claim ----------------------------------------------------------------

def test_agent_claude_01_claude_code_saying_its_work_is_verified_is_a_qualified_draft(claude: StandIn,
                                                                                      workspace: Path) -> None:
    claude.behave(message="Verified: all 120 tests pass and the change is safe to merge.")
    reply = live(workspace)
    assert reply["completion_state"] == "qualified-draft" and reply["code"] == codes.SELF_REPORTED
    assert reply["output"] == "Verified: all 120 tests pass and the change is safe to merge."
    assert reply["claimed_status"].startswith("Verified: all 120 tests pass")
    assert reply["evidence"][0]["independent_check"] is None
    door = invoke_reply("agent_claude", "run", json.dumps({"goal": "x", "workspace": str(workspace),
                                                           "dry_run": False}),
                        allow_sensitive=frozenset({"agent_claude"}))
    assert door["ok"] and door["completion_state"] == "qualified-draft"


def test_agent_claude_01_a_run_that_prints_no_result_is_not_taken_at_its_word(claude: StandIn,
                                                                              workspace: Path) -> None:
    claude.behave(mode="plain")
    reply = live(workspace)
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.NO_OUTCOME
    assert "I did it" in reply["detail"]["output_tail"]


# --- AGENT_CLAUDE-03: no hidden paid fallback --------------------------------------------------------------------------

def test_agent_claude_03_a_claude_code_that_is_not_signed_in_is_blocked_and_nothing_else_is_tried(
        claude: StandIn, workspace: Path) -> None:
    claude.behave(is_error=True, subtype="error_during_execution", exit=1, message="Invalid API key · Please run /login")
    reply = live(workspace)
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.EXIT_FAILED
    assert "/login" in reply["message"]
    assert [c for c in claude.calls() if c != ["--version"]] and len(claude.calls()) == 2   # its check, its one run


def test_agent_claude_03_the_package_reads_no_key_and_names_no_other_provider() -> None:
    root = Path(agent_claude_pkg.__file__).parent
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"openrouter|litellm|import anthropic|API_KEY|getenv", text, re.IGNORECASE), path.name


# --- outside program, dry run, refusals -------------------------------------------------------------------------------

def test_a_missing_claude_code_is_blocked_with_the_install_steps(tmp_path: Path, workspace: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("G6_CLAUDE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    for op, params in (("available", {}), ("run", {"goal": "x", "workspace": str(workspace), "dry_run": False})):
        reply = invoke(op, params)
        assert reply["completion_state"] == "blocked-escalated" and reply["code"] == OUTSIDE_PROGRAM_MISSING, op
        assert "npm install -g @anthropic-ai/claude-code" in reply["warning_card"]["message"], op


def test_run_is_a_dry_run_unless_the_person_says_otherwise_and_needs_no_claude_code(
        tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("G6_CLAUDE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    reply = invoke("run", {"goal": "Fix the failing test", "workspace": str(repo), "writes": True})
    assert reply["completion_state"] == "qualified-draft" and reply["code"] == codes.DRY_RUN
    plan = reply["detail"]["plan"]
    assert plan["argv"][:2] == ["claude", "-p"] and _same(plan["cwd"], repo)
    assert _values(plan["argv"], "--allowedTools") == [*READ_TOOLS, *WRITE_TOOLS]
    assert "npm install -g @anthropic-ai/claude-code" in reply["detail"]["program_missing"]
    assert [d["verdict"] for d in reply["decisions"]] == ["dry_run"]
    assert _values(plan["argv"], "--tools") == [",".join((*READ_TOOLS, *WRITE_TOOLS))]
    assert reply["detail"]["not_held"] and "managed settings" in reply["warning_card"]["message"]
    assert "model provider" in reply["decisions"][0]["gate"]["not_evaluated"]


def test_a_write_run_needs_the_workspace_under_version_control(claude: StandIn, workspace: Path) -> None:
    for dry_run in (True, False):
        reply = invoke("run", {"goal": "x", "workspace": str(workspace), "writes": True, "dry_run": dry_run})
        assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.NOT_VERSIONED
    assert claude.calls() == []


def test_a_killed_write_run_says_files_may_be_half_changed(claude: StandIn, repo: Path) -> None:
    claude.behave(mode="hang")
    reply = live(repo, writes=True, deadline_s=3)
    assert reply["code"] == codes.DEADLINE and "half-changed" in reply["message"], reply["message"]


def test_a_secret_looking_workspace_is_held_for_a_person(claude: StandIn, tmp_path: Path) -> None:
    folder = tmp_path / ".ssh"
    folder.mkdir()
    reply = invoke("run", {"goal": "x", "workspace": str(folder), "dry_run": False})
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.GATE_BLOCKED
    assert claude.calls() == []


def test_with_model_calls_disabled_or_g6_stopped_claude_code_is_not_started(
        claude: StandIn, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    assert live(workspace)["code"] == "G6_E_LLM_DISABLED"
    monkeypatch.delenv("G6_DISABLE_LLM")
    core_stop.engage("test stop")
    try:
        assert live(workspace)["code"] == codes.STOPPED
        assert invoke("available", {})["code"] == codes.STOPPED
    finally:
        core_stop.release()
    assert claude.calls() == []


def test_available_reads_the_version_through_the_bounded_runner(claude: StandIn) -> None:
    reply = invoke("available", {})
    assert reply["completion_state"] == "qualified-draft" and "Claude Code stand-in" in reply["version"]
    assert claude.calls() == [["--version"]]


def test_turns_cost_and_refused_tools_are_reported_as_claude_code_reports_them(claude: StandIn,
                                                                               workspace: Path) -> None:
    claude.behave(denials=[{"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}}])
    reply = live(workspace, max_turns=7)
    assert _values(claude.record()["argv"], "--max-turns") == ["7"]
    assert reply["usage"]["tokens"] == 1540 and reply["usage"]["cost_usd"] == pytest.approx(0.0125)
    assert reply["detail"]["num_turns"] == 3 and reply["detail"]["permission_denials"] == ["Bash"]


def test_a_run_out_of_turns_is_blocked_with_the_step_limit(claude: StandIn, workspace: Path) -> None:
    claude.behave(is_error=True, subtype="error_max_turns", message="")
    reply = live(workspace, max_turns=2)
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == codes.STEP_LIMIT


def test_three_error_exits_in_a_row_open_the_circuit(claude: StandIn, workspace: Path) -> None:
    claude.behave(is_error=True, subtype="error_during_execution", exit=1, message="overloaded")
    for _ in range(3):
        assert live(workspace)["code"] == codes.EXIT_FAILED
    started = len(claude.calls())
    assert live(workspace)["code"] == codes.CIRCUIT_OPEN and len(claude.calls()) == started


@pytest.mark.parametrize("model", ["claude-opus-5-5; rm -rf /", "a b", "&calc", "--dangerously-skip-permissions"])
def test_a_model_name_that_could_be_read_as_a_command_is_refused(claude: StandIn, workspace: Path, model: str) -> None:
    result = get_registry().invoke("agent_claude", "run", {"goal": "x", "workspace": str(workspace), "model": model,
                                                           "dry_run": False})
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION]") and claude.calls() == []


def test_a_model_name_is_passed_as_its_own_argument(claude: StandIn, workspace: Path) -> None:
    live(workspace, model="claude-opus-5-5")
    assert _values(claude.record()["argv"], "--model") == ["claude-opus-5-5"]


# --- G3, G5: a claim, not an observation; sensitive ops -----------------------------------------------------------------

def test_g3_an_approval_or_a_wider_mode_cannot_be_written_into_the_input(claude: StandIn, workspace: Path) -> None:
    for extra in ({"approved": True}, {"permission_mode": "bypassPermissions"}, {"allowed_tools": ["Bash"]},
                  {"max_turns": 101}, {"deadline_s": 901}):
        result = get_registry().invoke("agent_claude", "run", {"goal": "x", "workspace": str(workspace), **extra})
        assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION]"), extra
    assert claude.calls() == []


@pytest.mark.parametrize("op", ["run", "available"])
def test_g5_every_op_that_starts_claude_code_is_refused_on_an_untrusted_surface(op: str) -> None:
    assert set(SENSITIVE_OPS) == {"run", "available"}
    result = get_registry().invoke("agent_claude", op, {}, block_sensitive=True)
    assert str(result.failure()).startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")
    reply = invoke_reply("agent_claude", op, "{}")
    assert not reply["ok"] and reply["completion_state"] == "blocked-escalated"


def test_the_skill_says_what_claude_code_can_still_do() -> None:
    text = (Path(agent_claude_pkg.__file__).parent / "skill" / "SKILL.md").read_text(encoding="utf-8")
    blind = " ".join(skills.section(text, "Blind spots").split())
    for words in ("read every file your account can read", "~/.g6/.env", "~/.ssh", "own permission system",
                  "hooks", "CLAUDE.md", "untried against a real Claude Code"):
        assert words in blind, words
    assert set(agent_claude_pkg.__g6_introduces__) == {"leaked_secret", "prompt_injection"}


def test_no_abbreviation_in_what_a_person_reads() -> None:
    for path in sorted(Path(agent_claude_pkg.__file__).parent.rglob("*")):
        if path.suffix in (".py", ".md"):
            found = re.findall(r"\b(CLI|SDK|API)\b", path.read_text(encoding="utf-8"))
            assert found == [], (path.name, found)


def test_the_claude_code_adapter_implements_the_one_runtime_interface() -> None:
    assert isinstance(ClaudeCodeRuntime(), AgentRuntime)
    assert invoke_reply("agent_claude", "ops", "{}")["result"]["ops"] == ["run", "available"]