# SPDX-License-Identifier: Apache-2.0
"""Meta-harness Phase 0: the guardrails exist before any behaviour does (META_HARNESS_PLAN.md section 6, P0-1..P0-4)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sysconfig
import tomllib
from pathlib import Path

import pytest

import g6
from g6.core.registry import get_registry
from g6.self_training import patch_promotion_gate as gate
from g6.self_training.patch_promotion_gate import BLOCK, HarnessPatch, PatchEvalReport, govern_patch_promotion

SRC = Path(g6.__file__).parent
ROOT = SRC.parents[1]

# (fragments that protect it, a real judging module the meta-harness relies on)
_JUDGES = [
    (("metaharness/", "harness/"), "src/g6/metaharness/__init__.py"),
    (("evidence/",), "src/g6/evidence/certificate.py"),
    (("learning/promotion",), "src/g6/learning/promotion.py"),
    (("learning/paired",), "src/g6/learning/paired.py"),
    (("reasoning/compare",), "src/g6/reasoning/compare.py"),
    (("distillation/admission",), "src/g6/distillation/admission.py"),
    (("harness/",), "src/g6/harness/__init__.py"),
    (("core/ledger",), "src/g6/core/ledger.py"),
    (("core/vault",), "src/g6/core/vault.py"),
    (("core/adaptation_gate",), "src/g6/core/adaptation_gate.py"),
]


def _patch(path: str) -> HarnessPatch:
    return HarnessPatch(patch_id="p", target_surfaces=["prompt"], touched_paths=[path], diff="x")


def _passing_report() -> PatchEvalReport:
    return PatchEvalReport(targeted_failure_fixed=True, regression_pass_rate=1.0, safety_violations=0,
                           injection_resilience_not_worse=True, target_metric_improvement=0.5,
                           evaluator_confidence=1.0)


@pytest.mark.parametrize("fragments,path", _JUDGES)
def test_p0_1_a_patch_touching_what_judges_a_meta_harness_change_is_blocked(fragments, path) -> None:
    assert (ROOT / path).is_file(), path  # the module named is real
    decision = govern_patch_promotion(_patch(path), _passing_report(), autonomous_low_risk=False)
    assert decision.decision == BLOCK and any("protected paths" in r for r in decision.reasons)


def test_p0_1_survival_a_docs_patch_is_not_blocked_for_protection() -> None:
    decision = govern_patch_promotion(_patch("docs/x.md"), _passing_report(), autonomous_low_risk=False)
    assert not any("protected paths" in r for r in decision.reasons)


@pytest.mark.parametrize("fragments,path", _JUDGES)
def test_p0_4_revert_check_removing_the_fragment_unprotects_the_module(fragments, path, monkeypatch) -> None:
    monkeypatch.setattr(gate, "PROTECTED_PATH_FRAGMENTS",
                        tuple(f for f in gate.PROTECTED_PATH_FRAGMENTS if f not in fragments))
    assert gate.protected_paths_touched(_patch(path)) == []  # so P0-1 would fail: each fragment is load-bearing


def _contracts() -> dict[str, dict]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {c["name"]: c for c in data["tool"]["importlinter"]["contracts"]}


def test_the_layer_contract_names_every_package_but_mcp_and_the_meta_harness() -> None:
    packages = {f"g6.{p.name}" for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}
    layer = _contracts()["the meta-harness is a layer on top: MCP and CLI consent entry points"]
    assert set(layer["source_modules"]) == packages - {"g6.mcp", "g6.metaharness"}
    assert layer["ignore_imports"] == ["g6.cli -> g6.metaharness.surface"]
    assert "g6.metaharness" in _contracts()["core is the kernel: it imports no component"]["forbidden_modules"]


def _lint(tree: Path) -> subprocess.CompletedProcess:
    # The interpreter's scripts folder: beside python in a virtual environment, its Scripts folder in a Windows
    # installation (CI's setup-python), which is not beside python.exe.
    exe = Path(sysconfig.get_path("scripts")) / ("lint-imports.exe" if os.name == "nt" else "lint-imports")
    env = {**os.environ, "PYTHONPATH": str(tree / "src"), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([str(exe), "--config", str(tree / "pyproject.toml"), "--no-cache"], cwd=tree, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    shutil.copytree(SRC, tmp_path / "src" / "g6", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    return tmp_path


@pytest.mark.parametrize("planted,where", [
    ("import g6.metaharness\n", "debate/planted.py"),          # a component reaching up into the layer
    ("from g6.metaharness import __all__\n", "core/planted.py"),
    ("import g6.research.echa\n", "metaharness/planted.py"),    # the layer reaching into research (A1)
])
def test_p0_2_a_planted_import_breaks_the_contracts(tree: Path, planted: str, where: str) -> None:
    assert _lint(tree).returncode == 0  # survival: the unplanted copy passes
    (tree / "src" / "g6" / where).write_text(planted, encoding="utf-8")
    result = _lint(tree)
    assert result.returncode != 0 and "BROKEN" in result.stdout


def test_p0_3_the_package_is_a_registered_component() -> None:
    assert "metaharness" in get_registry().names()
