# SPDX-License-Identifier: Apache-2.0
"""The freeze-readiness check of V9's and V10's scripts against a G6 source tree (``freeze_check.py``).

Development only: it reads G6's own source (this checkout's, which is the frozen review's, b3c342b), and the parity
fixtures it runs are V9's development fixtures. Nothing here calls a model."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from g6.workforce import repo_tests
from patch_review_search import arm_e, freeze_check

SRC = freeze_check.REPOSITORY / "src"


def probe(name: str):
    (found,) = [check for feature, _, check in freeze_check._probes() if feature == name]
    return found()


def feature(name, group, present=True):
    return {"name": name, "group": group, "present": present, **({} if present else {"problem": "not there"})}


def test_every_feature_has_one_name_and_a_known_group():
    names = [(name, group) for name, group, _ in freeze_check._probes()]
    assert len({name for name, _ in names}) == len(names) and {group for _, group in names} == set(freeze_check.GROUPS)
    assert set(freeze_check.PARITY) <= set(freeze_check.GROUPS)


def test_a_missing_feature_or_a_parity_fixture_that_skips_stops_the_freeze_only_when_its_group_is_required():
    found = [feature("review_patch", "base"), feature("shadowed", "pool", present=False)]
    fixtures = {"a::passing": {"group": "base", "passed": 3, "failed": 0, "skipped": 0},
                "a::skipping": {"group": "pool", "passed": 0, "failed": 0, "skipped": 4}}
    assert freeze_check.evaluate(found, fixtures, ["base"]) == []
    problems = freeze_check.evaluate(found, fixtures, ["base", "pool"])
    assert problems == ["pool: shadowed: not there", "pool: parity fixture skipping: 0 failed, 4 skipped"]
    absent = {"a::gone": {"group": "base", "passed": 0, "failed": 0, "skipped": 0}}
    assert freeze_check.evaluate([], absent, ["base"]) == ["base: parity fixture gone: not collected"]
    failing = {"a::red": {"group": "base", "passed": 2, "failed": 1, "skipped": 0}}
    assert freeze_check.evaluate([], failing, ["base"]) == ["base: parity fixture red: 1 failed, 0 skipped"]


def test_at_the_frozen_review_the_base_features_are_there_and_the_pool_and_search_features_are_not():
    found = {item["name"]: item for item in freeze_check.features(SRC)}
    groups = {name: group for name, group, _ in freeze_check._probes()}
    assert set(found) == set(groups)
    assert [name for name, item in found.items() if groups[name] == "base" and not item["present"]] == []
    assert all(not found[name]["present"] for name in groups if groups[name] in ("pool", "search"))
    assert found["review_patch"]["read"] and "problem" in found["shadowed"]


def test_a_feature_in_another_shape_is_reported_missing_with_what_changed(monkeypatch):
    monkeypatch.setattr(repo_tests, "shadowed", lambda sources, ran: {}, raising=False)
    with pytest.raises(freeze_check.Missing, match="module and file"):
        probe("shadowed")
    monkeypatch.setattr(repo_tests, "REPO_ENV", "G6_REPO_ENV", raising=False)
    monkeypatch.setattr(repo_tests, "_repo_env", lambda: {"QT_QPA_PLATFORM": "offscreen"}, raising=False)
    with pytest.raises(freeze_check.Missing, match="encoding"):
        probe("G6_REPO_ENV encoding")
    import re
    monkeypatch.setattr(repo_tests, "_RUNNER_NAMES", re.compile(r"PYTEST_.*|PATH"), raising=False)
    with pytest.raises(freeze_check.Missing, match="PYTHONPATH"):
        probe("G6_REPO_ENV refused names")


def test_arm_es_mirrors_are_compared_with_g6s_rules_and_a_difference_is_named(monkeypatch):
    fake = types.ModuleType("g6.harness.verification_search")
    fake.MAX_CHECKS, fake.MAX_SOURCE_BYTES = 25, arm_e.MAX_SOURCE_BYTES
    fake._source_problem = arm_e.source_problem
    monkeypatch.setitem(sys.modules, "g6.harness.verification_search", fake)
    with pytest.raises(freeze_check.Missing, match="limit on checks is 25"):
        probe("check limits")
    fake._NAME, fake._PART, fake._NOT_A_PLACE = arm_e._NAME, arm_e._PART, arm_e._NOT_A_PLACE
    fake._METADATA, fake.FOLDER = arm_e._METADATA, arm_e.FOLDER
    fake._file_problem = lambda file: "" if file.endswith(".py") else "no"
    with pytest.raises(freeze_check.Missing, match="conftest.py"):
        probe("check file rule")
    fake._file_problem = arm_e.file_problem
    assert probe("check file rule")["folder"] == arm_e.FOLDER


def test_g6s_search_must_take_each_search_arms_configuration_as_the_driver_gives_it(monkeypatch):
    fake = types.ModuleType("g6.harness.verification_search")
    fake.SEARCH_SETTINGS = {"templates": True}

    def config(value):
        unknown = [key for key in value if key not in fake.SEARCH_SETTINGS]
        if unknown:
            raise ValueError("search holds setting(s) G6 does not have: " + ", ".join(unknown))
        return {**fake.SEARCH_SETTINGS, **value}

    fake.search_config = config
    monkeypatch.setitem(sys.modules, "g6.harness.verification_search", fake)
    with pytest.raises(freeze_check.Missing, match="arm T"):
        probe("search settings")
    fake.SEARCH_SETTINGS = {"templates": True, "test_adequacy": False}
    assert probe("search settings")["arms"] == {"C": {"templates": False},
                                                "T": {"templates": True, "test_adequacy": True}}


def test_the_check_writes_its_report_and_fails_loudly_when_a_required_group_is_missing(tmp_path, capsys):
    out = tmp_path / "report.json"
    assert freeze_check.main(["--g6-src", str(freeze_check.REPOSITORY), "--require", "base", "--output", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ready"] is True and report["required"] == ["base"] and len(report["g6"]["tree_sha256"]) == 64
    parity = report["parity"]
    assert all(c["passed"] and not c["failed"] and not c["skipped"] for c in parity.values() if c["group"] == "base")
    assert all(c["skipped"] and not c["passed"] for c in parity.values() if c["group"] == "pool")
    every = freeze_check.evaluate(report["features"], parity, list(freeze_check.GROUPS))
    assert any(p.startswith("pool: shadowed") for p in every) and any("search: check limits" in p for p in every)
    assert any("parity fixture test_g6_and_the_direct_checks_read_a_submodule_folder_alike" in p for p in every)
    text = out.read_text(encoding="utf-8")
    assert str(Path.home()) not in text and str(freeze_check.REPOSITORY) not in text      # no local path is recorded
