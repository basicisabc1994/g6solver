"""Review 4 findings: every node here fails on 8d46efa3. Archives and container replies are synthetic."""
import json
import subprocess
import sys
from types import ModuleType

import pytest

from wrfu4_helpers import COMMIT, Docker, TREE, add_run, rc, score


def small_source(root, *, missing_final=False):
    archive = rc.c.Archive(root)
    right = add_run(archive, "black-5634", b"right|insufficient_evidence", True)
    add_run(archive, "black-5634", b"wrong|insufficient_evidence", False)
    if missing_final:  # a second finished check of the right patch that holds no final result
        archive.start("check", "black-5634", checked_run=right.rel).finish({"checks": [
            {"patch": "base", "patch_applied": True, "passes": False}]}, "finished")
    return archive


def earlier_module(monkeypatch, registration, revision):
    """replay_rc.py exactly as committed at ``revision``, sharing this tree's run module."""
    module = ModuleType("worked_examples._review_wrfu4_earlier")
    module.__file__, module.__package__ = rc.__file__, "worked_examples"
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code = subprocess.run(["git", "show", f"{revision}:validation/worked_examples/replay_rc.py"],
                          cwd=TREE, check=True, capture_output=True).stdout
    exec(compile(code, rc.__file__, "exec"), module.__dict__)
    monkeypatch.setattr(module, "load_registration", lambda: registration)
    return module


def strict_json(text):
    def refuse(value):
        raise ValueError(f"not standard JSON: {value}")
    return json.loads(text, parse_constant=refuse)


# --- 1. the version integer is the only thing that binds saved labels to the rule ------------------------------------
@pytest.mark.parametrize("edit", ["pre-repair batch, version field added", "current batch, one saved flag edited"])
def test_one_edit_makes_saved_labels_and_flags_trusted(world, edit):
    source, output = world.tmp / "source", world.tmp / "out"
    small_source(source, missing_final=True)
    if edit.startswith("pre-repair"):
        # Review 3's batch: written by 45d5744b, whose rule calls the missing final harmless metadata.
        earlier = earlier_module(world.monkeypatch, world.registration, "45d5744b")
        world.monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
        assert earlier.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"],
                            docker=Docker()) == 0
        world.capsys.readouterr()
        batch = next(output.glob("replay-rc-batch/development/*/run.json"))
        code, captured = score(batch, world.capsys)
        assert code == 2 and json.loads(captured.out)["predictions"]["P3"]["status"] == "incomplete"  # as claimed
        saved = json.loads(batch.read_text(encoding="utf-8"))
        assert "source_snapshot" not in saved["plan"]  # nothing the current writer always saves is there
        saved["evidence_rule_version"] = 1
    else:
        live, batch, _ = world.live(source, output)
        assert live["predictions"]["P3"]["status"] == "incomplete"
        saved = json.loads(batch.read_text(encoding="utf-8"))
        flagged = [row for row in saved["plan"]["index_excluded"] if row["affects_denominator"]]
        assert len(flagged) == 1 and flagged[0]["final_results"] == 0
        flagged[0]["affects_denominator"] = False
    batch.write_text(json.dumps(saved), encoding="utf-8")
    code, captured = score(batch, world.capsys)
    p3 = json.loads(captured.out)["predictions"]["P3"]
    fresh = rc.enumerate_cases(source, rc.we.load_tasks())[1].index_excluded
    assert any(row.get("final_results") == 0 and row["affects_denominator"] for row in fresh)
    assert (code, p3["status"], p3["prediction_met"]) != (0, "met", True), (
        f"{edit}: rescored with exit={code}, P3={p3['status']}, prediction_met={p3['prediction_met']}, although the "
        "source still sits beside the batch and the current rule finds a check with no final result")


# --- 2. a batch from a newer rule is rescored by older code as certain -----------------------------------------------
def test_batch_from_a_newer_rule_is_not_scored_as_certain_by_older_code(world):
    source, output = world.tmp / "source", world.tmp / "out"
    small_source(source)
    current = rc.EVIDENCE_RULE_VERSION
    real = rc.enumerate_cases

    def newer_rule(*args, **kwargs):
        """Rule 2 in a newer checkout: the same reads, one more denominator-affecting exclusion."""
        cases, excluded = real(*args, **kwargs)
        excluded.index_excluded.append({"index": "runs.jsonl", "reason": "synthetic exclusion only rule 2 knows",
                                        "affects_denominator": True})
        return cases, excluded

    with world.monkeypatch.context() as newer:
        newer.setattr(rc, "EVIDENCE_RULE_VERSION", current + 1)
        newer.setattr(rc, "enumerate_cases", newer_rule)
        live, batch, _ = world.live(source, output)
    assert json.loads(batch.read_text(encoding="utf-8"))["evidence_rule_version"] == current + 1
    assert live["predictions"]["P3"]["status"] == "incomplete" and live["predictions"]["P3"]["prediction_met"] is None
    # Back in the older checkout (rule 1), with the source unchanged beside the batch.
    code, captured = score(batch, world.capsys)
    result = json.loads(captured.out)
    p3 = result["predictions"]["P3"]
    assert (code, p3["status"]) != (0, "met"), (
        f"batch saved under rule {current + 1} (P3 incomplete there) rescored by rule {current} code with "
        f"exit={code}, P3={p3['status']}, prediction_met={p3['prediction_met']}; the printed result names no rule "
        f"version: {'evidence_rule_version' in json.dumps(result)}")


