# SPDX-License-Identifier: Apache-2.0
"""Canonical stops, independent refusal state, and strict UTF-8 reason storage."""
import asyncio
import ast
import builtins
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client

from g6.mcp import build_server, improve
from g6.metaharness import runner
from g6.self_maintenance import envelope as e


SETTINGS = {'windows': [{'provider': 'fixture', 'allowance': 1,
                         'week_start': '2026-11-09T00:00:00+00:00'}]}


TEXT = ('\ud800', '\udfff', '\x00', '\x01\x1f\x7f', 'a\u2028b', 'x' * 100_000,
        'a\nb\r\tc', 'ordinary café 🚀', '', '~g6-text:literal')


def prepared(root):
    (root / 'settings.json').write_text(json.dumps(SETTINGS), encoding='utf-8')
    log = e.CycleLog(root / e.CYCLE_LOG)
    log.enrol('Ada', {})
    log.start('Ada', stops=0, settings=e.digest_of(SETTINGS), enrolment=e.enrolment_digest(log))
    return log.started()


def call(entry, root, text):
    if entry == 'python':
        return e.ask_to_stop(root, text)
    if entry == 'improve':
        return improve.stop(str(root), text)
    async def request():
        async with Client(build_server()) as client:
            return (await client.call_tool('g6_improve_stop', {'folder': str(root), 'reason': text})).data
    return asyncio.run(request())


@pytest.mark.parametrize('entry', ['python', 'improve', 'mcp'])
@pytest.mark.parametrize('text', TEXT, ids=lambda s: ascii(s[:15]))
def test_hard_reason_ends_the_approved_start_at_every_entry(tmp_path, monkeypatch, entry, text):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    start = prepared(tmp_path)
    assert runner._gate(tmp_path, runner.read_settings(tmp_path)) == ('', '')
    reply = call(entry, tmp_path, text)
    assert e.what_stops(tmp_path, start)[1]
    assert runner._gate(tmp_path, runner.read_settings(tmp_path))[1]
    reopened = e.CycleLog(tmp_path / e.CYCLE_LOG)
    # Direct ask_to_stop historically leaves the ledger note to its caller. Its independent gate must suffice.
    if entry != 'python':
        assert not reopened.start_in_force()
        expected = ('a STOP file is present: ' + (' '.join(text.split()) or 'stopped through g6_improve_stop'))
        expected = expected.encode('utf-8', 'backslashreplace').decode('utf-8')
        assert reopened._rows()[-1][1]['reason'] == expected.splitlines()[0]
    if isinstance(reply, dict):
        assert reply['recorded']['the stop'] is True
        assert reply['canonical_stop'] == e.canonical_stops(tmp_path)
        json.dumps(reply, ensure_ascii=False).encode('utf-8')
        if any(0xd800 <= ord(c) <= 0xdfff for c in text):
            assert 'escaped' in reply['message']
    elif any(0xd800 <= ord(c) <= 0xdfff for c in text):
        assert 'escaped' in str(reply)


def test_canonical_stop_precedes_every_reason_operation(tmp_path, monkeypatch):
    start = prepared(tmp_path)
    original = e.stop_entries
    seen = []
    def rendered(reason, ident):
        assert e.what_stops(tmp_path, start)[1], 'the stop must stand before rendering'
        records = list((tmp_path / 'stop_records').glob('*/stop.json'))
        assert len(records) == 1
        value = json.loads(records[0].read_text(encoding='ascii'))
        assert set(value) == {'id', 'at'}
        if not seen:
            seen.append(True)
            raise RuntimeError('reason renderer failed')
        return original(reason, ident)
    monkeypatch.setattr(e, 'stop_entries', rendered)
    result = improve.stop(str(tmp_path), 'caller payload')
    assert runner._gate(tmp_path, runner.read_settings(tmp_path))[1]
    assert result['code'].startswith('G6_E_')


