# SPDX-License-Identifier: Apache-2.0
"""The core's statistics: the regularised incomplete beta and gamma functions moved out of adapt_bayesian into
g6.core.special, pure Python, and adapt_bayesian's numerics are those same functions with the family's refusal."""
from __future__ import annotations

import ast
import math
import sys
from pathlib import Path

import pytest

from g6.core import special


def test_the_special_functions_live_in_the_core_and_import_only_the_standard_library():
    # Removing g6/core/special.py, or importing anything beyond the standard library into it, fails this.
    tree = ast.parse(Path(special.__file__).read_text(encoding="utf-8"))
    names = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
             for alias in node.names}
    names |= {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert names <= set(sys.stdlib_module_names) | {"__future__"}
    assert special.beta_cdf(.5, 3, 9) == pytest.approx(1981 / 2048, abs=1e-13)
    assert special.gamma_cdf(2, 1) == pytest.approx(1 - math.exp(-2), abs=1e-13)
    assert 1 - special.gamma_cdf(7, 6) == pytest.approx(.3007082762, abs=5e-11)
    with pytest.raises(special.NotConverged):
        special.beta_cdf(.4, 2.5, 1, max_iter=0)
    with pytest.raises(ValueError):
        special.gamma_cdf(-1, 1)


def test_bayesian_numerics_are_the_cores_with_the_familys_refusal(monkeypatch):
    # Removing numerics' MAX_ITER pass-through (or its MLError conversion) changes adapt_bayesian's behaviour.
    from g6.ml._types import MLError
    from g6.ml.adapt_bayesian import numerics

    assert numerics.beta_cdf(.3, 2.5, 4) == special.beta_cdf(.3, 2.5, 4)
    assert numerics.gamma_quantile(.5, 3) == special.gamma_quantile(.5, 3)
    with pytest.raises(MLError, match="positive shape"):
        numerics.gamma_cdf(-1, 1)
    monkeypatch.setattr(numerics, "MAX_ITER", 0)
    with pytest.raises(MLError, match="did not converge"):
        numerics.beta_cdf(.4, 2.5, 1)


@pytest.mark.parametrize(("x", "a", "expected"), [
    (50.0, 0.5, math.erfc(math.sqrt(50))), (40.5, 0.5, math.erfc(math.sqrt(40.5))), (0.3, 0.5, math.erfc(math.sqrt(.3))),
    (700.0, 1.0, math.exp(-700)), (2.0, 1.0, math.exp(-2)), (7.0, 6.0, .3007082762)])
def test_the_upper_gamma_is_computed_directly_so_a_far_tail_keeps_its_digits(x, a, expected):
    # Fails if gamma_sf in g6/core/special.py is computed as 1 - gamma_cdf: erfc(sqrt(50)) = 1.5e-23 would read 0.
    tolerance = 1e-9 if expected == .3007082762 else 1e-12
    assert math.isclose(special.gamma_sf(x, a), expected, rel_tol=tolerance, abs_tol=0)
    assert math.isclose(special.gamma_sf(x, a) + special.gamma_cdf(x, a), 1, rel_tol=1e-13)
    assert special.gamma_sf(0, 2.5) == 1
    with pytest.raises(ValueError):
        special.gamma_sf(-1, 1)
    with pytest.raises(special.NotConverged):
        special.gamma_sf(50, .5, max_iter=0)
