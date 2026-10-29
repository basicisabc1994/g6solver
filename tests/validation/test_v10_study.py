# SPDX-License-Identifier: Apache-2.0
"""V10's entry point for V9's feasibility level and V10 together (``validation/patch_review_search/study.py``): the
freeze file, the draw, the prepared sessions, the arms in the protocol's order with the record and its final hash
before any label, and the labels after the committed hash.

Development fixtures only: a tiny repository, patches, label contracts, session records and transcripts made here;
no V9 or V10 evaluation data, and the freeze file says ``"level": "development"``. Nothing calls a model or starts a
session: V9's arms 3 and 4 are stand-ins that read the patched file, arms C, T and E are fakes, and arm R's session
records are files written here, as an approved launcher would leave them."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from patch_review_search import analysis, bindings, record, study
from patch_review_utility import arms, dispositions, draw, labels

BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
EXISTING = "from calc import add\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n"
HIDDEN = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
MORE = "from calc import add\n\n\ndef test_more():\n    assert add(1, 1) == 2\n"
R_TEST = "from calc import add\n\n\ndef test_r():\n    assert add(2, 3) == 5\n"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PRICES = {"model-x": {"input_tokens": 3.0, "output_tokens": 15.0}}
SIMILARITY = {"measure": "token_containment.v1", "min_run": 8, "threshold": 0.5}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def diff(source: Path, changes: dict[str, str], work: Path) -> str:
    git(source.parent, "clone", "-q", str(source), str(work))
    for rel, text in changes.items():
        write(work / rel, text)
    git(work, "add", "-A")
    return git(work, "diff", "--cached", "--no-color")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def joint(tmp_path, monkeypatch):
    """A development freeze file for two tasks on one repository, in the draw's order: a correct patch and a
    defective one."""
    monkeypatch.setenv("G6_ECHA_RESEARCH", "1")
    source = tmp_path / "source"
    for rel, text in {"calc.py": BAD, "tests/test_existing.py": EXISTING}.items():
        write(source / rel, text)
    git(source, "init", "-q")
    git(source, "add", ".")
    git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "-qm", "development fixture")
    base = git(source, "rev-parse", "HEAD").strip()
    patches = {"dev-correct": diff(source, {"calc.py": GOOD, "tests/test_more.py": MORE}, tmp_path / "w1"),
               "dev-defective": diff(source, {"calc.py": "def add(a, b):\n    return a * b\n",
                                              "tests/test_more.py": MORE}, tmp_path / "w2")}
    reference = diff(source, {"calc.py": GOOD}, tmp_path / "w3")
    pool = [{"instance_id": name, "repo": "fixture/calc"} for name in patches]
    write(tmp_path / "pool.json", json.dumps(pool))
    seed = 20261029
    tasks = {}
    for task_id, text in patches.items():
        folder = tmp_path / "vault" / task_id
        write(folder / "reference.diff", reference)
        write(folder / "test_hidden.py", HIDDEN)
        write(folder / "contract.json", json.dumps({
            "task_id": task_id, "reference_patch": "reference.diff",
            "groups": [{"id": "hidden_tests", "origin": "hidden_tests", "files": {"test_hidden.py": "test_hidden.py"},
                        "fail_to_pass": ["test_hidden.py::test_add"],
                        "pass_to_pass": ["tests/test_existing.py::test_zero"]}]}))
        write(tmp_path / "patches" / f"{task_id}.diff", text)
        write(tmp_path / "sessions" / f"{task_id}.json", json.dumps({
            "task_id": task_id, "patch_sha256": sha(text), "answer": "Checked.\nVERDICT: ACCEPT", "ended": "completed",
            "elapsed_s": 60.0, "tokens": 1000, "model_calls": 3}))
        write(tmp_path / "transcripts" / f"{task_id}.jsonl", json.dumps({"type": "tool_use", "name": "Bash",
                                                                          "input": {"command": "pytest -q"}}))
        tasks[task_id] = {"task_id": task_id, "repository": "fixture/calc", "source": "source", "base_commit": base,
                          "statement": {"problem_statement": "add returns the difference instead of the sum.",
                                        "requirements": "- add(a, b) returns a + b.", "interface": ""},
                          "patch": f"patches/{task_id}.diff", "label_contract": f"vault/{task_id}/contract.json",
                          "self_review": f"sessions/{task_id}.json", "transcripts": [f"transcripts/{task_id}.jsonl"]}
    ordered = draw.order(pool, seed=seed)
    freeze = {
        "study": study.STUDY, "level": "development", "g6_commit": COMMIT,
        "pool": {"file": "pool.json", "sha256": hashlib.sha256((tmp_path / "pool.json").read_bytes()).hexdigest()},
        "seen": [], "replaced": {},
        "v9": {"seed": seed, "limits": {"arm_seconds": 300, "suite_run_seconds": 300, "calibration_seconds": 300,
                                        "token_cap": 200_000},
               "executor": {"command": list(study.v9run.executor.DEFAULT_COMMAND), "confined": False, "network": None},
               "suites": {"fixture/calc": ["tests"]},
               "vault": {"roots": ["vault"], "id": "v10-development", "ledger": "ledger.jsonl"},
               "audit": None, "registered_size": None,
               "broken_tasks": {"published_list": {"found": False}, "screen": {"on": False}},
               "tasks": [tasks[name] for name in ordered]},
        "v10": {"cap_cents": 400.0, "shares": None, "limit_s": 300, "grace_s": 30, "call_cap": 4,
                "rerun_timeout_s": 120, "arm_e": {"runs": 1, "run_s": 120},
                "arm_r": {"sessions": "r-sessions", "prices": PRICES},
                "similarity": dict(SIMILARITY), "bindings": dict(bindings.STAND_INS)}}
    path = write(tmp_path / "freeze.json", json.dumps(freeze))
    return tmp_path, path, freeze, ordered


def stand_ins(monkeypatch):
    """V9's arms 3 and 4 replaced by stand-ins that accept exactly when calc.py adds (no confined run is needed)."""
    def judged(arm, tree, *args, **kwargs):
        good = (Path(tree) / "calc.py").read_text(encoding="utf-8") == GOOD
        disposition = dispositions.make("accept" if good else "defect_found")
        return {"disposition": {**disposition, "cost": {"seconds": 0.1, "processor_seconds": 0.1, "model_calls": 0}},
                "trace": {"commands": ["pytest"], "isolation": [{"backend": "stand-in", "network": False}]},
                "a5": {"complete": True, "missing": []}}
    monkeypatch.setattr(arms, "in_child", judged)


