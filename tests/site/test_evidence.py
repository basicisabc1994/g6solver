# SPDX-License-Identifier: Apache-2.0
"""The Research page's numbers are read from the committed study results, never typed in."""
from __future__ import annotations

import json
import re

import pytest

from pages import evidence


def _bars(study_id: str) -> dict[str, dict[str, tuple[int, int | None]]]:
    study = next(s for s in evidence.studies() if s.id == study_id)
    return {g.label: {b.arm: (b.value, b.n) for b in g.bars} for g in study.groups}


def test_every_registered_study_has_a_tile_with_its_own_verdict():
    registry = (evidence.VALIDATION_DIR / "REGISTRY.md").read_text(encoding="utf-8")
    registered = re.findall(r"^## (V\w+) — ", registry, re.MULTILINE)
    tiles = {s.id: s for s in evidence.studies() if s.kind == "registered"}
    assert registered and sorted(tiles) == sorted(registered)
    for study_id, study in tiles.items():
        report = next((evidence.VALIDATION_DIR / "results" / study_id).glob("*.json"))
        if study_id.startswith("V6"):
            report = evidence.VALIDATION_DIR / "results" / study_id / "analysis.json"
        assert study.verdict == json.loads(report.read_text(encoding="utf-8"))["verdict"], study_id


def test_numbers_come_from_the_results_files():
    v6b = _bars("V6b")
    assert v6b["GSM8K"]["B"] == (20, 100) and v6b["GSM8K"]["D"] == (81, 100) and v6b["GSM8K"]["H"] == (78, 100)
    assert _bars("V6")["GSM8K"]["B"] == (10, 60) and _bars("V6")["GSM8K"]["D"] == (42, 60)
    assert _bars("V1")["Negated claims"]["check_claim"] == (0, 74)
    assert _bars("V2")["Harmful calls let through"]["gate_declared"] == (1, 30)


def test_the_headline_is_the_latest_adaptation_study():
    study_id, groups = evidence.headline()
    assert study_id == "V6b"
    assert [g.label for g in groups] == ["GSM8K", "MASSIVE", "SciFact", "MBPP"]
    # the model alone first (owner, 2026-10-19); every G6 option, on both pages, so the winning numbers agree (2026-10-20)
    assert [b.arm for b in groups[0].bars] == ["A", "B", "G", "H", "F", "D"]


def test_each_task_sets_winning_option_is_g6s_best_and_says_what_it_changed():
    # owner, 2026-10-20: the top number shows G6's highest-performing option and names the winning approach
    import json

    arms = json.loads((evidence.VALIDATION_DIR / "results" / "V6b" / "analysis.json").read_text(encoding="utf-8"))["arms"]
    _, groups = evidence.headline()
    for group, w in zip(groups, evidence.winners()):
        assert group.best.arm == w["arm"] and group.best.value == w["value"]
        assert w["value"] == max(arms[w["key"]][a]["success"] for a in evidence.G6_OPTIONS if a in arms[w["key"]])
        assert w["wins"] > w["losses"] and w["p"] < 0.05                     # each beat the model alone, task by task
    assert [(w["dataset"], w["arm"]) for w in evidence.winners()] == [
        ("GSM8K", "D"), ("MASSIVE", "F"), ("SciFact", "D"), ("MBPP", "G")]


def test_each_winning_change_is_the_change_that_was_approved(monkeypatch):
    """The words of WINNING_CHANGE against the approved profiles: the same digests recur in later rounds' records."""
    import json

    def head(rnd: str, wf: str) -> dict:
        path = evidence.VALIDATION_DIR / "rounds" / rnd / "tuning" / f"{wf}-D.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def activated(wf: str, arm: str) -> str:
        path = evidence.VALIDATION_DIR / "results" / "V6b" / "adaptation" / f"{wf}-{arm}.json"
        versions = json.loads(path.read_text(encoding="utf-8"))["inspect_versions"]
        return next(v["profile_digest"] for v in versions if v["kind"] == "activate")

    gsm8k = head("round-02", "gsm8k")                                  # the same profile the study approved
    assert gsm8k["head_digest"] == activated("gsm8k", "D") and "step by step" in gsm8k["head_profile"]["prompt"]
    massive = head("round-02", "massive")
    assert massive["head_digest"] == activated("massive", "F") and massive["head_profile"]["exemplars"] == 8
    sweep = (evidence.VALIDATION_DIR / "rounds" / "round-01" / "single_change_sweep.json").read_text(encoding="utf-8")
    assert activated("scifact", "D")[:12] in sweep and "Use only the abstract, not outside knowledge" in sweep
    monkeypatch.syspath_prepend(str(evidence.VALIDATION_DIR.parents[1] / "validation"))      # oss/validation
    from v6b_metaharness import workflows as v6b

    every = v6b.SPECS["mbpp"].everything()
    assert {"self_review", "repair_with_tests"} <= set(every["pipeline"]) and every["exemplars"] == 8
    assert every["extra_checks"] == ["imports-cleanly"]


