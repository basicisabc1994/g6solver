# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""AHI's research flag (founder decision A1, 2026-10-09): governed adaptation is OFF unless the environment says a plain
yes.

The flag gated the ECHA evidence kernel too until G6 0.3, which made ECHA a view over the issuer's records with no flag.
It stays here only for AHI's wrapper of the graduated gate, keeping its variable so nothing that sets it changes, until
that wrapper is retired as the G6 0.3 design's section 6 decides (``FailureCase`` and ``Funnel`` move to core for B).
AHI is not imported by any product module, and every entry point refuses with ``ResearchDisabled`` while the flag is off.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

FLAG = "G6_ECHA_RESEARCH"
_YES = frozenset({"1", "true", "yes", "on"})


class ResearchDisabled(RuntimeError):
    """The research flag is off, so nothing was created, written or read."""


def research_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only for a plain yes in the flag; anything else, including a typo, is off. Never raises."""
    source = os.environ if env is None else env
    return str(source.get(FLAG, "")).strip().lower() in _YES


def require_research() -> None:
    if not research_enabled():
        raise ResearchDisabled(f"AHI's research code is off: set {FLAG}=1 to use it (research only)")
