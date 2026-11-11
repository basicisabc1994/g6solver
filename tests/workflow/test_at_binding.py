# SPDX-License-Identifier: Apache-2.0
"""G6-UX-VALUE-001's acceptance tests, bound to tests and fixtures (design 17.5): bound by the digest of the owner's
text, never by a short description; a partial or deferred test is never reported as accepted; and a bound test id
that no longer exists is found here."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAPPING = ROOT / "validation" / "workflow_value" / "at_mapping.json"
APPROVED = ("AT-003", "AT-004", "AT-005", "AT-007", "AT-008")
HEX = re.compile(r"[0-9a-f]{64}\Z")
#: Tests the slices still to be built will add (S5 to S7); a bound id may wait for its slice, by name, and no other.
LATER = {"tests/workflow/test_diagnose.py": "S5", "tests/workflow/test_reassess.py": "S5",
         "tests/workflow/test_suggest.py": "S6", "tests/workflow/test_decisions.py": "S6",
         "tests/core/test_mcp_suggestion.py": "S6"}


def _mapping() -> dict:
    return json.loads(MAPPING.read_text(encoding="utf-8"))


def _rows() -> dict[str, dict]:
    return {row["id"]: row for row in _mapping()["rows"]}


def accepted(row: dict) -> bool:
    """Whether a row may be reported as accepted: in 0.3.0's scope, bound by digest, run, with a passing result."""
    return (row.get("scope") == "0.3.0" and bool(HEX.match(str(row.get("definition_sha256", ""))))
            and bool(HEX.match(str(row.get("spec_sha256", "")))) and row.get("status") == "run"
            and row.get("result") == "passed")


def _functions(path: Path) -> set[str]:
    return {node.name for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_every_at_has_a_mapping_row():
    rows = _rows()
    assert sorted(rows) == [f"AT-{n:03d}" for n in range(1, 11)] and len(_mapping()["rows"]) == 10
    spec = _mapping()["spec_sha256"]
    assert HEX.match(spec)
    for row in rows.values():
        assert HEX.match(row["definition_sha256"]) and row["spec_sha256"] == spec
        assert row["scope"] in ("0.3.0", "partial", "deferred") and row["status"] in ("bound", "run", "deferred")
    assert len({row["definition_sha256"] for row in rows.values()}) == 10


def test_an_at_with_no_definition_digest_cannot_be_reported_as_passed():
    row = {**_rows()["AT-003"], "status": "run", "result": "passed"}
    assert accepted(row)
    for missing in ({"definition_sha256": ""}, {"definition_sha256": "Constraint discrimination"},
                    {"spec_sha256": ""}, {"status": "bound"}, {"result": None}, {"result": "failed"}):
        assert not accepted({**row, **missing}), missing
    assert not any(accepted(row) for row in _rows().values())            # nothing has been run under its gate yet


def test_the_five_approved_ats_are_in_scope_and_bound():
    rows = _rows()
    assert tuple(sorted(ident for ident, row in rows.items() if row["scope"] == "0.3.0")) == APPROVED
    for ident in APPROVED:
        assert rows[ident]["status"] == "bound" and rows[ident]["gate"] and rows[ident]["tests"]
        assert any((ROOT / test.split("::")[0]).is_file() for test in rows[ident]["tests"]) or all(
            test.split("::")[0] in LATER for test in rows[ident]["tests"]), ident


def test_a_deferred_or_partial_at_is_never_reported_as_accepted():
    for ident, row in _rows().items():
        if row["scope"] != "0.3.0":
            assert row["deferred_part"], ident
            assert not accepted({**row, "status": "run", "result": "passed"}), ident
    text = MAPPING.read_text(encoding="utf-8").lower()
    assert '"accepted"' not in text and "never reported as accepted" in text


def test_the_owners_text_is_bound_by_digest_and_not_copied():
    text = MAPPING.read_text(encoding="utf-8")
    for phrase in ("Given a vague task request", "does not automatically prioritise", "tentative bottleneck hypothesis",
                   "repeatedly promote", "verified business impact"):
        assert phrase not in text


@pytest.mark.parametrize("ident", [f"AT-{n:03d}" for n in range(1, 11)])
def test_every_bound_test_id_exists(ident):
    # inventory: a renamed or removed test cannot leave an acceptance test bound to nothing
    row = _rows()[ident]
    assert row["tests"]
    for test in row["tests"]:
        file, _, name = test.partition("::")
        path = ROOT / file
        if not path.is_file():
            assert file in LATER, f"{test}: no such test file, and no slice still to be built owns it"
            continue
        assert name in _functions(path), f"{test}: no such test"
    for fixture in row["fixtures"]:
        assert (ROOT / "tests" / "workflow" / "fixtures" / "contracts" / (fixture + ".json")).is_file(), fixture
