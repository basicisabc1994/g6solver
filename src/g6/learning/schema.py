# SPDX-License-Identifier: Apache-2.0
"""Data types for ``g6.learning``: tasks, agent replies, scored results, theories and bundle artifacts.

Trimmed from the archive's ``learning_layer/schema.py`` (audit 006 §11). Not kept: ``ConfigOverride`` (LL-009: its
values other than ``system_prompt`` were silently ignored, so no strategy emits one now), ``GeneratedTool`` (no
generator produced one; bundles no longer carry executable code), the checkpoint and ``LearningConfig`` types.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

#: A theory's lifecycle. ``candidate``: proposed by a strategy or imported from a bundle, measured only inside a
#: training run. ``active``: a person accepted a promotion recommendation for it; only active theories reach
#: :func:`g6.learning.build_addendum`. ``retired``: its paired evidence went against it.
THEORY_STATUSES = ("candidate", "active", "retired")
#: Dataset splits a training run refuses to learn from (audit 006, LL-012).
PROTECTED_SPLITS = frozenset({"validation", "holdout", "test", "frozen"})
#: Score modes that need a judge model.
JUDGE_MODES = frozenset({"llm_judge", "rubric", "rubric_judge"})


@dataclass(frozen=True)
class TaskInstance:
    """One task. ``expected_output`` (or a ``rubric`` in ``metadata``) belongs to the evaluator; the agent never sees
    it. ``metadata`` may set ``score_mode``, ``score_threshold``, ``rubric`` and anything a theory trigger reads."""

    input: str
    expected_output: str = ""
    instance_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.instance_id:
            digest = hashlib.sha256(f"{self.input}:{self.expected_output}".encode()).hexdigest()[:12]
            object.__setattr__(self, "instance_id", f"task_{digest}")

    @property
    def rubric(self) -> str:
        return str(self.metadata.get("rubric") or self.metadata.get("criteria") or "").strip()

    @property
    def score_mode(self) -> str:
        return str(self.metadata.get("score_mode") or "normalized_exact").lower()

    @property
    def needs_judge(self) -> bool:
        return self.score_mode in JUDGE_MODES or bool(self.rubric)

    @property
    def scorable(self) -> bool:
        """Whether an answer can be scored at all: it needs an expected answer or a rubric."""
        return bool(self.expected_output.strip()) or bool(self.rubric)


@dataclass
class Dataset:
    """Named task instances. Instance ids are unique, because every result is keyed by instance id (LL-001)."""

    name: str
    instances: list[TaskInstance] = field(default_factory=list)
    split: str = "full"

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for inst in self.instances:
            if inst.instance_id in seen:
                raise ValueError(f"G6_E_LEARNING_DUPLICATE_INSTANCE: instance id {inst.instance_id!r} appears twice in "
                                 f"dataset {self.name!r}")
            seen.add(inst.instance_id)


@dataclass(frozen=True)
class AgentTrace:
    """What the agent reported about one call. A plain callable agent may return it inside an :class:`AgentReply`."""

    tool_calls: tuple[str, ...] = ()
    num_turns: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    error: str | None = None


@dataclass(frozen=True)
class AgentReply:
    """An agent's answer to one prompt. The agent only answers; scoring is the runner's, keyed by instance."""

    output: str
    trace: AgentTrace = field(default_factory=AgentTrace)


@dataclass(frozen=True)
class TaskResult:
    instance_id: str
    actual_output: str
    passed: bool
    score: float = 0.0
    signals: dict[str, Any] = field(default_factory=dict)
    trace: AgentTrace = field(default_factory=AgentTrace)


@dataclass(frozen=True)
class FailureAnalysis:
    instance_id: str
    failure_codes: tuple[str, ...] = ()
    primary_code: str = ""
    signals: dict[str, Any] = field(default_factory=dict)
    context: str = ""


@dataclass
class Theory:
    """A learned hint: when ``trigger`` matches a task, add ``instruction`` to the prompt.

    ``confirmations`` / ``disconfirmations`` count training instances where adding the theory turned a failure into a
    pass / a pass into a failure, against a paired control (LL-005). ``strength`` is their posterior mean under a
    prior worth two observations centred on ``prior_strength`` (LL-015: the prior no longer vanishes on the first
    outcome). Strength orders and retires theories; it never promotes one (see :func:`g6.learning.promote`).
    ``proposer`` is who proposed it (a name, the proposer's own word); that name cannot accept it (LEARNING-01).
    """

    theory_id: str
    failure_code: str
    instruction: str
    trigger: str = "always"
    rationale: str = ""
    prior_strength: float = 0.5
    confirmations: int = 0
    disconfirmations: int = 0
    strength: float = 0.5
    status: str = "candidate"
    scope: str = ""
    source_instances: list[str] = field(default_factory=list)
    skill_content: str | None = None
    activated_by: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    proposer: str = ""

    @classmethod
    def new(cls, failure_code: str, instruction: str, *, trigger: str = "always", rationale: str = "",
            prior_strength: float = 0.5, source_instances: list[str] | tuple[str, ...] = (),
            proposer: str = "") -> Theory:
        """A fresh, unsaved candidate with a new id (a store sets its scope when it is added)."""
        return cls(theory_id=f"th_{uuid.uuid4().hex[:12]}", failure_code=failure_code, instruction=instruction,
                   trigger=trigger, rationale=rationale, prior_strength=prior_strength, strength=prior_strength,
                   source_instances=list(source_instances), proposer=proposer)

    @property
    def digest(self) -> str:
        """A hash of what the theory says (code, trigger, instruction); held-out evidence is bound to it."""
        return hashlib.sha256("\x1f".join((self.failure_code, self.trigger, self.instruction)).encode()).hexdigest()

    def compute_strength(self) -> float:
        return posterior_strength(self.confirmations, self.disconfirmations, self.prior_strength)


def posterior_strength(confirmations: int, disconfirmations: int, prior: float = 0.5) -> float:
    """Posterior mean with a prior worth two observations centred on ``prior`` (Laplace's rule when it is 0.5)."""
    return (confirmations + 2 * prior) / (confirmations + disconfirmations + 2)


@dataclass(frozen=True)
class PromptAddendum:
    """Hint text prepended to a task prompt, and the theories it came from."""

    text: str
    theory_ids: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


@dataclass(frozen=True)
class SkillDocument:
    """A markdown guide rendered from a theory (off by default; see ``g6.learning.baselines.render_skill``)."""

    content: str
    theory_id: str = ""


HarnessArtifact = PromptAddendum | SkillDocument


@dataclass(frozen=True)
class ReviewRecord:
    """A person's review attached to an exported bundle. The fields must not be blank (LL-007: the archive accepted
    ``{"reviewer_id": ""}`` as a review). It is a record, not an authentication: nothing here proves who wrote it."""

    reviewer_id: str
    reviewed_at: str
    scope_summary: str
    residual_risk_notes: str = ""

    def __post_init__(self) -> None:
        blank = [name for name in ("reviewer_id", "reviewed_at", "scope_summary") if not str(getattr(self, name)).strip()]
        if blank:
            raise ValueError("G6_E_LEARNING_REVIEW_INCOMPLETE: review fields are blank: " + ", ".join(blank))

    def to_dict(self) -> dict[str, str]:
        return {"reviewer_id": self.reviewer_id, "reviewed_at": self.reviewed_at, "scope_summary": self.scope_summary,
                "residual_risk_notes": self.residual_risk_notes}


__all__ = ["JUDGE_MODES", "PROTECTED_SPLITS", "THEORY_STATUSES", "AgentReply", "AgentTrace", "Dataset",
           "FailureAnalysis", "HarnessArtifact", "PromptAddendum", "ReviewRecord", "SkillDocument", "TaskInstance",
           "TaskResult", "Theory", "posterior_strength"]
