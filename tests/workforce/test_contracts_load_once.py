# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 010 WF-009 (and the packaging half of WF-010).

The archive parsed every file of a generated 5,110-contract catalogue on each bind, and one malformed file anywhere in
``params/`` stopped the whole workforce from binding. The library ships only the workforce's own contracts, parses
them once per process, and never reads a file of any other name; a malformed workforce contract still refuses (fails
closed).
"""
from __future__ import annotations

import json
import shutil

import pytest
from g6.workforce import api, service
from g6.workforce import capabilities as wcap


def _clear() -> None:
    cached = getattr(wcap, "shipped_contracts", None)
    if cached is not None:
        cached.cache_clear()


@pytest.fixture
def params(tmp_path, monkeypatch):
    copy = tmp_path / "params"
    shutil.copytree(wcap.PARAMS_DIR, copy)
    monkeypatch.setattr(wcap, "PARAMS_DIR", copy)
    _clear()
    yield copy
    _clear()


def test_wf009_shipped_contracts_are_parsed_once_per_process(params, monkeypatch):
    parsed = []
    real = wcap.ParamContract.from_dict
    monkeypatch.setattr(wcap.ParamContract, "from_dict", classmethod(lambda cls, data: parsed.append(1) or real(data)))
    first = wcap.load_contracts()
    once = len(parsed)
    assert once == len(first) > 0
    assert wcap.load_contracts() == first
    assert len(parsed) == once  # the second read parses nothing


def test_wf009_a_malformed_file_of_another_name_never_stops_the_workforce(params, tmp_path, monkeypatch):
    (params / "other_component.op.json").write_text("{not json", encoding="utf-8")
    (params / "README.json").write_text(json.dumps({"component": "nothing"}), encoding="utf-8")
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path / "workforce"))
    wf = api.bind()
    assert set(wf.caps.contracts()) == {("workforce", "run_tests"), ("workforce", "run_tests_in_place")}


def test_a_malformed_workforce_contract_still_refuses(params):
    (params / "workforce.run_tests.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        wcap.load_contracts()
