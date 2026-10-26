# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The evaluator-only access boundary (ECHA-10; founder decision A2, 2026-10-09).

The vault itself is product code, ``g6.core.vault`` (graduated, audit 037): it seals the directories or files that hold
hidden tests, withheld fixes, trusted fixtures, oracle implementations and evaluator transcripts, decides membership by
file identity and resolved location, reads through the handle it checked, and records a refusal or a change after
sealing as an incident that voids every record taken against that fixture. This module adds the research flag to every
entry point that seals, checks or reads.

This is a software check on the research harness's read path, in the same process. It is NOT an operating-system
boundary: code that opens a file directly is not stopped. A result that depends on it is therefore recorded as
``unblinded`` (``blinding_status``), and stays so until a real separation exists.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from g6.core.vault import ENFORCEMENT, ROLES, AccessDenied, blinding_status
from g6.core.vault import Vault as _CoreVault
from g6.core.vault import guarded_read as _core_guarded_read

from .flag import require_research

__all__ = ["ENFORCEMENT", "ROLES", "AccessDenied", "Vault", "blinding_status", "guarded_read"]


class Vault(_CoreVault):
    @classmethod
    def seal(cls, roots: Sequence[str | Path], *, ledger: Any, vault_id: str = "vault") -> Vault:
        require_research()
        return super().seal(roots, ledger=ledger, vault_id=vault_id)

    def verify_unchanged(self, *, ledger: Any = None) -> tuple[str, ...]:
        require_research()
        return super().verify_unchanged(ledger=ledger)


def guarded_read(vault: _CoreVault, role: str, path: str | os.PathLike | bytes, *, ledger: Any = None) -> bytes:
    require_research()
    return _core_guarded_read(vault, role, path, ledger=ledger)
