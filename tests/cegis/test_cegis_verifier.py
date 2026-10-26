# SPDX-License-Identifier: Apache-2.0
"""g6.cegis verifiers without a real Z3 (audit 027, CG-008): a solver that does not decide is degraded and never a pass.

These run whether or not z3-solver is installed: Z3 is replaced in memory by a stub that answers unknown, or blocked.
The archive cannot run here; each regression test says what the archive did, and each ``test_null_*`` puts that
behaviour back in memory (monkeypatching only) and shows the regression test fails.
"""
from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

import g6.cegis.loop as cegis_loop
import g6.cegis.z3_verifier as z3_verifier
from g6.cegis import (
    Counterexample,
    Inconclusive,
    InputDomain,
    NoCounterexample,
    Sketch,
    SubprocessRunner,
    TableOracle,
    Z3Verifier,
    cegis,
)
from g6.invariants import check_envelope

LINEAR = "def f(x):\n    return x * ?? + ??\n"
X = InputDomain.ints(["x"], -10, 10)
SPEC = "(= result (+ (* 3 x) 1))"


def _fails(test, *args) -> None:
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        test(*args)


def _stub_z3(reason_unknown: str) -> types.ModuleType:
    """A z3 that reads any query and answers unknown, as Z3 does on a hard query (or, with "timeout", a timeout)."""
    z3 = types.ModuleType("z3")
    z3.sat, z3.unsat, z3.unknown = object(), object(), object()

    class Z3Exception(Exception):
        pass

    class Solver:
        def set(self, *args: Any) -> None:
            pass

        def from_string(self, query: str) -> None:
            self.query = query

        def check(self) -> object:
            return z3.unknown

        def reason_unknown(self) -> str:
            return reason_unknown

    z3.Z3Exception, z3.Solver = Z3Exception, Solver
    z3.get_version_string = lambda: "stub"
    return z3


@dataclass(frozen=True)
class _Says:
    """A verifier that always gives the same outcome."""

    outcome: Any

    def find_counterexample(self, sketch, control, domain):  # noqa: ANN001, ANN201
        return self.outcome


@dataclass(frozen=True)
class _Enumerating:
    """A verifier with no solver: it runs the candidate out of process on every input of the domain, from the last,
    and compares it with a reference. An independent check of the loop's feedback path."""

    reference: Any

    def find_counterexample(self, sketch, control, domain):  # noqa: ANN001, ANN201
        everything = InputDomain.of(dict(domain.values), max_inputs=domain.size).inputs()[::-1]
        row = SubprocessRunner().evaluate(sketch, [control], everything)[0]
        for inputs, got in zip(everything, row, strict=True):
            if got.get("ok") != self.reference(**inputs):
                return Counterexample(inputs)
        return NoCounterexample({"checked": len(everything)})


# --- archive behaviours -----------------------------------------------------------------------------------------------

def _archive_decide(decided, domain, evidence):  # noqa: ANN001, ANN202
    """synthesizer.py:230, ``if solver.check() != _Z3.sat: return None``: every answer but sat, unknown and a timeout
    included, read as "nothing found"."""
    if decided.satisfiable is True:
        return Counterexample({name: decided.model.get(name, values[0]) for name, values in domain.values}, evidence)
    return NoCounterexample(evidence)


def _archive_verifier_says(outcome):  # noqa: ANN001, ANN202
    """engine.py:175-181 with rosette/verify.py:64-67: a verifier that timed out came back "unavailable" and the
    sampled verdict stood, with nothing in the output saying the stronger check was attempted."""
    return "counterexample" if isinstance(outcome, Counterexample) else "no_counterexample"


# --- import and availability ------------------------------------------------------------------------------------------

def test_importing_g6_cegis_loads_no_z3(tmp_path) -> None:
    probe = "import sys, g6.cegis\nassert 'z3' not in sys.modules, 'importing g6.cegis imported z3'\n"
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)


def test_without_z3_the_verifier_is_honestly_unavailable(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "z3", None)  # ``import z3`` now raises ImportError
    outcome = Z3Verifier(SPEC).find_counterexample(Sketch.from_src(LINEAR), {0: 3, 1: 1}, X)
    assert isinstance(outcome, Inconclusive) and outcome.degraded is True
    assert outcome.degradation_reason == "z3_unavailable" and "g6[formal]" in outcome.reason
    result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X, verifier=Z3Verifier(SPEC))
    assert result.status == "inconclusive" and result.completion_state == "blocked-escalated"
    assert result.reason.startswith("z3_unavailable") and result.boundary["verifier"]["degraded"] is True
    assert check_envelope(result.to_envelope(request_id="q", run_id="r")) == []


