# SPDX-License-Identifier: Apache-2.0
from g6.core.diff import parse_diff


def test_git_quoted_renamed_file_and_header_shaped_removed_line():
    text = ('diff --git "a/old\\tname.py" "b/new\\tname.py"\n'
            'similarity index 90%\nrename from "old\\tname.py"\nrename to "new\\tname.py"\n'
            '--- "a/old\\tname.py"\n+++ "b/new\\tname.py"\n@@ -1,2 +1 @@\n--- fake.py\n-old\n+new\n')
    [file] = parse_diff(text)
    assert file.names == {"old\tname.py", "new\tname.py"}
    assert len(file.hunks) == 1
    assert file.hunks[0].new_start == 1 and file.hunks[0].new_count == 1
    assert file.text == text


def test_git_octal_utf8_name_and_null_file():
    [file] = parse_diff('--- /dev/null\n+++ "b/\\303\\251.py"\n@@ -0,0 +1 @@\n+x\n')
    assert file.names == {"é.py"}
    assert file.old_name == "" and file.new_name == "é.py"


def test_rename_without_hunks_still_names_both_paths():
    [file] = parse_diff('diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n')
    assert file.names == {"old.py", "new.py"}


def test_mode_only_git_header_names_quoted_file():
    [file] = parse_diff('diff --git "a/with\\tname.py" "b/with\\tname.py"\nold mode 100644\nnew mode 100755\n')
    assert file.names == {"with\tname.py"}


def test_hunk_counts_cannot_hide_truncated_evidence():
    [file] = parse_diff('--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n one\n')
    assert not file.valid


def test_extra_added_line_cannot_hide_after_hunk():
    [file] = parse_diff('--- a/test_x.py\n+++ b/test_x.py\n@@ -1 +1 @@\n-old\n+new\n+pytest.skip()\n')
    assert not file.valid


def test_partial_diff_is_not_evidence_when_another_file_is_valid():
    from g6.coding_agent_gate.evidence import Evidence
    diff = ('--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n'
            '--- a/y.py\n+++ b/y.py\n@@ -1,2 +1,2 @@\n one\n')
    assert Evidence("diff", "patch", output=diff).problem()
