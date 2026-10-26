# SPDX-License-Identifier: Apache-2.0
"""The model ``g6.csf`` computes on: a finite Markov chain over named states, checked once, on the way in.

``validate_transitions`` is the one validator every entry point uses (audit 012, CSF-002). The archive had two: its
operational ``verify`` checked states and sums, while its formal bridge skipped rows that were not objects, read an
unreadable probability as 1.0, kept unknown ``from``/``to`` names (mass sent to a typo then vanished, which read as
safety) and turned a kernel error into a self-loop. Here a model that is not a complete, explicit probability
distribution over declared states, for every state, is refused with ``G6_E_CSF_MODEL_INVALID``. There is no synthetic
or default kernel (CSF-012: every archive MCP call ran on one).

Nothing here is measured: the states, the probabilities and which states are unsafe are the caller's.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import InitVar, asdict, dataclass, field
from typing import Any

from g6.core.reliability_envelope import envelope_fields, warning_card

MODEL_INVALID = "G6_E_CSF_MODEL_INVALID"
EPSILON_INVALID = "G6_E_CSF_EPSILON_INVALID"
INPUT_INVALID = "G6_E_CSF_INPUT_INVALID"

#: The outgoing probabilities of each state must sum to 1 within this tolerance.
SUM_TOLERANCE = 1e-9
#: Largest horizon accepted, so a typo cannot start an unbounded computation.
MAX_HORIZON = 100_000
MAX_STATES = 10_000

_ROW_KEYS = frozenset({"from", "to", "probability"})


class CSFError(Exception):
    """A refusal or failure with a stable ``G6_E_CSF_*`` code and the reason in words."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


class CSFInputError(CSFError, ValueError):
    """An input g6.csf will not compute on: an invalid model, epsilon, horizon, id or operation list."""


