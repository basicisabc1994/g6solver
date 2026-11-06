# SPDX-License-Identifier: Apache-2.0
"""What counts as a G6 code is decided once, repair 3 of the consent change: review cons3's one finding, brought in as a
test of this file and failing on a0a4d0d, and the inventory that removes its class at the root, built from the code:

- one_fact_two_rules (finding 1): G6's own rule for a code is its envelope checker's, ``g6.invariants.ERROR_CODE_PATTERN``
  matched as ``check_envelope`` matches it. The one reader of a person's act recognises a reply only when its card's code
  is a G6 code by that rule, so a reply with any other code is ``G6_E_LOOP_ACT_FAILED`` naming the code. Every other
  place in ``g6/mcp``, ``g6/metaharness`` and ``g6/self_maintenance`` that decides whether a string is a G6 code imports
  the same rule, and the inventory, read from the code, fails on a place that does not.

No model, no runner process, no network: the act's child is a fake, and everything runs in this process.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from g6.core.llm import LLMError
from g6.invariants import ERROR_CODE_PATTERN, check_envelope
from g6.mcp import improve, mission
from g6.metaharness import adapt, reflex, runner
from g6.metaharness.api import _blocked, _envelope
from g6.self_maintenance import usage

SRC = Path(improve.__file__).resolve().parents[1]
#: The packages whose every decision of whether a string is a G6 code the inventory reads.
PACKAGES = ("mcp", "metaharness", "self_maintenance")
_rig_spec = importlib.util.spec_from_file_location("metaharness_rig",
                                                   Path(__file__).resolve().parents[1] / "metaharness" / "_rig.py")
rig = sys.modules.setdefault(_rig_spec.name, importlib.util.module_from_spec(_rig_spec))
if not hasattr(rig, "build"):
    _rig_spec.loader.exec_module(rig)


def code(reply: dict) -> str:
    return reply["warning_card"]["code"]


def checker_accepts(value) -> bool:
    """What G6's own envelope checker says of ``value`` as a card's code: whether it finds no ``unstable_code``."""
    probe = {"completion_state": "blocked-escalated", "warning_card": {"code": value}, "request_id": "", "run_id": ""}
    return not any(v.rule == "unstable_code" for v in check_envelope(probe))


class _Child:
    """A child that exited ``returncode`` having written ``out``: no process is started."""

    def __init__(self, returncode, out: str) -> None:
        self.returncode, self.out = returncode, out

    def communicate(self, request, timeout):
        return self.out, ""


def _refusal(said: str) -> dict:
    """A child's refusal as ``g6.metaharness.api._blocked`` writes it, with ``said`` as its code, after JSON."""
    reply = json.loads(json.dumps(_blocked("G6_E_LOOP_TEST_REFUSED", "The child refused.")))
    reply["code"] = reply["warning_card"]["code"] = said
    return reply


def _read(status, reply: dict) -> dict:
    try:
        return improve._read_act(status, json.dumps(reply) + "\n", "", act_name="enrol")
    except Exception as exc:  # noqa: BLE001 - the reader must never raise
        pytest.fail(f"the reader raised {type(exc).__name__}: {exc}")


# Finding 1, one_fact_two_rules: the one reader takes a code only by G6's own rule ---------------------------------------

@pytest.mark.parametrize("bad_code", ["G6_E_", "G6_E_lowercase", "G6_E_LOOP REFUSED"])
def test_a_child_refusal_whose_code_is_not_a_g6_code_is_an_act_failure(tmp_path, monkeypatch, bad_code):
    """Finding 1 (review cons3), as review cons3 wrote it. Failed on a0a4d0d: AssertionError: malformed refusal code
    'G6_E_' was passed through instead of G6_E_LOOP_ACT_FAILED (and the same for the other two)."""
    reply = _refusal(bad_code)
    assert any(v.rule == "unstable_code" for v in check_envelope(reply))          # G6's own checker rejects it
    monkeypatch.setattr(improve, "_interpreter", lambda settings: ["fake-python"])
    monkeypatch.setattr(improve.subprocess, "Popen", lambda *a, **k: _Child(1, json.dumps(reply) + "\n"))
    got = improve._run_act(tmp_path, {}, {"act": "enrol"})
    assert code(got) == improve.ACT_FAILED, (
        f"malformed refusal code {bad_code!r} was passed through instead of {improve.ACT_FAILED}")
    assert f"warning_card.code {bad_code!r} is not a G6 code" in got["message"], got["message"]


