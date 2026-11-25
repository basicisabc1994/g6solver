# SPDX-License-Identifier: Apache-2.0
"""Classify every worked-example Archive.start writer, without opening an archive."""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)


def test_every_archive_start_kind_has_a_classification():
    root = Path(__file__).resolve().parents[2]
    paths = subprocess.run(["git", "-C", str(root), "ls-files", "validation/worked_examples/*.py"],
                           capture_output=True, text=True, check=True).stdout.splitlines()
    kinds = set()

    def literals(node):
        if isinstance(node, ast.IfExp):
            return literals(node.body) | literals(node.orelse)
        assert isinstance(node, ast.Constant) and isinstance(node.value, str), ast.dump(node)
        return {node.value}

    for rel in paths:
        tree = ast.parse((root / rel).read_bytes(), filename=rel)
        for node in ast.walk(tree):
            # In this package argument-bearing starts are Archive.start; thread
            # and regex Match.start calls take no arguments. Include keyword kind
            # calls, and require a decision for any new dynamic calling shape.
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "start"):
                kind = node.args[0] if node.args else next(
                    (kw.value for kw in node.keywords if kw.arg == "kind"), None)
                if kind is None:
                    assert not node.keywords, f"unclassified start call: {rel}:{node.lineno}"
                else:
                    kinds |= literals(kind)
    assert kinds == set(rc.INDEX_CLASSIFICATION)
    assert rc.INDEX_CLASSIFICATION["run"] is None
    assert rc.INDEX_CLASSIFICATION["check"] is None
    assert all(reason for kind, reason in rc.INDEX_CLASSIFICATION.items() if kind not in {"run", "check"})


@pytest.mark.parametrize("kind", ["distill", "dryrun", "build", "validate", "amend", "recheck",
                                  "selfcheck", "g6replay", "replay", "modelcheck", "freecheck",
                                  "replay-rc", "replay-rc-batch"])
def test_known_nonreplayable_rows_are_named_without_candidate_reads(tmp_path, monkeypatch, kind):
    archive = rc.c.Archive(tmp_path)
    run = archive.start(kind, "G1" if kind == "distill" else TASK.id)
    run.finish({}, "finished")
    check = archive.start("check", TASK.id, checked_run=run.rel)
    check.finish({}, "finished")
    original = rc.Source.read_bytes

    def read(source, rel):
        assert str(rel) == "runs.jsonl", "metadata was read as a replay candidate"
        return original(source, rel)

    monkeypatch.setattr(rc.Source, "read_bytes", read)
    index_excluded = []
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK], index_excluded=index_excluded)
    assert cases == excluded == []
    assert len(index_excluded) == 4
    assert all(row["reason"] and row["run"] in {run.rel, check.rel} for row in index_excluded)


def test_cli_plan_retains_index_exclusions(tmp_path, capsys):
    rc.c.Archive(tmp_path).start("distill", "G1").finish({}, "finished")
    assert rc.main(["--source", str(tmp_path), "--registered-commit", "a" * 40, "--dry"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(plan["index_excluded"]) == 2
    assert all(row["kind"] == "distill" and row["reason"] for row in plan["index_excluded"])
    assert plan["excluded"] == []


def test_empty_patch_is_a_named_exclusion_before_labels_or_containers(tmp_path):
    run = rc.c.Archive(tmp_path).start("run", TASK.id)
    run.write("final.patch", b"")
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert cases == []
    assert excluded == [{"bug": TASK.id, "patch": f"{run.rel}/final.patch", "reason": "empty patch"}]
