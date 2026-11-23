# SPDX-License-Identifier: Apache-2.0
"""Tier 0 development fixtures: static leads have no assessment authority."""
from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import pytest

from g6.harness import change_map, patch_review
from g6.workforce.repo_tests import Change, GitName


BAR = """class ProgressBar:
    def __iter__(self):
        return iter(())

    def update(self, n):
        self._completed = n
"""
PATCHED_BAR = BAR.replace("return iter(())", "return iter(()) if self._completed else iter((1,))")
VISITOR = """class Visitor:
    def visit_FunctionDef(self, node):
        return node

    def visit_AsyncFunctionDef(self, node):
        return node
"""


def change(before, after, root=Path(".")):
    files = tuple(sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p)))
    return Change(root, "HEAD", "a" * 40, "b" * 40, files,
                  {p: before[p] for p in files if p in before},
                  {p: after[p] for p in files if p in after},
                  tuple(p for p in files if p.endswith(".py") and not p.startswith("tests/")), ())


def mapped(before, after, **kwargs):
    return change_map.build(change(before, after), before, after, **kwargs)


def target(record, symbol):
    return next(t for t in record["targets"] if t["kind"] == "sibling" and t["symbol"] == symbol)


def test_shared_state_writer_is_ranked_and_named_not_checked():
    before, after = {"bar.py": BAR}, {"bar.py": PATCHED_BAR}
    symbols = change_map.changed_symbols(change(before, after), before, after)
    assert [s["symbol"] for s in symbols] == ["ProgressBar.__iter__"]
    record = mapped(before, after)
    update = target(record, "ProgressBar.update")
    assert update["rank"] == 20
    assert update["exercised"] is False
    assert update["attributes"] == ["_completed"]
    assert any("ProgressBar.update" in s["check"] and "writes the state this patch reads" in s["reason"]
               and "no check calls it" in s["reason"] for s in record["skipped"])
    assert set(record) == {"targets", "instances", "cases", "gates", "skipped"}
    assert record["instances"] == record["cases"] == record["gates"] == []


def test_visitor_lists_handlers_not_added():
    after = VISITOR + "\n    def visit_ClassDef(self, node):\n        return node\n"
    record = mapped({"visitor.py": VISITOR}, {"visitor.py": after})
    assert target(record, "Visitor.visit_FunctionDef")["rank"] == 30
    assert target(record, "Visitor.visit_AsyncFunctionDef")["rank"] == 30
    points = [t for t in record["targets"] if t["kind"] == "decision_point"]
    assert any(t["decision"] == "handler" and t["symbol"] == "Visitor.visit_ClassDef" for t in points)
    assert not any(t["kind"] == "sibling" and t["symbol"] == "Visitor.visit_ClassDef" for t in record["targets"])


def test_startswith_keeps_separator_literal_and_multiline_location():
    before = {"paths.py": "def matches(x, prefix):\n    return x == prefix\n"}
    after = {"paths.py": 'def matches(x, prefix):\n    return x.startswith(\n        prefix + "/"\n    )\n'}
    points = change_map.decision_points(change(before, after), before, after)
    assert any(p["decision"] == "startswith" and p["literals"] == ['"/"'] and p["line"] == 2 for p in points)


def test_alias_reexport_import_closure_callers_and_subclass():
    source = "def f(x):\n    return x + 1\nalias = f\nclass Base:\n    def f(self, x):\n        return x + 1\n"
    before = {"pkg/core.py": source, "pkg/__init__.py": "from .core import alias as public\n",
              "pkg/api.py": "from . import public\ndef caller(x):\n    return public(x)\n",
              "pkg/sub.py": "from .core import Base\nclass Child(Base):\n    def f(self, x):\n        return x\n"}
    after = {**before, "pkg/core.py": source.replace("x + 1", "x + 2")}
    record = mapped(before, after)
    assert target(record, "alias")["binding"] == "pkg.core.alias"
    assert target(record, "public")["binding"] == "pkg.public"
    assert target(record, "caller")["rank"] == 10
    assert target(record, "Child.f")["rank"] == 40


def test_same_helper_sibling_and_callers_rank_before_family():
    before = {"bar.py": BAR.replace("return iter(())", "return self.step()") +
              "\n    def other(self):\n        return self.step()\n\ndef caller(bar):\n    return ProgressBar.__iter__(bar)\n"}
    after = {"bar.py": before["bar.py"].replace("return self.step()", "return self.step() + 1", 1)}
    record = mapped(before, after)
    assert target(record, "caller")["rank"] == 10
    assert target(record, "ProgressBar.other")["rank"] == 20


