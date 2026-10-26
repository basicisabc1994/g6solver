# SPDX-License-Identifier: Apache-2.0
"""The reader of the driver's records, on recorded output (no SWI-Prolog needed): only lines that carry the run's
nonce are records, and anything that is not one complete, well-formed run is refused rather than read as an answer."""
from __future__ import annotations

import json

import pytest

from g6.prolog import driver
from g6.prolog.envelope import OUTPUT_UNRECOGNISED, RUN_FAILED, Refusal

NONCE = "a" * 32


def _line(record: dict) -> str:
    return f"{NONCE} {json.dumps(record)}"


def _run(*records: dict, noise: str = "") -> str:
    lines = [_line({"kind": "version", "version": "10.0.2"}), *(_line(r) for r in records), _line({"kind": "end"})]
    return noise + "\r\n\r\n".join(lines) + "\r\n"


ANSWER = {"kind": "answer", "bindings": [["X", "[1]"], ["Y", "[2]"]], "text": "['X'=[1],'Y'=[2]]"}


def test_a_complete_run_is_read() -> None:
    read = driver.read_output(_run({"kind": "predicate", "name": "p", "arity": 1, "clauses": 2, "line": 3}, ANSWER,
                                   {"kind": "done", "exhausted": True}), NONCE, 0)
    assert read["version"] == "10.0.2" and read["exhausted"] is True and read["stopped"] == ""
    assert read["predicates"] == [{"name": "p", "arity": 1, "clauses": 2, "line": 3}]
    assert read["answers"] == [{"bindings": {"X": "[1]", "Y": "[2]"}, "text": "['X'=[1],'Y'=[2]]"}]


def test_the_restricted_syntax_refusals_and_the_stack_limit_are_read() -> None:
    read = driver.read_output(_run(
        {"kind": "problem", "stage": "program", "problem": "not_allowed", "line": 2, "column": 0, "message": "a"},
        {"kind": "problem", "stage": "goal", "problem": "undefined", "line": 0, "column": 0, "message": "b"},
        {"kind": "stopped", "by": "memory_limit"}), NONCE, 0)
    assert [p["problem"] for p in read["problems"]] == ["not_allowed", "undefined"]
    assert (read["stopped"], read["exhausted"]) == ("memory_limit", False)


def test_lines_without_the_nonce_are_not_records() -> None:
    """A goal's own output, even a record-shaped line with another nonce, is ignored."""
    spoof = "b" * 32 + " " + json.dumps({**ANSWER, "bindings": [["X", "[9]"], ["Y", "[]"]]}) + "\n"
    read = driver.read_output(_run(ANSWER, {"kind": "done", "exhausted": True}, noise=spoof + "hello\n"), NONCE, 0)
    assert [a["bindings"]["X"] for a in read["answers"]] == ["[1]"]


@pytest.mark.parametrize(("text", "exit_code", "code"), [
    ("", 0, RUN_FAILED),
    (_run(ANSWER).rsplit(NONCE, 1)[0], 0, RUN_FAILED),                       # stopped before end
    (_run(ANSWER), 1, RUN_FAILED),                                            # a failing exit code
    (_run({"kind": "driver_error", "message": "boom"}), 0, RUN_FAILED),
    (_line({"kind": "end"}), 0, RUN_FAILED),                                  # no version first
    (_run() + _line({"kind": "end"}), 0, RUN_FAILED),                         # two ends
    (_run({"kind": "version", "version": "9"}), 0, OUTPUT_UNRECOGNISED),      # two versions
    (_run({"kind": "done", "exhausted": True}, {"kind": "stopped", "by": "time_limit"}), 0, OUTPUT_UNRECOGNISED),
    (_run({"kind": "answer", "bindings": [["X"]], "text": "x"}), 0, OUTPUT_UNRECOGNISED),
    (_run({"kind": "answer", "bindings": [["X", "1"], ["X", "2"]], "text": "x"}), 0, OUTPUT_UNRECOGNISED),
    (_run({"kind": "answer", "bindings": [], "text": 7}), 0, OUTPUT_UNRECOGNISED),
    (_run({"kind": "answer"}), 0, OUTPUT_UNRECOGNISED),
    (_run({"kind": "stopped", "by": "patience"}), 0, OUTPUT_UNRECOGNISED),
    (_run({"kind": "done", "exhausted": False}), 0, OUTPUT_UNRECOGNISED),
    (_run({"kind": "recheck", "index": 0, "verdict": "probably", "why": ""}), 0, OUTPUT_UNRECOGNISED),
    (_run({"kind": "recheck", "index": True, "verdict": "agreed", "why": ""}), 0, OUTPUT_UNRECOGNISED),
    (_run({"kind": "problem", "stage": "elsewhere", "problem": "syntax", "line": 1, "column": 0, "message": ""}), 0,
     OUTPUT_UNRECOGNISED),
    (_run({"kind": "mystery"}), 0, OUTPUT_UNRECOGNISED),
    (_run() + f"{NONCE} not json\n", 0, OUTPUT_UNRECOGNISED),
])
def test_anything_but_one_complete_run_is_refused(text: str, exit_code: int, code: str) -> None:
    with pytest.raises(Refusal) as refused:
        driver.read_output(text, NONCE, exit_code)
    assert refused.value.code == code


def test_the_request_names_the_mode_the_limits_and_the_answers_to_recheck() -> None:
    sent = json.loads(driver.request("recheck", NONCE, max_solutions=3, time_limit=2.5, inference_limit=9,
                                     answers=["['X'=1]"], exhausted=True))
    assert sent == {"mode": "recheck", "nonce": NONCE, "max_solutions": 3, "time_limit": 2.5, "inference_limit": 9,
                    "answers": ["['X'=1]"], "exhausted": True}
