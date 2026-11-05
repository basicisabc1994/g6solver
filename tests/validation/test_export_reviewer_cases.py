# SPDX-License-Identifier: Apache-2.0
"""The reviewer domain's cases hold the issue, the patch and the parent's code, on bugs already scored, and never a stage 2 bug."""
import json
from pathlib import Path

import pytest

import export_reviewer_cases as ex
import run as we

DIFF = """diff --git a/src/pkg/core.py b/src/pkg/core.py
--- a/src/pkg/core.py
+++ b/src/pkg/core.py
@@ -10,2 +10,3 @@ def f():
     keep
+    added
     keep
@@ -40,1 +41,1 @@ def g():
-    old
+    new
diff --git a/tests/test_core.py b/tests/test_core.py
--- a/tests/test_core.py
+++ b/tests/test_core.py
@@ -1,1 +1,2 @@
 import pkg
+import os
diff --git a/src/pkg/new.py b/src/pkg/new.py
new file mode 100644
--- /dev/null
+++ b/src/pkg/new.py
@@ -0,0 +1,1 @@
+x = 1
"""


def test_no_bug_of_stage_2_is_on_the_list():
    assert not set(ex.BUGS) & {t.id for t in we.load_tasks(we.TASKS_UNSEEN)}


def test_the_list_is_the_twenty_one_bugs_stage_1_scored():
    assert len(ex.BUGS) == len(set(ex.BUGS)) == 21


def test_a_bug_of_stage_2_is_refused_even_if_named():
    assert ex.usable(["black-5634", "urllib3-5347"]) == ["black-5634"]


def test_the_seal_is_three_bugs_chosen_by_hash_and_stays_put():
    sealed = ex.sealed_bugs(ex.BUGS)
    assert len(sealed) == 3 and sealed <= set(ex.BUGS) and sealed == ex.sealed_bugs(tuple(reversed(ex.BUGS)))


def test_only_source_files_of_the_parent_are_read_around_a_change():
    assert list(ex.changed(DIFF)) == ["src/pkg/core.py", "src/pkg/new.py"]
    assert ex.changed(DIFF)["src/pkg/core.py"] == [(10, 11), (40, 40)]


def test_near_changes_share_a_window_and_a_window_stays_inside_the_file():
    assert ex.windows([(10, 11), (40, 40)], total=60, around=20) == [(1, 60)]
    assert ex.windows([(10, 11), (100, 100)], total=200, around=5) == [(5, 16), (95, 105)]


def _git_diff(tmp_path, old: str, new: str, *, quote: bool = True) -> str:
    """A real git patch from ``old`` to ``new`` (two files in ``tmp_path``), as git writes it; git only reads them."""
    import subprocess
    got = subprocess.run(["git", "-c", f"core.quotepath={'true' if quote else 'false'}", "diff", "--no-index", "--", old, new],
                         cwd=tmp_path, capture_output=True, timeout=30)
    assert got.returncode == 1, got.stderr
    return got.stdout.decode("utf-8")


def test_review_3_finding_7_a_path_git_quotes_and_escapes_is_read_as_git_reads_it(tmp_path):
    (tmp_path / "old.py").write_text("value = 1\n", "utf-8")
    (tmp_path / "café.py").write_text("value = 2\n", "utf-8")
    diff = _git_diff(tmp_path, "old.py", "café.py")
    assert '"b/caf\\303\\251.py"' in diff                                     # quoted and escaped in git's own header
    assert ex.changed(diff) == {"café.py": [(1, 1)]}


def test_review_3_finding_7_a_change_to_a_file_git_quotes_is_read_under_its_own_name(tmp_path):
    name = "café.py"
    diff = ('diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n--- "a/caf\\303\\251.py"\n+++ "b/caf\\303\\251.py"\n'
            "@@ -3,2 +3,2 @@ def f():\n keep\n-old\n+new\n")
    assert ex.changed(diff) == {name: [(3, 4)]}


def test_a_renamed_file_is_named_as_gits_reader_names_it_its_new_path():
    diff = ("diff --git a/src/old name.py b/src/new.py\nsimilarity index 80%\nrename from src/old name.py\nrename to src/new.py\n"
            "--- a/src/old name.py\n+++ b/src/new.py\n@@ -5,2 +5,2 @@\n keep\n-old\n+new\n")
    assert ex.changed(diff) == {"src/new.py": [(5, 6)]}


def test_a_path_with_a_space_is_read_whole(tmp_path):
    (tmp_path / "my mod.py").write_text("a = 1\nb = 2\n", "utf-8")
    (tmp_path / "other.py").write_text("a = 1\nb = 3\n", "utf-8")
    diff = _git_diff(tmp_path, "my mod.py", "other.py").replace("b/other.py", "b/my mod.py")
    assert ex.changed(diff) == {"my mod.py": [(1, 2)]}


