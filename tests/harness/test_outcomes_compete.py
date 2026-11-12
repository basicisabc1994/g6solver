# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Outcomes held, compared and shown in one encoding, and outcomes that compete (question slice, repair 1; review 1's
findings 1, 6 and 12, one root: outcomes that do not truly compete).

- One encoding (``behaviour_question.encoded``, read back by ``decoded``): the probe's own, example.v1's for a result,
  type-distinguishing. An inventory read from the code lists every place the slice compares, encodes, decodes or shows
  an outcome, and fails on one it cannot classify.
- An observation G6 cannot state exactly (a list or an object the probe writes, at any depth) is never offered.
- A candidate is offered only when each outcome's compiled check rejects what the other outcome shows, judged by
  example.v1's own compiled text; identical, overlapping and independent outcomes are refused, each with its code.

Development fixtures only: example checks import on-disk witnesses in fresh children; differential helpers use stand-ins.
"""
from __future__ import annotations

import ast
import builtins
import hashlib
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import canonical
from g6.harness import behaviour_question as bq
from g6.harness import verification_search as vs
from g6.harness import verification_templates as vt
from g6.harness.confirmed_expectation import confirm
from g6.harness.verification_templates import TemplateInstance, instance_digest

ROOT = Path(__file__).resolve().parents[2]
TASK = "Repair addition for positive integers."
CALL = {"args": [2, 3], "kwargs": {}}
#: The codes of two outcomes that do not compete (``behaviour_question``'s, named here so each test reads them).
IDENTICAL, OVERLAP, INDEPENDENT = ("G6_E_QUESTION_OUTCOMES_IDENTICAL", "G6_E_QUESTION_OUTCOMES_OVERLAP",
                                   "G6_E_QUESTION_OUTCOMES_INDEPENDENT")


def candidate(**extra) -> dict:
    return {"requirement_id": "R1", "function": "calc.add", "args": [2, 3],
            "outcomes": {"A": {"returns": 5}, "B": {"returns": -1}}, "cited": TASK, "evidence": ["calc.py: add"],
            **extra}


def namespace(tmp_path: Path, instance: TemplateInstance, program="def add(*args, **kwargs):\n    return None\n") -> dict:
    """The real fixed text of the instance's template, compiled for a stand-in ``calc.py`` (trusted fixture code)."""
    (tmp_path / 'calc.py').write_text(program, encoding='utf-8')
    tree = vt.Tree(tmp_path, SimpleNamespace(base_commit="fixture"), frozenset({"calc.py"}), frozenset(),
                   _texts={("calc.py", vt.PATCH): program})
    definition = vt.instance_definition(instance)
    applied = definition.applies(tree, definition.bindings.model_validate(instance.bindings), vt.PATCH)
    found: dict = {}
    exec(compile(vt.compile_text(definition, applied.data), "<G6 fixed template>", "exec"), found)  # noqa: S102
    return found


def _differential() -> TemplateInstance:
    return TemplateInstance(id="D1", template="differential.v1", requirement_id="R1", intent="change",
                            bindings={"function": "calc.add", "mode": "outputs", "calls": [{"args": [2, 3]}]})


def _differences(tmp_path: Path, values) -> list[dict]:
    """What the real differential.v1 probe writes for ``values`` returned at the base commit and on the patch, as
    verification search compares them."""
    one = _differential()
    probe = namespace(tmp_path, one)
    observations = []
    for value in values:
        probe["_load"] = lambda spec, value=value: (lambda *args: value)
        observations.append({"input": CALL, "output": probe["_outcome"](CALL)})
    return vs._differences([observations[0]], [observations[1]])


def _activity(differences: list[dict]) -> dict:
    one = _differential()
    return {"activity": "template instance", "check": one.id, "template": "differential.v1",
            "result": "behaviour_changed", "instance_sha256": instance_digest(one), "differences": differences}


# --- review 1's findings, brought in as this change's own ---------------------------------------------------------


@pytest.mark.parametrize("kind, code", [("exception_subclass", OVERLAP), ("identical", IDENTICAL),
                                        ("independent_facets", INDEPENDENT)])
