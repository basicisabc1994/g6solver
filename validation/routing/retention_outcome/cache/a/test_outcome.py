# SPDX-License-Identifier: Apache-2.0

def test_regression():
    import engine
    engine._cache.clear()
    assert engine.report_total([2, 3]) == 5
    assert engine.report_total([4, 7]) == 11
    rows = [4, 7]
    rows[0] = 8
    assert engine.report_total(rows) == 15
    assert engine.report_total([]) == 0
