# SPDX-License-Identifier: Apache-2.0
"""The frozen comparison (PLAN.md, 2026-10-31), read from saved rows on both scores."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import compare  # noqa: E402

BUGS = [f"bug-{n}" for n in range(1, 11)]


def _rows(loop=(), other=(), *, score="maintainers"):
    """Ten bugs, one repeat each: the loop passes the bugs in ``loop``, arm A those in ``other``."""
    rows = {}
    for bug in BUGS:
        rows[("M7", bug, 1)] = {"maintainers": False, "amended": False, score: bug in loop}
        rows[("A", bug, 1)] = {"maintainers": False, "amended": False, score: bug in other}
    return rows


def test_the_sign_test_is_exact_and_one_sided():
    assert compare.sign_test(5, 0) == pytest.approx(1 / 32)
    assert compare.sign_test(4, 1) == pytest.approx(6 / 32)
    assert compare.sign_test(0, 5) == 1.0
    assert compare.sign_test(0, 0) == 1.0


def test_the_comparison_counts_only_pairs_that_differ():
    got = compare.compare(_rows(BUGS[:6], BUGS[:1]), "M7", "A", BUGS, (1,), "maintainers")
    assert (got["loop_passed"], got["other_passed"], got["wins"], got["losses"]) == (6, 1, 5, 0)
    assert got["uplift"] == pytest.approx(0.5) and got["p"] == pytest.approx(1 / 32) and got["missing"] == 0


def test_the_two_scores_are_read_apart():
    rows = _rows(BUGS[:6], score="amended")
    assert compare.compare(rows, "M7", "A", BUGS, (1,), "amended")["loop_passed"] == 6
    assert compare.compare(rows, "M7", "A", BUGS, (1,), "maintainers")["loop_passed"] == 0


def test_the_rule_needs_more_passes_and_p_below_5_percent_against_every_arm():
    win = compare.compare(_rows(BUGS[:6]), "M7", "A", BUGS, (1,), "maintainers")
    small = compare.compare(_rows(BUGS[:4]), "M7", "A", BUGS, (1,), "maintainers")   # p = 1/16
    assert win["p"] < 0.05 and compare.judged([win])
    assert not compare.judged([small])
    assert not compare.judged([win, small])
    assert not compare.judged([])


def test_the_bar_for_two_primaries_is_stricter_and_the_rule_itself_is_unchanged():
    five = compare.compare(_rows(BUGS[:5]), "M7", "A", BUGS, (1,), "maintainers")   # 5-0: p = 1/32
    six = compare.compare(_rows(BUGS[:6]), "M7", "A", BUGS, (1,), "maintainers")    # 6-0: p = 1/64
    assert compare.judged([five]) and not compare.judged([five], compare.TWO_PRIMARIES)
    assert compare.judged([six], compare.TWO_PRIMARIES)


def test_the_report_says_beside_the_rule_if_a_result_also_clears_the_bar_for_two_primaries(capsys):
    def line(k1):
        rows = _cells(K1=k1, A="a0 b0 c0 d0 e0 f0", T="a0 b0 c0 d0 e0 f0")
        compare.report(rows, "stage 1", "abcdef", (1,), loop_arm="K1")
        return next(text for text in capsys.readouterr().out.splitlines() if "maintainers frozen rule" in text)

    assert "frozen rule: met; at 0.025: not cleared; 95% target: not met (83.3%)" in line("a1 b1 c1 d1 e1 f0")
    assert "frozen rule: met; at 0.025: cleared; 95% target: met (100.0%)" in line("a1 b1 c1 d1 e1 f1")


def test_a_missing_run_is_never_a_win_or_a_loss():
    rows = _rows(BUGS[:6])
    del rows[("A", BUGS[9], 1)]
    got = compare.compare(rows, "M7", "A", BUGS, (1,), "maintainers")
    assert got["pairs"] == 9 and got["missing"] == 1 and not compare.judged([got])


def _cells(**arms):
    """``M9="a11 b10"``: bug a passed twice, bug b once."""
    return {(arm, part[0], i + 1): {"maintainers": flag == "1", "amended": flag == "1"}
            for arm, cells in arms.items() for part in cells.split() for i, flag in enumerate(part[1:])}


def test_each_comparison_counts_the_bugs_as_well_as_the_runs():
    rows = _cells(M9="a11 b10 c00", A="a00 b11 c00")
    assert compare.by_bug(rows, "M9", "A", "abc", (1, 2), "amended") == {"better": 1, "worse": 1, "same": 1}


def test_a_bug_with_a_run_missing_is_counted_neither_way():
    rows = _cells(M9="a11", A="a00")
    del rows[("M9", "a", 2)]
    assert compare.by_bug(rows, "M9", "A", "a", (1, 2), "amended") == {"better": 0, "worse": 0, "same": 0}


def test_the_report_prints_the_bug_count_beside_the_rule(capsys):
    compare.report(_cells(M9="a11 b11", A="a00 b00", T="a00 b00"), "stage 2", "ab", (1, 2))
    assert "bugs better 2 worse 0 same 0" in capsys.readouterr().out


def test_the_control_is_reported_beside_the_rule_and_cannot_change_it(capsys):
    """Arm R is a report, by the plan: the rule stays M9 against A and T, so R beating M9 leaves the rule met."""
    rows = _cells(M9="a11 b11 c11 d11 e11", A="a00 b00 c00 d00 e00", T="a00 b00 c00 d00 e00", R="a11 b11 c11 d11 e11")
    compare.report(rows, "stage 2", "abcde", (1, 2))
    out = capsys.readouterr().out
    assert sum(" M9 " in line and " R " in line and "uplift" in line and "report only" in line for line in out.splitlines()) == 2
    assert "frozen rule: met" in out


def test_trust_counts_what_the_system_said_against_the_verdict():
    rows = {("M9", "a", 1): {"amended": True, "said": "clean"}, ("M9", "a", 2): {"amended": False, "said": "clean"},
            ("M9", "b", 1): {"amended": True, "said": "unsure"}, ("M9", "b", 2): {"amended": None, "said": "unsure"}}
    assert compare.trust(rows, "M9", "ab", (1, 2), "amended") == {"clean": {"right": 1, "wrong": 1}, "unsure": {"right": 1, "wrong": 0}}


def test_the_report_says_how_often_done_meant_right(capsys):
    rows = {("M9", "a", 1): {"maintainers": True, "amended": True, "said": "clean"},
            ("M9", "a", 2): {"maintainers": False, "amended": False, "said": "clean"},
            ("A", "a", 1): {"maintainers": True, "amended": True, "said": "done"},
            ("A", "a", 2): {"maintainers": False, "amended": False, "said": "done"}}
    compare.report(rows, "stage 2", "a", (1, 2), others=("A",))
    assert "M9 said clean: right 1 wrong 1 (50%)" in capsys.readouterr().out


def test_what_a_run_said_comes_from_its_own_record(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "run.json").write_text(json.dumps({"repairs": {"status": "no_counterexample_in_domain"}}), "utf-8")
    assert compare.said_of(tmp_path, {"arm": "M9", "run": "run"}) == "clean"
    (tmp_path / "run" / "run.json").write_text(json.dumps({"repairs": {"status": "inconclusive"}}), "utf-8")
    assert compare.said_of(tmp_path, {"arm": "M9", "run": "run"}) == "unsure"
    assert compare.said_of(tmp_path, {"arm": "A", "run": "run"}) == "done"
    assert compare.said_of(tmp_path, {"arm": "M9", "run": "missing"}) == "unsure"


def test_a_k_run_says_clean_or_unsure_from_its_own_record_and_not_a_plain_done(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "run.json").write_text(json.dumps({"repairs": {"status": compare.CLEAN}}), "utf-8")
    assert compare.said_of(tmp_path, {"arm": "K1", "run": "run"}) == "clean"
    (tmp_path / "run" / "run.json").write_text(json.dumps({"repairs": {"status": "inconclusive"}}), "utf-8")
    assert compare.said_of(tmp_path, {"arm": "K1", "run": "run"}) == "unsure"


def _k_run(root, verdicts, *, status=compare.CLEAN, reason="a review reproduced nothing", **checks):
    """A saved run of arm K: ``checks`` are its rounds' test runs, ``round_2={"exit": 0, ...}``."""
    (root / "run").mkdir()
    repairs = {"status": status, "rounds": len(verdicts), "reason": reason, "critic": {"verdicts": verdicts}}
    (root / "run" / "run.json").write_text(json.dumps({"repairs": repairs}), "utf-8")
    for name, body in checks.items():
        (root / "run" / f"repair_check_{name.removeprefix('round_')}_critic.json").write_text(json.dumps(body), "utf-8")
    return {"arm": "K1", "run": "run"}