@pytest.mark.parametrize("channel", ["stated", "changed", "proposed"])
def test_exercised_by_name_from_each_check_source(channel):
    before, after = {"bar.py": BAR}, {"bar.py": PATCHED_BAR}
    source = "from bar import ProgressBar\ndef test_update():\n    bar = ProgressBar()\n    bar.update(3)\n"
    kwargs = {}
    if channel == "stated":
        before["tests/test_bar.py"] = after["tests/test_bar.py"] = source
        kwargs["stated_checks"] = ["tests/test_bar.py::test_update"]
    elif channel == "changed":
        after["tests/test_bar.py"] = source
    else:
        kwargs["proposed_checks"] = [{"file": "tests/test_proposed.py", "source": source, "test": "test_update"}]
    record = mapped(before, after, **kwargs)
    assert target(record, "ProgressBar.update")["exercised"] is True
    assert not any("ProgressBar.update" in s["check"] for s in record["skipped"])


def test_unselected_test_and_unresolved_receiver_do_not_claim_exercised():
    checks = """from bar import ProgressBar
def test_selected():
    obj.update(1)
def test_other():
    ProgressBar().update(2)
"""
    before = {"bar.py": BAR, "tests/test_bar.py": checks}
    after = {**before, "bar.py": PATCHED_BAR}
    record = mapped(before, after, stated_checks=["tests/test_bar.py::test_selected"])
    assert target(record, "ProgressBar.update")["exercised"] is False
    assert any("static" in s["reason"].lower() and "dynamic" in s["reason"].lower() for s in record["skipped"])


def test_changed_return_is_a_decision_without_siblings():
    record = mapped({"a.py": "def f():\n    return 1\n"}, {"a.py": "def f():\n    return 2\n"})
    assert not any(p["kind"] == "sibling" for p in record["targets"])
    assert any(p.get("decision") == "return" and p["literals"] == ["2"] for p in record["targets"])


def test_formatting_does_not_change_symbol_body():
    before = {"a.py": "def f():\n    return 1\n"}
    after = {"a.py": "def f():\n    # comment\n    return (1)\n"}
    assert change_map.changed_symbols(change(before, after), before, after) == []


def test_deleted_symbol_retains_callers():
    before = {"a.py": "def f():\n    return 1\ndef caller():\n    return f()\n"}
    after = {"a.py": "def caller():\n    return f()\n"}
    assert target(mapped(before, after), "caller")["rank"] == 10


def test_comparisons_truthiness_membership_and_literals_only_in_added_lines():
    before = {"a.py": "def f(x):\n    return x == 10\n"}
    after = {"a.py": "def f(x):\n    if x and isinstance(x, str):\n        return x in ('a', 'b')\n    return x == 10\n"}
    points = change_map.decision_points(change(before, after), before, after)
    assert {p["decision"] for p in points} >= {"truthiness", "isinstance", "membership"}
    assert not any("10" in p["literals"] for p in points)


def test_token_family_lists_other_observed_and_declared_members():
    before = {"tokens.py": "class Token:\n    END = 0\n    NAME = 1\n    NUMBER = 2\n\ndef read(x):\n    return x\n"}
    after = {"tokens.py": before["tokens.py"].replace("return x", "return x == Token.END")}
    record = mapped(before, after)
    assert target(record, "Token.NAME")["rank"] == 30
    assert target(record, "Token.NUMBER")["rank"] == 30


def test_module_and_class_level_decisions_and_signed_numbers():
    before = {"a.py": "x = 0\nclass C:\n    flag = True\n"}
    after = {"a.py": 'x = 0\nif x.startswith("/"):\n    x = 1\nclass C:\n    flag = -2 < x\n'}
    points = change_map.decision_points(change(before, after), before, after)
    assert any(p["decision"] == "startswith" and '"/"' in p["literals"] for p in points)
    assert any(p["symbol"] == "C" and "-2" in p["literals"] for p in points)


def test_added_lambda_and_default_expressions_keep_decisions():
    before = {"a.py": "def f(x=0):\n    return x\n"}
    after = {"a.py": "def f(x=(1 < 2)):\n    return lambda y: y > 3\n"}
    points = change_map.decision_points(change(before, after), before, after)
    assert {tuple(p["literals"]) for p in points if p["decision"] == "comparison"} == {("1", "2"), ("3",)}


def test_parameter_shadowing_does_not_invent_a_caller():
    before = {"a.py": "def f():\n    return 1\ndef caller(f):\n    return f()\n"}
    after = {"a.py": before["a.py"].replace("return 1", "return 2")}
    record = mapped(before, after)
    assert not any(p["kind"] == "sibling" for p in record["targets"])
    assert any(p.get("decision") == "return" and p["literals"] == ["2"] for p in record["targets"])


