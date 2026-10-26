# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Curated, unmeasured failure-mode skills; data never grants execution authority."""
import re
from dataclasses import dataclass

# Pure data: this module imports nothing from the pipeline (audit 009 RP-013); intake re-exports these.
REPLAY_ANSWER = "identical_nonadjacent_in_memory"
REPLAY_REQUIREMENT = ("Make this Python event handler apply each stable event ID at most once, "
                      "even when another event arrives between identical retries. Preserve existing tests.")
#: The one supported phrasing of the nonadjacent replay requirement, matched on lower-cased, whitespace-collapsed
#: text; the premortem's constructor and the workforce readiness check share it (audit 009 RP-010).
REPLAY_REQUIREMENT_FORM = re.compile(
    r"make [a-z ]{1,80} apply each stable event id at most once, even when another event "
    r"arrives between identical retries\.(?: preserve [a-z ,]{1,180}\.)?")


def states_replay_requirement(text: str) -> bool:
    """``text`` is the supported phrasing of the nonadjacent replay requirement."""
    return REPLAY_REQUIREMENT_FORM.fullmatch(" ".join(text.lower().split())) is not None


@dataclass(frozen=True)
class Interpretation:
    key: str
    label: str
    requirement: str
    relation: str | None
    missing_capability: str = ""


@dataclass(frozen=True)
class FailureModeSkill:
    failure_id: str
    version: str
    family: str
    owner: str
    triggers: tuple[str, ...]
    interpretations: tuple[Interpretation, ...]
    consequence: str
    false_positives: tuple[str, ...]
    will_check: str
    decision: str
    cannot_verify: str
    observed_performance: str = "unmeasured"
    retired: bool = False


# COPY: The exact adjacent guarantee frozen into the accepted standard.
ADJACENT_REQUIREMENT = "Make this Python event handler ignore an identical adjacent duplicate event. Preserve existing tests."
SKILLS = (FailureModeSkill(
    failure_id="event-replay-semantics.v1", version="1", family="intent_substitution", owner="g6",
    triggers=("idempotent", "idempotency", "duplicate", "duplicates", "dedupe", "deduplicate", "retry", "retries",
              "replay", "at-least-once", "exactly-once"),
    interpretations=(
        # COPY: Adjacent-only choice and the existing nonadjacent requirement are intentionally distinct.
        Interpretation("adjacent_duplicate", "Identical adjacent duplicate", ADJACENT_REQUIREMENT, "adjacent-replay.v1"),
        # COPY: Preserve the established answer key and requirement verbatim.
        Interpretation(REPLAY_ANSWER, "Identical retry after a different event, in memory", REPLAY_REQUIREMENT, "event-replay-v1"),
        # COPY: Uncheckable guarantees remain explicit obligations, never implied coverage.
        Interpretation("concurrent_retry", "Concurrent retries",
                       "Make this event handler apply each stable event ID at most once under concurrent retries.",
                       None, "no concurrency harness"),
        # COPY: Restart durability is outside the in-memory relation.
        Interpretation("restart_persistence", "Retries after restart",
                       "Make this event handler preserve duplicate prevention across restart and persistence recovery.",
                       None, "no restart or persistence harness"),
        # COPY: External effects need their own ledger check.
        Interpretation("external_side_effect", "Duplicate external side effects",
                       "Make this event handler prevent duplicate external side effects for each stable event ID.",
                       None, "no external-effect ledger check"),
    ),
    # COPY: Why this ambiguity matters, without claiming measured incidence.
    consequence="An identical retry can repeat an effect outside the guarantee the tests cover.",
    # COPY: Lexical activation is bounded and can include non-event work.
    false_positives=("Retry wording may describe transport policy rather than event effects.",),
    # COPY: Preview of the checks this skill can add.
    will_check="What I will check: the existing tests and the selected available replay relation.",
    # COPY: The semantic decision needed to choose this skill's obligation.
    decision=("One decision I need: must an identical retry leave state unchanged only immediately, "
              "after a different event, concurrently, after restart, or for external effects?"),
    # COPY: Explicit limits of this skill's available checks.
    cannot_verify="Scope I cannot verify: concurrent deliveries, restart persistence and external effects.",
),)


def relevant(objective: str) -> tuple[FailureModeSkill, ...]:
    """Match whole lower-case objective terms; retired skills never propose questions."""
    task = objective.lower()
    return tuple(skill for skill in SKILLS if not skill.retired and any(
        re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", task) for term in skill.triggers))
