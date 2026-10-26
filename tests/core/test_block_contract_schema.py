# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from g6.core.block_contract import (
    BlockContract,
    contract_from_dict,
    contract_to_dict,
    _contract_enforce_mode,
)
from g6.core.extension_points import ExtensionPoints
from g6.core.state_surface import StateSurface


def test_block_contract_v2_fields_default_compatibly() -> None:
    contract = BlockContract(name="unit")

    assert contract.__contract_schema_version__ == "2.0"
    assert contract.mitigates_failure_modes == ()
    assert contract.state_surface == StateSurface()
    assert contract.extension_points is None


def test_block_contract_v2_fields_round_trip() -> None:
    contract = BlockContract(
        name="unit",
        mitigates_failure_modes=("hallucinated_success",),
        state_surface=StateSurface(persistence="project", queryable_via="scorecard", retention="90d"),
        extension_points=ExtensionPoints(validation_gate="unit_tests_only"),
    )

    restored = contract_from_dict(contract_to_dict(contract))

    assert restored.mitigates_failure_modes == ("hallucinated_success",)
    assert restored.state_surface.persistence == "project"
    assert restored.extension_points is not None
    assert restored.extension_points.validation_gate == "unit_tests_only"


def test_block_contract_enforce_raise_rejects_unknown_mitigated_failure_mode(monkeypatch) -> None:
    monkeypatch.setenv("G6_CONTRACT_ENFORCE", "raise")
    try:
        BlockContract(name="bad", mitigates_failure_modes=("not_registered",))
    except ValueError as exc:
        assert "G6_E_CONTRACT_UNKNOWN_FAILURE_MODE" in str(exc)
    else:
        raise AssertionError("unknown failure mode should fail")


def test_block_contract_enforce_warn_logs_unknown_mitigated_failure_mode(monkeypatch, caplog) -> None:
    monkeypatch.setenv("G6_CONTRACT_ENFORCE", "warn")

    contract = BlockContract(name="bad", mitigates_failure_modes=("not_registered",))

    assert contract.mitigates_failure_modes == ("not_registered",)
    assert "G6_E_CONTRACT_UNKNOWN_FAILURE_MODE" in caplog.text


def test_block_contract_enforce_off_skips_unknown_mitigated_failure_mode(monkeypatch, caplog) -> None:
    monkeypatch.setenv("G6_CONTRACT_ENFORCE", "off")

    contract = BlockContract(name="bad", mitigates_failure_modes=("not_registered",))

    assert contract.mitigates_failure_modes == ("not_registered",)
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# Production-aware default (audit 2026-06-21): unset G6_CONTRACT_ENFORCE is
# fail-CLOSED ('raise') in production, 'warn' in dev/test. Explicit always wins.
# ---------------------------------------------------------------------------

def test_enforce_mode_defaults_to_raise_in_production(monkeypatch) -> None:
    monkeypatch.delenv("G6_CONTRACT_ENFORCE", raising=False)
    monkeypatch.setenv("G6_ENV", "production")
    assert _contract_enforce_mode() == "raise"
    # End-to-end: an unregistered mitigated failure mode is rejected (fail-closed)
    # in production with no explicit enforce var set.
    with pytest.raises(ValueError, match="G6_E_CONTRACT_UNKNOWN_FAILURE_MODE"):
        BlockContract(name="bad_prod", mitigates_failure_modes=("not_registered",))


def test_enforce_mode_defaults_to_warn_in_dev(monkeypatch, caplog) -> None:
    monkeypatch.delenv("G6_CONTRACT_ENFORCE", raising=False)
    monkeypatch.delenv("G6_ENV", raising=False)
    assert _contract_enforce_mode() == "warn"
    # Dev/test must NOT raise — contract authoring stays unblocked.
    contract = BlockContract(name="bad_dev", mitigates_failure_modes=("not_registered",))
    assert contract.mitigates_failure_modes == ("not_registered",)
    assert "G6_E_CONTRACT_UNKNOWN_FAILURE_MODE" in caplog.text


def test_explicit_enforce_overrides_production(monkeypatch) -> None:
    # The explicit env var wins even in production (e.g. an operator deliberately
    # silencing during a migration).
    monkeypatch.setenv("G6_ENV", "production")
    monkeypatch.setenv("G6_CONTRACT_ENFORCE", "off")
    assert _contract_enforce_mode() == "off"
    BlockContract(name="ok", mitigates_failure_modes=("not_registered",))  # no raise
