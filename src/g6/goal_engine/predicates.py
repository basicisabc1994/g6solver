# SPDX-License-Identifier: Apache-2.0
"""Predicates: named checks on an executor's ``Observation``, and the registry a spec's ``predicate`` names.

A factory takes a control's ``params`` and returns a predicate, or raises ``ValueError`` when the params are wrong. A
predicate takes the ``Observation`` and returns ``"pass"``, ``"fail"`` or ``"unknown"``; a bool is read as pass or
fail and a ``(verdict, reason)`` pair adds a reason. Any other return value, or an exception, is ``unknown``, and an
unknown is never a pass (audit 013, GE-005).

Built-ins (each reads only the observation):

* ``resource_limit`` — params are ``ResourceBoundsSpec`` limits; fails when a reported usage figure is over its limit,
  unknown when a limited figure is not reported.
* ``metric_above`` / ``metric_below`` — params ``metric`` and ``threshold``; unknown when the metric is not reported
  (the archive passed, and read the metric off the spec, where it never was).
* ``csf_hazard`` — params ``max_prob`` and optionally ``action``; reads ``Observation.action`` against ``HAZARDS``.
  An action with no estimate fails, whatever ``max_prob`` says (the archive gave it 0.0, which passed). With
  ``action`` set, a known action other than that one passes as out of scope.

The archive's ``always_true`` and ``always_pause`` are not built in: neither reads anything, so neither can be made to
read an observation, and ``always_pause`` was ``always_true`` (GE-005). A breakpoint needs no predicate.
"""
from __future__ import annotations

import inspect
import math
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, Literal

from pydantic import ValidationError

from g6.goal_engine.codes import CONTROL_INVALID, PREDICATE_ERROR, PREDICATE_REGISTRATION, PREDICATE_UNKNOWN
from g6.goal_engine.schema import Observation, ResourceBoundsSpec

Verdict = Literal["pass", "fail", "unknown"]
VERDICTS: tuple[str, ...] = ("pass", "fail", "unknown")
Predicate = Callable[[Observation], Any]
Factory = Callable[[Mapping[str, Any]], Predicate]

#: Fixed per-action hazard estimates carried from the archive. They are not calibrated against outcomes.
HAZARDS: Mapping[str, float] = MappingProxyType({
    "llm_call": 0.05, "file_write": 0.01, "code_execute": 0.10, "external_api": 0.08, "rollback": 0.001,
})


