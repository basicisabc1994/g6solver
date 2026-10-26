# SPDX-License-Identifier: Apache-2.0
"""The workforce skill says what the repository check cannot do yet, so a run that names no failure is not read as a
check of the change (Study V7's pilot, 2026-10-25; the owner, 2026-10-26). Drop a limit here when its fix lands."""
from __future__ import annotations

from pathlib import Path

SKILL = Path(__file__).resolve().parents[2] / "src" / "g6" / "workforce" / "skill" / "SKILL.md"
LIMITS = (
    "pytest only",                  # no Go or JavaScript runner
    "Go and JavaScript",
    "`collection_error`",           # one file that cannot be imported ends the run
    "starting commit",              # no comparison with the tests before the change
    "older than 2.31",              # git refused
    "G6_E_GIT_SUBMODULE_REFUSED",   # submodules refused
)


def test_the_skill_names_the_repository_checks_limits() -> None:
    text = " ".join(SKILL.read_text(encoding="utf-8").split())
    missing = [limit for limit in LIMITS if limit not in text]
    assert missing == [], f"the workforce skill does not state: {missing}"
