# SPDX-License-Identifier: Apache-2.0
"""The door rule (DECISIONS #40, plan A1): ``verified`` stands only when G6's own code re-runs a check at the door
and reproduces it; a witness the producer hands over is ignored. Offline."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "g6"

#: Environment switches that once turned a door off, narrowed it or staged it. In one process any switch is a
#: bypass, so none may be read anywhere in the library.
RETIRED_SWITCHES = (
    "G6_TASK_CONTRACT_ENFORCE",
    "G6_TASK_CONTRACT_ENFORCE_SURFACES",
    "G6_METHOD_CONTRACT",
)


def _library_text() -> dict[str, str]:
    return {str(p.relative_to(SRC)): p.read_text(encoding="utf-8", errors="replace")
            for p in SRC.rglob("*.py") if "__pycache__" not in p.parts}


@pytest.mark.parametrize("switch", RETIRED_SWITCHES)
def test_no_retired_switch_is_read_anywhere_in_the_library(switch: str) -> None:
    pattern = re.compile(rf"\b{switch}\b")
    readers = sorted(path for path, text in _library_text().items() if pattern.search(text))
    assert readers == [], f"{switch} still appears in {readers}"


def test_the_task_contract_module_keeps_only_what_intake_uses() -> None:
    """The task-contract floor had no inputs: nothing fed ``task_mode`` or ``task_success_criteria`` to an envelope.
    What intake uses (``verifier_available`` and ``HUMAN_VERIFIER_PREFIX``) stays."""
    from g6.core import task_contract_verifier as tcv

    assert sorted(tcv.__all__) == ["HUMAN_VERIFIER_PREFIX", "verifier_available"]
    for gone in ("task_contract_floor_reasons", "task_contract_enforce_mode", "task_contract_enforce_scope",
                 "effective_task_contract_enforce_mode", "method_contract_enabled"):
        assert not hasattr(tcv, gone), gone
    assert tcv.verifier_available("human:expert_review") and not tcv.verifier_available("")


def test_an_envelope_has_no_task_contract_floor() -> None:
    from g6.core.reliability_envelope import ReliabilityEnvelope

    assert not hasattr(ReliabilityEnvelope, "_apply_task_contract_floor")