# --- 3. the folder --score opens is named by the batch file ----------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="UNC roots are a Windows spelling")
def test_score_opens_no_network_path_named_inside_a_batch(world):
    source = world.tmp / "source"
    small_source(source)
    _, batch, _ = world.live(source, world.tmp / "out")
    saved = json.loads(batch.read_text(encoding="utf-8"))
    saved["evidence_rule_version"] = 0
    saved["plan"]["source_snapshot"]["root"] = r"\\wrfu4-no-such-host.invalid\share\source"
    batch.write_text(json.dumps(saved), encoding="utf-8")
    asked = []
    real = rc._WindowsAPI.open

    def recording(self, path, readable):
        if str(path).startswith("\\\\"):  # never reaches CreateFileW: no network call is made by this test
            asked.append(str(path))
            raise FileNotFoundError(2, "synthetic: not contacted", str(path))
        return real(self, path, readable)

    world.monkeypatch.setattr(rc._WindowsAPI, "open", recording)
    code, captured = score(batch, world.capsys)
    assert code == 2
    assert not asked, f"--score asked Windows to open {len(asked)} time(s) a share named only inside the batch: {asked[0]}"


def test_saved_batch_and_dry_plan_name_no_absolute_host_path(world):
    source = world.tmp / "source"
    small_source(source)
    needle = json.dumps(str(source))[1:-1]
    earlier = earlier_module(world.monkeypatch, world.registration, "d8dcffb2")
    world.monkeypatch.setenv("G6_WE_ARCHIVE", str(world.tmp / "earlier"))
    assert earlier.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"],
                        docker=Docker()) == 0
    world.capsys.readouterr()
    before = next((world.tmp / "earlier").glob("replay-rc-batch/development/*/run.json")).read_text(encoding="utf-8")
    assert needle not in before and ":\\\\" not in before  # the parent commit saved relative names only
    _, batch, _ = world.live(source, world.tmp / "out")
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--dry"]) == 0
    dry = world.capsys.readouterr().out
    found = [name for name, text in (("saved batch", batch.read_text(encoding="utf-8")), ("dry plan", dry))
             if needle in text]
    assert not found, f"the absolute source folder ({len(str(source))} characters, user profile included) is in: {found}"


# --- 4. two refusal paths that do not print what the claim says ------------------------------------------------------
@pytest.mark.parametrize("version", ["NaN", "Infinity", "-Infinity"])
def test_older_rule_refusal_prints_standard_json(world, version):
    source = world.tmp / "source"
    small_source(source)
    _, batch, _ = world.live(source, world.tmp / "out")
    text = batch.read_text(encoding="utf-8")
    needle = '"evidence_rule_version": 1,'
    assert text.count(needle) == 1
    batch.write_text(text.replace(needle, f'"evidence_rule_version": {version},'), encoding="utf-8")
    source.rename(source.with_name("moved"))
    code, captured = score(batch, world.capsys)
    assert code == 2
    try:
        strict_json(captured.out)
    except ValueError as exc:
        line = next(line.strip() for line in captured.out.splitlines() if "saved_evidence_rule_version" in line)
        pytest.fail(f"exit 2 printed a document a JSON parser refuses ({exc}): {line}")


def test_older_batch_with_a_mode_3_record_is_incomplete_with_exit_2(world):
    source = world.tmp / "source"
    small_source(source)
    _, batch, _ = world.live(source, world.tmp / "out")
    saved = json.loads(batch.read_text(encoding="utf-8"))
    saved["plan"]["modes"] = ["hook", "neighbourhood"]
    saved["records"] += [{**record, "mode": "neighbourhood"} for record in saved["records"]]
    batch.write_text(json.dumps(saved), encoding="utf-8")
    code, captured = score(batch, world.capsys)
    assert code == 0 and json.loads(captured.out)["predictions"]["P7"]["status"] == "met"  # accepted under rule 1
    saved["evidence_rule_version"] = 0
    batch.write_text(json.dumps(saved), encoding="utf-8")
    code, captured = score(batch, world.capsys)
    assert code in (0, 2) and captured.out, (
        f"older batch, unchanged source: exit={code}, no JSON printed, stderr={captured.err.strip()!r}")


# --- 5. earlier than this round: a dry plan that passes, then a live batch that is never saved -----------------------
def test_live_batch_is_saved_when_the_dry_plan_passes(world):
    source, output = world.tmp / "source", world.tmp / "out"
    archive = small_source(source)
    run = archive.start("run", "click-3769", arm="B")
    patch = b"third|insufficient_evidence"
    run.write("final.patch", patch)
    run.write("prompt.txt", "Issue #7: synthetic")
    run.write("rounds/round-1.patch", patch)
    # What JSON.stringify writes for half a surrogate pair in a tool input, kept by the capture hook's `cat`.
    run.write("rounds/round-1.input.json", b'{"tool_input": {"task": "half an emoji \\ud83d here", "requirements": []}}')
    run.write_json("rounds.json", [{"round": 1}])
    run.finish({}, "finished")
    archive.start("check", "click-3769", checked_run=run.rel).finish({"checks": [
        {"patch": "final", "patch_applied": True, "passes": True}]}, "finished")
    world.monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    argv = ["--source", str(source), "--registered-commit", COMMIT, "--mode", "agent", "--mode", "hook"]
    assert rc.main([*argv, "--dry"]) == 0
    planned = json.loads(world.capsys.readouterr().out)["calls_by_bug"]
    planned = sum(calls["agent"] + calls["hook"] for calls in planned.values())
    docker = Docker()
    code = rc.main(argv, docker=docker)
    captured = world.capsys.readouterr()
    batches = list(output.glob("replay-rc-batch/development/*/run.json"))
    assert batches, (
        f"dry plan passed with {planned} planned reviews; the live run exited {code} after {docker.execs} review(s) "
        f"and saved no batch record at all: {captured.err.strip()}")
