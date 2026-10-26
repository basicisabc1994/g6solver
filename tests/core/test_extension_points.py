# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from g6.core.extension_points import ExtensionPoints
from g6.core.validation_gates import list_names


def test_extension_points_validate_gate_names() -> None:
    points = ExtensionPoints(
        training_signal_inputs=("reliability_incidents",),
        validation_gate="unit_tests_only",
        allowed_mutation_surfaces=("prompts",),
    )

    assert points.validation_gate in list_names()


def test_extension_points_reject_unknown_gate() -> None:
    try:
        ExtensionPoints(validation_gate="missing_gate")
    except ValueError as exc:
        assert "G6_E_CONTRACT_UNKNOWN_VALIDATION_GATE" in str(exc)
    else:
        raise AssertionError("unknown validation gate should fail")