@pytest.mark.parametrize('state', ['empty', 'partial', 'unreadable', 'wrong-id', 'wrong-time', 'foreign-separator'])
def test_uncertain_canonical_state_refuses_start_and_tick(tmp_path, monkeypatch, state):
    start = prepared(tmp_path)
    folder = tmp_path / 'stop_records' / ('a' * 32)
    folder.mkdir(parents=True)
    path = folder / 'stop.json'
    if state == 'partial':
        path.write_bytes(b'{"id":')
    elif state in {'unreadable', 'wrong-id', 'wrong-time', 'foreign-separator'}:
        path.write_text(json.dumps({'id': 'b' * 32 if state == 'wrong-id' else 'a' * 32,
                                    'at': 'no time' if state == 'wrong-time' else
                                    '2026-11-09\ud80000:00:00+00:00' if state == 'foreign-separator' else
                                    '2026-11-09T00:00:00+00:00'}),
                        encoding='ascii')
    if state == 'unreadable':
        original = Path.read_bytes
        def read(p):
            if p == path:
                raise PermissionError('canonical record unreadable')
            return original(p)
        monkeypatch.setattr(Path, 'read_bytes', read)
    assert e.what_stops(tmp_path, start)[1]
    assert runner._gate(tmp_path, runner.read_settings(tmp_path))[1]
    assert improve._cannot_start(tmp_path, e.stop_in_force(tmp_path))[0]['code'] == e.STOP_UNREADABLE
    assert improve.start(tmp_path, 'Ada')['code'] != improve.STARTED


class ForeignFault(BaseException):
    pass


def errors():
    found = []
    for cls in sorted({x for x in vars(builtins).values()
                       if isinstance(x, type) and issubclass(x, BaseException)}, key=lambda c: c.__name__):
        if cls is UnicodeEncodeError:
            err = cls('utf-8', '\ud800', 0, 1, 'test')
        elif cls is UnicodeDecodeError:
            err = cls('utf-8', b'\xff', 0, 1, 'test')
        elif cls is UnicodeTranslateError:
            err = cls('\ud800', 0, 1, 'test')
        elif cls is BaseExceptionGroup:
            err = cls('test', [SystemExit(7)])
        elif cls is ExceptionGroup:
            err = cls('test', [ValueError('test')])
        else:
            err = cls('test')
        assert type(err) is cls
        found.append(err)
    return [*found, asyncio.CancelledError(), ForeignFault()]


@pytest.mark.parametrize('error', errors(), ids=lambda x: type(x).__name__)
@pytest.mark.parametrize('stage', ['gate-directory', 'reservation', 'open', 'first-byte', 'mid-line', 'flush', 'close',
                                  'reason', 'count', 'note'])
