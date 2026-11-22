# SPDX-License-Identifier: Apache-2.0
"""A root record exemption cannot hide any nested or otherwise unpinned file."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("bundle", ["langextract", "superpowers"])
@pytest.mark.parametrize("relative", ["nested/VENDORED.md", "nested/LICENSE", "nested/unlisted.txt"])
def test_each_inventory_rejects_unpinned_files(monkeypatch, bundle, relative):
    spec = importlib.util.spec_from_file_location("inventory_checks", ROOT / f"tests/skills/test_vendored_{bundle}.py")
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    extra = checks.VENDOR / relative
    original_rglob, original_is_file = Path.rglob, Path.is_file

    def rglob(folder, pattern):
        yield from original_rglob(folder, pattern)
        if folder == checks.VENDOR and pattern == "*":
            yield extra

    monkeypatch.setattr(Path, "rglob", rglob)
    monkeypatch.setattr(Path, "is_file", lambda path: path == extra or original_is_file(path))
    with pytest.raises(AssertionError):
        checks.test_every_file_is_upstreams_byte_for_byte()
