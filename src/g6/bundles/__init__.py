# SPDX-License-Identifier: Apache-2.0
"""Bundles: ready-made harnesses for one real problem each, run from a private local web page.

A bundle declares a form (``fields``) and a ``run(inputs, llm)`` that returns a ``BundleResult``.
``g6 bundle <id>`` (or a downloaded bundle executable) starts the local runner and opens the
browser. Results always carry a reliability label; a bundle may never claim more than the
components it calls established.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from g6.core import BLOCKED_ESCALATED, COMPLETION_STATES, QUALIFIED_DRAFT, VERIFIED

_RANK = {VERIFIED: 0, QUALIFIED_DRAFT: 1, BLOCKED_ESCALATED: 2}


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = "textarea"   # textarea | text
    placeholder: str = ""
    help: str = ""


@dataclass
class BundleResult:
    completion_state: str
    summary: str
    rows: list[dict[str, Any]] = field(default_factory=list)   # rendered as a table
    notes: list[str] = field(default_factory=list)
    code: str = ""   # the warning card's G6_E_* code; defaults by state

    def __post_init__(self) -> None:
        if self.completion_state not in COMPLETION_STATES:
            raise ValueError(f"unknown completion state {self.completion_state!r}")

    def to_dict(self, *, request_id: str = "", run_id: str = "") -> dict[str, Any]:
        """The result inside the library envelope; anything short of ``verified`` carries a warning card. The bundle's
        own ``verified`` goes through the door rule (``g6.core.door``): G6 does not re-run a bundle's checks, so it is
        held at ``qualified-draft`` with a ``G6_E_PROMOTION_LAW`` card and ``claimed_completion_state``."""
        from g6.core.door import door_envelope, held_copy
        from g6.core.reliability_envelope import warning_card

        envelope = door_envelope(self.completion_state, who="this bundle", request_id=request_id, run_id=run_id)
        if envelope["completion_state"] != VERIFIED and "claimed_completion_state" not in envelope:
            code = self.code or ("G6_E_BLOCKED" if envelope["completion_state"] == BLOCKED_ESCALATED
                                 else "G6_E_QUALIFIED_DRAFT")
            envelope["warning_card"] = warning_card(code, self.summary)
        # A row's label is the bundle's own word too: G6 re-runs no row, so none reads as verified on the page.
        rows = held_copy(self.rows, envelope["completion_state"] if envelope["completion_state"] != VERIFIED
                         else QUALIFIED_DRAFT)
        return {**envelope, "summary": self.summary, "rows": rows, "notes": self.notes}


def weakest(states: list[str]) -> str:
    """The overall label of several results is the weakest of them (never the strongest)."""
    return max(states, key=_RANK.__getitem__) if states else QUALIFIED_DRAFT


@dataclass(frozen=True)
class Bundle:
    id: str
    title: str
    summary: str
    fields: tuple[Field, ...]
    run: Callable[[dict[str, str], Any], BundleResult]
    needs_model: bool = False
    disclaimer: str = ""
    #: It runs code the person gives it (a repository's tests, a script): its page asks, where no operating-system
    #: boundary can confine that code and nothing was chosen, whether it may run without one (``g6.bundles.server``).
    runs_untrusted_code: bool = False


def catalog() -> dict[str, Bundle]:
    from g6.bundles.shipped import BUNDLES

    return {b.id: b for b in BUNDLES}


#: How a bundle in a vetted area is listed until its certificate is current (as the generated catalogue says it).
AWAITING_CERTIFICATE = "built, awaiting its certificate"
AWAITING_NOTE = ("Built, awaiting its certificate: the rules this check uses have not yet been checked against their "
                 "sources by a G6 certificate. Read the cited text before relying on any row.")


def awaiting_certificate(bundle: Bundle) -> bool:
    """Whether the bundle's package declares a vetted area (``__g6_vetting__``) whose certificate is not current yet
    (``g6.core.vetting``): it is then listed only as built, awaiting its certificate."""
    from g6.core import vetting

    return vetting.awaiting_review(bundle.run.__module__)


__all__ = ["AWAITING_CERTIFICATE", "AWAITING_NOTE", "Bundle", "BundleResult", "Field", "awaiting_certificate", "catalog",
           "weakest"]
