# SPDX-License-Identifier: Apache-2.0
"""Code-derived stop-call faults, overlapping requests, and receipt read faults.

The call instrument changes only test-loaded functions, never product files. Its
queue grows when an injected fault opens another handler or fallback branch.
"""
import ast
import asyncio
import importlib.util
import inspect
import itertools
import json
import os
import textwrap
import threading
from pathlib import Path
from functools import lru_cache
from types import SimpleNamespace

import pytest

from g6.mcp import improve
from g6.metaharness import runner
from g6.metaharness.api import _envelope
from g6.self_maintenance import envelope as e


FUNCTIONS = {
    improve: ('stop', '_root', '_is_a_loop', '_log', '_stop_before', '_stop_on_record',
              '_not_a_loop', '_stop_entry_on_record', '_stop_read_back', '_stop_reply'),
    e: ('ask_to_stop', 'ask_to_stop_receipt', '_ask_to_stop', '_establish_stop',
        'stop_entries', 'stop_reason', 'canonical_stop_reason', 'canonical_stops',
        'canonical_stop_record', '_canonical_stops_on_disk', 'record_stop', 'runner_alive',
        'stop_standing', 'what_stops', 'stops_asked',
        'utf8_text', 'utf8_fields', '_on_a_clock', '_unreadable'),
    e.CycleLog: ('__init__', '_write_record', 'stop', 'record', '_rows', 'started',
                 'start_in_force'),
}
READS = {'_log', 'CycleLog', 'record', 'start_in_force', 'started', 'is_file',
         'is_dir', 'stat', 'read_bytes', 'read_text', 'iterdir', 'canonical_stops',
         'canonical_stop_record', '_canonical_stops_on_disk', 'stop_reason',
         'canonical_stop_reason', 'stop_standing', 'runner_alive',
         '_stop_entry_on_record', '_stop_on_record', 'entries', 'get', 'reload',
         'resolve', 'absolute', 'expanduser', 'mutex', 'ChainLedger', 'stops_asked'}


def clear_test_latches():
    for name in ('_STOP_PENDING', '_STOP_UNCERTAIN', '_STOP_UNRESOLVED'):
        if hasattr(e, name):
            getattr(e, name).clear()


@pytest.fixture(autouse=True)
def isolated_latches():
    clear_test_latches()
    yield
    clear_test_latches()


def prepared(root):
    root.mkdir(parents=True, exist_ok=True)
    settings = {'windows': [{'provider': 'fixture', 'allowance': 1,
                            'week_start': '2026-11-09T00:00:00+00:00'}]}
    (root / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
    log = e.CycleLog(root / e.CYCLE_LOG)
    log.enrol('Ada', {})
    log.start('Ada', stops=0, settings=e.digest_of(settings), enrolment=e.enrolment_digest(log))
    return settings


@lru_cache(maxsize=None)
def compiled_stop_calls(module_name, name, function, original_code):
    """Cache only the AST transformation; every product call and fault still runs.

    Function and code objects are both cache keys, so a replaced implementation
    cannot reuse another implementation's compiled call sites.
    """
    assert function.__code__ is original_code
    inventory = {}
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))

    class Calls(ast.NodeTransformer):
        def visit_Call(self, node):
            label = ast.unparse(node.func)
            site = f'{module_name}.{name}:{node.lineno}:{node.col_offset}:{label}'
            inventory[site] = label.rsplit('.', 1)[-1]
            self.generic_visit(node)
            # Installing the denial itself is the entry primitive. It does
            # no path work, entropy, clock reading, rendering or disk work.
            if label == '_begin_stop':
                return node
            return ast.copy_location(ast.Call(ast.Name('_stop_fault', ast.Load()),
                [ast.Constant(site), ast.Lambda(ast.arguments(posonlyargs=[], args=[],
                 kwonlyargs=[], kw_defaults=[], defaults=[]), node)], []), node)

    tree = Calls().visit(tree)
    ast.fix_missing_locations(tree)
    namespace = dict(function.__globals__)
    exec(compile(tree, function.__code__.co_filename, 'exec'), namespace)
    replacement = namespace[name]
    return (replacement.__code__, replacement.__defaults__, replacement.__kwdefaults__,
            replacement.__closure__, inventory)