def test_finding_1_competing_outcomes_never_both_accept_one_implementation(tmp_path, kind, code):
    outcomes = {"exception_subclass": {"A": {"raises": "Exception"}, "B": {"raises": "ValueError"}},
                "identical": {"A": {"returns": 5}, "B": {"returns": 5}},
                "independent_facets": {"A": {"returns": 5}, "B": {"effect": {"argument": 0, "equals": 2}}}}[kind]
    try:
        [one] = bq.caller_candidates(TASK, [candidate(outcomes=outcomes)])
    except bq.CandidateRefused as exc:
        assert exc.code == code
        return
    question = one.question("b" * 64)
    passing = []
    for option in ("A", "B"):
        program = ('def add(*args):\n    raise ValueError("the B outcome")\n' if kind == 'exception_subclass'
                   else 'def add(*args):\n    return 5\n')
        check = namespace(tmp_path, confirm(question, option, answered_by="person:Ada").instance, program)
        try:
            with pytest.MonkeyPatch.context() as local:
                local.chdir(tmp_path)
                check["test_example"]()
        except pytest.fail.Exception:
            continue
        passing.append(option)
    assert len(passing) == 1, "both competing outcomes' checks accept the same implementation"


@pytest.mark.parametrize("kind", ["set", "frozenset", "nonstring_mapping", "nested_set",
                                  # members the old pattern missed: what the probe writes as JSON may be another value
                                  "list", "tuple", "string_mapping", "mapping_proxy", "nested_list"])
def test_finding_6_a_lossy_structural_form_is_never_an_observed_output(tmp_path, kind):
    values = {"set": ({1}, {2}), "frozenset": (frozenset([1]), frozenset([2])),
              "nonstring_mapping": ({1: "a"}, {2: "b"}), "nested_set": ({"items": {1}}, {"items": {2}}),
              "list": ([1], [2]), "tuple": ((1,), (2,)), "string_mapping": ({"a": 1}, {"a": 2}),
              "mapping_proxy": (types.MappingProxyType({"a": 1}), types.MappingProxyType({"a": 2})),
              "nested_list": (5, [[5]])}[kind]
    differences = _differences(tmp_path, values)
    assert differences
    assert bq.differential_candidates(TASK, [_differential()], [_activity(differences)]) == [], (
        "a lossy structural rendering was offered as an output G6 observed verbatim")


@pytest.mark.parametrize("values", [(1, True), (1, 1.0), (-0.0, 0.0), (5, "5"), (None, False)],
                         ids=["bool-int", "int-float", "signed-zero", "int-text", "none-false"])
def test_finding_12_a_difference_the_probe_and_example_distinguish_is_kept(tmp_path, values):
    differences = _differences(tmp_path, values)
    assert len(differences) == 1
    found = bq.differential_candidates(TASK, [_differential()], [_activity(differences)])
    assert len(found) == 1, "a real JSON-distinct behavioural difference was erased by Python equality"
    assert [found[0].to_dict()["outcomes"][label]["returns"] for label in bq.LABELS] == list(values)
    assert [type(found[0].to_dict()["outcomes"][label]["returns"]) for label in bq.LABELS] == [type(v) for v in values]


# --- competition, judged by example.v1's own compiled text --------------------------------------------------------

#: Each pair of outcomes, the code ``competition`` gives ("" when they compete), the call's arguments, and for a pair
#: that does not compete a stand-in that meets both: what ``witness`` builds.
PAIRS = [
    ({"returns": 5}, {"returns": -1}, ""),
    ({"returns": 1}, {"returns": True}, ""),
    ({"returns": 1}, {"returns": 1.0}, ""),
    ({"returns": -0.0}, {"returns": 0.0}, ""),
    ({"returns": "5"}, {"returns": 5}, ""),
    ({"returns": 5}, {"returns": 5}, IDENTICAL),
    ({"raises": "ValueError"}, {"raises": "TypeError"}, OVERLAP),
    ({"raises": "Exception"}, {"raises": "ValueError"}, OVERLAP),
    ({"raises": "KeyError"}, {"raises": "LookupError"}, OVERLAP),
    ({"raises": "ValueError"}, {"raises": "ValueError"}, IDENTICAL),
    ({"returns": 5}, {"raises": "ValueError"}, ""),
    ({"effect": {"argument": 0, "equals": [1]}}, {"effect": {"argument": 0, "equals": [2]}}, ""),
    ({"effect": {"argument": 0, "equals": [1]}}, {"effect": {"argument": 0, "equals": [1]}}, IDENTICAL),
    ({"effect": {"argument": 0, "equals": [1]}}, {"raises": "KeyError"}, ""),
    ({"returns": 5}, {"effect": {"argument": 0, "equals": [1]}}, INDEPENDENT),
    ({"effect": {"argument": 0, "equals": [1]}}, {"effect": {"argument": 1, "equals": [2]}}, INDEPENDENT),
]


