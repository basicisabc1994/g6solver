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
    with pytest.raises(rehearsal.LLMError, match="dollar stop"):
        caller.complete("s", "u", max_tokens=4000)            # its worst case would pass the cap
    blocked = rehearsal.StoppedCaller(FakeCaller(), ledger, Meter(), share_left=lambda: False)
    with pytest.raises(rehearsal.LLMError, match="share"):
        blocked.complete("s", "u")


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
