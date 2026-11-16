# SPDX-License-Identifier: Apache-2.0
"""CLI calls restore their caller and reuse an outer capture without nesting."""
import os
import subprocess
import sys
import textwrap

import pytest


SCRIPT = textwrap.dedent('''
    import io
    import json
    import logging
    import sys
    import threading
    import warnings
    from contextlib import redirect_stdout, redirect_stderr
    from g6.cli import main
    from g6.cli._capture import InvokeCapture
    from g6.core.registry import get_registry
    from g6.core.result import Result

    root = logging.getLogger()
    def state():
        return (sys.stdout, sys.stderr, warnings.showwarning, warnings.filters[:],
                threading.excepthook, sys.unraisablehook, sys.excepthook,
                root.handlers[:], root.level, logging.lastResort)

    def restore(saved):
        (sys.stdout, sys.stderr, warnings.showwarning, filters,
         threading.excepthook, sys.unraisablehook, sys.excepthook,
         handlers, level, logging.lastResort) = saved
        warnings.filters[:] = filters
        root.handlers[:] = handlers
        root.setLevel(level)

    scenario, mode = sys.argv[1:]
    flags = ['--json'] if mode == 'json' else []
    argv = ['invoke', 'grounding', 'check_claim', '{}', *flags]
    calls = 0
    outer = None
    def infer(self, data):
        global calls
        calls += 1
        print('REENTRY_SECRET')
        warnings.warn('REENTRY_SECRET')
        logging.getLogger('reentry').error('REENTRY_SECRET')
        if scenario == 'nested' and calls == 1:
            assert main(argv) == 0
        if scenario == 'interrupt':
            raise KeyboardInterrupt
        if scenario == 'failure':
            raise ValueError('REENTRY_SECRET')
        return Result.ok({'answer': 42})

    get_registry().get_class('grounding').infer = infer
    original = state()
    try:
        class BrokenStream(io.StringIO):
            def write(self, text):
                raise OSError('REENTRY_SECRET')
        out = BrokenStream() if scenario == 'stdout_failure' else io.StringIO()
        err = BrokenStream() if scenario == 'stderr_failure' else io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            saved = state()
            if scenario == 'outer':
                outer = InvokeCapture()
                installed = state()
                for _ in range(2):
                    assert main(argv) == 0
                    assert state() == installed, 'outer capture was replaced or restored'
                assert outer.counts['warnings'] == 2, 'calls did not share the warning tally'
                assert outer.counts['log records'] == 2, 'calls did not share the logging tally'
            else:
                if scenario == 'interrupt':
                    try:
                        main(argv)
                    except KeyboardInterrupt:
                        pass
                    else:
                        raise AssertionError('interrupt was swallowed')
                else:
                    assert main(argv) == (1 if scenario in ('failure', 'stdout_failure') else 0)
                assert state() == saved, 'main did not restore the caller state'
                if scenario == 'repeat':
                    # The second call must use the streams current for that call.
                    second_out, second_err = io.StringIO(), io.StringIO()
                    with redirect_stdout(second_out), redirect_stderr(second_err):
                        second_saved = state()
                        assert main(argv) == 0
                        assert state() == second_saved
                    assert json.loads(second_out.getvalue())['answer' if mode == 'plain' else 'ok'] == (
                        42 if mode == 'plain' else True)
                    assert '1 warnings' in second_err.getvalue()
                if scenario == 'failure':
                    assert 'G6_E_INVOKE_FAILED' in out.getvalue() + err.getvalue()
        if scenario not in ('interrupt', 'failure', 'stdout_failure') or mode == 'json' and scenario == 'failure':
            decoder = json.JSONDecoder()
            replies, remaining = [], out.getvalue().lstrip()
            while remaining:
                reply, end = decoder.raw_decode(remaining)
                replies.append(reply)
                remaining = remaining[end:].lstrip()
            assert len(replies) == (2 if scenario in ('outer', 'nested') else 1), 'missing whole reply'
            for reply in replies:
                if mode == 'json':
                    assert reply['request_id'] and reply['ok'] is (scenario != 'failure')
                else:
                    assert reply == {'answer': 42}
        assert 'REENTRY_SECRET' not in out.getvalue() + err.getvalue()
    finally:
        # Test cleanup also exposes assertion tracebacks against the build-1 capture.
        restore(original)
    print('passed')
''')


@pytest.mark.parametrize('scenario', ['repeat', 'outer', 'nested', 'failure', 'interrupt',
                                    'stdout_failure', 'stderr_failure'])
@pytest.mark.parametrize('mode', ['plain', 'json'])
def test_main_reentry_and_restoration(tmp_path, scenario, mode):
    env = {**os.environ, 'G6_HOME': str(tmp_path / 'home'),
           'G6_CLAUDE': r'C:\nonexistent\g6-no-host.exe', 'G6_CODEX': r'C:\nonexistent\g6-no-host.exe'}
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', SCRIPT, scenario, mode],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'passed\n', result.stdout
