# SPDX-License-Identifier: Apache-2.0
"""The Swiss cheese table on recorded patches (loop plan Part 25; PLAN.md 2026-11-04): each layer measured on right patches, on the
commonest wrong change of a bug, and on other wrong patches, and each pair of layers by the wrong patches they both miss."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import layers  # noqa: E402

DIFF = """diff --git a/src/pkg/mod.py b/src/pkg/mod.py
--- a/src/pkg/mod.py
+++ b/src/pkg/mod.py
@@ -1,3 +1,3 @@
-    return  x
+    return x  +  1
+    # a comment
diff --git a/tests/test_mod.py b/tests/test_mod.py
--- a/tests/test_mod.py
+++ b/tests/test_mod.py
@@ -0,0 +1 @@
+def test_x(): pass
"""


def test_a_change_is_its_source_lines_without_whitespace_comments_or_tests():
    assert layers.change(DIFF) == (("src/pkg/mod.py", ("-return\0x", "+return\0x\0+\0" "1")),)   # token boundaries kept (review 4, 8)
    assert layers.change(DIFF.replace("x  +  1", "x+1")) == layers.change(DIFF)          # spacing is no difference
    assert layers.change(DIFF.replace("+    # a comment\n", "")) == layers.change(DIFF)  # nor is a comment


def test_a_string_literals_own_spaces_are_part_of_the_change():
    """Review v1 finding 6: two values that differ only in a literal's space are two changes."""
    one = DIFF.replace("x  +  1", "x + 'a b'")
    assert layers.change(one) != layers.change(DIFF.replace("x  +  1", "x + 'ab'"))
    assert layers.change(one) == layers.change(DIFF.replace("x  +  1", "x+'a b'"))


def test_a_quoted_name_and_a_hunk_line_that_begins_like_a_header_are_read_as_git_writes_them():
    """Review v1 findings 7 and 12: names are read as ``git apply`` reads them (``product_review``), and in a hunk every line
    is content, whatever it begins with."""
    quoted = ('diff --git "a/src/caf\\303\\251.py" "b/src/caf\\303\\251.py"\n--- "a/src/caf\\303\\251.py"\n'
              '+++ "b/src/caf\\303\\251.py"\n@@ -1 +1 @@\n-x = 1\n+x = 2\n')
    assert layers.change(quoted) == (("src/café.py", ("-x\0=\0" "1", "+x\0=\0" "2")),)
    plus = DIFF.replace("+    return x  +  1\n", "+    return x\n+++counter\n")
    assert "++\0+\0counter" in layers.change(plus)[0][1]      # the line "+++counter", its tokens joined by NUL (review 4, 8)


def test_a_flag_is_counted_by_the_comparisons_own_rule():
    """Review v1 finding 10: an opinion on a right patch is a false alarm, as ``replay_compare.right`` counts it, and on a wrong
    patch it is a miss."""
    got = layers.table({"r": "right", "w": "other wrong"}, {"final review": {"r": "opinion", "w": "opinion"}})
    assert got["final review"]["false alarms"] == (1, 1) and got["final review"]["misses"] == (1, 1)


def test_the_commonest_wrong_change_needs_two_runs_and_is_counted_by_run():
    runs = [  # (sha, normalised change, right?)
        ("a", ("w1",), False), ("a", ("w1",), False), ("b", ("w1",), False),     # three runs of one wrong change, two patches
        ("c", ("w2",), False),                                                     # wrong in its own way
        ("d", ("r",), True), ("e", ("r",), True), ("e", ("r",), True), ("e", ("r",), True),   # right, the larger group
    ]
    assert layers.kinds_of(runs) == {"a": "commonest wrong", "b": "commonest wrong", "c": "other wrong", "d": "right", "e": "right"}
    assert layers.kinds_of([("a", ("w1",), False), ("c", ("w2",), False)]) == {"a": "other wrong", "c": "other wrong"}


def test_a_bug_is_mixed_when_its_runs_hold_both_a_right_and_a_wrong_patch():
    assert layers.mixed({"a": [("x", (), True), ("y", (), False)], "b": [("z", (), True)], "c": [("w", (), False)]}) == {"a"}


def test_a_layer_is_counted_by_kind_and_a_pair_by_the_wrong_patches_both_miss():
    kinds = {"r1": "right", "r2": "right", "m1": "commonest wrong", "m2": "commonest wrong", "o1": "other wrong", "o2": "other wrong"}
    said = {"L": {"r1": "none", "r2": "reproduced", "m1": "none", "m2": "none", "o1": "reproduced", "o2": "reproduced"},
            "M": {"r1": "none", "r2": "none", "m1": "none", "m2": "reproduced", "o1": "none", "o2": "reproduced", "x": "none"}}
    got = layers.table(kinds, said)
    assert got["L"]["flagged"] == {"right": (1, 2), "commonest wrong": (0, 2), "other wrong": (2, 2)}
    assert got["L"]["misses"] == (2, 4) and got["L"]["false alarms"] == (1, 2)
    assert got["M"]["flagged"]["commonest wrong"] == (1, 2) and got["M"]["misses"] == (2, 4)     # "x" has no kind: not counted
    pair = layers.overlap(kinds, said, "L", "M")
    assert pair == {"both judged": 4, "a misses": 2, "b misses given a misses": (1, 2), "b misses": (2, 4)}
