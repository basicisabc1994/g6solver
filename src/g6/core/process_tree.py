# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Own a child process and every process it starts, so ``kill`` ends all of them (from the archive's MCP preflight).

On Windows the child starts suspended inside a Job Object that kills every member when it is closed; on POSIX it leads
a new session whose process group is killed. This is cleanup, not a sandbox: the children run as the same user, unless
``spawn`` starts them confined (``g6.core.confine.popen``: on Windows still suspended and inside the job, on POSIX
still leading a new session).
``memory_bytes`` caps each member's committed memory through the Job Object on Windows; on POSIX the child must lower
its own limits (``resource.setrlimit``), so the argument is ignored there.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

GRACE_S = 5.0  # the child's own exit after it is told to stop, before cleanup is called unconfirmed


class ProcessTreeError(RuntimeError):
    """The process tree could not be created, adopted or confirmed gone."""


class _Tree:
    """The started server and every process it starts. ``kill`` ends all of them."""

    def __init__(self, argv: list[str], *, env: dict[str, str], stderr,
                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd: str | Path | None = None,
                 memory_bytes: int | None = None, spawn=None) -> None:
        kw = {"stdin": stdin, "stdout": stdout, "stderr": stderr, "text": True,
              "encoding": "utf-8", "env": env, "shell": False, "cwd": cwd}
        self._job = None
        if sys.platform == "win32":
            self._job = _WinJob(memory_bytes)
            try:
                # Suspended until it is in the job, so not even its first child can start outside it.
                self.proc = (spawn or subprocess.Popen)(argv, creationflags=_CREATE_SUSPENDED, **kw)
            except BaseException:
                self._job.close()
                raise
            try:
                self._job.adopt(self.proc.pid)
            except BaseException:
                self.proc.kill()
                self._job.close()
                raise
        else:
            # Its own session either way, so its process group is killed whole; a confined start too (``spawn``).
            self.proc = (spawn or subprocess.Popen)(argv, start_new_session=True, **kw)

    def kill(self, *, strict: bool = False) -> None:
        if self._job is not None:
            self._job.close(strict=strict)   # every member, including nested jobs
            self._job = None
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)   # the session's group outlives its leader
            except ProcessLookupError:
                pass
            except PermissionError:
                if strict:
                    raise
        try:
            self.proc.wait(timeout=GRACE_S)
        except subprocess.TimeoutExpired:
            if strict:
                raise


_CREATE_SUSPENDED = 0x00000004


class _WinJob:
    """A Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (ctypes, no dependency), and optionally
    JOB_OBJECT_LIMIT_PROCESS_MEMORY."""

    def __init__(self, memory_bytes: int | None = None) -> None:
        import ctypes
        from ctypes import wintypes

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
                                                             "OtherOperationCount", "ReadTransferCount",
                                                             "WriteTransferCount", "OtherTransferCount")]

        class _Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._ntdll = ctypes.WinDLL("ntdll")
        self._k32.CreateJobObjectW.restype = wintypes.HANDLE
        self._k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self._k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        self._k32.OpenProcess.restype = wintypes.HANDLE
        self._k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self._k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self._k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        self._k32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                      wintypes.DWORD, ctypes.c_void_p)
        self._k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        self._handle = self._k32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ProcessTreeError(f"cannot own the owned process tree: CreateJobObject error {ctypes.get_last_error()}")
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = 0x00002000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_bytes:
            info.BasicLimitInformation.LimitFlags |= 0x00000100   # JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = int(memory_bytes)
        if not self._k32.SetInformationJobObject(self._handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            self.close()
            raise ProcessTreeError(f"cannot own the owned process tree: SetInformationJobObject error {error}")
        self._ctypes = ctypes

    def adopt(self, pid: int) -> None:
        """Put the (suspended) process in the job, then let it run."""
        access = 0x0100 | 0x0001 | 0x0800   # PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME
        process = self._k32.OpenProcess(access, False, pid)
        if not process:
            raise ProcessTreeError(f"cannot own the owned process tree: OpenProcess error {self._ctypes.get_last_error()}")
        try:
            if not self._k32.AssignProcessToJobObject(self._handle, process):
                raise ProcessTreeError("cannot own the owned process tree: AssignProcessToJobObject error "
                                      f"{self._ctypes.get_last_error()}")
            if self._ntdll.NtResumeProcess(process) != 0:
                raise ProcessTreeError("cannot start the process: NtResumeProcess failed")
        finally:
            self._k32.CloseHandle(process)

    def close(self, *, strict: bool = False) -> None:
        if self._handle:
            try:
                terminated = self._k32.TerminateJobObject(self._handle, 1)
                if strict:
                    if not terminated:
                        raise ProcessTreeError("cannot confirm termination of the owned process job")
                    self._wait_empty()
            finally:
                closed = self._k32.CloseHandle(self._handle)
                self._handle = None
                if strict and not closed:
                    raise ProcessTreeError("cannot close the owned process job")

    def _wait_empty(self) -> None:
        """Observe zero active job members before claiming strict cleanup."""
        ctypes = self._ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in (  # noqa: RUF012 - ctypes declaration
                "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")]
            _fields_ += [(name, wintypes.DWORD) for name in (
                "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]

        end = time.monotonic() + GRACE_S
        while True:
            info = Accounting()
            if not self._k32.QueryInformationJobObject(self._handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                raise ProcessTreeError("cannot inspect members of the owned process job")
            if info.ActiveProcesses == 0:
                return
            if time.monotonic() >= end:
                raise ProcessTreeError("owned process job still has active members after termination")
            time.sleep(.01)
