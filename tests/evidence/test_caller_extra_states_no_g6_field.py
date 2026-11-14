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

A caller's name is compared by its text, the text a reader of the entry gets (issuance review of that change,
finding 1). A key that is a ``str`` subclass equals its text and may hash elsewhere, so a lookup by hash missed it,
and in the entry's JSON it was the G6 field. Both writers that keep G6's fields from a caller's
(``mapping._provenance``, ``g6.ml._fit.job``) now read each caller name as exactly its text before they compare it,
and write it as that text.

Development fixtures only: no test runs, and no model runs.
"""
from __future__ import annotations

import ast
import enum
import inspect
import json
from types import SimpleNamespace

import pytest

from g6.evidence import mapping
from g6.ml import _fit
from tests.evidence.test_an_observed_failure_reaches_the_issuer import DECISIVE, FACTS, FLAKY_FAIL, SUBJECT


class Hashed(str):
    """Equal to its text, as every ``str`` is, and hashed elsewhere: a set or dict lookup by the text misses it."""

    def __hash__(self) -> int:
        return 7


class Plain(str):
    """A ``str`` subclass that keeps ``str``'s own hash."""


def member(text: str) -> str:
    """An enum member that is also a ``str`` with the value ``text``: a ``str`` subclass people do use as a key."""
    return enum.Enum("Field", {"NAME": text}, type=str).NAME


SUBCLASSES = pytest.mark.parametrize("named", [Hashed, Plain, member], ids=["own-hash", "str-hash", "enum"])


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


# --- a caller's name is its text, whatever class of ``str`` states it (issuance review, finding 1) --------------------


@SUBCLASSES
@pytest.mark.parametrize("record", [DECISIVE, FLAKY_FAIL], ids=["promoted", "caveat"])
def test_a_name_that_is_a_g6_fields_text_is_left_out_whatever_class_of_str_states_it(record, named):
    own = _entry(record)
    for key in sorted(mapping.PROVENANCE_OWN):
        got = _entry(record, {named(key): [["zz_caller", "sorts after G6's"]], "note": "kept"})
        assert got == {**own, "note": "kept"}, key


@SUBCLASSES
def test_every_g6_field_stated_at_once_by_its_text_changes_nothing(named):
    for record in (DECISIVE, FLAKY_FAIL):
        assert _entry(record, {named(key): "zz_caller" for key in mapping.PROVENANCE_OWN}) == _entry(record)


@SUBCLASSES
def test_a_callers_own_name_is_kept_under_its_text(named):
    written = mapping._provenance({"source_kind": "stated"}, {named("ran"): ["pytest"], named("scope"): "caller"})
    assert written == {"ran": ["pytest"], "source_kind": "stated"}
    assert all(type(key) is str for key in written), "the entry's names are text, exactly"
    assert _entry(DECISIVE, {named("ran"): ["pytest"]}) == {**_entry(DECISIVE), "ran": ["pytest"]}


def test_a_key_that_is_no_text_is_refused_by_the_ledger_as_before():
    """A key that is not a ``str`` is never a G6 field's text: the ledger takes text keys only, and refuses the
    entry."""
    for key in (1, None, ("refused",)):
        with pytest.raises(ValueError, match="not JSON"):
            _entry(DECISIVE, {key: "caller"})


def _job(**fields):
    prepared = SimpleNamespace(spec=SimpleNamespace(task="regression", seed=7),
                               split=SimpleNamespace(train=[0], held_out=[1]))
    encoded = SimpleNamespace(numeric=[0], categorical=[], X=[[1.0], [2.0]], y=[3.0, 4.0], groups=[0, 1])
    return _fit.job("fit", prepared, encoded, **fields)


def _as_the_child_reads(job):
    """``_fit.run`` writes ``json.dumps(job)`` as the child's job file; ``child_main`` reads it with ``json.loads``."""
    return json.loads(json.dumps(job))


@SUBCLASSES
def test_a_callers_field_never_replaces_what_g6_writes_in_a_childs_job(named):
    own = _job()
    for name in sorted(set(own) - {"kind"}):   # ``kind`` is the job's own parameter: Python refuses a second value
        job = _job(**{named(name): 99, "estimator": {"class": "Ridge"}})
        assert _as_the_child_reads(job) == {**_as_the_child_reads(own), "estimator": {"class": "Ridge"}}, name
        assert all(type(key) is str for key in job), name


@SUBCLASSES
def test_a_callers_own_field_is_in_the_job_under_its_text(named):
    job = _job(**{named("folds"): 3})
    assert job["folds"] == 3 and all(type(key) is str for key in job)
