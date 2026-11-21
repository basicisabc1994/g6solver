# SPDX-License-Identifier: Apache-2.0
"""The launch review's I6 asks about a module the run could not import that the patch imports on a line it adds. An
import the patch moves from one changed file to another is still on an added line, so it is still named (the
wire-parsers repair, stage 1: the pooled before/after subtraction dropped it)."""
from __future__ import annotations

from types import SimpleNamespace

from g6.harness import patch_review as pr

DIFF = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1 @@\n-import new_package\n x = 1\n"
        "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1,2 @@\n+import new_package\n y = 2\n")
BEFORE = {"a.py": "import new_package\nx = 1\n", "b.py": "y = 2\n"}
AFTER = {"a.py": "x = 1\n", "b.py": "import new_package\ny = 2\n"}


def test_an_import_moved_between_changed_files_is_still_named(monkeypatch):
    monkeypatch.setattr(pr.repo_tests, "diff_text", lambda change: DIFF)
    monkeypatch.setattr(pr.repo_tests, "related_texts", lambda change, related=None: (BEFORE, AFTER))
    monkeypatch.setattr(pr.repo_tests, "read_report", lambda ran: SimpleNamespace(
        valid=True, data={"missing_modules": ["new_package"]}))
    change = SimpleNamespace(files=("a.py", "b.py"))
    assert pr._patch_imports_missing(change, [{}]) == ["new_package"]
