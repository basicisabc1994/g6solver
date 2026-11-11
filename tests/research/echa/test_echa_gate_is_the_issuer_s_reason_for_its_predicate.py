# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""ECHA's view gives, for each predicate, the issuer's own reason for that predicate (G6 0.3, review 1 of slice 2's
follow-up, finding 4; class ``one_fact_two_rules``, met before).

The mapping named the gate a receipt did not meet by rerunning the issuer's rule beside it, and for the last step read
the certificate-wide ``unresolved``: a check naming R1 and R2 whose R2 control was missing from the ledger had R1 shown as
``not_in_ledger``, though the issuer said R1's control came after the receipt. Now the gate is read from the issuer's own
reason for that predicate, its gap in the certificate issued from that receipt alone (``mapping._gate``), and the rule is
not restated. The inventory lists, from the code, every field the view gives, each the issuer's for the same predicate
or a fact of the receipt, and plants a receipt with every pair of gates on two predicates.

Development fixtures only: no model runs, and no test runs.
"""
from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest

import g6
from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, Check, Control, MissionFacts, Receipt, issue, record
from g6.research.echa.view import WHY, certificate_view

# --- review 1's finding 4, brought in as this change's own ------------------------------------------------------------


def test_finding_4_the_gate_reason_is_for_this_predicate_not_another_predicate_s_missing_control():
    code, artifact_sha = "c" * 64, "a" * 64
    facts = MissionFacts("1" * 64, "5" * 64, "5" * 64, ("R1", "R2"), ("R1", "R2"))
    artifact = Artifact(artifact_sha, "agent", 1)
    late = Control("late", code, "R1", "g6", 4, True)
    missing = Control("missing", code, "R2", "g6", 2, True)
    check = Check("check", ("R1", "R2"), "reviewer", "independent_executable", code, 0, (late, missing))
    receipt = Receipt("receipt", "check", code, artifact_sha, "pass", 3)
    ledger = ChainLedger()
    record(ledger, late, receipt)
    cert = issue(facts, artifact, [check], [receipt], ledger=ledger)
    assert dict(cert.gaps)["predicate:R1"] == "control_after_receipt:check"
    assert dict(cert.gaps)["predicate:R2"].startswith("not_in_ledger:")
    view = certificate_view(cert.as_dict(), artifact_sha256=artifact_sha, ledger=ledger)
    shown = view["predicates"][0]["evidence"][0]
    assert shown["gate"] == "control_after_receipt", "R2's missing control was lent to R1's gate explanation"


# --- plants: one receipt, two predicates, every pair of the gates that differ by predicate ----------------------------

A, B, STANDARD, CODE, OTHER_CODE = "a" * 64, "b" * 64, "5" * 64, "c" * 64, "d" * 64
FACTS = MissionFacts("1" * 64, STANDARD, STANDARD, ("R1", "R2"), ("R1", "R2"))
SUBJECT = Artifact(A, "agent", 1)
#: What one passing receipt meets for one predicate: a sound control before it, held in the ledger (``supports``); no
#: control (``not_grounded``); a control after it; a control before it the ledger does not hold.
BY_PREDICATE = ("supports", "not_grounded", "control_after_receipt", "not_in_ledger")


def plant(r1: str, r2: str, *, result: str = "pass", on: str = A, code: str = CODE, seq: int = 3,
          positive_seq: int | None = None) -> tuple[dict, ChainLedger]:
    controls, held = [], []
    for predicate, how in (("R1", r1), ("R2", r2)):
        if how == "not_grounded":
            continue
        control = Control(f"control-{predicate}", CODE, predicate, "g6", 4 if how == "control_after_receipt" else 2,
                          True)
        controls.append(control)
        if how != "not_in_ledger":
            held.append(control)
    check = Check("check", ("R1", "R2"), "reviewer", "independent_executable", CODE, 0, tuple(controls),
                  positive_seq=positive_seq)
    receipt = Receipt("receipt", "check", code, on, result, seq)
    ledger = ChainLedger()
    record(ledger, *held, receipt)
    return issue(FACTS, SUBJECT, [check], [receipt], ledger=ledger).as_dict(), ledger


def shown(body: dict, ledger: ChainLedger | None) -> dict[str, tuple[str, str]]:
    view = certificate_view(body, artifact_sha256=A, ledger=ledger)
    return {p["predicate"]: (p["evidence"][0]["role"], p["evidence"][0]["gate"]) for p in view["predicates"]}


def issuer_reason(body: dict, predicate: str) -> str:
    """The issuer's own reason for ``predicate``: its gap, whose first part names the gate (one receipt, one reason)."""
    return dict((subject, why) for subject, why in body["gaps"]).get(f"predicate:{predicate}", "")


