# SPDX-License-Identifier: Apache-2.0
"""V9's entry point (``validation/patch_review_utility/run.py``) on a development manifest: calibration, arm 2's
session inputs, and one study run to a results file with its input digests.

Development fixtures only: the repository, the patches, the label contracts, the session records and the transcripts
are tiny ones made here; none is V9 evaluation data, and the manifest says ``"level": "development"``. G6's confined
runs need Linux bubblewrap: the test that takes ``bwrap_backend`` runs arms 3 and 4 for real and skips elsewhere; the
others replace those two arms with stand-ins that read the patched file."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from patch_review_utility import arms, dispositions
from patch_review_utility import run as study

BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
EXISTING = "from calc import add\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n"
HIDDEN = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
MORE = "from calc import add\n\n\ndef test_more():\n    assert add(1, 1) == 2\n"


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


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """A development manifest for three tasks on one repository: a correct patch, a defective one, an empty one."""
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
                                              "tests/test_more.py": MORE}, tmp_path / "w2"),
               "dev-empty": ""}
    reference = diff(source, {"calc.py": GOOD}, tmp_path / "w3")
    tasks = []
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
        record = {"task_id": task_id, "patch_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                  "answer": "Checked.\nVERDICT: ACCEPT", "ended": "completed", "elapsed_s": 60.0, "tokens": 1000,
                  "model_calls": 3}
        write(tmp_path / "sessions" / f"{task_id}.json", json.dumps(record))
        write(tmp_path / "transcripts" / f"{task_id}.jsonl", json.dumps({"type": "tool_use", "name": "Bash",
                                                                          "input": {"command": "pytest -q"}}))
        tasks.append({"task_id": task_id, "repository": "fixture/calc", "source": "source", "base_commit": base,
                      "statement": {"problem_statement": "add returns the difference instead of the sum.",
                                    "requirements": "- add(a, b) returns a + b.", "interface": ""},
                      "patch": f"patches/{task_id}.diff", "label_contract": f"vault/{task_id}/contract.json",
                      "self_review": f"sessions/{task_id}.json", "transcripts": [f"transcripts/{task_id}.jsonl"]})
    manifest = {"study": "V9", "level": "development", "seed": 20261028,
                "limits": {"arm_seconds": 300, "suite_run_seconds": 300, "calibration_seconds": 300,
                           "token_cap": 200_000},
                "executor": {"command": list(study.executor.DEFAULT_COMMAND), "confined": False, "network": None},
                "suites": {"fixture/calc": ["tests"]},
                "vault": {"roots": ["vault"], "id": "v9-development", "ledger": "ledger.jsonl"},
                "audit": None, "registered_size": None, "tasks": tasks}
    path = write(tmp_path / "manifest.json", json.dumps(manifest))
    return tmp_path, path, manifest


def stand_ins(monkeypatch):
    """Arms 3 and 4 replaced by stand-ins that accept exactly when calc.py adds (no confined run is needed)."""
    def judged(arm, tree, *args, **kwargs):
        assert arm in ("g6", "direct_checks")
        good = (Path(tree) / "calc.py").read_text(encoding="utf-8") == GOOD
        disposition = dispositions.make("accept" if good else "defect_found")
        return {"disposition": {**disposition, "cost": {"seconds": 0.1, "processor_seconds": 0.1, "model_calls": 0}},
                "trace": {"commands": ["pytest"], "isolation": [{"backend": "stand-in", "network": False}]},
                "a5": {"complete": True, "missing": []}}
    monkeypatch.setattr(arms, "in_child", judged)


def test_a_development_study_runs_from_calibration_to_one_results_file(layout, monkeypatch):
    root, manifest_path, _ = layout
    stand_ins(monkeypatch)
    calibration = root / "vault" / "calibration.json"
    study.main(["calibrate", "--manifest", str(manifest_path), "--output", str(calibration)])
    calibrated = json.loads(calibration.read_text(encoding="utf-8"))
    assert all(task["passed"] for task in calibrated["tasks"].values()) and calibrated["replace"] == []
    study.main(["prepare-self-review", "--manifest", str(manifest_path), "--output-dir", str(root / "inputs")])
    prepared = json.loads((root / "inputs" / "dev-correct.json").read_text(encoding="utf-8"))
    assert "R2. add(a, b) returns a + b." in prepared["prompt"] and prepared["caps"]["token_cap"] == 200_000
    output = root / "results.json"
    study.main(["run", "--manifest", str(manifest_path), "--calibration", str(calibration), "--output", str(output)])
    text = output.read_text(encoding="utf-8")
    assert "Infinity" not in text and "NaN" not in text
    results = json.loads(text)
    rows = {row["task_id"]: row for row in results["tasks"]}
    assert rows["dev-correct"]["label"] == "correct" and rows["dev-defective"]["label"] == "defective"
    assert rows["dev-empty"]["patch"] == "empty" and rows["dev-empty"]["label"] is None
    assert rows["dev-correct"]["arms"]["tests"]["accepted"] is True        # no regression, the added test passes
    assert rows["dev-defective"]["arms"]["tests"]["category"] == "defect_found"   # the added test fails
    assert rows["dev-defective"]["arms"]["self_review"]["accepted"] is True      # the session record said so
    assert rows["dev-defective"]["arms"]["g6"]["accepted"] is False
    assert rows["dev-correct"]["checks_cited"] == ["tests/test_more.py"]
    analysis = results["experiment_1"]
    assert analysis["counts"] == {"tasks": 3, "non_empty": 2, "empty": 1, "not_applying": 0, "unlabelled": 0,
                                  "correct": 1, "defective": 1}
    assert analysis["rates"]["self_review"]["false_acceptance"]["rate"] == 1.0
    assert results["feasibility"]["result"] == "feasible"
    assert results["verdict"]["verdict"] == "inconclusive" and results["level"] == "development"
    assert results["gaming"]["contaminated"] is False and results["gaming"]["vault_changes"] == []
    assert results["a5"] == {"checked": 2, "incomplete": []}
    assert results["model_calls_by_g6"] == 0 and results["model_calls_by_g6_source"] == []   # the stand-ins say none
    assert "scripts/INTERPRETATIONS.md" in results["input_sha256"]
    digests = results["input_sha256"]
    assert {"manifest", "calibration", "scripts/run.py", "scripts/arms.py", "validation/patch_review/run.py",
            "patches/dev-correct.diff", "sessions/dev-correct.json"} <= set(digests)
    assert results["vault"]["digest"] and results["vault"]["blinding"]["blinding"] == "unblinded"


def test_a_study_level_refuses_an_unconfined_executor_or_an_arm_input_inside_the_vault(layout, monkeypatch):
    root, manifest_path, manifest = layout
    commit = "0123456789abcdef0123456789abcdef01234567"
    feasibility = {**manifest, "level": "feasibility", "g6_commit": commit}
    write(manifest_path, json.dumps(feasibility))
    with pytest.raises(study.Refused, match="confined"):
        study.load_manifest(manifest_path)
    write(manifest_path, json.dumps({**feasibility, "executor": {**manifest["executor"], "confined": True,
                                                                   "network": False}}))
    assert study.load_manifest(manifest_path)["seed"] == study.stats.seed("feasibility", commit)
    unnamed = {**feasibility, "executor": {"command": ["docker", "run", "--rm", "image", "{tests}"], "confined": True,
                                           "network": False}}
    write(manifest_path, json.dumps(unnamed))
    with pytest.raises(study.Refused, match="container"):
        study.load_manifest(manifest_path)
    leaky = {**manifest, "tasks": [{**manifest["tasks"][0], "patch": "vault/dev-correct/reference.diff"}]}
    write(manifest_path, json.dumps(leaky))
    with pytest.raises(study.Refused, match="vault"):
        study.main(["run", "--manifest", str(manifest_path), "--calibration", str(root / "c.json"),
                    "--output", str(root / "r.json")])


def test_a_container_environment_runs_each_task_in_its_own_image_with_g6s_toolchain_only_where_g6_runs(layout):
    root, manifest_path, manifest = layout
    commit = "0123456789abcdef0123456789abcdef01234567"
    environment = {"kind": "containers", "toolchain": {"volume": "g6-v9-toolchain-x", "lock": "toolchain.lock.json"},
                   "python": {"fixture/calc": "/usr/bin/python"}}
    tasks = [{**task, "image": f"registry.invalid/calc:{task['task_id']}"} for task in manifest["tasks"]]
    feasibility = {**manifest, "level": "feasibility", "g6_commit": commit, "environment": environment, "tasks": tasks}
    feasibility.pop("executor")
    write(manifest_path, json.dumps(feasibility))
    loaded = study.load_manifest(manifest_path)          # containers confine every run, with the network closed
    task = loaded["tasks"][0]
    tests = study.task_executor(loaded, task)
    assert tests.confined is True and tests.network is False and tests.command == tuple(
        study.containers.tests_command(task["image"], python="/usr/bin/python"))
    g6 = study.g6_command(loaded, task)
    assert g6 == study.containers.g6_command(task["image"], toolchain="g6-v9-toolchain-x", study=study.VALIDATION,
                                             repo_python="/usr/bin/python")
    missing = {**feasibility, "tasks": [{k: v for k, v in t.items() if k != "image"} for t in tasks]}
    write(manifest_path, json.dumps(missing))
    with pytest.raises(study.Refused, match="image"):
        study.load_manifest(manifest_path)
    unnamed = {**feasibility, "environment": {**environment, "python": {}}}
    write(manifest_path, json.dumps(unnamed))
    with pytest.raises(study.Refused, match="Python"):
        study.load_manifest(manifest_path)
    write(manifest_path, json.dumps(manifest))
    local = study.load_manifest(manifest_path)
    assert study.g6_command(local, local["tasks"][0]) is None             # development runs G6 on the host


def test_the_cited_checks_are_the_patchs_test_files_still_on_the_tree(tmp_path):
    root = tmp_path / "r"
    for rel in ("test_gone.py", "test_kept.py", "calc.py"):
        write(root / rel, "def test_x():\n    pass\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=F", "-c", "user.email=f@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm",
        "base")
    (root / "test_gone.py").unlink()
    write(root / "test_kept.py", "def test_x():\n    assert 1\n")
    write(root / "tests" / "test_new.py", "def test_y():\n    pass\n")
    assert study.cited_checks(root) == ["test_kept.py", "tests/test_new.py"]


def test_the_research_flag_is_the_persons_decision(layout, monkeypatch):
    root, manifest_path, _ = layout
    monkeypatch.delenv("G6_ECHA_RESEARCH")
    with pytest.raises(RuntimeError, match="G6_ECHA_RESEARCH"):
        study.main(["calibrate", "--manifest", str(manifest_path), "--output", str(root / "c.json")])


def test_a_missing_session_record_is_a_counted_tool_error_never_a_dropped_patch(layout, monkeypatch):
    root, manifest_path, _ = layout
    stand_ins(monkeypatch)
    (root / "sessions" / "dev-defective.json").unlink()
    calibration = root / "vault" / "calibration.json"
    study.main(["calibrate", "--manifest", str(manifest_path), "--output", str(calibration)])
    study.main(["run", "--manifest", str(manifest_path), "--calibration", str(calibration),
                "--output", str(root / "results.json")])
    results = json.loads((root / "results.json").read_text(encoding="utf-8"))
    tally = results["experiment_1"]["tallies"]["self_review"]
    assert tally["tool_error_or_refusal"] == 1 and tally["error_kinds"] == {"tool_error": 1}


def test_confined_arms_on_the_development_manifest(bwrap_backend, layout):
    root, manifest_path, _ = layout
    calibration = root / "vault" / "calibration.json"
    study.main(["calibrate", "--manifest", str(manifest_path), "--output", str(calibration)])
    study.main(["run", "--manifest", str(manifest_path), "--calibration", str(calibration),
                "--output", str(root / "results.json")])
    results = json.loads((root / "results.json").read_text(encoding="utf-8"))
    rows = {r["task_id"]: r for r in results["tasks"]}
    assert results["model_calls_by_g6_source"] == [arms.MODEL_CALLS_NOT_REPORTED]
    assert rows["dev-correct"]["arms"]["g6"]["accepted"] is True
    assert rows["dev-correct"]["arms"]["direct_checks"]["accepted"] is True
    assert rows["dev-defective"]["arms"]["g6"]["category"] == "defect_found"
    assert rows["dev-defective"]["arms"]["direct_checks"]["category"] == "defect_found"