WOULD_NOT_RUN, FOUND_NOTHING = {"exit": 2, "ran": 0, "results": {}}, {"exit": 0, "ran": 1, "results": {"test_critic::t": "passed"}}


def test_a_k_run_that_found_nothing_ended_clean(tmp_path):
    assert compare.end_of(tmp_path, _k_run(tmp_path, ["none"])) == "nothing found"


def test_a_defect_with_no_test_is_counted_apart_from_a_clean_end(tmp_path):
    assert compare.end_of(tmp_path, _k_run(tmp_path, ["defect"])) == "a defect with no test"


def test_a_defect_whose_test_would_not_run_is_counted_apart_from_a_clean_end(tmp_path):
    assert compare.end_of(tmp_path, _k_run(tmp_path, ["defect"], round_1=WOULD_NOT_RUN)) == "a defect whose test would not run"


def test_a_defect_whose_test_ran_and_found_nothing_is_named_as_that(tmp_path):
    assert compare.end_of(tmp_path, _k_run(tmp_path, ["defect"], round_1=FOUND_NOTHING)) == "a defect whose test found nothing"


def test_it_is_the_last_review_that_says_how_a_k_run_ended(tmp_path):
    row = _k_run(tmp_path, ["defect", "defect"], round_1=FOUND_NOTHING, round_2=WOULD_NOT_RUN)
    assert compare.end_of(tmp_path, row) == "a defect whose test would not run"


