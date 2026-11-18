# SPDX-License-Identifier: Apache-2.0
"""Whether the loop learned from a failure: which signals can be trusted, which mistakes came back, and which predictions came true.

Where feedback is delayed or wrong, experience can teach the wrong lesson, so an immediate signal counts only as far as it agreed with the
truth that came later. These are plain counts and exact bounds over what a cycle recorded. A model may propose a lesson, and it never grades
one. Nothing here is stored: a cycle's row holds its lessons, so the loop's own log is the record.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from g6.core.binomial import lower_bound

#: The closed list of ``g6.research.ahi.contracts``, which product code may not import; a test holds the two equal.
ROOT_CAUSES = (
    "misstated_standard", "capability_wall", "wrong_algorithm", "missing_component", "unreachable_component",
    "not_activated", "ineffective_component", "verification_gap", "compromised_validator", "bad_remediation",
    "budget_or_environment", "distribution_shift_or_gaming",
)
HELD, FAILED, OPEN = "held", "failed", "open"
ALPHA = 0.05


@dataclass(frozen=True)
class Reading:
    """One immediate signal and whether the truth that came later agreed. ``group``: the unit of evidence, a bug on coding work."""

    signal: str
    kind: str
    group: str
    agreed: bool


@dataclass(frozen=True)
class Calibration:
    """How one signal did on one kind of task. ``lower`` is the exact lower bound by group, since readings of one group are not independent."""

    signal: str
    kind: str
    agreed: int
    readings: int
    groups: int
    groups_agreed: int
    lower: float

    @property
    def rate(self) -> float:
        return self.agreed / self.readings


def calibrate(readings: Iterable[Reading]) -> tuple[Calibration, ...]:
    """A row per signal and kind. A group counts as agreeing only when most of its readings did."""
    cells: dict[tuple[str, str], dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for reading in readings:
        cells[reading.signal, reading.kind][reading.group].append(reading.agreed)
    rows = []
    for (signal, kind), groups in sorted(cells.items()):
        said = [x for group in groups.values() for x in group]
        most = sum(sum(group) * 2 > len(group) for group in groups.values())
        rows.append(Calibration(signal, kind, sum(said), len(said), len(groups), most, lower_bound(most, len(groups), ALPHA)))
    return tuple(rows)


def trusted(table: Sequence[Calibration], signal: str, kind: str, floor: float) -> bool:
    """Whether a signal may be relied on for this kind of task. One never measured there is not."""
    return any(row.signal == signal and row.kind == kind and row.readings > 0 and row.lower >= floor for row in table)


def gap(tuned: Calibration, unseen: Calibration) -> float:
    """How much of a signal's accuracy did not carry to unseen tasks: how wicked this class is, measured and not assumed."""
    return tuned.rate - unseen.rate


@dataclass(frozen=True)
class Lesson:
    """What one failure taught. A failure that leaves no check of its own has taught nothing yet, so a lesson without one is refused.
    The shape follows ``FailureCase`` in ``g6.research.ahi.contracts``."""

    lesson_id: str
    generation: int
    root_cause: str
    case_ids: tuple[str, ...]
    hypothesis: str
    alternatives: tuple[str, ...]
    check: str
    scope: str

    def __post_init__(self) -> None:
        if self.root_cause not in ROOT_CAUSES:
            raise ValueError(f"root_cause must be one of {', '.join(ROOT_CAUSES)}")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("generation must be a whole number of at least 0")
        for name in ("lesson_id", "hypothesis", "check", "scope"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        for name in ("case_ids", "alternatives"):
            value = getattr(self, name)
            if not isinstance(value, tuple | list) or not value or not all(isinstance(x, str) and x.strip() for x in value):
                raise ValueError(f"{name} must be a non-empty tuple or list of texts")


def repeats(lessons: Iterable[Lesson], seen: Iterable[tuple[int, str, str]]) -> dict[str, dict[str, int]]:
    """Each mistake that came back after its first lesson. ``seen``: (generation, root cause, case). A mistake with no lesson is not
    here: it has not been learned yet, so it cannot be repeated. ``same_cases``: the lesson's own cases failing again."""
    first: dict[str, Lesson] = {}
    for lesson in sorted(lessons, key=lambda x: x.generation):
        first.setdefault(lesson.root_cause, lesson)
    later: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for generation, cause, case in seen:
        if cause in first and generation > first[cause].generation:
            later[cause].append((generation, case))
    return {cause: {"after_lesson": len(rows), "same_cases": sum(case in first[cause].case_ids for _, case in rows),
                    "generations": len({generation for generation, _ in rows})} for cause, rows in later.items()}


def stuck(found: Mapping[str, Mapping[str, int]], *, limit: int = 2) -> list[str]:
    """The mistakes that came back in at least ``limit`` generations after their lesson: the lesson is wrong or never reached the step."""
    return sorted(cause for cause, row in found.items() if row["generations"] >= limit)


@dataclass(frozen=True)
class Prediction:
    """A diagnosis written down before acting: what it should move, from where, to where."""

    prediction_id: str
    measure: str
    before: float
    expected: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.before) and math.isfinite(self.expected)):
            raise ValueError("a prediction needs finite numbers")
        if self.expected == self.before:
            raise ValueError("a prediction needs a direction: expected equals before")


def score(prediction: Prediction, observed: float | None) -> str:
    """``held``, ``failed``, or ``open`` while no usable outcome is in."""
    if observed is None or not math.isfinite(observed):
        return OPEN
    rising = prediction.expected > prediction.before
    return HELD if (observed >= prediction.expected if rising else observed <= prediction.expected) else FAILED


def hit_rate(scores: Iterable[str]) -> float | None:
    """The share of decided predictions that held, which is how good the loop's diagnoses are; None until one is decided."""
    decided = [s for s in scores if s != OPEN]
    return decided.count(HELD) / len(decided) if decided else None


__all__ = ["FAILED", "HELD", "OPEN", "ROOT_CAUSES", "Calibration", "Lesson", "Prediction", "Reading", "calibrate", "gap", "hit_rate", "repeats", "score",
           "stuck", "trusted"]
