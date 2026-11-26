"""In the null-device fallback, a failed copy must never close a descriptor G6 did not open.

4af1c617 falls back to an os.devnull placeholder when no unique temporary file can be made, "keeping the identity
comparison on failed copies" (stdio.py:189): after a failed os.dup2 the target is closed by G6 only when its
(st_dev, st_ino) still equals the placeholder's. On Windows os.stat(fd) reports (0, 0) for the null device, for every
pipe and for the console, so the comparison cannot tell G6's placeholder from another owner's pipe, nor from the
client's own stdin pipe copied onto the number. The comment at stdio.py:168 names only "its weaker distinction
between null opens".

Two members, both in the fallback only:
* CPython closes the target when the copy was made but its inheritance could not be cleared (posixmodule.c,
  os_dup2_impl). Another thread's new pipe can take the freed number before G6 looks at it. G6 then closes that pipe.
* tests/core/test_stdin_placeholder_lifecycle.py::test_fallback_copy_failure_compares_the_null_placeholder_identity
  [replaced] states that a replaced number is left alone. It uses a disk file as stdin. With the pipe an MCP client
  gives the server, the same number is closed.

Every descriptor trick runs in a child process.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

CHILD = textwrap.dedent('''
    import json
    import os
    import sys
    import tempfile
    from types import SimpleNamespace

    from g6.mcp import stdio

    temp, source_kind, outcome, temp_dir, wire = sys.argv[1:6]
    real_dup2 = os.dup2
    seen = {}

    def no_directory(*args, **kwargs):
        raise OSError("temporary directory unavailable")

    def failing_copy(fd, target, **kwargs):
        seen["target"] = target
        if outcome == "untouched":
            raise OSError("the borrowed source went away before the copy")
        real_dup2(fd, target, **kwargs)
        if outcome == "reused_by_pipe":
            os.close(target)                # what CPython does when it cannot clear the copy's inheritance
            first, second = os.pipe()       # another owner's pipe takes the freed number before G6 looks at it
            if first != target:
                real_dup2(first, target)
                os.close(first)
            seen["bystander_write"] = second
        raise OSError("copy could not establish inheritance")

    if temp == "missing":
        stdio.TemporaryDirectory = no_directory
    else:
        tempfile.tempdir = temp_dir
    # Only stdio's view of os changes: the child's own streams keep the real dup2.
    stdio.os = SimpleNamespace(**(vars(os) | {"dup2": failing_copy}))
    if source_kind == "pipe":
        read_fd, write_fd = os.pipe()       # the shape of stdin under an MCP client
    else:
        read_fd = os.open(wire, os.O_RDONLY)
    owner = stdio._InputDescriptor()
    refused = False
    try:
        owner.duplicate(read_fd)
    except OSError as exc:
        refused = "[G6_E_STDIN_HANDOFF]" in str(exc)
    owner.close()
    owner.close()
    target = seen["target"]
    try:
        os.fstat(target)
        open_after = True
    except OSError:
        open_after = False
    alive = None
    if "bystander_write" in seen:
        try:
            os.write(seen["bystander_write"], b"x")
            alive = os.read(target, 1) == b"x"
        except OSError:
            alive = False
    print(json.dumps({"refused": refused, "target_open_after_g6_close": open_after, "bystander_pipe_alive": alive,
                      "leftovers": sorted(os.listdir(temp_dir))}))
''')


def _child(tmp_path, temp, source, outcome):
    wire = tmp_path / "wire"
    wire.write_bytes(b"request\n")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    done = subprocess.run([sys.executable, "-B", "-c", CHILD, temp, source, outcome, str(temp_dir), str(wire)],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    result = json.loads(done.stdout)
    assert result["refused"], "the injected copy failure was not refused with the stable code"
    assert result["leftovers"] == []
    return result


@pytest.mark.skipif(os.name != "nt", reason="pipes and the null device share the identity (0, 0) on Windows only")
@pytest.mark.parametrize("temp", ["usable", "missing"], ids=["control_unique_placeholder", "null_fallback"])
def test_a_failed_copy_does_not_close_another_owners_pipe(tmp_path, temp):
    result = _child(tmp_path, temp, "pipe", "reused_by_pipe")
    assert result["bystander_pipe_alive"], "G6 closed a pipe that another owner opened on the freed number"


@pytest.mark.skipif(os.name != "nt", reason="pipes and the null device share the identity (0, 0) on Windows only")
@pytest.mark.parametrize("source", ["file", "pipe"], ids=["control_disk_file_stdin", "pipe_stdin"])
def test_a_replaced_number_is_left_alone_in_the_fallback(tmp_path, source):
    result = _child(tmp_path, "missing", source, "replaced")
    assert result["target_open_after_g6_close"], "G6 closed a number whose placeholder had been replaced"


def test_control_an_untouched_fallback_placeholder_is_closed(tmp_path):
    result = _child(tmp_path, "missing", "pipe", "untouched")
    assert not result["target_open_after_g6_close"], "the fallback placeholder leaked"
