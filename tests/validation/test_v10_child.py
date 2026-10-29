# SPDX-License-Identifier: Apache-2.0
"""V10's arms C and T in a child process of their own, as V9 runs arms 3 and 4 (``driver.ChildArm``): ``python -I``,
the process tree killed at the arm's limit plus its grace. The arms are fakes written here; no model is called."""
from __future__ import annotations

import time

import pytest

from patch_review_search import driver
from patch_review_utility.dispositions import ACCEPT, ERROR, make

ACCEPTED = make(ACCEPT)
FAKE_ARMS = '''\
import os
import time

ACCEPTED = {"accepted": True, "category": "accept", "error_kind": "", "time_or_cap_reached": False, "detail": ""}


def replies(patch, *, search, share_left, meter):
    meter.add(calls=1, characters=10, cents=2.0)
    flag = os.environ.get("G6_ECHA_RESEARCH")
    return {"own": ACCEPTED, "model_calls": 1, "spent_cents": 2.0,
            "reasons": [f"search:{sorted(search.items())}", f"share_left:{share_left()}", f"flag:{flag}"]}


def sleeps(patch, *, search, share_left, meter):
    meter.add(calls=1, cents=3.0)
    time.sleep(120)


def infrastructure(patch, **kwargs):
    from patch_review_search.metering import InfrastructureFailure
    raise InfrastructureFailure("the container did not start")


def crashes(patch, **kwargs):
    raise RuntimeError("the review crashed")
'''


@pytest.fixture
def fake_arms(tmp_path):
    (tmp_path / "v10_fake_arms.py").write_text(FAKE_ARMS, encoding="utf-8")
    return str(tmp_path)


def e_arm(patch, *, meter, **kwargs):
    meter.add(cents=1.0)
    return {"disposition": ACCEPTED, "findings": [], "spent_cents": 1.0, "calls": {"due": 1, "made": 1}}


def in_process(patch, *, meter, **kwargs):
    meter.add(cents=1.0)
    return {"own": ACCEPTED, "model_calls": 1, "spent_cents": 1.0}


def study(c, t=in_process, *, items=None, record=None, children_only=False):
    return driver.study(items or [{"id": "p0", "draw_index": 0, "arm_a": ACCEPTED}], arm_c=c, arm_t=t, arm_e=e_arm, arm_r=e_arm,
                        spending=driver.Spending(300.0), record=record or driver.Record(),
                        rerun=lambda *args: {}, excluded=[], children_only=children_only)


def test_a_child_arm_replies_from_its_own_isolated_process_metered_through_a_file(fake_arms, monkeypatch):
    monkeypatch.setenv("G6_ECHA_RESEARCH", "1")
    got = study(driver.ChildArm("v10_fake_arms:replies", limit_s=60, roots=(fake_arms,)))
    row = got["patches"][0]["arms"]["C"]
    assert row["disposition"]["accepted"] is True and row["meter"] == {"calls": 1, "characters": 10, "cents": 2.0}
    assert row["reasons"] == ["search:[('templates', False)]", "share_left:True", "flag:None"]
    assert got["spending"]["spent_cents"]["C"] == 2.0 and got["spending_differences"] == []


def test_a_child_arm_past_its_limit_is_killed_and_is_a_time_out_charged_for_its_calls(fake_arms):
    started = time.monotonic()
    got = study(driver.ChildArm("v10_fake_arms:sleeps", limit_s=1, roots=(fake_arms,), grace_s=1))
    assert time.monotonic() - started < 60
    row = got["patches"][0]["arms"]
    assert row["C"]["disposition"]["category"] == ERROR and row["C"]["disposition"]["error_kind"] == driver.TIME_OUT
    assert row["C"]["disposition"]["time_or_cap_reached"] is True and row["C"]["meter"]["cents"] == 3.0
    assert got["spending"]["spent_cents"]["C"] == 3.0 and row["T"]["disposition"]["accepted"] is True
    assert row["E"]["disposition"]["accepted"] is True


def test_a_child_arms_infrastructure_failure_is_retried_once_and_its_crash_is_a_tool_error(fake_arms):
    got = study(driver.ChildArm("v10_fake_arms:infrastructure", limit_s=60, roots=(fake_arms,)),
                driver.ChildArm("v10_fake_arms:crashes", limit_s=60, roots=(fake_arms,)))
    row = got["patches"][0]["arms"]
    assert row["C"]["disposition"]["error_kind"] == driver.INFRASTRUCTURE and row["C"]["attempts"] == 2
    assert [(r["arm"], r["attempt"]) for r in got["retries"]] == [("C", 1)]
    assert row["T"]["disposition"]["error_kind"] == driver.TOOL_ERROR
    assert "the review crashed" in row["T"]["disposition"]["detail"]


def test_a_study_level_runs_c_and_t_only_in_child_processes(fake_arms):
    with pytest.raises(ValueError, match="child process"):
        study(in_process, children_only=True)
    got = study(driver.ChildArm("v10_fake_arms:replies", limit_s=60, roots=(fake_arms,)),
                driver.ChildArm("v10_fake_arms:replies", limit_s=60, roots=(fake_arms,)), children_only=True)
    assert got["patches"][0]["arms"]["T"]["reasons"][0] == "search:[('templates', True), ('test_adequacy', True)]"


def test_a_file_meter_adds_up_what_it_recorded_and_ignores_a_line_cut_short(tmp_path):
    from patch_review_search import metering
    meter = metering.FileMeter(tmp_path / "meter.jsonl")
    meter.add(calls=1, characters=5, cents=0.5)
    meter.add(calls=1, characters=7, cents=0.25)
    with open(tmp_path / "meter.jsonl", "a", encoding="utf-8") as handle:
        handle.write('{"calls": 1, "chara')                              # a process killed mid-write
    assert metering.FileMeter(tmp_path / "meter.jsonl").reading() == {"calls": 2, "characters": 12, "cents": 0.75}


def test_a_meter_counts_the_tokens_a_call_reports_by_kind_and_only_once_one_does(tmp_path):
    from patch_review_search import metering
    for meter in (metering.Meter(), metering.FileMeter(tmp_path / "tokens.jsonl")):
        meter.add(calls=1, cents=0.5)
        assert "tokens" not in meter.reading()
        meter.add(calls=1, cents=0.5, tokens={"input_tokens": 900, "output_tokens": 100})
        meter.add(calls=1, cents=0.5, tokens={"input_tokens": 50, "cache_read_input_tokens": 7})
        assert meter.reading()["tokens"] == {"input_tokens": 950, "output_tokens": 100, "cache_read_input_tokens": 7}
        with pytest.raises(ValueError):
            meter.add(calls=1, tokens={"input_tokens": -1})