def test_an_earlier_reviews_pass_does_not_stand_for_the_last_reviews_own_test(tmp_path):
    check = {"exit": 0, "results": {"test_critic::test_first": "passed", "test_critic_2::test_second": "skipped"}}
    assert compare.end_of(tmp_path, _k_run(tmp_path, ["defect", "defect"], round_2=check)) == "a defect whose test would not run"


def test_a_last_review_with_no_test_of_its_own_on_the_record_did_not_run_one(tmp_path):
    check = {"exit": 0, "results": {"test_critic::test_first": "passed"}}
    assert compare.end_of(tmp_path, _k_run(tmp_path, ["defect", "defect"], round_2=check)) == "a defect whose test would not run"


def test_a_last_reviews_own_test_that_ran_found_nothing_whatever_an_earlier_one_did(tmp_path):
    check = {"exit": 0, "results": {"test_critic::test_first": "skipped", "test_critic_2::test_second": "passed"}}
    assert compare.end_of(tmp_path, _k_run(tmp_path, ["defect", "defect"], round_2=check)) == "a defect whose test found nothing"


def test_a_last_reviews_test_written_in_a_class_is_its_own(tmp_path):
    check = {"exit": 0, "results": {"test_critic.TestFix::test_it": "passed"}}
    assert compare.end_of(tmp_path, _k_run(tmp_path, ["defect"], round_1=check)) == "a defect whose test found nothing"


def test_a_class_in_an_earlier_reviews_test_does_not_stand_for_the_last_ones(tmp_path):
    check = {"exit": 0, "results": {"test_critic.TestFix::test_it": "passed", "test_critic_2.TestMore::test_it": "skipped"}}
    assert compare.end_of(tmp_path, _k_run(tmp_path, ["defect", "defect"], round_2=check)) == "a defect whose test would not run"


def test_a_k_run_with_a_defect_open_gives_the_reason_on_its_record(tmp_path):
    row = _k_run(tmp_path, ["defect"] * 4, status="inconclusive", reason="the repairs ran out with a defect open")
    assert compare.end_of(tmp_path, row) == "the repairs ran out with a defect open"


def test_a_k_run_with_no_record_says_so_and_no_other_arm_has_an_end(tmp_path):
    assert compare.end_of(tmp_path, {"arm": "K1", "run": "missing"}) == "no record"
    assert compare.end_of(tmp_path, {"arm": "M9", "run": "missing"}) is None


