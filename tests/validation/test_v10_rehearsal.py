# SPDX-License-Identifier: Apache-2.0
"""The paid rehearsal's plumbing (``validation/patch_review_search/rehearsal.py``): its development fixtures and freeze
file, the confined test command, the dollar-stopped caller G6's arms use, and arms C, T and E as the driver calls them.

Development fixtures only (two small applications written here, disjoint from every pool); no model is called: the
caller and G6's review are fakes."""
from __future__ import annotations

import json

import pytest

from patch_review_search import driver, launcher, rehearsal, study
from patch_review_search.metering import Meter
from patch_review_utility import dispositions, draw


def test_the_fixtures_build_a_development_freeze_in_draw_order_with_known_answers(tmp_path):
    rehearsal.build(tmp_path)
    freeze = study.load_freeze(tmp_path / "freeze.json")
    assert freeze["level"] == "development" and freeze["v9"]["seed"] == rehearsal.SEED
    rows = json.loads((tmp_path / "pool.json").read_text(encoding="utf-8"))
    assert [t["task_id"] for t in freeze["v9"]["tasks"]] == draw.order(rows, seed=rehearsal.SEED)
    assert {t["task_id"] for t in freeze["v9"]["tasks"]} == set(rehearsal.FIXTURES)
    assert all(not any(word in t["task_id"] for word in ("qutebrowser", "openlibrary", "ansible"))
               for t in freeze["v9"]["tasks"])
    assert freeze["v9"]["executor"]["confined"] is True and freeze["v9"]["executor"]["network"] is False
    assert freeze["v10"]["bindings"] == {arm: f"patch_review_search.rehearsal:arm_{arm.lower()}" for arm in "CTE"}
    # the rehearsal's first fault: with no .gitignore, the agent's pytest run left bytecode that V7's capture took as
    # binary stubs git cannot apply; every fixture ignores it, as a real repository does
    for task_id in rehearsal.FIXTURES:
        ignored = (tmp_path / "sources" / task_id / ".gitignore").read_text(encoding="utf-8").split()
        assert {"__pycache__/", "*.pyc", ".pytest_cache/"} <= set(ignored), task_id
    assert freeze["v9"]["suites"] == {f["repository"]: f["suite"] for f in rehearsal.FIXTURES.values()}
    contract = json.loads((tmp_path / "vault" / "dev-wallet" / "contract.json").read_text(encoding="utf-8"))
    assert contract["groups"][0]["fail_to_pass"] and (tmp_path / "vault" / "dev-wallet" / "reference.diff").is_file()


def test_every_test_run_is_confined_with_no_network_and_no_home_folder():
    command = list(rehearsal.CONFINED)
    assert command[0] == "bwrap" and "--unshare-all" in command and "--die-with-parent" in command
    bound = {command[i + 1] for i, word in enumerate(command) if word in ("--bind", "--ro-bind", "--ro-bind-try")}
    assert "/root" not in bound and "/" not in bound and "/home" not in bound
    assert command[command.index("--bind") + 1] == "{tree}" and "{tests}" in command


class FakeCaller:
    model = "fake-model"

    def __init__(self, reply="{}", usage=None):
        self.reply, self.last_usage = reply, usage or {"input_tokens": 1000, "output_tokens": 100}
        self.calls = []

    def complete(self, system, user, *, max_tokens=2048):
        self.calls.append((system, user, max_tokens))
        return self.reply


def test_the_callers_stop_counts_the_worst_case_first_and_charges_the_ledger_and_the_meter(tmp_path):
    ledger = launcher.Ledger(tmp_path / "ledger.json", cap=0.01)
    meter = Meter()
    caller = rehearsal.StoppedCaller(FakeCaller(), ledger, meter, share_left=lambda: True)
    caller.complete("s" * 10, "u" * 10, max_tokens=100)
    cost = (1000 * rehearsal.PRICE_G6["input"] + 100 * rehearsal.PRICE_G6["output"]) / 1e6
    assert ledger.spent == pytest.approx(cost) and meter.reading()["cents"] == pytest.approx(cost * 100)
    assert meter.reading()["calls"] == 1
    assert meter.reading()["tokens"] == {"input_tokens": 1000, "output_tokens": 100}   # measure 6, as reported
    with pytest.raises(rehearsal.LLMError, match="dollar stop"):
        caller.complete("s", "u", max_tokens=4000)            # its worst case would pass the cap
    blocked = rehearsal.StoppedCaller(FakeCaller(), ledger, Meter(), share_left=lambda: False)
    with pytest.raises(rehearsal.LLMError, match="share"):
        blocked.complete("s", "u")


