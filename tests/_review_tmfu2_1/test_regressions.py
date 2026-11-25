"""Retained parent checks, with every fixture write redirected into this review folder."""
import importlib
import sys

import pytest

from tests._review_tmfu2_1.test_review import tmp_path


MODULES = [
    "tests.harness.test_template_tmfu4_marks",
    "tests.harness.test_template_tmfu2_amendment1",
    *[f"tests._review_tmfu1.test_tmfu1_finding_{i}" for i in (5, 6, 7)],
    *[f"tests._review_tmfu2.test_tmfu2_finding_{i}" for i in (1, 2, 5, 6)],
    *[f"tests._review_tmfu3.test_tmfu3_finding_{i}" for i in (1, 4, 5)],
    *[f"tests._review_tmfu4.test_tmfu4_finding_{i}" for i in (1, 2, 5, 6)],
]

for module_name in MODULES:
    module = importlib.import_module(module_name)
    for name, function in vars(module).items():
        if name.startswith("test_") and callable(function):
            globals()["test_parent_" + module_name.rsplit(".", 1)[1] + "__" + name] = function


@pytest.fixture(autouse=True)
def redirect_parent_disks(tmp_path, monkeypatch):
    for name in ("tmfu1_support", "tmfu2_support", "tmfu3_support", "tmfu4_support"):
        if name in sys.modules:
            monkeypatch.setattr(sys.modules[name], "__file__", __file__)
