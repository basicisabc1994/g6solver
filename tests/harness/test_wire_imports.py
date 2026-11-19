# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace

from g6.harness import patch_review as pr


def missing(monkeypatch, before, after):
    monkeypatch.setattr(pr.repo_tests, "related_texts", lambda change, related: (before, after))
    monkeypatch.setattr(pr.repo_tests, "diff_text", lambda change: "")
    monkeypatch.setattr(pr.repo_tests, "read_report", lambda ran: SimpleNamespace(
        valid=True, data={"missing_modules": ["new_package", "existing"]}))
    change = SimpleNamespace(files=("module.py",))
    return pr._patch_imports_missing(change, [{}])


def test_added_function_local_multiline_import(monkeypatch):
    assert missing(monkeypatch, {"module.py": "pass\n"}, {"module.py":
        "def f():\n    from new_package import (\n        value,\n    )\n"}) == ["new_package"]


def test_removed_then_readded_import_is_not_new(monkeypatch):
    assert missing(monkeypatch, {"module.py": "import existing\n"}, {"module.py":
        "def f():\n    import existing\n"}) == []