def _is_real(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def check_epsilon(value: Any, name: str = "epsilon") -> float:
    """``value`` as a float in [0, 1]; ``CSFInputError(G6_E_CSF_EPSILON_INVALID)`` otherwise (audit 012, CSF-010)."""
    if not _is_real(value) or not 0.0 <= value <= 1.0:
        raise CSFInputError(EPSILON_INVALID, f"{name} must be a probability in [0, 1], got {value!r}")
    return float(value)


def check_id(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise CSFInputError(INPUT_INVALID, f"{name} must be a string, got {type(value).__name__}")
    return value


def _check_states(states: Any) -> tuple[str, ...]:
    if isinstance(states, str | bytes | Mapping) or not isinstance(states, Iterable):
        raise CSFInputError(MODEL_INVALID, "states must be a list of state names")
    names = tuple(states)
    if not names:
        raise CSFInputError(MODEL_INVALID, "a model needs at least one state")
    if len(names) > MAX_STATES:
        raise CSFInputError(MODEL_INVALID, f"a model has at most {MAX_STATES} states, got {len(names)}")
    for name in names:
        if not isinstance(name, str) or not name:
            raise CSFInputError(MODEL_INVALID, f"every state is a non-empty string, got {name!r}")
    if len(set(names)) != len(names):
        raise CSFInputError(MODEL_INVALID, "state names must be unique")
    return names


@dataclass(frozen=True)
class TransitionOutcome:
    """One successor of a state and the probability of moving to it in one step."""

    next_state: str
    probability: float


def validate_transitions(states: Iterable[str],
                         transitions: Iterable[Mapping[str, Any]]) -> dict[str, tuple[TransitionOutcome, ...]]:
    """The validated kernel: each state's successors with positive probability.

    ``transitions`` is a list of ``{"from": state, "to": state, "probability": p}`` rows. Refused, with
    ``G6_E_CSF_MODEL_INVALID``: no rows; a row that is not exactly those three keys; a state that is not declared; a
    probability that is not a real number in [0, 1] (``True`` included); the same ``from``/``to`` pair twice; a state
    with no outgoing row (write ``s -> s`` with probability 1.0 for a state the agent stays in); outgoing
    probabilities that do not sum to 1 within ``SUM_TOLERANCE``. Rows with probability 0 are accepted and dropped.
    """
    names = _check_states(states)
    known = frozenset(names)
    if isinstance(transitions, str | bytes | Mapping) or not isinstance(transitions, Iterable):
        raise CSFInputError(MODEL_INVALID, "transitions must be a list of {from, to, probability} rows")
    rows = list(transitions)
    if not rows:
        # audit 012, CSF-012: the archive fell back to a synthetic cyclic kernel when no transitions were given.
        raise CSFInputError(MODEL_INVALID, "a model needs explicit transitions; there is no synthetic default")
    by_source: dict[str, dict[str, float]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise CSFInputError(MODEL_INVALID, f"transition {i} is a {type(row).__name__}, not a "
                                               "{from, to, probability} row")
        if set(row) != _ROW_KEYS:
            raise CSFInputError(MODEL_INVALID, f"transition {i} has keys {sorted(map(str, row))}; a row has exactly "
                                               "from, to and probability")
        src, dst, p = row["from"], row["to"], row["probability"]
        for end in (src, dst):
            if not isinstance(end, str) or end not in known:
                # audit 012, CSF-002: the archive kept an undeclared target, and the mass sent there was lost.
                raise CSFInputError(MODEL_INVALID, f"transition {i} references an undeclared state {end!r}")
        if not _is_real(p) or not 0.0 <= p <= 1.0:
            # audit 012, CSF-002: the archive read an unreadable probability as 1.0.
            raise CSFInputError(MODEL_INVALID, f"transition {i} ({src!r} -> {dst!r}) has probability {p!r}; it must "
                                               "be a real number in [0, 1]")
        outgoing = by_source.setdefault(src, {})
        if dst in outgoing:
            raise CSFInputError(MODEL_INVALID, f"transition {src!r} -> {dst!r} is given twice")
        outgoing[dst] = float(p)
    for name in names:
        outgoing = by_source.get(name)
        if not outgoing:
            # audit 012, CSF-002: the archive made a state with no listed successor a silent self-loop.
            raise CSFInputError(MODEL_INVALID, f"state {name!r} has no outgoing transition; for a state the agent "
                                               f"stays in, write {name!r} -> {name!r} with probability 1.0")
        total = math.fsum(outgoing.values())
        if abs(total - 1.0) > SUM_TOLERANCE:
            raise CSFInputError(MODEL_INVALID, f"the transitions from {name!r} sum to {total:.12g}, not 1")
    return {name: tuple(TransitionOutcome(dst, p) for dst, p in by_source[name].items() if p > 0.0)
            for name in names}


@dataclass(frozen=True, kw_only=True)
class ResourceTriple:
    """The model's declared bounds. ``N`` is the horizon every check uses, at least 1 (audit 012, CSF-007: the archive
    accepted N=0, which made every query vacuously safe). ``T`` (steps per action) and ``M`` (memory units) are
    recorded in the evidence and checked by nothing here.

    The archive's ``epsilon`` and ``delta`` fields are gone: epsilon belongs to the question, not the model (the
    archive's Z3 path and its verifier read two different epsilons), and ``delta`` was never used (CSF-013).
    """

    N: int
    T: int = 0
    M: int = 0

    def __post_init__(self) -> None:
        for name, value in (("N", self.N), ("T", self.T), ("M", self.M)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CSFInputError(MODEL_INVALID, f"ResourceTriple.{name} must be a non-negative integer, "
                                                   f"got {value!r}")
        if not 1 <= self.N <= MAX_HORIZON:
            raise CSFInputError(MODEL_INVALID, f"the horizon N must be between 1 and {MAX_HORIZON}, got {self.N}")


@dataclass(frozen=True)
class BoundedAgent:
    """A finite Markov model of an agent: ``states``, explicit ``transitions`` (see :func:`validate_transitions`), the
    ``initial_state``, the ``unsafe_states`` and the horizon in ``resources``.

    The transitions are validated when the agent is built and kept as ``kernel`` (each state's successors with
    positive probability; build a new agent rather than editing it). An unsafe state that is not declared is refused:
    a typo there would make every bound 0. An empty unsafe set is accepted here, since ``check_z3`` reads a property
    over states, and :func:`~g6.csf.reachability_bound` refuses it.
    """

    states: tuple[str, ...]
    transitions: InitVar[Iterable[Mapping[str, Any]]]
    initial_state: str
    unsafe_states: frozenset[str]
    resources: ResourceTriple
    kernel: dict[str, tuple[TransitionOutcome, ...]] = field(init=False, repr=False, hash=False)

    def __post_init__(self, transitions: Iterable[Mapping[str, Any]]) -> None:
        names = _check_states(self.states)
        kernel = validate_transitions(names, transitions)
        if not isinstance(self.initial_state, str) or self.initial_state not in kernel:
            raise CSFInputError(MODEL_INVALID, f"the initial state {self.initial_state!r} is not a declared state")
        if isinstance(self.unsafe_states, str | bytes | Mapping) or not isinstance(self.unsafe_states, Iterable):
            raise CSFInputError(MODEL_INVALID, "unsafe_states must be a list of state names")
        unsafe = list(self.unsafe_states)
        undeclared = [repr(s) for s in unsafe if not isinstance(s, str) or s not in kernel]
        if undeclared:
            raise CSFInputError(MODEL_INVALID, f"unsafe states {', '.join(undeclared)} are not declared states")
        if not isinstance(self.resources, ResourceTriple):
            raise CSFInputError(MODEL_INVALID, "resources must be a ResourceTriple")
        object.__setattr__(self, "states", names)
        object.__setattr__(self, "unsafe_states", frozenset(unsafe))
        object.__setattr__(self, "kernel", kernel)

    def describe(self) -> dict[str, Any]:
        """The model as plain data, for evidence: what every number computed on it depends on."""
        return {
            "states": list(self.states),
            "initial_state": self.initial_state,
            "unsafe_states": sorted(self.unsafe_states),
            "transitions": [{"from": s, "to": o.next_state, "probability": o.probability}
                            for s in self.states for o in self.kernel[s]],
            "horizon_N": self.resources.N,
            "declared_T": self.resources.T,
            "declared_M": self.resources.M,
        }


def labelled(result: Any, *, state: str, code: str, message: str, action: str,
             evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """``result`` (a dataclass with ``request_id`` and ``run_id``) as a library envelope. ``state`` is
    ``qualified-draft`` or ``blocked-escalated``; nothing in g6.csf gives its own work ``verified``."""
    if state not in ("qualified-draft", "blocked-escalated"):
        raise ValueError(f"g6.csf labels results qualified-draft or blocked-escalated, not {state!r}")
    card = warning_card(code, message, action)
    card["severity"] = "blocked" if state == "blocked-escalated" else "amber"
    envelope = envelope_fields(reliability_label=state, warning_card=card, evidence=evidence,
                               request_id=result.request_id, run_id=result.run_id, degradation_reason=message)
    return {**asdict(result), **envelope}


__all__ = ["EPSILON_INVALID", "INPUT_INVALID", "MAX_HORIZON", "MAX_STATES", "MODEL_INVALID", "SUM_TOLERANCE",
           "BoundedAgent", "CSFError", "CSFInputError", "ResourceTriple", "TransitionOutcome", "check_epsilon",
           "check_id", "labelled", "validate_transitions"]
