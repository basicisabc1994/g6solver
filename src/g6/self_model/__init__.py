# SPDX-License-Identifier: Apache-2.0
"""An inspectable self-model: immutable facts, discrepancies and pre-action simulations.

This package performs no I/O and executes no actions. Its returned rows belong in
the runner's cycle log; predictions confer no gate authority.
"""

__g6_component__ = True
__g6_mitigates__: tuple[str, ...] = ("stale_context_used", "risk_management_failure")
__g6_introduces__: tuple[str, ...] = ("overstated_certainty",)