def test_a_run_record_a_killed_run_cut_short_is_no_record_and_says_unsure(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "run.json").write_text('{"repairs": {"sta', "utf-8")
    assert compare.end_of(tmp_path, {"arm": "K1", "run": "run"}) == "no record"
    assert compare.said_of(tmp_path, {"arm": "K1", "run": "run"}) == "unsure"
    assert compare.said_of(tmp_path, {"arm": "M9", "run": "run"}) == "unsure"


def test_a_test_record_a_killed_run_cut_short_is_no_record(tmp_path):
    row = _k_run(tmp_path, ["defect"])
    (tmp_path / "run" / "repair_check_1_critic.json").write_text('{"exit": 0, "re', "utf-8")
    assert compare.end_of(tmp_path, row) == "no record"


def test_a_saved_k_run_carries_its_end_and_no_other_arm_gains_a_field(tmp_path, monkeypatch):
    _archive(tmp_path, monkeypatch, {"arm": "K1", "task": "y-1", "passes": True, "run": "run"}, {"arm": "A", "task": "y-1", "passes": True})
    (tmp_path / "run").mkdir(exist_ok=True)              # _archive made the run's folder and record; this is its record in full
    (tmp_path / "run" / "run.json").write_text(json.dumps({"status": "finished", "repairs": {"status": compare.CLEAN, "critic": {
        "verdicts": ["none"]}}}), "utf-8")
    rows = compare.saved(tmp_path)
    assert rows[("K1", "y-1", 1)]["end"] == "nothing found" and "end" not in rows[("A", "y-1", 1)]


def test_the_report_counts_each_way_a_k_run_ended_on_each_score(capsys):
    rows = {("K1", "a", 1): {"maintainers": True, "amended": True, "said": "clean", "end": "a defect whose test would not run"},
            ("K1", "a", 2): {"maintainers": False, "amended": True, "said": "clean", "end": "a defect whose test would not run"},
            ("A", "a", 1): {"maintainers": False, "amended": False, "said": "done"},
            ("A", "a", 2): {"maintainers": False, "amended": False, "said": "done"}}
    compare.report(rows, "stage 1", "a", (1, 2), loop_arm="K1", others=("A",))
    out = capsys.readouterr().out
    assert "maintainers K1 last review a defect whose test would not run: right 1 wrong 1 (50%)" in out
    assert "amended     K1 last review a defect whose test would not run: right 2 wrong 0 (100%)" in out


def test_the_interval_resamples_whole_bugs():
    clear = compare.interval(_cells(M9="a11 b11 c11", A="a00 b00 c00"), "M9", "A", "abc", (1, 2), "amended")
    mixed = compare.interval(_cells(M9="a11 b00 c11", A="a00 b11 c00"), "M9", "A", "abc", (1, 2), "amended")
    assert clear == (1.0, 1.0) and mixed[0] < 0 < mixed[1]


def test_no_runs_give_no_interval():
    assert compare.interval({}, "M9", "A", "abc", (1, 2), "amended") is None


def test_stage_3_compares_the_trained_arm_with_its_untrained_parent_too(capsys):
    rows = _cells(M10="a11", M9="a00", A="a00", T="a00")
    compare.report(rows, "stage 3", "a", (1, 2), loop_arm="M10", others=("M9", "A", "T"))
    assert sum(" M10 " in line and "uplift" in line for line in capsys.readouterr().out.splitlines()) == 6


def _main(monkeypatch, tmp_path, rows, *argv):
    """The whole report, on these rows, with one fresh bug ``f``, one unseen ``u``, one held out ``h`` and one training bug ``t``."""
    tasks = {compare.we.TASKS_FRESH: ["f"], compare.we.TASKS_UNSEEN: ["u"], None: ["t", "h"]}
    monkeypatch.setattr(compare.c, "archive_root", lambda: tmp_path)
    monkeypatch.setattr(compare, "saved", lambda root: rows)
    monkeypatch.setattr(compare.we, "R3_HELD", ("h",))
    monkeypatch.setattr(compare.we, "load_tasks", lambda path=None: [type("Task", (), {"id": t}) for t in tasks[path]])
    compare.main(list(argv))


