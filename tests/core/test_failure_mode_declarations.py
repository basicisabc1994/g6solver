# SPDX-License-Identifier: Apache-2.0
"""FW-03: every component declares, by registered code, the failure modes it guards against and the ones its own use
can cause, as literal tuples in its package ``__init__.py`` (read without importing it); the codes and the packages
planned to guard them live in one file per family under ``g6/core/data/failure_modes/``."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import g6.workforce
from g6.core.block_contract import BlockContract
from g6.core.contract_decorator import get_block_contract_attr
from g6.core.failure_modes import CODE, DATA, FailureModeRegistry, declaration_problems, declared
from g6.core.registry import FAMILIES, get_registry


def _packages() -> dict[str, str]:
    reg = get_registry()
    return {name: reg.refs[name].module.split(":", 1)[0] for name in reg.names()}


def _canonical(code: str) -> bool:
    return FailureModeRegistry.is_registered(code) and str(FailureModeRegistry.get(code).code) == code


def test_there_is_one_file_per_family_and_one_for_the_core_harness() -> None:
    files = sorted(DATA.glob("*.json"))
    assert {f.stem for f in files} == FAMILIES | {"core"}
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["family"] == f.stem and set(data) == {"family", "codes", "planned"}, f.name
        assert all(CODE.fullmatch(entry["code"]) and entry["title"] for entry in data["codes"]), f.name


def test_each_planned_package_is_in_g6_and_plans_registered_codes_by_their_own_names() -> None:
    for package, codes in FailureModeRegistry.planned().items():
        top = package.split(".")[1]
        assert package.startswith("g6.") and (top in FAMILIES or importlib.util.find_spec(f"g6.{top}")), package
        assert codes and [c for c in codes if not _canonical(c)] == [], package


def test_every_registered_component_declares_registered_failure_modes() -> None:
    assert [p for package in _packages().values() for p in declaration_problems(package)] == []


@pytest.mark.parametrize(("body", "problem"), [
    ('"""A component that declares nothing."""\n', "no literal __g6_mitigates__"),
    ('__g6_mitigates__ = ("goal_drift",)\n', "no literal __g6_introduces__"),
    ("__g6_mitigates__ = ()\n__g6_introduces__ = ()\n", "mitigates no failure mode"),
    ('__g6_mitigates__ = ("made_up_failure",)\n__g6_introduces__ = ()\n', "'made_up_failure' is not a registered"),
    ('__g6_mitigates__ = ("goal_drift",)\n__g6_introduces__ = ("made_up_cause",)\n', "'made_up_cause' is not a"),
    ('__g6_mitigates__ = ("missing_context",)\n__g6_introduces__ = ()\n', "is an alias of 'context_loss'"),
    ('__g6_mitigates__ = ("goal_drift",)\n__g6_introduces__ = ("goal_drift",)\n', "both mitigated and introduced"),
    ('CODES = ("goal_drift",)\n__g6_mitigates__ = CODES\n__g6_introduces__ = ()\n', "no literal __g6_mitigates__"),
])
def test_a_component_that_declares_none_or_an_unknown_code_fails(tmp_path, monkeypatch, body, problem) -> None:
    (tmp_path / "fw03_component").mkdir()
    (tmp_path / "fw03_component" / "__init__.py").write_text(body, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert problem in " | ".join(declaration_problems("fw03_component"))


def test_a_sound_declaration_has_no_problems(tmp_path, monkeypatch) -> None:
    (tmp_path / "fw03_sound").mkdir()
    (tmp_path / "fw03_sound" / "__init__.py").write_text(
        '__g6_mitigates__: tuple[str, ...] = ("goal_drift",)\n__g6_introduces__: tuple[str, ...] = ()\n',
        encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert declaration_problems("fw03_sound") == []


def test_the_declarations_read_from_source_are_the_ones_the_package_carries() -> None:
    reg = get_registry()
    for name, package in _packages().items():
        mod = reg.get_module(name)
        assert declared(package) == {"__g6_mitigates__": mod.__g6_mitigates__,
                                     "__g6_introduces__": mod.__g6_introduces__}, name


def test_block_contracts_and_workforces_op_contracts_name_only_what_their_package_declares() -> None:
    reg = get_registry()
    for name in reg.names():
        cls = reg.get_class(name)
        contract = get_block_contract_attr(cls) if cls else None
        if contract is not None:
            assert set(contract.mitigates_failure_modes) <= set(reg.get_module(name).__g6_mitigates__), name
    claims = [json.loads(p.read_text(encoding="utf-8")).get("operational")
              for p in sorted((Path(g6.workforce.__file__).parent / "params").glob("*.json"))]
    claims = [c for c in claims if c]
    assert claims
    for claim in claims:
        assert {m["failure_mode"] for m in claim["mitigates_failure_modes"]} <= set(g6.workforce.__g6_mitigates__)
        assert set(claim["introduces_failure_modes"]) <= set(g6.workforce.__g6_introduces__)


def test_the_workforce_failure_surfaces_and_the_premortem_modes_are_registered() -> None:
    from g6.reliability_pipeline.premortem import PREMORTEM_FAILURE_MODES
    from g6.workforce.objective import FAILURE_SURFACES

    codes = [s["id"] for surfaces in FAILURE_SURFACES.values() for s in surfaces] + list(PREMORTEM_FAILURE_MODES)
    assert [c for c in codes if not FailureModeRegistry.is_registered(c)] == []


def test_every_code_a_component_declares_or_a_package_plans_has_a_written_title() -> None:
    reg = get_registry()
    codes = {c for name in reg.names() for attr in ("__g6_mitigates__", "__g6_introduces__")
             for c in getattr(reg.get_module(name), attr)}
    codes |= {c for planned in FailureModeRegistry.planned().values() for c in planned}
    assert sorted(c for c in codes if not FailureModeRegistry.get(c).title) == []


def test_tbd_is_not_a_failure_mode(monkeypatch) -> None:
    # The contract once skipped "TBD", so a placeholder passed as a declaration (FW-03).
    monkeypatch.setenv("G6_CONTRACT_ENFORCE", "raise")
    with pytest.raises(ValueError, match="G6_E_CONTRACT_UNKNOWN_FAILURE_MODE"):
        BlockContract(name="placeholder", mitigates_failure_modes=("TBD",))
