# SPDX-License-Identifier: Apache-2.0
"""The weekly closures check (``tools/gen_extra_closures.py --check``) fails only on requirement drift or on a licence
the conformance kit does not allow or cannot place, and reports version drift as information. Offline: a fake
resolution stands in for uv and PyPI."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "gen_extra_closures.py"
_spec = importlib.util.spec_from_file_location("gen_extra_closures", _TOOL)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

MIT = {"license_expression": "MIT", "license": "", "classifiers": []}
COMMITTED = {
    "core": {"requires": ["pydantic>=2.6"], "closure": ["pydantic==2.6.0"]},
    "extras": {
        "formal": {"requires": ["z3-solver>=4.12"], "closure": ["pydantic==2.6.0", "z3-solver==4.12.0"]},
        "lora": {"requires": ["torch>=2.2"], "closure": ["nvidia-cublas-cu12==12.1.0", "torch==2.2.0"]},
    },
    "packages": {"pydantic==2.6.0": MIT, "z3-solver==4.12.0": MIT, "torch==2.2.0": MIT,
                 "nvidia-cublas-cu12==12.1.0": {"license_expression": "", "license": "", "classifiers": []}},
}


def _moved() -> dict:
    """Today's resolution: the same requirements, newer versions, all allowed."""
    fresh = copy.deepcopy(COMMITTED)
    fresh["core"]["closure"] = ["pydantic==2.7.0"]
    fresh["extras"]["formal"]["closure"] = ["pydantic==2.7.0", "z3-solver==4.13.0"]
    fresh["packages"] = {**fresh["packages"], "pydantic==2.7.0": MIT, "z3-solver==4.13.0": MIT}
    for gone in ("pydantic==2.6.0", "z3-solver==4.12.0"):
        del fresh["packages"][gone]
    return fresh


def _check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh: dict) -> int:
    committed = tmp_path / "extra_closures.json"
    committed.write_text(gen.render(COMMITTED), encoding="utf-8")
    monkeypatch.setattr(gen, "OSS", tmp_path)
    monkeypatch.setattr(gen, "OUT", committed)
    monkeypatch.setattr(gen, "build", lambda: fresh)
    return gen.main(["--check"])


def test_version_drift_is_reported_and_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    failures, drift = gen.classify(COMMITTED, _moved())
    assert failures == [] and any("pydantic==2.7.0" in line for line in drift)
    assert _check(monkeypatch, tmp_path, _moved()) == 0
    said = capsys.readouterr().out
    assert "versions moved" in said and "z3-solver==4.13.0" in said and "regenerate it when convenient" in said


def test_an_unchanged_resolution_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    assert _check(monkeypatch, tmp_path, copy.deepcopy(COMMITTED)) == 0
    assert "the file is current" in capsys.readouterr().out


def test_requirement_drift_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    fresh = _moved()
    fresh["extras"]["formal"]["requires"] = ["z3-solver>=4.13"]  # edited in pyproject.toml
    fresh["extras"]["cas"] = {"requires": ["sympy>=1.14"], "closure": ["sympy==1.14.0"]}  # added
    fresh["packages"]["sympy==1.14.0"] = MIT
    del fresh["extras"]["lora"]  # removed
    failures, _ = gen.classify(COMMITTED, fresh)
    assert [f.split(":", 1)[0] for f in failures] == ["cas", "formal", "lora"], failures
    assert "formal: requirements ['z3-solver>=4.12'] -> ['z3-solver>=4.13']" in failures[1]
    assert _check(monkeypatch, tmp_path, fresh) == 1
    assert "needs attention" in capsys.readouterr().out


@pytest.mark.parametrize(("pin", "meta", "why"), [
    ("mystery-solver==1.0.0", {"license_expression": "GPL-3.0-only"}, "banned"),
    ("mystery-solver==1.0.0", {"license_expression": "", "license": "", "classifiers": []}, "no licence metadata"),
    ("mystery-solver==1.0.0", {"license_expression": "LicenseRef-Vendor-Proprietary"}, "unknown"),
    ("litellm==1.0.0", MIT, "a banned package"),
])
def test_a_licence_not_allowed_or_not_known_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pin: str,
                                                  meta: dict, why: str) -> None:
    fresh = _moved()
    fresh["extras"]["formal"]["closure"] = ["pydantic==2.7.0", pin]
    fresh["packages"][pin] = meta
    failures, _ = gen.classify(COMMITTED, fresh)
    assert len(failures) == 1 and failures[0].startswith(f"licence: formal: {pin}") and why in failures[0], failures
    assert _check(monkeypatch, tmp_path, fresh) == 1


def test_a_licence_the_kit_waives_does_not_fail() -> None:
    fresh = _moved()  # lora's nvidia wheel has no licence metadata; waivers.toml names ": nvidia-" (torch's CUDA wheels)
    fresh["extras"]["lora"]["closure"] = ["nvidia-cublas-cu12==12.4.0", "torch==2.2.0"]
    fresh["packages"]["nvidia-cublas-cu12==12.4.0"] = {"license_expression": "", "license": "", "classifiers": []}
    assert gen.classify(COMMITTED, fresh)[0] == []