def test_the_key_check_needs_the_file_present_unconfined_and_unreachable_from_both_confined_runs(tmp_path,
                                                                                                    monkeypatch):
    class Ran:
        def __init__(self, outcome):
            self.outcome = outcome

        def run(self, tree, tests, **kwargs):
            return {"outcomes": {tests[0] + "::test_the_key_file_is_out_of_reach": self.outcome}}

    def g6(result):
        return lambda tree, **kwargs: {"assessment": {"completion": {"activities": [
            {"activity": "proposed check", "check": "key", "result": result}]}}}

    for present, confined, proposed, ok in ((True, "passed", "not_refuted", True), (False, "passed", "not_refuted", False),
                                            (True, "failed", "not_refuted", False),
                                            (True, "passed", "fails_on_base_too", False)):
        monkeypatch.setattr(rehearsal, "review_patch", g6(proposed))
        got = rehearsal.key_check(tmp_path / f"k{present}{confined}{proposed}", executor=Ran(confined),
                                  present=lambda: present)
        assert got["ok"] is ok, (present, confined, proposed)
    assert "open(" in rehearsal.KEY_CHECK and rehearsal.KEY_PATH in rehearsal.KEY_CHECK


def test_the_paid_steps_run_each_task_once_in_draw_order_and_arm_r_in_the_host_path_of_its_copy(tmp_path,
                                                                                                    monkeypatch):
    rehearsal.build(tmp_path)
    ran = []
    monkeypatch.setattr(launcher, "patch_and_self_review", lambda task_id, **kw: ran.append(("patch", task_id)) or {})
    monkeypatch.setattr(launcher, "review_r", lambda task_id, **kw: ran.append(("r", task_id, kw["copy"])) or {})
    ledger = launcher.Ledger(tmp_path / "ledger.json", cap=15.0)
    monkeypatch.setattr(rehearsal, "_paid_context", lambda run, assets: {"docker": None, "assets": None,
                                                                          "key": "made-up", "ledger": ledger})
    (tmp_path / "patches").mkdir()
    (tmp_path / "patches" / "dev-wallet.diff").write_text("", encoding="utf-8")       # attempted already
    rehearsal.patches(tmp_path, assets=None, image="img")
    assert ran == [("patch", "dev-codec")]
    prepared = tmp_path / "prepared" / "arm_r"
    prepared.mkdir(parents=True)
    (prepared / "dev-codec.json").write_text(json.dumps({"task_id": "dev-codec", "copy": "/work/prepared/copies/c"}),
                                             encoding="utf-8")
    rehearsal.reviews(tmp_path, assets=None, image="img")
    assert ran[-1] == ("r", "dev-codec", tmp_path / "prepared" / "copies" / "c")


def test_no_r_session_starts_that_rs_share_cannot_cover_at_its_worst(tmp_path, monkeypatch):
    # ruling on items 68 and 71: R is charged only when judged, so its share is checked before each session
    rehearsal.build(tmp_path)
    ledger = launcher.Ledger(tmp_path / "ledger.json", cap=15.0)
    share = 1500 / 100 / 4                                                   # a quarter of V10's cap, in dollars
    held = ledger.reserve("review_r:earlier", share - 1.0)
    ledger.settle(held, share - 1.0, {})                                     # R has spent all but a dollar
    ran = []
    monkeypatch.setattr(launcher, "review_r", lambda task_id, **kw: ran.append(task_id) or {})
    monkeypatch.setattr(rehearsal, "_paid_context", lambda run, assets: {"docker": None, "assets": None,
                                                                          "key": "made-up", "ledger": ledger})
    prepared = tmp_path / "prepared" / "arm_r"
    prepared.mkdir(parents=True)
    (prepared / "dev-codec.json").write_text(json.dumps({"task_id": "dev-codec", "copy": "/work/prepared/copies/c"}),
                                             encoding="utf-8")
    with pytest.raises(launcher.Stopped, match="share"):
        rehearsal.reviews(tmp_path, assets=None, image="img")
    assert ran == [] and json.loads((tmp_path / "freeze.json").read_text(encoding="utf-8"))["v10"]["cap_cents"] == 1500