# --- CG-008: unknown, a timeout included, is never a decision ---------------------------------------------------------

def test_cg008_z3_unknown_is_degraded_never_no_counterexample(monkeypatch) -> None:
    """Archive: ``synthesizer.py:230`` read Z3 unknown as UNSAT and fell through with no disclosure. The audit's test:
    a Z3 stub returning unknown must yield degraded=True with reason z3_unknown."""
    monkeypatch.setitem(sys.modules, "z3", _stub_z3("incomplete"))
    outcome = Z3Verifier(SPEC).find_counterexample(Sketch.from_src(LINEAR), {0: 3, 1: 1}, X)
    assert isinstance(outcome, Inconclusive) and not isinstance(outcome, NoCounterexample)
    assert outcome.degraded is True and outcome.degradation_reason == "z3_unknown"
    assert outcome.evidence["solver"][0]["checks"][0]["result"] == "unknown"

    result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X, verifier=Z3Verifier(SPEC))
    assert result.status == "inconclusive" and result.completion_state == "blocked-escalated"
    assert result.control == {0: 3, 1: 1}  # the candidate is kept; only the claim about it is withheld
    assert result.reason.startswith("z3_unknown")
    said = result.boundary["verifier"]
    assert said["outcome"] == "inconclusive" and said["degraded"] is True and said["degradation_reason"] == "z3_unknown"
    envelope = result.to_envelope(request_id="q", run_id="r")
    assert check_envelope(envelope) == [] and envelope["completion_state"] == "blocked-escalated"
    assert envelope["degraded"] is True and envelope["degradation_reason"].startswith("z3_unknown")
    assert envelope["warning_card"]["code"] == "G6_E_CEGIS_INCONCLUSIVE"


def test_cg008_a_z3_timeout_is_degraded_with_its_own_reason(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "z3", _stub_z3("timeout"))
    outcome = Z3Verifier(SPEC, timeout_s=0.5).find_counterexample(Sketch.from_src(LINEAR), {0: 3, 1: 1}, X)
    assert isinstance(outcome, Inconclusive) and outcome.degradation_reason == "z3_timeout"


def test_null_cg008_z3(monkeypatch) -> None:
    monkeypatch.setattr(z3_verifier, "_decide", _archive_decide)
    _fails(test_cg008_z3_unknown_is_degraded_never_no_counterexample, monkeypatch)
    _fails(test_cg008_a_z3_timeout_is_degraded_with_its_own_reason, monkeypatch)


def test_cg008_an_undecided_verifier_never_lets_the_sampled_verdict_stand() -> None:
    """Archive: a Rosette timeout became ``status="unavailable"``, and the sampled verdict stood as if the stronger
    check had not been asked for (``engine.py:175-181``)."""
    for outcome, token in ((Inconclusive("z3_timeout", "z3 returned unknown (timeout)"), "z3_timeout"),
                           (None, "verifier_no_outcome")):
        result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X, verifier=_Says(outcome))
        assert result.status == "inconclusive" and result.completion_state == "blocked-escalated"
        assert result.reason.startswith(token) and result.boundary["compared"] == 21
        said = result.boundary["verifier"]
        assert said["outcome"] == "inconclusive" and said["degraded"] is True and said["degradation_reason"] == token
        assert check_envelope(result.to_envelope()) == []


def test_null_cg008_loop(monkeypatch) -> None:
    monkeypatch.setattr(cegis_loop, "_verifier_says", _archive_verifier_says)
    _fails(test_cg008_an_undecided_verifier_never_lets_the_sampled_verdict_stand)


# --- survival controls: the gate passes what it should, and a verifier's counterexample is used ------------------------

def test_a_verifier_that_finds_nothing_leaves_a_qualified_draft() -> None:
    result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X, verifier=_Says(NoCounterexample()))
    assert result.status == "no_counterexample_in_domain" and result.completion_state == "qualified-draft"
    assert result.boundary["verifier"] | {"evidence": {}} == {"name": "_Says", "calls": 1, "outcome": "no_counterexample",
                                                              "degraded": False, "degradation_reason": "",
                                                              "evidence": {}}
    envelope = result.to_envelope(request_id="q", run_id="r")
    assert check_envelope(envelope) == [] and envelope["warning_card"]["code"] == "G6_E_CEGIS_DOMAIN_BOUNDED"
    assert "not a proof a person has inspected" in envelope["warning_card"]["message"]