def test_every_write_failure_denies_stale_approval(tmp_path, monkeypatch, error, stage):
    start = prepared(tmp_path)
    real_open = builtins.open
    low_open, low_write, low_sync, low_close = os.open, os.write, os.fsync, os.close
    original_mkdir = Path.mkdir
    descriptors = set()
    hit = []
    def mkdir(path, *args, **kwargs):
        target = path.name == 'stop_records' if stage == 'gate-directory' else path.parent.name == 'stop_records'
        if stage in {'gate-directory', 'reservation'} and target:
            hit.append(stage)
            raise error
        return original_mkdir(path, *args, **kwargs)
    def os_open(path, *args, **kwargs):
        target = Path(path).name == 'stop.json'
        if target and stage == 'open':
            hit.append(stage)
            raise error
        descriptor = low_open(path, *args, **kwargs)
        if target:
            descriptors.add(descriptor)
        return descriptor
    def os_write(descriptor, data):
        if descriptor in descriptors and stage in {'first-byte', 'mid-line'}:
            if stage == 'mid-line':
                low_write(descriptor, data[:len(data) // 2])
            hit.append(stage)
            raise error
        return low_write(descriptor, data)
    def os_sync(descriptor):
        if descriptor in descriptors and stage == 'flush':
            hit.append(stage)
            raise error
        return low_sync(descriptor)
    def os_close(descriptor):
        selected = descriptor in descriptors
        descriptors.discard(descriptor)
        low_close(descriptor)
        if selected and stage == 'close':
            hit.append(stage)
            raise error
    def opened(path, mode='r', *args, **kwargs):
        name = Path(path).name
        target = name == 'STOP' and stage == 'reason'
        target |= name == e.STOPS_FILE and stage == 'count'
        if target and stage in {'open', 'reason', 'count'}:
            hit.append(stage)
            raise error
        return real_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', opened)
    monkeypatch.setattr(os, 'open', os_open)
    monkeypatch.setattr(os, 'write', os_write)
    monkeypatch.setattr(os, 'fsync', os_sync)
    monkeypatch.setattr(os, 'close', os_close)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    if stage == 'note':
        def note(*args, **kwargs):
            hit.append(stage)
            raise error
        monkeypatch.setattr(e.CycleLog, 'stop', note)
    try:
        reply = improve.stop(str(tmp_path), 'ordinary reason')
    except BaseException as exc:
        pytest.fail(f'stop write escaped its receipt boundary: {type(exc).__name__}')
    assert hit == [stage], 'the selected write stage must actually be reached once'
    assert e.what_stops(tmp_path, start)[1]
    assert runner._gate(tmp_path, runner.read_settings(tmp_path))[1]
    assert reply['code'].startswith('G6_E_')
    json.dumps(reply, ensure_ascii=False).encode('utf-8')


def test_stop_survives_an_import_only_process_restart_without_legacy_files(tmp_path):
    prepared(tmp_path)
    improve.stop(str(tmp_path), '\ud800')
    (tmp_path / 'STOP').unlink(missing_ok=True)
    (tmp_path / e.STOPS_FILE).unlink(missing_ok=True)
    # A fake legacy ledger still saying approved must not become executable.
    path = tmp_path / e.CYCLE_LOG
    rows = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b''.join(rows[:-1]))
    program = ('import sys; sys.path[:0]=[sys.argv[1]]; '
               'from pathlib import Path; from g6.metaharness.runner import _gate, read_settings; '
               'assert _gate(Path(sys.argv[2]), read_settings(Path(sys.argv[2])))[1]')
    result = subprocess.run([sys.executable, '-I', '-c', program, str(Path(e.__file__).resolve().parents[2]),
                             str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('stage', ['gate-directory', 'reservation', 'open', 'first-byte', 'mid-line', 'flush'])
def test_failed_canonical_write_and_failed_legacy_writes_survive_restart(tmp_path, monkeypatch, stage):
    prepared(tmp_path)
    original_open, original_write, original_sync = os.open, os.write, os.fsync
    original_mkdir = Path.mkdir
    descriptors = set()
    hit = []
    def mkdir(path, *args, **kwargs):
        target = path.name == 'stop_records' if stage == 'gate-directory' else path.parent.name == 'stop_records'
        if stage in {'gate-directory', 'reservation'} and target:
            hit.append(stage)
            raise OSError('canonical directory creation failed')
        return original_mkdir(path, *args, **kwargs)
    def opened(path, *args, **kwargs):
        target = Path(path).name == 'stop.json'
        if target and stage == 'open':
            hit.append(stage)
            raise OSError('canonical open failed')
        descriptor = original_open(path, *args, **kwargs)
        if target:
            descriptors.add(descriptor)
        return descriptor
    def written(descriptor, data):
        if descriptor in descriptors and stage in {'first-byte', 'mid-line'}:
            if stage == 'mid-line':
                original_write(descriptor, data[:7])
            hit.append(stage)
            raise OSError('canonical byte write failed')
        return original_write(descriptor, data)
    def synced(descriptor):
        if descriptor in descriptors and stage == 'flush':
            hit.append(stage)
            raise OSError('canonical sync failed')
        return original_sync(descriptor)
    original = builtins.open
    def no_legacy(path, mode='r', *args, **kwargs):
        if Path(path).name in {'STOP', e.STOPS_FILE} and mode == 'a':
            raise OSError('no legacy write can record the stop')
        return original(path, mode, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', opened)
        patch.setattr(os, 'write', written)
        patch.setattr(os, 'fsync', synced)
        patch.setattr(Path, 'mkdir', mkdir)
        patch.setattr(builtins, 'open', no_legacy)
        e.ask_to_stop(tmp_path, '\ud800')
    assert hit
    assert e.CycleLog(tmp_path / e.CYCLE_LOG).start_in_force()
    program = ('import sys; sys.path[:0]=[sys.argv[1]]; from pathlib import Path; '
               'from g6.metaharness.runner import _gate, read_settings; '
               'from g6.mcp.improve import _cannot_start, STOP_UNREADABLE, stop_in_force; '
               'root=Path(sys.argv[2]); assert _gate(root, read_settings(root))[1]; '
               # The merge with the stop's follow-up: the start's check takes the reading, and an uncertain stop state
               # has the one code every reader gives it.
               'assert _cannot_start(root, stop_in_force(root))[0]["code"] == STOP_UNREADABLE')
    result = subprocess.run([sys.executable, '-I', '-c', program, str(Path(e.__file__).resolve().parents[2]),
                             str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_stop_entry_inventory_is_derived_from_code():
    root = Path(e.__file__).resolve().parents[1]
    found = set()
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if name in {'ask_to_stop', 'improve.stop', '_ask_to_stop'}:
                    functions = [f for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                                 and node in ast.walk(f)]
                    owner = min(functions, key=lambda f: f.end_lineno - f.lineno).name if functions else '<module>'
                    found.add((path.relative_to(root).as_posix(), owner, name))
    assert found == {('mcp/__init__.py', 'g6_improve_stop', 'improve.stop'),
                     ('mcp/improve.py', 'stop', 'ask_to_stop'),
                     # The merge with the stop's follow-up: a start that is revoked, and an ending on a stop that
                     # cannot be read, write a stop through the same writer.
                     ('mcp/improve.py', '_revoke', 'ask_to_stop'),
                     ('self_maintenance/envelope.py', 'end_start', 'ask_to_stop'),
                     ('metaharness/loop.py', 'lost_cycle', 'ask_to_stop'),
                     ('self_maintenance/envelope.py', 'ask_to_stop', '_ask_to_stop'),
                     ('self_maintenance/envelope.py', 'ask_to_stop_receipt', '_ask_to_stop')}
    assert list(inspect.signature(improve.stop).parameters) == ['folder', 'reason']
    assert list(inspect.signature(e.ask_to_stop).parameters) == ['root', 'reason', 'record_id']
    # The merge with the stop's follow-up: the gate takes the one reading, and the canonical stops are part of it.
    assert '_gated(' in inspect.getsource(runner._admission)
    assert 'canonical_stops(root)' in inspect.getsource(e.stop_in_force)


@pytest.mark.parametrize('ident', TEXT, ids=lambda s: ascii(s[:15]))
def test_python_supplied_identity_never_enters_the_canonical_record(tmp_path, ident):
    start = prepared(tmp_path)
    reply = e.ask_to_stop(tmp_path, 'stop', record_id=ident)
    assert e.what_stops(tmp_path, start)[1], reply
    assert e.stops_asked(tmp_path) == (2 if ident == 'a\nb\r\tc' else 1)
    records = list((tmp_path / 'stop_records').glob('*/stop.json'))
    assert len(records) == 1
    row = json.loads(records[0].read_text(encoding='ascii'))
    assert set(row) == {'id', 'at'}
    assert len(row['id']) == 32 and all(c in '0123456789abcdef' for c in row['id'])


def test_canonical_identity_is_generated_even_when_the_caller_id_looks_safe(tmp_path, monkeypatch):
    prepared(tmp_path)
    monkeypatch.setattr(e, 'uuid4', lambda: SimpleNamespace(hex='b' * 32))
    e.ask_to_stop(tmp_path, 'stop', record_id='a' * 32)
    rows = list((tmp_path / 'stop_records').glob('*/stop.json'))
    assert len(rows) == 1
    assert json.loads(rows[0].read_text(encoding='ascii'))['id'] == 'b' * 32
    assert (tmp_path / 'STOP').read_bytes().endswith(('a' * 32 + '\n').encode('ascii'))


@pytest.mark.parametrize('kind', ['start', 'stop', 'enrol'])
@pytest.mark.parametrize('ident', TEXT, ids=lambda s: ascii(s[:15]))
def test_ledger_identity_receipt_reads_back_the_same_stored_id(tmp_path, kind, ident):
    log = e.CycleLog(tmp_path / e.CYCLE_LOG)
    def write():
        return (log.enrol('Ada', {}, record_id=ident) if kind == 'enrol' else
                log.start('Ada', record_id=ident) if kind == 'start' else
                log.stop('stop', record_id=ident))
    if not ident:
        with pytest.raises(e.LedgerError, match='non-empty'):
            write()
        return
    returned = write()
    expected = ident.encode('utf-8', 'backslashreplace').decode('utf-8')
    assert returned == expected
    name = {'enrol': 'enrolled', 'start': 'started', 'stop': 'stopped'}[kind]
    reopened = e.CycleLog(log.path)
    assert reopened.latest_id(name) == returned
    assert reopened.record(name, returned) is not None
    assert reopened.record(name, ident) is not None


@pytest.mark.parametrize('fallback', ['complete', 'partial'])
def test_selective_creation_failures_use_an_existing_count_gate_after_restart(tmp_path, monkeypatch, fallback):
    prepared(tmp_path)
    (tmp_path / 'STOP').write_bytes(b'an earlier stop\n')
    (tmp_path / e.STOPS_FILE).write_bytes(b'an earlier stop old-id\n')
    log = e.CycleLog(tmp_path / e.CYCLE_LOG)
    fields = {**log.started(), **e.stop_to_lift(tmp_path), 'stops': 1}
    fields.pop('by')
    log.start('Ada', **fields)
    assert runner._gate(tmp_path, runner.read_settings(tmp_path)) == ('', '')
    mkdir, opened = Path.mkdir, os.open
    reached = []
    def no_directory(path, *args, **kwargs):
        if path.name == 'stop_records':
            reached.append('directory')
            raise PermissionError('new children denied; existing files stay appendable')
        return mkdir(path, *args, **kwargs)
    def no_marker(path, *args, **kwargs):
        if Path(path).name == 'stop.denied':
            reached.append('marker')
            raise PermissionError('new children denied; existing files stay appendable')
        return opened(path, *args, **kwargs)
    ordinary_open = builtins.open
    class Partial:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def write(self, text):
            self.handle.write(text[:7])
            raise OSError('fallback count interrupted before the newline')
        def __exit__(self, *args):
            self.handle.close()
    def interrupted_count(path, mode='r', *args, **kwargs):
        handle = ordinary_open(path, mode, *args, **kwargs)
        return Partial(handle) if Path(path).name == e.STOPS_FILE and mode == 'a' else handle
    class Reason(str):
        def split(self, *args):
            pytest.fail('caller text reached before a canonical stop could be established')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'mkdir', no_directory)
        patch.setattr(os, 'open', no_marker)
        if fallback == 'partial':
            patch.setattr(builtins, 'open', interrupted_count)
        receipt = e.ask_to_stop(tmp_path, Reason('caller payload'))
    assert reached == ['directory', 'marker']
    assert 'G6_E_' in str(receipt)
    assert e.stops_asked(tmp_path) == (2 if fallback == 'complete' else -1)
    assert b'caller payload' not in (tmp_path / e.STOPS_FILE).read_bytes()
    assert (tmp_path / 'STOP').read_bytes() == b'an earlier stop\n'
    program = ('import sys; sys.path[:0]=[sys.argv[1]]; from pathlib import Path; '
               'from g6.metaharness.runner import _gate, read_settings; '
               'from g6.mcp.improve import _cannot_start, RECORDS_UNREADABLE; '
               'root=Path(sys.argv[2]); assert _gate(root, read_settings(root))[1]\n'
               'if sys.argv[3] == "partial":\n assert _cannot_start(root)["code"] == RECORDS_UNREADABLE\n')
    result = subprocess.run([sys.executable, '-I', '-c', program, str(Path(e.__file__).resolve().parents[2]),
                             str(tmp_path), fallback], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_live_refusal_uses_the_same_identity_through_another_path_spelling(tmp_path, monkeypatch):
    root = tmp_path / 'real'
    root.mkdir()
    (root / 'child').mkdir()
    alias = root / 'child' / '..'
    prepared(root)
    assert alias.resolve() == root.resolve()
    assert runner._gate(root, runner.read_settings(root)) == ('', '')
    mkdir, os_open, ordinary_open = Path.mkdir, os.open, builtins.open
    def no_directory(path, *args, **kwargs):
        if path.name == 'stop_records':
            raise PermissionError('no reservation')
        return mkdir(path, *args, **kwargs)
    def no_marker(path, *args, **kwargs):
        if Path(path).name == 'stop.denied':
            raise PermissionError('no marker')
        return os_open(path, *args, **kwargs)
    def no_count(path, mode='r', *args, **kwargs):
        if Path(path).name == e.STOPS_FILE and mode == 'a':
            raise PermissionError('no durable fallback in this same-process challenge')
        return ordinary_open(path, mode, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'mkdir', no_directory)
        patch.setattr(os, 'open', no_marker)
        patch.setattr(builtins, 'open', no_count)
        receipt = e.ask_to_stop(alias, '\ud800')
        assert runner._gate(root, runner.read_settings(root))[1], str(receipt)


@pytest.mark.parametrize('observation', ['wrong-valid-time', 'omitted-own-record'])
def test_reason_waits_for_this_records_exact_bytes_and_observation(tmp_path, monkeypatch, observation):
    prepared(tmp_path)
    old = tmp_path / 'stop_records' / ('a' * 32)
    old.mkdir(parents=True)
    (old / 'stop.json').write_bytes((json.dumps({'id': 'a' * 32, 'at': '2026-11-09T00:00:00+00:00'},
                                               sort_keys=True) + '\n').encode('ascii'))
    log = e.CycleLog(tmp_path / e.CYCLE_LOG)
    fields = {**log.started(), **e.stop_to_lift(tmp_path)}
    fields.pop('by')
    log.start('Ada', **fields)
    assert runner._gate(tmp_path, runner.read_settings(tmp_path)) == ('', '')
    read, iterdir = Path.read_bytes, Path.iterdir
    reached = []
    def wrong_bytes(path):
        held = read(path)
        if path.name == 'stop.json' and path.parent != old:
            reached.append(observation)
            value = json.loads(held)
            value['at'] = '2001-01-01T00:00:00+00:00'
            return (json.dumps(value, sort_keys=True) + '\n').encode('ascii')
        return held
    def omitted(path):
        if path == tmp_path / 'stop_records':
            reached.append(observation)
            return iter([old])
        return iterdir(path)
    class Reason(str):
        def split(self, *args):
            pytest.fail('reason was touched without observing this call\'s exact id/time bytes')
    with monkeypatch.context() as patch:
        if observation == 'wrong-valid-time':
            patch.setattr(Path, 'read_bytes', wrong_bytes)
        else:
            patch.setattr(Path, 'iterdir', omitted)
        receipt = e.ask_to_stop(tmp_path, Reason('caller'))
    assert reached
    assert 'G6_E_' in str(receipt)
    assert runner._gate(tmp_path, runner.read_settings(tmp_path))[1]


def test_an_uncertain_count_cannot_equal_an_approved_count_or_allow_a_start(tmp_path):
    prepared(tmp_path)
    (tmp_path / e.STOPS_FILE).write_bytes(b'G6_E_LOOP_STOP_CANONICAL_FAILED partial')
    log = e.CycleLog(tmp_path / e.CYCLE_LOG)
    fields = {**log.started(), 'stops': -1}
    fields.pop('by')
    log.start('Ada', **fields)
    assert e.stops_asked(tmp_path) == -1
    assert e.what_stops(tmp_path, log.started())[1]
    assert runner._gate(tmp_path, runner.read_settings(tmp_path))[1]
    assert improve._cannot_start(tmp_path, e.stop_in_force(tmp_path))[0]['code'] == e.STOP_UNREADABLE


@pytest.mark.parametrize('entry', ['python', 'improve', 'mcp'])
@pytest.mark.parametrize('text', TEXT, ids=lambda s: ascii(s[:15]))
def test_unusable_stop_locators_return_a_stable_refusal(tmp_path, monkeypatch, entry, text):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    target = tmp_path / ('input-' + text)
    try:
        result = call(entry, target, 'stop')
    except BaseException as exc:
        pytest.fail(f'unusable locator escaped the stop boundary: {type(exc).__name__}')
    code = result['code'] if isinstance(result, dict) else str(result)
    assert 'G6_E_' in code


@pytest.mark.parametrize('field', ['stop', 'start-name', 'start-reason', 'start-note', 'enrol', 'resume', 'demote', 'pause',
                                  'heartbeat', 'refusal'])
@pytest.mark.parametrize('text', TEXT[:-2], ids=lambda s: ascii(s[:15]))
def test_one_storage_policy_preserves_every_previously_storable_field(tmp_path, field, text):
    log = e.CycleLog(tmp_path / e.CYCLE_LOG)
    if not text:
        text = 'empty default'
    if field == 'heartbeat':
        e.beat(tmp_path, note=text)
        assert e.heartbeat(tmp_path)['note'] == text.encode('utf-8', 'backslashreplace').decode('utf-8')
        return
    if field == 'refusal':
        e.note_refusal(tmp_path, 'a' * 64, text)
        expected = ' '.join(text.split()).encode('utf-8', 'backslashreplace').decode('utf-8')
        assert (tmp_path / e.REFUSED_FILE).read_text(encoding='utf-8') == 'a' * 64 + ' ' + expected + '\n'
        return
    if field == 'stop':
        log.stop(text)
    elif field.startswith('start'):
        log.start(text if field == 'start-name' else 'Ada', **{field.split('-')[-1]: text})
    elif field == 'enrol':
        log.enrol(text, {})
    else:
        getattr(log, field)(text)
    expected = text.encode('utf-8', 'backslashreplace').decode('utf-8')
    assert expected in e.CycleLog(log.path)._rows()[-1][1].values()


@pytest.mark.parametrize('entry', ['python-run', 'cli-run', 'python-tick'])
def test_no_execution_entry_reuses_approval_when_only_canonical_stop_remains(tmp_path, monkeypatch, entry):
    from g6.metaharness.loop import ShadowLoop
    start = prepared(tmp_path)
    e.ask_to_stop(tmp_path, '\ud800')
    (tmp_path / 'STOP').unlink(missing_ok=True)
    (tmp_path / e.STOPS_FILE).unlink(missing_ok=True)
    if entry == 'python-tick':
        fake = type('Loop', (), {'root': tmp_path, 'cannot_record': '',
                                'log': e.CycleLog(tmp_path / e.CYCLE_LOG)})()
        assert ShadowLoop._stop(fake)[1]
    else:
        monkeypatch.setattr(runner, 'open_loop', lambda *a, **k: pytest.fail('stale approval reached domain loading'))
        monkeypatch.setattr(runner, 'runner_lock', lambda *a: __import__('contextlib').nullcontext())
        monkeypatch.setattr(runner, 'beat', lambda *a, **k: None)
        result = runner.main(['run', str(tmp_path)]) if entry == 'cli-run' else runner.run(tmp_path)
        assert result in {0, 1}
    assert e.what_stops(tmp_path, start)[1]
