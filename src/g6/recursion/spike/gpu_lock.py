# SPDX-License-Identifier: Apache-2.0
"""One local GPU holder, without touching GPU APIs or probing process ids.

The OS lock is authoritative. Metadata has no expiry and never permits taking
a live lock. The file stays at the same name/inode after release; an exited
holder's metadata is replaced only after obtaining the OS lock.
"""
from __future__ import annotations

import errno
import json
import os
from datetime import datetime, timezone

from g6.core.storage import state_path


class LockBusy(RuntimeError):
    code = "G6_E_GPU_BUSY"

    def __init__(self):
        super().__init__(f"{self.code}: another process holds the GPU reservation")


class GPULock:
    def __init__(self, holder_id: str):
        if not isinstance(holder_id, str) or not holder_id.strip():
            raise ValueError("nonempty holder id required")
        self.id = holder_id
        self.path = state_path("recursion", "gpu.lock")
        self.holder = None
        self._file = None

    def acquire(self):
        if self._file is not None:
            raise LockBusy()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        stream = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise LockBusy() from None
            raise
        try:
            holder = {"id": self.id, "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}
            stream.seek(0)
            stream.write(b"\0" + json.dumps(holder, sort_keys=True).encode("utf-8"))
            stream.truncate()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            raise
        self._file, self.holder = stream, holder
        return self

    def release(self):
        if self._file is not None:
            self._file.close()  # OS release also happens on process exit
            self._file = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()
