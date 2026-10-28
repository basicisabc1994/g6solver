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
    assert check["env"] == {"QUTE_QT_WRAPPER": "PyQt6", "QT_QPA_PLATFORM": "offscreen"}
    probe = dev_check.probe_command("img", python="python", folder="tests/unit/api", volume="tc", seconds=60,
                                    env=check["env"])
    assert containers.repo_env_flag(check["env"]) in probe and not any("V9_PROBE" in word for word in probe)


def test_the_check_reads_what_is_left_of_the_images_pytest_settings_after_the_apply_script():
    command = dev_check.settings_left_command("img", python="/usr/bin/python", env={"QT_QPA_PLATFORM": "offscreen"})
    assert command[-3:] == ["sh", "/v9/apply.sh", "env"] and "QT_QPA_PLATFORM=offscreen" in command


def test_the_probe_runs_in_the_same_container_as_g6s_arms_and_arm_1_in_the_pristine_image():
    probe = dev_check.probe_command("img", python="/usr/bin/python", folder="test/units/errors", volume="tc",
                                    seconds=600)
    g6 = containers.g6_command("img", toolchain="tc", study=dev_check.VALIDATION, repo_python="/usr/bin/python")
    cut = g6.index(containers.G6_PYTHON)
    assert probe[:cut] == g6[:cut]                                            # the same flags, mounts and setting
    assert probe[cut:] == [containers.G6_PYTHON, "-I", "/opt/v9/patch_review_utility/environment/probe.py",
                           "/v9/probe.json", "test/units/errors", "600"]
    assert dev_check.tests_command("img", python="/usr/bin/python") == containers.tests_command(
        "img", python="/usr/bin/python")
