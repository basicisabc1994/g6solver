# SPDX-License-Identifier: Apache-2.0
"""The types every agent adapter shares.

What a person lets an agent do: :class:`Permissions` (a workspace, and the kinds of access granted in it),
:class:`Limits` (steps, tool calls, wall-clock time; the trusted ``g6.safety_engine.RuntimePolicy`` caps each one, see
:func:`effective_limits`) and a :class:`ToolSpec` for each tool it may call, together an :class:`AgentTask`. What the
agent proposes: a :class:`ToolCall`; what the gate decided about it: a :class:`ToolDecision`. What an adapter reports as
it runs: an :class:`AgentEvent`; how the run ended: an :class:`AgentOutcome`.

Every model forbids fields it does not name, so an approval, a reviewer or a risk level cannot be written into a task
or a call. An outcome is ``qualified-draft`` at best: :class:`AgentOutcome` has no other success state, so no adapter
can return ``verified``, and what the agent says about its own work ("done", "tests passed") is kept in
``claimed_status``, where it decides nothing.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

import g6
from g6.agents._runtime import codes
from g6.core.reliability_envelope import WarningCard
from g6.core.storage import g6_home
from g6.safety_engine.schema import RuntimePolicy

Effect = Literal["none", "read", "write", "delete", "shell", "network", "send", "pay"]
#: The permission each effect needs (``none`` needs none).
NEEDS: dict[str, str] = {"read": "read_files", "write": "write_files", "delete": "write_files", "shell": "shell",
                         "network": "network", "send": "network", "pay": "network"}
#: Effects on files: a call to such a tool names the files it touches, and they must be inside the workspace.
FILE_EFFECTS = frozenset({"read", "write", "delete"})
#: Effects that act on the world: each call is an outward action, a dry run unless the person says otherwise.
OUTWARD_EFFECTS = frozenset({"write", "delete", "shell", "network", "send", "pay"})
#: What each effect does, in words.
DOES: dict[str, str] = {"none": "computes an answer", "read": "reads files", "write": "writes files",
                        "delete": "deletes files", "shell": "runs shell commands", "network": "reaches the network",
                        "send": "sends messages", "pay": "makes payments"}
_CODE = re.compile(r"G6_E_[A-Z0-9_]+")


class Permissions(BaseModel):
    """What the person lets the agent touch. Nothing is granted unless it is set here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace: str | None = Field(None, min_length=1, max_length=1000)  # the folder the agent works in, by full path
    read_files: bool = False   # inside the workspace only
    write_files: bool = False  # inside the workspace only
    shell: bool = False
    network: bool = False

    @model_validator(mode="after")
    def _files_need_a_workspace(self) -> Permissions:
        if (self.read_files or self.write_files or self.shell) and not self.workspace:
            raise ValueError("reading or writing files, or a shell, needs a workspace: the folder the agent works in")
        return self


class Limits(BaseModel):
    """The caller's limits for one run. The trusted policy's ceilings cap each one (:func:`effective_limits`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(20, ge=1, le=200)
    max_tool_calls: int = Field(20, ge=0, le=500)
    wall_clock_s: float = Field(600.0, gt=0, le=7200, allow_inf_nan=False)


def effective_limits(limits: Limits, policy: RuntimePolicy) -> Limits:
    """``limits`` capped by the trusted ``policy``: the caller can lower a ceiling, never raise it."""
    return Limits.model_construct(max_steps=min(limits.max_steps, max(0, int(policy.max_steps))),
                                  max_tool_calls=min(limits.max_tool_calls, max(0, int(policy.max_tool_calls))),
                                  wall_clock_s=min(limits.wall_clock_s, max(0.0, float(policy.max_runtime_seconds))))


class ToolSpec(BaseModel):
    """One tool the agent may call, and what it does. ``online``: it sends what it is given to a service off this
    machine (for a command-line agent, its own model), so calling it is an outward action whatever its effect."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
    effect: Effect
    online: bool = False
    description: str = Field("", max_length=500)

    @property
    def outward(self) -> bool:
        """A call acts on the world: a dry run unless the person says otherwise."""
        return self.effect in OUTWARD_EFFECTS or self.online

    @property
    def gated(self) -> bool:
        """``g6.action_gating`` evaluates each call (every effect but ``none``, and anything online)."""
        return self.effect != "none" or self.online

    @property
    def needs_network(self) -> bool:
        """A call needs the ``network`` permission: the tool is online, or its effect reaches off this machine."""
        return self.online or NEEDS.get(self.effect) == "network"