def instrument(patch, callback):
    inventory = {}
    originals = {(module, name): getattr(module, name) for module, names in FUNCTIONS.items()
                 for name in names if hasattr(module, name)}
    for (module, name), function in originals.items():
        code, defaults, keywords, closure, places = compiled_stop_calls(
            module.__name__, name, function, function.__code__)
        inventory.update(places)
        replacement = type(function)(code, function.__globals__, name, defaults, closure)
        replacement.__kwdefaults__ = dict(keywords) if keywords is not None else None
        patch.setitem(function.__globals__, '_stop_fault', callback)
        patch.setattr(module, name, replacement)
    # Imported aliases otherwise retain the original function objects and hide
    # their internal reads from the inventory, despite a wrapped outer call.
    for name in FUNCTIONS[e]:
        # only a function the module has (``originals`` holds no other): a listed name a later merge took away
        # (``_unreadable``, folded into the one stop reading) has no alias to move, and asking the module for it raised
        if (e, name) in originals and getattr(improve, name, None) is originals[(e, name)]:
            patch.setattr(improve, name, getattr(e, name))
    patch.setattr(improve, 'ask_to_stop', e.ask_to_stop_receipt)
    return inventory


@pytest.mark.parametrize('entry', ['python', 'managed', 'improve', 'default', 'earlier', 'relative', 'literal-tilde',
                                 'python-id', 'surrogate', 'settings-missing', 'lifted', 'count-only'])
@pytest.mark.parametrize('fault', [OSError, BaseException])
@pytest.mark.parametrize('mode', ['ordinary', 'directory-refused', 'creation-refused', 'legacy-refused',
                                 'zero-progress', 'fallback-mismatch'])
def test_every_reached_stop_call_fault_leaves_the_old_approval_denied(
        tmp_path, monkeypatch, record_property, entry, fault, mode):
    queue = [()]
    tried = set()
    for index, trace in enumerate(queue):
        target = trace[-1] if trace else None
        clear_test_latches()
        root = (tmp_path / str(index) / 'home' / 'improve' if entry == 'earlier' else
                tmp_path / str(index) / ('~' if entry == 'literal-tilde' else 'home-improve'))
        settings = prepared(root)
        (root / e.RUNNER_LOCK).mkdir()
        (root / e.RUNNER_LOCK / 'mutex').write_bytes(b'')
        if entry == 'settings-missing':
            (root / 'settings.json').unlink()
        if entry == 'lifted':
            (root / e.STOP_FILE).write_bytes(b'an earlier stop\n')
            log = e.CycleLog(root / e.CYCLE_LOG)
            log.start('Ada', stops=0, settings=e.digest_of(settings), enrolment=e.enrolment_digest(log),
                      **e.stop_to_lift(root))
        assert runner._gate(root, settings) == ('', '')
        seen = set()

        def call(site, invoke):
            seen.add(site)
            if site in trace:
                raise fault('planted call failure')
            return invoke()

        with monkeypatch.context() as patch:
            argument = root
            if entry in {'relative', 'literal-tilde'}:
                patch.chdir(root.parent)
                argument = Path(root.name)
            inventory = instrument(patch, call)
            patch.setattr(improve, 'g6_home', lambda: root.parent if entry == 'earlier' else root.with_name('home'))
            mkdir, low_open, ordinary_open, read = Path.mkdir, os.open, open, Path.read_bytes

            def reserve(path, *args, **kwargs):
                if mode in {'directory-refused', 'creation-refused', 'fallback-mismatch'} and path == root / e.STOP_RECORDS:
                    raise PermissionError('canonical directory refused')
                return mkdir(path, *args, **kwargs)

            def marker(path, *args, **kwargs):
                if mode in {'creation-refused', 'fallback-mismatch'} and Path(path).name == e.STOP_DENIED:
                    raise PermissionError('fallback marker refused')
                return low_open(path, *args, **kwargs)

            def legacy(path, mode_arg='r', *args, **kwargs):
                if entry == 'count-only' and Path(path).name == e.STOP_FILE:
                    raise PermissionError('only the count can be written')
                if mode == 'legacy-refused' and Path(path).name in {e.STOP_FILE, e.STOPS_FILE}:
                    raise PermissionError('legacy writes refused')
                return ordinary_open(path, mode_arg, *args, **kwargs)

            patch.setattr(Path, 'mkdir', reserve)
            patch.setattr(os, 'open', marker)
            patch.setattr(e, 'open', legacy, raising=False)
            if entry == 'count-only':
                def no_note(*args, **kwargs):
                    raise PermissionError('the constraint-log note cannot be written')
                patch.setattr(e.CycleLog, 'stop', no_note)
            if mode == 'zero-progress':
                patch.setattr(os, 'write', lambda fd, data: 0)
            if mode == 'fallback-mismatch':
                patch.setattr(Path, 'read_bytes', lambda path: b'' if path.name == e.STOPS_FILE else read(path))
            try:
                if entry in {'python', 'relative', 'literal-tilde'}:
                    e.ask_to_stop(argument, 'please stop')
                elif entry == 'python-id':
                    e.ask_to_stop(argument, 'please stop', record_id='caller-id')
                elif entry == 'surrogate':
                    e.ask_to_stop(argument, '\ud800')
                elif entry == 'managed':
                    e.ask_to_stop_receipt(root, 'please stop')
                else:
                    improve.stop('' if entry in {'default', 'earlier'} else str(root), 'please stop')
            except BaseException:
                pass  # cancellation may propagate; the old approval must still end
        assert runner._gate(root, settings)[1], f'{entry}: admission open after fault at {target}'
        if target is not None:
            assert target in seen, target
        for site in sorted(seen - tried):
            tried.add(site)
            queue.append((*trace, site))
    assert tried, 'the inventory never reached a stop call'
    record_property('injected_calls', json.dumps(sorted(tried)))
    record_property('source_calls', json.dumps(sorted(inventory)))
    record_property('fault_traces', json.dumps(queue[1:]))


