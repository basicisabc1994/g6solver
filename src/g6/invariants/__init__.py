# SPDX-License-Identifier: Apache-2.0
"""g6.invariants — check that a result envelope says only what its own fields support.

g6.invariants — check that a result envelope says only what its own fields support (audit 031 §11).

Capability label: ``implemented``.

``check_envelope(payload)`` lists every rule an envelope breaks (state label, warning card with a ``G6_E_*`` code on a
result that is not verified, evidence on a verified one, ``request_id`` / ``run_id`` present, ``degraded`` consistent);
``assert_envelope`` raises with the list; ``nonvacuous_pass`` refuses a pass from checks that checked nothing.

Limits: it checks consistency, not truth: a verified envelope with fabricated evidence passes. The archive's category
and tier system, its 14 verifiers, gate, pytest plugin and MCP tool are not ported (audit 031 §11).
"""
from g6.invariants.envelope import (
    ERROR_CODE_PATTERN,
    RELIABILITY_LABELS,
    Violation,
    assert_envelope,
    check_envelope,
    nonvacuous_pass,
)

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("overstated_verification",)
__g6_introduces__: tuple[str, ...] = ()

__all__ = ["ERROR_CODE_PATTERN", "RELIABILITY_LABELS", "Violation", "assert_envelope", "check_envelope",
           "nonvacuous_pass"]
