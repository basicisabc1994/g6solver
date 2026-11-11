"""A bound copy must cover unchanged files too, including called dependencies."""
from contextlib import contextmanager
import sys

import pytest

from g6.core.ledger import digest
from g6.harness import patch_review
from g6.workforce import repo_tests
from .helpers import REQUIREMENTS, TASK, repo, result, standard, stated


def test_unchanged_dependency_substituted_only_while_copying_is_not_counted(tmp_path, monkeypatch):
    bad = "def add(a, b):\n    return a - b\n"
    good = "def add(a, b):\n    return a + b\n"
    root = repo(tmp_path, extra={"calc.py": "from helper import add\n", "helper.py": bad})
    (root / "calc.py").write_text("from helper import add\n\n# patch\n", encoding="utf-8")
    before = repo_tests.snapshot(root)
    stated(monkeypatch)
    real_copy = repo_tests.repository_copy
    observed = []

    @contextmanager
    def substituted(*args, **kwargs):
        (root / "helper.py").write_text(good, encoding="utf-8")
        try:
            with real_copy(*args, **kwargs) as copy:
                (root / "helper.py").write_text(bad, encoding="utf-8")
                yield copy
        finally:
            (root / "helper.py").write_text(bad, encoding="utf-8")

    def run_copy(copy, ids, **kwargs):
        with monkeypatch.context() as local:
            local.chdir(copy)
            local.syspath_prepend(str(copy))
            for module in ("calc", "helper"):
                local.delitem(sys.modules, module, raising=False)
            compiled = {}
            exec(compile((copy / ids[0]).read_text(), "<G6 fixed template>", "exec"), compiled)
            try:
                compiled["test_example"]()
                status = "passed"
            except pytest.fail.Exception:
                status = "failed"
            observed.append(((copy / "helper.py").read_text(), status))
            for module in ("calc", "helper"):
                del sys.modules[module]
        return {**result(ids[0] + "::test_example", status),
                "source_before": digest(repo_tests._copy_snapshot(copy))}

    monkeypatch.setattr(repo_tests, "repository_copy", substituted)
    monkeypatch.setattr(repo_tests, "_run", run_copy)
    monkeypatch.setattr(repo_tests, "run_at_base", lambda change, ids, **k:
                        result(ids[0] + "::test_example", "failed"))
    got = patch_review.review_patch(root, task=TASK, requirements=REQUIREMENTS, confirmed=standard())
    if "assessment" not in got:
        assert got.get("ok") is False
        return
    assert repo_tests.snapshot(root) == before
    assert (root / "helper.py").read_text() == bad
    [statement] = got["assessment"]["confirmed_expectations"]["evidence"]["expectations"]
    assert statement["checked"] is False, "issuer counted a passing dependency substituted only in the copy"
