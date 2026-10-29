# SPDX-License-Identifier: Apache-2.0
"""V10's cost calibration (``validation/patch_review_search/calibration.py``): arms C, T, E and R on V7's archived
pilot patches only, under a hard dollar stop, into a cost record labelled development calibration.

Development fixtures only: a made-up archive and archive manifest, made-up task IDs, a tiny repository, and a freeze
file at the development level. No model is called and no session is started: arms C, T and E are fakes that spend
what they are told to, and arm R's session record is a file written here."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from patch_review_search import bindings, calibration, study
from patch_review_utility import dispositions

BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PRICES = {"model-x": {"input_tokens": 3.0, "output_tokens": 15.0}}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / "source"
    write(source / "calc.py", BAD)
    git(source, "init", "-q")
    git(source, "add", ".")
    git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "-qm", "development fixture")
    base = git(source, "rev-parse", "HEAD").strip()
    work = tmp_path / "w"
    git(tmp_path, "clone", "-q", str(source), str(work))
    write(work / "calc.py", GOOD)
    patch = git(work, "diff", "--no-color")
    archive = tmp_path / "archive"
    files = {}
    for task_id in ("cal-one", "cal-two"):
        rel = f"legacy/dry/probe/{task_id}/model.patch"
        write(archive / rel, patch)
        files[rel] = {"bytes": len(patch.encode("utf-8")), "sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest()}
    manifest = write(tmp_path / "archive_manifest.json", json.dumps({"study": "V7", "files": files}))
    v8 = write(tmp_path / "splits.draft.json", json.dumps({"scored": ["cal-scored"], "dev": ["cal-one"]}))
    write(tmp_path / "pool.json", json.dumps([{"instance_id": "cal-pool", "repo": "fixture/calc"}]))
    freeze = {
        "study": study.STUDY, "level": "development", "g6_commit": COMMIT,
        "pool": {"file": "pool.json", "sha256": hashlib.sha256((tmp_path / "pool.json").read_bytes()).hexdigest()},
        "v9": {"seed": 1, "limits": {"arm_seconds": 300, "suite_run_seconds": 300, "calibration_seconds": 300,
                                     "token_cap": 200_000},
               "executor": {"command": list(study.v9run.executor.DEFAULT_COMMAND), "confined": False, "network": None},
               "tasks": []},
        "v10": {"cap_cents": 400.0, "shares": None, "limit_s": 300, "grace_s": 30, "call_cap": 4,
                "rerun_timeout_s": 120, "arm_e": {"runs": 1, "run_s": 120},
                "arm_r": {"sessions": "r-sessions", "prices": PRICES}, "bindings": dict(bindings.STAND_INS),
                "similarity": {"measure": "token_containment.v1", "min_run": 8, "threshold": 0.5}}}
    freeze_path = write(tmp_path / "freeze.json", json.dumps(freeze))
    tasks = [{"task_id": name, "repository": "fixture/calc", "source": "source", "base_commit": base,
              "statement": {"problem_statement": "add returns the difference.", "requirements": "- add adds.",
                            "interface": ""}, "patch": f"legacy/dry/probe/{name}/model.patch"}
             for name in ("cal-one", "cal-two")]
    tasks_path = write(tmp_path / "tasks.json", json.dumps({"tasks": tasks}))
    return {"root": tmp_path, "freeze": freeze_path, "tasks": tasks_path, "archive": archive, "manifest": manifest,
            "v8": v8, "patch": patch}


def run(setup, *, cap_dollars=0.8, checkout=None, tasks=None, **more):
    root = setup["root"]
    if tasks is not None:
        write(setup["tasks"], json.dumps({"tasks": tasks}))
    return calibration.calibrate(
        study.load_freeze(setup["freeze"]), tasks_path=setup["tasks"], archive=setup["archive"],
        cap_dollars=cap_dollars, prepared=root / "prepared", work=root / "work", output=root / "out" / "cost.json",
        archive_manifest=setup["manifest"], v8_draft=setup["v8"],
        checkout=checkout or {"commit": COMMIT, "clean": True}, **more)


def fakes(monkeypatch, spend: dict):
    """C, T and E bound to fakes: C and T make one model call costing ``spend[arm]`` cents when the stop allows it; E
    keeps calling, up to three times, while the stop allows another call."""
    def arm(name):
        def call(patch, *, share_left, meter, **kwargs):
            if not share_left():
                raise RuntimeError("the dollar stop allows no further call")
            for _ in range(3 if name == "E" else 1):
                if not share_left():
                    break
                meter.add(cents=spend[name])
            return {"own" if name in "CT" else "disposition": dispositions.make("accept"),
                    "usage": {"input_tokens": 100, "output_tokens": 10}}
        return call
    by = {"search_not_built": None, "arm_e_not_built": arm("E")}

    def load(spec):
        name = spec.rpartition(":")[2]
        return by[name] if by[name] else _search(arm)
    monkeypatch.setattr(bindings, "load", load)


def _search(arm):
    """C and T share one binding; the fake tells them apart by their search configuration."""
    c, t = arm("C"), arm("T")
    return lambda patch, *, search, share_left, meter: (t if search.get("templates") else c)(
        patch, share_left=share_left, meter=meter)


def test_calibration_refuses_pool_ids_v8s_scored_ids_and_the_two_named_tasks_before_anything_runs(setup):
    original = json.loads(setup["tasks"].read_text(encoding="utf-8"))["tasks"]
    for task_id, why in (("cal-pool", "pool"), ("cal-scored", "scored"),
                         ("instance_x__qutebrowser-96b997802e94-v1", "never"), ("org__y-ec2dcfce9eee", "never")):
        with pytest.raises(calibration.Refused, match=why):
            run(setup, tasks=[{**original[0], "task_id": task_id}])
    assert not (setup["root"] / "out").exists() and not (setup["root"] / "work").exists()


def test_calibration_runs_only_from_the_frozen_commit_and_within_fifty_dollars(setup):
    with pytest.raises(calibration.Refused, match="frozen G6 commit"):
        run(setup, checkout={"commit": COMMIT, "clean": False})
    with pytest.raises(calibration.Refused, match="frozen G6 commit"):
        run(setup, checkout={"commit": "f" * 40, "clean": True})
    for dollars in (0, 50.01):
        with pytest.raises(calibration.Refused, match="50"):
            run(setup, cap_dollars=dollars)


def test_an_archived_patch_must_be_v7s_with_the_hash_its_manifest_records(setup):
    original = json.loads(setup["tasks"].read_text(encoding="utf-8"))["tasks"]
    with pytest.raises(calibration.Refused, match="archive"):
        run(setup, tasks=[{**original[0], "patch": "legacy/dry/probe/cal-one/other.patch"}])
    with pytest.raises(calibration.Refused, match="archive"):
        run(setup, tasks=[{**original[0], "patch": original[1]["patch"]}])       # another task's patch
    write(setup["archive"] / original[0]["patch"], setup["patch"] + "\n")
    with pytest.raises(calibration.Refused, match="SHA-256"):
        run(setup, tasks=original)


def test_the_dollar_stop_ends_model_calls_at_the_cap_and_the_record_is_development_calibration(setup, monkeypatch):
    fakes(monkeypatch, {"C": 30.0, "T": 30.0, "E": 30.0})
    root = setup["root"]
    before = setup["freeze"].read_bytes()
    calibration.prepare(study.load_freeze(setup["freeze"]), tasks_path=setup["tasks"], archive=setup["archive"],
                        output_dir=root / "prepared", archive_manifest=setup["manifest"], v8_draft=setup["v8"])
    got = run(setup)
    assert got["kind"] == calibration.KIND and got["study_data"] is False and calibration.LABEL in got["label"]
    assert got["informs"] == calibration.INFORMS
    rows = {(row["task_id"], row["arm"]): row for row in got["rows"]}
    assert [rows[("cal-one", arm)]["cents"] for arm in "CTE"] == [30.0, 30.0, 30.0]     # E began below the cap
    assert rows[("cal-one", "E")]["calls"] == 1                    # and made no further call once past it
    assert rows[("cal-one", "C")]["tokens"] == {"input_tokens": 100, "output_tokens": 10}
    assert rows[("cal-one", "C")]["dollars"] == pytest.approx(0.3) and rows[("cal-one", "C")]["seconds"] >= 0
    assert all(rows[("cal-two", arm)]["not_run"] for arm in "CTE") and got["stopped"] is True
    assert rows[("cal-one", "R")]["error"] == "no session record" and rows[("cal-one", "R")]["cents"] == 0.0
    assert got["spent_cents"] == pytest.approx(90.0) and got["cap_cents"] == pytest.approx(80.0)
    assert got["per_arm"]["C"]["patches"] == 1 and got["per_arm"]["C"]["dollars"] == pytest.approx(0.3)
    assert setup["freeze"].read_bytes() == before
    assert sorted(p.name for p in (root / "out").iterdir()) == ["cost.json"]
    assert json.loads((root / "out" / "cost.json").read_text(encoding="utf-8"))["kind"] == calibration.KIND