def fakes(monkeypatch, calls: list):
    """Arms C, T and E bound to fakes that say what they were given and spend one cent each."""
    def search(patch, *, search, share_left, meter):
        calls.append(("search", patch["id"], dict(search), list(patch["checks_cited"])))
        meter.add(cents=1.0)
        return {"own": dispositions.make("accept"), "model_calls": 1, "spent_cents": 1.0}

    def arm_e(patch, *, c_calls, t_calls, share_left, meter):
        calls.append(("E", patch["id"], c_calls, t_calls, patch["arm_1"]["category"]))
        meter.add(cents=1.0)
        return {"disposition": dict(patch["arm_1"]), "findings": [], "calls": {"due": 1, "made": 1}}

    monkeypatch.setattr(bindings, "load", lambda spec: arm_e if spec.endswith("arm_e_not_built") else search)


def session(root: Path, task_id: str, patch: str, prepared: Path):
    """What an approved launcher leaves for arm R: the session record and its transcript, and a test in the copy."""
    copy = Path(json.loads(prepared.read_text(encoding="utf-8"))["copy"])
    write(copy / "g6_check_review" / "test_r.py", R_TEST)
    write(root / "r-sessions" / f"{task_id}.jsonl", json.dumps({"type": "tool_use", "name": "Bash",
                                                                 "input": {"command": "git status"}}))
    write(root / "r-sessions" / f"{task_id}.json", json.dumps({
        "task_id": task_id, "patch_sha256": sha(patch), "answer": "VERDICT: REJECT", "ended": "completed",
        "elapsed_s": 30.0, "tokens": 900, "model_calls": 2, "model": "model-x",
        "usage": {"input_tokens": 800, "output_tokens": 100}, "transcript": f"{task_id}.jsonl"}))


