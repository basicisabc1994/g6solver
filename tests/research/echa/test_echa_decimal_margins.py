# SPDX-License-Identifier: Apache-2.0
"""Decimal equality must stay inconclusive for every executable oracle category."""
from pathlib import Path
import runpy

import pytest

from g6.research.echa import Ledger, trace_claim
from g6.research.echa.claims import EXECUTABLE

H = runpy.run_path(str(Path(__file__).with_name("test_echa_kernel_claims.py")))


@pytest.mark.parametrize("category", EXECUTABLE)
@pytest.mark.parametrize("lower_ticks,bound_ticks", [
    (lower, bound) for lower in range(1, 11) for bound in range(lower + 1)
])
def test_every_tenths_equality_is_inconclusive(category, lower_ticks, bound_ticks):
    # Integer ticks supply the equality independently of production arithmetic.
    led = Ledger()
    led.add_standard(H["STD"])
    led.add_vault("v1", H["FX"], files=1, roots=1)
    H["calibrated"](led, lower=lower_ticks / 10, bound=bound_ticks / 10,
                    check_over={"oracle_category": category})
    got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_correctness",
                       target=(lower_ticks - bound_ticks) / 10)
    assert got["result"] == "inconclusive", got
    assert "corrected_lower_bound_does_not_exceed_target" in got["reasons"]
    assert trace_claim(led, "c1")["verdict"] == got


@pytest.mark.parametrize("category", EXECUTABLE)
@pytest.mark.parametrize("lower,bound,target", [(0.8, 0.7, 0.09), (0.9, 0.02, 0.5), (1, 0, 0.99)])
def test_strict_decimal_margins_remain_usable(category, lower, bound, target):
    led = Ledger()
    led.add_standard(H["STD"])
    led.add_vault("v1", H["FX"], files=1, roots=1)
    H["calibrated"](led, lower=lower, bound=bound, check_over={"oracle_category": category})
    got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_correctness", target=target)
    assert got["result"] == "supported", got
    assert got["corrected_lower_bound"] > target
