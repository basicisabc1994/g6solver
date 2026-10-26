# SPDX-License-Identifier: Apache-2.0
"""Reading TLC's and SANY's output (owner, 2026-10-20), on text recorded from TLC2 Version 2026.03.24.222644 (rev:
1476e7f) in its ``-tool`` mode, with the run directory's path replaced by a neutral one. No Java needed.

TLC's verdict is read from its coded messages and checked against its exit code; anything that does not fit is
``unrecognised``, never a pass: a success message with a violation's exit code, an exit code 0 with no success message,
or a run cut off before TLC said what it found."""
from __future__ import annotations

import pytest

from g6.model_checking.tlc_output import parse_sany_output, parse_tlc_output, parse_value

HEAD = """@!@!@STARTMSG 2262:0 @!@!@
TLC2 Version 2026.03.24.222644 (rev: 1476e7f)
@!@!@ENDMSG 2262 @!@!@
@!@!@STARTMSG 2187:0 @!@!@
Running breadth-first search Model-Checking with fp 119 and seed -8648266151821530241 with 1 worker on 20 cores with 1024MB heap and 64MB offheap memory [pid: 740] (Windows 11 10.0 amd64, Eclipse Adoptium 21.0.10 64bit, MSBDiskFPSet, DiskStateQueue).
@!@!@ENDMSG 2187 @!@!@
@!@!@STARTMSG 2220:0 @!@!@
Starting SANY...
@!@!@ENDMSG 2220 @!@!@
Parsing file /tmp/g6_tlc_run/spec/Counter.tla
Parsing file /tmp/g6_tlc_run/java_tmp/tlc-3165980623663500135/Naturals.tla (jar:file:/tmp/g6_tlc_run/tools/tla2tools.jar!/tla2sany/StandardModules/Naturals.tla)
Semantic processing of module Naturals
Semantic processing of module Counter
Linting of module Counter
@!@!@STARTMSG 2219:0 @!@!@
SANY finished.
@!@!@ENDMSG 2219 @!@!@
@!@!@STARTMSG 2185:0 @!@!@
Starting... (2026-10-20 04:37:07)
@!@!@ENDMSG 2185 @!@!@
@!@!@STARTMSG 2189:0 @!@!@
Computing initial states...
@!@!@ENDMSG 2189 @!@!@
@!@!@STARTMSG 2190:0 @!@!@
Finished computing initial states: 1 distinct state generated at 2026-10-20 04:37:07.
@!@!@ENDMSG 2190 @!@!@
"""
TAIL = """@!@!@STARTMSG 2186:0 @!@!@
Finished in 236ms at (2026-10-20 04:37:07)
@!@!@ENDMSG 2186 @!@!@
"""


def stats(generated: int, distinct: int, depth: int) -> str:
    return (f"@!@!@STARTMSG 2199:0 @!@!@\n{generated} states generated, {distinct} distinct states found, 0 states "
            f"left on queue.\n@!@!@ENDMSG 2199 @!@!@\n@!@!@STARTMSG 2194:0 @!@!@\nThe depth of the complete state "
            f"graph search is {depth}.\n@!@!@ENDMSG 2194 @!@!@\n")


INVARIANT_VIOLATED = HEAD + """@!@!@STARTMSG 2110:1 @!@!@
Invariant Inv is violated.
@!@!@ENDMSG 2110 @!@!@
@!@!@STARTMSG 2121:1 @!@!@
The behavior up to this point is:
@!@!@ENDMSG 2121 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
1: <Initial predicate>
x = 0

@!@!@ENDMSG 2217 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
2: <Next line 5, col 9 to line 5, col 18 of module Counter>
x = 1

@!@!@ENDMSG 2217 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
3: <Next line 5, col 9 to line 5, col 18 of module Counter>
x = 2

@!@!@ENDMSG 2217 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
4: <Next line 5, col 9 to line 5, col 18 of module Counter>
x = 3

@!@!@ENDMSG 2217 @!@!@
@!@!@STARTMSG 2200:0 @!@!@
Progress(4) at 2026-10-20 04:37:07: 4 states generated (1,016 s/min), 4 distinct states found (1,016 ds/min), 0 states left on queue.
@!@!@ENDMSG 2200 @!@!@
""" + stats(4, 4, 4) + TAIL

