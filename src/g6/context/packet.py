# SPDX-License-Identifier: Apache-2.0
"""The context packet model: provenance-tagged items in fixed sections, plus the envelope fields (audit 032 §11).

Every item says where it came from (``source``), how much weight it carries (``authority``), whether it may be read
as an instruction (``kind`` + ``trust``) and how old it is (``freshness_class``). Item text and handle snippets are
masked by :func:`g6.context.scrub` when they are created, however they are created (CTX-007). Retrieved excerpts,
notes and model output are always ``trust="untrusted"`` and never above ``operational`` authority; only a trusted
``instruction`` item from ``governance``, ``user_intent`` or ``task_contract`` may be read as an instruction (CTX-011).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.context.redact import scrub


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AuthorityLevel(IntEnum):
    GOVERNANCE = 700
    USER_INTENT = 600
    TASK_CONTRACT = 500
    COMPONENT = 400
    OPERATIONAL = 300
    EXEMPLAR = 200
    MODEL_GENERATED = 100


AuthorityName = Literal["governance", "user_intent", "task_contract", "component", "operational", "exemplar",
                        "model_generated"]
Section = Literal["authority", "task", "component", "repository", "exemplar", "memory", "tool", "verification",
                  "output_contract"]
#: ``instruction``: may direct the agent. ``reference``: caller-supplied data. The other three are retrieved or
#: generated text and are always untrusted.
Kind = Literal["instruction", "reference", "retrieved_excerpt", "memory_note", "model_output"]
Trust = Literal["trusted", "untrusted"]
Freshness = Literal["live", "fresh", "aging", "stale", "unknown"]
ReliabilityState = Literal["verified", "qualified-draft", "blocked-escalated"]

PACKET_SECTIONS: tuple[str, ...] = get_args(Section)
_UNTRUSTED_KINDS = frozenset({"retrieved_excerpt", "memory_note", "model_output"})
_INSTRUCTION_AUTHORITIES = frozenset({"governance", "user_intent", "task_contract"})
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_DRIVE = re.compile(r"^[A-Za-z]:")


def validate_name(value: object, what: str = "run_id") -> str:
    """``value`` if it is safe as one file-name component, else ``ValueError`` (audit 032, CTX-003)."""
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError(f"G6_E_CONTEXT_UNSAFE_NAME: {what} must match {_NAME.pattern!r}, got {value!r}")
    return value


class ContextItem(BaseModel):
    """One datum with provenance, authority, trust and freshness."""

    model_config = ConfigDict(frozen=True)

    key: str
    value: str
    section: Section
    authority: AuthorityName = "model_generated"
    kind: Kind = "reference"
    trust: Trust = "untrusted"
    source: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    freshness_class: Freshness = "unknown"
    stale_after: str = ""
    retrieved_by: str = ""
    retrieved_at: str = Field(default_factory=utc_now_iso)
    included_because: str = ""
    #: For verification items: ``failed`` (a test failure) or ``unresolved`` (an open bug); read by the compaction.
    outcome: str = ""
    token_estimate: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("key")
    @classmethod
    def _required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("key is required")
        return value.strip()

    @field_validator("value", "source")
    @classmethod
    def _masked(cls, value: str) -> str:
        return scrub(value)  # audit 032, CTX-007: masked at item creation

    @model_validator(mode="after")
    def _trust_rules(self) -> ContextItem:
        # audit 032, CTX-011: retrieved text is quoted data, never an instruction.
        if self.kind in _UNTRUSTED_KINDS and (self.trust != "untrusted"
                                              or self.authority_level > AuthorityLevel.OPERATIONAL):
            raise ValueError(f"a {self.kind} item must be untrusted and at most operational authority")
        if self.kind == "instruction" and (self.trust != "trusted" or self.authority not in _INSTRUCTION_AUTHORITIES):
            raise ValueError("an instruction item must be trusted governance, user_intent or task_contract")
        return self

    @property
    def authority_level(self) -> AuthorityLevel:
        return AuthorityLevel[self.authority.upper()]

    @property
    def is_stale(self) -> bool:
        return self.freshness_class == "stale"

    @property
    def may_instruct(self) -> bool:
        """Whether the agent may treat this item as an instruction rather than as data."""
        return self.kind == "instruction"


class RepositoryHandle(BaseModel):
    """A pointer to a repository file or one matching line; the file is loaded on demand with ``load_handle``."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["file_path", "grep_result"]
    file_path: str
    line: int | None = None
    snippet: str = ""
    token_estimate: int = 0

    @field_validator("file_path")
    @classmethod
    def _relative(cls, value: str) -> str:
        path = value.replace("\\", "/")
        if not path or path.startswith("/") or _DRIVE.match(path) or ".." in path.split("/"):
            raise ValueError(f"G6_E_CONTEXT_HANDLE_INVALID: file_path must be relative inside the roots: {value!r}")
        return path

    @field_validator("snippet")
    @classmethod
    def _masked(cls, value: str) -> str:
        return scrub(value)  # audit 032, CTX-007

    @property
    def identity(self) -> tuple[Any, ...]:
        """Content identity (ignores the derived ``token_estimate``)."""
        return (self.kind, self.file_path, self.line, self.snippet)

    @property
    def summary(self) -> str:
        return f"{self.file_path}:{self.line}: {self.snippet}" if self.kind == "grep_result" else \
            f"file_path: {self.file_path}"