@pytest.mark.parametrize("offline", [False, True], ids=["with-the-ledger", "offline"])
@pytest.mark.parametrize(("r1", "r2"), list(itertools.product(BY_PREDICATE, repeat=2)),
                         ids=[f"{a}+{b}" for a, b in itertools.product(BY_PREDICATE, repeat=2)])
def test_each_predicate_s_gate_is_the_issuer_s_reason_for_that_predicate(r1, r2, offline):
    body, ledger = plant(r1, r2)
    got = shown(body, None if offline else ledger)
    for predicate, how in (("R1", r1), ("R2", r2)):
        if how == "supports":
            assert got[predicate] == ("supports", "") and not issuer_reason(body, predicate), (predicate, got)
        else:
            assert got[predicate] == ("not counted", how), (predicate, got)
            assert issuer_reason(body, predicate).startswith(how + ":"), (predicate, body["gaps"])


@pytest.mark.parametrize(("gate", "over"), [
    ("receipt_for_other_bytes", {"on": B}), ("check_changed_after_freeze", {"code": OTHER_CODE}),
    ("receipt_before_artifact", {"seq": 0}),
], ids=["other-bytes", "other-code", "before-the-artefact"])
def test_a_gate_of_the_whole_receipt_is_the_issuer_s_reason_for_each_predicate(gate, over):
    body, ledger = plant("supports", "not_in_ledger", **over)
    got = shown(body, ledger)
    for predicate in ("R1", "R2"):
        assert got[predicate] == ("not counted", gate) and issuer_reason(body, predicate).startswith(gate + ":")


def test_a_failure_refutes_one_predicate_and_is_a_caveat_for_another_each_as_the_issuer_says():
    body, ledger = plant("supports", "not_grounded", result="fail", seq=5, positive_seq=4)
    assert shown(body, ledger) == {"R1": ("refutes", ""), "R2": ("caveat", "")}
    assert body["predicates"][0]["refuted_by"] == ["check"] and body["predicates"][1]["caveats"] == ["check"]


def test_every_gate_shown_has_its_words():
    for r1, r2 in itertools.product(BY_PREDICATE, repeat=2):
        body, ledger = plant(r1, r2)
        view = certificate_view(body, artifact_sha256=A, ledger=ledger)
        assert all(e["why"] == WHY.get(e["gate"], "") and (e["gate"] == "" or e["why"])
                   for p in view["predicates"] for e in p["evidence"])


# --- the inventory: every field the view gives, from the code, and where each reason comes from -----------------------

SRC = Path(g6.__file__).resolve().parent
VIEW, MAPPING = SRC / "research" / "echa" / "view.py", SRC / "evidence" / "mapping.py"
FACT = "a fact of the receipt or the certificate, copied: no reason"
LEDGER = "whether the ledger holds a record with the body the certificate names: custody, read from the ledger"
#: Every field of the view's dicts, by the dict it is in, with where it comes from.
FIELDS = {
    "evidence": {
        "receipt_id": FACT, "check_id": FACT, "kind": FACT, "result": FACT,
        "role": ("the issuer's role for that receipt alone and that predicate (mapping.receipt_roles), or not "
                 "counted for the view's own gate"),
        "gate": ("the issuer's reason for that predicate (mapping._gate: its gap for the predicate), or the view's "
                 "own gate: the bytes the receipt's custody binding names, which are the receipt's, the same for "
                 "every predicate, and which the issuer never sees"),
        "why": "the words for gate (WHY)",
        "receipt_in_ledger": LEDGER, "controls": LEDGER + " (each control the check names for that predicate)",
        "provenance": "what the mapping kept beside the receipt in the ledger, copied",
    },
    "control": {"control_id": FACT, "killed": FACT + " (as the certificate records it)", "in_ledger": LEDGER},
    "predicate": {
        "predicate": FACT, "status": "the issuer's statement for that predicate, copied (STATUS_FIELDS)",
        "gap": "the issuer's gap for that predicate, copied", "evidence": "the receipts of checks naming it",
        "uncounted": "the checks of its receipts that the issuer's statement for it does not name",
    },
    "view": {
        "artifact_sha256": FACT, "state": FACT, "standard_sha256": FACT, "certificate_digest": FACT,
        "predicates": "each predicate's entry", "unresolved": FACT + " (the certificate's own list, shown as it is)",
        "authority": FACT,
    },
}


