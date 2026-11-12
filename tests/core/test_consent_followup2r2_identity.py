# SPDX-License-Identifier: Apache-2.0
"""A denied gate cannot stand in for this request's canonical stop record."""
import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.mcp import improve
from g6.metaharness import runner
from g6.metaharness.loop import ShadowLoop
from g6.self_maintenance import envelope as e


@pytest.mark.parametrize('canonical_refused', [True, False])
def test_repeated_time_stop_uses_its_canonical_record_never_an_earlier_note(
        tmp_path, monkeypatch, canonical_refused):
    settings = {'windows': [{'provider': 'fixture', 'allowance': 1,
                            'week_start': '2026-11-09T00:00:00+00:00'}]}
    (tmp_path / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
    log = e.CycleLog(tmp_path / e.CYCLE_LOG)
    log.enrol('Ada', {'domain_file': ''})

    def agreed_start():
        log = e.CycleLog(tmp_path / e.CYCLE_LOG)
        log.start('Ada', stops=e.stops_asked(tmp_path), settings=e.digest_of(settings),
                  enrolment=e.enrolment_digest(log), **e.stop_to_lift(tmp_path))
        assert runner._gate(tmp_path, settings) == ('', '')

    class RepeatedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 9, tzinfo=timezone.utc)

    monkeypatch.setattr(improve, 'datetime', RepeatedClock)
    monkeypatch.setattr(e, 'datetime', RepeatedClock)
    agreed_start()
    assert improve.stop(str(tmp_path), 'earlier stop')['code'] == improve.STOP_ASKED
    agreed_start()
    assert improve._cannot_start(tmp_path, e.stop_in_force(tmp_path))[0] is None
    earlier_binding = improve._terms(tmp_path)
    earlier = e.CycleLog(tmp_path / e.CYCLE_LOG)._rows()
    records = set((tmp_path / e.STOP_RECORDS).glob('*/stop.json'))
    canonical = e.canonical_stops(tmp_path)
    low_open = e.os.open

    def refused_legacy(*args, **kwargs):
        raise PermissionError('legacy stop writes refused')

    def refused_canonical(path, *args, **kwargs):
        if Path(path).name == 'stop.json':
            raise PermissionError('canonical stop write refused')
        return low_open(path, *args, **kwargs)

    monkeypatch.setattr(e, 'open', refused_legacy, raising=False)
    if canonical_refused:
        monkeypatch.setattr(e.os, 'open', refused_canonical)
    else:
        monkeypatch.setattr(e.CycleLog, 'stop', refused_legacy)
    reply = improve.stop(str(tmp_path), 'new stop at the same time')
    assert reply['code'] == (improve.STOP_FAILED if canonical_refused else improve.STOP_ASKED), reply
    assert reply['recorded']['its line in the STOP file'] is False
    assert reply['recorded']['its count in stops.log'] is False
    assert reply['recorded']['its note in the constraint log'] is False
    assert e.CycleLog(tmp_path / e.CYCLE_LOG)._rows() == earlier
    if canonical_refused:
        assert reply['canonical_stop_id'] is None
        assert e.canonical_stops(tmp_path) is None
    else:
        own = tmp_path / e.STOP_RECORDS / reply['canonical_stop_id'] / 'stop.json'
        assert own not in records and own.is_file()
        assert json.loads(own.read_bytes())['id'] == reply['canonical_stop_id']
        assert reply['canonical_stop'] == e.canonical_stops(tmp_path) != canonical
        assert 'the stop is on record' in reply['message']
        assert 'its line in the STOP file, its count in stops.log, its note in the constraint log are not' in reply['message']
    # Both a completed stop and uncertain stop state refuse every old execution path.
    assert runner._gate(tmp_path, settings)[1]
    fake = type('Loop', (), {'root': tmp_path, 'cannot_record': '',
                            'log': e.CycleLog(tmp_path / e.CYCLE_LOG)})()
    assert ShadowLoop._stop(fake)[1]
    monkeypatch.setattr(improve, '_spawn', lambda *a: pytest.fail('old approval spawned a runner'))
    assert improve.start(tmp_path, 'Ada', shown=earlier_binding)['code'] != improve.STARTED
    monkeypatch.setattr(runner, 'open_loop', lambda *a, **k: pytest.fail('old approval loaded the domain'))
    monkeypatch.setattr(runner, 'runner_lock', lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(runner, 'beat', lambda *a, **k: None)
    assert runner.run(tmp_path) in {0, 1}


def test_failed_canonical_stop_cannot_borrow_even_a_reused_legacy_note_id(tmp_path, monkeypatch):
    monkeypatch.setattr(improve, 'uuid4', lambda: SimpleNamespace(hex='f' * 32))
    test_repeated_time_stop_uses_its_canonical_record_never_an_earlier_note(tmp_path, monkeypatch, True)