def test_rebinding_an_import_does_not_invent_a_caller():
    before = {"a.py": "def f():\n    return 1\n", "b.py": "from a import f\nf = 0\ndef caller():\n    return f()\n"}
    after = {**before, "a.py": "def f():\n    return 2\n"}
    record = mapped(before, after)
    assert not any(p["kind"] == "sibling" for p in record["targets"])
    assert any(p.get("decision") == "return" and p["literals"] == ["2"] for p in record["targets"])


def test_invalid_proposed_check_has_a_named_skip():
    record = mapped({"bar.py": BAR}, {"bar.py": PATCHED_BAR},
                    proposed_checks=[{"file": "test_bad.py", "source": "def broken(:", "test": "broken"}])
    assert target(record, "ProgressBar.update")["exercised"] is False
    assert any("test_bad.py" in s["check"] and "syntax" in s["reason"] for s in record["skipped"])


def test_body_changes_in_tests_do_not_become_production_targets():
    record = mapped({"tests/test_a.py": "def test_a():\n    assert 1\n"},
                    {"tests/test_a.py": "def test_a():\n    assert 2 == 2\n"})
    assert record["targets"] == []


def test_lowercase_enum_members_form_a_family():
    before = {"a.py": "from enum import Enum\nclass Kind(Enum):\n    one = 1\n    two = 2\ndef f(x):\n    return x\n"}
    after = {"a.py": before["a.py"].replace("return x", "return x == Kind.one")}
    assert target(mapped(before, after), "Kind.two")["rank"] == 30


def test_invalid_syntax_and_limits_are_named_skips():
    record = mapped({"a.py": "def f():\n    return 1\n"}, {"a.py": "def broken(:\n"})
    assert any("syntax" in s["reason"].lower() for s in record["skipped"])
    record = mapped({}, {"a.py": "#" * (change_map.MAX_FILE_CHARS + 1)})
    assert any("limit" in s["reason"] for s in record["skipped"])


@pytest.mark.parametrize("source, reason", [
    ("def broken(:\n", "syntax unavailable"),
    ("#" * (change_map.MAX_FILE_CHARS + 1), "static analysis text limit reached"),
], ids=["syntax", "limit"])
def test_git_listed_name_in_analysis_skip_uses_the_display_rule(source, reason):
    rel = GitName(b'bad"name\n\xff.py')
    record = mapped({}, {rel: source})
    assert {"tier": 0, "check": 'change map: patch "bad\\"name\\n\\377.py"', "reason": reason} in record["skipped"]


def test_git_listed_module_parts_are_quoted_before_becoming_binding_text():
    rel = GitName(b'src/odd\n\xff/bar.py')
    record = mapped({rel: BAR}, {rel: PATCHED_BAR})
    update = target(record, "ProgressBar.update")
    assert update["file"] == rel
    assert update["binding"] == '"odd\\n\\377".bar.ProgressBar.update'
    assert all("\n" not in item["binding"] and "\udcff" not in item["binding"] for item in record["targets"])


@pytest.mark.parametrize("folder, listed, selected", [
    (b"tests", b"tests/test_bar.py", True),
    (b"tests///", b"tests/nested/test_bar.py", True),
    (b"tests", b"tests_extra/test_bar.py", False),
    (b"tests", b"Tests/test_bar.py", False),
    (b"tests", b"tests\\nested/test_bar.py", False),
    (b"tests\\nested/", b"tests\\nested/test_bar.py", True),
    (b"tests\xff/", b"tests\xff/test_bar.py", True),
    (b"tests\xff", b"tests\xfe/test_bar.py", False),
    (b".", b"tests/test_bar.py", False),
    (b"/", b"tests/test_bar.py", False),
    (b"", b"tests/test_bar.py", False),
    (b"tests//nested", b"tests/nested/test_bar.py", False),
], ids=["child", "trailing-slashes", "sibling-prefix", "case", "literal-backslash", "backslash-folder",
        "odd-byte", "different-byte", "dot", "root", "empty", "interior-slashes"])
def test_git_listed_folder_selection_preserves_exact_path_parts(folder, listed, selected):
    rel = GitName(listed)
    checks = "from bar import ProgressBar\ndef test_update():\n    ProgressBar().update(1)\n"
    before = {"bar.py": BAR, rel: checks}
    record = mapped(before, {**before, "bar.py": PATCHED_BAR}, stated_checks=[GitName(folder)])
    update = target(record, "ProgressBar.update")
    assert update["exercised"] is selected
    assert update["checks"] == ([rel] if selected else [])


