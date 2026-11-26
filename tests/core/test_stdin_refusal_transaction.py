# SPDX-License-Identifier: Apache-2.0
"""Refusal preserves caller text; split signatures and overlapping sessions retain bytes."""
import subprocess
import sys
import textwrap

import pytest


CHILD = textwrap.dedent(r'''
    import codecs
    import io
    import os
    import sys
    import anyio
    from g6.mcp import stdio
    from g6.mcp._stdin_cache import cache_reader

    case = sys.argv[1]
    owned = '\u2252 caller text\n'
    cases = {
        'cp932': ('cp932', b'launcher\n\x87\x90' + owned[1:].encode('cp932')),
        'utf16_be': ('utf-16', codecs.BOM_UTF16_BE + ('launcher\n' + owned).encode('utf-16-be')),
        'utf32_be': ('utf-32', codecs.BOM_UTF32_BE + ('launcher\n' + owned).encode('utf-32-be')),
        'opaque': ('euc_jp', ('launcher\n' + owned).encode('euc_jp')),
        'lossy': ('utf-8', b'launcher\ninvalid\xff\n'),
        'descriptor': ('cp1252', b'launcher\ncaller text\n'),
    }
    encoding, wire = cases[case]
    read_fd, write_fd = os.pipe()
    os.write(write_fd, wire)
    os.close(write_fd)
    errors = 'replace' if case == 'lossy' else 'strict'
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, 'rb')), encoding=encoding, errors=errors)
    assert source.read(len('launcher\n')) == 'launcher\n'
    def refusal(*args):
        raise OSError('[G6_E_STDIN_HANDOFF] injected descriptor refusal')
    async def enter():
        async with stdio._lines(anyio.wrap_file(source)):
            raise AssertionError('refusal yielded')
    try:
        if case == 'descriptor':
            stdio._InputDescriptor.duplicate = refusal
            operation = lambda: anyio.run(enter)
        else:
            operation = lambda: cache_reader(source)
        try:
            operation()
        except (OSError, ValueError) as exc:
            assert '[G6_E_STDIN_HANDOFF]' in str(exc)
        else:
            raise AssertionError('codec was not refused')
        expected = 'caller text\n' if case == 'descriptor' else ('invalid\ufffd\n' if case == 'lossy' else owned)
        assert source.read() == expected, 'refusal consumed caller text'
    finally:
        source.close()
''')


@pytest.mark.parametrize("case", ["cp932", "utf16_be", "utf32_be", "opaque", "lossy", "descriptor"])
def test_refusal_is_a_transaction_for_pending_text(case):
    done = subprocess.run([sys.executable, "-B", "-c", CHILD, case],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    assert done.returncode == 0, done.stderr.decode(errors="replace")


@pytest.mark.parametrize("prefix", [b"\xef", b"\xef\xbb"])
@pytest.mark.parametrize("newline", [None, "\n", "\r\n"])
def test_split_utf8_signature_is_kept_when_the_decoder_has_no_character(prefix, newline):
    child = textwrap.dedent(r'''
        import io
        import os
        import sys
        from g6.mcp._stdin_cache import cache_reader
        prefix = bytes.fromhex(sys.argv[1])
        newline = {'none': None, 'lf': '\n', 'crlf': '\r\n'}[sys.argv[2]]
        reader, writer = os.pipe()
        os.write(writer, prefix)
        os.close(writer)
        source = io.TextIOWrapper(io.BufferedReader(io.FileIO(reader, 'rb')), encoding='utf-8-sig', newline=newline)
        assert source.read() == ''
        cached, _ = cache_reader(source)
        assert cached() == prefix
        source.close()
    ''')
    label = {None: "none", "\n": "lf", "\r\n": "crlf"}[newline]
    done = subprocess.run([sys.executable, "-B", "-c", child, prefix.hex(), label],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    assert done.returncode == 0, done.stderr.decode(errors="replace")


def test_a_second_session_does_not_close_the_first_sessions_blocked_reader():
    child = textwrap.dedent(r'''
        import io
        import os
        import threading
        from types import SimpleNamespace
        import anyio
        from g6.mcp import stdio
        first_read, first_write = os.pipe()
        second_read, second_write = os.pipe()
        first = io.FileIO(first_read, 'rb')
        second = io.FileIO(second_read, 'rb')
        waiting = threading.Event()
        finished = threading.Event()
        native_read = os.read
        target = []
        native_reader = stdio._read_lines
        def reader(descriptor, *args):
            if not target:
                target.append(descriptor.file.fileno())
                try:
                    return native_reader(descriptor, *args)
                finally:
                    finished.set()
            return native_reader(descriptor, *args)
        def read(fd, size):
            if target and fd == target[0]:
                waiting.set()
            return native_read(fd, size)
        stdio._read_lines = reader
        stdio.os = SimpleNamespace(**(vars(os) | {'read': read}))
        async def run():
            with anyio.fail_after(5):
                async with stdio._lines(anyio.wrap_file(first)):
                    while not waiting.is_set():
                        await anyio.sleep(0.001)
                os.fstat(target[0])
                os.write(second_write, b'second session\n')
                os.close(second_write)
                async with stdio._lines(anyio.wrap_file(second)) as lines:
                    assert [line async for line in lines] == ['second session\n']
                os.fstat(target[0])
                os.close(first_write)
                while not finished.is_set():
                    await anyio.sleep(0.001)
                try:
                    os.fstat(target[0])
                except OSError:
                    pass
                else:
                    raise AssertionError('first reader retained its own duplicate')
        try:
            anyio.run(run)
        finally:
            first.close()
            second.close()
    ''')
    done = subprocess.run([sys.executable, "-B", "-c", child], stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=20)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
