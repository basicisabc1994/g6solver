"""Controls for review 3 of claude/wire-consistency-fu4: probes that pass on 4af1c617.

They show the review's fixtures reach working code: a real piped stdin is served in order in several text
configurations and leaves nothing on disk, the wrapper-state reading returns the exact wire bytes in randomised
launcher states, and a text layer of another type is refused before anything is read.
"""
import codecs
import io
import json
import os
import random
import subprocess
import sys
import textwrap

import pytest

from g6.mcp._stdin_cache import cache_reader

CHILD = textwrap.dedent('''
    import json
    import sys

    import anyio
    from mcp.shared.message import SessionMessage

    from g6.mcp import stdio

    if sys.argv[1] == "launcher_line":
        assert sys.stdin.readline() == "launcher\\n"


    class Output:
        async def write(self, data):
            return len(data)

        async def flush(self):
            pass


    async def run():
        got = []
        async with stdio.stdio_server(stdin=None, stdout=Output()) as (incoming, outgoing):
            async for item in incoming:
                got.append([item.message.root.id, item.message.root.method]
                           if isinstance(item, SessionMessage) else type(item).__name__)
            await outgoing.aclose()
        return got

    # PYTHONIOENCODING also sets stderr's encoding: report in ASCII bytes.
    sys.stderr.buffer.write(b"\\n" + json.dumps(anyio.run(run)).encode("ascii") + b"\\n")
    sys.stderr.buffer.flush()
''')

WIRE = (b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
        b'{"jsonrpc":"2.0","id":2,"method":"p\xc3\xa9ng"}\r\n'
        b'not json\n'
        b'{"jsonrpc":"2.0","id":3,"method":"\xe6\x97\xa5\xe6\x9c\xac"}\r'
        b'{"jsonrpc":"2.0","id":4,"method":"last"}')
EXPECTED = [[1, "ping"], [2, "péng"], "ValidationError", [3, "日本"], [4, "last"]]


@pytest.mark.parametrize("io_encoding", [None, "cp1252", "cp932", "utf-8:replace", "utf-16", "utf-8-sig", "euc_jp"])
@pytest.mark.parametrize("launcher", ["untouched", "launcher_line"])
def test_control_a_real_piped_stdin_is_served_in_order_and_leaves_nothing(tmp_path, io_encoding, launcher):
    if launcher == "launcher_line" and io_encoding in ("utf-8:replace", "utf-16", "euc_jp", "utf-8-sig"):
        pytest.skip("a launcher read in this configuration is a stated refusal or changes the wire's meaning")
    temp = tmp_path / "temp"
    temp.mkdir()
    env = dict(os.environ, TMP=str(temp), TEMP=str(temp), TMPDIR=str(temp))
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    if io_encoding is not None:
        env["PYTHONIOENCODING"] = io_encoding
    process = subprocess.Popen([sys.executable, "-B", "-c", CHILD, launcher], env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        wire = (b"launcher\n" if launcher == "launcher_line" else b"") + WIRE
        for start in range(0, len(wire), 37):           # several writes: requests split across reads
            process.stdin.write(wire[start:start + 37])
            process.stdin.flush()
        process.stdin.close()
        code = process.wait(timeout=90)
        errors = process.stderr.read().decode("ascii", errors="replace")
        assert code == 0, errors
        assert json.loads(errors.strip().splitlines()[-1]) == EXPECTED, errors
        assert sorted(path.name for path in temp.iterdir()) == []
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=20)
        process.stderr.close()
        if not process.stdin.closed:
            process.stdin.close()


STATELESS = {"utf-16": "utf-16-le", "utf-32": "utf-32-le", "utf-8-sig": "utf-8"}
ALPHABET = ["a", "b", "{", "}", '"', " ", "é", "ß", "日", "\U0001f600", "€", "\n", "\r\n", "\r"]
ENCODINGS = ["utf-8", "cp1252", "utf-16", "utf-8-sig", "cp932", "utf-32", "latin-1", "cp1250", "utf-16-le",
             "utf-16-be", "shift_jis", "big5", "cp437", "ascii", "cp949", "gbk", "koi8-r"]