def test_the_entry_point_refuses_to_start_without_a_freeze_file_naming_a_g6_commit(joint):
    root, path, freeze, _ = joint
    assert study.load_freeze(path)["g6_commit"] == COMMIT
    for broken_freeze, why in (({**freeze, "g6_commit": None}, "G6 commit"), ({**freeze, "g6_commit": "abc"}, "G6 commit"),
                               ({**freeze, "study": "V9"}, "freeze file"),
                               ({**freeze, "v10": {**freeze["v10"], "similarity": None}}, "similarity"),
                               ({**freeze, "v10": {**freeze["v10"], "similarity": {**SIMILARITY, "threshold": 2}}},
                                "similarity")):
        write(path, json.dumps(broken_freeze))
        with pytest.raises(study.Refused, match=why):
            study.load_freeze(path)
    feasibility = {**freeze, "level": "feasibility",
                   "v9": {**freeze["v9"], "executor": {**freeze["v9"]["executor"], "confined": True, "network": False}}}
    write(path, json.dumps(feasibility))
    with pytest.raises(study.Refused, match="stand-in"):
        study.load_freeze(path)
    with pytest.raises(study.Refused, match="no freeze file"):
        study.load_freeze(root / "missing.json")


def test_the_draw_leaves_out_every_excluded_id_and_the_manifest_must_follow_it(joint, tmp_path):
    root, path, freeze, ordered = joint
    other = write(tmp_path / "earlier-split.json", json.dumps({"eval": ["dev-spare"]}))
    pool = [*json.loads((root / "pool.json").read_text(encoding="utf-8")),
            {"instance_id": "dev-spare", "repo": "fixture/calc"}]
    write(root / "pool.json", json.dumps(pool))
    write(path, json.dumps({**freeze, "pool": {"file": "pool.json", "sha256": hashlib.sha256(
        (root / "pool.json").read_bytes()).hexdigest()}}))
    got = study.draw(study.load_freeze(path), exclusion_files=[other])
    assert "dev-spare" not in got["order"] and got["excluded"][str(other)] == ["dev-spare"]
    assert got["order"] == ordered and got["seed"] == freeze["v9"]["seed"]
    write(path, json.dumps({**freeze, "pool": {**freeze["pool"], "sha256": "f" * 64}}))
    with pytest.raises(study.Refused, match="pool"):
        study.draw(study.load_freeze(path), exclusion_files=[])
    swapped = {**freeze, "v9": {**freeze["v9"], "tasks": list(reversed(freeze["v9"]["tasks"]))}}
    write(path, json.dumps(swapped))
    with pytest.raises(study.Refused, match="draw order"):
        study.follows_draw(study.load_freeze(path), {"order": ordered, "seed": freeze["v9"]["seed"]})


def test_prepare_writes_arm_2s_and_arm_rs_inputs_and_rs_copies_with_no_history(joint):
    root, path, freeze, ordered = joint
    loaded = study.load_freeze(path)
    written = study.prepare(loaded, {"order": ordered, "seed": freeze["v9"]["seed"]}, root / "prepared")
    prepared = json.loads((root / "prepared" / "arm_r" / "dev-correct.json").read_text(encoding="utf-8"))
    assert "Another agent wrote the patch" in prepared["prompt"] and prepared["caps"]["token_cap"] == 200_000
    copy = Path(prepared["copy"])
    assert (copy / "calc.py").read_text(encoding="utf-8") == GOOD
    assert study.arm_r.history_problem(copy, freeze["v9"]["tasks"][0]["base_commit"]) == ""
    assert (root / "prepared" / "arm_2" / "dev-defective.json").is_file() and len(written) == 4


