# SPDX-License-Identifier: Apache-2.0
"""The launch review's I6 reads the import statements the patch adds by parsing each changed Python file as it is
now: an import statement whose lines meet the lines the change added or rewrote (``repo_tests.changed_lines``) names
its modules, whatever its layout; text that only looks like one (in a string) names nothing; an import the patch did
not touch names nothing (the environment lacks that dependency, as before)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import patch_review as pr
from g6.workforce import repo_tests as rt

MISSING = "g6_absent_package"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args], check=True, capture_output=True)


def _asked(tmp_path: Path, monkeypatch, before: dict[str, str], after: dict[str, str],
           missing: tuple[str, ...] = (MISSING,)) -> list[str]:
    for rel, text in before.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    for rel, text in after.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    monkeypatch.setattr(rt, "read_report", lambda ran: SimpleNamespace(
        valid=True, data={"missing_modules": list(missing)}))
    return pr._patch_imports_missing(rt.change(tmp_path), [{}])


def test_a_name_added_inside_an_import_it_spans_names_the_module(tmp_path, monkeypatch):
    before = {"calc.py": f"from {MISSING} import (\n    one,\n)\n"}
    after = {"calc.py": f"from {MISSING} import (\n    one,\n    two,\n)\n"}
    assert _asked(tmp_path, monkeypatch, before, after) == [MISSING]


def test_an_import_moved_between_changed_files_is_still_named(tmp_path, monkeypatch):
    """It stands on a line the patch adds (the pooled before/after subtraction the branch tried dropped it)."""
    before = {"a.py": f"import {MISSING}\nx = 1\n", "b.py": "y = 2\n"}
    after = {"a.py": "x = 1\n", "b.py": f"import {MISSING}\ny = 2\n"}
    assert _asked(tmp_path, monkeypatch, before, after) == [MISSING]


def test_an_added_import_in_a_test_file_names_its_module(tmp_path, monkeypatch):
    before = {"test_calc.py": "def test_x():\n    assert True\n"}
    after = {"test_calc.py": f"import {MISSING}\n\n\ndef test_x():\n    assert True\n"}
    assert _asked(tmp_path, monkeypatch, before, after) == [MISSING]


def test_an_import_written_inside_a_string_names_nothing(tmp_path, monkeypatch):
    before = {"calc.py": "X = 1\n"}
    after = {"calc.py": f'X = 1\nDOC = """\nimport {MISSING}\n"""\n'}
    assert _asked(tmp_path, monkeypatch, before, after) == []


def test_an_import_the_patch_did_not_touch_names_nothing(tmp_path, monkeypatch):
    before = {"calc.py": f"import {MISSING}\n\n\ndef add(a, b):\n    return a - b\n"}
    after = {"calc.py": f"import {MISSING}\n\n\ndef add(a, b):\n    return a + b\n"}
    assert _asked(tmp_path, monkeypatch, before, after) == []


@pytest.mark.parametrize(("line", "reported"), [
    (f"import {MISSING}", MISSING),
    (f"from {MISSING}.sub import thing", MISSING),
    (f"import os, {MISSING}.sub as sub", f"{MISSING}.sub"),
    (f"def f():\n    import {MISSING}", MISSING),
])
def test_an_added_import_names_its_module(tmp_path, monkeypatch, line, reported):
    assert _asked(tmp_path, monkeypatch, {"calc.py": "X = 1\n"}, {"calc.py": f"{line}\nX = 1\n"},
                  missing=(reported,)) == [reported]


def test_a_relative_import_names_nothing(tmp_path, monkeypatch):
    (tmp_path / "pkg").mkdir()
    before = {"pkg/__init__.py": "", "pkg/calc.py": "X = 1\n"}
    after = {"pkg/calc.py": f"from .{MISSING} import thing\nX = 1\n"}
    assert _asked(tmp_path, monkeypatch, before, after) == []


def test_an_added_file_that_does_not_parse_names_nothing(tmp_path, monkeypatch):
    """It cannot be imported, so its imports never ran: no missing module came from it."""
    after = {"calc.py": "X = 1\n", "new.py": f"import {MISSING}\ndef broken(:\n"}
    assert _asked(tmp_path, monkeypatch, {"calc.py": "X = 1\n"}, after) == []