def _view_dicts() -> dict[str, set[str]]:
    view = next(f for f in ast.walk(ast.parse(VIEW.read_text(encoding="utf-8")))
                if isinstance(f, ast.FunctionDef) and f.name == "_view")
    found: dict[str, set[str]] = {}
    for node in ast.walk(view):
        if isinstance(node, ast.Dict) and node.keys:   # an empty one is a default, never a field the view gives
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            where = ("evidence" if "receipt_id" in keys else "control" if "control_id" in keys
                     else "predicate" if "predicate" in keys else "view" if "authority" in keys else "")
            assert where, f"a dict the view gives that the inventory cannot place: {sorted(keys)}"
            if where:
                found[where] = keys
    return found


def test_every_field_the_view_gives_is_listed_with_where_it_comes_from():
    found = _view_dicts()
    assert set(found) == set(FIELDS), found
    for where, keys in found.items():
        assert sorted(keys - set(FIELDS[where])) == [], f"a field of the view's {where} the inventory does not list"
        assert sorted(set(FIELDS[where]) - keys) == [], f"listed {where} fields the view no longer gives"


def _function(path: Path, name: str) -> ast.FunctionDef:
    return next(f for f in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(f, ast.FunctionDef) and f.name == name)


def test_each_reason_the_view_gives_is_read_for_its_own_predicate():
    view = ast.unparse(_function(VIEW, "_view"))
    # the role and the gate: the view's own gate of the receipt's bytes, else the issuer's for that predicate (name)
    assert "role, why = ('not counted', other) if other else roles.get(name, ('not counted', 'not_a_receipt'))" in view
    assert "'gate': why" in view and "'why': WHY.get(why, '')" in view and "'role': role" in view
    assert "'gap': gaps.get(f'predicate:{name}', '')" in view
    assert "status = {field: statement[field] for field in STATUS_FIELDS}" in view


def certificate_reads(function: ast.FunctionDef, name: str) -> set[str]:
    """The attributes ``function`` reads of the certificate it holds as ``name``."""
    return {n.attr for n in ast.walk(function) if isinstance(n, ast.Attribute) and ast.unparse(n.value) == name}


def test_the_issuer_s_reason_is_read_only_from_its_output_for_that_predicate():
    gate, roles = _function(MAPPING, "_gate"), _function(MAPPING, "receipt_roles")
    assert certificate_reads(gate, "alone") == {"gaps"}
    assert "dict(alone.gaps).get(f'predicate:{predicate}', '')" in ast.unparse(gate)
    assert certificate_reads(roles, "alone") == {"predicates"}   # each statement, by its own predicate
    assert certificate_reads(roles, "statement") == {"predicate", "supported_by", "refuted_by", "caveats"}
    assert "_gate(statement.predicate, alone)" in ast.unparse(roles)
    assert not any(isinstance(n, ast.FunctionDef) and n.name != "_gate" for n in ast.walk(gate))


def test_the_scan_finds_a_planted_certificate_wide_read():
    planted = ast.parse("def _gate(predicate, alone):\n    return 'not_in_ledger' if alone.unresolved else ''\n")
    assert certificate_reads(planted.body[0], "alone") == {"unresolved"}
