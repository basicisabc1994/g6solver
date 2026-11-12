# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Support is advertised per tested fragment: an adapter without a fragment entry and a test that names it fails."""
from __future__ import annotations

from pathlib import Path

from g6.harness.formalisation import ADAPTERS, FRAGMENTS

ROOT = Path(__file__).resolve().parents[2]


def test_every_adapter_has_a_tested_fragment():
    assert ADAPTERS, "no adapter is registered"
    by_adapter = {entry["adapter"]: (fragment, entry) for fragment, entry in FRAGMENTS.items()}
    for name, adapter in ADAPTERS.items():
        assert name == adapter.id and name in by_adapter, f"{name} has no fragment entry"
        fragment, entry = by_adapter[name]
        assert fragment == adapter.fragment and entry["tested"] is True and entry["tests"]
        for module in entry["tests"]:
            text = (ROOT / module).read_text(encoding="utf-8")
            assert name in text, f"{module} does not name the adapter {name}"
            assert "def test_" in text


def test_every_fragment_names_a_registered_adapter_and_a_language():
    for fragment, entry in FRAGMENTS.items():
        assert entry["adapter"] in ADAPTERS and ADAPTERS[entry["adapter"]].fragment == fragment
        assert entry["language"] in ("smt", "lean4", "tla", "datalog")
        assert fragment.split(".")[0] in ("smt", "lean", "tla", "datalog") and fragment.rsplit(".", 1)[1].startswith("v")


def test_an_adapter_registered_without_tests_is_not_tested(monkeypatch):
    from g6.harness.formalisation import adapters

    class Bare:
        id, fragment, requires_function = "smt.bare.v1", "smt.bare_fragment.v1", False

    monkeypatch.setattr(adapters, "ADAPTERS", dict(adapters.ADAPTERS))
    monkeypatch.setattr(adapters, "FRAGMENTS", dict(adapters.FRAGMENTS))
    adapters.register(Bare(), language="smt", tests=())
    assert adapters.FRAGMENTS["smt.bare_fragment.v1"]["tested"] is False