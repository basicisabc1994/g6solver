# SPDX-License-Identifier: Apache-2.0
"""CodexRuntime: OpenAI's Codex, the command-line coding agent, as an adapter on the agents family's runtime
(``g6.agents._runtime``).

A Codex run is one online tool, ``codex_exec``, on one workspace: it reads the workspace, or writes it when the person
grants writes, and it sends what it reads to Codex's model under the person's own sign-in, so the task grants the
network permission. The runtime's gate decides it before anything starts (declared, permitted, the workspace as a
path ``g6.action_gating`` allows, within the trusted ceilings), and, being online, it is a dry run unless the person
runs with ``dry_run=False``. Then:

1. a write run needs the workspace under version control (a ``.git`` in it or in a folder above it), so the person
   can see and undo what Codex changed; otherwise it is refused (``G6_E_AGENT_NOT_VERSIONED``) before anything starts,
   and so is its dry run;
2. a dry run starts nothing and needs no Codex installed: ``detail.plan`` is the command line, folder and environment
   names a run would use, and ``detail.program_missing`` says how to install Codex when it is not found;
3. for a real run, with ``G6_DISABLE_LLM`` set it is refused (``G6_E_LLM_DISABLED``) and nothing starts; the program
   is found (``G6_CODEX``, or ``codex`` on PATH) and answers its version check, or the run is ``blocked-escalated``
   with ``G6_E_OUTSIDE_PROGRAM_MISSING`` and the install steps;
4. it is started by ``CliAgentRunner`` as ``codex exec --sandbox <read-only|workspace-write> --color never`` with
   four settings stated on every run (:data:`POSTURE`), ``--skip-git-repo-check`` only when it is read-only,
   ``--model`` only when one is named, and ``--output-last-message`` to a file in G6's scratch folder; the prompt (the
   goal, then the context) goes in on stdin; it starts in the workspace with a clean environment (its sign-in found
   through the home folder or ``CODEX_HOME``, no key passed), under the run's deadline (``gate.limits.wall_clock_s``:
   the task's, or a tighter time limit the person set), its process tree ended when it is over. A run ended early
   (the deadline, G6's stop, too much output) keeps what G6 knew in ``detail``, and a write run ended so says files
   in the workspace may be half-changed;
5. an exit other than 0 is ``blocked-escalated`` (``G6_E_AGENT_EXIT_FAILED``) with the end of its output; otherwise
   Codex's last message is the output and its first line the ``claimed_status``: a claim, never ``verified``.

Never passed: ``danger-full-access``, ``--dangerously-bypass-approvals-and-sandbox``, ``--approve-for-me``,
``--add-dir`` or any other flag that widens what Codex may do. What Codex does inside its run is enforced by Codex's own
sandbox, which differs by operating system; G6 states the posture and trusts Codex to keep it. That sandbox, read-only
included, reads every file the person can (``~/.g6/.env`` and ``~/.ssh`` among them), and Codex sends what it reads to
its model. Only the four settings of :data:`POSTURE` are G6's: the rest of the person's own Codex configuration (its MCP
servers, which run outside its sandbox, its profiles, web search) still applies. Overriding ``mcp_servers`` is untried
against a real Codex, so it is not passed.
"""
from __future__ import annotations

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
from g6.core import stop as core_stop
from g6.core.model_calls import note_model_call
from g6.core.outside import OutsideProgram
from g6.core.resources import ResourceUsage

#: The environment variable that names the codex program, when it is not ``codex`` on PATH.
CODEX_ENV = "G6_CODEX"
INSTALL_CODEX = ("Install OpenAI's Codex yourself (npm install -g @openai/codex, or as OpenAI's Codex documentation "
                 "describes), then sign in with codex login; or set G6_CODEX to the codex program. G6 never installs "
                 "it, never signs in for you and never passes it a key.")
CODEX = OutsideProgram(
    name="Codex", purpose="OpenAI's coding agent; run and available start the codex program you installed, signed in "
                          "with your account",
    install=INSTALL_CODEX, licence="Apache-2.0", command="codex", env=CODEX_ENV, paid=True, online=True)
