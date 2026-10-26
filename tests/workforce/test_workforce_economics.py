# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Money, cost rows and budgets as pure types (Phase E Part 1, E1a/E1c; Ledger §4, §5, §18, §20, §21).

Pinned: an amount is whole minor units with its currency, never a float; an unknown amount is None, never 0, and
a total holding one has no amount; amounts in two currencies are never added; a budget that cannot be read is 0
(fail closed); the soft limit is exact integer arithmetic; a hosted customer's missions are charged. Offline.
"""
from __future__ import annotations

import dataclasses

import pytest
from g6.workforce import economics as we


def _entry(amount, currency="AUD", **kw):
    base = {"category": "tool_api", "source": "declared_ceiling" if amount else "declared_free",
            "amount_minor_units": amount, "currency": currency, "estimated": bool(amount),
            "method": "the declared ceiling is charged" if amount else ""}
    return we.CostEntry(**{**base, **kw})


def _unknown(**kw):
    return we.CostEntry(**{"category": "tool_api", "source": "unpriced", "amount_minor_units": None,
                           "currency": None, "method": "this tool declares no cost", **kw})


@pytest.mark.parametrize("amount", [1.5, True, -1, "30", None])
def test_money_is_whole_minor_units_never_a_float(amount):
    with pytest.raises(ValueError, match="minor units"):
        we.Money(amount, "AUD")


@pytest.mark.parametrize("currency", ["aud", "AU", "AUDD", "", None])
def test_money_names_an_iso_currency(currency):
    with pytest.raises(ValueError, match="ISO-4217"):
        we.Money(30, currency)


def test_money_is_formatted_by_integer_arithmetic():
    assert we.format_money(30, "AUD") == "0.30 AUD"
    assert we.format_money(1234, "AUD") == "12.34 AUD"
    assert we.format_money(10**17 + 1, "AUD") == "1000000000000000.01 AUD"  # a float would round this


def test_an_unknown_amount_says_why_and_is_never_an_estimate():
    with pytest.raises(ValueError, match="why"):
        we.CostEntry(category="tool_api", source="unpriced", amount_minor_units=None, currency=None)
    with pytest.raises(ValueError, match="not an estimate"):
        _unknown(estimated=True)
    with pytest.raises(ValueError, match="method"):
        we.CostEntry(category="tool_api", source="declared_ceiling", amount_minor_units=30, currency="AUD",
                     estimated=True)


@pytest.mark.parametrize(("field", "value"), [("category", "tokens"), ("source", "guess")])
def test_categories_and_sources_are_the_closed_sets(field, value):
    with pytest.raises(ValueError, match=field):
        _entry(30, **{field: value})


def test_an_entry_round_trips_through_its_stored_form():
    entry = _entry(30, provider="fake_ledger", resource_name="fake_ledger.total", quantity=1, quantity_unit="calls",
                   metadata={"model_calls": 0}, attempt_id="run_1", work_unit_id="sum")
    stored = entry.as_detail()
    assert "id" not in stored and "mission_id" not in stored and stored["amount_minor_units"] == 30
    back = we.CostEntry.from_detail({**stored, "actor": "worker:wi_1", "attempt": 1}, id="ev_1", mission_id="m_1",
                                    recorded_at="2026-10-03T00:00:00+00:00")
    assert back == dataclasses.replace(entry, id="ev_1", mission_id="m_1", recorded_at="2026-10-03T00:00:00+00:00")


def test_a_total_with_an_unknown_entry_has_no_amount_never_zero():
    found = we.total([_entry(0), _unknown()])
    assert (found.amount_minor_units, found.known_minor_units, found.unknown_entries, found.summary) == (
        None, 0, 1, "unknown")


def test_amounts_in_another_currency_are_never_added_to_the_budgets():
    found = we.total([_entry(30), _entry(500, currency="USD")])
    assert (found.known_minor_units, found.other_currency_entries, found.amount_minor_units, found.summary) == (
        30, 1, None, "unknown")
    with pytest.raises(ValueError, match="conversion"):
        we.total([_entry(30)]).plus(we.total([_entry(5, currency="USD")], currency="USD"))


def test_the_summary_says_exact_estimated_or_unknown():
    assert (we.total([]).summary, we.total([]).amount_minor_units) == ("exact", 0)
    assert we.total([_entry(0)]).summary == "exact"
    assert we.total([_entry(30)]).summary == "estimated"
    assert we.total([_entry(30), _unknown()]).summary == "unknown"


def test_the_owner_session_does_not_bind_the_budget():
    chat = we.owner_session_cost()
    assert (chat.binding, chat.amount_minor_units, chat.source, chat.category) == (
        False, None, "owner_session", "model_inference")
    assert we.total([chat, _entry(30)], binding_only=True) == we.total([_entry(30)])
    assert we.total([chat, _entry(30)]).unknown_entries == 1


@pytest.mark.parametrize("value", [None, "x", True, 1.5, -1, "50"])
def test_a_budget_that_cannot_be_read_is_zero(value):
    assert we.hard_limit(value) == (0, False)


@pytest.mark.parametrize(("hard", "soft"), [(0, 0), (1, 0), (10, 9), (50, 45),
                                            (10**18 + 1, (10**18 + 1) * 9 // 10)])
def test_the_soft_limit_is_stop_fraction_in_integer_arithmetic(hard, soft):
    assert we.soft_limit(hard) == soft


def test_a_budget_policy_reads_the_mission_fail_closed():
    policy = we.budget_policy({"mission_id": "m1", "budget_cents": "lots"}, charged=True)
    assert (policy.hard_machine_cost_limit, policy.ceiling_readable, policy.charged) == (0, False, True)
    good = we.budget_policy({"mission_id": "m1", "budget_cents": 50}, charged=False)
    assert (good.hard_machine_cost_limit, good.soft_machine_cost_limit, good.currency) == (50, 45, "AUD")
    assert (good.max_concurrency, good.allow_budget_escalation_request, good.max_total_attempts) == (1, False, None)


def test_budget_pressure_is_read_from_the_known_binding_spend():
    policy = we.budget_policy({"mission_id": "m1", "budget_cents": 50}, charged=False)
    assert policy.pressure(we.total([_entry(30)])) == ""
    assert policy.pressure(we.total([_entry(45)])) == "SOFT_BUDGET"
    assert policy.pressure(we.total([_entry(50)])) == "BUDGET_EXHAUSTED"
    assert we.budget_policy({"budget_cents": 0}, charged=False).pressure(we.total([])) == ""
    assert we.budget_policy({"budget_cents": 1}, charged=False).pressure(we.total([])) == ""




def test_context_telemetry_keeps_tokens_unknown_and_flags_what_is_out_of_proportion():
    small = we.context_record(read_bytes=100, output_bytes=10, reused_inputs=0)
    assert (small["input_tokens"], small["output_tokens"], small["cache_hits"]) == (None, None, None)
    assert small["out_of_proportion"] is False and small["retrieved_artifact_bytes"] == 100
    assert we.context_record(read_bytes=100_000, output_bytes=100, reused_inputs=1)["out_of_proportion"] is True
    assert we.context_record(read_bytes=100_000, output_bytes=50_000, reused_inputs=0)["out_of_proportion"] is False
    assert we.context_record(read_bytes=100_000, output_bytes=0, reused_inputs=0)["out_of_proportion"] is False


def test_a_call_cost_follows_the_declared_ceiling_or_is_unknown():
    free = we.call_cost(we.Money(0, "AUD"), model_free=True, component="solver", op="check", charged_model_calls=0,
                        process_model_calls=0)
    assert (free.source, free.amount_minor_units, free.estimated, free.category) == (
        "declared_free", 0, False, "compute")
    priced = we.call_cost(we.Money(30, "AUD"), model_free=False, component="ledger", op="total",
                          charged_model_calls=2, process_model_calls=5)
    assert (priced.source, priced.amount_minor_units, priced.estimated, priced.category, priced.quantity) == (
        "declared_ceiling", 30, True, "model_inference", 2)
    assert priced.metadata == {"model_calls": 2, "process_model_calls": 5} and priced.method
    unpriced = we.call_cost(None, model_free=False, component="ledger", op="total", charged_model_calls=0,
                            process_model_calls=0)
    assert (unpriced.source, unpriced.amount_minor_units, unpriced.category, unpriced.resource_name) == (
        "unpriced", None, "tool_api", "ledger.total")
