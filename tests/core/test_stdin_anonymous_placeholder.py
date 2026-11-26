# SPDX-License-Identifier: Apache-2.0
"""Native ownership counterexamples stay isolated from the test runner's descriptors."""
import os
import subprocess
import sys
import textwrap

import pytest


CHILD = textwrap.dedent(r'''
    import gc
    import io
    import os
    import sys
    import tempfile
    from types import SimpleNamespace
    from g6.mcp import stdio

    mode, folder = sys.argv[1:]
    tempfile.tempdir = folder
    native_open, native_dup2, native_stat, native_unlink = os.open, os.dup2, os.stat, os.unlink
    attempts, slots, foreign, removals = [], [], [], []
    real_factory = stdio._InputFile
    def factory(*args, **kwargs):
        slot = (io.FileIO if mode == 'plain' else real_factory)(*args, **kwargs)
        slots.append(slot)
        return slot
    def opening(path, flags, *args, **kwargs):
        if os.path.basename(path).startswith('g6-stdin-'):
            attempts.append(flags)
            if mode in ('collision', 'fallback_pipe', 'fallback_null'):
                raise FileExistsError('single-attempt collision')
        return native_open(path, flags, *args, **kwargs)
    def copying(fd, target, **kwargs):
        unique = os.path.basename(slots[-1].name).startswith('g6-stdin-')
        assert os.listdir(folder) == ([os.path.basename(slots[-1].name)] if os.name == 'nt' and unique else [])
        if mode == 'before_copy':
            raise OSError('untouched unique placeholder')
        result = native_dup2(fd, target, **kwargs)
        if mode in ('plain', 'fallback_pipe', 'fallback_null'):
            os.close(target)
            if mode == 'fallback_null':
                other = native_open(os.devnull, os.O_RDONLY)
            else:
                other, writer = os.pipe()
                foreign.append(writer)
            if other != target:
                native_dup2(other, target)
                os.close(other)
            foreign.append(target)
            raise OSError('copy consumed its target before failing')
        return result
    def stat(path, *args, **kwargs):
        result = native_stat(path, *args, **kwargs)
        if mode == 'zero_identity' and isinstance(path, int):
            return SimpleNamespace(st_dev=result.st_dev, st_ino=0)
        return result
    def unlink(path):
        removals.append(path)
        if mode == 'first_unlink_failure' and len(removals) == 1:
            raise OSError('first unlink failed')
        return native_unlink(path)
    stdio._InputFile = factory
    stdio.os = SimpleNamespace(**(vars(os) | {'open': opening, 'dup2': copying, 'stat': stat, 'unlink': unlink}))
    source = io.FileIO(os.devnull, 'rb')
    owner = stdio._InputDescriptor()
    try:
        try:
            owner.duplicate(source.fileno())
        except OSError as exc:
            assert '[G6_E_STDIN_HANDOFF]' in str(exc)
            assert mode in ('plain', 'fallback_pipe', 'fallback_null', 'before_copy')
        else:
            assert mode not in ('plain', 'fallback_pipe', 'fallback_null', 'before_copy')
        owner.close()
        owner.close()
        gc.collect()
        assert len(attempts) == 1, attempts
        assert attempts[0] & os.O_EXCL
        if os.name == 'nt':
            assert attempts[0] & os.O_TEMPORARY
        assert owner.file is None
        if mode == 'first_unlink_failure':
            assert len(removals) == 2, removals
        for number in foreign:
            os.fstat(number)
        assert os.listdir(folder) == [], 'placeholder name survived'
        print('OWNERSHIP_OK')
    finally:
        owner.close()
        source.close()
        for number in foreign:
            os.close(number)
''')


@pytest.mark.parametrize("mode", ["success", "collision", "zero_identity", "before_copy", "plain",
                                  "fallback_pipe", "fallback_null",
                                  pytest.param("first_unlink_failure", marks=pytest.mark.skipif(
                                      os.name == "nt", reason="Windows uses native delete-on-close"))])
def test_single_attempt_placeholder_and_failed_copy_retirement(tmp_path, mode):
    done = subprocess.run([sys.executable, "-B", "-c", CHILD, mode, str(tmp_path)],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert b"OWNERSHIP_OK" in done.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-on-close handle")
def test_process_death_removes_a_placeholder_before_any_copy(tmp_path):
    child = textwrap.dedent(r'''
        import io
        import sys
        import tempfile
        from types import SimpleNamespace
        import os
        from g6.mcp import stdio
        tempfile.tempdir = sys.argv[1]
        def die(*args, **kwargs):
            os._exit(29)
        stdio.os = SimpleNamespace(**(vars(os) | {'dup2': die}))
        with io.FileIO(os.devnull, 'rb') as source:
            stdio._InputDescriptor().duplicate(source.fileno())
    ''')
    done = subprocess.run([sys.executable, "-B", "-c", child, str(tmp_path)],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    assert done.returncode == 29, done.stderr.decode(errors="replace")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows needs no success-path unlink")
def test_an_unlink_interrupt_is_preserved_and_its_owned_name_is_removed(tmp_path):
    child = textwrap.dedent(r'''
        import io
        import os
        import sys
        import tempfile
        from types import SimpleNamespace
        import anyio
        from g6.mcp import stdio
        tempfile.tempdir = sys.argv[1]
        native_unlink = os.unlink
        calls = []
        def unlink(path):
            calls.append(path)
            if len(calls) == 1:
                raise KeyboardInterrupt('unlink interrupted')
            native_unlink(path)
        stdio.os = SimpleNamespace(**(vars(os) | {'unlink': unlink}))
        reader, writer = os.pipe()
        os.write(writer, b'caller bytes\n')
        os.close(writer)
        source = io.FileIO(reader, 'rb')
        async def run():
            try:
                async with stdio._lines(anyio.wrap_file(source)):
                    raise AssertionError('interrupted startup yielded')
            except KeyboardInterrupt as exc:
                assert str(exc) == 'unlink interrupted'
            else:
                raise AssertionError('interrupt was swallowed')
        try:
            anyio.run(run)
            assert len(calls) == 2
            assert source.read() == b'caller bytes\n'
            assert os.listdir(sys.argv[1]) == []
        finally:
            source.close()
    ''')
    done = subprocess.run([sys.executable, "-B", "-c", child, str(tmp_path)],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
