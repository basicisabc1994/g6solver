# SPDX-License-Identifier: Apache-2.0
"""``ScorecardStore``: the maturity scorecard, kept in two plain files in one directory you name.

What it is (audit 008, ST-019: the archive's docs described an in-memory SQLite store; the code wrote JSON files under
``~/.g6``): ``audit_log.jsonl``, an append-only log with one :class:`AuditResult` per line, and ``scorecard.json``, the
current :class:`MaturityScore` of each component, rewritten whole on every change. Not SQLite, no in-memory mode, and
no default directory: an absolute path is used as given, and a relative one is placed under ``$G6_HOME``.

The rules (audit 008, ST-009):

* the tier is the latest evidenced one: each audit replaces it, so a failing re-audit lowers it (the archive kept the
  maximum ever seen, which is still kept, as ``max_tier_seen``, and never read as the current tier);
* while a component is flagged its tier cannot rise, by audit or by promotion;
* ``promote`` never clears a flag, and a promotion to T5 or above needs the latest audit to have passed on a reviewed
  rubric; only :meth:`ScorecardStore.resolve_flag`, with evidence, clears a flag;
* a promotion stands until the next audit, which replaces it.

A ``scorecard.json`` that cannot be read raises rather than starting empty, since starting empty would drop every flag.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from g6.self_training.schema import (
    PUBLICATION_THRESHOLD,
    TIER_NAMES,
    AuditResult,
    ComponentScorecard,
    MaturityScore,
)


class PromotionRefused(ValueError):
    """:meth:`ScorecardStore.promote` refused; the message says why."""


def _resolve_store_dir(store_dir: str | os.PathLike[str] | None) -> Path:
    # audit 008, ST-019: the archive fell back to ~/.g6/self_training when no directory was given.
    if store_dir is None or not str(store_dir).strip():
        raise ValueError("ScorecardStore needs an explicit store directory; there is no default")
    path = Path(store_dir)
    if path.is_absolute():
        return path
    from g6.core.storage import state_path

    return state_path(*path.parts)


def _next_tier(existing: MaturityScore | None, result: AuditResult) -> int:
    """The tier after ``result``: the audit's own, held down while a flag is unresolved (audit 008, ST-009)."""
    if existing is not None and existing.flagged:
        return min(existing.current_tier, result.tier)
    return result.tier


def _promotion_refusal(existing: MaturityScore | None, history: list[dict[str, Any]], tier: int) -> str | None:
    """Why promoting to ``tier`` is refused, or ``None``."""
    if existing is not None and existing.flagged and tier > existing.current_tier:
        # audit 008, ST-009: the archive let a T4 promote clear the flag, after which a T5 promote passed its gate.
        return (f"{existing.component} is flagged ({existing.flag_reason or 'no reason given'}); resolve the flag "
                "with evidence (resolve_flag) before promoting it")
    if tier >= PUBLICATION_THRESHOLD:
        if not history:
            return f"T{tier} needs a passing audit, and there is none on record"
        latest = history[-1]
        if not latest.get("passed_threshold"):
            return f"T{tier} needs the latest audit to have passed, and it did not (tier {latest.get('tier')})"
        if not latest.get("rubric_reviewed"):
            return f"T{tier} needs the latest audit to have used a reviewed rubric, and it did not"
    return None


