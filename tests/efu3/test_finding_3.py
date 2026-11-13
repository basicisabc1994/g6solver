"""The offline view must preserve the issuer's offline receipt reading."""
import hashlib
from dataclasses import replace

from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, Check, Control, MissionFacts, Receipt, issue, record, verify
from g6.research.echa.view import certificate_view


def test_offline_view_does_not_choose_a_different_body_for_a_control_id():
    payload = b"subject bytes"
    sha = hashlib.sha256(payload).hexdigest()
    facts = MissionFacts("1" * 64, "5" * 64, "5" * 64, ("R1",), ("R1",))
    artifact = Artifact(sha, "author", 1)
    killed = Control("same-control", "c" * 64, "R1", "g6", 2, True)
    not_killed = replace(killed, killed=False)
    check = Check("check", ("R1",), "reviewer", "independent_executable", "c" * 64, 0,
                  (not_killed, killed))
    receipt = Receipt("receipt", "check", "c" * 64, sha, "pass", 3)
    ledger = ChainLedger()
    record(ledger, killed, receipt)
    body = issue(facts, artifact, [check], [receipt], ledger=ledger).as_dict()
    assert body["predicates"][0]["support"] is True
    assert verify(body, payload, ledger=ledger) == []
    assert verify(body, payload) == []
    online = certificate_view(body, artifact_sha256=sha, ledger=ledger)
    assert online["predicates"][0]["evidence"][0]["role"] == "supports"
    offline = certificate_view(body, artifact_sha256=sha)
    assert offline["predicates"][0]["evidence"][0]["role"] == "supports", (
        "offline view rejects a receipt the issuer counts online and offline"
    )
