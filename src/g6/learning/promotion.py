# SPDX-License-Identifier: Apache-2.0
"""``promote``: may a candidate theory be recommended for activation, on held-out paired evidence?

Rewritten from the archive's ``promotion_gate.py`` (audit 006, LL-006). The archive's gate passed when four strings
were non-blank and two ids "resolved" against any ``str -> bool`` the caller injected, so a failed holdout, or one
for another harness, satisfied it. Here the decision reads what the evidence says:

- it is bound to this theory: the same id and the same text digest;
- it is held out: phase ``"holdout"``, and none of its instances is a training instance or one of the theory's
  sources (results on the training set never promote);
- it has at least ``policy.min_pairs`` distinct instances and ``policy.min_discordant`` pairs whose runs disagree;
- the theory wins more pairs than it loses, with a one-sided exact sign test at most ``policy.alpha``.

The best outcome is ``recommendation="promote"`` with ``completion_state="qualified-draft"``. The decision carries the
evidence it was made on, and a person accepts it with :meth:`g6.learning.TheoryStore.activate`, which decides again
from that evidence; nothing is activated here, and nothing is ever ``verified``, because the held-out tasks and their
answers are the caller's and the evidence is only as independent as the caller made it.

The evidence is passed in, never looked up: this module does not import the research kernel (founder decision A1).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from g6.learning.paired import CONFIRMED, DISCONFIRMED, PairedOutcome, credit, sign_test
from g6.learning.schema import Theory

RECOMMENDED = "G6_E_LEARNING_PROMOTION_RECOMMENDED"


@dataclass(frozen=True)
class HeldOutEvidence:
    """Paired outcomes for one theory on held-out instances, and the training instances they must avoid."""

    theory_id: str
    theory_digest: str
    pairs: tuple[PairedOutcome, ...]
    training_instance_ids: frozenset[str] = field(default_factory=frozenset)
    phase: str = "holdout"
    run_id: str = ""

    @property
    def wins(self) -> int:
        return sum(credit(p.with_theory, p.without_theory) == CONFIRMED for p in self.pairs)

    @property
    def losses(self) -> int:
        return sum(credit(p.with_theory, p.without_theory) == DISCONFIRMED for p in self.pairs)

    def to_dict(self) -> dict[str, Any]:
        return {"theory_id": self.theory_id, "theory_digest": self.theory_digest, "phase": self.phase,
                "run_id": self.run_id, "training_instance_ids": sorted(self.training_instance_ids),
                "pairs": [[p.instance_id, p.with_theory, p.without_theory] for p in self.pairs]}

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class PromotionPolicy:
    min_pairs: int = 10
    min_discordant: int = 5
    alpha: float = 0.05


@dataclass(frozen=True)
class PromotionDecision:
    """A recommendation (``"promote"``) or a refusal (``"refuse"``), with the code and reasons behind it."""

    theory_id: str
    theory_digest: str
    recommendation: str
    code: str
    reasons: tuple[str, ...]
    evidence_digest: str
    wins: int = 0
    losses: int = 0
    pairs: int = 0
    p_one_sided: float = 1.0
    policy: PromotionPolicy = field(default_factory=PromotionPolicy)
    #: The held-out evidence the decision was made on; :meth:`g6.learning.TheoryStore.activate` decides again from it,
    #: so a decision built by hand (with none) never activates a theory.
    evidence: HeldOutEvidence | None = field(default=None, repr=False)

    @property
    def completion_state(self) -> str:
        return "qualified-draft" if self.recommendation == "promote" else "blocked-escalated"

    def to_envelope(self, *, request_id: str = "", run_id: str = "") -> dict[str, Any]:
        from g6.core.reliability_envelope import envelope_fields, warning_card

        message = ("Recommended for activation on held-out paired evidence; a person must accept it "
                   "(TheoryStore.activate). Not verified: the held-out tasks are the caller's."
                   if self.recommendation == "promote" else "; ".join(self.reasons))
        evidence = [{"source": "g6.learning.promote", "summary": f"{self.wins} wins, {self.losses} losses over "
                     f"{self.pairs} held-out pairs (one-sided sign test p={self.p_one_sided:.4g})",
                     "evidence_digest": self.evidence_digest, "theory_id": self.theory_id}]
        return {"theory_id": self.theory_id, "recommendation": self.recommendation, "reasons": list(self.reasons),
                **envelope_fields(reliability_label=self.completion_state,  # type: ignore[arg-type]
                                  warning_card=warning_card(self.code, message), evidence=evidence,
                                  request_id=request_id, run_id=run_id)}


def promote(candidate: Theory, evidence: HeldOutEvidence, policy: PromotionPolicy | None = None) -> PromotionDecision:
    """Decide from ``evidence`` alone whether to recommend ``candidate`` for activation (see the module docstring)."""
    policy = policy or PromotionPolicy()
    faults = _faults(candidate, evidence, policy)
    common = {"theory_id": candidate.theory_id, "theory_digest": candidate.digest, "evidence_digest": evidence.digest,
              "wins": evidence.wins, "losses": evidence.losses, "pairs": len(evidence.pairs),
              "p_one_sided": sign_test(evidence.wins, evidence.losses), "policy": policy, "evidence": evidence}
    if faults:
        return PromotionDecision(recommendation="refuse", code=faults[0][0],
                                 reasons=tuple(f"{c}: {r}" for c, r in faults), **common)
    return PromotionDecision(recommendation="promote", code=RECOMMENDED,
                             reasons=(f"{evidence.wins} held-out wins against {evidence.losses} losses",), **common)


def _faults(candidate: Theory, evidence: HeldOutEvidence, policy: PromotionPolicy) -> list[tuple[str, str]]:
    # audit 006, LL-006: every check reads the evidence's content; ids that merely exist never satisfy the gate.
    found: list[tuple[str, str]] = []
    if candidate.status != "candidate":
        found.append(("G6_E_LEARNING_NOT_CANDIDATE", f"the theory is {candidate.status!r}, not a candidate"))
    if evidence.theory_id != candidate.theory_id or evidence.theory_digest != candidate.digest:
        found.append(("G6_E_LEARNING_EVIDENCE_UNBOUND", "the evidence was measured on a different theory or text"))
    ids: Sequence[str] = [p.instance_id for p in evidence.pairs]
    if len(set(ids)) != len(ids):
        found.append(("G6_E_LEARNING_EVIDENCE_INVALID", "the evidence counts an instance more than once"))
    seen_in_training = set(ids) & (set(evidence.training_instance_ids) | set(candidate.source_instances))
    if evidence.phase != "holdout" or seen_in_training:
        found.append(("G6_E_LEARNING_NOT_HELD_OUT", "results on training instances never promote"
                      + (f" ({len(seen_in_training)} instance(s) overlap)" if seen_in_training else "")))
    discordant = evidence.wins + evidence.losses
    if len(set(ids)) < policy.min_pairs or discordant < policy.min_discordant:
        found.append(("G6_E_LEARNING_EVIDENCE_INSUFFICIENT",
                      f"{len(set(ids))} held-out pairs ({discordant} discordant); the policy needs "
                      f"{policy.min_pairs} ({policy.min_discordant} discordant)"))
    elif evidence.wins <= evidence.losses or sign_test(evidence.wins, evidence.losses) > policy.alpha:
        found.append(("G6_E_LEARNING_NO_HELD_OUT_GAIN",
                      f"{evidence.wins} wins against {evidence.losses} losses (sign test "
                      f"p={sign_test(evidence.wins, evidence.losses):.4g} > {policy.alpha}, or no net gain)"))
    return found


__all__ = ["RECOMMENDED", "HeldOutEvidence", "PromotionDecision", "PromotionPolicy", "promote"]