#: Codes a child's reply could carry: each case the brief names, and others at the edges of the rule.
PROBES = [
    ("the prefix alone", "G6_E_"), ("lower case", "G6_E_lowercase"), ("a space", "G6_E_LOOP REFUSED"),
    ("empty", ""), ("a trailing newline", "G6_E_LOOP_REFUSED\n"), ("registered", improve.TERMS_CHANGED),
    ("well formed and registered nowhere", "G6_E_NOBODY_REGISTERED_THIS"), ("a leading space", " G6_E_LOOP_REFUSED"),
    ("a trailing space", "G6_E_LOOP_REFUSED "), ("two trailing newlines", "G6_E_LOOP_REFUSED\n\n"),
    ("a carriage return", "G6_E_LOOP_REFUSED\r"), ("a hyphen", "G6_E_LOOP-REFUSED"), ("lower-case prefix", "g6_e_loop_refused"),
    ("an accented capital", "G6_E_LOOP_REFUSÉ"), ("Arabic-Indic digits", "G6_E_١٢"), ("a NUL", "G6_E_LOOP\x00"),
    ("no prefix", "E_LOOP_REFUSED"), ("the prefix twice", "G6_E_G6_E_"), ("a long one", "G6_E_" + "X" * 5000),
]


@pytest.mark.parametrize("what, said", PROBES, ids=[what for what, _ in PROBES])
def test_the_reader_takes_a_refusal_code_exactly_as_g6s_envelope_checker_does(what, said):
    reply = _refusal(said)
    got = _read(1, reply)
    if checker_accepts(said):
        assert got == reply, f"{what}: G6's checker takes {said[:80]!r} as a code, so the refusal is passed on as it is"
    else:
        assert code(got) == improve.ACT_FAILED and got["completion_state"] == "blocked-escalated", (
            f"{what}: {said[:80]!r} is not a G6 code, so the refusal is not the act's")
        assert f"warning_card.code {said[:80]!r} is not a G6 code" in got["message"], got["message"]


@pytest.mark.parametrize("what, said", PROBES, ids=[what for what, _ in PROBES])
def test_a_success_status_with_a_code_that_is_not_the_acts_is_never_the_act_done(what, said):
    done = json.loads(json.dumps(_envelope("qualified-draft", "G6_E_LOOP_ENROLLED", "enrolled")))
    done["code"] = done["warning_card"]["code"] = said
    assert code(_read(0, done)) == improve.ACT_FAILED


@pytest.mark.parametrize("said, passed_on", [
    ("", False),                              # empty: not a code
    ("G6_E_LOOP_REFUSED\n", True),            # ruled: the checker's pattern ends in $, which allows one final newline
    (improve.TERMS_CHANGED, True),            # a code G6 defines
    ("G6_E_NOBODY_REGISTERED_THIS", True),    # ruled: G6 keeps no register of codes; its rule is a code's shape
])
def test_what_the_reader_gives_for_each_case_the_brief_names(said, passed_on):
    """What each gives, pinned. The reader follows G6's checker, so when the checker changes, the first assertion here
    fails and points at the ruling to revisit."""
    assert checker_accepts(said) is passed_on
    assert (code(_read(1, _refusal(said))) == said) is passed_on


def test_every_code_the_act_path_defines_is_a_g6_code_and_passes_as_a_refusal():
    defined = {value for module in (improve, runner) for name, value in vars(module).items()
               if name.isupper() and isinstance(value, str) and value[:4] == "G6_E"} | set(improve.ACT_DONE.values())
    assert len(defined) > 20
    assert sorted(said for said in defined if not checker_accepts(said)) == []
    assert sorted(said for said in defined if code(_read(1, _refusal(said))) != said) == []


# The class, one_fact_two_rules: every other place that decides whether a string is a G6 code ---------------------------

