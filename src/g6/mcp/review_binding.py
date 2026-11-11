# SPDX-License-Identifier: Apache-2.0
"""Versioned custody beside a saved reply; no binding field enters the reply's persisted schema."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path


class BoundReply(dict):
    """Process-local custody, deliberately excluded by the JSON encoder."""

    def __init__(self, reply: dict, identity: str):
        super().__init__(reply)
        self.task_identity = identity


def write(path: Path, reply: dict, data: bytes) -> None:
    """Publish custody last. A stop at any earlier boundary leaves an inadmissible reply."""
    if not isinstance(reply, BoundReply):
        return
    record = {"version": "review-binding.v1", "task_identity": reply.task_identity,
              "reply_sha256": hashlib.sha256(data).hexdigest()}
    side = path.with_suffix(".binding.json")
    part = side.with_name(side.name + ".part")
    with os.fdopen(os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="ascii",
                   newline="\n") as out:
        json.dump(record, out, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
    os.replace(part, side)


def matches(path: Path, identity: str, *, data: bytes | None = None) -> bool:
    """Read both files afresh; missing, partial, foreign or replaced custody supplies no candidates."""
    try:
        side = path.with_suffix(".binding.json")
        if not all(stat.S_ISREG(os.lstat(one).st_mode) for one in (path, side)):
            return False
        record = json.loads(side.read_bytes())
        return record == {"version": "review-binding.v1", "task_identity": identity,
                          "reply_sha256": hashlib.sha256(path.read_bytes() if data is None else data).hexdigest()}
    except (OSError, ValueError, TypeError):
        return False
