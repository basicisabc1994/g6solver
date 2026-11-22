# SPDX-License-Identifier: Apache-2.0
"""Bundled upstream examples are data; the conformance kit still checks their licence."""
from pathlib import Path

import conformance_kit as kit


def test_examples_are_not_g6_runtime_but_their_licence_is_checked():
    root = Path(__file__).resolve().parents[2]
    vendor = root / "src/g6/skills/vendor/langextract"
    target = kit.shared_target("skills")
    assert (vendor / "langextract-usage/examples/basic_extraction.py").is_file()
    assert root / "src/g6/skills/__init__.py" in target.files
    assert not [p for p in target.files if p.is_relative_to(root / "src/g6/skills/vendor")]
    assert kit.g8_vendored(target) == []


def test_missing_vendor_licence_is_still_a_problem(tmp_path):
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "vendor/langextract/langextract-usage").mkdir(parents=True)
    target = kit.Target("g6.skills", "g6.skills", tmp_path, (), "skills", "builtin")
    assert any(p.message == "vendored langextract carries no LICENSE file" for p in kit.g8_vendored(target))
