# SPDX-License-Identifier: Apache-2.0
"""MetaProgrammingBlock: the registry and MCP door (``g6_invoke``) to meta_programming.

``parse``, ``analyze``, ``rename`` and ``extract_function`` read and edit the source they are given as text, with the
standard library, and run none of it. ``run_tests`` runs your tests on your code in ``g6.core.sandbox`` (a bounded
child process on this computer), so it is sensitive: callable from Python, and over MCP only when the person who runs
the server lists it in ``G6_MCP_SENSITIVE``. Every answer is ``qualified-draft`` at best: an edit that passed its
checks is a structural fact about the text, and whether the code still does what it did is for your tests to say.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.code._types import CodeRefused, answer, refuse
from g6.code.meta_programming import analyze as reader
from g6.code.meta_programming.edits import DOES_NOT_PARSE, EDIT_UNCHECKED, Edit, extract_function, parse, rename
from g6.code.meta_programming.runner import run_tests
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result

COMPONENT = "meta_programming"
INPUT = "G6_E_SYNTAX_INPUT"
PARSED = "G6_E_SYNTAX_PARSED"
ANALYSED = "G6_E_SYNTAX_ANALYSED"
EDITED = "G6_E_SYNTAX_EDITED"
TESTS_PASSED = "G6_E_SYNTAX_TESTS_PASSED"
TESTS_FAILED = "G6_E_SYNTAX_TESTS_FAILED"
TESTS_DID_NOT_RUN = "G6_E_SYNTAX_TESTS_DID_NOT_RUN"
TEST_FORM_UNSUPPORTED = "G6_E_SYNTAX_TEST_FORM_UNSUPPORTED"
SENSITIVE_OPS: tuple[str, ...] = ("run_tests",)
_OPS: dict[str, str] = {
    "parse": "parse(source): whether the source parses, and where and why it does not (Python's own parser)",
    "analyze": "analyze(source): its functions (parameters, decorators, docstrings, decision counts), classes, "
               "imports, __all__ and module-level names, read from the syntax tree",
    "rename": "rename(source, old, new, line): every identifier that means one binding of old renamed, and nothing "
              "else; comments and layout kept; re-parsed, compiled and checked against the renamed tree",
    "extract_function": "extract_function(source, start_line, end_line, name): whole statements of a function moved "
                        "into a new function with the parameters and returns their names need, called where they "
                        "were; re-parsed, compiled and checked",
    "run_tests": "run_tests(source, tests, module, timeout_s): your test functions run on your code in a bounded "
                 "child process; sensitive",
}
_ACTIONS = {
    INPUT: "Give the fields the op needs (the help op lists them).",
    PARSED: "Read parses; where it is false, the error says where.",
    DOES_NOT_PARSE: "Fix the syntax error at the place named, then ask again.",
    ANALYSED: "These are what the text declares; what the code does at run time is not read.",
    EDITED: "Read the changed spans and what was left alone, then run your tests on edited (run_tests).",
    EDIT_UNCHECKED: "Nothing was returned: the edit failed a check. Report the source and the request.",
    TESTS_PASSED: "Your tests passed on this code: your tests' claim. Read what they cover before you rely on it.",
    TESTS_FAILED: "Read each failed test's message; the code, or the test, is wrong.",
    TESTS_DID_NOT_RUN: "Read the reason: the run timed out, failed to start or did not report.",
}


class MetaProgrammingInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["parse", "analyze", "rename", "extract_function", "run_tests", "ops", "help"] = "help"
    source: str = Field("", max_length=1_000_000)
    old: str = Field("", max_length=100)
    new: str = Field("", max_length=100)
    line: int | None = Field(None, ge=1)
    start_line: int | None = Field(None, ge=1)
    end_line: int | None = Field(None, ge=1)
    name: str = Field("", max_length=100)
    tests: str = Field("", max_length=500_000)
    module: str = Field("subject", max_length=100)
    timeout_s: float = Field(30.0, gt=0, le=120)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: MetaProgrammingInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _edited(op: str, edit: Edit, d: MetaProgrammingInput, message: str) -> dict[str, Any]:
    evidence = [{"kind": "edit_checks", "checks": edit.checks, "ran_code": False},
                {"kind": "changed_spans", "changes": edit.changes}]
    return answer(COMPONENT, op, EDITED, message + " Whether the code behaves as before is not checked: run your "
                  "tests on edited.", action=_ACTIONS[EDITED], evidence=evidence, **_ids(d), edited=edit.source,
                  changes=edit.changes, checks=edit.checks, left_alone=edit.left_alone, **edit.detail)


def parse_op(d: MetaProgrammingInput) -> dict[str, Any]:
    found = reader.parse(d.source)
    if found["parses"]:
        return answer(COMPONENT, "parse", PARSED, f"The source parses: {found['statements']} top-level statement(s).",
                      action=_ACTIONS[PARSED], evidence=[{"kind": "parse", "by": "Python's own parser (ast)"}],
                      **_ids(d), **found)
    error = found["error"]
    return answer(COMPONENT, "parse", DOES_NOT_PARSE, f"The source does not parse: {error['message']} (line "
                  f"{error['line']}).", action=_ACTIONS[DOES_NOT_PARSE], severity="red",
                  evidence=[{"kind": "parse", "by": "Python's own parser (ast)"}], **_ids(d), **found)


def analyze_op(d: MetaProgrammingInput) -> dict[str, Any]:
    found = reader.analyze(parse(d.source))
    said = (f"{len(found['functions'])} function(s), {len(found['classes'])} class(es) and {len(found['imports'])} "
            "import(s), read from the syntax tree; nothing was run.")
    return answer(COMPONENT, "analyze", ANALYSED, said, action=_ACTIONS[ANALYSED],
                  evidence=[{"kind": "syntax_tree", "ran_code": False}], **_ids(d), **found)


def rename_op(d: MetaProgrammingInput) -> dict[str, Any]:
    if not d.old or not d.new:
        raise CodeRefused(INPUT, "give source, old and new (and line, where old is bound in more than one scope)")
    edit = rename(d.source, d.old, d.new, d.line)
    return _edited("rename", edit, d, f"Renamed {edit.detail['occurrences']} identifier(s) that mean "
                   f"{edit.detail['binding']}'s {d.old} to {d.new}; the text was re-parsed and compiled, its tree is "
                   "the old one with exactly those renamed, and its comments are kept.")


def extract_op(d: MetaProgrammingInput) -> dict[str, Any]:
    if d.start_line is None or d.end_line is None or not d.name:
        raise CodeRefused(INPUT, "give source, start_line, end_line and name (the new function's)")
    edit = extract_function(d.source, d.start_line, d.end_line, d.name)
    params, returns = edit.detail["params"], edit.detail["returns"]
    return _edited("extract_function", edit, d, f"Moved lines {d.start_line} to {d.end_line} into {d.name}("
                   f"{', '.join(params)})" + (f", returning {', '.join(returns)}" if returns else "") + "; the text "
                   "was re-parsed and compiled, the moved statements are unchanged, and the call stands in their "
                   "place.")


def run_tests_op(d: MetaProgrammingInput) -> dict[str, Any]:
    if not d.tests.strip():
        raise CodeRefused(INPUT, "give tests: Python source with test functions that import the code as module")
    if not d.module.isidentifier() or d.module.startswith("_"):
        raise CodeRefused(INPUT, "module is the name the tests import the code as: a Python identifier")
    parse(d.source)
    _parses(d.tests)
    ran = run_tests(d.source, d.tests, d.module, timeout_s=d.timeout_s)
    evidence = [{"kind": "test_run", "where": "g6.core.sandbox (a bounded child process, not G6's own)",
                 "limits": ran["limits"], "isolation": ran["isolation"], "status": ran["status"],
                 "seconds": ran["seconds"],
                 "whose": "the caller's tests on the caller's code: a claim, not a check G6 made"}]
    fields = {k: ran[k] for k in ("collected", "results", "status", "stderr")}
    failed = [r for r in ran["results"] if r["outcome"] != "passed"]
    if not ran["collected"] or not ran["results"] or ran["status"] in ("timeout", "refused"):
        why = ran["error"] or ("the run timed out" if ran["status"] == "timeout" else ran["stderr"][:600]
                               if ran["status"] == "refused" else "the run did not report any test"
                               if ran["collected"] in (None, 0) else ran["stderr"][-200:] or ran["status"])
        return refuse(COMPONENT, "run_tests", TESTS_DID_NOT_RUN, f"No test outcome to report: {why}.",
                      action=_ACTIONS[TESTS_DID_NOT_RUN], evidence=evidence, **_ids(d), **fields)
    if any(r["outcome"] == "unsupported" for r in ran["results"]):
        return refuse(COMPONENT, "run_tests", TEST_FORM_UNSUPPORTED,
                      "Async and generator tests are refused: their bodies were not executed.",
                      action="Use synchronous test functions that complete when called.",
                      evidence=evidence, **_ids(d), **fields)
    if failed:
        return refuse(COMPONENT, "run_tests", TESTS_FAILED, f"{len(failed)} of {len(ran['results'])} test(s) failed; "
                      f"first: {failed[0]['test']}: {failed[0]['said']}", action=_ACTIONS[TESTS_FAILED],
                      evidence=evidence, **_ids(d), **fields)
    return answer(COMPONENT, "run_tests", TESTS_PASSED, f"All {len(ran['results'])} of your test(s) passed on this "
                  "code, in a bounded child process: your tests' claim, not a check G6 made.",
                  action=_ACTIONS[TESTS_PASSED], evidence=evidence, **_ids(d), **fields)


def _parses(text: str) -> None:
    try:
        compile(text, "<tests>", "exec", dont_inherit=True)  # compiled to check the syntax; never run here
    except (SyntaxError, ValueError) as exc:
        raise CodeRefused(DOES_NOT_PARSE, f"the tests do not parse: {exc}") from None


_HANDLERS = {"parse": parse_op, "analyze": analyze_op, "rename": rename_op, "extract_function": extract_op,
             "run_tests": run_tests_op}


@block_contract(
    purpose="read, rename and extract Python code through its syntax tree and scopes, so an edit changes exactly the "
            "identifiers and statements it means and keeps every comment, instead of a text edit that breaks the "
            "code's structure",
    mitigates_failure_modes=("fragile_text_edit",),
    verification_method="every edit re-parsed and compiled, and its tree compared with the tree the edit should "
                        "give (rename: the old tree with exactly the bound identifiers renamed; extract: the moved "
                        "statements unchanged and the call in their place); comments compared; behaviour is left to "
                        "the caller's tests, run in g6.core.sandbox",
)
@component_maturity("beta", reason="standard-library scopes and span edits, tested on shadowing, parameters, "
                                   "attributes, comments and moved statements")
class MetaProgrammingBlock(AIBlock[MetaProgrammingInput, dict, dict]):
    """Read and edit Python code through its syntax tree. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: MetaProgrammingInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            return Result.ok(_HANDLERS[data.op](data))
        except (RecursionError, MemoryError) as exc:
            return Result.ok(refuse(COMPONENT, data.op, "G6_E_CODE_RESOURCE_LIMIT",
                                    f"input exceeds syntax processing limits: {type(exc).__name__}",
                                    action="Supply a smaller, less deeply nested source.", **_ids(data)))
        except CodeRefused as exc:
            return Result.ok(refuse(COMPONENT, data.op, exc.code, exc.reason, action=_ACTIONS.get(
                exc.code, "Correct the request as the reason says."), evidence=exc.evidence, **_ids(data)))


__all__ = ["COMPONENT", "SENSITIVE_OPS", "MetaProgrammingBlock", "MetaProgrammingInput"]
