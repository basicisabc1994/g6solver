# SPDX-License-Identifier: Apache-2.0
"""Caller state, process shutdown and the legacy MCP export surface."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def child(script, *args):
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'G6_HOME': str(root / '.build/home'), 'PYTHONPATH': str(root / 'src'),
           'G6_CLAUDE': r'C:\nonexistent\g6-no-host.exe',
           'G6_CODEX': r'C:\nonexistent\g6-no-host.exe'}
    return subprocess.run([sys.executable, '-X', 'utf8', '-c', textwrap.dedent(script), *args],
                          cwd=root, env=env, capture_output=True, text=True, timeout=45)


@pytest.mark.parametrize('reuse', [False, True])
def test_capture_installation_failure_restores_every_completed_step(reuse):
    # Inject at every executed installation line, including Routing's constructor.
    # Removing rollback at any earlier mutation must strand observable caller state.
    result = child('''
        import logging, sys, threading, warnings
        from g6 import _streams
        from g6.cli import _capture
        if sys.argv[1] == 'True':
            _streams.routing()
        original = sys.stdout, sys.stderr
        root = logging.getLogger()
        named = logging.getLogger('repair3.prebound')
        handlers = [logging.StreamHandler(original[0]), logging.StreamHandler(original[1])]
        named.handlers = handlers
        logging.lastResort = logging.StreamHandler(original[1])
        hooks = warnings.showwarning, threading.excepthook, sys.unraisablehook, sys.excepthook
        filters = warnings.filters
        logging_state = root.handlers, root.level, logging.lastResort
        start = threading.Thread.start
        codes = {_capture.InvokeCapture.__init__.__code__, _streams.Routing.__init__.__code__}
        if hasattr(_capture.InvokeCapture, '_install'):
            codes.add(_capture.InvokeCapture._install.__code__)
        count = 0
        fail_at = None
        class Injected(BaseException): pass
        def trace(frame, event, arg):
            global count
            if event == 'line' and frame.f_code in codes:
                count += 1
                if count == fail_at:
                    raise Injected()
            return trace
        sys.settrace(trace)
        capture = _capture.InvokeCapture()
        sys.settrace(None)
        capture.restore()
        steps = count
        for fail_at in range(1, steps + 1):
            count = 0
            sys.settrace(trace)
            try:
                _capture.InvokeCapture()
            except Injected:
                pass
            else:
                raise AssertionError(('injection missed', fail_at))
            finally:
                sys.settrace(None)
            assert sys.stdout is original[0] and sys.stderr is original[1], ('streams', fail_at)
            assert (warnings.showwarning, threading.excepthook, sys.unraisablehook, sys.excepthook) == hooks, ('hooks', fail_at)
            assert warnings.filters is filters, ('filters', fail_at)
            assert root.handlers is logging_state[0] and root.level == logging_state[1], ('root', fail_at)
            assert logging.lastResort is logging_state[2], ('lastResort', fail_at)
            assert [h.stream for h in handlers] == list(original), ('handlers', fail_at)
            assert logging.lastResort.stream is original[1], ('fallback stream', fail_at)
            assert threading.Thread.start is start, ('thread hook', fail_at)
            assert _capture.current_capture() is None, ('active capture', fail_at)
            if sys.argv[1] == 'True':
                assert original[0].owner.defaults == original[0].owner.originals, ('targets', fail_at)
        print('restored', steps, 'installation boundaries')
    ''', str(reuse))
    assert result.returncode == 0, result.stderr
    assert 'installation boundaries' in result.stdout


def test_mcp_exports_match_the_pre_lazy_public_surface():
    result = child('''
        import subprocess
        import g6.mcp as mcp
        source = subprocess.check_output(['git', 'show', '5d7481f:src/g6/mcp/__init__.py'])
        before = {'__name__': 'g6.mcp'}
        exec(compile(source, '<legacy-mcp>', 'exec'), before)
        expected = {name for name in before if not name.startswith('_')}
        assert set(mcp.__all__) == expected, (set(mcp.__all__) ^ expected)
        namespace = {}
        exec('from g6.mcp import *', namespace)
        assert {name for name in namespace if not name.startswith('_')} == expected
        for name in ('SENSITIVE_ENV', 'allowed_sensitive', '_allowed', 'public_instructions'):
            assert name in dir(mcp)
            assert getattr(mcp, name) is before[name]
        assert set(vars(mcp)) <= set(dir(mcp))
    ''')
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('step', ['warning_context', 'buffer', 'counter', 'targets', 'root_level'])
@pytest.mark.parametrize('after', [False, True])
def test_capture_rolls_back_a_failing_installation_operation(step, after):
    result = child('''
        import logging, sys, threading, warnings
        from g6 import _streams
        from g6.cli import _capture
        original = sys.stdout, sys.stderr
        root = logging.getLogger()
        root.setLevel(logging.ERROR)
        state = root.handlers, root.level, logging.lastResort
        hooks = warnings.showwarning, threading.excepthook, sys.unraisablehook, sys.excepthook
        filters = warnings.filters
        targets = {
            'warning_context': (warnings.catch_warnings, '__enter__'),
            'buffer': (_capture._Buffer, '__init__'),
            'counter': (_capture._LogCounter, '__init__'),
            'targets': (_streams.Routing, 'set_defaults'),
            'root_level': (root, 'setLevel'),
        }
        obj, name = targets[sys.argv[1]]
        method = getattr(obj, name)
        class Injected(BaseException): pass
        def fail(*args, **kwargs):
            if sys.argv[2] == 'True': method(*args, **kwargs)
            raise Injected()
        setattr(obj, name, fail)
        try:
            try: _capture.InvokeCapture()
            except Injected: pass
            else: raise AssertionError('failure not injected')
        finally:
            setattr(obj, name, method)
        assert sys.stdout is original[0] and sys.stderr is original[1], 'streams'
        assert (warnings.showwarning, threading.excepthook, sys.unraisablehook, sys.excepthook) == hooks, 'hooks'
        assert warnings.filters is filters, 'filters'
        assert root.handlers is state[0] and root.level == state[1], 'root'
        assert logging.lastResort is state[2], 'lastResort'
        assert _capture.current_capture() is None, 'active capture'
    ''', step, str(after))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('failed', ['stdout', 'both'])
def test_console_shutdown_after_reused_router_keeps_failure_code(failed):
    result = child('''
        import sys
        from g6._streams import routing
        from g6.cli import entrypoint
        class Failed:
            def write(self, text): return len(text)
            def flush(self): raise OSError('PRIVATE')
        sys.stdout = Failed()
        if sys.argv[1] == 'both': sys.stderr = Failed()
        routing()
        sys.exit(entrypoint(['invoke', 'algebra', 'ops', '{}', '--json']))
    ''', failed)
    assert result.returncode == 1, result.stderr
    assert result.stdout == ''
    expected = '' if failed == 'both' else '[G6_E_INVOKE_FAILED] G6: output could not be written.\n'
    assert result.stderr == expected


@pytest.mark.parametrize('category', ['DeprecationWarning', 'PendingDeprecationWarning'])
def test_warning_roots_are_read_at_display_time_and_normalized(category):
    result = child('''
        import g6, os, sys, warnings
        from pathlib import Path
        from g6._warnings import install_process_warning_policy
        category = getattr(__builtins__, sys.argv[1])
        with warnings.catch_warnings(record=True) as shown:
            warnings.simplefilter('always')
            install_process_warning_policy()
            root = Path.cwd() / '.build/alternate/g6'
            g6.__path__.append(str(root / 'nested/..'))
            filename = str(root / 'module.py')
            if os.name == 'nt': filename = filename.swapcase()
            warnings.warn_explicit('G6', category, filename, 1)
            warnings.warn_explicit('EXTERNAL', category, str(root.parent / 'g6_vendor/module.py'), 1)
            g6.__path__.pop()
            warnings.warn_explicit('REMOVED', category, filename, 2)
        assert [str(item.message) for item in shown] == ['G6']
    ''', category)
    assert result.returncode == 0, result.stderr
