"""A temporary folder that denies new folders must fall back like a missing one, not keep the server from starting.

Review 2's M4 named a temporary folder that is "gone or not writable". 4af1c617 falls back when
TemporaryDirectory() raises OSError (stdio.py:164-171). On Windows, with the Python this tree runs on (3.12.11),
tempfile.mkdtemp does not raise when the folder exists and creating a folder in it is denied: it takes the
PermissionError for a name collision and tries the next name, up to TMP_MAX = 2,147,483,647 times
(Lib/tempfile.py:379-394; os.access reports every existing folder as writable there). No OSError reaches G6, so there
is no fallback and no refusal: the server spins on one core before it reads a byte, and the client sees a server that
never answers initialize.

What gives that answer for real: a temporary folder whose permissions allow new files but deny new folders (tempfile
chooses its folder by creating a file in it). This review may not change permissions, so the child process gives
tempfile's own view of os.mkdir the answer Windows gives ("Access is denied"). Everything else is real.
The parent ends the child after 20 seconds; a measured run made about 24,000 attempts per second.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

REQUEST = '{"jsonrpc":"2.0","id":7,"method":"ping"}\n'

CHILD = textwrap.dedent('''
    import io
    import json
    import os
    import sys
    import tempfile
    from types import SimpleNamespace

    import anyio

    from g6.mcp import stdio

    mode, temp_dir, request = sys.argv[1:4]
    attempts = [0]

    def access_is_denied(path, *args, **kwargs):
        attempts[0] += 1
        raise PermissionError(13, "Access is denied", path)

    tempfile.tempdir = temp_dir if mode != "missing" else os.path.join(temp_dir, "gone")
    if mode == "denies_new_folders":
        tempfile._os = SimpleNamespace(**(vars(os) | {"mkdir": access_is_denied}))
    read_fd, write_fd = os.pipe()
    source = io.FileIO(read_fd, "rb")
    os.write(write_fd, request.encode())
    os.close(write_fd)

    async def run():
        async with stdio._lines(anyio.wrap_file(source)) as lines:
            return [line async for line in lines]

    try:
        result = {"lines": anyio.run(run)}
    except OSError as exc:
        result = {"refused": str(exc), "cause": repr(exc.__cause__)}
    result["leftovers"] = sorted(os.listdir(temp_dir))
    print(json.dumps(result))
''')


@pytest.mark.skipif(os.name != "nt", reason="tempfile retries a denied folder creation on Windows only")
@pytest.mark.parametrize("mode", ["usable", "missing", "denies_new_folders"],
                         ids=["control_usable_temp", "control_missing_temp_falls_back", "temp_denies_new_folders"])
def test_an_open_stdin_is_served_soon_whatever_the_temporary_folder_answers(tmp_path, mode):
    temp = tmp_path / "temp"
    temp.mkdir()
    process = subprocess.Popen([sys.executable, "-B", "-c", CHILD, mode, str(temp), REQUEST],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        try:
            out, errors = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            pytest.fail("after 20 s the server had neither served nor refused its open stdin: "
                        "it was still asking the temporary folder for a name")
        assert process.returncode == 0, errors.decode(errors="replace")
        result = json.loads(out)
        assert result["leftovers"] == []
        assert result.get("lines") == [REQUEST], f"a healthy stdin was refused: {result}"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=20)
