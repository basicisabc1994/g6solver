# SPDX-License-Identifier: Apache-2.0
"""ClaudeCodeRuntime: Anthropic's Claude Code, the command-line coding agent, as an adapter on the agents family's
runtime (``g6.agents._runtime``).

A Claude Code run is one online tool, ``claude_code``, on one workspace: it reads the workspace, or writes it when the
person grants writes, and it sends what it reads to Anthropic's model under the person's own sign-in, so the task
grants the network permission. The runtime's gate decides it before anything starts (declared, permitted, the
workspace as a path ``g6.action_gating`` allows, within the trusted ceilings), and, being online, it is a dry run
unless the person runs with ``dry_run=False``. Then:

1. a write run needs the workspace under version control, so the person can see and undo what changed; otherwise it
   is refused (``G6_E_AGENT_NOT_VERSIONED``) before anything starts, and so is its dry run;
2. a dry run starts nothing and needs no Claude Code installed: ``detail.plan`` is the command line, folder and
   environment names a run would use, and ``detail.program_missing`` says how to install it when it is not found;
3. for a real run, with ``G6_DISABLE_LLM`` set it is refused (``G6_E_LLM_DISABLED``) and nothing starts; the program
   is found (``G6_CLAUDE``, or ``claude`` on PATH) and answers its version check, or the run is ``blocked-escalated``
   with ``G6_E_OUTSIDE_PROGRAM_MISSING`` and the install steps;
4. it is started by ``CliAgentRunner`` as ``claude -p --output-format json`` with its posture stated on every run
   (:meth:`ClaudeCodeRuntime.argv`): ``--permission-mode default``; ``--tools``, Claude Code's closed list of the
   built-in tools a session has, naming exactly the tools the run grants (its read tools, :data:`READ_TOOLS`, and its
   edit tools, :data:`WRITE_TOOLS`, only when writes are granted), so no other built-in tool is there at all;
   ``--allowedTools`` naming the same tools, so they run without a prompt; ``--disallowedTools`` naming every other
   tool G6 knows of (:data:`NEVER_TOOLS`: its shells, its background-shell and monitor tools, its web tools, its
   sub-agents and every MCP tool, which G6 never grants, and the edit tools on a read-only run), since Claude Code's
   deny rules win over any allow rule; ``--settings`` with a file of G6's that sets ``disableAllHooks``
   (:data:`HOOKS_OFF`), so the person's own hooks do not run; ``--setting-sources user``, so a repository's own Claude
   Code settings (its hooks, its environment, its allow rules) are not loaded; ``--strict-mcp-config`` with an empty
   ``--mcp-config``, so no MCP server is started; ``--max-turns``; ``--model`` only when one is named. What these
   flags cannot hold is said on every run (:data:`NOT_HELD`, ``detail.not_held``): hooks an administrator sets in
   managed settings still run, since ``disableAllHooks`` outside managed settings cannot turn them off. The prompt
   (the goal, then the
   context) goes in on stdin. It starts in the workspace with a clean environment (its sign-in found through the home
   folder or ``CLAUDE_CONFIG_DIR``; no key of G6's or of the environment is passed: Claude Code signs in on its own),
   under the run's deadline, its process tree ended when it is over. A run ended early keeps what G6 knew, and a write
   run ended so says files may be half-changed;
5. an exit other than 0 or an error result is ``blocked-escalated`` (``G6_E_AGENT_EXIT_FAILED``, or
   ``G6_E_AGENT_STEP_LIMIT`` when it ran out of turns) with what it said; a run that prints no result is
   ``G6_E_AGENT_NO_OUTCOME``; otherwise its result is the output and its first line the ``claimed_status``: a claim,
   never ``verified``. The turns, tokens and cost it reports are recorded as it reports them, and so are the tools its
   own permission system refused.

Never passed: ``--dangerously-skip-permissions``, ``--permission-mode bypassPermissions`` or ``acceptEdits``,
``--add-dir``, ``--permission-prompt-tool``, ``--continue`` or ``--resume``. What Claude Code does inside its run is
enforced by its own permission system; G6 states the posture and trusts Claude Code to keep it. Its read tools read
every file the person can, and it sends what it reads to its model. These flags are taken from Claude Code's
documentation and are untried against a real Claude Code.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from g6.agents._runtime import (
    AgentEvent,
    AgentRefusal,
    AgentTask,
    Availability,
    CliAgentRunner,
    Limits,
    Permissions,
    ToolCall,
    ToolGate,
    ToolSpec,
    clean_environment,
    codes,
    versioned,
)
from g6.agents._runtime.cli import OUTPUT_LOG
from g6.core import stop as core_stop
from g6.core.model_calls import note_model_call
from g6.core.outside import OutsideProgram
from g6.core.resources import ResourceUsage

#: The environment variable that names the claude program, when it is not ``claude`` on PATH.
CLAUDE_ENV = "G6_CLAUDE"
INSTALL_CLAUDE = ("Install Anthropic's Claude Code yourself (npm install -g @anthropic-ai/claude-code, or as "
                  "Anthropic's Claude Code documentation describes), then run claude once and sign in; or set "
                  "G6_CLAUDE to the claude program. G6 never installs it, never signs in for you and never passes it "
                  "a key. Its use is under Anthropic's terms.")
CLAUDE = OutsideProgram(
    name="Claude Code", purpose="Anthropic's coding agent; run and available start the claude program you installed, "
                                "signed in with your account",
    install=INSTALL_CLAUDE, licence="proprietary: Anthropic's terms (never bundled)", command="claude", env=CLAUDE_ENV,
    paid=True, online=True)
TOOL = "claude_code"
#: The code for a run refused because model calls are disabled (the one ``g6.core.llm`` uses).
LLM_DISABLED = "G6_E_LLM_DISABLED"
#: A model name Claude Code is given: it starts with a letter or digit, so it can never be read as a flag or by a shell.
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")
#: Claude Code's tools that read files; a run always has them.
READ_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep")
#: Its tools that change files; a run has them only when the person grants writes.
WRITE_TOOLS: tuple[str, ...] = ("Edit", "MultiEdit", "Write", "NotebookEdit")
#: Its tools G6 never grants: its shells (Bash, and PowerShell on Windows: either would run model-written commands with
#: your rights, with no sandbox G6 can state), its background-shell and monitor tools, its web tools, its sub-agents
#: (``Agent``, and ``Task``, its earlier name) and every MCP tool.
NEVER_TOOLS: tuple[str, ...] = ("Bash", "BashOutput", "KillShell", "PowerShell", "Monitor", "WebFetch", "WebSearch",
                                "Task", "Agent", "mcp__*")
#: Stated on every run, so neither the person's settings nor a repository's can widen them.
POSTURE = ("--permission-mode", "default", "--setting-sources", "user", "--strict-mcp-config")
NO_SERVERS = '{"mcpServers": {}}'
#: The settings G6 passes for the run (``--settings``, which wins over the person's and the project's): hooks off.
HOOKS_OFF = '{"disableAllHooks": true}'
#: What the flags cannot hold, said on every run.
NOT_HELD: tuple[str, ...] = (
    "Hooks set in managed settings (by an administrator) still run: G6's disableAllHooks turns off only your own and "
    "the project's hooks.",)
_TAIL = 2000
_HALF_CHANGED = (" Claude Code had leave to edit the workspace and its run did not end cleanly, so files there may be "
                 "half-changed: look at what changed (git status, git diff) before you go on.")


def _result(text: str) -> dict[str, Any] | None:
    """The result Claude Code printed last (``--output-format json``): the last line that is a JSON object of type
    ``result``, whatever it printed before it."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            found = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(found, dict) and found.get("type") == "result":
            return found
    return None


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class ClaudeCodeRuntime:
    """See the module docstring. ``model`` is the Claude model to ask for (Claude Code's own choice when empty);
    ``max_turns`` bounds its agentic turns."""

    name = "claude_code"

    def __init__(self, *, model: str = "", max_turns: int = 20, runner: CliAgentRunner | None = None) -> None:
        if model and not MODEL_NAME.fullmatch(model):
            raise ValueError(f"{model!r} is not a model name Claude Code can be given")
        if isinstance(max_turns, bool) or not 1 <= int(max_turns) <= 100:
            raise ValueError("max_turns is 1 to 100")
        self.model, self.max_turns = model, int(max_turns)
        self.runner = runner or CliAgentRunner(CLAUDE, keep_env=("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_GIT_BASH_PATH"))

    @staticmethod
    def task(goal: str, workspace: str, *, writes: bool = False, deadline_s: float = 600.0,
             context: str = "") -> AgentTask:
        """The task a Claude Code run is: the one online tool on ``workspace``, reading it, or writing it when
        ``writes``, with the network permission, because Claude Code sends what it reads to its model."""
        spec = ToolSpec(name=TOOL, effect="write" if writes else "read", online=True,
                        description="claude -p on the workspace, under Claude Code's permission system, signed in as you")
        return AgentTask(goal=goal, context=context, tools=(spec,),
                         permissions=Permissions(workspace=workspace, read_files=True, write_files=writes, network=True),
                         limits=Limits(max_steps=1, max_tool_calls=1, wall_clock_s=deadline_s))

    @staticmethod
    def tools(writes: bool) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The tools a run allows, and the ones it denies."""
        allowed = (*READ_TOOLS, *WRITE_TOOLS) if writes else READ_TOOLS
        return allowed, (*NEVER_TOOLS, *(() if writes else WRITE_TOOLS))

    def available(self) -> Availability:
        """Whether Claude Code answers its version check, run as every run is (clean environment, bounded, G6's stop
        polled); while G6 is stopped nothing is started."""
        try:
            why = core_stop.engaged()
        except Exception as exc:  # noqa: BLE001 - a stop that cannot be read counts as engaged
            why = f"the stop could not be read ({type(exc).__name__})"
        if why:
            return Availability(found=False, detail=f"G6 is stopped: {why}, so Claude Code's version check was not run",
                                code=codes.STOPPED)
        try:
            found = self.runner.require(stop=core_stop.engaged)
        except AgentRefusal as refusal:
            return Availability(found=False, detail=refusal.reason, code=refusal.code)
        return Availability(found=True, detail=found.detail, where=found.where, version=found.version)

    def argv(self, program: str, *, writes: bool, mcp_config: str, settings: str) -> list[str]:
        """The command line of one run; the prompt is never on it. ``settings`` is the file holding
        :data:`HOOKS_OFF`."""
        allowed, denied = self.tools(writes)
        args = [program, "-p", "--output-format", "json", *POSTURE, "--mcp-config", mcp_config, "--settings", settings,
                "--tools", ",".join(allowed), "--allowedTools", *allowed, "--disallowedTools", *denied,
                "--max-turns", str(self.max_turns)]
        if self.model:
            args += ["--model", self.model]
        return args

    def run(self, task: AgentTask, *, gate: ToolGate, usage: ResourceUsage,
            stop: Callable[[], str | None]) -> Iterator[AgentEvent]:
        """One Claude Code run on ``task``; see the module docstring."""
        permissions = task.permissions
        if not permissions.workspace:
            raise AgentRefusal(codes.WORKSPACE, "Claude Code needs a workspace: the full path of the project folder it "
                                                "works in")
        writes = permissions.write_files
        workspace = Path(permissions.workspace).resolve()
        allowed, denied = self.tools(writes)
        detail: dict[str, Any] = {"workspace": str(workspace), "writes": writes, "allowed_tools": list(allowed),
                                  "disallowed_tools": list(denied), "max_turns": self.max_turns,
                                  "model": self.model or "Claude Code's default", "hooks": "off (disableAllHooks)",
                                  "not_held": list(NOT_HELD)}
        if writes and not versioned(workspace):  # before the gate, which holds a write it has no checkpoint for
            raise AgentRefusal(codes.NOT_VERSIONED, f"a write run needs the workspace under version control, so you can "
                                                    f"see and undo what Claude Code changes, and {workspace} is not: "
                                                    "put it under version control (git init, then a first commit), or "
                                                    "run read-only", detail=detail)
        call = ToolCall(tool=TOOL, paths=(str(workspace),),
                        arguments={"allowed_tools": list(allowed), "model": self.model or "Claude Code's default"})
        decision = gate.decide(call)
        if decision.verdict == "refused":
            raise AgentRefusal(decision.code, decision.reason)
        prompt = task.goal + (f"\n\n{task.context}" if task.context else "")
        if decision.verdict == "dry_run":
            yield AgentEvent(kind="finished", payload={"output": "", "detail": self._plan(detail, writes, workspace)})
            return
        if os.environ.get("G6_DISABLE_LLM"):
            raise AgentRefusal(LLM_DISABLED, "model calls are disabled by G6_DISABLE_LLM, and Claude Code runs a model, "
                                             "so nothing was started", detail=detail)
        try:
            found = self.runner.require(stop=stop)
        except AgentRefusal as refusal:
            raise AgentRefusal(refusal.code, refusal.reason, detail={**detail, **refusal.detail},
                               backend_failure=refusal.backend_failure) from None
        detail |= {"program": found.where, "claude_code_version": found.version}
        yield AgentEvent(kind="step", payload={"writes": writes})
        note_model_call()  # Claude Code asks its model; counted before it starts, as g6.core.llm counts its own calls
        with self.runner.session() as scratch:
            servers = scratch / "no_mcp_servers.json"
            servers.write_text(NO_SERVERS, encoding="utf-8")
            hooks = scratch / "g6_settings.json"
            hooks.write_text(HOOKS_OFF, encoding="utf-8")
            try:
                argv = self.argv(found.where, writes=writes, mcp_config=self.runner.plain(servers),
                                 settings=self.runner.plain(hooks))
                detail["argv"] = argv
                done = self.runner.run(argv, prompt=prompt, cwd=workspace, scratch=scratch, stop=stop,
                                       seconds=gate.limits.wall_clock_s - usage.elapsed_seconds)
            except AgentRefusal as refusal:   # ended early: keep what G6 knew, and say what a write run may have left
                started = refusal.code != codes.START_FAILED
                raise AgentRefusal(refusal.code, refusal.reason + (_HALF_CHANGED if writes and started else ""),
                                   detail={**detail, **refusal.detail, "ended_early": started},
                                   backend_failure=refusal.backend_failure) from None
            printed = (scratch / OUTPUT_LOG).read_text(encoding="utf-8", errors="replace")
        detail |= {"exit_code": done.exit_code, "env_names": list(done.env_names), "elapsed_s": done.elapsed_s,
                   "cleanup_confirmed": done.cleanup_confirmed, "output_bytes": done.output_bytes,
                   "output_tail": done.output_tail[-_TAIL:]}
        result = _result(printed)
        said = " ".join(str((result or {}).get("result") or done.output_tail).split())[-300:]
        if result is not None:
            self._observe(result, usage, detail)
        if done.exit_code != 0 or (result is not None and result.get("is_error")):
            if result is not None and result.get("subtype") == "error_max_turns":
                raise AgentRefusal(codes.STEP_LIMIT, f"Claude Code used its {self.max_turns} turns without finishing",
                                   detail=detail)
            raise AgentRefusal(codes.EXIT_FAILED, f"claude exited {done.exit_code}"
                                                  f"{' with an error result' if result else ''}: {said}",
                               detail=detail, backend_failure=True)
        if result is None:
            raise AgentRefusal(codes.NO_OUTCOME, "Claude Code exited without the result G6 asked it to print "
                                                 "(--output-format json), so what it did is not known",
                               detail=detail, backend_failure=True)
        yield AgentEvent(kind="tool_result", payload={"call_id": call.call_id, "exit_code": done.exit_code})
        output = str(result.get("result") or "").strip()
        claimed = next((line.strip() for line in output.splitlines() if line.strip()), "")[:200]
        yield AgentEvent(kind="finished", payload={"output": output, "claimed_status": claimed, "detail": detail})

    @staticmethod
    def _observe(result: dict[str, Any], usage: ResourceUsage, detail: dict[str, Any]) -> None:
        """Record the turns, tokens, cost and refused tools Claude Code reports, as it reports them."""
        reported = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        tokens = _count(reported.get("input_tokens")) + _count(reported.get("output_tokens"))
        if tokens:
            usage.record_tokens(tokens)
        cost = result.get("total_cost_usd")
        cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0 else None
        if cost is not None:
            usage.record_cost(cost)
        denials = result.get("permission_denials") if isinstance(result.get("permission_denials"), list) else []
        detail |= {"num_turns": _count(result.get("num_turns")), "subtype": str(result.get("subtype") or ""),
                   "reported_cost_usd": cost,
                   "permission_denials": [str(d.get("tool_name", "")) if isinstance(d, dict) else str(d)
                                          for d in denials][:50]}

    def _plan(self, detail: dict[str, Any], writes: bool, workspace: Path) -> dict[str, Any]:
        """What a dry run reports: the command line, folder and environment names a run would use. Locating the
        program is part of really running, so a Claude Code that is not found is said, not refused."""
        try:
            program, missing = self.runner.locate(), ""
        except AgentRefusal as refusal:
            program, missing = self.runner.program.command or "claude", refusal.reason
        names = sorted(set(clean_environment(self.runner.keep_env)) | {"TEMP", "TMP", "TMPDIR"})
        plan = {"argv": self.argv(program, writes=writes, mcp_config="<an empty server list in G6's scratch folder>",
                                  settings="<G6's settings in its scratch folder: hooks off>"),
                "cwd": str(workspace), "env_names": names, "stdin": "the goal, then the context"}
        return {**detail, "program": program, **({"program_missing": missing} if missing else {}), "dry_run": True,
                "plan": plan}


__all__ = ["CLAUDE", "CLAUDE_ENV", "HOOKS_OFF", "INSTALL_CLAUDE", "LLM_DISABLED", "MODEL_NAME", "NEVER_TOOLS",
           "NOT_HELD", "POSTURE", "READ_TOOLS", "TOOL", "WRITE_TOOLS", "ClaudeCodeRuntime"]