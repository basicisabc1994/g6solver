# SPDX-License-Identifier: Apache-2.0
"""The versioned storage policy (design 17.3): finite retention for everything the workflow record stores, published
before collection is enabled, and a switch that is off unless the person turned it on."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import g6.harness.workflow
from g6.harness.workflow import policy

_PACKAGE = Path(g6.harness.workflow.__file__).parent
_DOC = Path(__file__).resolve().parents[2] / "docs" / "product_global_specs" / "workflow_storage_policy.md"
_FILE_NAME = re.compile(r"\.(jsonl|json|head|lock|part|tmp|new)\Z")


def _strings(path: Path) -> set[str]:
    """Every string constant in a module that is not a docstring: where a file name would have to be written."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)}
    return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docs}


def test_every_stored_kind_has_a_finite_retention():
    # inventory, from the code: a file name is written in policy.py and nowhere else in the package, and every name
    # there has a period (or is the one file the person writes)
    for path in sorted(_PACKAGE.rglob("*.py")):
        if path.name != "policy.py":
            assert not {s for s in _strings(path) if _FILE_NAME.search(s)}, path.name
    names = {s for s in _strings(_PACKAGE / "policy.py") if _FILE_NAME.search(s)}
    assert names and names == set(policy.FILES)
    for name, kind in policy.FILES.items():
        if kind in ("person", "sidecar"):           # the person's own file; a sidecar goes with the file it serves
            continue
        days = policy.RETENTION_DAYS[kind]
        assert isinstance(days, int) and 0 < days <= 3650, name
    assert set(policy.FILES.values()) - {"person", "sidecar"} == set(policy.RETENTION_DAYS)
    assert {name for name, kind in policy.FILES.items() if kind == "sidecar"} == set(policy.SIDECARS)
    assert tuple(name for name, kind in policy.FILES.items() if kind == "person") == policy.PERSON_FILES
    assert policy.PERSON_FILES == ("corrections.json",)


def test_policy_doc_states_each_period():
    text = " ".join(_DOC.read_text(encoding="utf-8").split())
    assert policy.POLICY_VERSION in text
    for kind, days in policy.RETENTION_DAYS.items():
        assert re.search(rf"\b{kind}\b[^|]*\|[^|]*\b{days} days\b", text), kind
    for name in policy.FILES:
        assert name.lstrip(".") in text, name
    for field in policy.RECEIPT_FIELDS:
        assert f"`{field}`" in text
    assert "G6_WORKFLOW_RECORD=on" in text and "second worktree is a second project" in text


def test_the_record_is_off_until_the_person_turns_it_on(monkeypatch):
    monkeypatch.delenv(policy.RECORD_ENV, raising=False)
    assert policy.ON_BY_DEFAULT is False                  # the one line the owner's answer would change
    on, why = policy.enabled()
    assert on is False and policy.RECORD_ENV in why
    monkeypatch.setenv(policy.RECORD_ENV, "on")
    assert policy.enabled() == (True, "")
    monkeypatch.setenv(policy.RECORD_ENV, "off")
    assert policy.enabled()[0] is False


@pytest.mark.parametrize("value", ["", " ", "ON", "yes", "1", "true", "on ", "off\n", "on;off", "Zebra-77"])
def test_unreadable_switch_value_turns_collection_off(monkeypatch, value):
    monkeypatch.setenv(policy.RECORD_ENV, value)
    on, why = policy.enabled()
    assert on is False and "cannot read" in why and "Zebra-77" not in why      # the value is not repeated


def test_default_on_is_one_constant(monkeypatch):
    monkeypatch.delenv(policy.RECORD_ENV, raising=False)
    monkeypatch.setattr(policy, "ON_BY_DEFAULT", True)
    assert policy.enabled() == (True, "")
    monkeypatch.setenv(policy.RECORD_ENV, "off")
    assert policy.enabled()[0] is False


def test_receipt_fields_are_the_declared_minimum():
    assert policy.RECEIPT_FIELDS == ("policy_version", "scope", "scope_id_sha256", "removed", "at", "reason")
