# SPDX-License-Identifier: Apache-2.0
"""The bounded script runner: files in and out, wall clock, memory, network, environment. Offline."""
from __future__ import annotations

import json
import os

import pytest

from g6.core.sandbox import NETWORK_OFF, run_script


def test_a_script_reads_its_inputs_and_its_outputs_come_back() -> None:
    run = run_script("open('out.txt', 'w').write(open('in.txt').read().upper())\nprint('done')",
                     {"in.txt": "hello"}, timeout_s=30)
    assert run.status == "ok" and run.returncode == 0 and "done" in run.stdout
    # Environmental: where the operating system confines the run (the Windows AppContainer once the owner's grant
    # covers this checkout) os_sandbox is True, elsewhere False; it must say which isolation the run had.
    # Removing run_script's `limits.update(os_sandbox=True, ...)` makes this fail on a confining machine.
    assert run.outputs == {"out.txt": b"HELLO"} and run.limits["os_sandbox"] is bool(run.isolation.get("backend"))


def test_a_script_that_never_ends_is_killed() -> None:
    run = run_script("while True:\n    pass\n", timeout_s=2)
    assert run.status == "timeout" and run.seconds < 15


def test_a_connection_off_this_machine_is_refused() -> None:
    run = run_script("import socket\ntry:\n    socket.create_connection(('192.0.2.1', 80), timeout=2)\n"
                     "except OSError as exc:\n    print(exc)\n", timeout_s=30)
    assert run.status == "ok" and NETWORK_OFF in run.stdout


def test_memory_beyond_the_cap_fails_the_script() -> None:
    script = "block = bytearray(700 * 1024 * 1024)\nprint('allocated')"
    run = run_script(script, timeout_s=30, mem_mb=256)
    assert run.status == "error" and "allocated" not in run.stdout and "MemoryError" in run.stderr
    control = run_script(script, timeout_s=30, mem_mb=2048)   # the cap, not the machine, refused the first
    assert control.status == "ok" and "allocated" in control.stdout


def test_numerical_libraries_run_on_one_thread_so_the_cpu_limit_is_not_reached_before_the_wall_clock(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """On POSIX the CPU limit counts every thread: an OpenMP or BLAS fit on eight cores reached it after an eighth of
    its wall-clock allowance and was reported as late. The child is told to use one thread, whatever the parent says."""
    monkeypatch.setenv("OMP_NUM_THREADS", "8")
    names = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
             "NUMEXPR_NUM_THREADS")
    run = run_script(f"import os\nprint(','.join(os.environ.get(k, '-') for k in {names!r}))", timeout_s=30)
    assert run.status == "ok" and run.stdout.strip() == "1,1,1,1,1"
    assert run.limits["threads"] == 1


@pytest.mark.skipif(os.name != "posix", reason="the memory rlimits are POSIX's; on Windows a Job Object caps memory")
@pytest.mark.parametrize(("memory", "capped", "left"), [("data", "RLIMIT_DATA", "RLIMIT_AS"),
                                                        (None, "RLIMIT_AS", "RLIMIT_DATA")])
def test_the_child_caps_its_data_segment_when_asked_and_its_address_space_otherwise(memory, capped, left) -> None:
    script = ("import json, resource\n"
              "print(json.dumps({n: resource.getrlimit(getattr(resource, n))[0] for n in ('RLIMIT_AS', 'RLIMIT_DATA')}))")
    run = run_script(script, timeout_s=30, mem_mb=512, **({"memory": memory} if memory else {}))
    found = json.loads(run.stdout)
    assert run.status == "ok" and found[capped] == 512 * 1024 * 1024 and found[left] != 512 * 1024 * 1024
    assert capped in run.limits["memory_limit"]


def test_the_limits_say_what_the_memory_cap_counts() -> None:
    refused = {"../x": ""}   # refused at once, with the limits it would have run under
    data = run_script("", refused, memory="data").limits["memory_limit"]
    whole = run_script("", refused).limits["memory_limit"]
    if os.name == "posix":
        assert "RLIMIT_DATA" in data and "shared libraries" in data and "RLIMIT_AS" in whole
    else:
        assert "Job Object" in data and data == whole


def test_the_limits_say_what_the_network_guard_covers() -> None:
    # Environmental: a run the operating system confines (the Windows AppContainer once the owner's grant covers this
    # checkout) has no network at all, which its limits say; an unconfined run has only Python's socket guard.
    # Removing either wording in run_script (`network=_CONFINED_NETWORK` or the default) makes this fail where it runs.
    run = run_script("print(1)", timeout_s=30)
    network = run.limits["network"]
    if run.isolation.get("backend"):
        assert "operating system refuses every connection" in network and "socket guard" in network
    else:
        assert "this machine" in network and "not the operating system" in network


def test_the_environment_carries_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_SANDBOX_TEST_SECRET", "s3cret")
    run = run_script("import os\nprint(os.environ.get('G6_SANDBOX_TEST_SECRET'))", timeout_s=30)
    assert run.status == "ok" and run.stdout.strip() == "None"


@pytest.mark.parametrize("name", ["../x.csv", "sub/x.csv", "", "_g6_script.py"])
def test_input_names_must_be_plain(name: str) -> None:
    run = run_script("print(1)", {name: "x"})
    assert run.status == "refused" and run.returncode is None


def test_outputs_are_bounded() -> None:
    run = run_script("open('a.bin', 'wb').write(b'x' * 1000)\nopen('b.bin', 'wb').write(b'y' * 1000)",
                     timeout_s=30, max_output_bytes=1500)
    assert list(run.outputs) == ["a.bin"]
