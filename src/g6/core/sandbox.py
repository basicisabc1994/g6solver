# SPDX-License-Identifier: Apache-2.0
"""Run a Python script out of process, bounded and, where the operating system allows, confined (the data-analysis
bundle; audit 025 HD-010).

``run_script(source, files)`` writes ``files`` and the script into a fresh folder G6 makes and runs ``python -I``
there under :mod:`g6.core.component_process`: a wall-clock allowance, then a kill of the whole process tree. The child's
environment carries no secret. A boot runs before the script: it refuses network connections to anywhere but this
machine and, on POSIX, lowers the CPU and file-size limits and, on Linux, the memory limit; on Windows the Job Object
caps each process's memory, and on macOS the memory is not capped (:func:`memory_limit`; the run's limits say so). On
Linux the memory cap is on the whole address space unless the caller asks for the data segment (``memory="data"``,
:data:`MEMORY_LIMITS`), for a child that maps large shared libraries whose address space it never uses. The files the
script leaves in its directory come back, up to a size limit.

Where :mod:`g6.core.confine` has a backend for the interpreter (bubblewrap on Linux, a Windows AppContainer), the
script also runs confined: it reads only the interpreter's folders and its own folder (and, on Windows, what every
AppContainer may read: the system folders, Program Files), writes only its own folder (and, on Windows, its run's
container folder, deleted after it), reaches no network, this machine included, and the processes it starts are
confined alike and end with it. Where none can confine it (macOS, Linux without bubblewrap, and Windows until the
owner's one-time grant lets the container read the interpreter), the script is refused and nothing runs (``status``
``"refused"``, ``G6_E_ISOLATION_UNAVAILABLE``, :func:`isolation_refused`), unless the person allowed such runs on this
computer (``confine.allowed_by``); ``G6_REQUIRE_ISOLATION=1`` refuses it whatever was allowed. An allowed run is a
resource bound, not an operating-system sandbox: the script runs as the same user and can read whatever that user can
read, and the network guard covers Python's own socket class, not the operating system (see ``NETWORK_GUARD``). Each
result says which (``isolation``: ``{"backend": null, "why": ..., "warning": ..., "allowed_by": ...}`` when
unconfined). The warning (``g6.core.confine.UNCONFINED_WARNING``):

Where no operating-system boundary can confine it (macOS, Linux without bubblewrap, and Windows before the one-time
access grant), G6 refuses to run untrusted code for you (a patch, a generated script, a repository's tests) unless you
allow it once with g6 setup. Code you allow then has your own rights: it can read your key file ~/.g6/.env and your
other files, and its network guard works inside Python only. You are responsible for what you run.

A confined child that cannot import G6 (its container cannot read the folder that holds the ``g6`` package) fails
with that reason and the owner's grant in front of its own error (``confine.import_failure``).
"""
from __future__ import annotations

import math
import os
import signal
import stat
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from g6.core import component_process, confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.outside import find_program

