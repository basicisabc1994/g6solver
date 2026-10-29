# SPDX-License-Identifier: Apache-2.0
"""V10's study driver over patches (PROTOCOL.md on the verification search branch, "For all arms of this study" and
"Reruns"). The arms are fakes: no model is called and G6's review is never run."""
from __future__ import annotations

import json

import pytest

from patch_review_search import driver
from patch_review_utility import dispositions
from patch_review_utility.dispositions import ABSTAIN, ACCEPT, DEFECT, ERROR, make

ACCEPTED, DEFECTIVE = make(ACCEPT), make(DEFECT, "a stated check failed")


def patches(*arm_a, start=0):
    return [{"id": f"p{index}", "draw_index": start + index, "arm_a": disposition}
            for index, disposition in enumerate(arm_a)]


class Spend:
    """A fake arm's run that meters ``calls`` model calls costing ``cents`` in all, then returns or raises ``then``."""

    def __init__(self, calls, cents, then):
        self.calls, self.cents, self.then = calls, cents, then


class Arm:
    """A fake arm: its replies by patch id (a dict, an exception to raise, a ``Spend``, or a function of the call's
    keywords, which meters its own calls), once per attempt, and every call. A plain reply meters the calls and cents
    it reports."""

    def __init__(self, name, replies=None, default=None):
        self.name, self.replies, self.calls = name, {k: list(v) for k, v in (replies or {}).items()}, []
        self.default = default or {"own": ACCEPTED, "model_calls": 1, "spent_cents": 1.0}

    def __call__(self, patch, **kwargs):
        self.calls.append((patch["id"], kwargs))
        queue = self.replies.get(patch["id"])
        found = queue.pop(0) if queue else self.default
        if callable(found) and not isinstance(found, BaseException):
            return found(**kwargs)                                      # it meters what it spends itself
        if isinstance(found, Spend):
            kwargs["meter"].add(calls=found.calls, cents=found.cents)
            found = found.then
        elif isinstance(found, dict):
            calls = found.get("model_calls", (found.get("calls") or {}).get("made", 0))
            kwargs["meter"].add(calls=calls, cents=found.get("spent_cents") or 0.0)
        if isinstance(found, BaseException):
            raise found
        return found


def e_arm(replies=None):
    return Arm("E", replies, default={"disposition": ACCEPTED, "findings": [], "spent_cents": 1.0,
                                      "calls": {"due": 1, "made": 1}})


def r_arm(replies=None):
    return Arm("R", replies, default={"disposition": ACCEPTED, "findings": [], "spent_cents": 1.0,
                                      "calls": {"made": 1}})


def study(items, c=None, t=None, e=None, r=None, *, cap=300.0, rerun=None, record=None, excluded=(), shares=None):
    c, t, e, r = c or Arm("C"), t or Arm("T"), e or e_arm(), r or r_arm()
    order = []
    for arm in (c, t, e, r):
        original = arm.__call__

        def logged(patch, _original=original, _name=arm.name, **kwargs):
            order.append((patch["id"], _name))
            return _original(patch, **kwargs)
        arm.logged = logged
    got = driver.study(items, arm_c=c.logged, arm_t=t.logged, arm_e=e.logged, arm_r=r.logged,
                       spending=driver.Spending(cap, shares), record=record or driver.Record(),
                       rerun=rerun or (lambda patch, arm, reproduction: {}), excluded=list(excluded))
    return got, order, (c, t, e)


def test_arms_c_t_e_and_r_all_run_on_one_patch_before_the_next_in_draw_order():
    got, order, _ = study(patches(ACCEPTED, ACCEPTED))
    assert order == [("p0", "C"), ("p0", "T"), ("p0", "E"), ("p0", "R"), ("p1", "C"), ("p1", "T"), ("p1", "E"),
                     ("p1", "R")]
    assert [row["patch"] for row in got["patches"]] == ["p0", "p1"]
    with pytest.raises(ValueError, match="draw order"):
        driver.study(list(reversed(patches(ACCEPTED, ACCEPTED))), arm_c=Arm("C"), arm_t=Arm("T"), arm_e=e_arm(), arm_r=e_arm(),
                     spending=driver.Spending(300.0), record=driver.Record(), rerun=lambda *a: {}, excluded=[])
    with pytest.raises(ValueError, match="arm A"):
        driver.study([{"id": "p0", "draw_index": 0}], arm_c=Arm("C"), arm_t=Arm("T"), arm_e=e_arm(), arm_r=e_arm(),
                     spending=driver.Spending(300.0), record=driver.Record(), rerun=lambda *a: {}, excluded=[])