def test_the_cost_record_gives_each_session_and_arm_and_leaves_another_models_sessions_out(tmp_path):
    def session(folder, task, kind, model, accounted):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
        (tmp_path / folder / f"{task}.json").write_text(json.dumps({
            "task_id": task, "kind": kind, "model": model, "model_calls": 3, "elapsed_s": 9.0, "dollars": accounted * 1.4,
            "accounted_dollars": accounted, "usage": {"input_tokens": 10, "output_tokens": 5,
                                                      "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20},
            "cache_writes": {"ephemeral_5m_input_tokens": 20, "ephemeral_1h_input_tokens": 0}}), encoding="utf-8")
    session("patch-sessions", "t1", "patch", launcher.MODEL, 0.04)
    session("sessions", "t1", "self_review", launcher.MODEL, 0.01)
    session("r-sessions", "t1", "review_r", "claude-sonnet-5-5", 0.05)
    (tmp_path / "arms.json").write_text(json.dumps({"v10": {"patches": [{"patch": "t1", "arms": {
        arm: {"meter": {"calls": 2, "cents": 1.5}, "seconds": 30.0, "disposition": {"category": "accept"}}
        for arm in "CTER"}}]}}), encoding="utf-8")
    got = rehearsal.costs(tmp_path)
    by = {(row["kind"], row["task"]): row for row in got["sessions"]}
    assert by[("patch", "t1")]["accounted_dollars"] == 0.04 and by[("patch", "t1")]["tokens"]["cache_read_input_tokens"] == 100
    assert by[("review_r", "t1")]["study_model"] is False                    # not the study's model: kept, labelled
    assert [row["kind"] for row in got["projection_input"]["sessions"]] == ["patch", "self_review"]
    assert got["arms"]["C"] == [{"task": "t1", "calls": 2, "dollars": 0.015, "seconds": 30.0}]
    assert got["kind"] == "rehearsal on development fixtures, not study data"


def test_the_committed_record_summarises_each_run_labels_other_models_and_names_no_path(tmp_path):
    run = tmp_path / "runA"
    (run / "patch-sessions").mkdir(parents=True)
    session = {"kind": "patch", "task_id": "dev-codec", "model": launcher.MODEL, "model_calls": 4,
               "usage": {"input_tokens": 10}, "cache_writes": None, "elapsed_s": 9.5, "dollars": 0.03,
               "accounted_dollars": 0.02, "transcript": str(run / "patch-sessions" / "dev-codec.jsonl")}
    (run / "patch-sessions" / "dev-codec.json").write_text(json.dumps(session), encoding="utf-8")
    other = dict(session, task_id="dev-wallet", model="claude-sonnet-5-5")
    (run / "patch-sessions" / "dev-wallet.json").write_text(json.dumps(other), encoding="utf-8")
    (run / "key-check.json").write_text(json.dumps({"key_file_present_unconfined": True, "confined_test": "passed",
                                                    "g6_proposed_check": "not_refuted", "ok": True}), encoding="utf-8")
    arms_out = {"g6_commit": "c" * 40, "record": {"final": "f" * 64, "entries": 3},
                "v9": {"gaming": {"contaminated": False, "findings": [], "vault_changes": [], "vault_incidents": []},
                       "rows": [{"task_id": "dev-codec", "arms": {"tests": {"category": "accept", "detail": str(run)}}}]},
                "v10": {"patches": [{"patch": "dev-codec", "arm_a": {"category": "insufficient_evidence"}, "arms": {
                    "E": {"disposition": {"category": "accept", "error_kind": "", "detail": str(run)},
                          "meter": {"calls": 1, "cents": 0.8}, "model_calls": 1, "seconds": 11.0,
                          "findings": []}}}],
                        "spending_differences": [], "reruns": []}}
    (run / "arms.json").write_text(json.dumps(arms_out), encoding="utf-8")
    (run / "ledger.json").write_text(json.dumps({"cap": 15.0, "spent": 0.5, "open": {}, "entries": []}),
                                     encoding="utf-8")
    got = rehearsal.record({"runA": run}, notes={"runA": "the first run"})
    text = json.dumps(got)
    assert str(tmp_path) not in text and "transcript" not in text                      # no private path
    one = got["runs"]["runA"]
    assert one["note"] == "the first run" and one["g6_commit"] == "c" * 40 and one["key_check"]["ok"] is True
    assert [s["label"] for s in one["sessions"]] == ["the study's model", "not the study's model"]
    assert one["v9"] == {"dev-codec": {"tests": "accept"}} and one["gaming"]["contaminated"] is False
    assert one["v10"]["dev-codec"]["E"] == {"disposition": "accept", "error_kind": "", "calls": 1, "g6_calls": 1,
                                            "dollars": 0.008, "seconds": 11.0, "findings": 0}
    assert got["spent_dollars"] == 0.5 and got["cap_dollars"] == rehearsal.CAP_DOLLARS
    assert got["projection"]["patch"]["observed"] == [0.03]                             # the study's model only
    assert got["projection"]["E"]["observed"] == [0.008]


def test_costs_reprices_a_recorded_session_from_its_transcripts_result_record_and_keeps_what_was_charged(tmp_path):
    # the fifth fault, for sessions that ran before its fix: the transcript holds the result record's totals
    folder = tmp_path / "run" / "patch-sessions"
    folder.mkdir(parents=True)
    totals = {"input_tokens": 50, "output_tokens": 4720, "cache_read_input_tokens": 135695,
              "cache_creation_input_tokens": 8674, "cache_creation": {"ephemeral_5m_input_tokens": 8674}}
    lines = [{"type": "assistant", "message": {"id": "m1", "usage": dict(totals, output_tokens=3)}},
             {"type": "result", "subtype": "success", "result": "done", "usage": totals, "total_cost_usd": 0.048062}]
    (folder / "dev-codec.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    (folder / "dev-codec.json").write_text(json.dumps({
        "kind": "patch", "task_id": "dev-codec", "model": launcher.MODEL, "model_calls": 1, "elapsed_s": 45.0,
        "usage": dict(totals, output_tokens=3), "dollars": 0.031, "accounted_dollars": 0.025,
        "transcript": "dev-codec.jsonl"}), encoding="utf-8")
    got = rehearsal.costs(tmp_path / "run")["sessions"][0]
    assert got["tokens"]["output_tokens"] == 4720 and got["usage_source"] == "result"
    assert got["stop_dollars"] == round(launcher.dollars(totals), 6) and got["charged_dollars"] == 0.031
    assert got["accounted_dollars"] == pytest.approx(0.048062, abs=1e-6)               # Claude Code's own figure


