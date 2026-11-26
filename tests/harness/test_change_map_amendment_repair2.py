# SPDX-License-Identifier: Apache-2.0
"""Genuine collected contents and bounded ancestor admission remain useful."""
import pytest

from g6.harness import change_map
from tests._review_cmfu2.test_authority import mapped, oracle
from tests._review_changemap1.test_review import repository
from tests.harness.test_change_map_repair2 import API


@pytest.mark.parametrize("kind", ["plain", "future_nested", "inherited", "assertion"])
def test_genuine_collected_contents_keep_positive(tmp_path, monkeypatch, kind):
    source = "from api import neighbour\ndef test_call():\n    neighbour()\n"
    others = {}
    if kind == "future_nested":
        source = ("from __future__ import annotations\nfrom api import neighbour\n"
                  "def test_call() -> None:\n"
                  "    def inner() -> None:\n        neighbour()\n    inner()\n")
    elif kind == "inherited":
        others["base.py"] = ("from __future__ import annotations\nfrom api import neighbour\n"
                             "class Parent:\n    def test_call(self) -> None:\n        neighbour()\n")
        source = "from base import Parent\nclass TestAPI(Parent):\n    pass\n"
    elif kind == "assertion":
        source = "from api import neighbour\ndef test_call():\n    assert neighbour() == 2\n"
    root = tmp_path / "reviewed"
    before = {"api.py": API, "test_api.py": source, **others}
    delta = repository(root, before, {**before, "api.py": API.replace("return 1", "return 2")})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    run, answer = oracle(root, extra=("--rootdir", str(tmp_path)))
    assert run.returncode == 0, run.stdout + run.stderr
    node = "test_api.py::" + ("TestAPI::" if kind == "inherited" else "") + "test_call"
    assert answer["nodes"] == [node]
    assert answer.get("unresolved", []) == []
    assert answer["codes"][node]
    assert answer["bodies"][node]["file"] == ("base.py" if kind == "inherited" else "test_api.py")
    assert mapped(delta, answer)["exercised"] is True


@pytest.mark.parametrize("suppressed", [False, True])
def test_deep_shared_ancestor_module_keeps_bounded_admission(suppressed):
    base = "from api import neighbour\nclass Base0:\n    def test_call(self):\n        neighbour()\n"
    base += "".join(f"class Base{i}(Base{i - 1}):\n    pass\n" for i in range(1, 450))
    if suppressed:
        base += "Base0.__test__ = False\n"
    source = "from base import Base449\nclass TestDeep(Base449):\n    pass\n"
    node = "test_api.py::TestDeep::test_call"
    found = change_map.exercised([{"binding": "api.neighbour"}], {node: source, "base.py": base})
    assert found == {"api.neighbour": [] if suppressed else [node]}
