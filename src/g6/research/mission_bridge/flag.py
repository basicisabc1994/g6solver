# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The research flag: the mission bridge is OFF unless the environment says a plain yes (integrated audit ENG-01).

Same rule as the ECHA kernel's flag. Research code, not imported by any product module; every entry point that would
build or judge a mission graph refuses with ``ResearchDisabled`` while the flag is off.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

FLAG = "G6_MISSION_BRIDGE"
_YES = frozenset({"1", "true", "yes", "on"})


class ResearchDisabled(RuntimeError):
    """The mission-bridge research flag is off, so nothing was built or judged."""


def research_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only for a plain yes in the flag; anything else, including a typo, is off. Never raises."""
    source = os.environ if env is None else env
    return str(source.get(FLAG, "")).strip().lower() in _YES


def require_research() -> None:
    if not research_enabled():
        raise ResearchDisabled(f"the mission-bridge research code is off: set {FLAG}=1 to use it (research only)")
