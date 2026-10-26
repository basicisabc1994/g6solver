# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Mission certificates in the workforce (Runtime Validator Constructor R6, 2026-10-14).

The certificate issuer (``workforce.certificates``) decides a coding mission's state (``G6_WORKFORCE_CERTIFICATES``;
founder ruling R6-P: with it on, the certificate decides the state outright). On by default for every plan (founder,
2026-10-15); only a plain no turns it off.
"""
from __future__ import annotations

import pytest
from g6.core.ledger import ChainLedger
from g6.evidence import certificate as C
from g6.workforce import service


@pytest.mark.parametrize(("value", "on"), [
    (None, True), ("", True), ("1", True), ("true", True), ("yes", True), ("on", True), ("yes please", True),
    ("0", False), ("false", False), ("no", False), ("off", False), (" FALSE ", False),
])
def test_certificates_are_on_unless_the_environment_says_a_plain_no(value, on):
    env = {} if value is None else {service.ENV_CERTIFICATES: value}
    assert service.certificates_enabled(env) is on


def test_the_flag_name_is_the_documented_one():
    assert service.ENV_CERTIFICATES == "G6_WORKFORCE_CERTIFICATES"


from g6.workforce import coding_certificate as CC  # noqa: E402
from g6.workforce.contracts import BLOCKED, QUALIFIED, VERIFIED, Outcome  # noqa: E402


def lift(cert_state, outcome, **over):
    kw = {"unchecked": (), "all_passed": True}
    kw.update(over)
    return CC.ruled(cert_state, outcome, **kw)


def test_a_verified_certificate_lifts_a_clean_qualified_draft():
    assert lift(VERIFIED, Outcome(QUALIFIED)).state == VERIFIED


@pytest.mark.parametrize(("outcome", "over"), [
    (Outcome(BLOCKED), {}),                                   # a blocked mission is never lifted
    (Outcome(QUALIFIED, degraded=True), {}),                  # degraded evidence is outside the certificate
    (Outcome(QUALIFIED), {"unchecked": ("helper",)}),         # a disclosed unchecked part (founder 10-08)
    (Outcome(QUALIFIED), {"all_passed": False}),              # a check on the latest bytes did not pass
])
def test_a_verified_certificate_never_lifts_what_something_outside_it_holds_back(outcome, over):
    assert lift(VERIFIED, outcome, **over) is outcome


def test_a_certificate_short_of_verified_only_ever_lowers():
    assert lift(QUALIFIED, Outcome(VERIFIED)).state == QUALIFIED
    assert lift(BLOCKED, Outcome(QUALIFIED)).state == BLOCKED
    held = Outcome(BLOCKED)
    assert lift(QUALIFIED, held) is held  # never raises a blocked mission


A, B = "tests/test_a.py::test_a", "tests/test_b.py::test_b"


def _suite(outcomes, *, ran=True, collect_errors=()):
    return {"ran": ran, "outcomes": outcomes, "collect_errors": list(collect_errors)}


@pytest.mark.parametrize(("found", "caught"), [
    (_suite({A: "failed", B: "passed"}), {A}),
    (_suite({A + "[1]": "passed", A + "[2]": "failed", B: "passed"}), {A}),  # any case of a parametrized test
    # Review #1 (2026-10-15): only a test whose body ran and failed saw the defect. A skip, an expected failure, a
    # fixture error or a file that no longer imports ran none of its assertions, so it caught nothing.
    (_suite({A: "skipped", B: "passed"}), set()),
    (_suite({A: "xfailed", B: "passed"}), set()),
    (_suite({A: "error", B: "passed"}), set()),
    (_suite({A + "[1]": "passed", A + "[2]": "error", B: "passed"}), set()),
    (_suite({B: "passed"}, collect_errors=["tests/test_a.py"]), set()),
    (_suite({B: "passed"}), set()),                                         # absent for no stated reason: never caught
    (_suite({A: "failed", B: "failed"}, ran=False), set()),                 # a run that did not report says nothing
    (_suite({"tests/test_a.py::test_a_more": "failed", B: "passed"}), set()),  # another test, not a case of this one
])
def test_a_seeded_defect_catches_a_test_only_when_the_run_reported_it_not_passing(found, caught):
    assert CC.caught((A, B), found) == caught


def test_the_issuer_needs_no_research_flag_and_decides_from_values():
    facts = C.MissionFacts(mission_sha="ab" * 32, mission_standard_sha="cd" * 32, standard_sha="cd" * 32,
                           predicates=("p",), standard_predicates=("p",))
    art = C.Artifact("ef" * 32, "local:model", 10)
    check = C.Check("chk", ("p",), "g6:constructor", "independent_executable", "12" * 32, 1,
                    (C.Control("c", "12" * 32, "p", C.G6, 2, True),), positive_seq=3)
    receipt = C.Receipt("r", "chk", "12" * 32, "ef" * 32, "pass", 20)
    # EVIDENCE-01: values typed by hand support nothing until G6's ledger holds them. Removing g6.evidence's
    # `elif missing := _unresolved(c, p, r, resolves):` branch makes the first assertion fail.
    assert C.issue(facts, art, [check], [receipt]).state == "qualified-draft"
    ledger = ChainLedger()
    C.record(ledger, *check.controls, receipt)
    cert = C.issue(facts, art, [check], [receipt], ledger=ledger)
    assert cert.state == "verified"
    assert C.issue(facts, art, [check], [], ledger=ledger).state == "qualified-draft"
