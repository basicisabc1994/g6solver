# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""No caller ``extra`` states a field G6 itself writes in a provenance entry (review-efu3, finding 4; the certificate
issuance window before 0.3.0).

``issue_records`` writes one provenance entry beside each promoted receipt and one beside each caveat. Both merged the
caller's ``extra`` last, so a caller could replace the refusal reasons of a caveat, the kind a receipt was promoted as,
or any other field a reader takes as G6's own. The class, not that one field: every field either writer states
(``PROVENANCE_OWN``) is G6's in both entries, whether or not this entry has it, so a caller can neither replace one nor
add one G6 left out (a promoted receipt marked ``kept_as: caveat``, a caveat given a ``binding``). What a caller adds
under any other name is kept.

Development fixtures only: no test runs, and no model runs.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from g6.evidence import mapping
from tests.evidence.test_an_observed_failure_reaches_the_issuer import DECISIVE, FACTS, FLAKY_FAIL, SUBJECT


def _entry(record, extra=None):
    issued = mapping.issue_records(FACTS, SUBJECT, [record], extra=extra)
    ident = issued.caveats[0][1] if issued.caveats else record.record_id
    return issued.ledger.get(mapping.PROVENANCE_KIND, ident)


@pytest.mark.parametrize("record", [DECISIVE, FLAKY_FAIL], ids=["promoted", "caveat"])
def test_a_caller_states_no_field_of_either_entry_in_either_entry(record):
    own = _entry(record)
    for key in sorted(mapping.PROVENANCE_OWN):
        got = _entry(record, {key: "caller", "note": "kept"})
        assert got == {**own, "note": "kept"}, key


def test_the_reserved_fields_are_every_field_a_provenance_entry_is_written_with():
    """From the code: each string key of a dict literal handed to the one writer of a provenance entry."""
    tree = ast.parse(inspect.getsource(mapping.issue_records))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "_provenance"]
    assert len(calls) == 2, "a promoted receipt's entry and a caveat's"
    written = {key.value for call in calls for part in ast.walk(call.args[0]) if isinstance(part, ast.Dict)
               for key in part.keys if isinstance(key, ast.Constant)}
    assert written == set(mapping.PROVENANCE_OWN)
    appended = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "append"
                and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "PROVENANCE_KIND"]
    assert len(appended) == 2 and all(
        isinstance(node.args[2], ast.Call) and node.args[2].func.id == "_provenance" for node in appended), (
        "every provenance entry goes through the one writer")


def test_what_a_caller_adds_under_its_own_names_is_kept():
    assert _entry(DECISIVE, {}) == _entry(DECISIVE)
    assert _entry(DECISIVE, {"ran": ["pytest", "-q"]}) == {**_entry(DECISIVE), "ran": ["pytest", "-q"]}
    assert _entry(FLAKY_FAIL, {"ran": ["pytest", "-q"]}) == {**_entry(FLAKY_FAIL), "ran": ["pytest", "-q"]}