def schedules(n):
    events = [(i, action) for i in range(n) for action in ('begin', 'end')]
    return [order for order in itertools.permutations(events)
            if all(order.index((i, 'begin')) < order.index((i, 'end')) for i in range(n))]


ORDERS = [(n, order, outcomes) for n in (2, 3) for order in schedules(n)
          for outcomes in itertools.product((False, True), repeat=n)]


@pytest.mark.parametrize('n,order,outcomes', ORDERS)
def test_each_stop_owns_its_latch_in_every_schedule(tmp_path, monkeypatch, n, order, outcomes):
    spec = importlib.util.spec_from_file_location('binding_fixture',
        Path(__file__).resolve().parents[1] / 'test_consent_binding.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    rig = fixture.Rig(tmp_path, monkeypatch)
    assert rig.call('g6_improve_enrol')['code'] == 'G6_E_LOOP_ENROLLED'
    monkeypatch.setattr(improve, '_spawn', lambda *a: object())
    monkeypatch.setattr(improve, '_watch', lambda *a: _envelope('qualified-draft', improve.STARTED, 'fake runner'))
    assert rig.call('g6_improve_start')['code'] == improve.STARTED
    root = rig.root
    entered = [threading.Event() for _ in range(n)]
    released = [threading.Event() for _ in range(n)]
    results = {}
    mkdir, low_open, ordinary_open = Path.mkdir, os.open, open

    def current():
        name = threading.current_thread().name
        return int(name[5:]) if name.startswith('stop-') else None

    def reserve(path, *args, **kwargs):
        i = current()
        if i is not None and path == root / e.STOP_RECORDS:
            entered[i].set()
            assert released[i].wait(30), 'test failed to release a stop'
            if not outcomes[i]:
                raise PermissionError('this request cannot reserve its canonical directory')
        return mkdir(path, *args, **kwargs)

    def marker(path, *args, **kwargs):
        i = current()
        if i is not None and not outcomes[i] and Path(path).name == e.STOP_DENIED:
            raise PermissionError('this request cannot create its marker')
        return low_open(path, *args, **kwargs)

    def counted(path, mode='r', *args, **kwargs):
        i = current()
        if i is not None and not outcomes[i] and Path(path).name == e.STOPS_FILE and mode == 'a':
            raise PermissionError('this request cannot append its count')
        return ordinary_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'mkdir', reserve)
    monkeypatch.setattr(os, 'open', marker)
    monkeypatch.setattr(e, 'open', counted, raising=False)

    def stop(i):
        try:
            results[i] = improve.stop(str(root), f'stop {i}')
        except BaseException as exc:
            results[i] = exc

    threads = [threading.Thread(target=stop, args=(i,), name=f'stop-{i}') for i in range(n)]
    active, failed = set(), set()
    try:
        for i, action in order:
            if action == 'begin':
                threads[i].start()
                assert entered[i].wait(30)
                active.add(i)
            else:
                released[i].set()
                threads[i].join(30)
                assert not threads[i].is_alive()
                assert isinstance(results[i], dict), results[i]
                active.remove(i)
                if not outcomes[i]:
                    failed.add(i)
                    assert results[i]['code'] == improve.STOP_FAILED

            async def agree(**kwargs):
                return SimpleNamespace(action='accept', content={'value': 'Ada'})

            ctx = SimpleNamespace(session=SimpleNamespace(elicit_form=agree), request_id='test')
            reply = asyncio.run(rig.server.tools['g6_improve_start'](ctx=ctx, folder=str(root)))
            if active or failed:
                assert reply['code'] != improve.STARTED, (order, outcomes, i, action)
                assert runner._gate(root, rig.settings)[1]
            else:
                assert reply['code'] == improve.STARTED, reply
    finally:
        for release in released:
            release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(30)


