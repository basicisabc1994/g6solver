# SPDX-License-Identifier: Apache-2.0
"""Competence and case retrieval over ECHA episodes (audits 015 and 026 §11). Research code, default OFF.

From the archive's experience loop: a Beta posterior over a task family's outcomes, computed from admissible episodes
only, and advice to grant or refuse autonomy against a frozen policy. An episode is admissible when its stored record is
intact (``verify_episode_body``), its own header says its oracle was independent, the oracle is one the policy admits,
and its outcome is decisive (``verified`` is a success, ``blocked-escalated`` a failure). What the caller asserts about
provenance counts for nothing; only what the stored episodes say counts (XL-002, XL-010).

From the Soar case bank: an internal-consistency pre-check on a success claim, and an owner-scoped top-k retrieval over
the caller's case texts through a retriever the caller supplies, with a score floor and the backend named (SOAR-001:
no pseudo-embedding fallback; with BM25 and no word in common there is no neighbour).

Advice is advice: it is never a completion state, and nothing here grants anything by itself.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from .episodes import EpisodeRecord, verify_episode_body
from .flag import require_research
from .records import digest

#: The weakest gate a policy may set; a looser one is refused rather than used (XL-003).
MIN_THRESHOLD, MIN_EPISODES = 0.5, 10


@dataclass(frozen=True)
class CompetencePolicy:
    threshold: float = 0.9          # the lower credible bound the success rate must reach
    min_episodes: int = 30          # admissible episodes needed before any advice to grant
    margin: float = 0.0             # added to the threshold
    credible: float = 0.95          # one-sided credibility of the lower bound
    admissible_oracles: frozenset[str] = frozenset()   # oracle versions admitted; empty admits any independent one

    def __post_init__(self) -> None:
        if not (MIN_THRESHOLD <= self.threshold < 1 and 0 <= self.margin < 1 and self.threshold + self.margin < 1):
            raise ValueError(f"threshold must be in [{MIN_THRESHOLD}, 1) and threshold + margin below 1")
        if isinstance(self.min_episodes, bool) or not isinstance(self.min_episodes, int) or self.min_episodes < MIN_EPISODES:
            raise ValueError(f"min_episodes must be a whole number of at least {MIN_EPISODES}")
        if not 0.5 <= self.credible < 1:
            raise ValueError("credible must be in [0.5, 1)")

    @property
    def digest(self) -> str:
        return digest({"threshold": self.threshold, "min_episodes": self.min_episodes, "margin": self.margin,
                       "credible": self.credible, "admissible_oracles": sorted(self.admissible_oracles)})


@dataclass(frozen=True)
class CompetenceEstimate:
    task_family: str
    successes: int
    failures: int
    excluded: Mapping[str, int]
    lower_bound: float
    mean: float
    policy_digest: str

    @property
    def admissible_count(self) -> int:
        return self.successes + self.failures

    @property
    def excluded_count(self) -> int:
        return sum(self.excluded.values())


@dataclass(frozen=True)
class Advice:
    grant: bool
    reason: str
    policy_digest: str
    checked: tuple[str, ...] = field(default=("record_intact", "oracle_independent", "oracle_admitted",
                                              "outcome_decisive"))


def _betacf(a: float, b: float, x: float) -> float:
    """The continued fraction for the regularised incomplete beta function (Numerical Recipes 6.4)."""
    tiny, qab, qap, qam = 1e-300, a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1 + aa * d
            d = 1 / (d if abs(d) > tiny else tiny)
            c = 1 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1) < 3e-12:
            break
    return h


def _beta_cdf(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    return front * _betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - front * _betacf(b, a, 1 - x) / b


def _lower_bound(a: float, b: float, credible: float) -> float:
    """The ``1 - credible`` quantile of Beta(a, b), by bisection."""
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _beta_cdf(mid, a, b) < 1 - credible else (lo, mid)
    return lo


def _exclusion(record: EpisodeRecord, task_family: str, policy: CompetencePolicy) -> str:
    if record.header.get("task_family") != task_family:
        return "other_task_family"
    if verify_episode_body(record.to_dict()):
        return "record_not_intact"
    if record.independent_oracle_access is not True:
        return "oracle_not_independent"
    if policy.admissible_oracles and record.header.get("oracle_version") not in policy.admissible_oracles:
        return "oracle_not_admitted"
    if record.outcome_state not in ("verified", "blocked-escalated"):
        return "outcome_not_decisive"
    return ""


def competence(episodes: Iterable[EpisodeRecord], *, task_family: str, policy: CompetencePolicy) -> CompetenceEstimate:
    """A Beta(1 + successes, 1 + failures) posterior over the admissible episodes of ``task_family``."""
    require_research()
    excluded: Counter[str] = Counter()
    successes = failures = 0
    for record in episodes:
        why = _exclusion(record, task_family, policy)
        if why:
            excluded[why] += 1
        elif record.outcome_state == "verified":
            successes += 1
        else:
            failures += 1
    a, b = 1 + successes, 1 + failures
    return CompetenceEstimate(task_family, successes, failures, dict(excluded),
                              round(_lower_bound(a, b, policy.credible), 6), a / (a + b), policy.digest)


def autonomy_advice(estimate: CompetenceEstimate, policy: CompetencePolicy) -> Advice:
    """Advice to grant autonomy for the family, or the reason not to. The estimate must be under this same policy."""
    require_research()
    if estimate.policy_digest != policy.digest:
        return Advice(False, "the estimate was made under another policy", policy.digest)
    if estimate.admissible_count < policy.min_episodes:
        return Advice(False, f"{estimate.admissible_count} admissible episode(s), {policy.min_episodes} needed "
                             f"({estimate.excluded_count} excluded)", policy.digest)
    bar = policy.threshold + policy.margin
    if estimate.lower_bound < bar:
        return Advice(False, f"the success rate's lower bound {estimate.lower_bound:.3f} is under {bar:.3f}",
                      policy.digest)
    return Advice(True, f"lower bound {estimate.lower_bound:.3f} >= {bar:.3f} over {estimate.admissible_count} "
                        "admissible episodes; advisory only", policy.digest)


@dataclass(frozen=True)
class ConsistencyVerdict:
    consistent: bool
    problems: tuple[str, ...] = ()


def success_claim_consistent(record: EpisodeRecord) -> ConsistencyVerdict:
    """Is a ``verified`` episode internally consistent? Necessary, never sufficient (it reads the record, not the world)."""
    require_research()
    problems = list(verify_episode_body(record.to_dict()))
    kinds = [e.get("kind") for e in record.events]
    if record.outcome_state == "verified":
        if "action" not in kinds and "tool_result" not in kinds:
            problems.append("a success with no recorded step")
        if not record.candidate_artifact_hash:
            problems.append("a success with no artifact")
        if "incident" in kinds:
            problems.append("a success after a recorded incident")
        if not record.evidence_refs:
            problems.append("a success that cites no evidence")
    return ConsistencyVerdict(not problems, tuple(problems))


@dataclass(frozen=True)
class ScoredEpisode:
    episode_id: str
    score: float
    score_scale: str
    backend: str


def similar_episodes(episodes: Iterable[EpisodeRecord], query: str, *, texts: Mapping[str, str], owner: str,
                     make_retriever: Callable[[Mapping[str, str]], object], k: int = 5, min_score: float = 1.0,
                     outcome_states: tuple[str, ...] = ("verified",)) -> tuple[list[ScoredEpisode], dict[str, int]]:
    """The ``k`` episodes of ``owner`` (their ``repository_or_group_id``) whose case text best matches ``query``, at or
    above ``min_score``, with the counts excluded and why. ``texts`` maps episode id to the caller's case text;
    ``make_retriever`` builds a ``g6.grounding`` retriever from it (e.g. ``TextRetriever.from_texts``), since the kernel
    imports nothing from the product."""
    require_research()
    excluded: Counter[str] = Counter()
    pool: dict[str, str] = {}
    for record in episodes:
        if record.header.get("repository_or_group_id") != owner:
            excluded["other_owner"] += 1
        elif record.outcome_state not in outcome_states:
            excluded["outcome_not_selected"] += 1
        elif not success_claim_consistent(record).consistent and record.outcome_state == "verified":
            excluded["inconsistent_success"] += 1
        elif not texts.get(record.episode_id, "").strip():
            excluded["no_case_text"] += 1
        else:
            pool[record.episode_id] = texts[record.episode_id]
    if not pool:
        return [], dict(excluded)
    retriever = make_retriever(pool)
    backend = type(retriever).__name__
    best: dict[str, ScoredEpisode] = {}
    for passage in retriever.search(query, max(k * 4, k)):
        if passage.score < min_score:
            excluded["below_score_floor"] += 1
            continue
        current = best.get(passage.source)
        if current is None or passage.score > current.score:
            best[passage.source] = ScoredEpisode(passage.source, round(passage.score, 4), passage.score_scale, backend)
    ranked = sorted(best.values(), key=lambda s: (-s.score, s.episode_id))[:k]
    return ranked, dict(excluded)


__all__ = ["MIN_EPISODES", "MIN_THRESHOLD", "Advice", "CompetenceEstimate", "CompetencePolicy", "ConsistencyVerdict",
           "ScoredEpisode", "autonomy_advice", "competence", "similar_episodes", "success_claim_consistent"]