def _round_three() -> tuple[dict, callable]:
    folder = evidence.VALIDATION_DIR / "rounds" / "round-03"
    board = json.loads((folder / "scoreboard.json").read_text(encoding="utf-8"))
    return board, lambda name: json.loads((folder / "tuning" / f"{name}.json").read_text(encoding="utf-8"))


def test_round_three_says_whether_the_two_modes_stack():
    # owner, 2026-10-20: the experiments freeze after round 3, which asks whether self-tuning that starts from every
    # prebuilt component beats each mode alone
    board, _ = _round_three()
    found = evidence.both_modes()
    assert found["round"] == 3 and found["datasets"] == len(board["workflows"]) == 4
    approved = [k for k, t in board["tuning"].items() if k.endswith("-DG") and t["outcome"] == "activated"]
    assert found["approved_from_components"] == len(approved) == 0
    worst, scifact = found["worst"], board["workflows"]["scifact"]
    assert (worst["dataset"], worst["alone"], worst["components"], worst["n"]) == (
        "SciFact", scifact["A"]["success"], scifact["G"]["success"], scifact["G"]["n"])
    assert worst["lost"] > worst["gained"]


def test_round_threes_note_is_its_own_records():
    """Every figure in the round's note, against its scoreboard and tuning records, and the masking it names against
    the profile it started from: a second look at each of SciFact's three labels re-asks every answer."""
    board, tuning = _round_three()
    note, flows = evidence.ROUND_NOTES["round-03"], board["workflows"]
    assert f"GSM8K {flows['gsm8k']['A']['success']} to {flows['gsm8k']['D']['success']}" in note
    assert f"MASSIVE {flows['massive']['A']['success']} to {flows['massive']['G']['success']}" in note
    assert f"({flows['scifact']['G']['success']} against {flows['scifact']['A']['success']})" in note
    tuned = tuning("scifact-D")["run"]
    chosen, fresh = tuned["cycles"][-1]["raced"][0], tuned["confirmation"]["success"]
    assert f"won {chosen['wins']} to {chosen['losses']}" in note and f"{fresh['wins']} to {fresh['losses']} on fresh" in note
    masked = tuning("scifact-DG")
    assert all(r["wins"] == r["losses"] == 0 for c in masked["run"]["cycles"] for r in c["raced"])
    assert {"second_look_support", "second_look_contradict", "second_look_not_enough_info"} <= set(
        masked["initial_profile"]["pipeline"]) and "changed no task" in note
    assert f"{flows['mbpp']['A']['n']} fresh tasks" in note
    assert f"all {sum(v['n'] for v in board['repeat'].values())} outputs" in note
    assert all(v["identical"] == v["n"] for v in board["repeat"].values())


def test_the_wall_lists_the_newest_study_first_and_planned_ones_last():
    assert [s.id for s in evidence.studies()] == ["V6b", "V6-pilot", "V6", "V2", "V1", "V3", "V4", "V5"]


def test_planned_studies_are_marked_not_run():
    planned = {s.id: s for s in evidence.studies() if s.kind == "planned"}
    assert sorted(planned) == ["V3", "V4", "V5"]
    assert all(s.status == "not run" and s.verdict is None and not s.groups for s in planned.values())


def test_the_development_pilot_is_labelled_as_development():
    pilot = next(s for s in evidence.studies() if s.kind == "development")
    assert pilot.verdict is None and "development" in pilot.note.lower()


def test_every_archive_row_carries_its_audit():
    archive = evidence.archive()
    assert archive["label"] == "Earlier G6 benchmark study, audited, not re-validated"
    assert len(archive["rows"]) == 16                     # the old page's results table, row for row
    for row in archive["rows"]:
        assert row["audit"] and row["audit_status"] in evidence.AUDIT_STATUSES, row["benchmark"]
        assert row["g6"] and row["baseline"] and row["in_library"], row["benchmark"]
    by_name = {row["benchmark"]: row for row in archive["rows"]}
    # the audited figures, not the old page's headlines (47% SWE-bench Pro, 70.2% HLE, 95% BBEH)
    assert by_name["SWE-bench Pro"]["g6"].startswith("38%") and by_name["HLE"]["g6"] == "not run"
    assert by_name["BBEH"]["g6"].startswith("87.0%")