@pytest.mark.parametrize('fault', [PermissionError, OSError])
@pytest.mark.parametrize('mode', ['ordinary', 'legacy-refused'])
def test_each_post_write_read_fault_returns_its_canonical_identity(tmp_path, monkeypatch, record_property, fault, mode):
    queue = [()]
    tried = set()
    for index, trace in enumerate(queue):
        target = trace[-1] if trace else None
        root = tmp_path / str(index)
        prepared(root)
        (root / e.RUNNER_LOCK).mkdir()
        (root / e.RUNNER_LOCK / 'mutex').write_bytes(b'')
        seen, durable = set(), []
        failed_parts = []
        write = os.write

        def written(fd, data):
            result = write(fd, data)
            durable.append(True)
            return result

        def call(site, invoke):
            leaf = site.rsplit(':', 1)[-1].rsplit('.', 1)[-1]
            if durable and leaf in READS:
                seen.add(site)
                if site in trace:
                    if 'runner_alive' in site:
                        failed_parts.extend(('running', 'stopped'))
                    if '._stop_on_record:' in site and leaf == '_stop_entry_on_record':
                        failed_parts.append('its line in the STOP file' if 'stop_entry' in invoke.__code__.co_freevars
                                            else 'its count in stops.log')
                    raise fault('post-write read refused')
            return invoke()

        with monkeypatch.context() as patch:
            instrument(patch, call)
            patch.setattr(os, 'write', written)
            if mode == 'legacy-refused':
                def refused(*args, **kwargs):
                    raise PermissionError('legacy entries refused')
                patch.setattr(e, 'open', refused, raising=False)
            try:
                reply = improve.stop(str(root), 'please stop')
            except BaseException as exc:
                pytest.fail(f'read {target} escaped the receipt: {type(exc).__name__}')
        assert reply['canonical_stop_id'], (target, reply)
        assert reply['recorded'], (target, reply)
        if target is not None:
            assert target in seen, target
            for part in failed_parts:
                assert reply.get(part, reply['recorded'].get(part)) is None, (target, part, reply)
        for site in sorted(seen - tried):
            tried.add(site)
            queue.append((*trace, site))
    assert tried
    record_property('injected_reads', json.dumps(sorted(tried)))
    record_property('fault_traces', json.dumps(queue[1:]))


@pytest.mark.parametrize('fault', [OSError, BaseException])
def test_non_loop_stop_calls_still_install_denial_first(tmp_path, monkeypatch, record_property, fault):
    """No approval exists yet here; a refused reply must still retain its first-act denial."""
    queue, tried = [()], set()
    for index, trace in enumerate(queue):
        clear_test_latches()
        root = tmp_path / str(index)
        root.mkdir()
        seen = set()

        def call(site, invoke):
            seen.add(site)
            if site in trace:
                raise fault('non-loop reply call refused')
            return invoke()

        with monkeypatch.context() as patch:
            instrument(patch, call)
            try:
                improve.stop(str(root))
            except BaseException:
                pass
        assert e.canonical_stops(root) != '', (trace, 'the non-loop reply lost its denial')
        if trace:
            assert trace[-1] in seen
        for site in sorted(seen - tried):
            tried.add(site)
            queue.append((*trace, site))
    record_property('injected_calls', json.dumps(sorted(tried)))
    record_property('fault_traces', json.dumps(queue[1:]))


