"""What the input-file factory returns is used as a plain file object on every path, not only in cleanup.

4af1c617: "Keep factory-result cleanup at the fileno/close contract ... without requiring release on the returned
object", and stdio.py:211 says the factory's result is "not necessarily G6's native owner".
tests/core/test_stdin_file_factory_cleanup.py pins that for close(). The failed-copy branch still calls
self.file.release() (stdio.py:187 and :190). With the same plain-file factory that test uses, a copy that failed
after its target was closed or replaced raises AttributeError instead of the stable [G6_E_STDIN_HANDOFF] refusal,
the closing claim is not retired, and the startup cleanup then closes the number, which by then belongs to
somebody else.

Reach: only a caller that replaces the private factory, as the branch's own tests do. Runs in a child process.
"""
import json
import subprocess
import sys
import textwrap

import pytest

CHILD = textwrap.dedent('''
    import io
    import json
    import os
    import sys
    import tempfile
    from types import SimpleNamespace

    import anyio

    from g6.mcp import stdio

    factory, temp_dir = sys.argv[1:3]
    tempfile.tempdir = temp_dir
    real_dup2 = os.dup2
    seen = {}

    def failing_copy(fd, target, **kwargs):
        real_dup2(fd, target, **kwargs)
        os.close(target)                    # what CPython does when it cannot clear the copy's inheritance
        first, second = os.pipe()           # another owner's pipe takes the freed number
        if first != target:
            real_dup2(first, target)
            os.close(first)
        seen.update(target=target, bystander_write=second)
        raise OSError("copy could not establish inheritance")

    stdio.os = SimpleNamespace(**(vars(os) | {"dup2": failing_copy}))
    if factory == "plain_file":
        stdio._InputFile = lambda *args, **kwargs: io.FileIO(*args, **kwargs)
    read_fd, write_fd = os.pipe()
    source = io.FileIO(read_fd, "rb")

    async def run():
        async with stdio._lines(anyio.wrap_file(source)):
            raise AssertionError("a failed copy yielded input")

    try:
        anyio.run(run)
        error = "no error"
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
    try:
        os.write(seen["bystander_write"], b"x")
        alive = os.read(seen["target"], 1) == b"x"
    except OSError:
        alive = False
    print(json.dumps({"error": error, "bystander_pipe_alive": alive, "leftovers": sorted(os.listdir(temp_dir))}))
''')


@pytest.mark.parametrize("factory", ["native_owner", "plain_file"], ids=["control_native_owner", "plain_file"])
def test_a_failed_copy_is_refused_and_closes_nothing_foreign_whatever_the_factory_returns(tmp_path, factory):
    temp = tmp_path / "temp"
    temp.mkdir()
    done = subprocess.run([sys.executable, "-B", "-c", CHILD, factory, str(temp)], stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    result = json.loads(done.stdout)
    assert result["leftovers"] == []
    assert result["error"].startswith("OSError: [G6_E_STDIN_HANDOFF]"), result
    assert result["bystander_pipe_alive"], "G6 closed a pipe that another owner opened on the freed number"