class AgentTask(BaseModel):
    """The work, and everything the agent may use for it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(min_length=1, max_length=100_000)
    context: str = Field("", max_length=200_000)
    tools: tuple[ToolSpec, ...] = Field((), max_length=100)
    permissions: Permissions = Field(default_factory=Permissions)
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def _one_spec_per_tool(self) -> AgentTask:
        names = [t.name for t in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("two of the task's tools share a name")
        return self


def _real(path: Path) -> Path | None:
    """``path`` with ``..`` and symbolic links resolved, or ``None`` when it cannot be."""
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _homes(names: Mapping[str, str]) -> list[Path]:
    """Every folder that may be the person's home: from the environment, and on POSIX from the account itself."""
    found = [names.get("USERPROFILE"), names.get("HOME")]
    if names.get("HOMEDRIVE") and names.get("HOMEPATH"):
        found.append(names["HOMEDRIVE"] + names["HOMEPATH"])
    if os.name != "nt":
        try:
            import pwd

            found.append(pwd.getpwuid(os.getuid()).pw_dir)
        except (ImportError, KeyError, OSError):
            pass
    return [p for p in (_real(Path(h)) for h in dict.fromkeys(found) if h and Path(h).is_absolute()) if p is not None]


def workspace_problem(permissions: Permissions, *, environ: Mapping[str, str] | None = None) -> str:
    """Why the declared workspace cannot be used, in words; ``""`` when it can, or when none is declared. It must be
    an existing folder named by its full path, and not a drive's root, the home folder or a folder that holds it, nor
    a folder that is, holds or sits inside G6's own state folder (``$G6_HOME``: its key file, the action gate's audit
    trail, the stop) or G6's installed code. Paths are compared after ``..`` and symbolic links are resolved, and
    without regard to letter case where the system ignores it. The folder the server runs in is not refused for being
    that: an MCP client usually starts G6 in the person's project."""
    raw = permissions.workspace
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        return f"the workspace {raw!r} is not a full path"
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return f"the workspace {raw!r} does not exist"
    if not resolved.is_dir():
        return f"the workspace {raw!r} is not a folder"
    if resolved.parent == resolved:
        return f"the workspace {raw!r} is a whole drive"
    names = os.environ if environ is None else environ
    for home in _homes(names):
        if inside(home, resolved):
            if inside(resolved, home):
                return f"the workspace {raw!r} is your home folder"
            return f"the workspace {raw!r} holds your home folder ({home})"
    for folder, what in ((_real(g6_home()), "G6's own state folder, where its key file, audit trail and stop are kept"),
                         (_real(Path(g6.__file__).parent), "G6's own installed code")):
        if folder is not None and (inside(resolved, folder) or inside(folder, resolved)):
            return f"the workspace {raw!r} is, holds or sits inside {what} ({folder})"
    return ""


def model_named(llm: object) -> str:
    """The model a run would ask, in words, for a dry run's plan: the caller's class, and its model when it names one;
    nothing is asked."""
    if llm is None:
        return "none"
    model = getattr(llm, "model", "")
    return f"{type(llm).__name__} ({model})" if isinstance(model, str) and model else type(llm).__name__


def versioned(folder: Path) -> bool:
    """Whether ``folder`` is under version control: a ``.git`` (a folder, or the file a git worktree has) in it or in a
    folder above it. A command-line agent's write run needs it, so the person can see and undo what the agent changed."""
    return any((place / ".git").exists() for place in (folder, *folder.parents))


def resolve_in(workspace: Path, raw: str) -> Path:
    """``raw`` (absolute, or relative to ``workspace``) with ``..`` and symbolic links resolved."""
    candidate = Path(raw)
    return (candidate if candidate.is_absolute() else workspace / candidate).resolve(strict=False)


