# SPDX-License-Identifier: Apache-2.0
"""The replay cohort's defect 2 (PLAN.md, 2026-11-06): a review called an empty patch clean (K3 pilot, pygments-3158 repeat 1:
first patch, rule patch and final patch all empty; review 1 said "none"; the loop's status was "no_counterexample_in_domain").

An empty patch is never clean: every review loop's status is decided by one rule (``run.loop_status``) from the size of the
tree's patch, read in the container as every capture makes it (``run.patch_bytes``). A tree with no change is "no_patch"; a loop
is clean only over a patch known to hold a change; a size that cannot be read is never clean. Fakes only: no docker, no model."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import compare  # noqa: E402
import run as we  # noqa: E402

_spec = importlib.util.spec_from_file_location("one_tree_helpers_no_patch", Path(__file__).with_name("test_box_one_tree.py"))
tree = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tree)
helpers = tree.helpers

CLEAN = "no_counterexample_in_domain"
NO_PATCH = getattr(we, "NO_PATCH", "no_patch")
MARK = getattr(we, "PATCH_BYTES", "we-patch-bytes")
QUIET = {"verdict": "none", "rule": "", "why": "", "test": "", "seconds": 1.0}


class SizedDocker:
    """A container that answers the patch's size (``run.patch_bytes``) with ``size`` (None: a reply with no size in it), and every
    other script with a tree that never moves."""

    def __init__(self, size):
        self.size, self.scripts = size, []

    def sh(self, agent, script, *a, **kw):
        self.scripts.append(script)
        said = f"{MARK}={self.size}\n" if self.size is not None and MARK in script else "tree 0"
        return SimpleNamespace(returncode=0, stdout=said.encode(), stderr=b"")


# --- the pilot's case, end to end ---------------------------------------------------------------------------------------------
def test_a_k3_run_whose_patches_are_all_empty_is_no_patch_though_its_review_found_nothing(monkeypatch, tmp_path):
    got = tree.run_with(monkeypatch, tmp_path, "K3", [("s1", []), ("s1", [])], commands={}, review=lambda n: QUIET)
    assert got.record["status"] == "finished", got.record.get("error")
    assert (got.folder / "final.patch").read_bytes() == b"" and we.baseline_of(got.folder).read_bytes() == b""
    assert got.record["repairs"]["status"] == NO_PATCH, got.record["repairs"]
    assert compare.said_of(got.ctx.archive.root, {"arm": "K3", "run": got.record["run"]}) != "clean"


def test_the_same_run_with_a_change_is_clean(monkeypatch, tmp_path):
    got = tree.run_with(monkeypatch, tmp_path, "K3", [("s1", [("write", "src/mod.py", b"def f():\n    return 1\n")]), ("s1", [])],
                        commands={}, review=lambda n: QUIET)
    assert got.record["repairs"]["status"] == CLEAN, got.record["repairs"]


# --- each loop, by the one rule -------------------------------------------------------------------------------------------------
def _first():
    first = we.c.Tally()
    first.feed({"type": "result", "subtype": "success", "is_error": False, "session_id": "s1"})
    return first


@pytest.mark.parametrize(("size", "status"), [(0, NO_PATCH), (120, CLEAN), (None, "inconclusive")])
def test_arm_ks_loop_over_an_empty_patch_is_no_patch_and_never_clean(monkeypatch, size, status):
    monkeypatch.setattr(we, "keep_first_patch", lambda *a, **kw: None)
    got = we.reviews_until_clean(SimpleNamespace(docker=SizedDocker(size), arm="K2"), SimpleNamespace(repo="/w/r"),
                                 SimpleNamespace(write_json=lambda *a: None), "agent", "base", [], "key", _first(), 1.0, 0.0,
                                 lambda n: dict(QUIET), finding="parent")
    assert got["status"] == status and got["rounds"] == 1


@pytest.mark.parametrize(("size", "status"), [(0, NO_PATCH), (120, CLEAN), (None, "inconclusive")])
def test_arm_rs_one_review_over_an_empty_patch_is_no_patch(monkeypatch, size, status):
    ctx = SimpleNamespace(docker=SizedDocker(size), arm="R")
    got = we.review_once(ctx, SimpleNamespace(repo="/w/r"), SimpleNamespace(write_json=lambda *a: None), "agent", [], "key", None,
                         1.0, 0.0, lambda: dict(QUIET), base="base")
    assert got["status"] == status


import replay4 as r4  # noqa: E402

AT_PARENT = we.ProbeRun(1, {"t::test_example": "failed"}, {}, "")
NOTHING_AT_PARENT = we.ProbeRun(0, {"t::test_example": "passed"}, {}, "")
HOLDS = we.ProbeRun(0, {"t::test_example": "passed"}, {}, "")


def _bug(**kw):
    return {"verdict": "defect", "rule": "r", "why": "w", "test": "def test_example():\n    assert 0\n", "error": "",
            "at_parent": AT_PARENT, "instruction": r4.SYMPTOM.digest, "arm": "V4", **kw}


@pytest.mark.parametrize("found", [_bug(test="", verdict="none", at_parent=None), _bug(at_parent=NOTHING_AT_PARENT), _bug()],
                         ids=["no test written", "nothing on the parent", "the test holds"])
@pytest.mark.parametrize(("size", "status"), [(0, NO_PATCH), (120, CLEAN), (None, "inconclusive")])
def test_arm_vs_loop_over_an_empty_patch_is_no_patch_whichever_way_it_found_nothing(monkeypatch, found, size, status):
    monkeypatch.setattr(we, "run_set", lambda *a, **kw: HOLDS)
    monkeypatch.setattr(we, "write_in", lambda *a, **kw: None)
    ctx = SimpleNamespace(docker=SizedDocker(size), arm="V4")
    got = we.reviews_with_bug_test(ctx, SimpleNamespace(repo="/w/r"), SimpleNamespace(write_json=lambda *a: None), "agent", "base",
                                   [], "key", None, 1.0, 0.0, found)
    assert got["status"] == status, got


@pytest.mark.parametrize(("said", "size", "status"), [(CLEAN, 0, NO_PATCH), (CLEAN, 120, CLEAN), (CLEAN, None, "inconclusive"),
                                                      ("inconclusive", 0, NO_PATCH), ("inconclusive", 120, "inconclusive")])
def test_arm_ls_loop_over_an_empty_patch_is_no_patch_whatever_cegis_said(monkeypatch, said, size, status):
    import g6.cegis

    monkeypatch.setattr(g6.cegis, "cegis", lambda *a, **kw: SimpleNamespace(status=said, reason="r"))
    ctx = SimpleNamespace(docker=SizedDocker(size), arm="L1")
    got = we.repair_loop(ctx, SimpleNamespace(repo="/w/r"), SimpleNamespace(write_json=lambda *a: None, write=lambda *a: None),
                         "agent", [], "key", None, 1.0, 0.0, base="base")
    assert got["status"] == status


def test_the_patch_size_is_read_as_every_capture_makes_the_patch_and_a_reply_without_it_is_none():
    docker = SizedDocker(42)
    assert we.patch_bytes(SimpleNamespace(docker=docker), SimpleNamespace(repo="/w/r"), "agent", "base") == 42
    assert we.diff_script("/w/r", "base", f"{we.WE}/we-size.patch") in docker.scripts[0]
    assert we.patch_bytes(SimpleNamespace(docker=SizedDocker(None)), SimpleNamespace(repo="/w/r"), "agent", "base") is None

    class Fails:
        def sh(self, *a, **kw):
            raise we.subprocess.TimeoutExpired("docker", 0)
    assert we.patch_bytes(SimpleNamespace(docker=Fails()), SimpleNamespace(repo="/w/r"), "agent", "base") is None
    assert we.patch_bytes(SimpleNamespace(docker=docker), SimpleNamespace(repo="/w/r"), "agent", None) is None


# --- the inventory: every status a review loop returns goes through the one rule ---------------------------------------------
LOOPS = {"reviews_until_clean", "review_once", "reviews_with_bug_test", "repair_loop"}


def test_every_status_a_review_loop_returns_is_decided_by_the_one_rule_and_the_clean_word_is_written_once():
    source = (ROOT / "validation" / "worked_examples" / "run.py").read_text("utf-8")
    module = ast.parse(source)
    seen: dict[str, int] = {}
    for fn in [n for n in ast.walk(module) if isinstance(n, ast.FunctionDef) and n.name in LOOPS]:
        for ret in [n for n in ast.walk(fn) if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]:
            for key, value in zip(ret.value.keys, ret.value.values):
                if isinstance(key, ast.Constant) and key.value == "status":
                    seen[fn.name] = seen.get(fn.name, 0) + 1
                    assert (isinstance(value, ast.Call) and getattr(value.func, "id", "") == "loop_status"), \
                        f"{fn.name} line {ret.lineno} returns a status the one rule did not decide"
    assert set(seen) == LOOPS, seen
    clean_words = [n.lineno for n in ast.walk(module) if isinstance(n, ast.Constant) and n.value == CLEAN]
    assert len(clean_words) == 1, f"the clean word is written at lines {clean_words}: one fact, one place"
    assert compare.CLEAN == we.LOOP_CLEAN