def test_every_study_run_is_counted_once_with_what_it_covers():
    # owner, 2026-10-19: the count reflects all previous work, and says which studies are about the core harness and
    # which about extensions
    rows = evidence.ledger()
    registered = [s for s in evidence.studies() if s.kind == "registered"]
    finished = [r for r in evidence.rounds() if not r.status.startswith("in progress")]
    assert len(rows) == len(registered) + 1 + len(finished) + len(evidence.archive()["rows"])   # + the pilot
    assert len({r.name for r in rows}) == len(rows)
    by = {r.name: r for r in rows}
    assert by["SWE-bench Pro"].scope == "core" and by["Omni-MATH"].scope == "extensions"
    assert by["GAIA Level 2"].scope == "core + extensions"
    assert all(r.scope == "core" for r in rows if r.source in ("registered", "development"))


def test_each_study_says_how_the_same_model_did_without_g6_and_with_it():
    by = {r.name: r for r in evidence.ledger()}
    swe = by["SWE-bench Pro"]
    assert (swe.without, swe.with_g6, swe.outcome) == ("27%", "38%", "gain")
    assert by["SkillsBench"].outcome == "lower" and by["ARC-AGI v1"].outcome == "no difference"
    assert by["tau2-bench"].outcome == "no baseline"
    assert by["HLE"].outcome == "specialised" and "70.2%" in by["HLE"].with_g6
    assert by["ARC-AGI 3"].outcome == "specialised" and "81 of 183" in by["ARC-AGI 3"].with_g6
    tuned = by["Self-tuning on four public datasets"]                   # the model alone against G6, self-tuned
    assert (tuned.without, tuned.with_g6, tuned.outcome) == ("193 of 400", "266 of 400", "gain")
    assert by["Claim checking"].better == "lower" and by["Claim checking"].with_g6 == "0 of 174"


def test_the_component_matrix_shows_every_single_change_that_was_measured():
    matrix = evidence.component_sweep()
    sweep = json.loads((evidence.VALIDATION_DIR / "rounds" / "round-01" / "single_change_sweep.json")
                       .read_text(encoding="utf-8"))
    measured = sum(1 for profiles in sweep["workflows"].values() for p in profiles if p["diff"])
    assert sum(1 for row in matrix["rows"] for cell in row["cells"] if cell is not None) == measured
    col = matrix["workflows"].index("GSM8K")
    step = next(r for r in matrix["rows"] if r["label"].startswith("Prompt: “Work through the problem step by step"))
    assert step["cells"][col] == 64
    ground = next(r for r in matrix["rows"] if r["label"] == "Check a “supports” verdict against the abstract")
    assert ground["cells"][matrix["workflows"].index("SciFact")] == -33          # losses are shown too


def test_g6s_own_variants_are_named_as_g6_and_the_model_alone_is_the_comparator():
    # owner, 2026-10-19: the hand-written prompt runs on G6's own architecture, so it is not a separate category
    assert evidence.ARMS["H"].startswith("G6") and evidence.ARMS["A"] == "The model alone"
    assert {"B", "D", "F", "G", "H"} <= evidence.G6_ARMS and "A" not in evidence.G6_ARMS


def _round(root, n: int, measured: list[str], planned: list[str]) -> None:
    arms = {arm: {"success": 5, "n": 10} for arm in ("A", "B", "H", "F", "D")}
    folder = root / "rounds" / f"round-{n:02d}"
    folder.mkdir(parents=True)
    (folder / "scoreboard.json").write_text(json.dumps({"round": n, "model": "m", "workflows": {k: arms for k in measured}}))
    (folder / "splits.json").write_text(json.dumps({"splits": {k: {} for k in planned}}))


def test_a_round_still_running_shows_as_in_progress_and_is_not_yet_counted(tmp_path, monkeypatch):
    # a round writes its scoreboard after each dataset; the page must not fail while it runs (2026-10-19)
    monkeypatch.setattr(evidence, "VALIDATION_DIR", tmp_path)
    _round(tmp_path, 9, ["gsm8k"], ["gsm8k", "massive"])
    (running,) = evidence.rounds()
    assert running.status == "in progress, 1 of 2 datasets" and "In progress" in running.note


def test_a_finished_round_with_no_write_up_still_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "VALIDATION_DIR", tmp_path)
    _round(tmp_path, 9, ["gsm8k", "massive"], ["gsm8k", "massive"])
    with pytest.raises(LookupError):
        evidence.rounds()


def test_a_missing_results_file_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "VALIDATION_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        evidence.studies()


def test_fixed_arm_and_posthoc_selection_are_separate():
    facts = evidence.headline_facts()
    assert facts["primary_total"] == facts["total"]["D"] and facts["primary_arm"] == "D"
    assert all("post-hoc" in row["selection"] and "unadjusted" in row["p_scope"] for row in evidence.winners())
