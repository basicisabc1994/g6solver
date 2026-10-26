# SPDX-License-Identifier: Apache-2.0
"""g6.core.host_models: the person's own Claude Code or Codex sign-in as G6's model. One text completion, no tools,
headless; the line ``G6_LLM_PROVIDER=claude_code`` (or ``codex``) in the person's key file is their approval, and the
same line only in the environment starts nothing. Each call runs in an empty folder of its own, with a clean
environment (no key, no Claude Code or Codex setting of the session that runs G6), the prompt on stdin or in a file,
under a deadline with its whole process tree ended, and its reply capped. The tests never run the real programs: each
writes a stand-in and points ``G6_CLAUDE`` / ``G6_CODEX`` at it.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from g6.core import host_models, llm

STAND_IN = r'''
import json, os, subprocess, sys, time
from pathlib import Path

here = Path(__file__).resolve().parent
args = sys.argv[1:]
plan = json.loads((here / "behaviour.json").read_text(encoding="utf-8"))
listing = sorted(os.listdir("."))
prompt = sys.stdin.read()

def text_after(flag):
    return Path(args[args.index(flag) + 1]).read_text(encoding="utf-8") if flag in args else None

(here / "record.json").write_text(json.dumps({
    "argv": args, "cwd": os.getcwd(), "cwd_listing": listing, "stdin": prompt, "env": dict(os.environ),
    "system_prompt": text_after("--system-prompt-file"), "mcp_config": text_after("--mcp-config"),
    "settings": text_after("--settings")}), encoding="utf-8")
mode = plan.get("mode", "ok")
if mode == "hang":
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
if mode == "flood":
    for _ in range(150):
        sys.stdout.write("x" * 65536)
    sys.stdout.flush()
    sys.exit(0)
if mode == "fail":
    print("error: the model refused this prompt: " + prompt, file=sys.stderr, flush=True)
    print("this program printed its end before exiting", flush=True)
    sys.exit(3)
reply = plan.get("reply", "ready")
if "--output-last-message" in args:
    if not plan.get("no_message"):
        Path(args[args.index("--output-last-message") + 1]).write_text(reply, encoding="utf-8")
    print("codex: a line of progress", flush=True)
elif not plan.get("no_result"):
    print(json.dumps({"type": "result", "subtype": "success", "is_error": plan.get("is_error", False),
                      "result": reply}), flush=True)
sys.exit(plan.get("exit", 0))
'''

SYSTEM, USER = "SYSTEM-TEXT-Z7: answer in one word.", "USER-TEXT-Q9: say ready."
#: Set in the environment of the process that runs G6 (a coding agent's own session); none may reach the program.
FORBIDDEN = {"ANTHROPIC_API_KEY": "canary-anthropic", "ANTHROPIC_AUTH_TOKEN": "canary-token",
             "ANTHROPIC_BASE_URL": "http://canary.invalid", "OPENAI_API_KEY": "canary-openai",
             "CODEX_API_KEY": "canary-codex", "CLAUDECODE": "canary-1", "CLAUDE_CODE_ENTRYPOINT": "canary-entry",
             "CLAUDE_CODE_OAUTH_TOKEN": "canary-oauth"}


class StandIn:
    """A stand-in for claude and codex: what it was told to do, and what it saw when it was started."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def behave(self, **plan: object) -> None:
        (self.folder / "behaviour.json").write_text(json.dumps(plan), encoding="utf-8")

    def started(self) -> bool:
        return (self.folder / "record.json").exists()

    def record(self) -> dict:
        return json.loads((self.folder / "record.json").read_text(encoding="utf-8"))


@pytest.fixture
def programs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StandIn:
    folder = tmp_path / "bin"
    folder.mkdir()
    script = folder / "stand_in.py"
    script.write_text(STAND_IN, encoding="utf-8")
    for name, variable in (("claude", "G6_CLAUDE"), ("codex", "G6_CODEX")):
        if os.name == "nt":
            launcher = folder / f"{name}.cmd"
            launcher.write_text(f'@echo off\r\n"{sys.executable}" -I "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = folder / name
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{script}" "$@"\n', encoding="utf-8")
            launcher.chmod(0o755)
        monkeypatch.setenv(variable, str(launcher))
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    stand_in = StandIn(folder)
    stand_in.behave()
    return stand_in


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _pairs(argv: list[str], flag: str) -> list[str]:
    """The value after each time ``flag`` is given."""
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == flag]


