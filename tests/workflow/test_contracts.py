# SPDX-License-Identifier: Apache-2.0
"""The local deterministic contracts of design 17.5: every fixture's actual output equals its frozen expected output,
with zero unsupported definite claims and zero unauthorised disclosures. Fixture success is not a developer-value
result, and nothing here says it is."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contracts"
#: Every word a fixture's own text may use: fixtures are synthetic, never a person's task or report.
SYNTHETIC = re.compile(r"[A-Za-z0-9_ .,:;'`*#()\[\]{}<>/=+\-|\"\\!?]*\Z")


def _runner():
    spec = importlib.util.spec_from_file_location("run_contracts", ROOT / "validation" / "workflow_value" / "run_contracts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cases() -> list[Path]:
    return sorted(FIXTURES.rglob("*.json"))


def test_every_fixture_matches_its_expected_output(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "unused"))
    result = _runner().run(FIXTURES)
    assert result["cases"] == len(_cases()) >= 14
    assert result["mismatches"] == [] and result["matched"] == result["cases"]
    assert result["unsupported_definite_claims"] == 0 and result["unauthorised_disclosures"] == 0
    assert not (tmp_path / "unused").exists()                 # the runner works in a scratch home of its own


@pytest.mark.parametrize("contract,least", [(1, 5), (2, 2), (4, 2), (5, 2)])
def test_each_contract_of_this_slice_has_its_cases(contract, least):
    held = [json.loads(path.read_text(encoding="utf-8")) for path in _cases() if path.parent.name == str(contract)]
    assert len(held) >= least and all(one["contract"] == contract for one in held)
    assert all(set(one) <= {"contract", "function", "events", "inputs", "expected"} and one["expected"] for one in held)


def test_a_changed_output_is_a_mismatch_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "unused"))
    source = FIXTURES / "1" / "explicit_blocking_dependency_gives_the_scoped_obstacle.json"
    fixture = json.loads(source.read_text(encoding="utf-8"))
    fixture["expected"]["obstacles"] = []
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / source.name).write_text(json.dumps(fixture), encoding="utf-8")
    broken = dict(fixture, function="no_such_function")
    (tmp_path / "1" / "broken.json").write_text(json.dumps(broken), encoding="utf-8")
    result = _runner().run(tmp_path / "1")
    assert result["cases"] == 2 and result["matched"] == 0 and len(result["mismatches"]) == 2
    assert _runner().main([str(tmp_path / "1")]) == 1 and _runner().main([str(tmp_path / "empty")]) == 1


def test_fixture_folder_holds_no_private_path_or_real_task_text():
    assert _cases()
    for path in _cases():
        text = path.read_text(encoding="utf-8")
        for private in ("Users\\\\", "Users/", "/home/", "App" + "Data", "C:\\\\", "@", "http"):
            assert private not in text, (path.name, private)
        assert SYNTHETIC.match(text.replace("\n", " ")), path.name
        fixture = json.loads(text)
        requests = [fixture.get("inputs", {}).get("request", ""), (fixture.get("inputs", {}).get("intent") or {}).get("goal", "")]
        assert set(requests) <= {"", "fix this bug", "PLANTED-REQUEST", "PLANTED-GOAL", "add returns the sum"}


def test_fixture_success_is_described_nowhere_as_a_developer_value_result():
    runner = _runner()
    assert "not a developer-value result" in runner.run.__doc__ or "not a developer-value result" in runner.run(FIXTURES)["note"]
    readme = (ROOT / "validation" / "workflow_value" / "README.md").read_text(encoding="utf-8").lower()
    assert "fixture success is not a developer-value result" in " ".join(readme.split())
    for banned in ("developers found", "proved useful", "verified benefit", "saves time"):
        assert banned not in readme


def test_the_simulated_pre_flight_measures_what_the_view_adds(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "unused"))
    result = _runner().simulate(6)
    assert result["simulated"] is True and result["sessions"] == 6
    measures = result["measures"]
    assert measures["added_block_lines"]["value"] <= 8 and measures["added_questions"]["value"] == 0
    assert 0 <= measures["added_wall_seconds_median"]["value"] <= measures["added_wall_seconds_worst"]["value"]
    assert all(one["n"] == 6 for one in measures.values())