def test_empty_and_non_applying_patches_are_counted_and_recorded_with_their_reason_never_dropped():
    record = driver.Record()
    excluded = [{"id": "x1", "draw_index": 1, "reason": "empty"}, {"id": "x3", "draw_index": 3, "reason": "not_applying"}]
    items = [{"id": "p0", "draw_index": 0, "arm_a": ACCEPTED}, {"id": "p2", "draw_index": 2, "arm_a": ACCEPTED}]
    got, _, _ = study(items, record=record, excluded=excluded)
    assert got["excluded"] == {"counts": {"empty": 1, "not_applying": 1}, "patches": excluded}
    assert [entry["data"] for entry in record.entries if entry["kind"] == "excluded"] == excluded
    assert record.entries[0]["kind"] == "excluded"                    # recorded before any arm runs
    for bad, said in (([{"id": "x", "draw_index": 5, "reason": "dull"}], "reason"),
                      ([{"id": "x", "draw_index": 0, "reason": "empty"}], "draw index"),
                      ([{"id": "x", "reason": "empty"}], "draw index")):
        with pytest.raises(ValueError, match=said):
            study(items, excluded=bad)


def test_g6s_arms_get_their_search_configuration_as_data_and_e_the_calls_c_and_t_made():
    c = Arm("C", {"p0": [{"own": ACCEPTED, "model_calls": 3, "spent_cents": 1.0}]})
    t = Arm("T", {"p0": [{"own": ACCEPTED, "model_calls": 5, "spent_cents": 1.0}]})
    _, _, (c, t, e) = study(patches(ACCEPTED), c, t)
    assert c.calls[0][1]["search"] == {"templates": False}
    assert t.calls[0][1]["search"] == {"templates": True, "test_adequacy": True}
    assert (e.calls[0][1]["c_calls"], e.calls[0][1]["t_calls"]) == (3, 5)
    c.calls[0][1]["search"]["templates"] = True
    assert driver.SEARCH["C"] == {"templates": False}              # each call gets its own copy


@pytest.mark.parametrize("arm_a, own, category, error_kind", [
    (ACCEPTED, ACCEPTED, ACCEPT, ""), (ACCEPTED, DEFECTIVE, DEFECT, ""), (DEFECTIVE, ACCEPTED, DEFECT, ""),
    (make(ABSTAIN, "no check"), make(DEFECT, "x"), ABSTAIN, ""),
    (ACCEPTED, make(ERROR, "refused", error_kind="refusal"), ERROR, "refusal"),
    (make(ERROR, "x", error_kind="tool_error"), ACCEPTED, ERROR, driver.ARM_A)])
def test_a_search_arm_accepts_only_when_arm_a_and_its_own_review_accept(arm_a, own, category, error_kind):
    c = Arm("C", {"p0": [{"own": own, "model_calls": 1, "spent_cents": 1.0}]})
    got, _, _ = study(patches(arm_a), c)
    row = got["patches"][0]["arms"]["C"]
    assert row["disposition"]["category"] == category and row["disposition"]["error_kind"] == error_kind
    assert row["own"] == own


def test_each_arm_runs_under_a_quarter_of_the_spending_cap_and_a_patch_past_its_share_is_a_counted_tool_error():
    c = Arm("C", {"p0": [{"own": ACCEPTED, "model_calls": 2, "spent_cents": 100.0}]})
    got, order, (c, t, e) = study(patches(ACCEPTED, ACCEPTED), c, cap=300.0)
    assert [patch for patch, _ in c.calls] == ["p0"] and ("p1", "C") not in order
    second = got["patches"][1]["arms"]
    assert second["C"]["disposition"]["error_kind"] == driver.SPEND_SHARE and second["T"]["disposition"]["accepted"]
    assert got["spending"] == {"cap_cents": 300.0, "shares_cents": {"C": 75.0, "T": 75.0, "E": 75.0, "R": 75.0},
                               "spent_cents": {"C": 100.0, "T": 2.0, "E": 2.0, "R": 2.0}}
    assert got["tallies"]["C"][ERROR] == 1 and got["tallies"]["C"]["error_kinds"] == {driver.SPEND_SHARE: 1}
    assert e.calls[1][1]["c_calls"] == 0                            # C made no call on p1
    assert t.calls[0][1]["share_left"]() is True


