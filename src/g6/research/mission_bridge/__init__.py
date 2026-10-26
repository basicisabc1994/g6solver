# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The mission bridge (integrated audit ENG-01 and following, 2026-10-11). Research code, default OFF.

A mission as a typed graph (``MissionIR``) and the screen that finds a graph that cannot be trusted before anything
runs: type mismatches, missing prerequisites, ops with no authority, a lowered standard, unapproved egress, a budget
overrun. The pure modules import nothing from the product and nothing in the product imports any of this. Set
``G6_MISSION_BRIDGE=1`` to use it.
"""
from .checks import Finding, OpFact, validate
from .flag import FLAG, ResearchDisabled, require_research, research_enabled
from .ir import IRRejected, MissionIR, Node, Standard

__all__ = [
    "FLAG",
    "Finding",
    "IRRejected",
    "MissionIR",
    "Node",
    "OpFact",
    "ResearchDisabled",
    "Standard",
    "require_research",
    "research_enabled",
    "validate",
]
