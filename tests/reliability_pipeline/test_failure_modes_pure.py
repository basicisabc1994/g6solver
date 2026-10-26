# SPDX-License-Identifier: Apache-2.0
"""``failure_modes`` is pure data with one home for the replay phrasing (audit 009 RP-010, RP-013). Offline."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from g6.reliability_pipeline import failure_modes, intake

SRC = Path(failure_modes.__file__).resolve().parents[1]


def test_rp013_failure_modes_imports_no_other_pipeline_stage() -> None:
    probe = ("import sys; import g6.reliability_pipeline.failure_modes; "
             "print(sorted(m for m in sys.modules if m.startswith('g6.') and m != 'g6.reliability_pipeline.failure_modes'"
             " and m not in ('g6.reliability_pipeline',)))")
    out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True).stdout
    loaded = eval(out)  # noqa: S307 - our own repr of a list of module names
    assert not [m for m in loaded if m.startswith(("g6.reliability_pipeline.", "g6.intent_engine"))], loaded


def test_rp010_the_replay_phrasing_has_one_home() -> None:
    assert intake.REPLAY_REQUIREMENT == failure_modes.REPLAY_REQUIREMENT
    assert intake.REPLAY_ANSWER == failure_modes.REPLAY_ANSWER
    assert failure_modes.states_replay_requirement(failure_modes.REPLAY_REQUIREMENT)
    assert not failure_modes.states_replay_requirement("Make this Python event handler idempotent.")
    literal = "apply each stable event id at most once, even when another event"
    holders = sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if literal in p.read_text(encoding="utf-8"))
    assert holders == ["reliability_pipeline/failure_modes.py", "reliability_pipeline/premortem.py"], holders
