# SPDX-License-Identifier: Apache-2.0
"""The authority rule for the ops a plan names (audit 016 §11: the archive's ``door_authority``, proposed for
``g6.core``).

One rule, the workforce planner's (``g6.workforce.plans``), read from its own tables so the two cannot drift: an op
below A1 (class A0, which includes every op no reading measured) is refused, and an op that can act (``external``,
``irreversible``, or with no declared side effect) is refused below A2. An unknown class reads A0.

The class comes from an authority reading ``(component, op) -> class``. The default reading is the library's own table
(``g6.workforce.service.CENSUS``, carried over from the archive's census and not re-measured), each row bound to the
digest of the contract it was read for: a changed contract reads A0. Every other op reads A0 until a caller passes a
reading it trusts. Nothing here imports a component, calls a model or runs an op.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from g6.workforce.contracts import AUTHORITY, authority_rank
from g6.workforce.plans import EFFECT_AUTHORITY
from g6.workforce.service import CENSUS

NOT_CLEARED = "G6_E_OP_NOT_CLEARED"
ACTS_BELOW_A2 = "G6_E_OP_ACTS_BELOW_A2"

#: ``(component, op) -> class``: "A0".."A4", anything else (or "") meaning nothing measured the op.
Authority = Callable[[str, str], str]


def shipped_authority(contracts: Mapping[tuple[str, str], Any]) -> Authority:
    """The library's own reading over ``contracts``: a ``CENSUS`` row counts only for the exact contract it was read
    for (its digest), so authority cannot outlive a changed contract."""

    def read(component: str, op: str) -> str:
        found = CENSUS.get((component, op))
        contract = contracts.get((component, op))
        return found[0] if found and contract is not None and contract.sha256 == found[1] else ""

    return read


def measured(component: str, op: str, authority: Authority) -> str | None:
    """The op's class as the reading gives it, or None when the reading has none on the ladder."""
    found = str(authority(component, op) or "")
    return found if found in AUTHORITY else None


@dataclass(frozen=True)
class Finding:
    """Why an op is not cleared: its class (A0 when unmeasured), the effect it was judged on, and what it needs."""

    component: str
    op: str
    cls: str
    measured: bool
    effect: str
    need: str
    code: str


def judge(component: str, op: str, *, authority: Authority, effect: str | None) -> Finding | None:
    """None when the op is cleared; else why not. ``effect`` is the op's declared side effect (None: undeclared,
    which counts as acting)."""
    found = measured(component, op, authority)
    cls = found or "A0"
    if cls == "A0":
        return Finding(component, op, "A0", found is not None, effect or "", "A1", NOT_CLEARED)
    acting = effect or "external"
    need = EFFECT_AUTHORITY.get(acting, "A2")
    if authority_rank(cls) < authority_rank(need):
        return Finding(component, op, cls, True, acting, need, ACTS_BELOW_A2)
    return None


__all__ = ["ACTS_BELOW_A2", "NOT_CLEARED", "Authority", "Finding", "judge", "measured", "shipped_authority"]