def _values(argv: list[str], flag: str) -> list[str]:
    """The values after ``flag`` up to the next flag (a flag that takes a list)."""
    out = []
    for arg in argv[argv.index(flag) + 1:]:
        if arg.startswith("-"):
            break
        out.append(arg)
    return out


def _assert_clean_environment(seen: dict, home: Path) -> None:
    env = seen["env"]
    leaked = sorted(name for name, value in env.items()
                    if "canary" in value or name.upper() in FORBIDDEN or name.upper().startswith(("CLAUDE_CODE_", "G6_")))
    assert leaked == []
    # PROMPT: cmd.exe, which runs the stand-in's .cmd launcher on Windows, sets it itself; PWD: /bin/sh, which runs it
    # elsewhere, sets it itself
    allowed = {n.upper() for n in (*host_models.BASE_ENV, "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TEMP", "TMP", "TMPDIR",
                                   "PROMPT", "PWD")}
    assert sorted(n for n in env if n.upper() not in allowed and n in os.environ) == []
    assert Path(env["TEMP"]).resolve().is_relative_to(home.resolve())


def _scratch(seen: dict, home: Path) -> Path:
    cwd = Path(seen["cwd"]).resolve()
    assert cwd.is_relative_to((home / "host_models").resolve()), cwd
    assert seen["cwd_listing"] == []   # an empty folder: no CLAUDE.md, AGENTS.md or repository there
    return cwd


def _approve(key_file, provider: str, extra: str = "") -> None:
    # the person's own key file, with a key in it that must never reach the program
    key_file(f"G6_LLM_PROVIDER={provider}\nANTHROPIC_API_KEY=canary-file-key\nOPENAI_API_KEY=canary-file-key2\n{extra}")


# --- claude_code -----------------------------------------------------------------------------------------------------