class ScorecardStore:
    """The scorecard in ``<store_dir>/audit_log.jsonl`` (append-only) and ``<store_dir>/scorecard.json``."""

    AUDIT_LOG = "audit_log.jsonl"
    SCORECARD = "scorecard.json"

    def __init__(self, store_dir: str | os.PathLike[str]) -> None:
        self._dir = _resolve_store_dir(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._audit_log = self._dir / self.AUDIT_LOG
        self._scorecard_path = self._dir / self.SCORECARD
        self._scores: dict[str, MaturityScore] = {}
        if self._scorecard_path.exists():
            try:
                data = json.loads(self._scorecard_path.read_text(encoding="utf-8"))
                self._scores = {name: MaturityScore.model_validate(s) for name, s in data.get("scores", {}).items()}
            except Exception as exc:  # noqa: BLE001 - re-raised: an unreadable scorecard must not drop its flags
                raise ValueError(f"cannot read the scorecard at {self._scorecard_path}: {exc}; move it aside to "
                                 "start a new one") from exc

    @property
    def store_dir(self) -> Path:
        return self._dir

    def _save(self) -> None:
        tmp = self._scorecard_path.with_suffix(".json.tmp")
        tmp.write_text(self.get_scorecard().model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self._scorecard_path)

    def append_audit(self, result: AuditResult) -> MaturityScore:
        """Log ``result`` and make it the component's latest evidence."""
        with open(self._audit_log, "a", encoding="utf-8") as log:
            log.write(result.model_dump_json() + "\n")
        existing = self._scores.get(result.component)
        tier = _next_tier(existing, result)
        base = existing or MaturityScore(component=result.component)
        score = base.model_copy(update={
            "current_tier": tier, "tier_name": TIER_NAMES[tier], "tier_source": "audit",
            "max_tier_seen": max(base.max_tier_seen, result.tier),
            "audit_count": base.audit_count + 1, "pass_count": base.pass_count + int(result.passed_threshold),
            "last_score": result.score, "last_evaluable": result.evaluable,
            "last_audit_timestamp": result.timestamp,
        })
        self._scores[result.component] = score
        self._save()
        return score

    def get_score(self, component: str) -> MaturityScore | None:
        return self._scores.get(component)

    def get_scorecard(self) -> ComponentScorecard:
        scores = dict(self._scores)
        flagged = sum(1 for s in scores.values() if s.flagged)
        above = sum(1 for s in scores.values() if not s.flagged and s.current_tier >= PUBLICATION_THRESHOLD)
        return ComponentScorecard(scores=scores, total_components=len(scores), above_threshold=above,
                                  below_threshold=len(scores) - above - flagged, flagged=flagged,
                                  timestamp=time.time())

    def promote(self, component: str, tier: int) -> MaturityScore:
        """Set ``component``'s tier by decision. Raises :class:`PromotionRefused` while it is flagged (to raise it), or
        for T5 and above unless its latest audit passed on a reviewed rubric. Never clears a flag (audit 008, ST-009)."""
        if tier not in TIER_NAMES:
            raise ValueError(f"tier must be 0-6, got {tier!r}")
        existing = self._scores.get(component)
        refusal = _promotion_refusal(existing, self.audit_history(component), tier)
        if refusal is not None:
            raise PromotionRefused(f"refusing to promote {component} to T{tier}: {refusal}")
        base = existing or MaturityScore(component=component)
        score = base.model_copy(update={"current_tier": tier, "tier_name": TIER_NAMES[tier],
                                        "tier_source": "promotion", "max_tier_seen": max(base.max_tier_seen, tier)})
        self._scores[component] = score
        self._save()
        return score

    def flag(self, component: str, reason: str = "") -> MaturityScore:
        """Flag ``component``: its tier is held where it is until :meth:`resolve_flag`."""
        base = self._scores.get(component) or MaturityScore(component=component)
        score = base.model_copy(update={"flagged": True, "flag_reason": reason, "flag_resolution": ""})
        self._scores[component] = score
        self._save()
        return score

    def resolve_flag(self, component: str, evidence: str) -> MaturityScore:
        """Clear ``component``'s flag, recording ``evidence`` (required) for why it is resolved."""
        if not evidence.strip():
            raise ValueError("resolving a flag needs evidence of what resolved it")
        existing = self._scores.get(component)
        if existing is None or not existing.flagged:
            raise ValueError(f"{component} is not flagged")
        score = existing.model_copy(update={"flagged": False, "flag_reason": "",
                                            "flag_resolution": f"{existing.flag_reason}: {evidence.strip()}"})
        self._scores[component] = score
        self._save()
        return score

    def audit_history(self, component: str | None = None) -> list[dict[str, Any]]:
        """The logged audits, oldest first, optionally for one component. Lines that do not parse are skipped."""
        if not self._audit_log.exists():
            return []
        found = []
        for line in self._audit_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and (component is None or entry.get("component") == component):
                found.append(entry)
        return found


__all__ = ["PromotionRefused", "ScorecardStore"]