TOOL = "codex_exec"
#: The code for a run refused because model calls are disabled (the one ``g6.core.llm`` uses).
LLM_DISABLED = "G6_E_LLM_DISABLED"
#: A model name Codex is given: letters, digits and ``. _ : / @ + -``, starting with a letter or digit, so it can never
#: be read as a flag or by a shell.
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")
#: The four settings stated on every run, so neither the person's nor a repository's Codex settings can widen them: no
#: approval prompt (no one is there to answer it), no network for the commands Codex runs, no folder writable beyond
#: the workspace and its own temporary folder, and not ``/tmp``. Nothing else of Codex's configuration is overridden.
POSTURE = ("-c", "approval_policy=never", "-c", "sandbox_workspace_write.network_access=false",
           "-c", "sandbox_workspace_write.writable_roots=[]", "-c", "sandbox_workspace_write.exclude_slash_tmp=true")
_TAIL = 2000
_HALF_CHANGED = (" Codex had leave to write in the workspace and its run did not end cleanly, so files there may be "
                 "half-changed: look at what changed (git status, git diff) before you go on.")



class CodexRuntime:
    """See the module docstring. ``model`` is the Codex model to ask for (Codex's own default when empty)."""

    name = "codex"

    def __init__(self, *, model: str = "", runner: CliAgentRunner | None = None) -> None:
        if model and not MODEL_NAME.fullmatch(model):
            raise ValueError(f"{model!r} is not a model name Codex can be given")
        self.model = model
        self.runner = runner or CliAgentRunner(CODEX, keep_env=("CODEX_HOME",))

    @staticmethod
    def task(goal: str, workspace: str, *, writes: bool = False, deadline_s: float = 600.0,
             context: str = "") -> AgentTask:
        """The task a Codex run is: the one online tool on ``workspace``, reading it, or writing it when ``writes``, with
        the network permission, because Codex sends what it reads to its model."""
        spec = ToolSpec(name=TOOL, effect="write" if writes else "read", online=True,
                        description="codex exec on the workspace, under Codex's sandbox, signed in as you")
        return AgentTask(goal=goal, context=context, tools=(spec,),
                         permissions=Permissions(workspace=workspace, read_files=True, write_files=writes, network=True),
                         limits=Limits(max_steps=1, max_tool_calls=1, wall_clock_s=deadline_s))

    def available(self) -> Availability:
        """Whether Codex answers its version check, run as every run is (clean environment, bounded, G6's stop
        polled); while G6 is stopped nothing is started."""
        try:
            why = core_stop.engaged()
        except Exception as exc:  # noqa: BLE001 - a stop that cannot be read counts as engaged
            why = f"the stop could not be read ({type(exc).__name__})"
        if why:
            return Availability(found=False, detail=f"G6 is stopped: {why}, so Codex's version check was not run",
                                code=codes.STOPPED)
        try:
            found = self.runner.require(stop=core_stop.engaged)
        except AgentRefusal as refusal:
            return Availability(found=False, detail=refusal.reason, code=refusal.code)
        return Availability(found=True, detail=found.detail, where=found.where, version=found.version)

    def argv(self, program: str, *, writes: bool, last_message: str) -> list[str]:
        """The command line of one run; the prompt is never on it."""
        args = [program, "exec", "--sandbox", "workspace-write" if writes else "read-only", "--color", "never",
                *POSTURE]
        if not writes:
            args.append("--skip-git-repo-check")   # a read-only run changes nothing, so it needs no version control
        if self.model:
            args += ["--model", self.model]
        return [*args, "--output-last-message", last_message, "-"]

    def run(self, task: AgentTask, *, gate: ToolGate, usage: ResourceUsage,
            stop: Callable[[], str | None]) -> Iterator[AgentEvent]:
        """One Codex run on ``task``; see the module docstring."""
        permissions = task.permissions
        if not permissions.workspace:
            raise AgentRefusal(codes.WORKSPACE, "Codex needs a workspace: the full path of the project folder it "
                                                "works in")
        writes = permissions.write_files
        workspace = Path(permissions.workspace).resolve()
        sandbox = "workspace-write" if writes else "read-only"
        detail: dict[str, Any] = {"workspace": str(workspace), "sandbox": sandbox, "writes": writes,
                                  "model": self.model or "Codex's default"}
        if writes and not versioned(workspace):  # before the gate, which holds a write it has no checkpoint for
            raise AgentRefusal(codes.NOT_VERSIONED, f"a write run needs the workspace under version control, so you can "
                                                    f"see and undo what Codex changes, and {workspace} is not: put it "
                                                    "under version control (git init, then a first commit), or run "
                                                    "read-only", detail=detail)
        call = ToolCall(tool=TOOL, paths=(str(workspace),),
                        arguments={"sandbox": sandbox, "model": self.model or "Codex's default"})
        decision = gate.decide(call)
        if decision.verdict == "refused":
            raise AgentRefusal(decision.code, decision.reason)
        prompt = task.goal + (f"\n\n{task.context}" if task.context else "")
        if decision.verdict == "dry_run":
            yield AgentEvent(kind="finished", payload={"output": "", "detail": self._plan(detail, writes, workspace)})
            return
        if os.environ.get("G6_DISABLE_LLM"):
            raise AgentRefusal(LLM_DISABLED, "model calls are disabled by G6_DISABLE_LLM, and Codex runs a model, so "
                                             "nothing was started", detail=detail)
        try:
            found = self.runner.require(stop=stop)
        except AgentRefusal as refusal:
            raise AgentRefusal(refusal.code, refusal.reason, detail={**detail, **refusal.detail},
                               backend_failure=refusal.backend_failure) from None
        detail |= {"program": found.where, "codex_version": found.version}
        yield AgentEvent(kind="step", payload={"sandbox": sandbox})
        note_model_call()  # Codex asks its model; counted before it starts, as g6.core.llm counts its own calls
        with self.runner.session() as scratch:
            last = scratch / "last_message.txt"
            try:
                argv = self.argv(found.where, writes=writes, last_message=self.runner.plain(last))
                detail["argv"] = argv
                done = self.runner.run(argv, prompt=prompt, cwd=workspace, scratch=scratch, stop=stop,
                                       seconds=gate.limits.wall_clock_s - usage.elapsed_seconds)
            except AgentRefusal as refusal:   # ended early: keep what G6 knew, and say what a write run may have left
                started = refusal.code != codes.START_FAILED
                raise AgentRefusal(refusal.code, refusal.reason + (_HALF_CHANGED if writes and started else ""),
                                   detail={**detail, **refusal.detail, "ended_early": started},
                                   backend_failure=refusal.backend_failure) from None
            message = last.read_text(encoding="utf-8", errors="replace").strip() if last.is_file() else ""
        detail |= {"argv": list(done.argv), "exit_code": done.exit_code, "env_names": list(done.env_names),
                   "elapsed_s": done.elapsed_s, "cleanup_confirmed": done.cleanup_confirmed,
                   "output_bytes": done.output_bytes, "output_tail": done.output_tail[-_TAIL:]}
        if done.exit_code != 0:
            said = " ".join(done.output_tail.split())[-300:]
            raise AgentRefusal(codes.EXIT_FAILED, f"codex exited {done.exit_code}: {said}", detail=detail,
                               backend_failure=True)
        yield AgentEvent(kind="tool_result", payload={"call_id": call.call_id, "exit_code": done.exit_code})
        output = message or done.output_tail.strip()
        claimed = next((line.strip() for line in output.splitlines() if line.strip()), "")[:200]
        yield AgentEvent(kind="finished", payload={"output": output, "claimed_status": claimed, "detail": detail})

    def _plan(self, detail: dict[str, Any], writes: bool, workspace: Path) -> dict[str, Any]:
        """What a dry run reports: the command line, folder and environment names a run would use. Locating the
        program is part of really running, so a Codex that is not found is said, not refused."""
        try:
            program, missing = self.runner.locate(), ""
        except AgentRefusal as refusal:
            program, missing = self.runner.program.command or "codex", refusal.reason
        names = sorted(set(clean_environment(self.runner.keep_env)) | {"TEMP", "TMP", "TMPDIR"})
        plan = {"argv": self.argv(program, writes=writes, last_message="<a file in G6's scratch folder>"),
                "cwd": str(workspace), "sandbox": detail["sandbox"], "env_names": names,
                "stdin": "the goal, then the context"}
        return {**detail, "program": program, **({"program_missing": missing} if missing else {}), "dry_run": True,
                "plan": plan}


__all__ = ["CODEX", "CODEX_ENV", "INSTALL_CODEX", "LLM_DISABLED", "MODEL_NAME", "POSTURE", "TOOL", "CodexRuntime",
           "versioned"]