#: Environment variables the child may see (the interpreter needs these on Windows); nothing else is passed.
_ENV_KEEP = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
#: Numerical libraries (OpenMP, OpenBLAS, MKL, Accelerate, numexpr) run on one thread in the child: the POSIX CPU limit
#: counts every thread, so a fit on eight threads would reach it after an eighth of its wall-clock allowance.
_ONE_THREAD = {name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                                      "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")}
SCRIPT = "_g6_script.py"
#: A confined script's temporary folder, inside its own folder (never an output: outputs are files).
TEMP = "_g6_temp"
#: What a refused connection says.
NETWORK_OFF = "G6: network connections are refused in this sandbox"
#: Installed before the script runs: a connection to anywhere but this machine raises. It guards the interpreter's socket
#: class (``connect``, ``connect_ex``, ``sendto``), not the operating system: code that restores the methods, calls the C
#: base class or starts another program is outside it.
NETWORK_GUARD = (
    "import socket\n"
    "_connect, _connect_ex, _sendto = socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto\n"
    "def _here(address):\n"
    "    if not isinstance(address, tuple) or not address:\n"
    "        return True\n"
    "    host = str(address[0])\n"
    "    if host in ('localhost', '::1'):\n"
    "        return True\n"
    "    import ipaddress\n"
    "    try:\n"
    "        return ipaddress.ip_address(host.split('%')[0]).is_loopback\n"
    "    except ValueError:\n"
    "        return False\n"
    "def _guard(method):\n"
    "    def guarded(self, address):\n"
    "        if not _here(address):\n"
    "            raise OSError(" + repr(NETWORK_OFF) + ")\n"
    "        return method(self, address)\n"
    "    return guarded\n"
    "def _guard_sendto(self, *args):\n"
    "    if args and not _here(args[-1]):\n"
    "        raise OSError(" + repr(NETWORK_OFF) + ")\n"
    "    return _sendto(self, *args)\n"
    "socket.socket.connect, socket.socket.connect_ex = _guard(_connect), _guard(_connect_ex)\n"
    "socket.socket.sendto = _guard_sendto\n")
#: The POSIX limit each memory cap sets, and what it counts (the run's ``limits`` say it). ``address`` caps the whole
#: address space; ``data`` caps the data segment, for a child that maps large shared libraries (PyTorch's CUDA build
#: maps gigabytes of address space it never touches): only the heap and other private writable memory count, so such a
#: library can be loaded while what the child allocates stays capped.
MEMORY_LIMITS = {
    "address": ("RLIMIT_AS", "the whole address space (RLIMIT_AS)"),
    "data": ("RLIMIT_DATA", "the data segment (RLIMIT_DATA): the heap and other private writable memory; code and "
                            "read-only data mapped from shared libraries are not counted, and the address space is not "
                            "capped"),
}
#: On Windows, whatever is asked.
_JOB_OBJECT = "each process's committed memory (a Windows Job Object)"
#: On macOS, whatever is asked: it refuses RLIMIT_AS (EINVAL) and does not enforce RLIMIT_DATA for mapped memory.
_NOT_CAPPED = ("not capped: macOS refuses the address-space limit and does not enforce the data segment's, so the "
               "script's memory is bounded only by the machine; its CPU time and the wall clock still are")
#: What the limits say of the network when the run is confined.
_CONFINED_NETWORK = ("no network: the operating system refuses every connection, this machine's included (an "
                     "AppContainer with no network capability); Python's socket guard runs as well")
# argv after ``-c``: memory bytes, CPU seconds, file-size bytes, the memory limit's name ("" for none), then the script
_BOOT = (confine.CHILD_PRELUDE +
         "import sys\n"
         "mem, cpu, fsize = (int(v) for v in sys.argv[1:4])\n"
         "try:\n"
         "    import resource\n"
         "    caps = [(resource.RLIMIT_CPU, cpu), (resource.RLIMIT_FSIZE, fsize)]\n"
         "    if sys.argv[4]:\n"
         "        caps.append((getattr(resource, sys.argv[4]), mem))\n"
         "    for limit, value in caps:\n"
         "        resource.setrlimit(limit, (value, value))\n"
         "except ImportError:\n"
         "    pass\n"
         + NETWORK_GUARD +
         "import runpy\n"
         "sys.argv = sys.argv[5:]\n"
         "runpy.run_path(sys.argv[0], run_name='__main__')\n")


@dataclass(frozen=True)
class ScriptRun:
    """How a script run ended: ``ok`` (exit 0), ``error``, ``timeout`` or ``refused`` (it never started). ``isolation``:
    the operating-system isolation it ran under (``g6.core.confine``), ``{"backend": None, "why": ...}`` when none."""

    status: str
    returncode: int | None
    stdout: str
    stderr: str
    outputs: dict[str, bytes] = field(default_factory=dict)
    seconds: float = 0.0
    limits: dict[str, Any] = field(default_factory=dict)
    isolation: dict[str, Any] = field(default_factory=dict)