def _stand_in(*outcomes: dict):
    """Source that shows every outcome given at once, where it can: their returned value, each argument's value
    after the call, and the narrowest error named (raised, so nothing else is shown)."""
    errors = [getattr(builtins, one["raises"]) for one in outcomes if "raises" in one]
    witness_error = max(errors, key=lambda kind: len(kind.__mro__)) if errors else None
    if len(errors) == 2 and not issubclass(errors[0], errors[1]) and not issubclass(errors[1], errors[0]):
        witness_error = type("Both", tuple(errors), {})

    lines = []
    if witness_error and witness_error.__name__ == 'Both':
        lines.append('class Both(' + ', '.join(error.__name__ for error in errors) + '):\n    pass')
    lines.append('def add(*args, **kwargs):')
    if errors:
        lines.append('    raise ' + witness_error.__name__ + '()')
    for one in outcomes:
        if 'effect' in one:
            lines.append(f'    args[{one["effect"]["argument"]}][:] = {one["effect"]["equals"]!r}')
    found = [one['returns'] for one in outcomes if 'returns' in one]
    lines.append('    return ' + repr(found[0] if found else None))
    return '\n'.join(lines) + '\n'


def _accepts(tmp_path: Path, outcome: dict, function) -> bool:
    question = {"A": outcome, "B": {"raises": "ZeroDivisionError"}} if "raises" not in outcome or \
        outcome["raises"] != "ZeroDivisionError" else {"A": outcome, "B": {"returns": 0}}
    from g6.harness.confirmed_expectation import Question

    shown = Question(requirement_id="R1", function="calc.add", args=[[0], [0]], outcomes=question,
                     scope=bq.CASE_SCOPE, cited=TASK, baseline_sha256="b" * 64)
    check = namespace(tmp_path, confirm(shown, "A", answered_by="person:Ada").instance, function)
    try:
        with pytest.MonkeyPatch.context() as local:
            local.chdir(tmp_path)
            check["test_example"]()
    except pytest.fail.Exception:
        return False
    return True


@pytest.mark.parametrize("a, b, code", PAIRS)
def test_competition_is_the_compiled_checks_own_judgement(tmp_path, a, b, code):
    assert bq.competition({"A": a, "B": b})[0] == code
    if code:   # one implementation meets both outcomes' compiled checks
        witness = _stand_in(a, b)
        assert _accepts(tmp_path, a, witness) and _accepts(tmp_path, b, witness)
    else:   # each outcome's check accepts what it states, and rejects what the other states
        for mine, other in ((a, b), (b, a)):
            assert _accepts(tmp_path, mine, _stand_in(mine))
            assert not _accepts(tmp_path, mine, _stand_in(other))


@pytest.mark.parametrize("a, b", [("calc.Oops", "ValueError"), ("calc.Oops", "calc.Other")])
def test_two_errors_g6_cannot_tell_apart_by_kind_are_refused(a, b):
    with pytest.raises(bq.CandidateRefused) as raised:
        bq.caller_candidates(TASK, [candidate(outcomes={"A": {"raises": a}, "B": {"raises": b}})])
    assert raised.value.code == OVERLAP and "cannot tell" in str(raised.value)


def test_a_refused_differential_pair_gives_no_candidate_and_a_caller_s_is_refused_by_code():
    differences = [{"input": vs._canonical(CALL), "base": vs._canonical({"raised": "Exception"}),
                    "patch": vs._canonical({"raised": "ValueError"})}]
    assert bq.differential_candidates(TASK, [_differential()], [_activity(differences)]) == []
    with pytest.raises(bq.CandidateRefused) as raised:
        bq.Candidate.make(source="caller", requirement_id="R1", function="calc.add", args=[2, 3], kwargs={},
                          outcomes={"A": {"returns": 5}, "B": {"returns": 5}}, cited=TASK, evidence=["calc.py"])
    assert raised.value.code == IDENTICAL


# --- the one encoding ------------------------------------------------------------------------------------------------

VALUES = [1, True, 1.0, -0.0, 0.0, "5", 5, None, False, [1, 2], {"b": 1, "a": [True]}, "a  b", "a b", 10 ** 20, 1e-7]