def test_k1_is_judged_in_sections_of_its_own_and_never_pooled_with_m9(tmp_path, monkeypatch, capsys):
    wins, none = "f11 u11 t11 h11111", "f00 u00 t00 h00000"
    _main(monkeypatch, tmp_path, _cells(K1=wins, M9=none, L9=none, A=none, T=none, R=none))
    lines = capsys.readouterr().out.splitlines()
    for section in ("K1, stage 1, all eleven", "K1, stage 1, determined 7", "K1, stage 2, new repos", "K1, held-out, second look",
                    "K1, training, tuned"):
        assert any(line.startswith(section) for line in lines), section
    judged = [line for line in lines if " uplift " in line and "report only" not in line]
    assert len(judged) == 52      # each primary, on two scores: five sections and failure-rich against A and T, one tuned against A
    assert all(" K1 " in line for line in judged if line.startswith("K1, "))
    assert not any(" K1 " in line for line in judged if not line.startswith("K1, "))
    for section, shown in (("K1, stage 1, all eleven", "K1   2/2"), ("K1, stage 2, new repos", "K1   2/2"),
                           ("K1, held-out, second look", "K1   5/5"), ("K1, training, tuned", "K1   2/2"),
                           ("stage 1, all eleven", "M9   0/2"), ("stage 2, new repos", "M9   0/2"),
                           ("held-out, second look", "M9   0/5"), ("training, tuned", "L9   0/2")):
        picked = [line for line in judged if line.startswith(section)]
        assert picked and all(shown in line for line in picked), section


def test_the_rule_gives_no_verdict_on_bugs_an_arm_was_tuned_on(tmp_path, monkeypatch, capsys):
    """Seen bugs are development: however well a tuned section reads, it is no result of the rule."""
    _main(monkeypatch, tmp_path, _cells(K1="t11", L9="t11", A="t00", T="t00"))
    lines = [line for line in capsys.readouterr().out.splitlines() if "training, tuned" in line and "frozen rule" in line]
    assert len(lines) == 4 and all("frozen rule: not applied, tuned on these bugs" in line for line in lines)
    assert not any("cleared" in line or "frozen rule: met" in line for line in lines)


def test_m9_is_only_a_report_in_k1s_sections(tmp_path, monkeypatch, capsys):
    rows = _cells(K1="f11", M9="f00", A="f00", T="f00", R="f00")
    _main(monkeypatch, tmp_path, rows)
    k1 = [line for line in capsys.readouterr().out.splitlines() if line.startswith("K1, stage 1, all eleven") and " uplift " in line]
    assert all("report only" in line for line in k1 if " M9 " in line) and any(" M9 " in line for line in k1)


def test_stage_3_is_judged_against_k1_its_parent_as_well_as_a_and_t(tmp_path, monkeypatch, capsys):
    rows = _cells(P1="u11", K1="u00", M9="u00", A="u00", T="u00", R="u00")
    _main(monkeypatch, tmp_path, rows, "--trained", "P1")
    judged = [line for line in capsys.readouterr().out.splitlines()
              if line.startswith("stage 3, new repos") and " uplift " in line and "report only" not in line]
    assert sum(" P1 " in line for line in judged) == 6 and sum(" K1 " in line for line in judged) == 2
    assert not any(" M9 " in line for line in judged)


def test_stage_2s_determined_bugs_are_reported_beside_all_of_them_and_read_the_rule_on_their_own(tmp_path, monkeypatch, capsys):
    determined, other = compare.DETERMINED_2[0], "charset-normalizer-715"
    rows = {(arm, task, repeat): {"maintainers": arm == "K1", "amended": arm == "K1"} for arm in ("M9", "K1", "A", "T", "R")
            for task in (determined, other) for repeat in (1, 2)}
    tasks = {compare.we.TASKS_FRESH: [], compare.we.TASKS_UNSEEN: [determined, other], None: []}
    monkeypatch.setattr(compare.c, "archive_root", lambda: tmp_path)
    monkeypatch.setattr(compare, "saved", lambda root: rows)
    monkeypatch.setattr(compare.we, "R3_HELD", ())
    monkeypatch.setattr(compare.we, "load_tasks", lambda path=None: [type("Task", (), {"id": t}) for t in tasks[path]])
    compare.main([])
    lines = capsys.readouterr().out.splitlines()
    assert other not in compare.DETERMINED_2
    assert any(line.startswith("K1, stage 2, determined") and "K1   2/2" in line for line in lines)
    assert any(line.startswith("K1, stage 2, new repos") and "K1   4/4" in line for line in lines)
    assert any(line.startswith("stage 2, determined") and "M9   0/2" in line for line in lines)