def test_latch_precedes_folder_resolution_and_preflight_in_the_code():
    for function in (improve.stop, e.ask_to_stop, e.ask_to_stop_receipt, e._establish_stop):
        tree = ast.parse(inspect.getsource(function))
        body = tree.body[0].body
        statements = [node for node in body if not isinstance(node, ast.Expr)
                      or not isinstance(node.value, ast.Constant)]
        assert '_begin_stop' in ast.unparse(statements[0]), function.__name__


def test_denial_insertion_calls_no_fallible_helper():
    function = getattr(e, '_begin_stop', None)
    assert function is not None, 'there is no denial primitive before fallible stop work'
    tree = ast.parse(inspect.getsource(function))
    assert not any(isinstance(node, ast.Call) for node in ast.walk(tree))
    assert '_STOP_PENDING[token] = root' in ast.unparse(tree)


@pytest.mark.parametrize('persistent', [False, True])
def test_legacy_entropy_failure_keeps_the_canonical_identity_and_receipt(tmp_path, monkeypatch, persistent):
    settings = prepared(tmp_path)
    random = os.urandom
    draws = []

    def unavailable(size):
        draws.append(size)
        if len(draws) == 2 or persistent and len(draws) > 2:
            raise OSError('legacy receipt entropy unavailable')
        return random(size)

    monkeypatch.setattr(os, 'urandom', unavailable)
    reply = improve.stop(str(tmp_path), 'please stop')
    assert draws == [16, 16]
    assert reply['code'] == improve.STOP_ASKED, reply
    assert reply['canonical_stop_id']
    assert e.canonical_stop_record(tmp_path, reply['canonical_stop_id']) is True
    assert reply['recorded']['its line in the STOP file'] is True
    assert reply['recorded']['its count in stops.log'] is True
    assert runner._gate(tmp_path, settings)[1]


def test_total_entropy_failure_returns_an_unidentified_denial_receipt(tmp_path, monkeypatch):
    from g6.invariants.envelope import check_envelope

    settings = prepared(tmp_path)

    def unavailable(size):
        raise OSError('OS entropy unavailable')

    monkeypatch.setattr(os, 'urandom', unavailable)
    reply = improve.stop(str(tmp_path), 'please stop')
    assert reply['code'] == improve.STOP_FAILED
    assert reply['canonical_stop_id'] is None
    assert check_envelope(reply) == []
    assert runner._gate(tmp_path, settings)[1]


def test_failed_canonical_read_back_keeps_identity_and_unknown_state(tmp_path, monkeypatch):
    settings = prepared(tmp_path)
    read = Path.read_bytes
    hit = []

    def unreadable(path):
        if path.name == 'stop.json':
            hit.append(path)
            raise PermissionError('canonical bytes cannot be read back')
        return read(path)

    monkeypatch.setattr(Path, 'read_bytes', unreadable)
    reply = improve.stop(str(tmp_path), 'please stop')
    assert hit
    assert reply['code'] == improve.STOP_FAILED
    assert reply['canonical_stop_id'], reply
    assert reply['canonical_stop'] is None
    assert reply['recorded']['the stop'] is None
    assert runner._gate(tmp_path, settings)[1]