def test_an_arm_that_raises_after_two_calls_is_charged_for_both_and_the_next_patch_sees_the_reduced_share():
    seen = []

    def spends(meter, share_left, **kwargs):
        seen.append(share_left())
        meter.add(calls=1, characters=900, cents=41.0)
        seen.append(share_left())
        return {"own": ACCEPTED, "model_calls": 1, "spent_cents": 41.0}

    c = Arm("C", {"p0": [Spend(2, 60.0, then=RuntimeError("the review crashed"))], "p1": [spends]})
    got, _, (c, t, e) = study(patches(ACCEPTED, ACCEPTED, ACCEPTED), c, cap=300.0)
    first = got["patches"][0]["arms"]["C"]
    assert first["disposition"]["error_kind"] == driver.TOOL_ERROR and first["model_calls"] == 2
    assert first["meter"] == {"calls": 2, "characters": 0, "cents": 60.0}
    assert e.calls[0][1]["c_calls"] == 2                               # E matches the calls C made before it failed
    assert seen == [True, False]                                       # 60 spent, 40 left, 41 more passes the share
    assert got["spending"]["spent_cents"]["C"] == 101.0
    assert got["patches"][2]["arms"]["C"]["disposition"]["error_kind"] == driver.SPEND_SHARE
    assert [patch for patch, _ in c.calls] == ["p0", "p1"]


def test_what_an_arm_says_it_spent_is_a_cross_check_and_a_difference_is_logged():
    c = Arm("C", {"p0": [Spend(1, 5.0, then={"own": ACCEPTED, "model_calls": 1, "spent_cents": 1.0})]})
    record = driver.Record()
    got, _, _ = study(patches(ACCEPTED), c, record=record)
    assert got["spending"]["spent_cents"]["C"] == 5.0                  # the meter is charged, not the reply
    assert got["spending_differences"] == [{"patch": "p0", "arm": "C", "metered_cents": 5.0, "reported_cents": 1.0,
                                            "metered_calls": 1, "reported_calls": 1}]
    assert [entry["kind"] for entry in record.entries].count("spending_difference") == 1


def test_a_meter_counts_calls_characters_and_cents_and_only_counts_up():
    meter = driver.Meter()
    meter.add(characters=1200, cents=0.5)
    meter.add(calls=2, characters=300, cents=1.25)
    assert meter.reading() == {"calls": 3, "characters": 1500, "cents": 1.75}
    with pytest.raises(ValueError):
        meter.add(cents=-1.0)


def test_an_infrastructure_failure_after_a_model_call_is_a_tool_error_never_retried():
    c = Arm("C", {"p0": [Spend(1, 2.0, then=driver.InfrastructureFailure("the container went away"))]})
    got, _, (c, _, _) = study(patches(ACCEPTED), c)
    row = got["patches"][0]["arms"]["C"]
    assert row["disposition"]["error_kind"] == driver.TOOL_ERROR and row["attempts"] == 1 and got["retries"] == []
    assert len(c.calls) == 1 and got["spending"]["spent_cents"]["C"] == 2.0


def test_a_run_that_failed_from_infrastructure_before_its_first_call_is_retried_once_and_logged():
    broken = driver.InfrastructureFailure("the container did not start")
    c = Arm("C", {"p0": [broken, {"own": ACCEPTED, "model_calls": 1, "spent_cents": 1.0}], "p1": [broken, broken]})
    t = Arm("T", {"p0": [RuntimeError("a bug in the arm")]})
    record = driver.Record()
    got, _, (c, t, _) = study(patches(ACCEPTED, ACCEPTED), c, t, record=record)
    first, second = got["patches"][0]["arms"], got["patches"][1]["arms"]
    assert first["C"]["disposition"]["accepted"] is True and first["C"]["attempts"] == 2
    assert second["C"]["disposition"]["error_kind"] == driver.INFRASTRUCTURE and second["C"]["attempts"] == 2
    assert first["T"]["disposition"]["error_kind"] == driver.TOOL_ERROR and first["T"]["attempts"] == 1
    assert [(r["patch"], r["arm"]) for r in got["retries"]] == [("p0", "C"), ("p1", "C")]
    assert sum(entry["kind"] == "retry" for entry in record.entries) == 2


