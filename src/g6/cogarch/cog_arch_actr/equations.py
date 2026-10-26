# SPDX-License-Identifier: Apache-2.0
"""The ACT-R declarative and utility equations, as the ACT-R 7.30+ Reference Manual (Bothell) states them.

Each function is one equation; ``source_register.json`` beside this file holds the manual's clause for each, with its
page and SHA-256. Times are seconds.

- base level: ``B_i = ln(sum_j t_j ** -d) + beta_i``, ``t_j`` the time since the j-th presentation, ``d`` the decay
  (recommended 0.5);
- spreading: ``S_i = sum_j W_j * S_ji``, ``W_j`` the source activation divided by the number of sources,
  ``S_ji = S - ln(fan_ji)`` with ``fan_ji = (1 + slots_j) / slotsof_ji``, and a negative ``S_ji`` counted as 0.0;
- noise: logistic, mean 0, scale ``s``;
- retrieval time ``F * e ** (-f * A_i)``; the time to note a failure ``F * e ** (-f * tau)``;
- utility learning ``U_i(n) = U_i(n-1) + alpha * (R_i(n) - U_i(n-1))``, ``R_i(n)`` the reward less the time since the
  n-th selection.
"""
from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

#: The shortest age a presentation is given, in seconds: one production firing (ACT-R's default action time). A
#: presentation made now would otherwise count as infinite.
MIN_AGE_S = 0.05


def base_level(ages: Iterable[float], decay: float, offset: float = 0.0) -> float:
    """``ln(sum t_j ** -d) + beta``; each age is floored at :data:`MIN_AGE_S`. Raises ``ValueError`` with no ages."""
    total = sum(max(age, MIN_AGE_S) ** -decay for age in ages)
    if total <= 0:
        raise ValueError("a chunk has at least one presentation")
    return math.log(total) + offset


def fan(value: object, chunk_slots: Mapping[str, object], all_slots: Iterable[Mapping[str, object]]) -> float:
    """``(1 + slots_j) / slotsof_ji``: how many slots across memory hold ``value``, over how many of this chunk's do."""
    slots_j = sum(1 for slots in all_slots for v in slots.values() if v == value)
    slotsof_ji = sum(1 for v in chunk_slots.values() if v == value)
    return (1 + slots_j) / slotsof_ji if slotsof_ji else math.inf


def spreading(sources: Mapping[str, object], source_activation: float, max_strength: float | None,
              chunk_slots: Mapping[str, object], all_slots: Sequence[Mapping[str, object]], *,
              fan_counts: Mapping[object, int] | None = None) -> tuple[float, list[dict]]:
    """``sum_j W_j * S_ji`` over the source values (``W_j = W / n``), with each term; 0.0 when spreading is off
    (``max_strength`` is ``None``) or there are no sources. A negative strength counts as 0.0 and is marked."""
    values = [v for v in sources.values() if v is not None]
    if max_strength is None or not values or source_activation == 0:
        return 0.0, []
    weight = source_activation / len(values)
    total, terms = 0.0, []
    local_counts = Counter(chunk_slots.values()) if fan_counts is not None else None
    for value in values:
        if local_counts is None:
            f = fan(value, chunk_slots, all_slots)
        else:
            count = local_counts[value]
            f = (1 + fan_counts.get(value, 0)) / count if count else math.inf
        if math.isinf(f):
            continue
        strength = max_strength - math.log(f)
        used = max(strength, 0.0)
        terms.append({"source": value, "fan": f, "strength": strength, "counted": used, "weight": weight,
                      "negative_counted_as_zero": strength < 0})
        total += weight * used
    return total, terms


def logistic_noise(s: float, rng: random.Random) -> float:
    """One draw from a logistic distribution with mean 0 and scale ``s`` (0.0 when ``s`` is 0)."""
    if s <= 0:
        return 0.0
    p = min(max(rng.random(), 1e-12), 1 - 1e-12)
    return s * math.log(p / (1 - p))


def retrieval_time(activation: float, factor: float, exponent: float) -> float:
    """``F * e ** (-f * A)``: seconds to retrieve a chunk of activation ``A``."""
    try:
        latency = factor * math.exp(-exponent * activation)
    except OverflowError as exc:
        raise ValueError("the latency exponent exceeds the finite numeric range") from exc
    if not math.isfinite(latency):
        raise ValueError("the latency is not finite")
    return latency


def failure_time(threshold: float, factor: float, exponent: float) -> float:
    """``F * e ** (-f * tau)``: seconds to note that nothing was retrieved."""
    return retrieval_time(threshold, factor, exponent)


def utility_update(utility: float, reward: float, alpha: float) -> float:
    """``U + alpha * (R - U)``."""
    return utility + alpha * (reward - utility)


__all__ = ["MIN_AGE_S", "base_level", "failure_time", "fan", "logistic_noise", "retrieval_time", "spreading",
           "utility_update"]