def test_stage_3_gets_no_verdict_on_stage_1_bugs_which_are_development_for_it(tmp_path, monkeypatch, capsys):
    """PLAN.md, 2026-11-02: a scored bug is development for every later generation, so a win there is a tuned score."""
    _main(monkeypatch, tmp_path, _cells(P1="u11 f11", K1="u00 f00", M9="u00 f00", A="u00 f00", T="u00 f00", R="u00 f00"), "--trained", "P1")
    lines = capsys.readouterr().out.splitlines()
    tuned = [line for line in lines if line.startswith("stage 3, stage 1 bugs") and "frozen rule" in line]
    assert len(tuned) == 2 and all("frozen rule: not applied, tuned on these bugs" in line for line in tuned)
    unseen = [line for line in lines if line.startswith("stage 3, new repos") and "frozen rule" in line]
    assert len(unseen) == 2 and not any("not applied" in line for line in unseen)


def test_k0_is_a_report_and_the_rule_gives_it_no_verdict(tmp_path, monkeypatch, capsys):
    """PLAN.md, 2026-11-02: it was chosen after stage 1 was read, so however well it reads it is a hypothesis for a fresh test."""
    _main(monkeypatch, tmp_path, _cells(K0="u11", K1="u00", M9="u00", A="u00", T="u00", R="u00"))
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("K0, stage 2, exploratory")]
    assert sum("K0   2/2" in line for line in lines) == 2 * 5          # against A, T, R, M9 and K1, on two scores
    verdicts = [line for line in lines if "frozen rule" in line]
    assert len(verdicts) == 2 and all("frozen rule: not applied, exploratory" in line for line in verdicts)
    assert not any("cleared" in line or "frozen rule: met" in line for line in verdicts)


@pytest.mark.parametrize("flag", ["--loop", "--second", "--trained"])
def test_k0_is_refused_as_a_primary_so_it_gets_no_verdict_and_chooses_no_bugs(flag, capsys):
    with pytest.raises(SystemExit):
        compare.main([flag, "K0"])
    assert "never a primary" in capsys.readouterr().err


def test_k0_gets_no_verdict_from_report_however_it_reads(capsys):
    lost = "u00 v00 w00"
    rows = _cells(K0="u11 v11 w11", A=lost, T=lost)      # six wins and no loss would clear the rule at either bar
    compare.report(rows, "direct", "uvw", (1, 2), loop_arm="K0", scores=("amended",))
    lines = [line for line in capsys.readouterr().out.splitlines() if "frozen rule" in line]
    assert len(lines) == 1 and "frozen rule: not applied, exploratory" in lines[0]


@pytest.mark.parametrize("flag", ["--loop", "--second"])
def test_a_later_arm_gets_no_verdict_on_stage_1_bugs_or_held_out_bugs(tmp_path, monkeypatch, capsys, flag):
    lost = "f00 g00 h00 x00000"
    rows = _cells(M10="f11 g11 h11 x11111", M9=lost, K1=lost, A=lost, T=lost, R=lost)
    tasks = {compare.we.TASKS_FRESH: ["f", "g", "h"], compare.we.TASKS_UNSEEN: [], None: ["x"]}
    monkeypatch.setattr(compare.c, "archive_root", lambda: tmp_path)
    monkeypatch.setattr(compare, "saved", lambda root: rows)
    monkeypatch.setattr(compare.we, "R3_HELD", ("x",))
    monkeypatch.setattr(compare.we, "load_tasks", lambda path=None: [type("Task", (), {"id": t}) for t in tasks[path]])
    compare.main([flag, "M10"])
    lines = [line for line in capsys.readouterr().out.splitlines() if "frozen rule" in line and "M10" not in line.split("frozen rule")[1]]
    named = "" if flag == "--loop" else "M10, "
    for section in ("stage 1, all eleven", "held-out, second look"):
        picked = [line for line in lines if line.startswith(named + section)]
        assert len(picked) == 2 and all("frozen rule: not applied, tuned on these bugs" in line for line in picked), section


