# SPDX-License-Identifier: Apache-2.0
"""The CEGIS loop (Solar-Lezama 2008, Algorithm 1): fit the examples, check on the domain, add the counterexample.

What a result can say, and nothing more:

* ``no_counterexample_in_domain``: the program agrees with the oracle on every input of the domain the oracle answered
  (the boundary names the domain, how many inputs were compared and how many were skipped). Not a proof outside it;
* ``counterexample_budget_exhausted``: ``max_iterations`` counterexamples and still none fits;
* ``unsat``: an exhaustive search found no control that fits the examples (only an exhaustive search can say this);
* ``timeout``: the wall-clock budget (or one candidate run's) ran out;
* ``inconclusive``: nothing could be compared, or a non-exhaustive search found no fitting control;
* ``refused``: no candidate ran, because isolation was required and no operating-system backend could confine the
  candidates' child (``G6_E_ISOLATION_UNAVAILABLE``; never a search result).

``boundary["isolation"]`` says how the candidates' children ran (``g6.core.confine.summary`` of every run: the first
that was not confined, else the first).

``completion_state`` is ``qualified-draft`` for the first and ``blocked-escalated`` otherwise, never ``verified``
(audit 027: the archive's ``verified`` was vacuous on reachable paths); ``to_envelope`` gives the library envelope.

With a ``verifier`` (such as :class:`~g6.cegis.z3_verifier.Z3Verifier`), a candidate the sampled check passes is also
put to it. Its counterexample is fed back only once the candidate, run on it out of process, disagrees with the oracle;
its ``NoCounterexample`` lets the result stand; its ``Inconclusive`` makes the result ``inconclusive``, with the
verifier's degradation reason first in ``reason`` (audit 027, CG-008). ``boundary["verifier"]`` records what it said.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from g6.cegis.runner import Runner, RunnerRefused, RunnerTimeout, SubprocessRunner
from g6.cegis.sketch import Control, Sketch
from g6.cegis.verify import (
    Counterexample,
    InputDomain,
    NoCounterexample,
    Oracle,
    Outcome,
    Verifier,
    answered,
    check_candidate,
)
from g6.core import confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.reliability_envelope import envelope_fields, warning_card

Status = Literal["no_counterexample_in_domain", "counterexample_budget_exhausted", "unsat", "timeout", "inconclusive",
                 "refused"]
_CODES = {"counterexample_budget_exhausted": "G6_E_CEGIS_BUDGET_EXHAUSTED", "unsat": "G6_E_CEGIS_UNSAT",
          "timeout": "G6_E_CEGIS_TIMEOUT", "inconclusive": "G6_E_CEGIS_INCONCLUSIVE", "refused": ISOLATION_UNAVAILABLE}


@dataclass(frozen=True)
class CegisResult:
    status: Status
    control: Control | None
    program: str
    iterations: int
    counterexamples: tuple[dict[str, Any], ...]
    #: What the result rests on, computed from this run: the domain, inputs compared and skipped, how controls were found.
    boundary: dict[str, Any]
    trace: tuple[dict[str, Any], ...] = field(default=())
    reason: str = ""

    @property
    def completion_state(self) -> str:
        return "qualified-draft" if self.status == "no_counterexample_in_domain" else "blocked-escalated"

    def to_envelope(self, *, request_id: str = "", run_id: str = "") -> dict[str, Any]:
        """The result as a library envelope (``g6.core.reliability_envelope``), with a ``G6_E_*`` warning card."""
        if self.status == "no_counterexample_in_domain":
            message = (f"No counterexample on {self.boundary['compared']} compared input(s) of the stated domain; this "
                       "is not a proof outside it.")
            if (self.boundary.get("verifier") or {}).get("outcome") == "no_counterexample":
                message += (" The verifier found none on any input of its encoded domain either: a solver decision on "
                            "your spec, not a proof a person has inspected.")
            card = warning_card("G6_E_CEGIS_DOMAIN_BOUNDED", message,
                                "Widen the domain or prove the property before relying on it elsewhere.")
        else:
            card = warning_card(_CODES[self.status], self.reason or self.status.replace("_", " "))
        evidence = [{"kind": "cegis_boundary", **self.boundary}]
        envelope = envelope_fields(reliability_label=self.completion_state, warning_card=card, evidence=evidence,
                                   request_id=request_id, run_id=run_id, degradation_reason=self.reason)
        return {**envelope, "status": self.status, "program": self.program}


def _verifier_says(outcome: Outcome) -> str:
    """``counterexample`` or ``no_counterexample`` for those two outcomes; anything else is ``inconclusive``
    (audit 027, CG-008: a verifier that did not decide never lets the sampled verdict stand as if it had)."""
    if isinstance(outcome, Counterexample):
        return "counterexample"
    if isinstance(outcome, NoCounterexample):
        return "no_counterexample"
    return "inconclusive"


def cegis(sketch: Sketch, oracle: Oracle, *, domain: InputDomain, runner: Runner | None = None,
          max_iterations: int = 20, timeout_s: float = 120.0, max_exhaustive: int = 50_000,
          verifier: Verifier | None = None) -> CegisResult:
    """Synthesize a control for ``sketch`` that agrees with ``oracle`` on ``domain`` (and, with a ``verifier``, that
    the verifier finds no counterexample to)."""
    with confine.recording() as runs:
        return _cegis(sketch, oracle, domain=domain, runner=runner, max_iterations=max_iterations,
                      timeout_s=timeout_s, max_exhaustive=max_exhaustive, verifier=verifier, runs=runs)


def _cegis(sketch: Sketch, oracle: Oracle, *, domain: InputDomain, runner: Runner | None, max_iterations: int,
           timeout_s: float, max_exhaustive: int, verifier: Verifier | None,
           runs: list[dict[str, Any]]) -> CegisResult:
    runner = runner or SubprocessRunner()
    started = time.monotonic()
    inputs = domain.inputs()
    cases = answered(oracle, inputs)
    skipped = len(inputs) - len(cases)
    examples: list[tuple[dict[str, Any], Any]] = cases[:1]
    counterexamples: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    methods: list[str] = []
    control: Control | None = None
    said: dict[str, Any] = {"name": type(verifier).__name__, "calls": 0, "outcome": "not_run", "degraded": False,
                            "degradation_reason": "", "evidence": {}}

    def result(status: Status, iterations: int, reason: str = "", compared: int = 0) -> CegisResult:
        boundary = {"domain": domain.describe(), "answered": len(cases), "skipped": skipped, "compared": compared,
                    "search": methods[-1] if methods else None, "examples": len(examples),
                    "elapsed_s": round(time.monotonic() - started, 3), "model_calls": 0,
                    "isolation": confine.summary(runs)}
        if verifier is not None:  # audit 027, CG-004: what the verifier said in this run, not a default
            boundary["verifier"] = dict(said)
        program = sketch.instantiate(control) if control is not None else ""
        return CegisResult(status, control, program, iterations, tuple(counterexamples), boundary, tuple(trace),
                           reason)

    if not cases:
        return result("inconclusive", 0, "the oracle answered no input in the domain, so nothing can be compared")
    for iteration in range(1, max_iterations + 1):
        if time.monotonic() - started >= timeout_s:
            return result("timeout", iteration - 1, f"the {timeout_s:g}s budget ran out")
        try:
            found = runner.search(sketch, examples, max_exhaustive=max_exhaustive)
        except RunnerTimeout as exc:
            return result("timeout", iteration, str(exc))
        except RunnerRefused as exc:
            return result("refused", iteration - 1, str(exc))
        methods.append(found.method)
        trace.append({"iteration": iteration, "search": found.method, "tried": found.tried,
                      "found": found.control is not None})
        if found.control is None:
            control = None
            if found.complete:
                return result("unsat", iteration, "no control of the sketch fits the examples (searched exhaustively)")
            return result("inconclusive", iteration, "the search found no fitting control, and it was not exhaustive")
        control = found.control
        try:
            checked = check_candidate(sketch, control, cases, skipped=skipped, runner=runner)
        except RunnerTimeout as exc:
            return result("timeout", iteration, str(exc))
        except RunnerRefused as exc:
            control = None
            return result("refused", iteration, str(exc))
        trace[-1].update(check=checked.status, compared=checked.compared)
        if checked.status == "no_counterexample" and verifier is not None:
            outcome = verifier.find_counterexample(sketch, control, domain)
            says = _verifier_says(outcome)
            token = (getattr(outcome, "degradation_reason", "") or "verifier_no_outcome") if says == "inconclusive" \
                else ""
            said.update(calls=said["calls"] + 1, outcome=says, degraded=says == "inconclusive",
                        degradation_reason=token, evidence=dict(getattr(outcome, "evidence", {}) or {}))
            trace[-1]["verifier"] = says
            if says == "inconclusive":
                # audit 027, CG-008: no decision is never "no counterexample", whatever the sampled check found
                return result("inconclusive", iteration,
                              f"{token}: {getattr(outcome, 'reason', '') or 'the verifier gave no decision'} (the "
                              f"sampled check passed on {checked.compared} input(s); the verifier's no-decision is not "
                              "a pass)", compared=checked.compared)
            if says == "counterexample":
                # The verifier's word is not taken: the candidate runs on its input against the oracle, out of process
                # like every candidate run (audit 027, CG-002)
                case = answered(oracle, [outcome.inputs])
                try:
                    confirmed = check_candidate(sketch, control, case, skipped=1 - len(case), runner=runner)
                except RunnerTimeout as exc:
                    return result("timeout", iteration, str(exc), compared=checked.compared)
                except RunnerRefused as exc:
                    control = None
                    return result("refused", iteration, str(exc), compared=checked.compared)
                if confirmed.status != "counterexample":
                    said.update(outcome="counterexample_unconfirmed", degraded=True,
                                degradation_reason="counterexample_unconfirmed", inputs=dict(outcome.inputs))
                    trace[-1]["verifier"] = "counterexample_unconfirmed"
                    why = ("the oracle does not answer it" if not case else
                           "the candidate and the oracle agree there, so the verifier's spec and the oracle disagree")
                    return result("inconclusive", iteration,
                                  f"counterexample_unconfirmed: the verifier's counterexample {outcome.inputs} is not "
                                  f"confirmed: {why}", compared=checked.compared)
                counterexamples.append(dict(confirmed.counterexample or {}))
                examples.append((dict(confirmed.counterexample or {}), confirmed.expected))
                continue
        if checked.status == "no_counterexample":
            return result("no_counterexample_in_domain", iteration, compared=checked.compared)
        if checked.status == "inconclusive":
            return result("inconclusive", iteration, checked.reason, compared=checked.compared)
        counterexamples.append(dict(checked.counterexample or {}))
        examples.append((dict(checked.counterexample or {}), checked.expected))
    return result("counterexample_budget_exhausted", max_iterations,
                  f"{max_iterations} counterexample(s) and no control agrees on the domain")


__all__ = ["CegisResult", "Status", "cegis"]
