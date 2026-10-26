# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""kill_switch.py -- the per-run, in-memory kill switch.

This is the run-scoped kill switch, ``state.kill_switch.tripped``. It lives on a
single :class:`~g6.safety_engine.state.RunState` and governs exactly one run; it
is not a process-wide stop. That is ``g6.core.stop``, which the policy engine reads
in the same check (audit 007 SE-010); ``G6_DISABLE_LLM`` stops model calls only.

Trust property: the kill switch **outranks the planner**. A planner emits only
data; it can call no methods. Resetting is a capability of the switch's owner:
:meth:`KillSwitch.with_reset_token` hands out a switch and its reset token once,
and the switch keeps only the token's digest, so no other code in the process can
get the token from the module or the switch (audit 007 SE-002: the archive's
``trusted_reset_token()`` gave it to any importer). A switch made without one, as
``RunState`` makes it, cannot be reset at all.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading


def _digest(token: bytes) -> bytes:
    return hashlib.sha256(token).digest()


class KillSwitch:
    """A one-way, run-scoped halt flag.

    ``trip()`` moves the switch from armed to tripped and is monotone within a
    run (the first reason is retained). There is no public attribute to set it
    back: ``reset()`` requires the token handed to the switch's owner and refuses
    otherwise.
    """

    __slots__ = ("_tripped", "_reason", "_lock", "_reset_digest")

    def __init__(self) -> None:
        self._tripped = False
        self._reason = ""
        self._lock = threading.Lock()
        self._reset_digest = b""  # no reset token: this switch can never be reset

    @classmethod
    def with_reset_token(cls) -> tuple[KillSwitch, bytes]:
        """A new switch and the only token that resets it, handed out here and nowhere else."""
        switch = cls()
        token = secrets.token_bytes(32)
        switch._reset_digest = _digest(token)
        return switch, token

    @property
    def tripped(self) -> bool:
        # Read without the lock: the flag only ever transitions False->True
        # once (monotone, one-way) and a bool read is atomic under CPython, so a
        # concurrent reader sees either the pre- or post-trip value, never a torn
        # one. The conservative direction (a reader briefly seeing not-yet-tripped
        # then re-checking at the next loop iteration) is safe.
        return self._tripped

    @property
    def reason(self) -> str:
        return self._reason

    def trip(self, reason: str = "") -> None:
        """Trip the switch (idempotent / monotone within a run)."""
        with self._lock:
            if not self._tripped:
                self._tripped = True
                self._reason = reason or "kill switch tripped"

    def reset(self, token: object) -> None:
        """Clear the switch — its owner only.

        Raises :class:`PermissionError` unless *token* is the one
        :meth:`with_reset_token` handed out for this switch. This is never called
        from planner output.
        """
        if not (self._reset_digest and isinstance(token, bytes)
                and hmac.compare_digest(_digest(token), self._reset_digest)):
            raise PermissionError(
                "kill switch reset refused: the caller does not hold this switch's reset token"
            )
        with self._lock:
            self._tripped = False
            self._reason = ""
