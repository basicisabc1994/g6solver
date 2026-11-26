# SPDX-License-Identifier: Apache-2.0
"""Path comparison keys must never replace names used by replay I/O or git."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from worked_examples import replay_rc as rc


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive path arguments")
def test_mixed_case_paths_replay_and_score_with_exact_tracked_registration(tmp_path, monkeypatch, capsys):
    source, output = tmp_path / "SoUrCe", tmp_path / "OuTPuT"
    task = rc.we.load_tasks()[0]
    archive = rc.c.Archive(source)
    run = archive.start("run", task.id, arm="B")
    run.write("FiNaL.PaTcH", b"synthetic patch")
    run.write_json("rounds.json", [])
    run.write("prompt.txt", "Issue #496: synthetic issue")
    run.finish({}, "finished")
    check = archive.start("check", task.id, checked_run=run.rel)
    check.finish({"checks": [{"patch": "final", "patch_applied": True, "passes": False}]}, "finished")
    real_registration, real_repo = rc.REGISTRATION, rc.we.REPO_ROOT
    tracked = real_registration.relative_to(real_repo).as_posix()
    commit, image = "a" * 40, "sha256:" + "2" * 64
    git_run, git_calls, copies = subprocess.run, [], []

    def git(args, **kwargs):
        assert args == ["git", "-C", str(real_repo), "show", f"HEAD:{tracked}"]
        git_calls.append(args)
        return git_run(args, **kwargs)

    class Docker:
        def __call__(self, *args, **kwargs):
            payload = b""
            if args[:2] == ("image", "inspect"):
                payload = json.dumps([{"Id": image, "Config": {"Labels": {"we.g6.commit": commit}}}]).encode()
            elif args[0] == "cp":
                copies.append(args[1])
            elif args[0] == "exec":
                payload = json.dumps({"ok": True, "assessment": {
                    "outcome": "insufficient_evidence", "completion": {}}}).encode()
            return subprocess.CompletedProcess(args, 0, payload, b"")

        def sh(self, *args, **kwargs):
            state = {"head": task.parent, "tree": "1" * 40, "parent_tree": "1" * 40,
                     "dirty": 0, "commits": 1, "oracle": False}
            return subprocess.CompletedProcess(args, 0, json.dumps(state).encode(), b"")

    monkeypatch.setattr(rc.subprocess, "run", git)
    monkeypatch.setattr(rc, "REGISTRATION", Path(str(real_registration).swapcase()))
    monkeypatch.setattr(rc.we, "REPO_ROOT", Path(str(real_repo).swapcase()))
    source_arg = Path(str(source).swapcase())
    # The existing prefix must regain its real name; the absent output leaf
    # must keep the exact spelling requested for creation.
    output_arg = Path(str(tmp_path).swapcase()) / output.name
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output_arg))
    assert rc.main(["--source", str(source_arg), "--registered-commit", commit, "--mode", "hook"],
                   docker=Docker()) == 0
    evaluation = json.loads(capsys.readouterr().out)
    score = next(output.glob("replay-rc-batch/development/*/run.json"))
    saved = json.loads(score.read_text(encoding="utf-8"))
    assert len(saved["records"]) == 1
    assert saved["records"][0]["status"] == "finished"
    assert saved["plan"]["cases"][0]["patch"] == f"{run.rel}/FiNaL.PaTcH"
    assert saved["records"][0]["origins"][0]["run"] == run.rel
    assert len(copies) == 1 and copies[0].startswith(str(output) + os.sep)
    assert rc.main(["--score", str(score).swapcase()]) == 0
    assert json.loads(capsys.readouterr().out) == {**evaluation,
        "evidence_rule_version": rc.EVIDENCE_RULE_VERSION,
        "saved_evidence_rule_version": rc.EVIDENCE_RULE_VERSION, "evidence_source": "batch"}
    assert len(git_calls) >= 2
    assert all(call[-1] == "HEAD:validation/worked_examples/REPLAY_RC.md" for call in git_calls)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle final-name spelling")
def test_canonical_paths_keep_real_names_and_absent_tail_spelling(tmp_path):
    source = tmp_path / "SoUrCe"
    source.mkdir()
    content = source / "PaYlOaD.TxT"
    content.write_bytes(b"inside")
    assert str(rc.canonical_path(Path(str(content).swapcase()))) == str(content)
    absent = Path(str(source).swapcase()) / "MiSsInG" / "LeAf.TxT"
    assert str(rc.canonical_path(absent)) == str(source / "MiSsInG" / "LeAf.TxT")
    reader = rc.Source(Path(str(source).swapcase()))
    assert str(reader.path("payload.txt")) == str(content)
    assert reader.read_bytes("pAyLoAd.tXt") == b"inside"


@pytest.mark.skipif(os.name != "nt", reason="Windows case and extended-prefix containment")
@pytest.mark.parametrize("direction", ["same", "inside", "parent"])
def test_mixed_case_comparison_still_refuses_overlapping_output(tmp_path, direction):
    source = tmp_path / "SoUrCe"
    source.mkdir()
    target = {"same": source, "inside": source / "ChIlD", "parent": tmp_path}[direction]
    source_arg = Path("\\\\?\\" + str(source).swapcase())
    with pytest.raises(ValueError, match="disjoint"):
        rc.output_archive(source_arg, Path(str(target).swapcase()))


@pytest.mark.skipif(os.name != "nt", reason="Windows full-path alias folding")
@pytest.mark.parametrize("component", range(4))
@pytest.mark.parametrize("suffix", [".", " "])
def test_ambiguous_names_refuse_before_any_component_open(tmp_path, monkeypatch, component, suffix):
    parts = ["run", "black-5634", "001", "final.patch"]
    parts[component] += suffix

    def no_open(*args, **kwargs):
        pytest.fail("ambiguous spelling reached a component open")

    monkeypatch.setattr(rc._WindowsAPI, "open", no_open)
    with pytest.raises(ValueError, match="forbidden archive path"):
        rc.canonical_path(tmp_path.joinpath(*parts))
