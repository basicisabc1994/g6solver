# SPDX-License-Identifier: Apache-2.0
"""The library has no trust verdict built from caller-declared numbers (CORE_LABELS-02).

``evaluate_trust`` returned ``sla_class`` "verified" and a confidence from a fixed weighting of numbers the caller
supplied, beside constants that claimed a calibration the library does not hold. Both modules were removed.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import g6
import g6.core

_SRC = Path(g6.__file__).resolve().parent
_GONE_NAMES = ("evaluate_trust", "TrustEnvelope", "theory_constants", "trust_envelope")


@pytest.mark.parametrize("name", ["evaluate_trust", "TrustEnvelope"])
def test_g6_core_no_longer_exports_the_trust_verdict(name: str) -> None:
    # Fails if the "TrustEnvelope" / "evaluate_trust" entries come back in g6.core._LAZY.
    assert name not in g6.core.__all__
    with pytest.raises(AttributeError):
        getattr(g6.core, name)


@pytest.mark.parametrize("module", ["g6.core.trust_envelope", "g6.core.theory_constants"])
def test_the_modules_are_gone(module: str) -> None:
    # Fails if src/g6/core/trust_envelope.py or theory_constants.py is restored.
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_no_library_source_names_them() -> None:
    # Fails if any file under src/g6 mentions the removed names again.
    files = sorted(_SRC.rglob("*.py"))
    assert len(files) > 200, f"walked only {len(files)} files under {_SRC}"
    hits = [
        f"{path.relative_to(_SRC)}: {name}"
        for path in files
        for name in _GONE_NAMES
        if name in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert hits == []