class ContextPacket(BaseModel):
    """A sectioned context bundle for one agent invocation, with the cross-surface envelope fields.

    ``completion_state``, ``warning_card`` and ``evidence`` are outputs of :func:`g6.context.score_packet`; nothing
    in this package reads them back from a packet (CTX-004). ``conflicts`` records the values a merge discarded.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    phase: str = ""
    role: str = "orchestrator"
    authority: list[ContextItem] = Field(default_factory=list)
    task: list[ContextItem] = Field(default_factory=list)
    component: list[ContextItem] = Field(default_factory=list)
    repository: list[ContextItem] = Field(default_factory=list)
    exemplar: list[ContextItem] = Field(default_factory=list)
    memory: list[ContextItem] = Field(default_factory=list)
    tool: list[ContextItem] = Field(default_factory=list)
    verification: list[ContextItem] = Field(default_factory=list)
    output_contract: list[ContextItem] = Field(default_factory=list)
    repository_handles: list[RepositoryHandle] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    budget_tokens: int = 8000
    used_tokens: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    completion_state: ReliabilityState = "qualified-draft"
    warning_card: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    task_id: str = ""

    @field_validator("run_id")
    @classmethod
    def _run_id(cls, value: str) -> str:
        return validate_name(value, "run_id")  # audit 032, CTX-003

    @field_validator("phase")
    @classmethod
    def _phase(cls, value: str) -> str:
        return value if value == "" else validate_name(value, "phase")  # audit 032, CTX-003

    @property
    def all_items(self) -> list[ContextItem]:
        return [item for section in PACKET_SECTIONS for item in getattr(self, section)]

    @property
    def stale_items(self) -> list[ContextItem]:
        return [item for item in self.all_items if item.is_stale]

    def section_items(self, section: str) -> list[ContextItem]:
        return list(getattr(self, section)) if section in PACKET_SECTIONS else []


class ContextScorecard(BaseModel):
    """What :func:`g6.context.score_packet` recomputed from a packet's content."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    item_count: int
    used_tokens: int
    budget_tokens: int
    budget_ratio: float
    stale_count: int
    conflict_count: int
    empty_retrieval: bool
    missing_sections: list[str] = Field(default_factory=list)
    completion_state: ReliabilityState = "qualified-draft"
    warning_card: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    task_id: str = ""


class SubagentResult(BaseModel):
    """What a subagent reported. It carries no self-assigned label (the archive defaulted one to "supported")."""

    model_config = ConfigDict(frozen=True)

    agent_id: str
    task: str
    summary: str
    evidence: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def item_tokens(item: ContextItem) -> int:
    """An item's cost: its declared estimate, never less than its text (a low declared estimate is not trusted)."""
    return max(int(item.token_estimate or 0), estimate_tokens(item.value))


def packet_tokens(packet: ContextPacket) -> int:
    return sum(item_tokens(item) for item in packet.all_items)


def merge_context_items(items: Iterable[ContextItem]) -> list[ContextItem]:
    """One item per key: the highest authority wins; on a tie the later item wins (``find_conflicts`` reports it)."""
    winners: dict[str, ContextItem] = {}
    for item in items:
        current = winners.get(item.key)
        if current is None or item.authority_level >= current.authority_level:
            winners[item.key] = item
    return list(winners.values())


def find_conflicts(items: Iterable[ContextItem]) -> list[dict[str, Any]]:
    """Every key that carries more than one distinct value, and how the merge resolves it.

    Run on the items BEFORE ``merge_context_items``: after the merge each key has one value left (audit 032, CTX-001).
    ``tie`` is true when two different values share the top authority, so the order of the items decided.
    """
    by_key: dict[str, list[ContextItem]] = {}
    for item in items:
        by_key.setdefault(item.key, []).append(item)
    found: list[dict[str, Any]] = []
    for key, group in by_key.items():
        values = {item.value for item in group}
        if len(values) < 2:
            continue
        kept = merge_context_items(group)[0]
        top = max(item.authority_level for item in group)
        found.append({"key": key, "values": len(values), "kept_source": kept.source, "kept_authority": kept.authority,
                      "tie": len({item.value for item in group if item.authority_level == top}) > 1,
                      "sources": [item.source for item in group]})
    return found


__all__ = ["PACKET_SECTIONS", "AuthorityLevel", "ContextItem", "ContextPacket", "ContextScorecard", "RepositoryHandle",
           "SubagentResult", "estimate_tokens", "find_conflicts", "item_tokens", "merge_context_items",
           "packet_tokens", "utc_now_iso", "validate_name"]
