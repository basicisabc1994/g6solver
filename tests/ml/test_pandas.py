# SPDX-License-Identifier: Apache-2.0
"""adapt_pandas: a table you give, read by one stated rule and described (data_handling_error). Numbers mixed with
text, values that look like missing markers, codes with leading zeros, repeated rows and repeated keys are reported,
never converted or dropped silently."""
from __future__ import annotations

import json
import re
import socket
import subprocess
from pathlib import Path

import pytest

import g6.ml.adapt_pandas as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import describe_reply, invoke_reply
from g6.ml import _types
from g6.ml.adapt_pandas import table as tables

SRC = Path(package.__file__).parent
PRICES = ("order,zip,price,status\n"
          "1,02134,9.50,paid\n"
          "2,90210,n/a,paid\n"
          "3,10001,12,NA\n"
          "4,02139,\"12,5\",paid\n"
          "\n"
          "5,60601,7.25,refunded\n"
          "5,60601,7.25,refunded\n")


def _invoke(op: str, **params) -> dict:
    result = get_registry().invoke("adapt_pandas", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_pandas", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


def _column(found: dict, name: str) -> dict:
    return next(c for c in found["profile"]["columns"] if c["name"] == name)


# -- D1: a column of numbers with stray text is found and named, never read as numbers --------------------------------

def test_d1_numbers_mixed_with_text_are_named_with_their_counts():
    found = _invoke("profile", csv=PRICES, key=["order"])
    assert found["completion_state"] == "qualified-draft" and found["warning_card"]["code"] == _types.PROFILED
    price = _column(found, "price")
    assert price["kind"] == "mixed" and price["texts"]["count"] == 2 and price["numbers"]["count"] == 4
    assert any(w.startswith("price: 2 text value(s) among 4 number(s)") and "'n/a'" in w and "'12,5'" in w
               for w in found["profile"]["warnings"])
    assert "price: 2 text value(s)" in found["warning_card"]["message"]


def test_d1_cases_the_check_was_not_written_around():
    found = _invoke("profile", csv=PRICES, key=["order"])
    warnings = " | ".join(found["profile"]["warnings"])
    assert "a decimal comma" in warnings                         # "12,5"
    assert "status: value(s) that often stand for no value were read as text ('NA' x1)" in warnings
    assert "1 row(s) repeat an earlier row exactly (first: row 5)" in warnings
    assert "the key (order) repeats: 1 value(s)" in warnings and "would multiply rows" in warnings
    zips = _column(found, "zip")
    assert zips["kind"] == "text" and "start with a zero" in " ".join(zips["notes"])
    # numbers given as text in JSON are mixed too, and said to be
    rows = [{"id": 1, "amount": 3}, {"id": 2, "amount": "4"}, {"id": 3, "amount": 5.5}]
    found = _invoke("profile", json_text=json.dumps(rows))
    assert _column(found, "amount")["kind"] == "mixed"
    assert any("given as text" in w for w in found["profile"]["warnings"])


def test_the_rule_keeps_codes_and_reads_numbers_and_missing_only_as_asked():
    loaded = _invoke("load", csv=PRICES)
    rows = loaded["rows"]
    assert rows[0]["zip"] == "02134" and rows[1]["zip"] == "90210"     # codes, as written
    assert rows[0]["price"] == 9.5 and rows[2]["price"] == 12 and rows[1]["price"] == "n/a"
    assert rows[2]["status"] == "NA"                                     # not missing unless asked
    asked = _invoke("load", csv=PRICES, missing=["", "NA", "n/a"])
    assert asked["rows"][2]["status"] is None and asked["rows"][1]["price"] is None
    assert loaded["accounting"] == {"lines_read": 8, "blank_lines_skipped": 1, "header_lines": 1, "rows_out": 6}
    reading = loaded["evidence"][0]
    assert reading["kind"] == "ml_table_reading" and reading["rule"] == tables.READING_RULE
    assert reading["missing_tokens"] == [""] and len(reading["rows_sha256"]) == 64


def test_loaded_rows_feed_the_family_dataset_spec():
    csv_text = "x,y\n" + "".join(f"{i},{2 * i + (i % 3)}\n" for i in range(40))
    rows = _invoke("load", csv=csv_text)["rows"]
    spec = _types.DatasetSpec(rows=rows, target="y", features=["x"], task="regression", seed=1)
    assert spec.rows[3] == {"x": 3, "y": 6}


# -- what is refused, never repaired --------------------------------------------------------------------------------

@pytest.mark.parametrize("text, said", [
    ("a,b\n1,2\n3\n4,5,6\n", "2 line(s) do not have the header's 2 fields (line 3 has 1, line 4 has 3)"),
    ("a,a\n1,2\n", "the column name 'a' is used twice"),
    ("a,\n1,2\n", "column 2 has no name"),
    ('a,b\n"1,2\n', "cannot be read"),
    ("\n\n", "no header line"),
])
def test_a_malformed_table_is_refused_with_where(text: str, said: str):
    found = _invoke("load", csv=text)
    assert found["completion_state"] == "blocked-escalated" and found["code"] == _types.INPUT_INVALID
    assert said in found["reason"], found["reason"]


def test_a_json_key_given_twice_in_one_object_is_refused_never_one_value_kept():
    text = '[{"price": 10, "price": 999}, {"price": 20}]'
    for op in ("load", "profile"):
        found = _invoke(op, json_text=text)
        assert found["completion_state"] == "blocked-escalated" and found["code"] == _types.INPUT_INVALID, op
        assert "'price'" in found["reason"] and "twice" in found["reason"], found["reason"]


def test_one_table_one_way():
    for params in ({}, {"csv": "a\n1\n", "rows": [{"a": 1}]}):
        found = _invoke("profile", **params)
        assert found["code"] == _types.INPUT_INVALID and "give the table one way" in found["reason"]
    nested = _invoke("profile", json_text='[{"a": {"b": 1}}]')
    assert nested["code"] == _types.INPUT_INVALID and "holds a dict" in nested["reason"]


def test_sizes_are_capped():
    assert "G6_E_INPUT_VALIDATION" in _refused("load", csv="a\n" + "1\n" * (tables.MAX_TEXT_CHARS // 2 + 1))
    wide = ",".join(f"c{i}" for i in range(tables.MAX_COLUMNS + 1))
    assert _invoke("load", csv=wide + "\n")["code"] == _types.TOO_LARGE


# -- the gates --------------------------------------------------------------------------------------------------------

def test_g1_every_result_is_a_qualified_draft_at_best_over_mcp_too():
    reply = invoke_reply("adapt_pandas", "profile", json.dumps({"csv": PRICES, "request_id": "q1", "run_id": "u1"}))
    assert reply["ok"] and reply["completion_state"] == "qualified-draft" and check_envelope(reply) == []
    assert (reply["result"]["request_id"], reply["result"]["run_id"]) == ("q1", "u1")
    for path in SRC.rglob("*.py"):
        assert not re.search(r"""["']verified["']""", path.read_text(encoding="utf-8")), path.name


@pytest.mark.parametrize("field", [{"completion_state": "verified"}, {"approved": True},
                                   {"reviewer_signature": "Dr Who"}, {"path": "C:/data.csv"},
                                   {"query": "price > 3"}, {"eval": "df.x"}])
def test_g2_g3_claims_paths_and_expressions_are_refused(field: dict):
    assert "G6_E_INPUT_VALIDATION" in _refused("profile", csv=PRICES, **field)


def test_g2_a_formula_in_a_cell_stays_text_and_nothing_is_evaluated():
    found = _invoke("load", csv='a,b\n1,"=cmd|\' /C calc\'!A0"\n2,__import__(\'os\')\n')
    assert found["rows"][0]["b"] == "=cmd|' /C calc'!A0" and found["rows"][1]["b"] == "__import__('os')"


def test_g4_an_unexpected_error_is_a_refusal_with_its_reason(monkeypatch: pytest.MonkeyPatch):
    def broken(*args, **kwargs):
        raise RuntimeError("the reader broke")

    monkeypatch.setattr(tables, "profile", broken)
    found = _invoke("profile", csv=PRICES)
    assert found["completion_state"] == "blocked-escalated" and found["code"] == _types.OP_FAILED
    assert "the reader broke" in found["reason"]


def test_g5_it_has_no_sensitive_ops_because_it_reads_no_file_and_opens_no_socket(monkeypatch: pytest.MonkeyPatch):
    assert package.__g6_sensitive_ops__ == () and package.__g6_outside__ == () and package.__g6_extras__ == {}

    def refused(*args, **kwargs):
        raise AssertionError("adapt_pandas reached outside the call")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(subprocess, "Popen", refused)
    for op in ("load", "profile"):
        assert _invoke(op, csv=PRICES)["completion_state"] == "qualified-draft"


def test_g6_g7_the_package_reads_no_key_calls_no_model_and_holds_no_stand_in():
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"getenv|os\.environ|API_KEY|litellm|openai|anthropic|g6\.core\.llm", text), path.name
        assert not re.search(r"(?i)mock|fake|simulat", text), path.name


# -- D2, D6 -----------------------------------------------------------------------------------------------------------

def test_d2_it_declares_what_it_guards_against_and_left_planned():
    assert package.__g6_mitigates__ == ("data_handling_error",) and package.__g6_introduces__ == ()
    assert "g6.ml.adapt_pandas" not in FailureModeRegistry.planned()
    assert FailureModeRegistry.is_registered("data_handling_error")


def test_d6_an_agent_finds_it_by_its_failure_mode_describes_it_and_invokes_it_over_mcp():
    title = FailureModeRegistry.title("data_handling_error")
    assert "adapt_pandas" in [r["component"] for r in discovery.find(title, limit=5)]
    info = describe_reply("adapt_pandas")
    assert info["family"] == "ml" and info["skill"] and info["sensitive_ops"] == []
    assert sorted(info["ops"]) == ["load", "profile"]
    for op in ("load", "profile"):
        reply = invoke_reply("adapt_pandas", op, json.dumps({"csv": PRICES}))
        assert reply["ok"] and reply["completion_state"] == "qualified-draft", reply
