# SPDX-License-Identifier: Apache-2.0
"""The pool ledger: every split of the frontier programme excludes every case id an earlier study or round drew."""
from __future__ import annotations

import json

import pytest

from frontier import ledger


def _splits(folder, name, splits):
    (folder / name).mkdir(parents=True)
    (folder / name / "splits.json").write_text(json.dumps({"splits": splits}), encoding="utf-8")


@pytest.fixture
def docs(tmp_path, monkeypatch):
    results, rounds = tmp_path / "results", tmp_path / "rounds"
    _splits(results, "V6", {"gsm8k": {"dev": ["g1"], "gate": ["g2"], "eval": ["g3"]}})
    _splits(results, "V6b", {"gsm8k": {"dev": ["g4"], "gate": [], "eval": ["g5"]}})
    _splits(rounds, "round-01", {"gsm8k": {"dev": ["g6"]}})
    monkeypatch.setattr(ledger, "RESULTS", results)
    monkeypatch.setattr(ledger, "ROUNDS", rounds)
    return tmp_path


def test_used_ids_reads_every_study_and_round(docs):
    assert ledger.used_ids("gsm8k") == {"g1", "g2", "g3", "g4", "g5", "g6"}
    assert ledger.used_ids("massive") == set()


def test_draw_excludes_every_used_id(docs):
    pool = [f"g{i}" for i in range(1, 40)]
    out = ledger.draw("gsm8k", pool, {"dev": 5, "selection": 10, "confirmation": 10}, seed=1, label="round-02")
    drawn = [i for ids in out.values() for i in ids]
    assert len(drawn) == len(set(drawn)) == 25
    assert not set(drawn) & ledger.used_ids("gsm8k")
    assert list(out) == ["dev", "selection", "confirmation"]


def test_draw_is_reproducible(docs):
    pool = [f"g{i}" for i in range(1, 40)]
    sizes = {"dev": 5, "selection": 10}
    assert ledger.draw("gsm8k", pool, sizes, seed=1, label="r") == ledger.draw("gsm8k", list(reversed(pool)), sizes,
                                                                               seed=1, label="r")


def test_draw_refuses_when_too_few_remain(docs):
    with pytest.raises(ValueError, match="gsm8k"):
        ledger.draw("gsm8k", [f"g{i}" for i in range(1, 10)], {"dev": 5}, seed=1, label="r")
