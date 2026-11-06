# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The evaluator-only access boundary (ECHA-10; founder decision A2, 2026-10-09).

The vault itself is product code, ``g6.core.vault`` (graduated, audit 037): it seals the directories or files that hold
hidden tests, withheld fixes, trusted fixtures, oracle implementations and evaluator transcripts, decides membership by
file identity and resolved location, reads through the handle it checked, and records a refusal or a change after
sealing as an incident that voids every record taken against that fixture. This module names it for the research
analysis; the research flag that gated it is gone (G6 0.3).

This is a software check on the research harness's read path, in the same process. It is NOT an operating-system
boundary: code that opens a file directly is not stopped. A result that depends on it is therefore recorded as
``unblinded`` (``blinding_status``), and stays so until a real separation exists.
"""
from __future__ import annotations

from g6.core.vault import ENFORCEMENT, ROLES, AccessDenied, Vault, blinding_status, guarded_read

__all__ = ["ENFORCEMENT", "ROLES", "AccessDenied", "Vault", "blinding_status", "guarded_read"]
