# SPDX-License-Identifier: Apache-2.0
"""On Windows, ``msvcrt.locking(LK_LOCK)`` gives up after ten one-second tries and raises ``OSError``; the repair log
swallowed that error, so a writer that waited longer than ten seconds behind other servers lost its row silently (one of
12,000 rows from eight processes under load). A writer keeps waiting for its turn within a bounded time."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from g6.core import repair_log

pytestmark = pytest.mark.skipif(os.name != "nt", reason="msvcrt locking is Windows only")


def test_a_lock_wait_past_lk_locks_retries_still_writes_the_row(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import msvcrt

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    real, busy = msvcrt.locking, {"left": 2}

    def locking(fd: int, mode: int, n: int) -> None:
        if mode == msvcrt.LK_LOCK and busy["left"]:
            busy["left"] -= 1
            raise OSError(36, "Resource deadlock avoided")      # what LK_LOCK raises after its ten tries
        real(fd, mode, n)

    monkeypatch.setattr(msvcrt, "locking", locking)
    repair_log.append({"event": "refused", "id": "a:1", "code": "G6_E_INPUT_VALIDATION", "tool": "g6_select"})
    rows = (tmp_path / "home/repairs/repairs.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(r)["id"] for r in rows] == ["a:1"]
