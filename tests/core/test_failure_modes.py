# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from g6.core.failure_modes import FailureModeCode, FailureModeRecord, FailureModeRegistry


def test_failure_mode_registry_has_self_programming_seeds() -> None:
    codes = {str(record.code) for record in FailureModeRegistry.list_all()}

    assert "hallucinated_success" in codes
    assert "unsafe_autonomous_action" in codes
    assert len(codes) >= 24


def test_failure_mode_registry_registers_custom_records(monkeypatch) -> None:
    # The registry is class-level state: register into a copy, so the failure map other tests check is left as shipped.
    monkeypatch.setattr(FailureModeRegistry, "_records", dict(FailureModeRegistry._records))
    record = FailureModeRecord(
        code=FailureModeCode("unit_custom_failure"),
        description="custom",
        aliases=("unit_custom_alias",),
    )

    FailureModeRegistry.register(record)

    assert FailureModeRegistry.is_registered("unit_custom_failure")
    assert FailureModeRegistry.get("unit_custom_alias") is record
