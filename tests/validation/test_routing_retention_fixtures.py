# SPDX-License-Identifier: Apache-2.0
"""Hidden retention checks fail each seed and accept an independent reference repair."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2] / "validation/routing"
REPAIRS = {
    "rounding": ("ROUND_DOWN", "ROUND_HALF_UP"),
    "utc": ("return datetime.datetime.fromisoformat(value).date()",
            "return datetime.datetime.fromisoformat(value).astimezone(datetime.timezone.utc).date()"),
    "ownership": ("return copy.copy(value)", "return copy.deepcopy(value)"),
    "cache": ("key = len(rows)", "key = tuple(rows)"),
}


@pytest.mark.parametrize("family", REPAIRS)
@pytest.mark.parametrize("stage", ("a", "b"))
def test_hidden_checks_reject_seed_accept_repair_and_reject_unrelated_edit(family, stage, monkeypatch):
    source = HERE / "retention_fixtures" / family / stage / "engine.py"
    check = HERE / "retention_outcome" / family / stage / "test_outcome.py"
    spec = importlib.util.spec_from_file_location("retention_hidden", check)
    hidden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hidden)
    checks = [v for k, v in vars(hidden).items() if k.startswith("test_")]
    assert checks
    before, after = REPAIRS[family]
    original = source.read_text(encoding="utf-8")
    assert before in original

    def probe(text):
        module = type(sys)("engine")
        exec(compile(text, str(source), "exec"), module.__dict__)
        monkeypatch.setitem(sys.modules, "engine", module)
        for test in checks:
            test()

    with pytest.raises(AssertionError):
        probe(original)
    probe(original.replace(before, after))
    with pytest.raises(AssertionError):
        probe(original + "\n# formatting changed\n")


def test_retention_materials_are_cue_free_and_checks_are_outside_every_fixture():
    data = json.loads((HERE / "retention_tasks.json").read_text(encoding="utf-8"))
    for task in data["tasks"]:
        assert task["source_ids"] and task["fair"]
        folder = HERE / task["snapshot"]
        for name in ("engine.py", "README.md", "tests/test_engine.py"):
            text = (folder / name).read_text(encoding="utf-8")
            assert not re.search(r"\b(?:g6|routing|trials?|measurements?|hidden)\b", text, re.I)
        assert not (folder / "test_outcome.py").exists()