def test_the_one_encoding_is_the_probe_s_and_example_v1_s_and_tells_types_apart(tmp_path):
    compiled = namespace(tmp_path, confirm(bq.caller_candidates(TASK, [candidate()])[0].question("b" * 64), "A",
                                           answered_by="person:Ada").instance)
    for value in VALUES:
        # Read the real child's encoder through the same host comparison used by the portable check.
        (tmp_path / 'calc.py').write_text(f'def add(*args):\n    return {value!r}\n', encoding='utf-8')
        compiled['BINDINGS']['cases'][0]['value'] = value
        observed = []
        finish = compiled['finish']
        def capture(code, raw, data):
            reading = finish(code, raw, data)
            observed.append(reading['observed']['outcomes'][0]['returned'])
            return reading
        compiled['finish'] = capture
        with pytest.MonkeyPatch.context() as local:
            local.chdir(tmp_path)
            compiled['test_example']()
        compiled['finish'] = finish
        assert bq.encoded(value) == vs._canonical(value), value
        text = bq.encoded(value)
        raw = text.encode('utf-8')
        assert observed == [{'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw), 'prefix': text[:200]}], value
        assert observed[0]['prefix'] == text, value
        assert bq.decoded(bq.encoded(value)) == value and bq.encoded(bq.decoded(bq.encoded(value))) == bq.encoded(value)
    texts = {bq.encoded(value) for value in VALUES}
    assert len(texts) == len(VALUES)   # 1, true, 1.0, -0.0, 0.0, "5" and 5 all differ
    # a record's canonical bytes (slice 2's Question, the store's ledger) are equal exactly when the encoding is
    for one in VALUES:
        for other in VALUES:
            assert (canonical(one) == canonical(other)) == (bq.encoded(one) == bq.encoded(other))


def test_the_form_and_the_report_show_a_call_and_an_outcome_in_the_one_encoding():
    shown = {"function": "calc.add", "args": [2, "2"], "kwargs": {"b": 3, "a": True}}
    assert bq.shown_call(shown) == 'calc.add(2, "2", a=true, b=3)'
    assert bq.shown_outcome({"returns": "5"}) == 'returns "5"' and bq.shown_outcome({"returns": 5}) == "returns 5"
    assert bq.shown_outcome({"effect": {"argument": 0, "equals": [1.0]}}) == "leaves argument 0 as [1.0]"
    assert bq.shown_outcome({"raises": "ValueError"}) == "raises ValueError"
    assert bq.shown_outcome("not an outcome") == bq.shown_outcome({"returns": 1, "raises": "E"}) \
        == "an outcome G6 cannot read"


SLICE = ("harness/behaviour_question.py", "harness/question_store.py", "mcp/behaviour.py", "harness/patch_report.py",
         "harness/confirmed_expectation.py")
#: The keys and attributes by which code reads an outcome or what the probe observed.
OUTCOME_KEYS = frozenset({"returns", "raises", "effect", "equals", "outcomes", "returned", "raised", "base", "patch"})
ENCODINGS = frozenset({"dumps", "loads", "canonical", "_canonical", "digest", "repr", "ascii", "encoded", "decoded",
                       "shown_call", "shown_outcome", "_value", "_said", "_call", "competition", "_question_sha256",
                       "_meaning", "_scenario", "latest_confirmed"})


def _reads_outcome(node: ast.AST) -> bool:
    """Whether ``node`` reads an outcome: a key or attribute that holds one, or a record's dictionary, which holds
    its outcomes (``to_dict``)."""
    def key(one: ast.AST) -> bool:
        return isinstance(one, ast.Constant) and one.value in OUTCOME_KEYS

    return any((isinstance(n, ast.Subscript) and key(n.slice))
               or (isinstance(n, ast.Attribute) and n.attr == "outcomes")
               or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and (
                   n.func.attr == "to_dict" or (n.func.attr == "get" and n.args and key(n.args[0]))))
               for n in ast.walk(node))


def _constant(node: ast.AST) -> bool:
    """A literal, a container of literals, or a module's constant (a name in capitals): a kind of entry or a status."""
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return all(_constant(one) for one in node.elts)
    return isinstance(node, ast.Constant) or (isinstance(node, ast.Name) and node.id.isupper())


def places() -> set[tuple[str, str, str]]:
    """(file, function, expression) for every place in the slice's modules that encodes, decodes, compares or shows an
    outcome: in each function that reads one, every call of an encoding (json's, the ledger's, the probe's, repr, the
    slice's own), every == or != with no constant side, and every value an f-string writes that reads an outcome."""
    found = set()
    for rel in SLICE:
        tree = ast.parse((ROOT / "src" / "g6" / rel).read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not (_reads_outcome(function) or function.name in ENCODINGS or any(
                    isinstance(n, ast.Call) and getattr(n.func, "id", "") in ENCODINGS for n in ast.walk(function))):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    if name in ENCODINGS:
                        found.add((rel, function.name, ast.unparse(node)[:90]))
                elif isinstance(node, ast.Compare) and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
                    if not any(_constant(side) for side in (node.left, *node.comparators)):
                        found.add((rel, function.name, ast.unparse(node)[:90]))
                elif isinstance(node, ast.FormattedValue) and _reads_outcome(node.value):
                    found.add((rel, function.name, "f-string: " + ast.unparse(node.value)[:80]))
    return found


THROUGH = "through the one encoding (encoded, decoded) or the form's and report's rendering of it"
NAME = "an exception type's name or an argument's index: one type each (text, a whole number), so == is exact"
RECORD = ("a record's identity by its digest or its canonical bytes (slice 2's records, the store's ledger), equal "
          "exactly when the one encoding is (test_the_one_encoding_is_the_probe_s_and_example_v1_s...)")
PLACES: dict[tuple[str, str, str], str] = {
    ("harness/behaviour_question.py", "encoded", "vs._canonical(value)"): "the one encoding: the probe's own",
    ("harness/behaviour_question.py", "decoded", "json.loads(text)"): "its one reader",
    ("harness/behaviour_question.py", "competition", "encoded(a['returns']) == encoded(b['returns'])"): THROUGH,
    ("harness/behaviour_question.py", "competition", "encoded(a['returns'])"): THROUGH,
    ("harness/behaviour_question.py", "competition", "encoded(b['returns'])"): THROUGH,
    ("harness/behaviour_question.py", "competition", "f-string: encoded(a['returns'])[:80]"): THROUGH,
    ("harness/behaviour_question.py", "competition", "a['raises'] == b['raises']"): NAME,
    ("harness/behaviour_question.py", "competition", "f-string: a['raises']"): NAME,
    ("harness/behaviour_question.py", "competition", "f-string: b['raises']"): NAME,
    ("harness/behaviour_question.py", "competition", "one['argument'] != other['argument']"): NAME,
    ("harness/behaviour_question.py", "competition", "encoded(one['equals']) == encoded(other['equals'])"): THROUGH,
    ("harness/behaviour_question.py", "competition", "encoded(one['equals'])"): THROUGH,
    ("harness/behaviour_question.py", "competition", "encoded(other['equals'])"): THROUGH,
    ("harness/behaviour_question.py", "competition", "f-string: encoded(one['equals'])[:80]"): THROUGH,
    ("harness/behaviour_question.py", "_structural", "encoded(record)"): THROUGH,
    ("harness/behaviour_question.py", "make", "competition(made.to_dict()['outcomes'])"): THROUGH,
    ("harness/behaviour_question.py", "from_record", "decoded(data)"): THROUGH,
    ("harness/behaviour_question.py", "from_record", "made.data != data"): RECORD,
    ("harness/behaviour_question.py", "with_support", "made.data == candidate.data"): RECORD,
    ("harness/behaviour_question.py", "from_record", "competition(outcomes)"): THROUGH,
    ("harness/behaviour_question.py", "form", "competition(question.outcomes)"): THROUGH,
    ("harness/behaviour_question.py", "_structural", "sorted(record['outcomes']) != list(LABELS)"): (
        "the outcomes' labels (text), not their values"),
    ("harness/behaviour_question.py", "settled_by", "encoded(value)"): THROUGH,
    ("harness/behaviour_question.py", "settled_by", "f-string: outcome['raises'].rsplit('.', 1)[-1]"): NAME,
    ("harness/behaviour_question.py", "differential_candidates", "decoded(difference['input'])"): THROUGH,
    ("harness/behaviour_question.py", "differential_candidates", "decoded(difference['base'])"): THROUGH,
    ("harness/behaviour_question.py", "differential_candidates", "decoded(difference['patch'])"): THROUGH,
    ("harness/behaviour_question.py", "differential_candidates", "f-string: difference['base']"): (
        "the probe's own text, as verification search wrote it with the one encoding"),
    ("harness/behaviour_question.py", "differential_candidates", "f-string: difference['patch']"): (
        "the probe's own text, as verification search wrote it with the one encoding"),
    ("harness/behaviour_question.py", "differential_candidates",
     "activity.get('instance_sha256') != instance_digest(one)"): RECORD,
    ("harness/behaviour_question.py", "shown_outcome", "f-string: _value(outcome['returns'])"): THROUGH,
    ("harness/behaviour_question.py", "shown_outcome", "_value(outcome['returns'])"): THROUGH,
    ("harness/behaviour_question.py", "shown_outcome", "_value(name)"): THROUGH,
    ("harness/behaviour_question.py", "shown_outcome", "_value(effect.get('argument'))"): THROUGH,
    ("harness/behaviour_question.py", "shown_outcome", "_value(effect.get('equals'))"): THROUGH,
    ("harness/behaviour_question.py", "shown_outcome", "f-string: _value(effect.get('equals'))"): THROUGH,
    ("harness/behaviour_question.py", "shown_call", "_value(one)"): THROUGH,
    ("harness/behaviour_question.py", "shown_call", "_value(value)"): THROUGH,
    ("harness/behaviour_question.py", "_call", "shown_call(question.to_dict())"): THROUGH,
    ("harness/behaviour_question.py", "_said", "shown_outcome(outcome)"): THROUGH,
    ("harness/patch_report.py", "_question_sha256", "digest(question)"): RECORD,
    ("harness/behaviour_question.py", "_value", "encoded(value)"): THROUGH,
    ("harness/behaviour_question.py", "form", "_said(shown['outcomes']['A'])"): THROUGH,
    ("harness/behaviour_question.py", "form", "_said(shown['outcomes']['B'])"): THROUGH,
    ("harness/behaviour_question.py", "form", "f-string: caller_text(_said(shown['outcomes']['A']), literal=False)"): THROUGH,
    ("harness/behaviour_question.py", "form", "f-string: caller_text(_said(shown['outcomes']['B']), literal=False)"): THROUGH,
    ("harness/behaviour_question.py", "form", "shown_call(shown)"): THROUGH,
    ("harness/behaviour_question.py", "form", "_call(question)"): THROUGH,
    ("harness/patch_report.py", "_confirmed", "bq.shown_call(question)"): THROUGH,
    ("harness/patch_report.py", "_confirmed", "bq.shown_outcome(outcomes.get(option))"): THROUGH,
    ("harness/patch_report.py", "_confirmed", "_question_sha256(question)"): RECORD,
    ("harness/question_store.py", "_kind_rules", "asked.sha256 != ident"): RECORD,
    ("harness/question_store.py", "_kind_rules", "answer.sha256 != ident"): RECORD,
    ("harness/question_store.py", "_kind_rules",
     "encoded({k: v for k, v in asked.to_dict().items() if k != 'scope'})"): THROUGH,
    ("harness/question_store.py", "_kind_rules",
     "encoded({k: v for k, v in rule.to_dict().items() if k != 'scope'})"): THROUGH,
    ("harness/question_store.py", "_kind_rules",
     "encoded({k: v for k, v in rule.to_dict().items() if k != 'scope'}) != same"): THROUGH,
    ("harness/question_store.py", "_kind_rules", "Candidate.from_record(data).data != data"): (
        "a candidate's text in the one encoding, read back and made again: compared as that text"),
    ("harness/question_store.py", "ask", "competition(question.outcomes)"): THROUGH,
    ("harness/question_store.py", "ask", "one.question.sha256 == question.sha256"): RECORD,
    ("mcp/behaviour.py", "_reply", "one.answer_sha256 == reading.answer.sha256"): RECORD,
    ("mcp/behaviour.py", "confirm", "digest(repo_tests.snapshot(root))"): "the artefact's digest, not an outcome",
    ("mcp/behaviour.py", "_review_candidates", "digest(a)"): RECORD,
    ("mcp/behaviour.py", "_review_candidates", "owned.get(a.get('check')) == digest(a)"): RECORD,
    ("mcp/behaviour.py", "_review_candidates", "json.loads(saved_bytes)"): (
        "one decode of the exact saved bytes whose task and probe custody are checked"),
    ("mcp/behaviour.py", "_review_candidates", "saved.get('artifact_digest') != artifact"): RECORD,
    ("harness/behaviour_question.py", "_get", "decoded(self.data)"): THROUGH,
    ("harness/behaviour_question.py", "to_dict", "decoded(self.data)"): THROUGH,
    ("harness/question_store.py", "allow_more", "digest(['more', asked.question.sha256, len(ledger.entries())])"): (
        "an entry's id, not an outcome"),
    ("harness/question_store.py", "show", "digest(['shown', asked.question.sha256, len(ledger.entries())])"): (
        "an entry's id, not an outcome"),
    ("harness/question_store.py", "task_identity", "digest({'repository': str(Path(root).resolve()), 'task': task})"): (
        "a task's identity, not an outcome"),
}
#: Slice 2's records (``confirmed_expectation``), unchanged here: each held and compared as its canonical bytes
#: (``core.ledger.canonical``), equal exactly when the one encoding is (the test above); slice 2's own inventory
#: (``tests/harness/test_confirmed_records_inventory.py``) holds every such comparison to bytes.
SLICE_2 = ("slice 2's record, held and compared as its canonical bytes, whose equality is the one encoding's")
PLACES.update({("harness/confirmed_expectation.py", function, expression): SLICE_2 for function, expression in (
    ("__init__", "_canonical(record)"), ("__init__", "chosen.question_sha256 != shown.sha256"),
    ("__init__", "json.loads(data)"), ("__post_init__", "_meaning(one)"), ("__post_init__", "_scenario(one)"),
    ("__post_init__", "before != meaning"), ("_canonical", "canonical(value)"),
    ("_meaning", "_canonical(one.instance.bindings)"),
    ("_options_sha256", "digest({'outcomes': shown['outcomes'], 'options': shown['options']})"),
    ("_parsed", "_canonical(record)"), ("_parsed", "again != data"), ("_parsed", "json.loads(data)"),
    ("_scenario", "_canonical({'function': shown['function'], 'args': shown['args'], 'kwargs': shown['kwargs'"),
    ("_scenario", "json.loads(one._question)"), ("contradicted_by", "_meaning(new)"),
    ("contradicted_by", "_meaning(one)"), ("contradicted_by", "_meaning(one) != meaning"),
    ("contradicted_by", "_scenario(new)"), ("contradicted_by", "_scenario(one)"),
    ("contradicted_by", "_scenario(one) == scenario"), ("from_record", "_canonical(pair['answer'])"),
    ("from_record", "_canonical(pair['question'])"),
    ("record", "_canonical({'question': json.loads(self._question), 'answer': json.loads(self._answer)})"),
    ("record", "json.loads(self._answer)"), ("record", "json.loads(self._question)"),
    ("sha256", "digest({'answers': [one.answer_sha256 for one in self.expectations], 'revision': self.revi"),
    ("latest_confirmed", "_scenario(one)"),
    ("with_expectation", "latest_confirmed((*self.expectations, new))"))})
PLACES.update({
    ("harness/confirmed_expectation.py", "with_expectation", "len(held) == len(self.expectations)"): (
        "counts of held answers, not outcome values"),
    ("harness/confirmed_expectation.py", "with_expectation", "one.answer_sha256 == old.answer_sha256"): RECORD,
    ("harness/confirmed_expectation.py", "__init__", "kept != answered_at"): "a time, as text: not an outcome",
    ("harness/confirmed_expectation.py", "_parsed", "set(record) != set(fields)"): "field names: not an outcome"})


def test_every_place_the_slice_handles_an_outcome_goes_through_the_one_encoding():
    found = places()
    assert sorted(found - set(PLACES)) == [], "unclassified: route it through encoded/decoded, or classify it"
    assert sorted(set(PLACES) - found) == [], "listed places the scan no longer finds: remove them"
    assert all(why.strip() for why in PLACES.values())


def test_the_scan_finds_a_python_equality_on_outcomes():
    # survival control: finding 12's comparison is found, and is not allowed
    planted = ast.parse("def differential_candidates(base, patch):\n    return base['returned'] == patch['returned']\n")
    found = {(f.name, ast.unparse(n)) for f in ast.walk(planted) if isinstance(f, ast.FunctionDef)
             for n in ast.walk(f) if isinstance(n, ast.Compare)}
    assert found == {("differential_candidates", "base['returned'] == patch['returned']")}
    assert not any(where[2] == "base['returned'] == patch['returned']" for where in PLACES)
