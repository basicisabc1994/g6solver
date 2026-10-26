# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Standard refinement ``S' >= S`` (CSIR D1, tex:192-212): every mandatory predicate of ``S`` stays mandatory in ``S'``.

A frozen standard changes only by a re-freeze that adds at least one predicate and says why (an owner's answer, an owner's
approval). Each revision names its parent's hash, so a chain of revisions can be checked offline; a revision that drops a
predicate, or whose parent is not the previous revision, breaks the chain. There is deliberately no operation that removes
a predicate: lowering a standard is a new mission with a new standard, decided by its owner, never an edit of this one.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from ..flag import require_research


class NotARefinement(ValueError):
    """A re-freeze that would not refine the standard it starts from."""


@dataclass(frozen=True)
class FrozenStandard:
    standard_id: str
    version: int
    predicates: tuple[str, ...]
    parent: str = ""
    reason: str = ""

    @classmethod
    def first(cls, standard_id: str, predicates: Sequence[str]) -> FrozenStandard:
        require_research()
        names = _names(predicates)
        if not names:
            raise NotARefinement("a standard needs at least one predicate")
        return cls(standard_id, 1, tuple(sorted(set(names))))

    @property
    def sha256(self) -> str:
        body = {"standard_id": self.standard_id, "version": self.version, "predicates": sorted(self.predicates),
                "parent": self.parent, "reason": self.reason}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _names(predicates: Sequence[str]) -> list[str]:
    if any(not isinstance(p, str) or not p.strip() or p != p.strip() for p in predicates):
        raise NotARefinement("a predicate name must be non-empty text with no surrounding spaces")
    return list(predicates)


def refines(new: FrozenStandard, old: FrozenStandard) -> bool:
    """``new >= old``: same standard, and every predicate of ``old`` is still in ``new``."""
    return new.standard_id == old.standard_id and set(old.predicates) <= set(new.predicates)


def refreeze(old: FrozenStandard, *, add: Sequence[str], reason: str) -> FrozenStandard:
    """The next revision: ``old`` plus ``add``. Refused when nothing is new or no reason is given."""
    require_research()
    new = sorted(set(_names(add)) - set(old.predicates))
    if not new:
        raise NotARefinement("a re-freeze must add at least one predicate the standard does not already have")
    if not reason.strip():
        raise NotARefinement("a re-freeze must say why (the owner's answer or approval it records)")
    return FrozenStandard(old.standard_id, old.version + 1, tuple(sorted({*old.predicates, *new})), parent=old.sha256,
                          reason=reason.strip())


def chain_problems(revisions: Sequence[FrozenStandard]) -> list[str]:
    """Every break in a chain of revisions (first to last); an empty list means it starts at the first revision and each
    revision refines the one before it (a chain that starts later could hide a predicate dropped before it)."""
    if not revisions:
        return ["the chain is empty"]
    problems: list[str] = []
    root = revisions[0]
    if root.version != 1 or root.parent:
        problems.append(f"version {root.version}: the chain does not start at the first revision")
    for before, after in pairwise(revisions):
        where = f"version {after.version}"
        if after.parent != before.sha256:
            problems.append(f"{where}: its parent is not the hash of version {before.version}")
        if after.version != before.version + 1:
            problems.append(f"{where}: does not follow version {before.version}")
        dropped = sorted(set(before.predicates) - set(after.predicates))
        if after.standard_id != before.standard_id:
            problems.append(f"{where}: belongs to another standard")
        elif dropped:
            problems.append(f"{where}: drops predicate(s) {dropped}")
    return problems