NO_ERROR = HEAD + """@!@!@STARTMSG 2193:0 @!@!@
Model checking completed. No error has been found.
  Estimates of the probability that TLC did not check all reachable states
  because two distinct states had the same fingerprint:
  calculated (optimistic):  val = 3.3E-19
@!@!@ENDMSG 2193 @!@!@
@!@!@STARTMSG 2200:0 @!@!@
Progress(6) at 2026-10-20 04:37:21: 7 states generated (1,818 s/min), 6 distinct states found (1,558 ds/min), 0 states left on queue.
@!@!@ENDMSG 2200 @!@!@
""" + stats(7, 6, 6) + """@!@!@STARTMSG 2268:0 @!@!@
The average outdegree of the complete state graph is 1 (minimum is 0, the maximum 1 and the 95th percentile is 1).
@!@!@ENDMSG 2268 @!@!@
""" + TAIL

DEADLOCK = HEAD + """@!@!@STARTMSG 2114:1 @!@!@
Deadlock reached.
@!@!@ENDMSG 2114 @!@!@
@!@!@STARTMSG 2121:1 @!@!@
The behavior up to this point is:
@!@!@ENDMSG 2121 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
1: <Initial predicate>
/\\ s = {1, 2}
/\\ x = 0

@!@!@ENDMSG 2217 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
2: <Next line 5, col 9 to line 5, col 46 of module Dead>
/\\ s = {0, 1, 2}
/\\ x = 1

@!@!@ENDMSG 2217 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
3: <Next line 5, col 9 to line 5, col 46 of module Dead>
/\\ s = {0, 1, 2}
/\\ x = 2

@!@!@ENDMSG 2217 @!@!@
""" + stats(3, 3, 3) + TAIL

STUTTERING = HEAD + """@!@!@STARTMSG 2192:0 @!@!@
Checking temporal properties for the complete state space with 3 total distinct states at (2026-10-20 04:37:31)
@!@!@ENDMSG 2192 @!@!@
@!@!@STARTMSG 2116:1 @!@!@
Temporal properties were violated.

@!@!@ENDMSG 2116 @!@!@
@!@!@STARTMSG 2264:1 @!@!@
The following behavior constitutes a counter-example:

@!@!@ENDMSG 2264 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
1: <Initial predicate>
x = 0

@!@!@ENDMSG 2217 @!@!@
@!@!@STARTMSG 2218:4 @!@!@
2: Stuttering
@!@!@ENDMSG 2218 @!@!@
@!@!@STARTMSG 2267:0 @!@!@
Finished checking temporal properties in 00s at 2026-10-20 04:37:31
@!@!@ENDMSG 2267 @!@!@
""" + stats(4, 3, 3) + TAIL

LASSO = HEAD + """@!@!@STARTMSG 2116:1 @!@!@
Temporal properties were violated.

@!@!@ENDMSG 2116 @!@!@
@!@!@STARTMSG 2264:1 @!@!@
The following behavior constitutes a counter-example:

@!@!@ENDMSG 2264 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
1: <Initial predicate>
/\\ x = 0
/\\ log = [name |-> "start", seen |-> {}, path |-> <<1, 2>>, f |-> <<"a", "b">>]

@!@!@ENDMSG 2217 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
2: <Next line 5, col 9 to line 5, col 175 of module Lasso>
/\\ x = 1
/\\ log = [ name |->
      "a much longer name to make this record wrap over several lines of output when TLC prints it out",
  seen |-> {0},
  path |-> <<1, 2>>,
  f |-> <<"a", "b">> ]

@!@!@ENDMSG 2217 @!@!@
@!@!@STARTMSG 2122:4 @!@!@
1: Back to state: <Next line 5, col 9 to line 5, col 175 of module Lasso>

@!@!@ENDMSG 2122 @!@!@
""" + stats(7, 6, 6) + TAIL

INITIAL_STATE = HEAD.replace("""@!@!@STARTMSG 2190:0 @!@!@
Finished computing initial states: 1 distinct state generated at 2026-10-20 04:37:07.
@!@!@ENDMSG 2190 @!@!@
""", "") + """@!@!@STARTMSG 2107:1 @!@!@
Invariant InitBad is violated by the initial state:
x = 0

@!@!@ENDMSG 2107 @!@!@
""" + TAIL

ACTION_PROPERTY = HEAD + """@!@!@STARTMSG 2112:1 @!@!@
Action property Up is violated.
@!@!@ENDMSG 2112 @!@!@
@!@!@STARTMSG 2121:1 @!@!@
The behavior up to this point is:
@!@!@ENDMSG 2121 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
1: <Initial predicate>
x = 2

@!@!@ENDMSG 2217 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
2: <Next line 6, col 9 to line 6, col 24 of module Misc>
x = 0

@!@!@ENDMSG 2217 @!@!@
""" + TAIL

