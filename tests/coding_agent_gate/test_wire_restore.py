# SPDX-License-Identifier: Apache-2.0
"""The structural diff scan and the diff evidence item read as they did before the shared diff reader (the
wire-parsers repair, stage 1): an ordinary hunk of indented Python is no shortcut, the skill's own example still finds
both shortcuts, and a diff item needs an ``@@`` hunk, not hunk counts that match its lines."""
from __future__ import annotations

from g6.coding_agent_gate import Evidence, scan_diff

INDENTED = ("--- a/src/calc.py\n+++ b/src/calc.py\n@@ -10,3 +10,3 @@ def add(a, b):\n"
            "     if a is None:\n-        return b\n+        return 0 + b\n     return a + b\n")


def test_an_ordinary_indented_python_hunk_is_no_shortcut():
    assert scan_diff(INDENTED) == []


def test_the_skill_example_finds_both_shortcuts():
    assert scan_diff("@@ -1,2 +1,2 @@\n-    assert total == 3\n+    assert True\n") == [
        "assertion removed", "tautological assertion"]


def test_a_diff_item_whose_hunk_counts_differ_from_its_lines_has_no_problem():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n one\n"
    assert Evidence("diff", "patch", output=diff).problem() == ""
