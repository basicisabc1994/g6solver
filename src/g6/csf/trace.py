# SPDX-License-Identifier: Apache-2.0
"""Fit an explicitly labelled finite model from observed transition pairs, with a conservative hazard.

A trace gives counts, not probabilities. Plugging in the observed frequencies would give a state whose few
observations all went somewhere safe a hazard of 0, and a bound of 0 on thin data. Here every safe state's chance
of moving to an unsafe state is replaced by an exact upper confidence bound instead (Clopper-Pearson, one-sided,
95% for all safe states at once by a Bonferroni split), and the largest of those bounds is used for every safe
state, so the bound holds whatever the true proportions among the safe successors are. The hazard the trace did not
show goes to one added absorbing unsafe state, ``UNOBSERVED``.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import islice
from types import MappingProxyType
from typing import Any

from g6.core.binomial import upper_bound
from g6.csf.schema import BoundedAgent, CSFInputError, ResourceTriple

#: The added absorbing unsafe state that carries the hazard the trace could not rule out.
UNOBSERVED = "<unobserved>"
#: The simultaneous confidence of the per-state hazard bounds.
CONFIDENCE = 0.95
METHOD = ("Clopper-Pearson exact one-sided upper bound on each safe state's chance of an unsafe next state, 95% for "
          "all safe states together (Bonferroni), the largest bound applied to every safe state")
FIT_ASSUMPTIONS = (
    "Each observed pair is an independent draw of the next state given the current state, from one fixed kernel.",
    "The agent's states are the ones in the trace; a state the trace never left has no bound.",
    "The unsafe labels are right; they come from the caller or an evaluator, not from the trace.",
)


@dataclass(frozen=True)
class FittedAgent(BoundedAgent):
    transition_counts: Mapping[tuple[str, str], int] = field(default_factory=dict)
    fit: Mapping[str, Any] = field(default_factory=dict)

    def describe(self):
        return {**super().describe(), "transition_counts": [
            {"from": a, "to": b, "count": n} for (a, b), n in self.transition_counts.items()],
            "fit": dict(self.fit),
            "provenance": "counts from supplied trace; unsafe labels supplied by user or evaluator",
            "blind_spot": "states never observed, and dependent or biased sampling, are not bounded"}


def fit_from_trace(trace: Iterable[tuple[str, str]], *, unsafe: Iterable[str], min_support: int,
                   resources: ResourceTriple, initial_state: str | None = None) -> BoundedAgent:
    """Return a checked BoundedAgent with per-transition counts and a conservative hazard, or a typed refusal.

    Every pair is one observed transition, not adjacent unrelated audit records.
    Every observed edge needs min_support observations. Every state needs an
    observed outgoing transition; terminal self-loops must be observed too. The
    horizon is the caller's ``resources``; the initial state defaults to the first source.
    """
    try:
        if type(min_support) is not int or min_support < 1:
            raise CSFInputError("G6_E_CSF_TRACE_SUPPORT", "min_support must be a positive integer")
        rows = list(islice(trace, 100001))
        if len(rows) > 100000:
            raise CSFInputError("G6_E_CSF_TRACE_INPUT", "at most 100000 observed pairs")
        counts = Counter()
        for pair in rows:
            if (not isinstance(pair, (tuple, list)) or len(pair) != 2
                    or not all(isinstance(s, str) and s for s in pair)):
                raise CSFInputError("G6_E_CSF_TRACE_INPUT", "each observation is two non-empty state names")
            counts[tuple(pair)] += 1
        if not counts or any(n < min_support for n in counts.values()):
            raise CSFInputError("G6_E_CSF_TRACE_SUPPORT", "no model: every observed transition needs min_support observations")
        if isinstance(unsafe, (str, bytes)):
            raise CSFInputError("G6_E_CSF_TRACE_INPUT", "unsafe is a collection of explicitly labelled states")
        unsafe = tuple(unsafe)
        if not unsafe:
            raise CSFInputError("G6_E_CSF_TRACE_INPUT", "name unsafe states from observed outcomes; no verdict inference")
        states = tuple(dict.fromkeys(s for pair in counts for s in pair))
        if UNOBSERVED in states:
            raise CSFInputError("G6_E_CSF_TRACE_INPUT", f"{UNOBSERVED!r} is reserved for the unseen hazard")
        labelled = frozenset(unsafe)
        successors: dict[str, list[tuple[str, int]]] = {}
        totals, to_unsafe = Counter(), Counter()
        for (a, b), n in counts.items():
            successors.setdefault(a, []).append((b, n))
            totals[a] += n
            to_unsafe[a] += n if b in labelled else 0
        safe = [s for s in states if s not in labelled and totals[s]]
        alpha = (1 - CONFIDENCE) / max(1, len(safe))
        per_state = {s: upper_bound(to_unsafe[s], totals[s], alpha) for s in safe}
        hazard = max(per_state.values(), default=0.0)
        transitions = []
        for a, rows_from in successors.items():
            if a not in per_state:
                transitions += [{"from": a, "to": b, "probability": n / totals[a]} for b, n in rows_from]
                continue
            safe_total = totals[a] - to_unsafe[a]
            transitions += [{"from": a, "to": b, "probability": n / totals[a] if b in labelled
                             else (1 - hazard) * n / safe_total} for b, n in rows_from]
            transitions.append({"from": a, "to": UNOBSERVED, "probability": hazard - to_unsafe[a] / totals[a]})
        fit = {"method": METHOD, "confidence": CONFIDENCE, "per_state_alpha": alpha, "hazard_upper_bound": hazard,
               "per_state": [{"state": s, "observations": totals[s], "to_unsafe": to_unsafe[s], "upper_bound": u}
                             for s, u in per_state.items()],
               "assumptions": list(FIT_ASSUMPTIONS)}
        if safe:
            states += (UNOBSERVED,)
            unsafe += (UNOBSERVED,)
            transitions.append({"from": UNOBSERVED, "to": UNOBSERVED, "probability": 1.0})
        return FittedAgent(states=states, transitions=transitions,
                           initial_state=initial_state if initial_state is not None else rows[0][0],
                           unsafe_states=unsafe, resources=resources,
                           transition_counts=MappingProxyType(dict(counts)), fit=MappingProxyType(fit))
    except CSFInputError:
        raise
    except Exception as exc:  # noqa: BLE001 -- a failed trace reader or validator produces no model
        raise CSFInputError("G6_E_CSF_TRACE_FAILED", f"{type(exc).__name__}: {exc}"[:500]) from exc
