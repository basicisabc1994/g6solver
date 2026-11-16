# SPDX-License-Identifier: Apache-2.0
"""Caller metadata errors are G6 refusals with relative names and no OS text."""
from pathlib import Path

import pytest

from g6.code import _roots

HERE = Path(__file__).resolve().parent


def test_unavailable_installed_package_is_a_refusal(monkeypatch):
    from g6.code.component_creator import ComponentCreatorBlock, ComponentCreatorInput, conformance
    from g6.code.component_creator.block import COMPONENT

    unavailable = _roots.Roots((HERE / "missing-package.zip" / "pkg",), "installed package")
    monkeypatch.setattr(conformance._roots, "for_folder", lambda *_: unavailable)
    out = ComponentCreatorBlock().infer(ComponentCreatorInput(op="check_conformance", component=COMPONENT)).unwrap()
    assert out["code"] == _roots.OUTSIDE_ROOT

@pytest.mark.parametrize("operation", ["stat", "read"])
def test_diagnostics_postcheck_errors_are_refused(operation, monkeypatch):
    from g6.code.swe_diagnostics import SweDiagnosticsBlock, SweDiagnosticsInput

    roots = _roots.for_folder(HERE, "test source")
    real_existing = _roots.existing

    def fail(*args, **kwargs):
        raise PermissionError("private-system-details")

    def existing(*args, **kwargs):
        path = real_existing(*args, **kwargs)
        monkeypatch.setattr(Path if operation == "stat" else _roots,
                            "stat" if operation == "stat" else "read_text", fail)
        return path

    monkeypatch.setattr(_roots, "existing", existing)
    out = SweDiagnosticsBlock(roots=roots).infer(
        SweDiagnosticsInput(op="code_summarize", path=str(Path(__file__).resolve()))).unwrap()
    assert out["code"] == _roots.PATH_INVALID
    assert "private-system-details" not in repr(out)
    assert str(HERE) not in repr(out)

def test_build_marker_metadata_reason_uses_g6_words(monkeypatch):
    from g6.code.swe_diagnostics import read

    roots = _roots.for_folder(HERE, "test source")

    def fail(*args, **kwargs):
        raise PermissionError("private-system-details")

    monkeypatch.setattr(read, "confined_path", fail)
    found = read.detect_build_env(HERE, roots)
    assert found["unreadable"]
    assert "private-system-details" not in repr(found["unreadable_reasons"])