def isolation_refused(run: Any) -> bool:
    """Whether ``run`` (a :class:`ScriptRun`) was refused because no operating-system boundary could confine it: the
    script never ran, so its empty output is no answer (``confine.IsolationRefused``)."""
    return run.status == "refused" and str(run.stderr).startswith(f"[{ISOLATION_UNAVAILABLE}]")


def interpreter() -> str | None:
    """The Python to run scripts with: ``G6_PYTHON``, else this one, else (in a frozen build) ``python`` on PATH."""
    if os.environ.get("G6_PYTHON"):
        return os.environ["G6_PYTHON"]
    if not getattr(sys, "frozen", False):
        return sys.executable
    return find_program("python") or find_program("python3")


def memory_limit(memory: str, mem_mb: int, *, os_name: str = os.name,
                 platform: str = sys.platform) -> tuple[str, str, str]:
    """For a ``memory`` kind (:data:`MEMORY_LIMITS`): the POSIX limit the boot sets (``""`` for none), the run's
    ``memory_cap`` and its ``memory_limit``. Linux caps what ``memory`` names, Windows each process's committed memory
    through the Job Object, and macOS nothing, which the limits say."""
    rlimit, counts = MEMORY_LIMITS[memory]
    if os_name != "posix":
        return rlimit, "job object", f"{mem_mb:,} MB of {_JOB_OBJECT}"
    if platform == "darwin":
        return "", "none", _NOT_CAPPED
    return rlimit, "rlimit", f"{mem_mb:,} MB of {counts}"


def _plain_name(name: str) -> bool:
    return bool(name) and name == Path(name).name and name not in (".", "..", SCRIPT, TEMP)