def test_k0_is_never_what_decides_which_bugs_finished(tmp_path, monkeypatch, capsys):
    _main(monkeypatch, tmp_path, _cells(K1="u11", M9="u11", A="u00", T="u00", R="u00"))
    lines = capsys.readouterr().out.splitlines()
    assert "stage 2: 1 of 1 bugs finished, in the frozen order" in lines
    assert not any(line.startswith("K0, ") for line in lines)


FROZEN = ("M9", "K1", "A", "T", "R")


def test_the_bugs_that_finished_are_read_in_the_frozen_order_and_stop_at_the_first_that_did_not():
    rows = _cells(**{arm: "a11 b11 d11" for arm in FROZEN})
    assert compare.finished(rows, "abcd", FROZEN, (1, 2)) == ["a", "b"]
    assert compare.finished({}, "abcd", FROZEN, (1, 2)) == []


def test_a_bug_one_arm_or_one_repeat_did_not_reach_has_not_finished():
    rows = _cells(**{arm: "a11 b11" for arm in FROZEN})
    del rows[("R", "b", 2)]
    assert compare.finished(rows, "ab", FROZEN, (1, 2)) == ["a"]


def test_which_bugs_finished_does_not_depend_on_how_they_came_out():
    won, lost = _cells(**{arm: "a11 b11" for arm in FROZEN}), _cells(**{arm: "a00 b00" for arm in FROZEN})
    assert compare.finished(won, "ab", FROZEN, (1, 2)) == compare.finished(lost, "ab", FROZEN, (1, 2)) == ["a", "b"]


def _stopped(monkeypatch, tmp_path, *argv):
    """Stage 2 stopped after bug ``u``: ``v`` never started, and ``w`` has runs but comes after it in the frozen order."""
    cells = {arm: "u00 w00" for arm in ("M9", "A", "T", "R")} | {"K1": "u11 w00", "P1": "u11 w00"}
    tasks = {compare.we.TASKS_FRESH: [], compare.we.TASKS_UNSEEN: ["u", "v", "w"], None: []}
    monkeypatch.setattr(compare.c, "archive_root", lambda: tmp_path)
    monkeypatch.setattr(compare, "saved", lambda root: _cells(**cells))
    monkeypatch.setattr(compare.we, "R3_HELD", ())
    monkeypatch.setattr(compare.we, "load_tasks", lambda path=None: [type("Task", (), {"id": t}) for t in tasks[path]])
    compare.main(list(argv))


def test_stage_2_reads_only_the_bugs_that_finished_and_says_how_many(tmp_path, monkeypatch, capsys):
    _stopped(monkeypatch, tmp_path)
    lines = capsys.readouterr().out.splitlines()
    assert "stage 2: 1 of 3 bugs finished, in the frozen order" in lines
    k1 = [line for line in lines if line.startswith("K1, stage 2, new repos") and " uplift " in line and "report only" not in line]
    assert k1 and all("K1   2/2" in line and "MISSING" not in line for line in k1)
    m9 = [line for line in lines if line.startswith("stage 2, new repos") and " uplift " in line and "report only" not in line]
    assert m9 and all("M9   0/2" in line and "MISSING" not in line for line in m9)


def test_stage_3_reads_the_stage_2_bugs_that_finished_and_its_arm_reached(tmp_path, monkeypatch, capsys):
    _stopped(monkeypatch, tmp_path, "--trained", "P1")
    lines = capsys.readouterr().out.splitlines()
    assert "stage 3: 1 of 1 bugs finished, in the frozen order" in lines
    judged = [line for line in lines if line.startswith("stage 3, new repos") and " uplift " in line and "report only" not in line]
    assert judged and all("P1   2/2" in line and "MISSING" not in line for line in judged)