ASSUMPTION = HEAD + """@!@!@STARTMSG 2104:1 @!@!@
Assumption line 4, col 8 to line 4, col 12 of module Misc is false.
@!@!@ENDMSG 2104 @!@!@
""" + TAIL

ASSERT_IN_ACTION = HEAD + """@!@!@STARTMSG 2132:1 @!@!@
The first argument of Assert evaluated to FALSE; the second argument was:
"too far"
@!@!@ENDMSG 2132 @!@!@
@!@!@STARTMSG 2121:1 @!@!@
The behavior up to this point is:
@!@!@ENDMSG 2121 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
1: <Initial predicate>
x = 0

@!@!@ENDMSG 2217 @!@!@
@!@!@STARTMSG 2217:4 @!@!@
2: <Next line 5, col 9 to line 5, col 46 of module Asrt>
x = 1

@!@!@ENDMSG 2217 @!@!@
""" + TAIL

EVALUATION_ERROR = HEAD + """@!@!@STARTMSG 1000:1 @!@!@
@!@!@STARTMSG 2179:0 @!@!@
The second argument of \\div is 0.
@!@!@ENDMSG 2179 @!@!@
@!@!@ENDMSG 1000 @!@!@
@!@!@STARTMSG 2103:1 @!@!@
The error occurred when TLC was evaluating the nested
expressions at the following positions:
0. Line 5, column 9 to line 5, column 13 in Misc
1. Line 9, column 9 to line 9, column 28 in Misc


@!@!@ENDMSG 2103 @!@!@
""" + TAIL

CONFIG_ERROR = HEAD + """@!@!@STARTMSG 2229:1 @!@!@
The invariant Nope specified in the configuration file
is not defined in the specification.
@!@!@ENDMSG 2229 @!@!@
""" + TAIL

PARSE_FAILED = """@!@!@STARTMSG 2262:0 @!@!@
TLC2 Version 2026.03.24.222644 (rev: 1476e7f)
@!@!@ENDMSG 2262 @!@!@
@!@!@STARTMSG 2220:0 @!@!@
Starting SANY...
@!@!@ENDMSG 2220 @!@!@
Parsing file /tmp/g6_tlc_run/spec/Bad2.tla
***Parse Error***
Was expecting "Expression or Instance"
Encountered "Beginning of definition" at line 4, column 9 and token "=="

Residual stack trace follows:
Definition starting at line 4, column 1.
Module body starting at line 3, column 1.
Module definition starting at line 1, column 1.


Fatal errors while parsing TLA+ spec in file Bad2

In module Bad2

Could not parse module Bad2 from file Bad2.tla
*** Errors: 1

@!@!@STARTMSG 2219:0 @!@!@
SANY finished.
@!@!@ENDMSG 2219 @!@!@
@!@!@STARTMSG 3002:1 @!@!@
Parsing or semantic analysis failed.
@!@!@ENDMSG 3002 @!@!@
""" + TAIL


def test_an_invariant_violation_with_its_trace() -> None:
    run = parse_tlc_output(INVARIANT_VIOLATED, 12)
    assert run["verdict"] == "invariant_violated" and run["violated"] == "Inv"
    assert run["version"] == "2026.03.24.222644 (rev: 1476e7f)"
    assert [s["variables"] for s in run["trace"]] == [{"x": 0}, {"x": 1}, {"x": 2}, {"x": 3}]
    assert [s["number"] for s in run["trace"]] == [1, 2, 3, 4]
    assert run["trace"][0]["action"] == "Initial predicate" and run["trace"][1]["action"].startswith("Next line 5")
    assert run["trace"][3]["assignments"] == {"x": "3"}
    assert (run["states_generated"], run["distinct_states"], run["states_left"], run["depth"]) == (4, 4, 0, 4)
    assert run["exit_code"] == 12 and run["trace_end"] == {}


def test_no_error_is_read_only_from_the_success_message_and_a_clean_exit() -> None:
    run = parse_tlc_output(NO_ERROR, 0)
    assert run["verdict"] == "no_error" and run["trace"] == [] and run["violated"] == ""
    assert (run["states_generated"], run["distinct_states"], run["depth"]) == (7, 6, 6)
    assert run["fingerprint_collision"] == "3.3E-19" and run["errors"] == []