@pytest.mark.skipif(sys.byteorder != "little", reason="the stateless names below are this machine's byte order")
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_control_wrapper_state_reading_returns_the_wire_in_random_launcher_states(seed):
    """Every newline setting io accepts (None, "", LF, CR, CRLF), small decode chunks and buffers, five launcher
    reads. A refusal is allowed only after a launcher read; no byte may be lost, repeated or moved."""
    rng = random.Random(seed)
    ran = handed_off = 0
    for _ in range(1200):
        encoding = rng.choice(ENCODINGS)
        errors = rng.choice(["strict", "surrogateescape"])
        newline = rng.choice([None, "", "\n", "\r", "\r\n"])
        pieces = [rng.choice(ALPHABET) for _ in range(rng.randint(3, 40))]
        text = "".join(pieces) + "x\ntail\r\n{}\n"
        try:
            wire = text.encode(encoding)
        except UnicodeError:
            text = "".join(piece for piece in pieces if ord(piece[0]) < 128) + "x\ntail\r\n{}\n"
            wire = text.encode(encoding)
        if wire.decode(encoding) != text:
            continue
        op = rng.choice(["none", "peek", "read", "readline", "read_then_peek", "iter"])
        read_fd, write_fd = os.pipe()
        os.write(write_fd, wire)
        os.close(write_fd)
        raw = io.FileIO(read_fd, "rb")
        try:
            source = io.TextIOWrapper(io.BufferedReader(raw, buffer_size=rng.choice([1, 2, 3, 7, 64, 8192])),
                                      encoding=encoding, errors=errors, newline=newline)
            source._CHUNK_SIZE = rng.choice([1, 2, 3, 5, 8, 64, 8192])
            taken = ""
            if op == "peek":
                source.buffer.peek(1)
            elif op in ("read", "read_then_peek"):
                taken = source.read(rng.randint(1, max(1, len(text) - 12)))
                if op == "read_then_peek":
                    source.buffer.peek(1)
            elif op == "readline":
                taken = source.readline()
            elif op == "iter":
                taken = next(source)
            ran += 1
            try:
                cached, _ = cache_reader(source)
            except ValueError as exc:
                assert "[G6_E_STDIN_HANDOFF]" in str(exc)
                assert op not in ("none", "peek"), f"untouched {encoding}/{newline!r} refused: {exc}"
                continue
            chunks = [cached()]
            while piece := os.read(read_fd, 65536):
                chunks.append(piece)
            handed = b"".join(chunks)
            handed_off += 1
            context = (encoding, errors, newline, op, wire, taken, handed)
            if op in ("none", "peek"):
                assert handed == wire, context
            else:
                name = codecs.lookup(encoding).name
                whole, want = taken + handed.decode(STATELESS.get(name, name), errors), text
                if newline is None:             # the text layer had already translated what it decoded
                    whole = whole.replace("\r\n", "\n").replace("\r", "\n")
                    want = want.replace("\r\n", "\n").replace("\r", "\n")
                assert whole == want, context
        finally:
            raw.close()
    assert ran > 900 and handed_off > 700, (ran, handed_off)


LEAK_CHILD = textwrap.dedent('''
    import gc
    import io
    import json
    import os
    import sys
    import tempfile
    import time

    import anyio

    from g6.mcp import stdio

    tempfile.tempdir = sys.argv[1]
    REQUEST = '{"jsonrpc":"2.0","id":1,"method":"ping"}\\n'


    def lowest_free_number():
        fd = os.open(os.devnull, os.O_RDONLY)
        os.close(fd)
        return fd


    def session(mode):
        read_fd, write_fd = os.pipe()
        source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding="cp1252")
        os.write(write_fd, REQUEST.encode())
        if mode == "end_of_input":
            os.close(write_fd)

        async def run():
            got = []
            async with stdio._lines(anyio.wrap_file(source)) as lines:
                async for line in lines:
                    got.append(line)
                    if mode == "session_ends_first":
                        break
            return got

        got = anyio.run(run)
        if mode == "session_ends_first":
            os.close(write_fd)              # the client goes away later: the blocked reader ends and closes
            time.sleep(0.05)
        source.close()
        return got


    def no_directory(*args, **kwargs):
        raise OSError("temporary directory unavailable")


    report = {}
    real_directory = stdio.TemporaryDirectory
    for placeholder in ("unique", "null_fallback"):
        stdio.TemporaryDirectory = real_directory if placeholder == "unique" else no_directory
        for mode in ("end_of_input", "session_ends_first"):
            session(mode)
            gc.collect()
            time.sleep(0.2)
            before = lowest_free_number()
            delivered = all(session(mode) == [REQUEST] for _ in range(25))
            gc.collect()
            time.sleep(0.5)
            report[placeholder + "/" + mode] = [delivered, before, lowest_free_number()]
    report["leftovers"] = sorted(os.listdir(sys.argv[1]))
    print(json.dumps(report))
''')


def test_control_repeated_sessions_leak_no_descriptor_and_leave_no_file(tmp_path):
    temp = tmp_path / "temp"
    temp.mkdir()
    done = subprocess.run([sys.executable, "-B", "-c", LEAK_CHILD, str(temp)], stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=300)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    report = json.loads(done.stdout)
    assert report.pop("leftovers") == []
    assert len(report) == 4
    for name, (delivered, before, after) in report.items():
        assert delivered, name
        assert after == before, f"{name}: the lowest free descriptor number moved from {before} to {after}"


def test_control_a_text_layer_of_another_type_is_refused_before_anything_is_read():
    class LauncherText(io.TextIOWrapper):
        pass

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"request\n")
    os.close(write_fd)
    source = LauncherText(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding="utf-8")
    try:
        with pytest.raises(TypeError):
            cache_reader(source)
        assert source.read() == "request\n"
    finally:
        source.close()
