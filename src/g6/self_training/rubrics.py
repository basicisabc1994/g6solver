# SPDX-License-Identifier: Apache-2.0
"""Shape rubrics: evaluate a component's output against structured assertions, and turn an audit into a tier.

Structured assertions, not a model as judge. They check the output's *shape*; a high score is not a quality
measurement (audit 008, ST-010). The caller invokes the component itself and passes its output to
:func:`audit_output`; nothing here invokes, imports or registers a component.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from g6.self_training.schema import (
    PUBLICATION_THRESHOLD,
    TIER_NAMES,
    UNREVIEWED_TIER_CAP,
    AssertionResult,
    AuditResult,
    ComponentRubric,
    RubricAssertion,
    RubricScore,
)

_MISSING = "<missing>"
_TYPES: dict[str, type] = {"str": str, "int": int, "float": float, "list": list, "dict": dict, "bool": bool}


def _resolve(output: Any, path: str) -> tuple[Any, bool]:
    """The value at a dot-path into nested mappings or attributes, and whether it was found."""
    current = output
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None, False
            current = current[part]
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None, False
    return current, True


def _holds(check: str, value: Any, expected: Any) -> bool:
    if check == "not_empty":
        return value is not None and bool(value)
    if check in ("gt", "gte", "lt", "lte"):
        a, b = float(value), float(expected)
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[check]
    if check == "eq":
        return value == expected
    if check == "contains":
        return expected in str(value)
    if check == "type_is":
        if str(expected) not in _TYPES:
            raise ValueError(f"type_is names an unknown type {expected!r}")   # the archive silently checked str
        return isinstance(value, _TYPES[str(expected)])
    if check == "length_gte":
        return len(value) >= int(expected)
    if check == "in_set":
        return value in (expected if isinstance(expected, list) else [expected])
    raise ValueError(f"unknown check type: {check}")


def evaluate_assertion(assertion: RubricAssertion, output: Any) -> AssertionResult:
    """Apply one assertion to ``output``. An assertion that cannot be evaluated fails, with the reason in ``error``."""
    value, found = _resolve(output, assertion.field)
    if assertion.check == "exists":
        return AssertionResult(assertion=assertion, passed=found, actual_value=value if found else _MISSING)
    if not found:
        return AssertionResult(assertion=assertion, passed=False, actual_value=_MISSING,
                               error=f"field {assertion.field!r} not found in the output")
    try:
        passed = _holds(assertion.check, value, assertion.expected)
    except Exception as exc:  # noqa: BLE001 - an assertion that cannot be evaluated is a failed assertion
        return AssertionResult(assertion=assertion, passed=False, actual_value=value, error=str(exc))
    return AssertionResult(assertion=assertion, passed=bool(passed), actual_value=value)


def evaluate_rubric(rubric: ComponentRubric, output: Any) -> RubricScore:
    """The weighted share of shape assertions ``output`` meets. A rubric with no assertions, or whose weights sum to
    zero, is ``unevaluable`` with score 0.0 (audit 008, ST-010: the archive scored both 1.0)."""
    if not rubric.assertions:
        return RubricScore(evaluable=False, reviewed=rubric.reviewed, reason="the rubric has no assertions")
    results = [evaluate_assertion(a, output) for a in rubric.assertions]
    total = sum(a.weight for a in rubric.assertions)
    if total <= 0:
        return RubricScore(evaluable=False, reviewed=rubric.reviewed, results=results,
                           reason="every assertion has weight 0")
    earned = sum(r.assertion.weight for r in results if r.passed)
    return RubricScore(evaluable=True, score=earned / total, reviewed=rubric.reviewed, results=results)


def classify_tier(*, invoke_ok: bool, invoke_error: str = "", rubric_score: RubricScore | None = None) -> int:
    """The tier an audit earns. Never T6 (only a promotion sets it). Without a reviewed rubric that evaluates the
    output, at most T3 (audit 008, ST-010: the archive gave T4 with no rubric and T5 on an empty one)."""
    if not invoke_ok:
        err = invoke_error.lower()
        if "not found" in err or "no aiblock" in err:
            return 0
        if "cannot import" in err:
            return 1
        return 2
    if rubric_score is None or not rubric_score.evaluable:
        return UNREVIEWED_TIER_CAP
    s = rubric_score.score
    tier = 5 if s >= 0.8 else 4 if s >= 0.5 else 3 if s >= 0.2 else 2
    return tier if rubric_score.reviewed else min(tier, UNREVIEWED_TIER_CAP)


def audit_output(component: str, op: str = "infer", *, output: Any = None, invoke_ok: bool = True,
                 invoke_error: str = "", rubric: ComponentRubric | None = None,
                 timestamp: float | None = None) -> AuditResult:
    """Score one invocation the caller already ran: ``output`` (or ``invoke_ok=False`` with ``invoke_error``)
    against ``rubric``, into an :class:`AuditResult` for :class:`~g6.self_training.persistence.ScorecardStore`."""
    scored = evaluate_rubric(rubric, output) if invoke_ok and rubric is not None else None
    tier = classify_tier(invoke_ok=invoke_ok, invoke_error=invoke_error, rubric_score=scored)
    return AuditResult(
        component=component, op=op, tier=tier, tier_name=TIER_NAMES[tier],
        passed_threshold=tier >= PUBLICATION_THRESHOLD,
        score=scored.score if scored else 0.0,
        evaluable=bool(scored and scored.evaluable),
        rubric_reviewed=bool(scored and scored.reviewed),
        assertion_results=list(scored.results) if scored else [],
        error=invoke_error,
        timestamp=time.time() if timestamp is None else timestamp,
    )


class RubricRegistry:
    """Rubrics by component, registered in code or loaded from JSON files."""

    def __init__(self) -> None:
        self._rubrics: dict[str, list[ComponentRubric]] = {}
        #: ``"<file>: <error>"`` for each file :meth:`load_from_dir` skipped.
        self.load_errors: list[str] = []

    def register(self, rubric: ComponentRubric) -> None:
        self._rubrics.setdefault(rubric.component, []).append(rubric)

    def get(self, component: str, op: str | None = None) -> list[ComponentRubric]:
        found = self._rubrics.get(component, [])
        return [r for r in found if op is None or r.op == op]

    def list_components(self) -> list[str]:
        return sorted(self._rubrics)

    def load_from_dir(self, rubric_dir: str | Path) -> int:
        """Register every rubric in the directory's ``*.json`` files (an object or a list of them); returns how many.
        A file that does not parse is skipped and named in :attr:`load_errors`."""
        directory = Path(rubric_dir)
        if not directory.is_dir():
            return 0
        count = 0
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rubrics = [ComponentRubric.model_validate(item) for item in (data if isinstance(data, list) else [data])]
            except Exception as exc:  # noqa: BLE001 - one bad file does not stop the others loading
                self.load_errors.append(f"{path.name}: {exc}")
                continue
            for rubric in rubrics:
                self.register(rubric)
            count += len(rubrics)
        return count

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {name: [r.model_dump() for r in found] for name, found in self._rubrics.items()}


__all__ = ["RubricRegistry", "audit_output", "classify_tier", "evaluate_assertion", "evaluate_rubric"]