class ControlError(ValueError):
    """A control that cannot be built: its stable code and the reason in words."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


def _only(params: Mapping[str, Any], allowed: tuple[str, ...], name: str) -> None:
    extra = sorted(set(params) - set(allowed))
    if extra:
        raise ValueError(f"{name} does not take {extra}; it takes {list(allowed)}")


def _finite(params: Mapping[str, Any], key: str, name: str) -> float:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} needs a finite number {key!r}")
    return float(value)


def usage_within(limits: Mapping[str, float]) -> Predicate:
    """A predicate that fails when a reported usage figure is over its limit and is unknown when one is missing."""
    limits = dict(limits)

    def predicate(observation: Observation) -> tuple[str, str]:
        over = [f"{key} {observation.usage[key]:g} > {limit:g}" for key, limit in limits.items()
                if key in observation.usage and observation.usage[key] > limit]
        missing = [key for key in limits if key not in observation.usage]
        if over:
            return "fail", "over the limit: " + ", ".join(over)
        if missing:
            return "unknown", "usage not reported: " + ", ".join(missing)
        return "pass", "within " + ", ".join(f"{key} <= {limit:g}" for key, limit in limits.items())

    return predicate


def _resource_limit(params: Mapping[str, Any]) -> Predicate:
    limits = ResourceBoundsSpec.model_validate(dict(params)).limits()
    if not limits:
        raise ValueError("resource_limit needs at least one limit, for example max_tokens_per_hour")
    return usage_within(limits)


def _metric(params: Mapping[str, Any], name: str, above: bool) -> Predicate:
    _only(params, ("metric", "threshold"), name)
    metric = params.get("metric")
    if not isinstance(metric, str) or not metric.strip():
        raise ValueError(f"{name} needs a 'metric' name")
    threshold = _finite(params, "threshold", name)
    sign = ">=" if above else "<="

    def predicate(observation: Observation) -> tuple[str, str]:
        value = observation.metrics.get(metric)
        if value is None:
            return "unknown", f"metric {metric!r} not reported"
        ok = value >= threshold if above else value <= threshold
        return ("pass" if ok else "fail"), f"{metric} = {value:g}, required {sign} {threshold:g}"

    return predicate


def _metric_above(params: Mapping[str, Any]) -> Predicate:
    return _metric(params, "metric_above", above=True)


def _metric_below(params: Mapping[str, Any]) -> Predicate:
    return _metric(params, "metric_below", above=False)


def _csf_hazard(params: Mapping[str, Any]) -> Predicate:
    _only(params, ("max_prob", "action"), "csf_hazard")
    max_prob = _finite(params, "max_prob", "csf_hazard")
    if not 0.0 <= max_prob <= 1.0:
        raise ValueError("csf_hazard max_prob must be between 0 and 1")
    scoped = params.get("action")
    # A misspelt scope would make every real action "out of scope" and pass, so it must name a known action.
    if scoped is not None and (not isinstance(scoped, str) or scoped not in HAZARDS):
        raise ValueError(f"csf_hazard action {scoped!r} has no hazard estimate; known: {sorted(HAZARDS)}")

    def predicate(observation: Observation) -> tuple[str, str]:
        action = observation.action
        if action is None:
            return "unknown", "no action reported"
        if action not in HAZARDS:  # audit 013, GE-005: unknown is not safe
            return "fail", f"no hazard estimate for action {action!r}; an unknown action blocks"
        if scoped is not None and action != scoped:
            return "pass", f"action {action!r} is outside this guardrail's scope ({scoped!r})"
        hazard = HAZARDS[action]
        return ("pass" if hazard <= max_prob else "fail"), f"{action} hazard {hazard:g}, max_prob {max_prob:g}"

    return predicate


_BUILTIN: dict[str, Factory] = {
    "resource_limit": _resource_limit,
    "metric_above": _metric_above,
    "metric_below": _metric_below,
    "csf_hazard": _csf_hazard,
}
_PREDICATES: dict[str, Factory] = dict(_BUILTIN)


def register_predicate(name: str, factory: Factory, *, override: bool = False) -> None:
    """Register a predicate factory under ``name``.

    A built-in or an existing name is refused unless ``override=True``. The factory must take exactly one required
    positional argument, the params. It is not probed with empty params (the archive's probe forced every factory to
    default every parameter, which is how ``csf_hazard`` came to default to always passing): a factory that refuses
    its params makes the control invalid when a plan or report is built, and is reported there.
    """
    if not isinstance(name, str) or not name.strip() or not name.replace("_", "").isalnum():
        raise ValueError(f"{PREDICATE_REGISTRATION}: invalid predicate name")
    key = name.strip()
    if key in _BUILTIN and not override:
        raise ValueError(f"{PREDICATE_REGISTRATION}: built-in predicate override refused")
    if key in _PREDICATES and not override:
        raise ValueError(f"{PREDICATE_REGISTRATION}: predicate already registered")
    if not callable(factory):
        raise ValueError(f"{PREDICATE_REGISTRATION}: factory must be callable")
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{PREDICATE_REGISTRATION}: factory signature unavailable") from exc
    required = [p for p in signature.parameters.values()
                if p.default is inspect.Parameter.empty
                and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    if len(required) != 1:
        raise ValueError(f"{PREDICATE_REGISTRATION}: factory must take exactly one required params argument")
    _PREDICATES[key] = factory


def list_predicates() -> list[str]:
    """Registered predicate names, sorted."""
    return sorted(_PREDICATES)


def _why(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        return f"{'.'.join(str(part) for part in first['loc']) or 'params'}: {first['msg']}"
    return str(exc) or type(exc).__name__


def resolve(predicate: str, params: Mapping[str, Any]) -> Predicate:
    """The predicate a control names, built from its params, or ``ControlError`` saying why it cannot be built."""
    factory = _PREDICATES.get(predicate)
    if factory is None:
        raise ControlError(PREDICATE_UNKNOWN, f"no predicate named {predicate!r}; registered: {list_predicates()}")
    try:
        check = factory(MappingProxyType(dict(params)))
    except Exception as exc:  # a factory refusing its params is a spec error, reported with the control
        raise ControlError(CONTROL_INVALID, f"{predicate}: {_why(exc)}") from exc
    if not callable(check):
        raise ControlError(CONTROL_INVALID, f"{predicate}: the factory returned {type(check).__name__}, not a predicate")
    return check


def run(check: Predicate, observation: Observation) -> tuple[Verdict, str, str]:
    """``(verdict, reason, error code)`` for one predicate on one observation; the code is empty unless it raised."""
    try:
        value = check(observation)
    except Exception as exc:
        return "unknown", f"the predicate raised {type(exc).__name__}: {exc}"[:300], PREDICATE_ERROR
    if isinstance(value, bool):
        return ("pass" if value else "fail"), "", ""
    if isinstance(value, str) and value in VERDICTS:
        return value, "", ""  # type: ignore[return-value]
    if (isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str) and value[0] in VERDICTS
            and isinstance(value[1], str)):
        return value[0], value[1], ""  # type: ignore[return-value]
    return "unknown", f"the predicate returned {type(value).__name__}, not a verdict", ""


__all__ = ["HAZARDS", "VERDICTS", "ControlError", "Factory", "Predicate", "Verdict", "list_predicates",
           "register_predicate", "resolve", "run", "usage_within"]