def test_arms_run_in_the_protocols_order_and_the_record_and_its_final_hash_come_before_any_label(joint, monkeypatch):
    root, path, freeze, ordered = joint
    stand_ins(monkeypatch)
    calls = []
    fakes(monkeypatch, calls)
    loaded = study.load_freeze(path)
    order = {"order": ordered, "seed": freeze["v9"]["seed"]}
    calibration = root / "vault" / "calibration.json"
    study.v9run.calibrate(loaded["v9"], calibration)
    study.prepare(loaded, order, root / "prepared")
    session(root, "dev-correct", (root / "patches" / "dev-correct.diff").read_text(encoding="utf-8"),
            root / "prepared" / "arm_r" / "dev-correct.json")         # no session for dev-defective

    def no_label(*args, **kwargs):
        raise AssertionError("a label was read before the record's final hash")
    monkeypatch.setattr(labels, "label", no_label)
    got = study.arms(loaded, order, calibration=calibration, prepared=root / "prepared", work=root / "work",
                     record_path=root / "record.jsonl", output=root / "arms.json")
    final = (root / "record.jsonl.final").read_text(encoding="utf-8").strip()
    assert final == got["record"]["final"] and len(final) == 64
    kinds = [entry["kind"] for entry in record.verify(root / "record.jsonl", final)]
    assert kinds[:3] == ["v9_arms", "v9_arms", "v9_gaming"] and kinds.index("arm") > 2
    assert kinds.index("comparison") > max(i for i, kind in enumerate(kinds) if kind == "arm")
    assert [c[:2] for c in calls] == [("search", ordered[0]), ("search", ordered[0]), ("E", ordered[0]),
                                      ("search", ordered[1]), ("search", ordered[1]), ("E", ordered[1])]
    assert calls[0][2] == {"templates": False} and calls[1][2]["templates"] is True
    assert calls[0][3] == ["tests/test_more.py"]                 # the checks arm A cites, for C and T too
    written = json.loads((root / "arms.json").read_text(encoding="utf-8"))
    assert all(row["label"] is None for row in written["v9"]["rows"]) and written["labels"] == "not read"
    by = {row["patch"]: row["arms"] for row in got["v10"]["patches"]}
    assert by["dev-defective"]["R"]["disposition"]["detail"] == "no session record"     # never launched here
    assert by["dev-correct"]["R"]["disposition"]["category"] == "defect_found"
    assert by["dev-correct"]["R"]["gaming"]["git_commands"] and by["dev-correct"]["R"]["counts"]["kept_tests"] == 1
    assert by["dev-correct"]["R"]["meter"]["cents"] == pytest.approx((800 * 3 + 100 * 15) / 1_000_000 * 100)
    with pytest.raises(study.Refused, match="one run"):
        study.arms(loaded, order, calibration=calibration, prepared=root / "prepared", work=root / "work2",
                   record_path=root / "record.jsonl", output=root / "arms2.json")


def test_the_labels_are_read_only_after_the_committed_hash_and_both_studies_results_are_written(joint, monkeypatch):
    root, path, freeze, ordered = joint
    stand_ins(monkeypatch)
    fakes(monkeypatch, [])
    loaded = study.load_freeze(path)
    order = {"order": ordered, "seed": freeze["v9"]["seed"]}
    calibration = root / "vault" / "calibration.json"
    study.v9run.calibrate(loaded["v9"], calibration)
    study.prepare(loaded, order, root / "prepared")
    session(root, "dev-correct", (root / "patches" / "dev-correct.diff").read_text(encoding="utf-8"),
            root / "prepared" / "arm_r" / "dev-correct.json")
    got = study.arms(loaded, order, calibration=calibration, prepared=root / "prepared", work=root / "work",
                     record_path=root / "record.jsonl", output=root / "arms.json")
    with pytest.raises(record.RecordRefused):
        study.label(loaded, arms_path=root / "arms.json", record_path=root / "record.jsonl", committed="a" * 64,
                    calibration=calibration, work=root / "label-work", output_dir=root / "results")
    assert not (root / "results").exists()
    done = study.label(loaded, arms_path=root / "arms.json", record_path=root / "record.jsonl",
                       committed=got["record"]["final"], calibration=calibration, work=root / "label-work",
                       output_dir=root / "results")
    v9 = json.loads((root / "results" / "v9-results.json").read_text(encoding="utf-8"))
    rows = {row["task_id"]: row for row in v9["tasks"]}
    assert rows["dev-correct"]["label"] == "correct" and rows["dev-defective"]["label"] == "defective"
    v10 = json.loads((root / "results" / "v10-results.json").read_text(encoding="utf-8"))
    assert v10["study"] == "V10" and v10["record"]["committed"] == got["record"]["final"]
    assert v10["counts"]["defective"] == 1 and v10["counts"]["correct"] == 1
    assert set(v10["rates"]) >= set(analysis.ARMS) and done["v10"]["feasibility"]["criteria"]
    # similarity, measured in the label step inside the vault: R's test is the hidden test with another name
    assert v10["similarity"]["spec"] == SIMILARITY and set(v10["similarity"]["patches"]) == set(ordered)
    (checked,) = v10["similarity"]["patches"]["dev-correct"]["R"]
    assert checked["above"] is True and checked["similarity"] == pytest.approx(12 / 18, abs=1e-6)
    assert v10["headline_without_similar"] is not None and v10["secondary"]["template_applicability"]
