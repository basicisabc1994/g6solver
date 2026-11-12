# SPDX-License-Identifier: Apache-2.0
"""The class one_fact_two_rules, removed at its root for what counts as a belief (review 1 of PR #8's second follow-up,
finding 3): ``check_beliefs`` applied the belief rule (``engine._beliefs``) to a caller's priors, and an updater's
posterior was checked by it, but ``BayesianUpdater.update``, the selectors' ``rank`` and ``expected_information_gain``
took a belief map as it came, so priors of 1.5 and -0.5 gave a posterior outside [0, 1]. Now ``_beliefs`` is the one
rule for a belief map, wherever it enters: 2 to 32 hypotheses, each named by nonempty text, each belief a number (not
a flag) finite in [0, 1], summing to one with no implicit normalisation.

This lists, from the code, every function or method that takes beliefs (by the parameter's name or its annotation),
checks that each public one applies ``_beliefs`` where the value enters and that each other one has a reason, and plants
every kind of bad prior at each, beside ``check_beliefs``, which must refuse the same."""
from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from g6.cognitive_bridge import (
    BayesianUpdater, CognitiveAgent, Hypothesis, InformationGainSelector, MLAcquisitionSelector, Observation, Probe,
    StaticProposer, ToolSpec, check_beliefs, codes, expected_information_gain,
)

PACKAGE = Path(codes.__file__).resolve().parent
#: Annotations that type a belief map.
BELIEF_TYPES = {"Mapping[str, float]", "dict[str, float]"}


def _functions():
    """Every function in the package: (module, dotted name, node, the enclosing class node or None)."""
    found = []

    def visit(node, module, prefix, cls):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, module, f"{prefix}{child.name}.", child)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((module, f"{prefix}{child.name}", child, cls))
                visit(child, module, f"{prefix}{child.name}.", None)
            else:
                visit(child, module, prefix, cls)

    for path in sorted(PACKAGE.glob("*.py")):
        visit(ast.parse(path.read_text(encoding="utf-8")), path.name, "", None)
    return found


def _belief_params(fn) -> list[str]:
    params = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    return [p.arg for p in params if p.arg == "beliefs" or (p.annotation is not None and ast.unparse(
        p.annotation).strip("'\"") in BELIEF_TYPES)]


def _protocol(cls) -> bool:
    return cls is not None and any(ast.unparse(b) == "Protocol" for b in cls.bases)


def takers():
    """Every function or method that takes beliefs: (module, dotted name, parameter) -> 'public', 'protocol' or
    'private'."""
    out = {}
    for module, name, fn, cls in _functions():
        for param in _belief_params(fn):
            kind = ("protocol" if _protocol(cls) else
                    "private" if any(part.startswith("_") for part in name.split(".")) else "public")
            out[(module, name, param)] = kind
    return out


#: The public entries: each applies the rule where the value enters.
PUBLIC = {
    ("engine.py", "expected_information_gain", "beliefs"),
    ("engine.py", "InformationGainSelector.rank", "beliefs"),
    ("engine.py", "BayesianUpdater.update", "beliefs"),
    ("engine.py", "MLAcquisitionSelector.rank", "beliefs"),
}
#: The others, each with why it applies no rule of its own.
PRIVATE = {
    ("engine.py", "_beliefs", "beliefs"): "the rule itself",
    ("engine.py", "_validate_probe", "beliefs"): "called with beliefs the rule took (an entry's, the agent's checked "
                                                 "beliefs), or with hypothesis ids alone (LLMProposer reads its keys)",
    ("engine.py", "_posterior", "prior"): "the prior the agent checked; the posterior it is handed is taken by the rule",
    ("engine.py", "_digest", "beliefs"): "the digest of beliefs the agent checked",
    ("engine.py", "_prior", "beliefs"): "the agent's checked beliefs, compared with their digest",
}
#: The interfaces a caller's code implements: no body of their own.
PROTOCOLS = {("engine.py", "Selector.rank", "beliefs"), ("engine.py", "Updater.update", "beliefs")}


def test_every_function_that_takes_beliefs_is_listed_from_the_code():
    found = takers()
    assert {k for k, v in found.items() if v == "public"} == PUBLIC
    assert {k for k, v in found.items() if v == "private"} == set(PRIVATE)
    assert {k for k, v in found.items() if v == "protocol"} == PROTOCOLS


def _fn(module, name):
    return next(f for m, n, f, _ in _functions() if (m, n) == (module, name))


def _reads(node, name) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load) for n in ast.walk(node))


def _calls(node, name) -> bool:
    return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == name for c in ast.walk(node))