#: What mission, api and reflex each searched words with for a code on a0a4d0d, kept here as the behaviour to keep.
_BEFORE = re.compile(r"\b(G6_E_[A-Z0-9_]+)\b")
TEXTS = ["G6_E_LLM_TIMEOUT: no answer in time", "[G6_E_SENSITIVE_COMPONENT_DENIED] refused",
         "HTTP 500 from https://model: G6_E_MODEL_REFUSED: a server's body says this", "G6_E_: nothing after the prefix",
         "G6_E_lowercase", "G6_E_LOOP REFUSED", "xG6_E_GLUED on the left", "G6_E_GLUEDé on the right", "no code at all",
         "", "two: G6_E_FIRST then G6_E_SECOND", "G6_E_lower then G6_E_UPPER", "(G6_E_IN_BRACKETS)",
         "G6_E_BEFORE_A_NEWLINE\nthe next line", "G6_E_١ digits of another script", "G6_E_AT_THE_END.", "a-G6_E_HYPHENED-b",
         "G6_E_G6_E_TWICE", "G6_E_TRAILING_ ", "G6_E_X_é"]


def _named(text: str) -> str | None:
    """The first word of ``text`` that is a G6 code by G6's own checker."""
    return next((word for word in re.findall(r"\w+", text) if checker_accepts(word)), None)


def _raise(exc: Exception):
    raise exc


@pytest.mark.parametrize("text", TEXTS)
def test_a_code_named_in_words_is_read_by_g6s_rule_and_found_as_before(text):
    before = _BEFORE.search(text)
    assert _named(text) == (before[1] if before else None)
    assert reflex._reason({"reason": text}) == (_named(text) or reflex.CYCLE)
    assert code(mission._guarded(lambda: _raise(ValueError(text)))) == (_named(text) or mission.REFUSED)


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    workflow, _ = rig.build()
    made = adapt(workflow, workspace=tmp_path_factory.mktemp("repair3") / "ws", clock=rig.Clock())
    assert made.confirm("Ada")["completion_state"] == "qualified-draft", made.envelope
    return made


@pytest.mark.parametrize("text", TEXTS)
def test_a_model_failure_is_refused_with_the_code_its_words_name_by_g6s_rule(harness, text):
    class Fails:
        name = "fails"

        def propose(self, view, dev):
            raise LLMError(text)

    assert code(harness.propose(Fails())) == (_named(text) or "G6_E_META_MODEL_FAILED")


#: Each place in PACKAGES that decides whether a string is a G6 code, by module, innermost function, how and what:
#: every one imports G6's own rule from the envelope checker and matches it as the checker does.
CODE_RULES = {
    ("mcp/improve.py", "<module>", "import", "ERROR_CODE_PATTERN from g6.invariants"): "G6's own rule, imported",
    ("mcp/improve.py", "_recognised", "rule", "ERROR_CODE_PATTERN.match"): "a child's reply is the act's only with a G6 code",
    ("mcp/mission.py", "<module>", "import", "ERROR_CODE_PATTERN from g6.invariants"): "G6's own rule, imported",
    ("mcp/mission.py", "_guarded", "rule", "ERROR_CODE_PATTERN.match"): "the code a refusal's words name",
    ("metaharness/api.py", "<module>", "import", "ERROR_CODE_PATTERN from g6.invariants"): "G6's own rule, imported",
    ("metaharness/api.py", "_offer", "rule", "ERROR_CODE_PATTERN.match"): "the code a model failure's words name",
    ("metaharness/reflex.py", "<module>", "import", "ERROR_CODE_PATTERN from g6.invariants"): "G6's own rule, imported",
    ("metaharness/reflex.py", "_reason", "rule", "ERROR_CODE_PATTERN.match"): "the code a stopped cycle's reason names",
}
#: Places that test a string against a ``G6_E`` literal to decide something else, and what they decide.
OTHER_FACTS = {
    ("metaharness/recovery.py", "partition", "prefix test", ".startswith('G6_E_PIPELINE_')"):
        "which family a dev failure's code is in, not whether it is a code: the code is the one G6's own run set",
    ("self_maintenance/usage.py", "<module>", "literal", repr(usage._LIMIT.pattern)[:60]):
        "whether a failure is at a provider's limit, by two codes it names (G6_E_LLM_RATE_LIMITED, G6_E_LLM_HOST_FAILED) "
        "at the head of G6's own words: it decides which refusal it is, not what a code is",
}
_PATTERN_GOES_ON = set("[*+?{\\|")
_PREFIX_TESTS = {"startswith", "endswith", "removeprefix", "removesuffix", "find", "rfind", "index", "rindex", "partition",
                 "rpartition"}
