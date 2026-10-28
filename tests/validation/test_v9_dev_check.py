# SPDX-License-Identifier: Apache-2.0
"""V9's development check of the production environment (``environment/dev_check.py``): which images it may use and
the commands it runs. No image is started here; the image names are made-up listings."""
from __future__ import annotations

import pytest

from patch_review_utility import containers
from patch_review_utility.environment import dev_check

REGISTRY = "ghcr.io/scaleapi/swe-bench_pro-v2"


def listing(*ids: str) -> list[str]:
    return [f"{REGISTRY}:{task}-vabc" for task in ids]


def test_the_check_uses_only_v7s_named_development_images_and_never_v8s_scored_ones():
    named = [check["task"] for check in dev_check.CHECKS]
    assert len(named) == 6 and not any(bad in task for task in named for bad in dev_check.NEVER)
    assert {check["repository"] for check in dev_check.CHECKS} == {"ansible/ansible", "qutebrowser/qutebrowser",
                                                                   "internetarchive/openlibrary"}


def test_an_image_is_used_only_when_exactly_one_is_already_on_the_computer():
    task = dev_check.CHECKS[0]["task"]
    assert dev_check.select(listing(task, "instance_other__x-1"), task) == f"{REGISTRY}:{task}-vabc"
    with pytest.raises(ValueError, match="not on this computer"):
        dev_check.select(listing("instance_other__x-1"), task)
    with pytest.raises(ValueError, match="more than one"):
        dev_check.select([*listing(task), f"{REGISTRY}:{task}-vdef"], task)
    with pytest.raises(ValueError, match="never"):
        dev_check.select(listing(*dev_check.NEVER), dev_check.NEVER[0])


def test_qutebrowser_is_checked_with_its_qt_wrapper_and_the_probe_is_told_which_variables_its_tests_need():
    (check,) = [c for c in dev_check.CHECKS if c["repository"] == "qutebrowser/qutebrowser"]
    assert check["env"] == {"QUTE_QT_WRAPPER": "PyQt6", "QT_QPA_PLATFORM": "offscreen",
                            "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox --disable-dev-shm-usage --disable-gpu"}
    probe = dev_check.probe_command("img", python="python", folder="tests/unit/api", volume="tc", seconds=60,
                                    env=check["env"])
    assert containers.repo_env_flag(check["env"]) in probe and not any("V9_PROBE" in word for word in probe)


def test_every_image_has_a_planted_control_and_ansible_puts_its_lib_first():
    for check in dev_check.CHECKS:
        control = check["control"]
        assert control["old"] != control["new"] and control["test"].startswith(check["folder"])
        assert control["test"].partition("::")[0].endswith(".py") and "::" in control["test"]
    (ansible,) = [c for c in dev_check.CHECKS if c["repository"] == "ansible/ansible"]
    assert ansible["import_roots"] == ["lib"] and ansible["env"] == {"ANSIBLE_VERBOSITY": "3"}


@pytest.mark.parametrize("without, with_change, verdict", [
    ("passed", "failed", "tests the patch"), ("passed", "passed", "does not test the patch"),
    ("failed", "failed", "inconclusive: the control test does not pass without the change"),
    ("passed", "missing", "does not test the patch")])
def test_a_control_that_does_not_fail_means_the_arm_is_not_testing_the_patch(without, with_change, verdict):
    assert dev_check.control_verdict(without, with_change) == verdict


def test_g6s_arms_get_a_job_that_states_the_control_test_as_the_check_with_the_import_roots():
    (ansible,) = [c for c in dev_check.CHECKS if c["repository"] == "ansible/ansible"]
    job = dev_check.arm_job("g6", ansible, roots=True)
    assert job["arm"] == "g6" and job["tree"] == containers.REPOSITORY and job["roots"] == [containers.STUDY_MOUNT]
    assert job["checks"] == [ansible["control"]["test"]] and job["import_roots"] == ["lib"]
    assert dev_check.arm_job("direct_checks", ansible, roots=False)["import_roots"] == []
    benign = dev_check.benign(ansible["control"])
    assert benign["old"] == ansible["control"]["old"] and benign["new"].startswith(benign["old"])
    assert "#" in benign["new"].removeprefix(benign["old"])


def test_the_guard_is_read_from_g6s_reason_codes_and_from_the_direct_checks_details():
    assert dev_check.guard_fired({"row": {"reason_codes": ["evidence:changed_code_not_loaded"]}}) is True
    assert dev_check.guard_fired({"checks": [{"name": "stated_checks", "passed": False,
                                              "detail": "changed code not loaded: calc.py"}]}) is True
    assert dev_check.guard_fired({"row": {"reason_codes": ["tier_1:check_failed"]}, "checks": []}) is False


def test_a_rerun_of_g6s_arms_replaces_only_the_named_tasks_controls_and_says_which_g6_ran_each():
    old = {"g6_arms_toolchain": {"commit": "a" * 40},
           "results": [{"task": "t1", "arm_1": {"passed": 7}, "g6_arms_control": {"g6": "old"}},
                       {"task": "t2", "g6_arms_control": {"g6": "kept"}}, {"task": "t3", "skipped": "absent"}]}
    new = dev_check.merge_g6_arms(old, {"t1": {"g6": "new"}}, {"commit": "b" * 40, "volume": "v"})
    first, second, third = new["results"]
    assert first == {"task": "t1", "arm_1": {"passed": 7}, "g6_arms_control": {"g6": "new"},
                     "g6_arms_commit": "b" * 40}
    assert second["g6_arms_control"] == {"g6": "kept"} and second["g6_arms_commit"] == "a" * 40
    assert "g6_arms_commit" not in third and new["g6_arms_toolchains"] == [{"commit": "a" * 40}, {"commit": "b" * 40,
                                                                                                "volume": "v"}]
    assert "g6_arms_toolchain" not in new and old["results"][0]["g6_arms_control"] == {"g6": "old"}   # a copy
    with pytest.raises(ValueError, match="t9"):
        dev_check.merge_g6_arms(old, {"t9": {}}, {"commit": "b" * 40})
    again = dev_check.merge_g6_arms(new, {"t1": {"g6": "newer"}}, {"commit": "b" * 40, "volume": "v"})
    assert again["g6_arms_toolchains"] == new["g6_arms_toolchains"]          # a toolchain is listed once


def test_the_check_reads_what_is_left_of_the_images_pytest_settings_after_the_apply_script():
    command = dev_check.settings_left_command("img", python="/usr/bin/python", env={"QT_QPA_PLATFORM": "offscreen"})
    assert command[-3:] == ["sh", "/v9/apply.sh", "env"] and "QT_QPA_PLATFORM=offscreen" in command


def test_the_probe_runs_in_the_same_container_as_g6s_arms_and_arm_1_in_the_pristine_image():
    probe = dev_check.probe_command("img", python="/usr/bin/python", folder="test/units/errors", volume="tc",
                                    seconds=600, import_roots=["lib"], control_test="test/units/errors/t.py::test")
    g6 = containers.g6_command("img", toolchain="tc", study=dev_check.VALIDATION, repo_python="/usr/bin/python")
    cut = g6.index(containers.G6_PYTHON)
    assert probe[:cut] == g6[:cut]                                            # the same flags, mounts and setting
    assert probe[cut:] == [containers.G6_PYTHON, "-I", "/opt/v9/patch_review_utility/environment/probe.py",
                           "/v9/probe.json", "test/units/errors", "600", '["lib"]', "test/units/errors/t.py::test"]
    assert dev_check.tests_command("img", python="/usr/bin/python") == containers.tests_command(
        "img", python="/usr/bin/python")