def test_the_projection_gives_each_unit_a_low_a_high_and_a_cap_bound_for_the_real_runs_counts():
    units = {"patch": [0.02, 0.04], "self_review": [0.01], "review_r": [0.05], "C": [0.005], "T": [0.03], "E": [0.06]}
    got = rehearsal.projection(units)
    assert got["patch"]["low"] == pytest.approx(0.03 * 30) and got["patch"]["high"] == pytest.approx(0.04 * 45)
    assert got["patch"]["cap_bound"] == pytest.approx((launcher.CAPS["patch"]["max_usd"] + launcher.MARGIN_USD) * 45)
    assert got["T"]["observed"] == [0.03, *rehearsal.SMOKE_PER_REVIEW]           # the smoke's reviews are T's kind
    worst = rehearsal.PROPOSER["max_model_calls"] * (rehearsal.PROPOSER["max_input_chars"] * rehearsal.PRICE_G6["input"]
                                                     + rehearsal.PROPOSER["max_output_tokens"]
                                                     * rehearsal.PRICE_G6["output"]) / 1e6
    assert got["E"]["cap_bound"] == pytest.approx(worst * 30)
    assert got["totals"]["V10"]["low"] == pytest.approx(sum(got[u]["low"] for u in ("C", "T", "E", "review_r")))
    assert got["totals"]["V9"]["high"] == pytest.approx(got["patch"]["high"] + got["self_review"]["high"])


def test_arm_c_and_t_are_arm_as_rule_with_their_search_and_the_stopped_caller(tmp_path, monkeypatch):
    seen = {}

    def review(tree, *, task, requirements, timeout_s, search=None, llm=None, **kwargs):
        seen.update(search=search, llm=llm, requirements=requirements)
        return {"ok": True, "assessment": {"outcome": "supported_within_scope", "reasons": [],
                                          "requirements": [{"id": "R1", "status": "passed_on_stated_checks",
                                                            "checks": [{"id": "test_a.py", "verdict": "passed",
                                                                        "provenance": "stated"}]}],
                                          "completion": {"activities": [{"activity": "model call", "model_calls": 1},
                                                                        {"check": "g6p-1", "result": "not_refuted"}],
                                                         "defects_reproduced": {"stated_and_repository_checks": [],
                                                                                "proposed_checks": []}}}}
    monkeypatch.setattr(rehearsal, "review_patch", review)
    monkeypatch.setattr(rehearsal, "study_caller", lambda: FakeCaller())
    monkeypatch.setenv(rehearsal.LEDGER_SETTING, str(tmp_path / "ledger.json"))
    patch = {"id": "dev-wallet", "requirements": [{"id": "R1", "text": "x"}], "checks_cited": ["test_a.py"],
             "trees": {"patched": str(tmp_path), "base": str(tmp_path)}, "arm_a": dispositions.make("accept")}
    got = rehearsal.arm_t(patch, search=dict(driver.SEARCH["T"]), share_left=lambda: True, meter=Meter())
    assert seen["search"]["templates"] is True and seen["search"]["propose"] is True
    assert isinstance(seen["llm"], rehearsal.StoppedCaller) and seen["requirements"][0]["checks"] == ["test_a.py"]
    assert got["own"]["category"] in dispositions.CATEGORIES and got["report"]["assessment"]
    assert got["search_stage"] == {"ran_to_result": True} and got["model_calls"] == 1 and got["findings"] == []
