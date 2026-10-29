# SPDX-License-Identifier: Apache-2.0
"""V10's arm R, a fresh review by a coding agent that did not write the patch (PROTOCOL.md version 2 on the
verification search amendment branch, "Arm R in detail"). The script never starts a model or a session: these tests
use development fixtures and written session records and transcripts only."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from patch_review_search import arm_e, arm_r
from patch_review_utility import contract, executor, self_review, trees
from patch_review_utility.dispositions import ABSTAIN, ACCEPT, DEFECT

REQUIREMENTS = contract.split("Addition returns the sum of its two arguments.")
BASE = {"calc.py": "def add(a, b):\n    return a + b\n", "tests/test_calc.py": "def test_true():\n    assert True\n"}
PATCH_TEXT = "def add(a, b):\n    return a - b\n"
FINDING = "from calc import add\n\n\ndef test_sum():\n    assert add(2, 3) == 5\n"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    for rel, text in BASE.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD").strip()
    (root / "calc.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8", newline="\n")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qam", "a later fix")
    git(root, "checkout", "-q", base)
    (root / "calc.py").write_text(PATCH_TEXT, encoding="utf-8", newline="\n")
    patch = git(root, "diff")
    git(root, "checkout", "-q", "--", "calc.py")
    return root, base, patch


def test_the_prompt_is_v9s_arm_2_prompt_with_exactly_the_registered_changes():
    expected = self_review.REVIEW_PROMPT
    for old, new in arm_r.PROMPT_CHANGES:
        assert expected.count(old) == 1
        expected = expected.replace(old, new)
    assert arm_r.REVIEW_PROMPT == expected and arm_r.REVIEW_PROMPT != self_review.REVIEW_PROMPT
    (who, _), (kept, _) = arm_r.PROMPT_CHANGES
    assert who.startswith("You wrote the patch") and kept.endswith("is discarded.")
    assert arm_r.REVIEW_PROMPT.startswith("Another agent wrote the patch")
    assert f"in the folder {arm_r.FOLDER}" in arm_r.REVIEW_PROMPT
    assert "Only the tests in that folder are kept, as evidence, and they are never applied to the patch; every other " \
           "change you make while reviewing is discarded." in arm_r.REVIEW_PROMPT
    assert arm_r.FOLDER.startswith("g6_check_")


def test_prepare_freezes_the_prompt_the_caps_the_network_rule_and_the_session_set_up():
    got = arm_r.prepare("t1", REQUIREMENTS, patch="the diff", time_limit_s=1800, token_cap=200_000)
    assert got["prompt"] == arm_r.REVIEW_PROMPT.format(contract=contract.render(REQUIREMENTS))
    assert got["caps"]["time_limit_s"] == 1800 and got["caps"]["token_cap"] == 200_000
    assert got["caps"]["network"] == arm_r.NETWORK and "model provider" in arm_r.NETWORK
    assert got["session"] == arm_r.SESSION and got["session"]["tool_servers"] == []
    assert {"plugins", "skills", "hooks", "memory", "instruction_files"} <= set(got["session"])
    assert got["diff"] == "the diff" and got["tests_folder"] == arm_r.FOLDER
    assert got["patch_sha256"] == self_review.prepare("t1", REQUIREMENTS, patch="the diff", time_limit_s=1800,
                                                      token_cap=200_000)["patch_sha256"]
    with pytest.raises(ValueError):
        arm_r.prepare("t1", REQUIREMENTS, patch="x", time_limit_s=0, token_cap=1)


def test_the_copy_holds_the_base_commit_with_the_patch_uncommitted_and_no_later_history(source, tmp_path):
    root, base, patch = source
    copy = arm_r.make_copy(root, base, patch, tmp_path / "copy")
    assert git(copy, "rev-parse", "HEAD").strip() == base and trees.changed_files(copy) == ["calc.py"]
    assert (copy / "calc.py").read_text(encoding="utf-8") == PATCH_TEXT and arm_r.history_problem(copy, base) == ""
    full = trees.checkout(root, base, tmp_path / "full")
    assert "commit" in arm_r.history_problem(full, base)
    with pytest.raises(ValueError, match="does not apply"):
        arm_r.make_copy(root, base, "--- a/none.py\n+++ b/none.py\n@@ -1 +1 @@\n-x\n+y\n", tmp_path / "bad")


def test_the_disposition_is_v9_arm_2s_rule_and_a_session_past_its_limits_abstains():
    prepared = arm_r.prepare("t1", REQUIREMENTS, patch="the diff", time_limit_s=60, token_cap=100)
    record = {"task_id": "t1", "patch_sha256": prepared["patch_sha256"], "ended": "completed", "elapsed_s": 10,
              "tokens": 50, "model_calls": 3, "answer": "Fine.\nVERDICT: ACCEPT"}
    assert arm_r.disposition is self_review.disposition
    assert arm_r.disposition(prepared, record)["category"] == ACCEPT
    assert arm_r.disposition(prepared, {**record, "answer": "Broken.\nVERDICT: REJECT"})["category"] == DEFECT
    late = arm_r.disposition(prepared, {**record, "elapsed_s": 61})
    assert late["category"] == ABSTAIN and late["time_or_cap_reached"] is True


def test_its_tests_are_collected_from_the_folder_kept_as_arm_es_and_judged_on_the_patched_tree_and_the_base(
        source, tmp_path):
    root, base, patch = source
    copy = arm_r.make_copy(root, base, patch, tmp_path / "copy")
    folder = copy / arm_r.FOLDER
    folder.mkdir()
    (folder / "test_sum.py").write_text(FINDING, encoding="utf-8", newline="\n")
    (folder / "notes.txt").write_text("not a test\n", encoding="utf-8")
    (folder / "test_zmany.py").write_text("".join(f"def test_{i}():\n    pass\n\n\n" for i in range(25)),
                                         encoding="utf-8", newline="\n")
    (copy / "test_elsewhere.py").write_text(FINDING, encoding="utf-8")    # outside the folder: never collected
    pool = arm_r.collect(copy)
    assert sorted(item["path"] for item in pool) == ["notes.txt", "test_sum.py", "test_zmany.py"]
    patched = arm_r.make_copy(root, base, patch, tmp_path / "patched")
    at_base = trees.checkout(root, base, tmp_path / "base")
    got = arm_r.judge(pool, executor=executor.Executor(), patched_tree=patched, base_tree=at_base, runs=1, run_s=60)
    assert got["counts"]["kept_tests"] == arm_e.MAX_TESTS and got["counts"]["discarded"] == 1 + 6
    sums = arm_e.placement("test_sum.py", FINDING) + "::test_sum"
    assert got["tests"][sums]["finding"] is True and got["tests"][sums]["fails_at_base"] is False
    assert [f["node"] for f in got["findings"]] == [sums] and got["findings"][0]["text"] == FINDING
    # each kept file's text stays in R's output, for the similarity measure after the labels
    assert {one["file"]: one["text"] for one in got["kept"]}["test_sum.py"] == FINDING


def test_collection_skips_the_bytecode_of_running_rs_tests_outright(tmp_path):
    # the rehearsal's observation, and the coordinator's ruling (2026-10-29): nothing to discard and count
    folder = tmp_path / "copy" / arm_r.FOLDER
    (folder / "__pycache__").mkdir(parents=True)
    (folder / "__pycache__" / "test_sum.cpython-312-pytest-9.1.1.pyc").write_bytes(b"\x00\x01")
    (folder / "deep" / "__pycache__").mkdir(parents=True)
    (folder / "deep" / "__pycache__" / "notes.txt").write_text("cached\n", encoding="utf-8")
    (folder / "stray.pyc").write_bytes(b"\x00")
    (folder / "test_sum.py").write_text(FINDING, encoding="utf-8")
    assert [item["path"] for item in arm_r.collect(tmp_path / "copy")] == ["test_sum.py"]


def test_its_tokens_are_the_sessions_usage_by_kind_for_measure_6():
    record = {"usage": {"input_tokens": 40, "output_tokens": 2000, "cache_read_input_tokens": 90000,
                        "cache_creation_input_tokens": 5000}}
    assert arm_r.tokens(record) == record["usage"]
    assert arm_r.tokens({"usage": {}}) is None and arm_r.tokens({}) is None


def test_its_cost_is_the_sessions_tokens_priced_by_the_frozen_price_list():
    prices = {"claude-opus-5-5": {"input_tokens": 15.0, "output_tokens": 75.0, "cache_read_input_tokens": 1.5}}
    record = {"model": "claude-opus-5-5", "usage": {"input_tokens": 200_000, "output_tokens": 10_000,
                                                    "cache_read_input_tokens": 1_000_000}}
    assert arm_r.cost_cents(record, prices) == pytest.approx(100 * (3.0 + 0.75 + 1.5))
    with pytest.raises(ValueError, match="price list"):
        arm_r.cost_cents({**record, "model": "another-model"}, prices)
    with pytest.raises(ValueError, match="cache_creation"):
        arm_r.cost_cents({**record, "usage": {**record["usage"], "cache_creation_input_tokens": 5}}, prices)
    assert arm_r.cost_cents({"model": "claude-opus-5-5"}, prices) is None


def test_the_gaming_scan_reads_rs_whole_transcript(tmp_path):
    copy = tmp_path / "copy"
    copy.mkdir()

    def tool(name, value):
        return json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name,
                                                                         "input": value}]}})
    lines = [json.dumps({"type": "system", "subtype": "init", "mcp_servers": [{"name": "g6", "status": "connected"}],
                         "tools": ["Bash", "Read", "mcp__g6__review_patch"]}),
             tool("Bash", {"command": "git log --oneline"}), tool("Bash", {"command": "git show HEAD~1"}),
             tool("Bash", {"command": "curl https://example.invalid/fix.diff"}),
             tool("Write", {"file_path": str(copy / arm_r.FOLDER / "test_a.py"), "content": "def test_a(): pass"}),
             tool("Write", {"file_path": str(tmp_path / "outside.txt"), "content": "x"}),
             tool("Edit", {"file_path": str(copy / "conftest.py"), "old_string": "a", "new_string": "b"})]
    got = arm_r.scan_session("\n".join(lines), source="R:t1", copy_root=copy, vault_markers=["vault-folder"])
    assert got["git_commands"] == ["git log --oneline", "git show HEAD~1"]
    kinds = [finding["kind"] for finding in got["findings"]]
    assert "network" in kinds and kinds.count("session_set_up") == 2 and kinds.count("edit_outside_copy") == 1
    assert not any(f["kind"] == "edit_outside_patch" and arm_r.FOLDER in f["detail"] for f in got["findings"])
    assert any(f["kind"] == "edit_outside_patch" and "conftest.py" in f["detail"] for f in got["findings"])
    clean = arm_r.scan_session(json.dumps({"type": "system", "subtype": "init", "mcp_servers": [], "tools": ["Bash"]}),
                               source="R:t2", copy_root=copy, vault_markers=[])
    assert clean == {"git_commands": [], "findings": []}
