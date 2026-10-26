# SPDX-License-Identifier: Apache-2.0
"""Numeric parametric sketches. Fitting is proposal, never admission or promotion."""
from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Sequence

from g6.cegis.sketch import Sketch
from g6.distillation import admission
from g6.distillation.admission import Candidate, HoldoutCase, HoldoutReport, same
from g6.distillation.sandbox import run_batch

FAMILIES = ("linear", "polynomial", "power", "clamp", "rational", "piecewise_linear",
            "exponential", "log", "affine_vec", "scaled_sigmoid")
#: The most training-by-held-out leak comparisons one fit makes in process.
MAX_COMPARISONS = 1_000_000


@dataclass(frozen=True)
class TemplateFit:
    family: str
    sketch: Sketch | None
    report: HoldoutReport

    @property
    def completion_state(self) -> str:
        return self.report.completion_state


def _number(value) -> str:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("non-finite fitted parameter")
    return repr(value)


def _fit(family, cases, degree):
    import numpy as np
    from scipy.optimize import curve_fit

    x = np.asarray([c.input for c in cases], dtype=float)
    y = np.asarray([c.expected for c in cases], dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("samples must be finite")

    def least(design, target):
        coefficients, _, rank, _ = np.linalg.lstsq(design, target, rcond=None)
        if rank < design.shape[1]:
            raise ValueError("samples do not identify all parameters")
        return coefficients

    def line(xs, ys):
        return least(np.column_stack((xs, np.ones(len(xs)))), ys)

    def affine(a, b, arg="x"):
        return f"({_number(a)} * {arg} + {_number(b)})"

    if family == "affine_vec":
        if x.ndim != 2 or y.ndim != 2 or not x.shape[1] or not y.shape[1]:
            raise ValueError("affine_vec needs fixed-size input and output vectors")
        weights = least(np.column_stack((x, np.ones(len(x)))), y)
        expressions = [" + ".join([f"{_number(weights[i, j])} * x[{i}]" for i in range(x.shape[1])]
                                   + [_number(weights[-1, j])]) for j in range(y.shape[1])]
        return "[" + ", ".join(expressions) + "]", ""
    if x.ndim != 1 or y.ndim != 1:
        raise ValueError("this family needs scalar samples")
    if family in ("linear", "polynomial"):
        d = 1 if family == "linear" else degree
        coeff = least(np.vander(x, d + 1), y)
        return " + ".join(f"({_number(c)} * x ** {d-i})" for i, c in enumerate(coeff)), ""
    if family in ("power", "exponential", "log"):
        if family in ("power", "log") and (x <= 0).any():
            raise ValueError("all inputs must be positive for this family")
        if family in ("power", "exponential") and (y <= 0).any():
            raise ValueError("all outputs must be positive for this family")
        a, b = line(np.log(x) if family in ("power", "log") else x,
                    np.log(y) if family in ("power", "exponential") else y)
        if family == "power":
            return f"{_number(math.exp(b))} * x ** {_number(a)}", ""
        if family == "exponential":
            return f"{_number(math.exp(b))} * {math.e!r} ** ({_number(a)} * x)", ""
        # No import in generated source: log via a bounded atanh series after range reduction.
        helper = ("def natural_log(x):\n    if x <= 0:\n        raise ValueError('log domain')\n"
                  "    exponent = 0\n    for i in range(1100):\n"
                  "        if x >= 2:\n            x = x / 2\n            exponent += 1\n"
                  "        elif x < 1:\n            x = x * 2\n            exponent -= 1\n"
                  "        else:\n            break\n"
                  "    z = (x - 1) / (x + 1)\n"
                  "    return 2 * sum(z ** (2*i+1) / (2*i+1) for i in range(40)) + exponent * 0.6931471805599453\n")
        return affine(a, b, "natural_log(x)"), helper
    if family == "clamp":
        lows, highs = y[x < y], y[x > y]
        if not len(lows) or not len(highs):
            raise ValueError("clamp needs observations of both plateaus")
        lo, hi = max(lows), min(highs)
        if lo > hi:
            raise ValueError("clamp bounds conflict")
        return f"max({_number(lo)}, min({_number(hi)}, x))", ""
    if family == "rational":
        # Normalise the denominator's constant to one to remove scale ambiguity.
        a, b, c = least(np.column_stack((x, np.ones(len(x)), -x*y)), y)
        return f"{affine(a, b)} / {affine(c, 1)}", ""
    if family == "piecewise_linear":
        order = np.argsort(x)
        x, y = x[order], y[order]
        best = None
        for k in range(2, len(x)-1):
            if x[k-1] == x[k]:
                continue
            try:
                a, b = line(x[:k], y[:k])
                c, d = line(x[k:], y[k:])
            except ValueError:
                continue
            error = float(np.sum((a*x[:k]+b-y[:k])**2) + np.sum((c*x[k:]+d-y[k:])**2))
            if best is None or error < best[0]:
                best = error, k, a, b, c, d
        if best is None:
            raise ValueError("piecewise_linear needs two identifiable lines")
        _, k, a, b, c, d = best
        return f"{affine(a, b)} if x < {_number(x[k])} else {affine(c, d)}", ""
    if family == "scaled_sigmoid":
        if len(x) < 3 or len(set(x)) < 3:
            raise ValueError("scaled_sigmoid needs three distinct inputs")
        def sigmoid(xs, k, x0):
            return 1 / (1 + np.exp(-k * (xs-x0)))
        (k, x0), _ = curve_fit(sigmoid, x, y, p0=(1, 0), maxfev=5000)
        return f"1 / (1 + {math.e!r} ** (-{_number(k)} * (x - {_number(x0)})))", ""
    raise ValueError(f"unknown family {family!r}")


def fit_template(family: str, training: Sequence[HoldoutCase], holdout: Sequence[HoldoutCase], *,
                 negative_control: Candidate, degree: int = 2, timeout_s: float = 10.0,
                 memory_mb: int = 256) -> TemplateFit:
    """Fit one explicitly chosen family; freeze it before judging on disjoint held-out cases.

    No model, CLI, fallback, registry or reuse store. All candidate execution uses
    distillation's bounded child. Training ids AND equal inputs cannot be held out.
    A refitted family requires fresh holdout; callers own that sampling discipline.
    """
    sketch = None
    candidate = Candidate("")
    def refused(code, reason):
        return TemplateFit(family, sketch, HoldoutReport("refused", candidate.digest, reason, code=code))
    try:
        train, held = list(training), list(holdout)
        if family not in FAMILIES or type(degree) is not int or not 1 <= degree <= 3:
            return refused("G6_E_CEGIS_TEMPLATE_INPUT", "unknown numeric family or degree outside 1..3")
        if not 2 <= len(train) <= 10000 or not 1 <= len(held) <= 10000:
            return refused("G6_E_CEGIS_TEMPLATE_INPUT", "need 2..10000 training and 1..10000 held-out cases")
        if len(train) * len(held) > MAX_COMPARISONS:
            # The leak check below compares every held-out case with every training case, in this process.
            return refused("G6_E_CEGIS_TEMPLATE_INPUT", f"training cases times held-out cases is at most "
                                                        f"{MAX_COMPARISONS}; split the held-out set")
        if len({c.case_id for c in train}) != len(train) or any(not c.case_id for c in train + held):
            return refused("G6_E_CEGIS_TEMPLATE_INPUT", "training ids must be distinct and all case ids non-empty")
        if any(c.case_id == t.case_id or same(c.input, t.input) for c in held for t in train):
            return refused("G6_E_CEGIS_TEMPLATE_LEAK", "held-out cases overlap training ids or inputs")
        expression, helper = _fit(family, train, degree)
        sketch = Sketch.from_src(helper + f"def solve(x):\n    return {expression}\n", fn_name="solve")
        candidate = Candidate(sketch.instantiate({}), training_case_ids=tuple(c.case_id for c in train))
        runs = run_batch(candidate.source, [c.input for c in train], timeout_s=timeout_s, memory_mb=memory_mb)
        if len(runs) != len(train) or any(not r.ok or not same(r.value, c.expected) for r, c in zip(runs, train)):
            return refused("G6_E_CEGIS_TEMPLATE_TRAINING", "the fitted program did not run and match every training case")
        report = admission.evaluate(candidate, held, negative_control=negative_control,
                                    timeout_s=timeout_s, memory_mb=memory_mb)
        return TemplateFit(family, sketch, report)
    except ImportError:
        return refused("G6_E_CEGIS_TEMPLATE_EXTRA", "numeric fitting needs NumPy and SciPy: install g6[optim]")
    except Exception as exc:  # noqa: BLE001 -- fitting, execution and judge failures never yield a pass
        return refused("G6_E_CEGIS_TEMPLATE_FAILED", f"{type(exc).__name__}: {exc}"[:500])