def test_an_unreadable_alias_cannot_lose_its_denial_when_all_writes_fail(tmp_path, monkeypatch):
    settings = prepared(tmp_path)
    (tmp_path / 'child').mkdir()
    alias = tmp_path / 'child' / '..'
    resolve, low_open, ordinary_open = Path.resolve, os.open, open
    assert runner._gate(tmp_path, settings) == ('', '')

    def unreadable(path, *args, **kwargs):
        if path == alias:
            raise PermissionError('the alias cannot be resolved')
        return resolve(path, *args, **kwargs)

    def marker(path, *args, **kwargs):
        if Path(path).name == e.STOP_DENIED:
            raise PermissionError('the marker cannot be created')
        return low_open(path, *args, **kwargs)

    def count(path, mode='r', *args, **kwargs):
        if Path(path).name == e.STOPS_FILE:
            raise PermissionError('the fallback count cannot be written')
        return ordinary_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', unreadable)
    monkeypatch.setattr(os, 'open', marker)
    monkeypatch.setattr(e, 'open', count, raising=False)
    receipt = e.ask_to_stop(alias, 'please stop')
    assert 'G6_E_' in str(receipt)
    assert e.CycleLog(tmp_path / e.CYCLE_LOG).start_in_force()
    assert runner._gate(tmp_path, settings)[1]


@pytest.mark.parametrize('fault', [PermissionError, OSError])
def test_fallback_count_read_failure_is_in_the_receipt(tmp_path, monkeypatch, record_property, fault):
    settings = prepared(tmp_path)
    mkdir, low_open, read, sync = Path.mkdir, os.open, Path.read_bytes, os.fsync
    synced, hits = [], []

    def reserve(path, *args, **kwargs):
        if path.name == e.STOP_RECORDS:
            raise PermissionError('canonical directories refused')
        return mkdir(path, *args, **kwargs)

    def marker(path, *args, **kwargs):
        if Path(path).name == e.STOP_DENIED:
            raise PermissionError('marker refused')
        return low_open(path, *args, **kwargs)

    def flushed(fd):
        sync(fd)
        synced.append(True)

    def unreadable(path):
        if path.name == e.STOPS_FILE:
            assert synced, 'the planted read must follow the fallback durable write'
            hits.append(True)
            raise fault('fallback count cannot be read')
        return read(path)

    tree = ast.parse(inspect.getsource(e._establish_stop))
    sites = [f'{e.__name__}._establish_stop:{node.lineno}:{node.col_offset}:{ast.unparse(node.func)}'
             for node in ast.walk(tree) if isinstance(node, ast.Call)
             and ast.unparse(node.func) == '(root / STOPS_FILE).read_bytes']
    assert len(sites) == 1
    monkeypatch.setattr(Path, 'mkdir', reserve)
    monkeypatch.setattr(os, 'open', marker)
    monkeypatch.setattr(os, 'fsync', flushed)
    monkeypatch.setattr(Path, 'read_bytes', unreadable)
    reply = improve.stop(str(tmp_path), 'please stop')
    assert hits
    assert reply['code'] == improve.STOP_FAILED
    assert reply['canonical_stop_id'] is None  # canonical reservation failed before drawing its identity
    assert reply['recorded']['its count in stops.log'] is None
    assert runner._gate(tmp_path, settings)[1]
    record_property('injected_reads', json.dumps(sites))


def test_loop_preflight_read_failure_cannot_skip_the_durable_stop(tmp_path, monkeypatch):
    settings = prepared(tmp_path)
    is_file = Path.is_file

    def unreadable(path):
        if path == tmp_path / 'settings.json':
            raise PermissionError('settings metadata cannot be inspected')
        return is_file(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'is_file', unreadable)
        assert runner._gate(tmp_path, settings) == ('', '')
        reply = improve.stop(str(tmp_path), 'please stop')
    assert reply['code'] == improve.STOP_ASKED
    assert reply['canonical_stop_id']
    clear_test_latches()
    assert runner._gate(tmp_path, settings)[1], 'the stop must survive loss of all process latches'


def test_an_uncertain_default_choice_keeps_the_denial_on_the_earlier_folder(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    root = home / 'improve'
    settings = prepared(root)
    is_file = Path.is_file

    def unreadable(path):
        if path == root / 'settings.json':
            raise PermissionError('the earlier default cannot be inspected')
        return is_file(path)

    monkeypatch.setattr(improve, 'g6_home', lambda: home)
    monkeypatch.setattr(Path, 'is_file', unreadable)
    assert runner._gate(root, settings) == ('', '')
    try:
        improve.stop('')
    except OSError:
        pass  # no folder was selected; the raw default request must still deny admission
    assert runner._gate(root, settings)[1]
