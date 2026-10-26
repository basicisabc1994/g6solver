# SPDX-License-Identifier: Apache-2.0
"""Draft checks never call a draft ``verified``; a check that could not run, or no check at all, is said (VEN-006)."""
from __future__ import annotations

from g6.operator import Context, Finding, route

CONTACTS = frozenset({"b1"})


def test_ven006_a_check_that_could_not_run_never_reads_as_passed():
    # Archive: router._failed ignored not_measured steps, so such a draft was routed "verified".
    routed = route({"type": "email", "to": "b1", "body": "Hi Sam, see you Monday."}, Context(secrets=("hunter2",)))
    assert routed.state == "qualified-draft"
    assert routed.code == "G6_E_OPERATOR_NOT_MEASURED" and "recipient" in routed.message


def test_ven006_every_check_passing_is_still_not_verified():
    routed = route({"type": "email", "to": "b1", "body": "Our price is $19 a month."},
                   Context(contacts=CONTACTS, floor=15.0, secrets=("hunter2",)))
    assert [f.status for f in routed.findings if f.status != "n/a"] == ["pass", "pass", "pass"]
    assert (routed.state, routed.code) == ("qualified-draft", "G6_E_OPERATOR_CHECKS_PASSED")


def test_ven006_a_draft_no_check_ran_on_says_so():
    routed = route({"type": "post", "channel": "blog", "body": "We shipped dark mode."})
    assert not any(f.status in ("pass", "fail") for f in routed.findings)
    assert (routed.state, routed.code) == ("qualified-draft", "G6_E_OPERATOR_NO_CHECK")


def test_failing_checks_block_and_name_themselves():
    action = {"type": "email", "to": "stranger", "evidence": [],
              "body": 'A customer told us: "this halved our month-end close time" - only $5 today. Key hunter2.'}
    routed = route(action, Context(contacts=CONTACTS, floor=15.0, secrets=("hunter2",)))
    assert routed.state == "blocked-escalated"
    assert set(routed.fired) == {"recipient", "witness", "price_floor", "secret_scan"}
    assert route({"type": "spend", "body": "ads"}).fired == ("spend_amount",)


def test_caller_checks_run_and_one_that_raises_has_not_answered():
    def boom(action):
        raise RuntimeError("down")

    def mine(action):
        return Finding("mine", "fail", "no")

    routed = route({"type": "post", "body": "x"}, Context(checks=(boom, mine, lambda a: True)))
    by = {f.name: f.status for f in routed.findings}
    assert (by["boom"], by["mine"], by["<lambda>"]) == ("not_measured", "fail", "pass")
    assert routed.state == "blocked-escalated"