def test_every_tool_error_is_counted_and_no_patch_is_dropped():
    e = e_arm({"p1": [{"disposition": make(ERROR, "unparseable", error_kind="unparseable_reply"), "findings": [],
                       "spent_cents": 0.0, "calls": {"due": 1, "made": 1}}]})
    t = Arm("T", {"p0": [{"model_calls": 1}]})                          # a reply without its own review's disposition
    got, _, _ = study(patches(ACCEPTED, ACCEPTED, DEFECTIVE), t=t, e=e)
    assert all(set(row["arms"]) == {"C", "T", "E", "R"} for row in got["patches"]) and len(got["patches"]) == 3
    assert got["tallies"]["E"][ERROR] == 1 and got["tallies"]["T"][ERROR] == 1
    assert got["tallies"]["C"]["missing"] == 0 and sum(got["tallies"]["C"][k] for k in dispositions.CATEGORIES) == 3


def test_a_difference_from_arm_a_is_attributed_to_search_only_by_its_reasons_or_its_deadline():
    found = make(ABSTAIN, "unconfirmed")
    c = Arm("C", {"p0": [{"own": found, "model_calls": 1, "spent_cents": 1.0,
                          "reasons": ["evidence:unconfirmed_counterexample"]}],
                  "p1": [{"own": found, "model_calls": 1, "spent_cents": 1.0, "reasons": ["evidence:no_test_ran"]}],
                  "p2": [{"own": found, "model_calls": 1, "spent_cents": 1.0, "reasons": [],
                          "deadline_in_search": True}]})
    got, _, _ = study(patches(ACCEPTED, ACCEPTED, ACCEPTED), c)
    assert [row["arms"]["C"]["attribution"] for row in got["patches"]] == [
        driver.TO_SEARCH, driver.BETWEEN_RUNS, driver.TO_SEARCH]
    assert got["patches"][0]["arms"]["T"]["attribution"] is None


def test_a_failed_proposer_is_listed_with_g6s_reason_beside_its_disposition():
    c = Arm("C", {"p0": [{"own": make(ABSTAIN, "x"), "model_calls": 0, "spent_cents": 0.0,
                          "proposer_failure": "the model's reply named no check"}]})
    got, _, _ = study(patches(ACCEPTED), c)
    assert got["proposer_failures"] == [{"patch": "p0", "arm": "C", "reason": "the model's reply named no check",
                                         "category": ABSTAIN}]


def test_every_finding_is_rerun_after_every_arm_has_run_on_every_patch():
    repro = {"file": "tests/g6_check_0123456789ab/test_a.py", "text": "def test_a():\n    assert 0\n",
             "node": "tests/g6_check_0123456789ab/test_a.py::test_a"}
    c = Arm("C", {"p0": [{"own": make(ABSTAIN, "x"), "model_calls": 1, "spent_cents": 1.0, "findings": [repro]}]})
    e = e_arm({"p1": [{"disposition": DEFECTIVE, "spent_cents": 1.0, "calls": {"due": 1, "made": 1},
                       "findings": [{"file": repro["file"], "text": repro["text"], "node": repro["node"],
                                     "sha256": "0" * 64, "call": 1}]}]})
    seen = []

    def rerun(patch, arm, reproduction):
        seen.append((patch["id"], arm, reproduction["node"]))
        return {"reproducible": True}

    record = driver.Record()
    got, _, _ = study(patches(ACCEPTED, ACCEPTED), c, e=e, rerun=rerun, record=record)
    assert seen == [("p0", "C", repro["node"]), ("p1", "E", repro["node"])]
    kinds = [entry["kind"] for entry in record.entries]
    assert kinds.index("rerun") > max(i for i, kind in enumerate(kinds) if kind == "arm")
    assert got["reruns"][0]["result"] == {"reproducible": True}


def test_template_applicability_and_the_formal_path_are_recorded_before_the_reruns_and_each_rerun_keeps_its_code():
    repro = {"file": "g6_check_0123456789ab/test_a.py", "text": "def test_a():\n    assert 0\n",
             "node": "g6_check_0123456789ab/test_a.py", "code": "unconfirmed_failure"}
    report = {"assessment": {"completion": {"activities": [
        {"activity": "proposer stage", "stage": "example.v1", "result": "replied"},
        {"activity": "template instance", "check": "g6p-example-1", "template": "example.v1"}]}}}
    t = Arm("T", {"p0": [{"own": make(ABSTAIN, "x"), "model_calls": 1, "spent_cents": 1.0, "findings": [repro],
                          "report": report}]})
    record = driver.Record()
    got, _, _ = study(patches(ACCEPTED), t=t, rerun=lambda patch, arm, reproduction: {"reproducible": True},
                      record=record)
    kinds = [entry["kind"] for entry in record.entries]
    assert kinds.index("template_applicability") < kinds.index("rerun") and "formal_path" in kinds
    applied = next(e["data"] for e in record.entries if e["kind"] == "template_applicability")
    assert applied["templates"]["example.v1"]["applied"] == ["p0"]
    assert got["template_applicability"] == applied and got["formal_path"]["total"]["models_built"] == 0
    assert [e["data"]["code"] for e in record.entries if e["kind"] == "rerun"] == ["unconfirmed_failure"]