def test_map_never_imports_repository_code(tmp_path, monkeypatch):
    name = "change_map_import_trap"
    text = 'raise RuntimeError("repository code imported")\n' + BAR
    (tmp_path / f"{name}.py").write_text(text, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    record = mapped({f"{name}.py": text}, {f"{name}.py": text.replace(BAR, PATCHED_BAR)})
    assert target(record, "ProgressBar.update")
    assert name not in sys.modules


@pytest.mark.parametrize("stage", ["success", "error", "deadline"])
@pytest.mark.usefixtures("declared_mcp_grants")
def test_review_decided_fields_byte_identical_with_and_without_map(tmp_path, monkeypatch, stage):
    """Exercise the real review path using recorded runs and a temporary repository supplied by the lead."""
    from tests.harness.test_patch_review import BAD, GOOD, SUM, TEST, Recorded, _git, result

    (tmp_path / "calc.py").write_text(BAD)
    (tmp_path / "test_calc.py").write_text(TEST)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    (tmp_path / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, default=result(["test_calc.py::test_add", "test_calc.py::test_negative"]),
             at_base=result(failed=["test_calc.py::test_add", "test_calc.py::test_negative"]))
    real = patch_review._change_neighbourhood
    monkeypatch.setattr(patch_review, "_change_neighbourhood", lambda *a, **k: change_map.empty())
    baseline = patch_review.review_patch(tmp_path, task="Repair addition", requirements=SUM)
    if stage == "success":
        record = mapped({"bar.py": BAR}, {"bar.py": PATCHED_BAR})
        monkeypatch.setattr(patch_review, "_change_neighbourhood", lambda *a, **k: copy.deepcopy(record))
    elif stage == "error":
        def failed(*a, **k):
            raise RuntimeError("analysis stopped")
        monkeypatch.setattr(patch_review, "_change_neighbourhood", failed)
    else:
        monkeypatch.setattr(patch_review, "_change_neighbourhood", lambda *a, **k: real(*a, **{**k, "deadline": 0.0}))
    got = patch_review.review_patch(tmp_path, task="Repair addition", requirements=SUM)
    assert baseline["ok"] and got["ok"]
    keys = ("outcome", "reasons", "requirements", "requirements_without_adequate_check", "summary", "next_actions")
    def decided(reply):
        return json.dumps({k: reply["assessment"][k] for k in keys}, sort_keys=True).encode()
    assert decided(got) == decided(baseline)
    assert got["gate"] == baseline["gate"]
    record = got["assessment"]["completion"]["neighbourhood"]
    assert record["skipped"]
    assert any(a["result"] == "skipped" and a["tier"] == 0 for a in got["assessment"]["completion"]["activities"])


def test_reader_does_nothing_after_deadline(monkeypatch):
    monkeypatch.setattr(patch_review.repo_tests, "_git", lambda *a, **k: pytest.fail("read after deadline"))
    record = patch_review._change_neighbourhood(change({}, {}), (), (), (), deadline=time.monotonic() - 1)
    assert record["targets"] == []
    assert any("budget" in s["reason"] for s in record["skipped"])


def test_reader_supplies_both_trees_and_stated_checks(tmp_path, monkeypatch):
    checks = "from bar import ProgressBar\ndef test_update():\n    ProgressBar().update(1)\n"
    (tmp_path / "bar.py").write_text(PATCHED_BAR, encoding="utf-8")
    (tmp_path / "test_bar.py").write_text(checks, encoding="utf-8")
    rt = patch_review.repo_tests
    monkeypatch.setattr(rt, "_git", lambda *a, **k: b"bar.py\0test_bar.py\0")
    monkeypatch.setattr(rt, "_listed_at", lambda *a, **k: {
        "bar.py": ("100644", "blob", "a" * 40, str(len(BAR))),
        "test_bar.py": ("100644", "blob", "b" * 40, str(len(checks)))})
    monkeypatch.setattr(rt, "blob_bytes", lambda repo, key: (BAR if key == "a" * 40 else checks).encode())
    delta = change({"bar.py": BAR}, {"bar.py": PATCHED_BAR}, tmp_path)
    record = patch_review._change_neighbourhood(delta, ["test_bar.py::test_update"], (), (), deadline=time.monotonic() + 60)
    assert target(record, "ProgressBar.update")["exercised"] is True


def test_reader_does_not_read_oversize_base_as_a_new_function(tmp_path, monkeypatch):
    (tmp_path / "bar.py").write_text(PATCHED_BAR, encoding="utf-8")
    rt = patch_review.repo_tests
    monkeypatch.setattr(rt, "_git", lambda *a, **k: b"bar.py\0")
    monkeypatch.setattr(rt, "_listed_at", lambda *a, **k: {
        "bar.py": ("100644", "blob", "a" * 40, str(change_map.MAX_FILE_CHARS + 1))})
    monkeypatch.setattr(rt, "blob_bytes", lambda *a, **k: pytest.fail("oversize blob read"))
    delta = change({"bar.py": BAR}, {"bar.py": PATCHED_BAR}, tmp_path)
    record = patch_review._change_neighbourhood(delta, (), (), (), deadline=time.monotonic() + 60)
    assert record["targets"] == []
    assert any("limit" in s["reason"] for s in record["skipped"])
