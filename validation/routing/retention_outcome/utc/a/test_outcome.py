# SPDX-License-Identifier: Apache-2.0

def test_regression():
    import engine
    assert str(engine.event_day('2026-01-02T00:30:00+02:00')) == '2026-01-01'
    assert str(engine.event_day('2026-01-01T23:30:00-02:00')) == '2026-01-02'
