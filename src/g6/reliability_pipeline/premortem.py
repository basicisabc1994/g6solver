# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Stage 1 premortem: the failure-mode checklist and the one bounded check constructor (``construct_replay_check``).

The archive's ``run_premortem`` stage (and its plan-critic dependency) is not ported; only the constant checklist and
the constructor the coding mission uses are (audit 009 §11). The constructor recognises one benchmark phrasing of an
event-replay requirement (WF-015): it is a narrow, regex-bounded compiler, not harness intelligence, and what it builds
is task-derived and nonindependent (it may refute a change, never independently support one). Every construction
says so itself (``CONSTRUCTOR``, and ``TEMPLATE_LIMIT`` among the limits a person reads before confirming); the coding
standard's archive key ``harness_intelligence`` is kept for compatibility only.
"""
from __future__ import annotations

import ast
import hashlib
import json
import keyword
import re
from pathlib import Path



_CHECKLIST = (
    ("unclear_success", "Are acceptance criteria measurable?", "Block or clarify before execution."),
    ("context_loss", "Does the task depend on unavailable files or facts?", "Surface missing inputs."),
    ("unsafe_autonomous_action", "Could the task mutate protected state?", "Require explicit approval and rollback."),
    ("unverified_reliability", "What evidence would prove completion?", "Attach tests or evidence bundle."),
    ("missing_domain_standard", "Does the task require a domain evidence standard?", "Select an epistemic contract before execution."),
    ("wrong_risk_tier", "Could harm or irreversibility be underestimated?", "Classify risk before routing."),
    ("budget_overrun", "Could the task exceed token, cost, or latency budget?", "Set a budget and scope down if needed."),
    ("unavailable_tool", "Does the plan rely on unavailable tools or harnesses?", "Record the missing dependency and downgrade or block."),
    ("hidden_assumption", "What assumptions would change the answer if false?", "Track assumptions in the execution ledger."),
    ("source_gap", "What claims need citations or source evidence?", "Track sources and unsupported claims."),
    ("verification_gap", "Which independent harness should verify the output?", "Dispatch per-harness verification."),
    ("handoff_gap", "Would a user know how to rely on the result?", "Produce a warning card and handoff package."),
    ("learning_gap", "Would a reliability incident be captured for later improvement?", "Record incidents and observations."),
)


# The premortem failure-mode taxonomy, exported so other harnesses (e.g. the
# verification-deck edge-case generator, P2-b) can target the SAME named modes
# instead of inventing a parallel vocabulary.
PREMORTEM_FAILURE_MODES: tuple[str, ...] = tuple(code for code, _q, _g in _CHECKLIST)

#: Audit 010 WF-015: what the constructor is, recorded in every construction it returns (and, by the coding mission, in
#: every standard it did not apply to): two fixed check templates for one kind of requirement (an identical event retry
#: leaves state unchanged), each selected only by one fixed requirement sentence or by a replay relation a person
#: confirmed. Not a general premortem, and not harness intelligence: a reworded requirement gets no check.
CONSTRUCTOR = {"kind": "fixed_templates", "templates": ("event-replay-v1", "adjacent-replay.v1"),
               "selected_by": "one fixed requirement sentence each, or a replay relation a person confirmed",
               "general": False}
# COPY: The limit every constructed check carries; the person reads it before confirming (audit 010 WF-015).
TEMPLATE_LIMIT = ("A fixed template, not harness intelligence: G6 adds this check only for one requirement sentence or "
                  "a replay relation a person confirmed; a reworded requirement gets no check.")


def constructor() -> dict:
    """``CONSTRUCTOR`` as JSON-ready data (a fresh copy each time)."""
    return {**CONSTRUCTOR, "templates": list(CONSTRUCTOR["templates"])}


def construct_replay_check(task: str, *, module: str, function: str,
                           test_sources: dict[str, str], executed_tests: tuple[str, ...] = (),
                           clarified_requirement: str = "", relation: str | None = None) -> dict | None:
    """Compile one explicit identical-retry requirement, without running supplied code.

    This static, deliberately bounded policy supports a root Python module and
    two literal dictionary arguments from an executed positive test assertion. It is
    not learned planning, semantic understanding of arbitrary prose, or an
    independent oracle. The new obligation must be shown and accepted before use.
    Missing/ambiguous inputs produce no construction. No experiment fixtures are
    inputs; a distinct event is derived from the supplied repository example.
    """
    from g6.reliability_pipeline.failure_modes import ADJACENT_REQUIREMENT
    from g6.reliability_pipeline.intake import REPLAY_REQUIREMENT

    if relation not in (None, "event-replay-v1", "adjacent-replay.v1"):
        return None
    adjacent = relation == "adjacent-replay.v1" or (relation is None and
                clarified_requirement == ADJACENT_REQUIREMENT)
    selected_requirement = (ADJACENT_REQUIREMENT if adjacent else REPLAY_REQUIREMENT) if relation else (clarified_requirement or task)
    requirement = re.fullmatch(
        r"make [a-z ]{1,80} apply each stable event id at most once, even when another event "
        r"arrives between identical retries\.(?: preserve [a-z ,]{1,180}\.)?",
        " ".join(selected_requirement.lower().split()))
    if adjacent:
        requirement = re.fullmatch(re.escape(ADJACENT_REQUIREMENT.lower()), selected_requirement.lower())
    stem = module.removesuffix(".py")
    if (not requirement or not module.endswith(".py") or not stem.isidentifier()
            or keyword.iskeyword(stem) or not function.isidentifier() or keyword.iskeyword(function)
            or len(test_sources) > 128 or sum(len(s) for s in test_sources.values()) > 262144):
        return None
    for path, source in sorted(test_sources.items()):
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError, RecursionError):
            continue
        imported = {a.asname or a.name for n in tree.body if isinstance(n, ast.ImportFrom)
                    and n.module == stem and n.level == 0 for a in n.names if a.name == function}
        # Refuse ambiguous bindings, including aliases shadowed in any scope.
        imports = [a.asname or a.name.split(".")[0] for n in ast.walk(tree)
                   if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
        if (any(imports.count(name) != 1 for name in imported)
                or any(isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)) and n.id in imported
                       or isinstance(n, ast.arg) and n.arg in imported
                       or isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in imported
                       for n in ast.walk(tree))):
            continue
        # Only direct assertions in a named, executed no-argument test are a
        # source. No dead helpers, nested branches, fixtures or arbitrary AST walk.
        definitions = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        nodes = []
        for test in tree.body:
            if not (isinstance(test, ast.FunctionDef) and f"{path}::{test.name}" in executed_tests
                    and definitions.count(test.name) == 1
                    and not (test.args.posonlyargs or test.args.args or test.args.kwonlyargs
                             or test.args.vararg or test.args.kwarg or test.decorator_list)):
                continue
            body = test.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]  # optional docstring, no return/branch/dead assertion
            if (all(isinstance(n, ast.Assert) for n in body)
                    and not any(isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))
                                and n.id == test.name for n in ast.walk(tree))):
                nodes.extend(body)
        for node in nodes:
            if not (isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare)
                    and len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.Eq)):
                continue
            call = node.test.left
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id in imported and len(call.args) == 2 and not call.keywords):
                continue
            try:
                state, event = (ast.literal_eval(a) for a in call.args)
                expected = ast.literal_eval(node.test.comparators[0])
                # JSON round-trip disallows sets, bytes and non-string mapping keys.
                values = [state, event, expected]
                if json.loads(json.dumps(values, allow_nan=False)) != values:
                    continue
            except (TypeError, ValueError, SyntaxError, RecursionError):
                continue
            if not (isinstance(state, dict) and isinstance(event, dict) and isinstance(expected, dict)
                    and state != expected and isinstance(event.get("id"), str) and 0 < len(event["id"]) <= 128):
                continue
            intervening = {**event, "id": event["id"] + ":g6-distinct"}
            code = (
                "# G6 event-replay-v1: task-derived, nonindependent.\n"
                "from copy import deepcopy as _g6_copy\n"
                f"from {stem} import {function} as _g6_target\n\n"
                "def test_nonadjacent_event_replay():\n"
                f"    initial = {state!r}\n"
                f"    event = {event!r}\n"
                f"    intervening = {intervening!r}\n"
                "    first = _g6_target(_g6_copy(initial), _g6_copy(event))\n"
                "    second = _g6_target(_g6_copy(first), _g6_copy(intervening))\n"
                "    replayed = _g6_target(_g6_copy(second), _g6_copy(event))\n"
                "    assert replayed == second, 'identical event replay after an intervening event changed state'\n"
            )
            def sha(s: str) -> str:
                return hashlib.sha256(s.encode("utf-8")).hexdigest()
            predicate = f"{stem}.{function}.unique-event-effect.v1"
            failure = "nonadjacent_event_replay"
            check = {"predicate_id": predicate, "failure_id": failure,
                     "nodeid": f".g6_harness/test_replay_{sha(code)[:16]}.py::test_nonadjacent_event_replay",
                     "source": code, "source_sha256": sha(code),
                     "inputs": {"initial": state, "event": event, "intervening": intervening},
                     "input_derivation": "Copy the literal event; append ':g6-distinct' to its ID for a distinct intervening event.",
                     "example": {"path": path, "line": node.lineno, "source_sha256": sha(source)},
                     "oracle": {"relation": "fold([a,b,a]) == fold([a,b])", "basis": "explicit identical-retry task requirement",
                                "independent": False},
                     "selection": {"capability": ["workforce", "run_tests"],
                                   "reason": "Execute the constructed replay relation against the actual Python artifact.",
                                   "qualification": "Required at workforce admission; this construction grants no authority."}}
            pack = {"policy_version": "event-replay-v1", "task_sha256": sha(task),
                    "invocation": {"component": "reliability_pipeline", "function": "premortem.construct_replay_check",
                                   "kind": "local_library", "model_calls": 0,
                                   "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
                    "hypotheses": [{"failure_id": failure, "predicate_id": predicate, "severity": "material",
                                    "likelihood": "unmeasured",
                                    # audit 009 RP-009: with a confirmed relation the requirement is the relation's
                                    # constant text, not words of the task, and says so
                                    "triggering_task_facts": {"requirement": requirement.group(),
                                                             "requirement_source": (
                                                                 "confirmed_relation" if relation else
                                                                 "clarified_requirement" if clarified_requirement
                                                                 else "task_text"),
                                                             "example": check["example"]},
                                    "required_control_kind": "metamorphic_replay",
                                    "candidate_capabilities": [["workforce", "run_tests"]],
                                    "prediction": "Only the latest event ID is remembered, so an identical retry after another event changes state.",
                                    "falsifier": "The constructed replay relation holds for the bound example."}],
                    "checks": [check],
                    "limits": ["One caller-supplied example; not exhaustive or independently held out.",
                               "Assumes the derived distinct ID is valid and state fully represents the effect.",
                               "No concurrency, restart or external-effect guarantee.",
                               "Keep original positive checks: this relation alone accepts a no-op.",
                               TEMPLATE_LIMIT],
                    "constructor": constructor()}
            if adjacent:
                # COPY: The adjacent relation makes no intervening-event promise.
                code = (
                    "# G6 adjacent-replay.v1: task-derived, nonindependent.\n"
                    "from copy import deepcopy as _g6_copy\n"
                    f"from {stem} import {function} as _g6_target\n\n"
                    "def test_adjacent_event_replay():\n"
                    f"    initial = {state!r}\n"
                    f"    event = {event!r}\n"
                    "    first = _g6_target(_g6_copy(initial), _g6_copy(event))\n"
                    "    replayed = _g6_target(_g6_copy(first), _g6_copy(event))\n"
                    "    assert replayed == first, 'identical adjacent event replay changed state'\n"
                )
                predicate = f"{stem}.{function}.adjacent-event-effect.v1"
                check.update(predicate_id=predicate, failure_id="adjacent_event_replay", source=code,
                             source_sha256=sha(code), inputs={"initial": state, "event": event},
                             nodeid=f".g6_harness/test_replay_{sha(code)[:16]}.py::test_adjacent_event_replay")
                # COPY: Literal example derivation and bounded oracle scope.
                check["input_derivation"] = "Replay the same literal event immediately after its first application."
                check["oracle"]["relation"] = "fold([a,a]) == fold([a])"
                pack["policy_version"] = "adjacent-replay.v1"
                hypothesis = pack["hypotheses"][0]
                hypothesis.update(predicate_id=predicate, failure_id="adjacent_event_replay")
                # COPY: A falsifiable hypothesis, not a finding about the baseline.
                hypothesis["prediction"] = "An identical immediately repeated event changes state."
                # COPY: No derived ID is needed for the adjacent relation.
                pack["limits"][1] = "Assumes state fully represents the effect; no nonadjacent replay guarantee."
            return pack
    return None


def premortem_failure_mode_questions() -> dict[str, str]:
    """Map each premortem failure-mode code to its diagnostic question."""
    return {code: question for code, question, _g in _CHECKLIST}


__all__ = [
    "CONSTRUCTOR",
    "PREMORTEM_FAILURE_MODES",
    "TEMPLATE_LIMIT",
    "construct_replay_check",
    "constructor",
    "premortem_failure_mode_questions",
]