def test_arm_as_findings_are_rerun_too_and_every_arm_run_is_timed():
    repro = {"file": "tests/test_calc.py", "text": "def test_add():\n    assert 0\n", "node": "tests/test_calc.py::test_add"}
    items = [{"id": "p0", "draw_index": 0, "arm_a": DEFECTIVE, "arm_a_findings": [repro]}]
    seen = []
    got, _, _ = study(items, rerun=lambda patch, arm, reproduction: seen.append((patch["id"], arm)) or {})
    assert seen == [("p0", "A")] and got["reruns"][0]["arm"] == "A"
    for arm in ("C", "T", "E"):
        row = got["patches"][0]["arms"][arm]
        assert row["seconds"] >= 0 and row["processor_seconds"] >= 0


def test_what_a_search_arm_says_of_its_search_stage_is_kept_for_the_feasibility_check():
    stage = {"ran_to_result": True, "checks": 3}
    c = Arm("C", {"p0": [{"own": ACCEPTED, "model_calls": 1, "spent_cents": 1.0, "search_stage": stage}]})
    got, _, _ = study(patches(ACCEPTED), c)
    assert got["patches"][0]["arms"]["C"]["search_stage"] == stage
    assert got["patches"][0]["arms"]["T"]["search_stage"] is None


def test_arm_r_runs_after_e_under_its_own_share_its_findings_rerun_and_its_transcript_scanned(tmp_path):
    repro = {"file": "g6_check_0123456789ab/test_a.py", "text": "def test_a():\n    assert 0\n",
             "node": "g6_check_0123456789ab/test_a.py::test_a"}
    transcript = '{"type": "system", "subtype": "init", "mcp_servers": [{"name": "g6"}], "tools": ["Bash"]}'
    r = r_arm({"p0": [{"disposition": DEFECTIVE, "findings": [repro], "spent_cents": 80.0, "calls": {"made": 1},
                       "transcript": transcript, "copy_root": str(tmp_path)}]})
    seen = []
    got, _, (_, _, _) = study(patches(ACCEPTED, ACCEPTED), r=r,
                              rerun=lambda patch, arm, reproduction: seen.append((patch["id"], arm)) or {})
    first, second = got["patches"][0]["arms"]["R"], got["patches"][1]["arms"]["R"]
    assert first["disposition"]["category"] == DEFECT and ("p0", "R") in seen
    assert [f["kind"] for f in first["gaming"]["findings"]] == ["session_set_up"]
    assert second["disposition"]["error_kind"] == driver.SPEND_SHARE          # 80 cents is past R's 75
    assert got["tallies"]["R"][ERROR] == 1


def test_the_freeze_entry_may_set_other_shares_of_the_cap():
    got, _, _ = study(patches(ACCEPTED), shares={"C": 0.3, "T": 0.3, "E": 0.2, "R": 0.2})
    assert got["spending"]["shares_cents"] == {"C": 90.0, "T": 90.0, "E": 60.0, "R": 60.0}
    for bad in ({"C": 0.5, "T": 0.5, "E": 0.2, "R": 0.2}, {"C": 0.5, "T": 0.5}):
        with pytest.raises(ValueError, match="share"):
            driver.Spending(300.0, bad)


def test_the_record_is_append_only_each_entry_chained_to_the_one_before(tmp_path):
    path = tmp_path / "record.jsonl"
    record = driver.Record(path)
    first = record.add("arm", {"patch": "p0", "arm": "C", "output": {"x": 1}})
    final = record.add("rerun", {"patch": "p0", "result": {"reproducible": False}})
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["chain"] for line in lines] == [first, final] and record.final == final
    assert lines[1]["previous"] == first and len(final) == 64
    other = driver.Record()
    other.add("arm", {"patch": "p0", "arm": "C", "output": {"x": 2}})
    assert other.final != first
    with pytest.raises(FileExistsError):
        driver.Record(path)
