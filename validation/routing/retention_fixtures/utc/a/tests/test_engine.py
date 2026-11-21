# SPDX-License-Identifier: Apache-2.0
import engine

def test_ordinary():
    assert str(engine.event_day('2026-01-02T12:00:00+00:00')) == '2026-01-02'
