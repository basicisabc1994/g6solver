# SPDX-License-Identifier: Apache-2.0
from g6.coding_agent_gate.detectors import scan_diff
from g6.sources.ctx_git import runner, ops
from g6.media.adapt_diagrams.block import _svg_size
from g6.skills import corpus


def test_alias_multiline_skip_is_detected():
    diff = ('--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@ -1,2 +1,6 @@\n'
            '+import pytest as p\n+@p.mark.skipif(\n+    True,\n+)\n def test_x():\n     assert value == 1\n')
    assert scan_diff(diff)


def test_new_tautological_test_is_not_clean_evidence():
    assert scan_diff('--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1,2 @@\n'
                     '+def test_new():\n+    assert True\n')


def test_new_unittest_tautology_is_not_clean_evidence():
    assert scan_diff('--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1,2 @@\n'
                     '+def test_new(self):\n+    self.assertTrue(True)\n')


def test_unnamed_diff_cannot_be_clean_evidence():
    assert scan_diff('@@ -1 +1 @@\n-old\n+new\n')


def test_git_error_code_is_independent_of_language():
    assert runner.classify("fatal: not a git repository") == runner.classify("fatal: kein Git-Repository")


def test_porcelain_v2_status_does_not_read_branch_prose():
    def run(*args):
        assert args[:2] == ("status", "--porcelain=v2")
        return runner.Done(0, (b"# branch.oid abc\0# branch.head main\0# branch.upstream origin/main\0"
                               b"# branch.ab +2 -3\0" b"1 M. N... 100644 100644 100644 abc abc file.py\0"), b"")
    found = ops.status_of(run)
    assert found["branch"] == "main" and found["ahead"] == 2 and found["behind"] == 3


def test_svg_size_uses_xml_attributes():
    assert _svg_size("<svg height='20pt' width='30pt'></svg>") == (30.0, 20.0)


def test_corpus_fields_do_not_come_from_error_wording():
    class Field:
        def is_required(self):
            return False
    assert corpus._fill("unrelated optional word", {"task": "<task>"}, {"task": Field(), "optional": Field()}) == ["task"]
