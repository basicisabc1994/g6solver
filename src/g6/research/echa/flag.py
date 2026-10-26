# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The research flag (founder decision A1, 2026-10-09): the evidence kernel is OFF unless the environment says a plain yes.

The kernel is research code. It is not imported by any product module, and every entry point that would create, write or
read a record refuses with ``ResearchDisabled`` while the flag is off, so turning it off changes nothing else.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

FLAG = "G6_ECHA_RESEARCH"
_YES = frozenset({"1", "true", "yes", "on"})


class ResearchDisabled(RuntimeError):
    """The ECHA research flag is off, so nothing was created, written or read."""


def research_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only for a plain yes in the flag; anything else, including a typo, is off. Never raises."""
    source = os.environ if env is None else env
    return str(source.get(FLAG, "")).strip().lower() in _YES


def require_research() -> None:
    if not research_enabled():
        raise ResearchDisabled(f"the ECHA research kernel is off: set {FLAG}=1 to use it (research only)")
