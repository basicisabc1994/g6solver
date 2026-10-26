# SPDX-License-Identifier: Apache-2.0
"""A strict eligible-set clamp; model output can reorder or drop, never add text."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RerankDecision:
    ordered_indices: tuple[int, ...]
    dropped_indices: tuple[int, ...] = ()
    eligible_fingerprint: str = ""


def validate_rerank_decision(decision: RerankDecision, *, n_eligible: int,
                             expected_fingerprint: str) -> tuple[int, ...]:
    """Return the kept indices, or refuse the entire decision with a stable code.

    The trusted caller computes the expected fingerprint before invoking its model.
    Omitted indices are dropped. This does not alter build_packet's legacy seam.
    """
    code = "G6_E_CONTEXT_RERANK_INVALID"
    if type(n_eligible) is not int or not 0 <= n_eligible <= 100_000:
        raise ValueError(f"[{code}] invalid eligible count")
    if not expected_fingerprint or decision.eligible_fingerprint != expected_fingerprint:
        raise ValueError(f"[{code}] decision belongs to a different eligible set")
    union = (*decision.ordered_indices, *decision.dropped_indices)
    if any(type(i) is not int or not 0 <= i < n_eligible for i in union):
        raise ValueError(f"[{code}] index outside retrieved candidates")
    if len(set(union)) != len(union):
        raise ValueError(f"[{code}] repeated or both kept and dropped index")
    return tuple(decision.ordered_indices)
