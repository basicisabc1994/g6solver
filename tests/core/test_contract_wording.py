# SPDX-License-Identifier: Apache-2.0
"""A block contract is declared structural obligations, and its module says which code checks which part, naming no
checker that does not exist (plan Step 1.5)."""
from __future__ import annotations

import importlib
import inspect

# The module, not g6.core's decorator of the same name, which shadows it as an attribute of g6.core.
block_contract = importlib.import_module("g6.core.block_contract")


def test_the_contract_module_names_no_checker_that_does_not_exist() -> None:
    text = inspect.getsource(block_contract)
    for gone in ("JobReliabilityOrchestrator", "job_reliability_orchestrator", "verify_output", "test_fleet_drift",
                 "truthfulness harness", "mvp.core"):
        assert gone not in text, gone
    # Restoring any of the old references to an orchestrator, a fleet-drift test or a truthfulness harness makes
    # this fail.


def test_the_contract_module_says_what_is_checked_and_what_is_description() -> None:
    doc = " ".join((block_contract.__doc__ or "").split())
    assert "declared structural obligations" in doc
    assert "strict composition" in doc and "every other field is description" in doc
