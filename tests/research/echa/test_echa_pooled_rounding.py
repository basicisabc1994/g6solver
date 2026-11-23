# SPDX-License-Identifier: Apache-2.0
"""Pooling and display must preserve conservative bounds across scales and order."""
from fractions import Fraction
from itertools import permutations
import math
from pathlib import Path
import runpy

import pytest

from g6.research.echa import Ledger, trace_claim
from g6.research.echa.claims import EXECUTABLE

H = runpy.run_path(str(Path(__file__).with_name("test_echa_kernel_claims.py")))
POOLS = tuple(dict.fromkeys(
    order
    for power in (54, 55, 60, 1074)
    for pool in ((0.5, 2.0 ** -power), (0.25, 0.25, 2.0 ** -power))
    for order in permutations(pool)
))


@pytest.mark.parametrize("category", EXECUTABLE)
@pytest.mark.parametrize("bounds", POOLS)
def test_every_pool_is_conservative_in_both_number_interpretations(category, bounds):
    led = Ledger()
    led.add_standard(H["STD"])
    led.add_vault("v1", H["FX"], files=1, roots=1)
    for index, bound in enumerate(bounds):
        H["calibrated"](led, eid=f"e{index}", code="adf"[index], bound=bound, lower=0.75,
                        check_over={"oracle_category": category})
    got = H["verdict"](led, subject="sha256:" + "b" * 64,
                       evidence_ids=tuple(f"e{i}" for i in range(len(bounds))),
                       estimand="true_correctness", target=0.25)
    # Independent exact arithmetic over the inputs, never over a production helper.
    for convert in (Fraction, lambda value: Fraction(str(value))):
        exact_pool = sum(map(convert, bounds), Fraction())
        exact_lower = max(Fraction(), convert(0.75) - exact_pool)
        assert Fraction(got["oracle_error_bound"]) >= exact_pool, got
        assert Fraction(got["corrected_lower_bound"]) <= exact_lower, got
    assert got["result"] == "inconclusive", got
    assert trace_claim(led, "c1")["verdict"] == got


@pytest.mark.parametrize("category", EXECUTABLE)
def test_sub_ulp_error_cannot_support_the_adjacent_target(category):
    led = Ledger()
    led.add_standard(H["STD"])
    led.add_vault("v1", H["FX"], files=1, roots=1)
    for index, bound in enumerate((0.5, 2.0 ** -54)):
        H["calibrated"](led, eid=f"e{index}", code="ad"[index], bound=bound, lower=0.75,
                        check_over={"oracle_category": category})
    got = H["verdict"](led, subject="sha256:" + "b" * 64, evidence_ids=("e0", "e1"),
                       estimand="true_correctness", target=math.nextafter(0.25, 0.0))
    assert got["result"] == "inconclusive", got