def test_a_deadlock_with_several_variables_and_sets() -> None:
    run = parse_tlc_output(DEADLOCK, 11)
    assert run["verdict"] == "deadlock"
    assert run["trace"][0]["variables"] == {"s": {"set": [1, 2]}, "x": 0}
    assert run["trace"][2]["variables"] == {"s": {"set": [0, 1, 2]}, "x": 2}


def test_a_temporal_violation_ending_in_stuttering_or_a_loop() -> None:
    stutter = parse_tlc_output(STUTTERING, 13)
    assert stutter["verdict"] == "property_violated" and stutter["violated"] == "temporal properties"
    assert len(stutter["trace"]) == 1 and stutter["trace_end"] == {"kind": "stuttering", "state": 2}
    lasso = parse_tlc_output(LASSO, 13)
    assert lasso["trace_end"] == {"kind": "back_to_state", "state": 1} and len(lasso["trace"]) == 2
    log = lasso["trace"][1]["variables"]["log"]
    assert log == {"name": "a much longer name to make this record wrap over several lines of output when TLC "
                           "prints it out", "seen": {"set": [0]}, "path": [1, 2], "f": ["a", "b"]}


def test_an_invariant_broken_by_the_initial_state() -> None:
    run = parse_tlc_output(INITIAL_STATE, 12)
    assert run["verdict"] == "invariant_violated" and run["violated"] == "InitBad"
    assert run["trace"] == [{"number": 1, "action": "Initial predicate", "variables": {"x": 0},
                             "assignments": {"x": "0"}}]


@pytest.mark.parametrize(("text", "code", "verdict", "violated"), [
    (ACTION_PROPERTY, 13, "property_violated", "Up"),
    (ASSUMPTION, 10, "assumption_violated", ""),
    (ASSERT_IN_ACTION, 14, "assertion_failed", ""),
    (EVALUATION_ERROR, 75, "evaluation_error", ""),
    (CONFIG_ERROR, 151, "spec_error", ""),
    (PARSE_FAILED, 150, "spec_error", ""),
])
def test_each_other_verdict_and_what_tlc_said(text: str, code: int, verdict: str, violated: str) -> None:
    run = parse_tlc_output(text, code)
    assert run["verdict"] == verdict and run["violated"] == violated and run["errors"], run
    assert all(set(e) == {"code", "text"} for e in run["errors"])


def test_nested_messages_are_read_inside_their_parent() -> None:
    run = parse_tlc_output(EVALUATION_ERROR, 75)
    assert [e["code"] for e in run["errors"]] == [1000, 2103]
    assert "The second argument of \\div is 0." in run["errors"][0]["text"]
    asserted = parse_tlc_output(ASSERT_IN_ACTION, 14)
    assert '"too far"' in asserted["errors"][0]["text"] and len(asserted["trace"]) == 2


def test_a_parse_failure_carries_sanys_errors_with_their_places() -> None:
    run = parse_tlc_output(PARSE_FAILED, 150)
    assert run["sany_errors"] and run["sany_errors"][0]["line"] == 4 and run["sany_errors"][0]["column"] == 9


@pytest.mark.parametrize(("text", "code"), [
    (NO_ERROR, 12),                                      # a success message with a violation's exit code
    (NO_ERROR.replace("Model checking completed. No error has been found.", "Model checking stopped."), 0),
    (INVARIANT_VIOLATED, 0),                             # a violation with a clean exit
    (INVARIANT_VIOLATED, 11),                            # a violation with a deadlock's exit code
    (DEADLOCK, 12),
    (INVARIANT_VIOLATED.split("@!@!@STARTMSG 2110")[0], 12),   # cut off before TLC said what it found
    ("", 0), ("java.lang.OutOfMemoryError: Java heap space\n", 0),
    (NO_ERROR.replace(stats(7, 6, 6), ""), 0),           # no state counts, so no account of what was explored
    (NO_ERROR + CONFIG_ERROR.split(TAIL)[0].split(HEAD)[1], 0),   # an error message alongside the success one
])
def test_anything_that_does_not_fit_is_unrecognised_never_a_pass(text: str, code: int) -> None:
    assert parse_tlc_output(text, code)["verdict"] == "unrecognised"


def test_windows_line_endings_read_the_same() -> None:
    assert parse_tlc_output(INVARIANT_VIOLATED.replace("\n", "\r\n"), 12) == parse_tlc_output(INVARIANT_VIOLATED, 12)


