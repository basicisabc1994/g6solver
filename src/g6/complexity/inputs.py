# SPDX-License-Identifier: Apache-2.0
"""Typed caller input for g6.complexity's block, and the frozen intent built from it.

A requirement's ``authority`` and an obligation's ``normative`` flag are the caller's word. They decide which
obligations a change must keep; ``FrozenIntent`` refuses a normative obligation on a requirement only a model or an
unconfirmed adoption stated, so a model cannot make its own guess binding.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.code.design_governance.graph import module_of
from g6.complexity.atlas import build_atlas
from g6.complexity.ir import CIR, FrozenIntent, Obligation, Requirement

#: The most characters (paths and contents together) a file map may hold.
MAX_TOTAL_CHARS = 4_000_000


class RequirementIn(BaseModel):
    """One stated requirement: what it says, on whose authority, and whether a change satisfies, keeps or avoids it."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=100)
    text: str = Field(..., max_length=2000)
    authority: Literal["stated", "repository_stated", "person_confirmed", "model_hypothesis", "adopted_unconfirmed"]
    source: str = Field(..., max_length=200)
    kind: Literal["satisfy", "preserve", "forbid"] = "satisfy"


class ObligationIn(BaseModel):
    """One obligation of a requirement: what must hold, which oracle says so, and whether it binds a change."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=100)
    requirement_id: str = Field(..., min_length=1, max_length=100)
    expectation: str = Field(..., max_length=2000)
    oracle_source: str = Field(..., max_length=200)
    normative: bool = False


Path = Annotated[str, Field(max_length=300)]
Source = Annotated[str, Field(max_length=1_000_000)]


class TreeFields(BaseModel):
    """The fields that name a tree to map: the file map, its package, the caller's intent, the environment it is pinned
    to, and (for a cone) what changed. Shared by g6.complexity's block and algorithm_design's ``fuse``."""

    model_config = ConfigDict(extra="forbid")

    files: dict[Path, Source] = Field(default_factory=dict, max_length=500)
    package: str = Field("", max_length=100)
    requirements: list[RequirementIn] = Field(default_factory=list, max_length=200)
    obligations: list[ObligationIn] = Field(default_factory=list, max_length=200)
    environment_digest: str = Field("", max_length=128)
    changed: list[Path] = Field(default_factory=list, max_length=100)


def intent_from(requirements: list[RequirementIn], obligations: list[ObligationIn]) -> FrozenIntent:
    """The frozen intent of the caller's requirements and obligations; ``ValueError`` when the IR refuses them."""
    return FrozenIntent(tuple(Requirement(**r.model_dump()) for r in requirements),
                        tuple(Obligation(**o.model_dump()) for o in obligations))


def atlas_of(d: TreeFields) -> CIR:
    """The atlas of the tree ``d`` names; ``ValueError`` (with why) for a file map, package, environment or intent the
    atlas cannot take. Nothing in the files is imported or run."""
    problem = files_problem(d.files)
    if problem:
        raise ValueError(problem)
    intent = intent_from(d.requirements, d.obligations)
    try:
        return build_atlas(d.files, package=d.package, intent=intent, environment_digest=d.environment_digest)
    except (MemoryError, RecursionError) as exc:   # CPython's parser stack or AST depth: nesting past its limits
        raise ValueError("a file is nested too deeply for Python's parser to read; split the expression or the "
                         "file") from exc


def declared(d: TreeFields) -> dict[str, Any]:
    """The evidence entry for what the caller declared: kept as their word (``declared_by: "caller"``), never a
    reason to raise a state."""
    return {"declared_by": "caller", "source": "caller",
            "summary": "the requirements' authority, the obligations' normative flags and the environment digest are "
                       "the caller's word; they choose which obligations a change must keep and raise no state",
            "requirements": [{"id": r.id, "authority": r.authority, "source": r.source} for r in d.requirements],
            "normative_obligations": sorted(o.id for o in d.obligations if o.normative),
            "environment_digest": d.environment_digest}


def files_problem(files: Mapping[str, str]) -> str:
    """``""`` when the file map can be read, else why it is refused: more than ``MAX_TOTAL_CHARS`` characters in all,
    or a Python file whose path is not a relative module path (no drive, no leading ``/``, no ``..``)."""
    total = sum(len(path) + len(text) for path, text in files.items())
    if total > MAX_TOTAL_CHARS:
        return f"the files hold {total:,} characters; at most {MAX_TOTAL_CHARS:,} are read"
    bad = sorted(path for path in files if path.endswith(".py") and module_of(path) is None)
    if bad:
        return f"{bad[0]!r} is not a relative path of a module (use a/b/c.py, with no drive, no leading / and no ..)"
    return ""


__all__ = ["MAX_TOTAL_CHARS", "ObligationIn", "RequirementIn", "TreeFields", "atlas_of", "declared", "files_problem",
           "intent_from"]
