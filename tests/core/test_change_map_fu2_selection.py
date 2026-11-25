# SPDX-License-Identifier: Apache-2.0
"""An explicit filename bypasses python_files, retaining body naming rules."""
from g6.harness import change_map
from tests.harness.test_change_map import change, target


def test_explicit_nonmatching_filename_keeps_function_rules():
    api = "def changed(): return 1\ndef neighbour(): return changed()\n"
    before = {"api.py": api, "checks.py": "from api import neighbour\ndef test_call(): neighbour()\ndef helper(): neighbour()\n"}
    after = {**before, "api.py": api.replace("return 1", "return 2")}
    positive = change_map.build(change(before, after), before, after, stated_checks=("checks.py::test_call",))
    negative = change_map.build(change(before, after), before, after, stated_checks=("checks.py::helper",))
    assert target(positive, "neighbour")["exercised"] is True
    assert target(negative, "neighbour")["exercised"] == "uncertain"
