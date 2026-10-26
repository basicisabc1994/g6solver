# SPDX-License-Identifier: Apache-2.0
"""Level 2: root causes in the meta-harness's own method, and what to change (frontier design §4.3).

``diagnose_method`` reads tuning-run records only (``TuningRun`` objects or their ledger rows: dev and selection
counts, the confirmation's aggregates, the decision) and names causes, each with the counts behind it:

* ``underpowered``: the champion won its races but the confirmation refused it narrowly (p within 3× its level);
* ``exhausted``: most cycles after the first found no better champion;
* ``repetitive``: most reflected changes repeated a profile already tried;
* ``uncombined``: no candidate that changes two or more fields ever raced;
* ``misallocated``: on average, most selection cases went to candidates dropped at the first block.

``edits_for`` maps a cause to method edits, the way ``recovery`` maps a task's causes to G6 components. An edited
method is tested like any change: ``compare_methods`` compares it with the current method over paired tuning runs (a
sign test across runs and a bootstrap interval), and a person approves it. This module and the reflection prompt are
code: no method version can edit them ("no level 3").
"""
from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from g6.metaharness.method import Method, TuningRun
from g6.reasoning.compare import sign_test_p

CAUSES = ("underpowered", "exhausted", "repetitive", "uncombined", "misallocated")
_CLOSE = {"G6_E_META_NO_GAIN", "G6_E_META_INSUFFICIENT_EVIDENCE"}


@dataclass(frozen=True)
class MethodCause:
    code: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class MethodDiagnosis:
    causes: list[MethodCause]
    runs: int


def _row(run: TuningRun | dict[str, Any]) -> dict[str, Any]:
    return run.to_dict() if isinstance(run, TuningRun) else dict(run)


def diagnose_method(runs: Sequence[TuningRun | dict[str, Any]]) -> MethodDiagnosis:
    rows = [_row(r) for r in runs]
    causes: list[MethodCause] = []
    close = 0
    for row in rows:
        confirmation = row.get("confirmation") or {}
        decision, success = row.get("decision") or {}, confirmation.get("success") or {}
        level = confirmation.get("level", success.get("boundary", 0.05))     # the level the gate itself used
        if (decision.get("code") in _CLOSE and success.get("wins", 0) > success.get("losses", 0)
                and success.get("p_one_sided", 1.0) <= 3 * level):
            close += 1
    if close:
        causes.append(MethodCause("underpowered", f"{close} confirmation(s) refused a champion narrowly after it won "
                                  "its races", {"runs": close}))
    later = [c for row in rows for c in row.get("cycles") or [] if c.get("cycle", 1) >= 2]
    stale = sum(not c.get("new_champion") for c in later)
    if later and stale / len(later) > 0.5:
        causes.append(MethodCause("exhausted", f"{stale} of {len(later)} later cycles found no better champion",
                                  {"stale_cycles": stale, "later_cycles": len(later)}))
    attempts = duplicates = 0
    for row in rows:
        stats = [c.get("strategies", {}).get("reflect") or {} for c in row.get("cycles") or []]
        attempts += max((s.get("attempts") or 0 for s in stats), default=0)
        duplicates += max((s.get("duplicates") or 0 for s in stats), default=0)
    if attempts and duplicates / attempts > 0.5:
        causes.append(MethodCause("repetitive", f"{duplicates} of {attempts} reflected changes repeated a profile "
                                  "already tried", {"attempts": attempts, "duplicates": duplicates}))
    raced = [r for row in rows for c in row.get("cycles") or [] for r in c.get("raced") or []]
    if raced and not any(r.get("fields", 0) >= 2 for r in raced):
        causes.append(MethodCause("uncombined", f"none of {len(raced)} raced candidates changed two or more fields",
                                  {"raced": len(raced)}))
    shares = []
    for row in rows:
        for c in row.get("cycles") or []:
            total = sum(r.get("n", 0) for r in c.get("raced") or [])
            if total:
                shares.append(sum(r.get("n", 0) for r in c.get("raced") or [] if r.get("dropped_at_block") == 1)
                              / total)
    if shares and statistics.fmean(shares) > 0.5:
        causes.append(MethodCause("misallocated", "on average most selection cases went to candidates dropped at the "
                                  "first block", {"mean_share": round(statistics.fmean(shares), 3)}))
    return MethodDiagnosis(causes, len(rows))


def edits_for(cause: str, method: Method) -> list[Method]:
    """The method edits that answer ``cause``; each is a new ``Method`` to test, never applied here."""
    m = method
    change: dict[str, Any] = {
        "underpowered": {"select_budget": int(m.select_budget * 1.5)},
        "exhausted": {"reflect_calls": min(12, m.reflect_calls + 2), "use_edits": True},
        "repetitive": {"merge_every": 2 if m.merge_every in (0, 2) else max(2, m.merge_every - 1),
                       "reflect_calls": min(12, m.reflect_calls + 1)},
        "uncombined": {"merge_every": 2, "use_edits": True},
        "misallocated": {"race_block": max(5, m.race_block // 2) if m.race_block > 5 else m.race_block + 5},
    }[cause]
    edited = Method.model_validate({**m.model_dump(), **change})
    return [edited] if edited.digest() != m.digest() else []     # already at its limits: nothing new to test


def compare_methods(pairs: Sequence[tuple[float, float]], *, clusters: Sequence[Any] | None = None, seed: int = 0,
                    resamples: int = 2000) -> dict[str, Any]:
    """``pairs``: per paired tuning run, (current method's eval success, candidate method's). Accept the candidate when
    it wins more runs than it loses, the one-sided sign test is at or below 0.05, and the bootstrap 95% interval of
    the mean difference lies above 0. With ``clusters`` (one label per pair, such as its workflow) the bootstrap
    resamples whole clusters, since runs on one workflow are not independent (spec §4.3, §5)."""
    diffs = [b - a for a, b in pairs]
    wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    p = sign_test_p(wins, losses)
    rng = random.Random(seed)
    groups: dict[Any, list[float]] = {}
    for label, d in zip(clusters if clusters is not None else range(len(diffs)), diffs):
        groups.setdefault(label, []).append(d)
    blocks = list(groups.values())

    def resample() -> float:
        drawn = [d for block in rng.choices(blocks, k=len(blocks)) for d in block]
        return statistics.fmean(drawn)

    means = sorted(resample() for _ in range(resamples)) if diffs else [0.0]
    ci = (means[int(0.025 * len(means))], means[min(len(means) - 1, int(0.975 * len(means)))])
    mean = statistics.fmean(diffs) if diffs else 0.0
    return {"pairs": len(diffs), "wins": wins, "losses": losses, "p_one_sided": round(p, 6),
            "mean_diff": round(mean, 6), "ci95": (round(ci[0], 6), round(ci[1], 6)),
            "accept": wins > losses and p <= 0.05 and ci[0] > 0}