_RULE = "ERROR_CODE_PATTERN"
_THE_CHECKER = ("g6.invariants", "g6.invariants.envelope")


def _states_a_shape(text: str) -> bool:
    """Whether a literal says what a code looks like: it holds a ``G6_E`` word that is not a whole code by G6's rule (a
    prefix, or ``G6_E_[A-Z0-9_]+``), or one a pattern goes on from (``G6_E_LOOP_*``, ``G6_E_X\\b``)."""
    return any(not checker_accepts(found.group()) or text[found.end():found.end() + 1] in _PATTERN_GOES_ON
               for found in re.finditer(r"G6_E\w*", text))


def _decisions_in(tree: ast.AST, rel: str) -> dict[tuple[str, str, str, str], int]:
    """Each place in ``tree`` that decides whether a string is a G6 code, with its line: a use of G6's rule (how it is
    used) and where it comes from; a literal that states a code's shape; a prefix or containment test against a
    ``G6_E`` literal; a name of its own for the rule."""
    found: dict[tuple[str, str, str, str], int] = {}
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.body
                  and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}

    def literal(node: ast.AST) -> str | None:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    def visit(node: ast.AST, parent: ast.AST | None, owner: str) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            owner = node.name
        where = getattr(node, "lineno", 0)
        text = literal(node)
        if text is not None and id(node) not in docstrings and _states_a_shape(text):
            found[(rel, owner, "literal", repr(text)[:60])] = where
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _PREFIX_TESTS:
            for argument in node.args:
                for item in (argument.elts if isinstance(argument, ast.Tuple) else [argument]):
                    said = literal(item) or ""
                    if "G6_E" in said or (len(said) >= 2 and "G6_E_".startswith(said)):     # G6_E_..., or G6_
                        found[(rel, owner, "prefix test", f".{node.func.attr}({literal(item)!r})")] = where
        left = literal(node.left) if isinstance(node, ast.Compare) else None
        if left is not None and "G6_E" in left and any(isinstance(op, ast.In | ast.NotIn) for op in node.ops) and (
                left.endswith("_") or not checker_accepts(left)):      # a prefix or a family, not one named code
            found[(rel, owner, "containment test", repr(left))] = where
        if isinstance(node, ast.ImportFrom) and any(alias.name in (_RULE, "check_envelope") for alias in node.names):
            for alias in node.names:
                if alias.name in (_RULE, "check_envelope"):
                    found[(rel, owner, "import", f"{alias.name} from {node.module}")] = where
        named = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None)
        if named == _RULE:
            if isinstance(node.ctx, ast.Store):
                found[(rel, owner, "a rule of its own", f"{_RULE} assigned")] = where
            elif isinstance(parent, ast.Attribute) and parent.value is node:
                found[(rel, owner, "rule", f"{_RULE}.{parent.attr}")] = where
            else:
                found[(rel, owner, "rule", f"{_RULE} as {type(parent).__name__}")] = where
        if named == "check_envelope" and isinstance(parent, ast.Call) and parent.func is node:
            found[(rel, owner, "checker", "check_envelope")] = where
        for child in ast.iter_child_nodes(node):
            visit(child, node, owner)

    visit(tree, None, "<module>")
    return found


def _decisions() -> dict[tuple[str, str, str, str], int]:
    found: dict[tuple[str, str, str, str], int] = {}
    for folder in PACKAGES:
        for path in sorted((SRC / folder).rglob("*.py")):
            found |= _decisions_in(ast.parse(path.read_text(encoding="utf-8")), path.relative_to(SRC).as_posix())
    return found