def test_a_new_file_has_nothing_to_show_and_is_no_error():
    text = ex.context(DIFF, {"src/pkg/core.py": "\n".join(f"line{i}" for i in range(1, 80))})
    assert "src/pkg/core.py" in text and "line11" in text and "new.py" not in text


def test_the_context_is_cut_at_its_limit_and_says_so():
    text = ex.context(DIFF, {"src/pkg/core.py": "\n".join("x" * 200 for _ in range(400))}, limit=1000)
    assert len(text) < 1100 and text.endswith(ex.CUT)


def test_a_case_holds_the_issue_the_patch_and_the_parent_and_ends_by_asking_for_a_verdict():
    text = ex.case_text("Issue #1: it breaks", DIFF, {"src/pkg/core.py": "keep\n" * 30})
    assert text.index("Issue #1") < text.index("diff --git") < text.index("as it stood before")
    assert text.rstrip().endswith(ex.ASK) and "VERDICT: RIGHT" in ex.ASK and "VERDICT: WRONG" in ex.ASK


def test_a_prompt_gives_the_issue_and_not_the_instructions_round_it():
    assert ex.issue_of("You are working in a clone.\n\nIssue #7: bad\n\nbody") == "Issue #7: bad\n\nbody"


def test_two_labels_on_one_patch_keep_neither(tmp_path):
    patch = tmp_path / "a.patch"
    patch.write_text("same", encoding="utf-8")
    assert ex.distinct([("t", patch, True), ("t", patch, False)]) == {}


def test_the_same_patch_on_two_runs_is_one_case(tmp_path):
    one, two = tmp_path / "a.patch", tmp_path / "b.patch"
    one.write_text("same", encoding="utf-8")
    two.write_text("same", encoding="utf-8")
    assert len(ex.distinct([("t", one, True), ("t", two, True)])) == 1


def test_an_empty_patch_or_a_verdict_of_neither_is_no_case(tmp_path):
    empty, good = tmp_path / "e.patch", tmp_path / "g.patch"
    empty.write_text("", encoding="utf-8")
    good.write_text("x", encoding="utf-8")
    assert ex.distinct([("t", empty, True), ("t", good, None), ("t", tmp_path / "gone.patch", True)]) == {}


def test_a_case_row_is_what_coding_replay_loads(tmp_path):
    load_cases = pytest.importorskip("g6.metaharness.coding_replay").load_cases
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(ex.row("black-5634", "abc", "text", True)) + "\n", encoding="utf-8")
    case = load_cases(path)[0]
    assert (case.case_id, case.expected, case.meta["group"]) == ("black-5634:abc", "right", "black-5634")


def test_the_module_never_names_the_oracle():
    source = Path(ex.__file__).read_text(encoding="utf-8")
    assert "fix.patch" not in source and "/oracle" not in source and "g6we-oracle" not in source


# --- review 4, findings 5 and 8: git reads which files a part touches, in its own world, and its names stay as git gives them ----
def _octal_patch(octal: str) -> str:
    return (f'diff --git "a/\\{octal}.py" "b/\\{octal}.py"\n--- "a/\\{octal}.py"\n+++ "b/\\{octal}.py"\n'
            '@@ -1 +1 @@\n-old\n+new\n')


def test_review_4_finding_5_two_names_git_keeps_apart_stay_apart():
    """Bytes that are not UTF-8 in a name (\\376 and \\377, as git writes them quoted): held as git's bytes, never one replacement
    character for both."""
    first, second = (ex.touched(_octal_patch(octal)) for octal in ("376", "377"))
    assert first == ["\udcfe.py"] and second == ["\udcff.py"] and first != second
    assert first[0].encode("utf-8", errors="surrogateescape") == b"\xfe.py"
    assert set(ex.changed(_octal_patch("376") + _octal_patch("377"))) == {"\udcfe.py", "\udcff.py"}


def _folder_in_a_work_tree(tmp_path, monkeypatch):
    """The empty folder ``touched`` is given, inside a git work tree of its own (``git init``, local, no network)."""
    import subprocess
    from contextlib import contextmanager

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, timeout=60)
    inside = repo / "empty"
    inside.mkdir()

    @contextmanager
    def empty_folder():
        yield str(inside)

    monkeypatch.setattr(ex.tempfile, "TemporaryDirectory", empty_folder)
    return repo


def test_review_4_finding_8_a_work_tree_that_holds_the_temporary_folder_never_changes_what_git_reads(tmp_path, monkeypatch):
    repo = _folder_in_a_work_tree(tmp_path, monkeypatch)
    monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)
    diff = "diff --git a/src/mod.py b/src/mod.py\n--- a/src/mod.py\n+++ b/src/mod.py\n@@ -1 +1 @@\n-old\n+new\n"
    assert ex.changed(diff) == {"src/mod.py": [(1, 1)]}
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))          # a repository the caller's environment names: never git's world
    monkeypatch.setenv("GIT_WORK_TREE", str(repo))
    assert ex.changed(diff) == {"src/mod.py": [(1, 1)]}