def inside(path: Path, workspace: Path) -> bool:
    """Whether ``path`` (resolved) is ``workspace`` or under it."""
    p, w = os.path.normcase(str(path)), os.path.normcase(str(workspace))
    return p == w or p.startswith(w.rstrip("\\/") + os.sep)


class ToolCall(BaseModel):
    """A call the agent proposes: the tool, its arguments, the files or folders it touches, and its id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)
    paths: tuple[Annotated[str, Field(min_length=1, max_length=1000)], ...] = Field((), max_length=200)
    call_id: str = Field(default_factory=lambda: uuid4().hex[:12], min_length=1, max_length=64)


class ToolDecision(BaseModel):
    """What the gate decided about one call: ``run``, ``dry_run`` (decided, not run) or ``refused``."""

    model_config = ConfigDict(frozen=True)

    call_id: str
    tool: str
    effect: str = ""
    verdict: Literal["run", "dry_run", "refused"]
    code: str
    reason: str
    outward: bool = False
    paths: tuple[str, ...] = ()
    gate: dict[str, Any] | None = None  # what g6.action_gating decided, when it was asked

    @property
    def runs(self) -> bool:
        return self.verdict == "run"


class AgentEvent(BaseModel):
    """What an adapter reports as it runs: ``step`` before each step, ``tool_result`` (its ``call_id``) after a tool it
    ran, ``message``, and ``finished`` once, with ``output``, ``claimed_status`` and ``detail``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["step", "tool_result", "message", "finished"]
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class Availability:
    """What an adapter's ``available()`` found: ``found`` only when what it needs answered."""

    found: bool
    detail: str = ""
    where: str = ""
    version: str = ""
    code: str = ""


class AgentRefusal(Exception):
    """An adapter that cannot go on: its stable ``G6_E_*`` code, the reason, facts to keep, and whether the failure is
    the backend's own (which counts in its circuit breaker)."""

    def __init__(self, code: str, reason: str, *, detail: Mapping[str, Any] | None = None,
                 backend_failure: bool = False) -> None:
        if not _CODE.fullmatch(code):
            raise ValueError(f"an agent refusal needs a G6_E_ code; got {code!r}")
        super().__init__(f"[{code}] {reason}")
        self.code, self.reason, self.detail, self.backend_failure = code, reason, dict(detail or {}), backend_failure


class AgentOutcome(BaseModel):
    """How one run ended. ``qualified-draft`` at best; the agent's own report is ``claimed_status``."""

    model_config = ConfigDict(frozen=True)

    adapter: str
    state: Literal["qualified-draft", "blocked-escalated"]
    code: str
    message: str
    output: str = ""
    claimed_status: str = ""
    decisions: tuple[ToolDecision, ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    usage: dict[str, Any] = Field(default_factory=dict)
    detail: dict[str, Any] = Field(default_factory=dict)
    request_id: str = ""
    run_id: str = ""

    def envelope(self) -> dict[str, Any]:
        """The outcome in the library envelope: ``completion_state``, ``warning_card``, ``evidence``, ``request_id``
        and ``run_id`` beside its own fields."""
        card = WarningCard(code=self.code, message=self.message, suggested_action=codes.action(self.code),
                           severity="blocked" if self.state == "blocked-escalated" else "amber").model_dump()
        decisions = [d.model_dump(mode="json") for d in self.decisions]
        evidence = {"kind": "agent_run", "adapter": self.adapter, "code": self.code,
                    "claimed_status": self.claimed_status, "independent_check": None, "decisions": decisions,
                    "usage": self.usage, "detail": self.detail}
        return {"adapter": self.adapter, "code": self.code, "message": self.message, "output": self.output,
                "claimed_status": self.claimed_status, "decisions": decisions, "events": list(self.events),
                "usage": self.usage, "detail": self.detail, "completion_state": self.state, "warning_card": card,
                "evidence": [evidence], "request_id": self.request_id, "run_id": self.run_id}


__all__ = ["DOES", "FILE_EFFECTS", "NEEDS", "OUTWARD_EFFECTS", "AgentEvent", "AgentOutcome", "AgentRefusal",
           "AgentTask", "Availability", "Effect", "Limits", "Permissions", "ToolCall", "ToolDecision", "ToolSpec",
           "effective_limits", "inside", "model_named", "resolve_in", "versioned", "workspace_problem"]
