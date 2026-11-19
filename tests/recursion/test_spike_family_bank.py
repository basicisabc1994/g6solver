# SPDX-License-Identifier: Apache-2.0
from g6.recursion.spike.family_bank import dry_run
from g6.recursion.spike.runbook import check_record


def test_40_families_checked_with_fake_executions():
    value = dry_run()
    assert value["mode"] == "fake"
    assert len({f["id"] for f in value["metrics"]["families"]}) == 40
    assert all(f["mutation_check"]["rejected"] > 0 for f in value["metrics"]["families"])
    assert check_record("P0-9", value).status == "pass"


def test_survivor_and_long_episode_and_actor_text_fail():
    for changes, reason in (({"survive": True}, "bar_suite_strength"),
                            ({"episode_seconds": 1201}, "bar_episode_le_20_minutes"),
                            ({"actor_text": "actor supplied repair"}, "bar_template_no_actor_text")):
        result = check_record("P0-9", dry_run(**changes))
        assert result.status == "fail"
        assert reason in " ".join(result.reasons)