@pytest.mark.parametrize("module,name,param", sorted(PUBLIC))
def test_every_public_entry_applies_the_belief_rule_where_the_value_enters(module, name, param):
    # the first statement that reads the beliefs takes them through the boundary, and the next applies the rule (or
    # one statement does both): nothing reads them before the rule has
    body = [s for s in _fn(module, name).body if _reads(s, param)]
    # Native cardinality admission must precede the copy. The copy and the
    # numeric belief rule remain mandatory immediately afterward.
    assert isinstance(body[0], ast.Expr) and _calls(body[0], '_admit_hypotheses')
    body = body[1:]
    first = body[0]
    assert isinstance(first, ast.Assign) and _calls(first.value, "snapshot"), ast.unparse(first)[:100]
    ruled = first if _calls(first.value, "_beliefs") else body[1]
    assert isinstance(ruled, ast.Assign) and _calls(ruled.value, "_beliefs") and [
        ast.unparse(t) for t in ruled.targets] == [param], f"{name} reads its beliefs before the rule: " \
                                                           f"{ast.unparse(ruled)[:100]}"


def test_the_priors_and_an_updaters_posterior_are_taken_by_the_same_rule():
    assert _calls(_fn("engine.py", "check_beliefs"), "_beliefs")
    assert _calls(_fn("engine.py", "_posterior"), "_beliefs")


# --- Every kind of bad prior, at each entry and at check_beliefs -------------------------------------------------------

BAD = {
    "above-one": {"A": 1.5, "B": -.5},
    "negative": {"A": -.25, "B": 1.25},
    "nan": {"A": math.nan, "B": .5},
    "inf": {"A": math.inf, "B": 0.0},
    "not-summing-to-one": {"A": .2, "B": .2},
    "one-hypothesis": {"A": 1.0},
    "thirty-three-hypotheses": {f"h{i}": 1 / 33 for i in range(33)},
    "a-flag": {"A": True, "B": False},
    "text": {"A": "0.5", "B": "0.5"},
    "null": {"A": None, "B": 1.0},
    "empty-id": {"": .5, "B": .5},
    "not-a-mapping": [.5, .5],
}
GOOD = {
    "halves": {"A": .5, "B": .5},
    "integers": {"A": 1, "B": 0},
    "uneven": {"A": .25, "B": .75},
    "thirty-two-hypotheses": {f"h{i}": 1 / 32 for i in range(32)},
}


def _probe(beliefs):
    ids = list(beliefs) if isinstance(beliefs, dict) else ["A", "B"]
    return Probe("p", "measure", "read", {h: i % 2 == 0 for i, h in enumerate(ids)}, .8)


class _NoPredictor:
    def predict(self, rows):
        raise AssertionError("the predictor was asked to score beliefs the rule refuses")


class _Predictor:
    def predict(self, rows):
        return [1.0 for _ in rows]


ENTRIES = {
    "expected_information_gain": lambda b, p, model=None: expected_information_gain(b, p),
    "InformationGainSelector.rank": lambda b, p, model=None: InformationGainSelector().rank(b, [p], 1.0),
    "BayesianUpdater.update": lambda b, p, model=None: BayesianUpdater().update(b, p, True),
    "MLAcquisitionSelector.rank": lambda b, p, model=None: MLAcquisitionSelector(model or _NoPredictor()).rank(
        b, [p], 1.0),
}


def test_every_public_entry_is_planted():
    assert {name for _, name, _ in PUBLIC} == set(ENTRIES)


@pytest.mark.parametrize("kind", sorted(BAD))
@pytest.mark.parametrize("entry", sorted(ENTRIES))
def test_a_bad_prior_is_refused_at_every_entry(entry, kind):
    with pytest.raises(ValueError):
        ENTRIES[entry](BAD[kind], _probe(BAD[kind]))


@pytest.mark.parametrize("kind", sorted(set(BAD) - {"not-a-mapping"}))
def test_check_beliefs_refuses_the_same_priors(kind):
    with pytest.raises(ValueError):
        check_beliefs([Hypothesis(h, f"statement {h}", p) for h, p in BAD[kind].items()])


@pytest.mark.parametrize("kind", sorted(GOOD))
@pytest.mark.parametrize("entry", sorted(ENTRIES))
def test_a_good_prior_is_taken_at_every_entry_and_by_check_beliefs(entry, kind):
    out = ENTRIES[entry](GOOD[kind], _probe(GOOD[kind]), _Predictor())
    if entry == "BayesianUpdater.update":
        assert set(out) == set(GOOD[kind]) and all(0 <= v <= 1 for v in out.values())
        assert math.isclose(sum(out.values()), 1.0, abs_tol=1e-9)
    assert check_beliefs([Hypothesis(h, f"statement {h}", p) for h, p in GOOD[kind].items()]) == {
        h: float(p) for h, p in GOOD[kind].items()}


@pytest.mark.parametrize("kind", sorted(BAD))
def test_an_updaters_posterior_is_refused_by_the_same_rule(kind):
    bad = BAD[kind]

    class Updater:
        def update(self, beliefs, probe, positive):
            return bad

    hypotheses = [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)]
    agent = CognitiveAgent(StaticProposer([Probe("p", "measure", "read", {"A": True, "B": False}, .8)]),
                           {"read": ToolSpec("read", lambda q, a: Observation(True, "source:actual"))}, updater=Updater())
    with pytest.raises(ValueError):
        agent.run("measure", hypotheses, budget=1, max_steps=1)