# --- values ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "value"), [
    ("0", 0), ("-12", -12), ("TRUE", True), ("FALSE", False), ('"a b"', "a b"), ('"say \\"hi\\""', 'say "hi"'),
    ("{}", {"set": []}), ("{1, 2}", {"set": [1, 2]}), ("<<>>", []), ('<<1, "a", {TRUE}>>', [1, "a", {"set": [True]}]),
    ("[a |-> 1, b |-> <<2>>]", {"a": 1, "b": [2]}), ("(1 :> 2 @@ 3 :> 4)", {"function": [[1, 2], [3, 4]]}),
    ("(a :> 1)", {"function": [[{"model_value": "a"}, 1]]}), ("m1", {"model_value": "m1"}),
    ("{<<1, 2>>, <<2, 1>>}", {"set": [[1, 2], [2, 1]]}),
    ("[ name |->\n      \"long\",\n  seen |-> {0} ]", {"name": "long", "seen": {"set": [0]}}),
    ("1..3", {"text": "1..3"}), ("{1, 2", {"text": "{1, 2"}), ("[x \\in {1} |-> 2]", {"text": "[x \\in {1} |-> 2]"}),
])
def test_tlc_values_become_json(text: str, value) -> None:
    assert parse_value(text) == value


# --- SANY ----------------------------------------------------------------------------------------------------------------

SANY_OK = """
****** SANY2 Version 2.2 created 08 July 2020

Parsing file /tmp/g6_tlc_run/spec/Counter.tla
Parsing file /tmp/g6_tlc_run/java_tmp/tlc-9261042245517684458/Naturals.tla (jar:file:/tmp/g6_tlc_run/tools/tla2tools.jar!/tla2sany/StandardModules/Naturals.tla)
Semantic processing of module Naturals
Semantic processing of module Counter
Linting of module Counter
"""
SANY_PARSE_ERROR = """
****** SANY2 Version 2.2 created 08 July 2020

Parsing file /tmp/g6_tlc_run/spec/Bad.tla
***Parse Error***
Was expecting "Expression or Instance"
Encountered "Beginning of definition" at line 4, column 9 and token "=="

Residual stack trace follows:
Definition starting at line 4, column 1.
Module body starting at line 3, column 1.
Module definition starting at line 1, column 1.


Fatal errors while parsing TLA+ spec in file Bad.tla

In module Bad

Could not parse module Bad from file Bad.tla
*** Errors: 1

In module Bad

Could not parse module Bad from file Bad.tla



"""
SANY_SEMANTIC_ERROR = """
****** SANY2 Version 2.2 created 08 July 2020

Parsing file /tmp/g6_tlc_run/spec/Sem.tla
Parsing file /tmp/g6_tlc_run/java_tmp/tlc-15365453950965947348/Naturals.tla (jar:file:/tmp/g6_tlc_run/tools/tla2tools.jar!/tla2sany/StandardModules/Naturals.tla)
Semantic processing of module Naturals
Semantic processing of module Sem
Semantic errors:

*** Errors: 1

line 5, col 14 to line 5, col 14 of module Sem

Unknown operator: `y'.


"""


def test_sany_reports_a_clean_module() -> None:
    run = parse_sany_output(SANY_OK, 0, "Counter")
    assert run["verdict"] == "no_error" and run["errors"] == [] and run["version"] == "2.2 created 08 July 2020"


def test_sany_reports_a_parse_error_with_its_place() -> None:
    run = parse_sany_output(SANY_PARSE_ERROR, 255, "Bad")
    assert run["verdict"] == "errors"
    assert run["errors"][0]["line"] == 4 and run["errors"][0]["column"] == 9
    assert "Was expecting" in run["errors"][0]["message"]


def test_sany_reports_a_semantic_error_even_with_a_clean_exit() -> None:
    """Without -error-codes SANY exits 0 on a semantic error; the output decides, so this is still an error."""
    for code in (0, 4):
        run = parse_sany_output(SANY_SEMANTIC_ERROR, code, "Sem")
        assert run["verdict"] == "errors" and run["errors"][0]["message"] == "Unknown operator: `y'."
        assert (run["errors"][0]["line"], run["errors"][0]["column"], run["errors"][0]["module"]) == (5, 14, "Sem")


@pytest.mark.parametrize(("text", "code", "module"), [
    (SANY_OK, 2, "Counter"),                             # a clean-looking output with a failing exit code
    (SANY_OK, 0, "Other"),                               # the module asked for was never processed
    ("", 0, "Counter"),
])
def test_sany_fails_closed(text: str, code: int, module: str) -> None:
    assert parse_sany_output(text, code, module)["verdict"] == "errors"
