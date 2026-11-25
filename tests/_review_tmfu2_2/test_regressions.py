"""Retained regressions with disk fixtures restricted to this review's directory."""
import importlib
import sys

import pytest

from tests._review_tmfu2_2.test_review import tmp_path

MODULES = [
    "tests.harness.test_template_tmfu4_marks",
    "tests.harness.test_template_tmfu2_amendment1",
    "tests.harness.test_template_tmfu2_r1",
    *[f"tests._review_tmfu1.test_tmfu1_finding_{i}" for i in range(1, 8)],
    *[f"tests._review_tmfu2.test_tmfu2_finding_{i}" for i in (1, 2, 5, 6)],
    *[f"tests._review_tmfu3.test_tmfu3_finding_{i}" for i in (1, 4, 5)],
    *[f"tests._review_tmfu4.test_tmfu4_finding_{i}" for i in (1, 2, 5, 6)],
    "tests._review_tmfu2_1.test_review",
    "tests._review_tmfu2_1.test_checks",
]

for module_name in MODULES:
    module = importlib.import_module(module_name)
    for name, function in vars(module).items():
        if name.startswith("test_") and callable(function):
            globals()["test_retained_" + module_name.replace(".", "_") + "__" + name] = function


@pytest.fixture(autouse=True)
def redirect_disks(tmp_path, monkeypatch):
    for name in ("tmfu1_support", "tmfu2_support", "tmfu3_support", "tmfu4_support"):
        if name in sys.modules:
            monkeypatch.setattr(sys.modules[name], "__file__", __file__)
