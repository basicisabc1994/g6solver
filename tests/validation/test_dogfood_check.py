# SPDX-License-Identifier: Apache-2.0
"""The dogfooding log checker: entry schema and the beta bar (n=1 dogfooding, not a study)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from validation.dogfood import check


def entry(i: int = 1, **over: object) -> dict:
    base: dict = {
        "task_id": f"t{i}",
        "date": "2026-11-21",
        "repo": "example/widgets",
        "task": "fix an off-by-one in the pager",
        "calls": [
            {"tool": "g6_select", "phase": "plan", "rank": None},
            {"tool": "g6_review_patch", "phase": "review", "rank": 1},
        ],
        "refusals": [],
        "reroute_to_absent_tool": False,
        "false_alarms": {"count": 0, "notes": []},
        "overrides_asked_person": True,
        "minutes": 12,
        "notes": "",
    }
    base.update(over)
    return base


def write(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "log.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return p


def ranked(i: int, rank: int | None) -> dict:
    return entry(i, calls=[{"tool": "g6_select", "phase": "plan", "rank": rank}])


def ten(hits: int) -> list[dict]:
    return [ranked(i, 2 if i <= hits else 5) for i in range(1, 11)]


def run(tmp_path: Path, entries: list[dict], capsys) -> tuple[int, str]:
    code = check.main([str(write(tmp_path, entries))])
    return code, capsys.readouterr().out


def test_seven_of_ten_meets_the_bar(tmp_path, capsys):
    code, out = run(tmp_path, ten(7), capsys)
    assert code == 0
    assert "n=1 dogfooding, not a study" in out


def test_six_of_ten_misses_the_bar(tmp_path, capsys):
    code, out = run(tmp_path, ten(6), capsys)
    assert code == 1
    assert "NOT MET" in out


def test_nine_tasks_is_too_few(tmp_path, capsys):
    code, _ = run(tmp_path, ten(10)[:9], capsys)
    assert code == 1


def test_rank_three_counts_rank_four_does_not(tmp_path, capsys):
    es = [ranked(i, 3) for i in range(1, 8)] + [ranked(i, 4) for i in range(8, 11)]
    assert run(tmp_path, es, capsys)[0] == 0
    es = [ranked(i, 3) for i in range(1, 7)] + [ranked(i, 4) for i in range(7, 11)]
    assert run(tmp_path, es, capsys)[0] == 1


def test_percent_bar_applies_over_all_tasks(tmp_path, capsys):
    # 7 hits of 11 tasks is 63.6 percent: the count passes, the 70 percent share does not.
    es = [ranked(i, 1) for i in range(1, 8)] + [ranked(i, 9) for i in range(8, 12)]
    assert run(tmp_path, es, capsys)[0] == 1


def test_task_without_any_rank_is_not_a_hit(tmp_path, capsys):
    es = ten(7)
    es[0] = ranked(1, None)
    assert run(tmp_path, es, capsys)[0] == 1


def test_first_ranked_call_decides_the_task(tmp_path, capsys):
    calls = [
        {"tool": "g6_select", "phase": "plan", "rank": 2},
        {"tool": "g6_select", "phase": "fix", "rank": 8},
    ]
    es = ten(7)
    es[0] = entry(1, calls=calls)
    assert run(tmp_path, es, capsys)[0] == 0


def test_reroute_to_absent_tool_fails_the_bar(tmp_path, capsys):
    es = ten(10)
    es[3] = entry(4, reroute_to_absent_tool=True, calls=es[3]["calls"])
    code, out = run(tmp_path, es, capsys)
    assert code == 1
    assert "absent" in out


def test_override_without_asking_fails_the_bar(tmp_path, capsys):
    es = ten(10)
    es[0]["overrides_asked_person"] = False
    code, out = run(tmp_path, es, capsys)
    assert code == 1
    assert "override" in out


def test_empty_log_is_unmet_not_invalid(tmp_path, capsys):
    assert run(tmp_path, [], capsys)[0] == 1


def test_committed_log_is_empty_and_unmet():
    log = Path(check.__file__).parent / "log.jsonl"
    assert log.read_text(encoding="utf-8") == ""
    assert check.main([str(log)]) == 1


def test_unknown_field_is_invalid(tmp_path, capsys):
    es = ten(10)
    es[0]["mood"] = "fine"
    assert run(tmp_path, es, capsys)[0] == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"date": "21/11/2026"},
        {"minutes": -1},
        {"minutes": "12"},
        {"overrides_asked_person": "yes"},
        {"calls": [{"tool": "g6_select", "phase": "plan", "rank": 0}]},
        {"calls": [{"tool": "g6_select", "phase": "plan"}]},
        {"refusals": [{"code": "oops", "reroute_resolved": True}]},
        {"false_alarms": {"count": 2, "notes": ["only one"]}},
        {"repo": "C:\\Users\\someone\\code"},
        {"task": "see /home/someone/code"},
        {"task_id": ""},
    ],
)
def test_invalid_lines_exit_2(tmp_path, capsys, bad):
    es = ten(10)
    es[0] = entry(1, **bad)
    code, out = run(tmp_path, es, capsys)
    assert code == 2
    assert "line 1" in out


def test_valid_refusal_and_false_alarm_are_accepted(tmp_path, capsys):
    es = ten(10)
    es[0] = entry(
        1,
        refusals=[{"code": "G6_E_CONSENT_REQUIRED", "reroute_resolved": True}],
        false_alarms={"count": 1, "notes": ["flagged a test helper as unsafe"]},
    )
    assert run(tmp_path, es, capsys)[0] == 0


def test_duplicate_task_id_is_invalid(tmp_path, capsys):
    es = ten(10)
    es[1]["task_id"] = es[0]["task_id"]
    assert run(tmp_path, es, capsys)[0] == 2


def test_not_json_is_invalid(tmp_path, capsys):
    p = tmp_path / "log.jsonl"
    p.write_text("not json\n", encoding="utf-8")
    assert check.main([str(p)]) == 2


def test_missing_file_is_invalid(tmp_path):
    assert check.main([str(tmp_path / "nope.jsonl")]) == 2