def run_script(source: str, files: Mapping[str, bytes | str] | None = None, *, timeout_s: float = 60.0,
               mem_mb: int = 1024, max_output_bytes: int = 8 * 1024 * 1024, file_mb: int = 64,
               memory: str = "address") -> ScriptRun:
    """Run ``source`` with ``files`` beside it; return the files it wrote (plain names only, at most
    ``max_output_bytes`` in all). ``memory`` is what ``mem_mb`` caps on POSIX (:data:`MEMORY_LIMITS`)."""
    if memory not in MEMORY_LIMITS:
        raise ValueError(f"memory must be one of {', '.join(MEMORY_LIMITS)}, not {memory!r}")
    files = dict(files or {})
    rlimit, cap, capped = memory_limit(memory, mem_mb)
    limits = {"timeout_s": timeout_s, "mem_mb": mem_mb,
              "network": "connections to other machines are refused by Python's socket guard; this machine (loopback) "
                         "is allowed; the guard is in the interpreter, not the operating system",
              "memory_cap": cap, "memory_limit": capped, "threads": 1, "os_sandbox": False}
    bad = [name for name in files if not _plain_name(name)]
    python = interpreter()
    if bad or python is None:
        why = f"input names must be plain file names: {bad}" if bad else "no Python interpreter was found (set G6_PYTHON)"
        return ScriptRun("refused", None, "", why, limits=limits)
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ} | {"PYTHONIOENCODING": "utf-8"} | _ONE_THREAD
    mem = mem_mb * 1024 * 1024
    cpu = max(1, math.ceil(timeout_s)) + 1   # a second past the wall clock, so the clean kill normally fires first
    argv = [python, "-I", "-c", _BOOT, str(mem), str(cpu), str(file_mb * 1024 * 1024), rlimit, SCRIPT]
    started = time.monotonic()
    with confine.run_folder(prefix="g6_sandbox_") as cwd:
        # Canonicalize the host-created root before untrusted execution (macOS's
        # /var is an alias); output paths must still contain no child-created links.
        root = Path(cwd).resolve(strict=True)
        for name, content in files.items():
            (root / name).write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        (root / SCRIPT).write_text(source, encoding="utf-8")
        confined = confine.plan(confine.Confinement(read_write=(cwd,), read_only=confine.interpreter_paths(python)),
                                python)
        isolation = confined.record()
        said = confine.refused(confined)
        if said:
            return ScriptRun("refused", None, "", said, limits=limits, isolation=isolation)
        if confined.backend is not None:
            limits.update(os_sandbox=True, network=_CONFINED_NETWORK)
            (root / TEMP).mkdir()
            env["TMPDIR"] = str(root / TEMP)   # Python's temporary files stay in the run's own folder
        try:
            with component_process.window(timeout_s):
                done = component_process.run_contained(argv, env=env, cwd=cwd, memory_bytes=mem, confined=confined)
        except component_process.ExecutionInterrupted as exc:
            return ScriptRun("timeout", None, exc.stdout.decode("utf-8", "replace"),
                             f"the script took longer than {timeout_s:g}s and was killed",
                             seconds=time.monotonic() - started, limits=limits, isolation=isolation)
        except (OSError, ConnectionResetError) as exc:
            if isinstance(exc, confine.NotStarted):
                isolation = confine.not_started(isolation, exc)
                limits.update(os_sandbox=False)
            return ScriptRun("error", None, "", f"the script could not be run to the end: {exc}",
                             seconds=time.monotonic() - started, limits=limits, isolation=isolation)
        finally:
            left = confined.release()   # the run's container profile: after the run, whatever happened
            if left:
                isolation["cleanup"] = left
        try:
            outputs = _collect_outputs(root, set(files) | {SCRIPT}, max_output_bytes)
        except (OSError, ValueError):
            return ScriptRun("error", done.returncode, done.stdout.decode("utf-8", "replace"),
                             "[G6_E_UNSAFE_OUTPUT] output collection refused a linked, special or changed file",
                             seconds=time.monotonic() - started, limits=limits, isolation=isolation)
    seconds = time.monotonic() - started
    stdout, stderr = done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")
    if confined.backend is not None and done.returncode != 0:
        why = confine.import_failure(stderr)   # S5: never "No module named 'g6'" alone from a confined child
        if why:
            stderr = f"{why}\n{stderr}"
    xcpu = getattr(signal, "SIGXCPU", None)
    if xcpu is not None and done.returncode == -xcpu:
        return ScriptRun("timeout", done.returncode, stdout, f"the script used up {cpu}s of CPU and was killed",
                         outputs, seconds, limits, isolation)
    return ScriptRun("ok" if done.returncode == 0 else "error", done.returncode, stdout, stderr, outputs, seconds, limits,
                     isolation)


def _collect_outputs(root: Path, excluded: set[str], limit: int) -> dict[str, bytes]:
    """Read only ordinary files after all children have exited, never follow child-created links.

    The trusted host must not mutate the run folder concurrently. POSIX O_NOFOLLOW and
    inode comparison also reject a swapped file; reads remain bounded by actual bytes.
    """
    from g6.core.storage import contained, plain_file

    outputs, total = {}, 0
    for path in sorted(root.iterdir()):
        if path.name in excluded:
            continue
        found = path.lstat()
        if stat.S_ISDIR(found.st_mode) and not (getattr(found, "st_file_attributes", 0)
                                               & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            continue  # outputs are flat; no recursion into child-owned directories
        checked = plain_file(path)
        contained(root, path, refuse_links=True)
        if total + checked.st_size > limit:
            break
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (checked.st_dev, checked.st_ino):
                raise ValueError("output changed")
            content = stream.read(limit - total + 1)
        if total + len(content) > limit:
            break
        outputs[path.name] = content
        total += len(content)
    return outputs


__all__ = ["MEMORY_LIMITS", "NETWORK_GUARD", "ScriptRun", "interpreter", "isolation_refused", "memory_limit",
           "run_script"]
