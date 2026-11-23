"""Round four: invisible-only premises cannot establish visible claims."""
from pathlib import Path
import runpy
import shutil
from uuid import uuid4

import pytest

from g6.research.echa import Ledger, trace_claim

ROOT = Path(__file__).resolve().parents[2]
H = runpy.run_path(str(ROOT / "tests/research/echa/test_echa_kernel_claims.py"))
INVISIBLE = [
    pytest.param("\u0301", id="combining-only"),
    pytest.param("\u034f", id="combining-grapheme-joiner"),
    pytest.param("\ufe0f", id="variation-selector-16"),
    pytest.param("\U000e0100", id="supplementary-variation-selector"),
    pytest.param("\u115f\u1160", id="hangul-fillers"),
    pytest.param("\u3164", id="hangul-compatibility-filler"),
    pytest.param("\uffa0", id="halfwidth-hangul-filler"),
]


@pytest.fixture
def disk_dir():
    # pytest's restricted-mode tmp_path is inaccessible in this Windows sandbox.
    parent = Path(__file__).resolve().parent
    path = parent / ("disk-" + uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        assert path.resolve().parent == parent
        shutil.rmtree(path)


def ledger(path):
    led = Ledger(path)
    led.add_standard(H["STD"])
    led.add_vault("sealed", H["FX"], files=1, roots=1)
    return led


@pytest.mark.parametrize("text", INVISIBLE)
def test_invisible_statement_cannot_establish_general_breadth(disk_dir, text):
    path = disk_dir / "evidence.jsonl"
    led = ledger(path)
    check = H["calibrated"](led, check_over={
        "oracle_category": "FORMAL_CHECK", "checked_statement": text,
        "scope_mapping_ref": "scope"})
    led.add_evidence(H["ev"](
        "scope", purpose="scope_mapping", source_ref=H["_binding"]("c"),
        independence_group="assessor", negative_control_ref="e1-cal-ctl",
        applies_to_sha256=check.sha256, population=check.population,
        sample_unit=check.sample_unit, scope="all inputs", checked_statement=text))
    led.add_claim(H["claim"](
        subject="sha256:" + "b" * 64, breadth="general", scope="all inputs"))
    reopened = Ledger(path, expect_head=led.head())
    traced = trace_claim(reopened, "c1")
    assert traced["ledger_ok"] is True
    got = traced["verdict"]
    assert (got["result"], got["effective_breadth"]) == (
        "inconclusive", "covered_cases"), got


@pytest.mark.parametrize("text", INVISIBLE)
@pytest.mark.parametrize("field", ["scope", "population", "sample_unit"])
def test_invisible_claim_premise_cannot_be_supported(disk_dir, text, field):
    path = disk_dir / "evidence.jsonl"
    led = ledger(path)
    led.add_evidence(H["control"]("ctl", source_ref=H["_binding"]("a")))
    led.add_evidence(H["ev"]("e1", source_ref=H["_binding"]("a")))
    led.add_claim(H["claim"](subject="sha256:" + "b" * 64, **{field: text}))
    reopened = Ledger(path, expect_head=led.head())
    traced = trace_claim(reopened, "c1")
    assert traced["ledger_ok"] is True
    got = traced["verdict"]
    assert got["result"] == "inconclusive", got
