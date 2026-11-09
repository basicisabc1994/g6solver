"""A later caller must not redirect a confirmed entry point through import_roots."""
import sys

import pytest

from g6.harness import patch_review
from g6.harness import verification_search as vs
from g6.mcp import behaviour
from .helpers import REQUIREMENTS, TASK, ask, repo, result, stated


def test_later_import_roots_cannot_replace_the_confirmed_repository_module(tmp_path, monkeypatch):
    root = repo(tmp_path, extra={"alternate/calc.py": "def add(a, b):\n    return a - b\n"})
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n\n# patch\n", encoding="utf-8")
    (root / "alternate/calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    assert ask(root)["status"] == "confirmed"
    held = behaviour.enforced(str(root), TASK)
    stated(monkeypatch)

    def run_trusted_fixture(change, placed, source, node, *, at_base, roots=(), **kwargs):
        if at_base:
            # Both base fixtures return -1; this is their recorded failure.
            return result(node + "::test_example", "failed")
        with monkeypatch.context() as local:
            local.chdir(root)
            local.setattr(sys, "path", [*(str(root / rel) for rel in roots), str(root), *sys.path])
            local.delitem(sys.modules, "calc", raising=False)
            compiled = {}
            exec(compile(source, "<G6 fixed template>", "exec"), compiled)
            try:
                compiled["test_example"]()
                status = "passed"
            except pytest.fail.Exception:
                status = "failed"
            # The confirmed runner imports in its child. There is deliberately no
            # host sys.modules entry to collect as diagnostic bookkeeping.
        return result(node + "::test_example", status)

    monkeypatch.setattr(vs, "_run_check", run_trusted_fixture)
    ordinary = patch_review.review_patch(root, task=TASK, requirements=REQUIREMENTS, confirmed=held)
    redirected = patch_review.review_patch(root, task=TASK, requirements=REQUIREMENTS, confirmed=held,
                                            import_roots=["alternate"])
    assert ordinary["assessment"]["outcome"] == patch_review.DEFECT
    if "assessment" not in redirected:
        assert redirected.get("ok") is False
        return
    assert redirected["assessment"]["outcome"] in (patch_review.DEFECT, patch_review.INSUFFICIENT,
                                                    patch_review.INTEGRITY), (
        "caller import_roots redirected the confirmed calc.add and changed defect_detected to "
        + redirected["assessment"]["outcome"])
