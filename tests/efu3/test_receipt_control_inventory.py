"""Every control body field and both duplicate orders across the three views."""
import hashlib
import ast
from dataclasses import replace
from pathlib import Path

import pytest

from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, Check, Control, MissionFacts, Receipt, issue, record, verify
from g6.research.echa.view import certificate_view
from g6.evidence import certificate, mapping


@pytest.mark.parametrize("change", [
    {"killed": False}, {"check_sha": "d" * 64}, {"predicate": "R2"},
    {"generated_by": "caller"}, {"seq": 4},
])
@pytest.mark.parametrize("reverse", [False, True])
def test_control_body_choice_agrees_with_the_issuers_online_and_offline_reading(change, reverse):
    payload = b"subject bytes"
    sha = hashlib.sha256(payload).hexdigest()
    facts = MissionFacts("1" * 64, "5" * 64, "5" * 64, ("R1",), ("R1",))
    artifact = Artifact(sha, "author", 1)
    good = Control("same-control", "c" * 64, "R1", "g6", 2, True)
    bad = replace(good, **change)
    controls = (good, bad) if reverse else (bad, good)
    check = Check("check", ("R1",), "reviewer", "independent_executable", "c" * 64, 0, controls)
    receipt = Receipt("receipt", "check", "c" * 64, sha, "pass", 3)
    ledger = ChainLedger()
    record(ledger, good, receipt)
    body = issue(facts, artifact, [check], [receipt], ledger=ledger).as_dict()
    assert body["predicates"][0]["support"]
    assert verify(body, payload, ledger=ledger) == verify(body, payload) == []
    online = certificate_view(body, artifact_sha256=sha, ledger=ledger)
    offline = certificate_view(body, artifact_sha256=sha)
    online_role = online["predicates"][0]["evidence"][0]["role"]
    offline_role = offline["predicates"][0]["evidence"][0]["role"]
    assert offline_role == online_role == "supports"


def test_all_receipt_views_delegate_control_selection_to_the_same_issuer():
    issuer = ast.parse(Path(certificate.__file__).read_text(encoding="utf-8"))
    public_issue = next(n for n in issuer.body if isinstance(n, ast.FunctionDef) and n.name == "issue")
    assert any(isinstance(n, ast.Call) and ast.unparse(n.func) == "_issue" for n in ast.walk(public_issue))
    source = Path(mapping.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    roles = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "receipt_roles")
    calls = {ast.unparse(n.func) for n in ast.walk(roles) if isinstance(n, ast.Call)}
    assert {"issue", "_issuers_decision", "_offline_resolution"} <= calls
    assert "_held" not in {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    imports = {alias.asname: alias.name for n in tree.body if isinstance(n, ast.ImportFrom)
               and n.module == "g6.evidence.certificate" for alias in n.names}
    assert imports["_issuers_decision"] == "_issue"
    offline = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_offline_resolution")
    assert not any(isinstance(n, ast.Attribute) and n.attr == "control_id" for n in ast.walk(offline))
    assert not any(isinstance(n, ast.Call) and ast.unparse(n.func) == "record" for n in ast.walk(offline))
