"""Round-two reproductions for unknown-premises receipt admission."""
from pathlib import Path
import json
import runpy

from g6.research.echa import trace_claim

ROOT = Path(__file__).resolve().parents[3]
H = runpy.run_path(str(ROOT / "tests/research/echa/test_echa_kernel_claims.py"))


def ledger():
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    led.add_vault("v1", H["FX"], files=1, roots=1)
    return led


def test_calibration_cannot_borrow_another_fixtures_seal():
    led = ledger()
    binding = json.loads(H["_binding"]("c"))
    binding["fixture_sha256"] = "e" * 64
    # No vault seals e*64. The declaration still names the unrelated sealed FX.
    H["calibrated"](led, calibration_over={"source_ref": H["canonical"](binding)})
    assert led.verify() == ()
    got = H["verdict"](led, subject="sha256:" + "b" * 64,
                       estimand="true_correctness", target=0.5)
    trace = trace_claim(led, "c1")
    assert trace["ledger_ok"] is True
    assert trace["verdict"] == got
    assert got["identification"] == "not_identified", got


def test_whitespace_is_not_a_checked_statement_for_general_breadth():
    led = ledger()
    check = H["calibrated"](led, check_over={
        "oracle_category": "FORMAL_CHECK", "checked_statement": " \t\n",
        "scope_mapping_ref": "scope"})
    led.add_evidence(H["ev"](
        "scope", purpose="scope_mapping", source_ref=H["_binding"]("c"),
        independence_group="assessor", negative_control_ref="e1-cal-ctl",
        applies_to_sha256=check.sha256, population=check.population,
        sample_unit=check.sample_unit, scope="all inputs", checked_statement=" \t\n"))
    assert led.verify() == ()
    got = H["verdict"](led, subject="sha256:" + "b" * 64,
                       breadth="general", scope="all inputs")
    assert got["result"] == "inconclusive", got

