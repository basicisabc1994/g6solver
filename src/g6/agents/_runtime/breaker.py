# SPDX-License-Identifier: Apache-2.0
"""A circuit breaker for one adapter: a deterministic three-state machine with no model in it (AGENT_RUNTIME-02).

CLOSED becomes OPEN after ``failure_threshold`` failures in a row. OPEN becomes HALF_OPEN once ``cooldown_s`` has
passed; then one caller at a time may probe, and the probe's success closes the circuit while its failure opens it
again from that moment. A rate limit or a request timeout (HTTP 429 or 408, or words saying so) is transient: it backs
off without counting toward opening, and it leaves a probe inconclusive, which re-opens the circuit. Any other failure
counts, a server error (5xx) included, so a backend that answers 503 for ever opens the circuit (AGENT_RUNTIME-03). An
outcome that says nothing about the backend (a refusal before it ran, a dry run, a stop) counts neither way: it leaves
a probe inconclusive too, so a probe is always released and the circuit can never stay open for good. Thread-safe; the
clock is injectable.

Ported from the three-state machine of the archive's ``agent_runtime/circuit_breaker.py``, without the model it asked
inside its lock on every failure, and with 5xx counted as failures.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto


class CircuitState(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


#: Statuses that mean "slow down", not "broken".
_TRANSIENT_STATUS = frozenset({408, 429})
_TRANSIENT_WORDS = ("rate limit", "rate_limit", "ratelimit", "too many requests", "request timeout", "http 408",
                    "http 429", "error code: 408", "error code: 429")


def is_transient_provider_error(error: object) -> bool:
    """Whether ``error`` is a rate limit or request timeout: an HTTP ``status_code`` (or ``http_status``) settles it;
    otherwise its words do."""
    if error is None:
        return False
    for attr in ("status_code", "http_status"):
        value = getattr(error, attr, None)
        if value is not None:
            try:
                return int(value) in _TRANSIENT_STATUS
            except (TypeError, ValueError):
                continue
    text = str(error).lower()
    return any(word in text for word in _TRANSIENT_WORDS)


@dataclass
class CircuitBreaker:
    """See the module docstring."""

    failure_threshold: int = 5
    cooldown_s: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False, repr=False)
    _failures: int = field(default=0, init=False, repr=False)
    _opened_at: float = field(default=0.0, init=False, repr=False)
    _probe: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.failure_threshold, bool) or int(self.failure_threshold) < 1:
            raise ValueError("failure_threshold must be 1 or more")
        if not float(self.cooldown_s) >= 0:
            raise ValueError("cooldown_s must be zero or more")

    def _cooled(self) -> bool:
        return self.clock() - self._opened_at >= self.cooldown_s

    @property
    def state(self) -> CircuitState:
        """The state now (reading it claims no probe)."""
        with self._lock:
            if self._state is CircuitState.OPEN and not self._probe and self._cooled():
                return CircuitState.HALF_OPEN
            return self._state

    def acquire_probe(self) -> bool:
        """Claim the one HALF_OPEN probe: true for exactly one caller while the circuit is half open."""
        with self._lock:
            if self._state is CircuitState.OPEN and not self._probe and self._cooled():
                self._state, self._failures = CircuitState.HALF_OPEN, 0
            if self._state is CircuitState.HALF_OPEN and not self._probe:
                self._probe = True
                return True
            return False

    def is_available(self) -> bool:
        """Whether a call may go ahead now: always when CLOSED, and for the one probe when HALF_OPEN."""
        return True if self.state is CircuitState.CLOSED else self.acquire_probe()

    def record_success(self) -> None:
        with self._lock:
            self._state, self._failures, self._probe = CircuitState.CLOSED, 0, False

    def record_failure(self) -> None:
        with self._lock:
            if self._state is CircuitState.HALF_OPEN or self._probe:
                self._state, self._opened_at, self._probe = CircuitState.OPEN, self.clock(), False
                self._failures = self.failure_threshold
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state, self._opened_at = CircuitState.OPEN, self.clock()

    def record_inconclusive(self) -> None:
        """An outcome that says nothing about the backend: no count either way; an in-flight probe is released, so the
        circuit re-opens and its cooldown starts again."""
        with self._lock:
            if self._state is CircuitState.HALF_OPEN or self._probe:
                self._state, self._opened_at, self._probe = CircuitState.OPEN, self.clock(), False

    def record_transient(self) -> None:
        """A rate limit or request timeout: no count toward opening, and an in-flight probe is inconclusive."""
        self.record_inconclusive()

    def record_transient_or_failure(self, error: object) -> None:
        if is_transient_provider_error(error):
            self.record_transient()
        else:
            self.record_failure()


__all__ = ["CircuitBreaker", "CircuitState", "is_transient_provider_error"]