def _archive(root, monkeypatch, *rows):
    (root / "loop" / "amended").mkdir(parents=True)
    diff = root / "x-1.patch"
    diff.write_text("a diff", encoding="utf-8")
    monkeypatch.setattr(compare.we, "amendment", lambda task: diff if task == "x-1" else None)
    for row in rows:     # a saved row names how its run ended, as every row ``loop.one`` saves does: no status, no verdict
        row = {"repeat": 1, "run": f"run/{row['task']}/{row['arm']}", "status": "finished", "failing": 0 if row.get("passes") else 1,
               **row}
        (root / "loop" / f"{row['arm']}-{row['task']}-1.json").write_text(json.dumps(row), "utf-8")
        made = root / row["run"]    # and the run's own record in the archive, which the admission rule reads (run.unscored_why)
        made.mkdir(parents=True, exist_ok=True)
        if not (made / "run.json").exists():
            (made / "run.json").write_text(json.dumps({"status": row["status"], "agent_started": True}), "utf-8")
    (root / "loop" / "cegis.json").write_text('{"status": "inconclusive"}', "utf-8")
    return compare.c.sha256_file(diff)


def _amended(root, arm, sha, *, run=None, passes=True):
    (root / "loop" / "amended" / f"{arm}-x-1-1.json").write_text(json.dumps(
        {"amended_passes": passes, "amendment_sha256": sha, "run": run or f"run/x-1/{arm}"}), "utf-8")


def test_an_amended_bug_not_yet_rescored_counts_as_missing_not_as_its_old_verdict(tmp_path, monkeypatch):
    sha = _archive(tmp_path, monkeypatch, {"arm": "A", "task": "x-1", "passes": True}, {"arm": "A", "task": "y-1", "passes": True},
                   {"arm": "M7", "task": "x-1", "passes": False})
    _amended(tmp_path, "M7", sha)
    rows = compare.saved(tmp_path)
    assert rows[("A", "y-1", 1)] == {"maintainers": True, "amended": True, "said": "done"}      # no amendment: one score
    assert rows[("M7", "x-1", 1)] == {"maintainers": False, "amended": True, "said": "unsure"}
    assert rows[("A", "x-1", 1)] == {"maintainers": True, "amended": None, "said": "done"}
    assert compare.compare(rows, "M7", "A", ["x-1"], (1,), "amended")["missing"] == 1


def test_a_verdict_from_another_diff_or_another_run_is_missing(tmp_path, monkeypatch):
    sha = _archive(tmp_path, monkeypatch, {"arm": "A", "task": "x-1", "passes": True}, {"arm": "M7", "task": "x-1", "passes": False})
    _amended(tmp_path, "A", "not-this-diff")
    _amended(tmp_path, "M7", sha, run="run/x-1/another")
    rows = compare.saved(tmp_path)
    assert rows[("A", "x-1", 1)]["amended"] is None and rows[("M7", "x-1", 1)]["amended"] is None


def test_a_finished_run_with_no_independent_verdict_is_missing_not_a_failure(tmp_path, monkeypatch):
    _archive(tmp_path, monkeypatch, {"arm": "A", "task": "y-1", "passes": False, "status": "finished", "failing": None},
             {"arm": "M7", "task": "y-1", "passes": False, "status": "error", "failing": None},
             {"arm": "T", "task": "y-1", "passes": False, "status": "finished", "failing": 3})
    rows = compare.saved(tmp_path)
    assert rows[("A", "y-1", 1)] == {"maintainers": None, "amended": None, "said": "done"}
    assert rows[("M7", "y-1", 1)]["maintainers"] is False                       # a run that ended badly did fail
    assert rows[("T", "y-1", 1)]["maintainers"] is False
    five_missing = {("M7", f"b{n}", 1): {"maintainers": True} for n in range(5)}
    five_missing.update({("A", f"b{n}", 1): rows[("A", "y-1", 1)] for n in range(5)})
    got = compare.compare(five_missing, "M7", "A", [f"b{n}" for n in range(5)], (1,), "maintainers")
    assert got["missing"] == 5 and got["pairs"] == 0 and not compare.judged([got])


def test_the_seven_determined_fresh_bugs_are_the_audits_labels():
    fresh = [t.id for t in compare.we.load_tasks(compare.we.TASKS_FRESH)]
    assert len(fresh) == 11 and set(compare.DETERMINED) <= set(fresh) and len(compare.DETERMINED) == 7
    assert not {"black-5144", "attrs-1509", "starlette-3166", "pydantic-13133"} & set(compare.DETERMINED)
