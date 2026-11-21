# SPDX-License-Identifier: Apache-2.0
"""The census resolves static prefixes without executing component code."""
from __future__ import annotations

import pytest

from tests.core.test_refusals import dynamic_builders, written


@pytest.mark.parametrize("expression", [
    '_P + "BROKEN"', 'f"{_P}BROKEN"', '"%sBROKEN" % _P', '"{}BROKEN".format(_P)',
])
def test_census_resolves_each_finite_prefix_shape(expression):
    class Source:
        def read_text(self, **kwargs):
            return '_P = "G6_E_CENSUS_"\ncode = ' + expression

    assert written(Source()) == {"G6_E_CENSUS_BROKEN"}


@pytest.mark.parametrize("expression", [
    '_P + code', 'f"[{_P}{code}] refused"', '"%s%s" % (_P, code)', '"{}{}".format(_P, code)',
])
def test_census_requires_a_family_for_each_open_prefix_shape(expression):
    assert dynamic_builders('_P = "G6_E_CENSUS_"\nresult = ' + expression) == {"G6_E_CENSUS_{}"}