def test_every_place_that_decides_whether_a_string_is_a_g6_code_reads_it_by_g6s_own_rule():
    """The class's inventory. Failed on a0a4d0d: improve._read_act tested ``startswith('G6_E_')``, and mission, api
    and reflex each searched words with a pattern of their own; none imported G6's rule."""
    found = _decisions()
    unruled = {key: line for key, line in found.items() if key not in CODE_RULES and key not in OTHER_FACTS}
    assert unruled == {}, "places that decide whether a string is a G6 code by a rule of their own: " + "; ".join(
        f"{rel}:{line} {owner} {how} {what}" for (rel, owner, how, what), line in sorted(unruled.items()))
    assert set(CODE_RULES) <= set(found), f"listed and no longer there: {sorted(set(CODE_RULES) - set(found))}"
    assert set(OTHER_FACTS) <= set(found), f"ruled and no longer there: {sorted(set(OTHER_FACTS) - set(found))}"
    assert all(what.endswith(_THE_CHECKER) for (_, _, how, what) in found if how == "import")


def test_the_rule_the_places_import_is_the_checkers_own_object():
    from g6.invariants import envelope

    assert improve.ERROR_CODE_PATTERN is mission.ERROR_CODE_PATTERN is envelope.ERROR_CODE_PATTERN
    assert reflex.ERROR_CODE_PATTERN is ERROR_CODE_PATTERN is sys.modules["g6.metaharness.api"].ERROR_CODE_PATTERN
    assert "ERROR_CODE_PATTERN.match(code)" in Path(envelope.__file__).read_text(encoding="utf-8")   # matched as here


@pytest.mark.parametrize("source, how", [
    ('def f(said):\n    return said.startswith("G6_E_")\n', "prefix test"),          # improve on a0a4d0d
    ('import re\n_CODE = re.compile(r"\\bG6_E_[A-Z0-9_]+\\b")\n', "literal"),       # reflex and mission on a0a4d0d
    ('def f(why):\n    return re.search(r"\\bG6_E_[A-Z0-9_]+\\b", why)\n', "literal"),  # api on a0a4d0d
    ('def f(c):\n    return "G6_E_" in c\n', "literal"),
    ('def f(c):\n    return "G6_E_LOOP_" in c\n', "containment test"),
    ('def f(c):\n    return c[:5] == "G6_E_"\n', "literal"),
    ('def f(c):\n    return c.startswith(("x", "G6_E_PIPELINE_"))\n', "prefix test"),
    ('def f(c):\n    return c.startswith("G6_")\n', "prefix test"),
    ('def f(c):\n    return fnmatch(c, "G6_E_LOOP_*")\n', "literal"),
    ('def f(c):\n    return re.fullmatch("G6_E_" + "[A-Z]+", c)\n', "literal"),
    ('from g6.invariants import ERROR_CODE_PATTERN\ndef f(c):\n    return ERROR_CODE_PATTERN.fullmatch(c)\n', "rule"),
    ('from g6.invariants import ERROR_CODE_PATTERN\ndef f(c):\n    return ERROR_CODE_PATTERN.search(c)\n', "rule"),
    ('from g6.invariants import ERROR_CODE_PATTERN\ndef f(c):\n    return re.match(ERROR_CODE_PATTERN, c)\n', "rule"),
    ('ERROR_CODE_PATTERN = re.compile("^G6_E_[A-Z]+$")\n', "a rule of its own"),
    ('from g6.somewhere_else import ERROR_CODE_PATTERN\n', "import"),
])
def test_the_inventory_sees_each_way_a_rule_of_its_own_could_be_written(source, how):
    found = _decisions_in(ast.parse(source), "mcp/improve.py")
    assert any(key[2] == how for key in found), found
    assert set(found) - set(CODE_RULES) - set(OTHER_FACTS), f"the inventory would let this stand: {found}"


def test_the_inventory_reads_no_docstring_comment_or_message_as_a_rule():
    source = ('"""Replies carry a ``G6_E_*`` code."""\n'
              'def f():\n    """Its ``G6_E_*`` code."""\n'
              '    return "[G6_E_SENSITIVE_COMPONENT_DENIED] refused", "G6_E_META_SKILL_CODE: unclosed code fence"\n')
    assert _decisions_in(ast.parse(source), "mcp/improve.py") == {}
