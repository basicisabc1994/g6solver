"""A temporary folder that cannot give the placeholder a file identity must not refuse a healthy stdin.

Review 2's M4: reading stdin must not depend on the temporary folder. 4af1c617 falls back to the null device when
the unique placeholder cannot be allocated (the folder is missing or not writable). When the folder is writable but
its files report st_ino == 0 (os.stat on a volume without file ids: some network redirectors and shared folders),
stdio.py:179-180 still refuses with [G6_E_STDIN_HANDOFF] and the server never starts. The same fact, "no unique
placeholder is available", is decided by two rules: one falls back, one refuses.
tests/core/test_stdin_placeholder_ownership.py::test_placeholder_allocation_refusals_leave_no_owned_resource
[zero_identity] pins the refusal, as its [directory] case did before the lead's ruling superseded it.

No such volume exists on this machine: the child process gives stdio's own view of os.stat a zero st_ino for regular
files (the technique of the pinned test). Everything else is real: a pipe, a request, the reader thread.
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
    import stat as stat_module
    import sys
    import tempfile
    from types import SimpleNamespace

    import anyio

    from g6.mcp import stdio

    mode, temp_dir, request = sys.argv[1:4]
    real_stat = os.stat
    zeroed = []

    def stat_without_file_ids(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if isinstance(path, int) and stat_module.S_ISREG(result.st_mode):
            zeroed.append(path)
            return SimpleNamespace(st_dev=result.st_dev, st_ino=0, st_mode=result.st_mode)
        return result

    tempfile.tempdir = temp_dir if mode != "missing" else os.path.join(temp_dir, "gone")
    if mode == "no_file_ids":
        stdio.os = SimpleNamespace(**(vars(os) | {"stat": stat_without_file_ids}))
    read_fd, write_fd = os.pipe()
    source = io.FileIO(read_fd, "rb")
    os.write(write_fd, request.encode())
    os.close(write_fd)

    async def run():
        with anyio.fail_after(20):
            async with stdio._lines(anyio.wrap_file(source)) as lines:
                return [line async for line in lines]

    try:
        result = {"lines": anyio.run(run)}
    except OSError as exc:
        result = {"refused": str(exc), "cause": repr(exc.__cause__)}
    result["zeroed"] = len(zeroed)
    result["leftovers"] = sorted(os.listdir(temp_dir))
    print(json.dumps(result))
''')


@pytest.mark.parametrize("mode", ["usable", "missing", "no_file_ids"],
                         ids=["control_usable_temp", "control_missing_temp_falls_back", "temp_without_file_ids"])
def test_an_open_stdin_hands_off_whatever_the_temporary_folder_can_give(tmp_path, mode):
    temp = tmp_path / "temp"
    temp.mkdir()
    done = subprocess.run([sys.executable, "-B", "-c", CHILD, mode, str(temp), REQUEST], stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    result = json.loads(done.stdout)
    if mode == "no_file_ids":
        assert result["zeroed"] >= 1, "the fixture never reached the placeholder's identity check"
    assert result["leftovers"] == []
    assert result.get("lines") == [REQUEST], f"a healthy stdin was refused: {result}"