def test_claude_code_runs_with_no_tool_no_server_no_hooks_and_the_prompt_off_the_command_line(
        programs: StandIn, key_file, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in FORBIDDEN.items():
        monkeypatch.setenv(name, value)
    _approve(key_file, "claude_code")
    caller = llm.provider_from_env()
    assert caller.complete(SYSTEM, USER) == "ready"
    seen = programs.record()
    argv = seen["argv"]
    assert argv[0] == "-p" and _after(argv, "--output-format") == "json"
    assert _after(argv, "--tools") == ""                       # no built-in tool at all
    denied = set(_values(argv, "--disallowedTools"))
    assert {"Bash", "PowerShell", "Read", "Glob", "Grep", "Edit", "Write", "WebFetch", "WebSearch", "Task", "Agent",
            "mcp__*"} <= denied
    assert "--strict-mcp-config" in argv and json.loads(seen["mcp_config"]) == {"mcpServers": {}}
    assert json.loads(seen["settings"]) == {"disableAllHooks": True}
    assert _after(argv, "--setting-sources") == "user" and _after(argv, "--max-turns") == "1"
    assert "--no-session-persistence" in argv and "--model" not in argv
    assert seen["system_prompt"] == SYSTEM and seen["stdin"] == USER   # the system text is the whole system prompt
    line = " ".join(argv)
    assert "SYSTEM-TEXT" not in line and "USER-TEXT" not in line
    assert not [a for a in argv if "danger" in a or "bypass" in a or a in ("--add-dir", "--continue", "--resume")]
    cwd = _scratch(seen, key_file.path.parent)
    _assert_clean_environment(seen, key_file.path.parent)
    assert not cwd.exists()   # removed afterwards
    # Removing any posture flag from ClaudeCodeCaller.argv, writing G6's files into the work folder in _command, or
    # passing os.environ in place of clean_environment(keep) in _environment makes this fail.


def test_the_names_never_passed_are_left_out_even_when_a_caller_asks_to_keep_them(tmp_path: Path,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in FORBIDDEN.items():
        monkeypatch.setenv(name, value)
    env = host_models._environment(tuple(FORBIDDEN), tmp_path)
    assert not set(env) & set(FORBIDDEN) and env["TEMP"] == str(tmp_path)
    # Removing the _never filter in _environment makes this fail.


def test_claude_code_with_a_model_named_passes_it_as_its_own_argument(programs: StandIn, key_file) -> None:
    _approve(key_file, "claude_code", "G6_LLM_MODEL=sonnet\n")
    llm.provider_from_env().complete(SYSTEM, USER)
    assert _after(programs.record()["argv"], "--model") == "sonnet"
    _approve(key_file, "claude_code", "G6_LLM_MODEL=--dangerously-skip-permissions\n")
    with pytest.raises(llm.LLMError, match="G6_E_LLM_BAD_MODEL"):
        llm.provider_from_env()
    # Removing the MODEL_NAME check in _HostCaller.__init__ makes the second half fail.


def test_claude_code_an_error_result_or_no_result_is_an_llm_error(programs: StandIn, key_file) -> None:
    _approve(key_file, "claude_code")
    programs.behave(is_error=True, reply="Credit balance is too low")
    with pytest.raises(llm.LLMError, match="G6_E_LLM_HOST_FAILED") as caught:
        llm.provider_from_env().complete(SYSTEM, USER)
    assert "USER-TEXT" not in str(caught.value) and "SYSTEM-TEXT" not in str(caught.value)
    programs.behave(no_result=True)
    with pytest.raises(llm.LLMError, match="G6_E_LLM_HOST_FAILED"):
        llm.provider_from_env().complete(SYSTEM, USER)
    # Removing the is_error / missing-result check in ClaudeCodeCaller._reply makes this fail.


# --- codex -----------------------------------------------------------------------------------------------------------

def test_codex_runs_read_only_ephemeral_without_its_shell_and_the_prompt_on_stdin(
        programs: StandIn, key_file, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in FORBIDDEN.items():
        monkeypatch.setenv(name, value)
    _approve(key_file, "codex")
    programs.behave(reply="ready from codex")
    assert llm.provider_from_env().complete(SYSTEM, USER) == "ready from codex"
    seen = programs.record()
    argv = seen["argv"]
    assert argv[0] == "exec" and argv[-1] == "-"
    assert _after(argv, "--sandbox") == "read-only" and _after(argv, "--color") == "never"
    assert {"--skip-git-repo-check", "--ephemeral", "--ignore-user-config"} <= set(argv)
    assert "approval_policy=never" in _pairs(argv, "-c")
    assert {"shell_tool", "unified_exec"} <= set(_pairs(argv, "--disable"))   # its shell turned off
    assert "--output-last-message" in argv and "--model" not in argv and "-m" not in argv
    cwd = _scratch(seen, key_file.path.parent)
    assert Path(_after(argv, "-C")).resolve() == cwd
    assert SYSTEM in seen["stdin"] and USER in seen["stdin"] and seen["stdin"].index(SYSTEM) < seen["stdin"].index(USER)
    line = " ".join(argv)
    assert "SYSTEM-TEXT" not in line and "USER-TEXT" not in line
    assert not [a for a in argv if "danger" in a or "full-access" in a or a in ("--add-dir", "--approve-for-me")]
    _assert_clean_environment(seen, key_file.path.parent)
    assert not cwd.exists()
    # Removing any posture flag from CodexCaller.argv (the --disable pair among them) makes this fail.


def test_codex_without_its_last_message_is_an_llm_error(programs: StandIn, key_file) -> None:
    _approve(key_file, "codex")
    programs.behave(no_message=True)
    with pytest.raises(llm.LLMError, match="G6_E_LLM_HOST_FAILED"):
        llm.provider_from_env().complete(SYSTEM, USER)
    # Removing the missing-file check in CodexCaller._reply makes this fail.


# --- both: exits, size, deadline, kill switch, approval --------------------------------------------------------------

@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_a_non_zero_exit_is_an_llm_error_with_the_end_of_its_output_and_no_prompt_or_key(
        programs: StandIn, key_file, provider: str) -> None:
    _approve(key_file, provider)
    programs.behave(mode="fail")
    with pytest.raises(llm.LLMError, match="G6_E_LLM_HOST_FAILED") as caught:
        llm.provider_from_env().complete(SYSTEM, USER)
    said = str(caught.value)
    assert "exited 3" in said and "printed its end before exiting" in said and "the model refused" in said
    assert "USER-TEXT" not in said and "SYSTEM-TEXT" not in said and "canary" not in said
    # Removing _tail's redaction of the prompt's lines makes this fail (the stand-in prints the prompt back).


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_a_reply_past_the_cap_is_refused(programs: StandIn, key_file, provider: str) -> None:
    _approve(key_file, provider)
    programs.behave(mode="flood")   # about 9.8 MB, past MAX_REPLY_BYTES
    with pytest.raises(llm.LLMError, match=llm.REPLY_TOO_LARGE):
        llm.provider_from_env().complete(SYSTEM, USER)
    # Removing both MAX_REPLY_BYTES checks in _run (the stop while it prints, and the one after it exits) makes this
    # fail.


@pytest.mark.parametrize("caller_type", [host_models.ClaudeCodeCaller, host_models.CodexCaller])
def test_a_program_that_hangs_is_stopped_at_its_deadline_with_its_whole_tree(programs: StandIn, caller_type) -> None:
    programs.behave(mode="hang")
    started = time.monotonic()
    with pytest.raises(llm.LLMError, match=llm.REPLY_TIMEOUT):
        caller_type(timeout=3).complete(SYSTEM, USER)
    assert time.monotonic() - started < 30
    assert (programs.folder / "grandchild.txt").exists()
    beat = programs.folder / "heartbeat.txt"
    size = beat.stat().st_size
    time.sleep(1.0)
    assert beat.stat().st_size == size, "the grandchild outlived the deadline"
    # Giving component_process.window in _run anything longer than the caller's timeout makes this fail.


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_with_model_calls_disabled_nothing_starts(programs: StandIn, key_file, monkeypatch: pytest.MonkeyPatch,
                                                  provider: str) -> None:
    _approve(key_file, provider)
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    with pytest.raises(llm.LLMError, match="G6_E_LLM_DISABLED"):
        llm.provider_from_env().complete(SYSTEM, USER)
    assert not programs.started()
    # Removing _start_model_call() from _HostCaller.complete makes this fail.


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_named_only_in_the_environment_nothing_starts_and_the_key_file_is_named(
        programs: StandIn, key_file, monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    key_file("ANTHROPIC_API_KEY=canary-file-key\n")
    monkeypatch.setenv("G6_LLM_PROVIDER", provider)
    for caller in (llm.provider_from_env(), llm.provider_from_env({"G6_LLM_PROVIDER": provider})):
        with pytest.raises(llm.LLMError, match="G6_E_NO_MODEL") as caught:
            caller.complete(SYSTEM, USER)
        assert str(key_file.path) in str(caught.value) and f"G6_LLM_PROVIDER={provider}" in str(caught.value)
    assert not programs.started()
    # Removing the key-file approval check in provider_from_env makes this fail (the stand-in would start).


def test_the_key_file_setting_builds_the_caller_and_python_code_may_name_one_itself(key_file) -> None:
    key_file("G6_LLM_PROVIDER=claude_code\n")
    caller = llm.provider_from_env()
    assert isinstance(caller, llm.EmptyRetry) and isinstance(caller._inner, host_models.ClaudeCodeCaller)
    key_file("")
    chosen = llm.provider_from_env({}, provider="codex")
    assert isinstance(chosen, llm.EmptyRetry) and isinstance(chosen._inner, host_models.CodexCaller)
    assert not llm.supports_images(caller) and not llm.supports_images(chosen)
    assert llm.HOST_PROVIDERS == ("claude_code", "codex")
    assert llm.PROVIDERS == ("ollama", "anthropic", "openai", "openrouter")   # the router's table is unchanged
    # Removing the HOST_PROVIDERS branch of provider_from_env makes this fail (unknown G6_LLM_PROVIDER).


@pytest.mark.parametrize("caller_type", [host_models.ClaudeCodeCaller, host_models.CodexCaller])
def test_a_missing_program_names_the_install_steps_and_nothing_starts(
        programs: StandIn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller_type) -> None:
    monkeypatch.setenv(caller_type.program.env, str(tmp_path / "not-there" / "program.exe"))
    with pytest.raises(llm.LLMError, match="G6_E_OUTSIDE_PROGRAM_MISSING") as caught:
        caller_type().complete(SYSTEM, USER)
    assert "install it yourself" in str(caught.value) and "never" in str(caught.value)
    assert not programs.started()
    # Removing the locate() refusal in _HostCaller.complete makes this fail.
