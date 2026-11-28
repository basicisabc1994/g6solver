# SPDX-License-Identifier: Apache-2.0
"""Preserve accepted handoffs and native caller caches; isolate every descriptor fault."""
import queue
import subprocess
import sys
import textwrap
import threading

import pytest


def _child(source, *args):
    done = subprocess.run([sys.executable, "-B", "-c", textwrap.dedent(source), *args],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    assert done.returncode == 0, done.stderr.decode(errors="replace")


@pytest.mark.parametrize("encoding", ["mac-arabic", "mac-farsi", "cp932"])
@pytest.mark.parametrize("newline", ["universal", "lf", "crlf"])
@pytest.mark.parametrize("transport", ["file", "pipe"])
def test_consumed_cache_needs_no_inverse(tmp_path, encoding, newline, transport):
    _child(r'''
        import io, os, sys
        from g6.mcp._stdin_cache import cache_reader
        encoding, label, transport, folder = sys.argv[1:]
        newline = {'universal': None, 'lf': '\n', 'crlf': '\r\n'}[label]
        # Include a consumed ambiguous CP932 character, not just ASCII metadata.
        text = '\u2252 launcher\n' if encoding == 'cp932' else 'launcher\n'
        wire = text.encode(encoding)
        if transport == 'pipe':
            reader, writer = os.pipe()
            os.write(writer, wire)
            os.close(writer)
            raw = io.FileIO(reader, 'rb')
        else:
            path = os.path.join(folder, 'input')
            with open(path, 'wb') as stream:
                stream.write(wire)
            raw = io.FileIO(path, 'rb')
        with io.TextIOWrapper(io.BufferedReader(raw), encoding=encoding, newline=newline) as source:
            assert source.read(len(text)) == text
            cached, _ = cache_reader(source)
            assert cached() == b'', 'empty cache was changed or refused'
    ''', encoding, newline, transport, str(tmp_path))


@pytest.mark.parametrize("prefix,tail", [("eda0", "80"), ("e080", "af"), ("f49080", "80"), ("ff", "")])
@pytest.mark.parametrize("newline", ["universal", "lf", "crlf"])
def test_surrogateescape_checks_the_whole_cache_before_partial_decoder_bytes(prefix, tail, newline):
    _child(r'''
        import io, os, sys
        from g6.mcp._stdin_cache import cache_reader
        prefix, tail = (bytes.fromhex(value) for value in sys.argv[1:3])
        newline = {'universal': None, 'lf': '\n', 'crlf': '\r\n'}[sys.argv[3]]
        reader, writer = os.pipe()
        source = io.TextIOWrapper(io.BufferedReader(io.FileIO(reader, 'rb')),
                                  encoding='utf-8', errors='surrogateescape', newline=newline)
        try:
            os.write(writer, b'launcher\n' + prefix)
            assert source.read(len('launcher\n')) == 'launcher\n'
            remaining = tail + b'\nnext message\n'
            os.write(writer, remaining)
            os.close(writer)
            writer = None
            cached, _ = cache_reader(source)
            got = cached()
            while chunk := os.read(source.fileno(), 65536):
                got += chunk
            assert got == prefix + remaining, 'surrogateescape bytes changed order'
            assert source.read() == '', 'successful handoff retained decoder bytes'
        finally:
            source.close()
            if writer is not None:
                os.close(writer)
    ''', prefix, tail, newline)


@pytest.mark.parametrize("encoding", ["cp932", "utf-16", "utf-32", "mac-arabic", "mac-farsi"])
@pytest.mark.parametrize("reader", ["read", "readline", "iterate"])
@pytest.mark.parametrize("transport", ["pipe", "file"])
def test_late_refusal_restores_native_text_methods(tmp_path, encoding, reader, transport):
    _child(r'''
        import codecs, io, os, sys
        from g6.mcp._stdin_cache import cache_reader
        encoding, reader, transport, folder = sys.argv[1:]
        pending = '\u2252 first\nlast\n' if encoding == 'cp932' else 'first last\nlast\n'
        if encoding in ('utf-16', 'utf-32'):
            width = encoding[-2:]
            opposite = 'be' if sys.byteorder == 'little' else 'le'
            bom = getattr(codecs, 'BOM_UTF' + width + '_' + opposite.upper())
            wire = bom + ('launcher\n' + pending).encode(encoding + '-' + opposite)
        else:
            wire = ('launcher\n' + pending).encode(encoding)
        if transport == 'pipe':
            number, writer = os.pipe()
            os.write(writer, wire)
            os.close(writer)
            raw = io.FileIO(number, 'rb')
        else:
            path = os.path.join(folder, 'input')
            with open(path, 'wb') as stream:
                stream.write(wire)
            raw = io.FileIO(path, 'rb')
        with io.TextIOWrapper(io.BufferedReader(raw), encoding=encoding) as source:
            assert source.read(len('launcher\n')) == 'launcher\n'
            position = source.tell() if transport == 'file' else None
            try:
                cache_reader(source)
            except ValueError as exc:
                assert '[G6_E_STDIN_HANDOFF]' in str(exc)
            else:
                raise AssertionError('ambiguous/stateful pending cache was accepted')
            assert not any(name in vars(source) for name in ('read', 'readline', '__next__'))
            if position is not None:
                assert source.tell() == position, 'refusal changed the file cookie'
            if reader == 'read':
                assert source.read() == pending, 'refusal consumed caller text'
            elif reader == 'readline':
                assert source.readline() + source.read() == pending
            else:
                assert ''.join(source) == pending
    ''', encoding, reader, transport, str(tmp_path))


@pytest.mark.parametrize("newline", ["universal", "preserve", "fixed"])
def test_refusal_keeps_newline_observations_and_partial_bytes(newline):
    _child(r'''
        import io, os, sys
        import anyio
        from g6.mcp import stdio
        newline = {'universal': None, 'preserve': '', 'fixed': '\r\n'}[sys.argv[1]]
        number, writer = os.pipe()
        source = io.TextIOWrapper(io.BufferedReader(io.FileIO(number, 'rb')),
                                  encoding='cp932', newline=newline)
        try:
            os.write(writer, b'launcher\r\n\x87\x90 still\r\nnext\x82')
            assert source.readline() == ('launcher\n' if newline is None else 'launcher\r\n')
            before = source.newlines
            os.write(writer, b'\xa0\r\n')
            os.close(writer)
            writer = None
            async def run():
                try:
                    async with stdio._lines(anyio.wrap_file(source)):
                        raise AssertionError('codec refusal yielded a stream')
                except OSError as exc:
                    assert '[G6_E_STDIN_HANDOFF]' in str(exc)
                else:
                    raise AssertionError('late codec refusal was swallowed')
            anyio.run(run)
            assert source.newlines == before, 'refusal changed newline observations'
            expected = '\u2252 still\r\nnext\u3042\r\n'
            assert source.read() == (expected.replace('\r\n', '\n') if newline is None else expected)
        finally:
            source.close()
            if writer is not None:
                os.close(writer)
    ''', newline)


@pytest.mark.parametrize("outcome", ["untouched", "closed", "reused_null", "reused_file", "replaced"])
def test_failed_copy_disposes_only_its_own_handle_once(tmp_path, outcome):
    _child(r'''
        import gc, io, os, sys, tempfile
        from types import SimpleNamespace
        from g6.mcp import stdio
        outcome, folder = sys.argv[1:]
        tempfile.tempdir = folder
        native_dup2, native_close = os.dup2, os.close
        real_file, real_cleanup = stdio._InputFile, stdio._InputDescriptor.close
        slots, disposed, cleanups, foreign = [], [], [], []
        other_path = os.path.join(folder, 'another-owners-file')
        with open(other_path, 'wb') as stream:
            stream.write(b'foreign bytes')
        class TrackedFile(real_file):
            def close(self):
                if not self.closed:
                    disposed.append(self.fileno())
                super().close()
        def opened(*args, **kwargs):
            slot = TrackedFile(*args, **kwargs)
            slots.append(slot)
            return slot
        def cleanup(owner, *, reader=False):
            if owner.file is not None and owner.claimed == reader:
                cleanups.append(owner.file)
            real_cleanup(owner, reader=reader)
        def copying(fd, target, **kwargs):
            if outcome == 'untouched':
                raise OSError('copy did not touch its target')
            native_dup2(fd, target, **kwargs)
            disposed.append(target)  # the copy closed G6's placeholder handle
            if outcome != 'replaced':
                native_close(target)
            if outcome.startswith('reused'):
                other = os.open(os.devnull if outcome == 'reused_null' else other_path, os.O_RDONLY)
                if other != target:
                    native_dup2(other, target)
                    native_close(other)
                foreign.append(target)
            elif outcome == 'replaced':
                foreign.append(target)
            raise OSError('copy failed after consuming its target')
        stdio._InputFile = opened
        stdio._InputDescriptor.close = cleanup
        stdio.os = SimpleNamespace(**(vars(os) | {'dup2': copying}))
        source_read, source_write = os.pipe()
        source = io.FileIO(source_read, 'rb')
        owner = stdio._InputDescriptor()
        try:
            try:
                owner.duplicate(source.fileno())
            except OSError as exc:
                assert '[G6_E_STDIN_HANDOFF]' in str(exc)
            else:
                raise AssertionError('failed copy yielded ownership')
            owner.close()
            owner.close()
            gc.collect()
            assert cleanups == slots, 'refused duplicate skipped or repeated owner cleanup'
            assert len(disposed) == 1, 'owned placeholder handle was not disposed exactly once'
            assert slots[0].closed
            for number in foreign:
                os.fstat(number)
            if outcome == 'reused_file':
                assert os.read(foreign[0], 100) == b'foreign bytes'
            os.fstat(source.fileno())
        finally:
            owner.close()
            source.close()
            native_close(source_write)
            for number in foreign:
                native_close(number)
            os.unlink(other_path)
        assert os.listdir(folder) == [], 'G6 left a created file behind'
    ''', outcome, str(tmp_path))


def test_failed_identity_probe_defers_one_owned_close_to_cleanup(tmp_path):
    _child(r'''
        import io, os, sys, tempfile
        from types import SimpleNamespace
        from g6.mcp import stdio
        tempfile.tempdir = sys.argv[1]
        real_file, native_stat = stdio._InputFile, os.stat
        slots, closes = [], []
        class TrackedFile(real_file):
            def close(self):
                if not self.closed:
                    closes.append(self.fileno())
                super().close()
        def opened(*args, **kwargs):
            slot = TrackedFile(*args, **kwargs)
            slots.append(slot)
            return slot
        def stat(path, *args, **kwargs):
            if isinstance(path, int):
                raise OSError('identity query failed')
            return native_stat(path, *args, **kwargs)
        stdio._InputFile = opened
        stdio.os = SimpleNamespace(**(vars(os) | {'stat': stat}))
        owner = stdio._InputDescriptor()
        with io.FileIO(os.devnull, 'rb') as source:
            try:
                try:
                    owner.duplicate(source.fileno())
                except OSError as exc:
                    assert '[G6_E_STDIN_HANDOFF]' in str(exc)
                else:
                    raise AssertionError('failed identity probe yielded ownership')
                assert len(slots) == 1, 'a probe failure allocated a fallback'
                number = slots[0].fileno()
                owner.close()
                owner.close()
                assert closes == [number], 'probe refusal did not close exactly once'
                os.fstat(source.fileno())
            finally:
                owner.close()
        assert os.listdir(sys.argv[1]) == []
    ''', str(tmp_path))


@pytest.mark.parametrize("ending", ["session", "killed"])
def test_open_client_stdin_leaves_no_created_file_after_server_exit(tmp_path, ending):
    child = textwrap.dedent(r'''
        import sys, tempfile
        import anyio
        from g6.mcp import stdio
        tempfile.tempdir = sys.argv[1]
        async def run():
            async with stdio._lines(None):
                print('READY', flush=True)
                if sys.argv[2] == 'killed':
                    while True:
                        await anyio.sleep(1)
            # Let the blocked daemon retain its duplicate through interpreter exit.
        anyio.run(run)
    ''')
    process = subprocess.Popen([sys.executable, "-B", "-c", child, str(tmp_path), ending],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ready = queue.Queue()
    threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
    try:
        assert ready.get(timeout=15).strip() == b"READY", "server did not reach serving"
        assert not process.stdin.closed, "client closed stdin before the session ended"
        if ending == "killed":
            process.kill()
        process.wait(timeout=15)
        if ending == "session":
            assert process.returncode == 0, process.stderr.read().decode(errors="replace")
        assert list(tmp_path.iterdir()) == [], "server exit left a G6-created file"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