def test_without_a_verifier_the_boundary_is_unchanged() -> None:
    result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X)
    assert "verifier" not in result.boundary and all("verifier" not in step for step in result.trace)


def test_a_verifier_counterexample_the_sample_missed_is_fed_back() -> None:
    """Three sampled inputs of [-1000, 1000] let a wrong clamp through; a verifier that checks every input does not."""
    sketch = Sketch.from_src("def f(x):\n    return ?? if x > ?? else x\n")
    domain = InputDomain.ints(["x"], -1000, 1000, max_inputs=3, seed=1)
    sampled = cegis(sketch, lambda x: min(x, 20), domain=domain)
    assert sampled.status == "no_counterexample_in_domain"
    assert isinstance(_Enumerating(lambda x: min(x, 20)).find_counterexample(sketch, sampled.control, domain),
                      Counterexample)  # the sampled result is wrong somewhere in its own domain
    checked = cegis(sketch, lambda x: min(x, 20), domain=domain, verifier=_Enumerating(lambda x: min(x, 20)))
    assert checked.status == "no_counterexample_in_domain" and checked.control[0] == 20 and checked.control[1] in (19, 20)
    assert "counterexample" in [step.get("verifier") for step in checked.trace]


def test_a_verifier_counterexample_is_not_taken_on_its_word() -> None:
    """The candidate runs on the verifier's input (out of process) against the oracle; agreement, or an oracle that
    does not answer, makes the result inconclusive, never a counterexample fed back."""
    agrees = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X, verifier=_Says(Counterexample({"x": 4})))
    unanswered = cegis(Sketch.from_src(LINEAR), TableOracle.of([({"x": x}, 3 * x + 1) for x in range(0, 5)]),
                       domain=X, verifier=_Says(Counterexample({"x": 9})))
    for result, said, why in ((agrees, {"x": 4}, "agree there"), (unanswered, {"x": 9}, "does not answer")):
        assert result.status == "inconclusive" and result.reason.startswith("counterexample_unconfirmed")
        assert why in result.reason and said not in result.counterexamples
        assert result.boundary["verifier"]["outcome"] == "counterexample_unconfirmed"
        assert check_envelope(result.to_envelope()) == []


# --- what Z3 is never given -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("spec", ["(= x 1)", "", "(= results 1)", 42])
def test_a_spec_that_never_names_result_is_refused(spec) -> None:
    """A spec that does not mention the return value passes or fails every candidate alike (audit 027, CG-001's
    lesson: a check that cannot compare is refused, not run)."""
    with pytest.raises(ValueError, match="names result"):
        Z3Verifier(spec)


@pytest.mark.parametrize("src", [
    "def f(x):\n    return abs(x)\n",
    "def f(x):\n    return x ** 2\n",
    "def f(x):\n    return x / 2\n",
    "def f(x):\n    return x // -2\n",
    "def f(x):\n    return x % 0\n",
    "def f(x):\n    return True + x\n",
    "def f(x):\n    return x if x else 0\n",
    "def f(x):\n    return x and 1\n",
    "def f(x):\n    return 'a'\n",
    "def f(x):\n    y = x\n    return y\n",
    "def f(x, y=1):\n    return x\n",
    "def g(x):\n    return x\n",
])
def test_a_candidate_outside_the_grammar_is_untranslatable_never_approximated(src: str) -> None:
    outcome = Z3Verifier("(= result x)").find_counterexample(Sketch.from_src(src), {}, X)
    assert isinstance(outcome, Inconclusive) and outcome.degradation_reason == "z3_untranslatable"


@pytest.mark.parametrize("values", [{"x": ["a", "b"]}, {"x": [0.5, 1.0]}, {"x": [1, True]},
                                    {"x": list(range(0, 1000, 3))}, {"result": [1, 2]}])
def test_a_domain_outside_the_grammar_is_untranslatable(values) -> None:
    name = next(iter(values))
    sketch = Sketch.from_src(f"def f({name}):\n    return 1\n")
    outcome = Z3Verifier("(= result 1)").find_counterexample(sketch, {}, InputDomain.of(values))
    assert isinstance(outcome, Inconclusive) and outcome.degradation_reason == "z3_untranslatable"
