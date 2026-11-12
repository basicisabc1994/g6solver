# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""One authority (G6 0.3 design, section 3.1; slice 2, item 1): ``g6.evidence.certificate`` alone decides a requirement's
status, and nothing else in the product reaches one without it.

The inventory is built from the code. Every module under ``src/g6`` is parsed, and a module is a place that produces or
reads a status, a support or a refutation when it:

- names the issuer's Belnap pair (``support``, ``refute``, ``supported_by``, ``refuted_by``) as an attribute, a keyword,
  a field or a text constant;
- imports the issuer's decision API (``issue``, ``verify``, ``grounding``, ``PredicateStatement``, ``Certificate``) from
  ``g6.evidence``, or reads a ``verified`` or ``verified_predicates`` attribute;
- defines, imports or calls ECHA's claim rule (``evaluate_claim``, ``trace_claim``);
- names the patch review's requirement status (the value of ``patch_review.PASSED_ON_CHECKS``, or imports it).

Each place must be in ``PLACES`` with its role, and each role is checked from the code:

- ``issuer``: ``evidence/certificate.py`` only;
- ``through the issuer``: its own imports (those inside functions too) name the issuer, or an adapter whose one job
  is to build the issuer's inputs and which imports the issuer itself;
- ``reader``: it reads the pair and never sets it (no keyword, field, dictionary key or assignment of it);
- ``view``: ECHA's view, a reader that copies the issuer's statement (``tests/research/echa/test_echa_view.py``);
- ``analysis``: research code no product module imports (its records the mapping refuses, tested beside ECHA's view);
- ``other meaning``: the word names something else, said in the table;
- ``open``: product code that does not go through the issuer yet, each with why. The list may only shrink.

A place the scan finds that ``PLACES`` does not list fails the test, and so does a listed place the scan no longer finds.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import g6
from g6.harness import patch_review

SRC = Path(g6.__file__).resolve().parent
ISSUER = "g6.evidence.certificate"
BELNAP = frozenset({"support", "refute", "supported_by", "refuted_by"})
ISSUER_API = frozenset({"issue", "verify", "grounding", "PredicateStatement", "Certificate"})
CLAIM_RULE = frozenset({"evaluate_claim", "trace_claim"})
STATUS_NAMES = frozenset({"PASSED_ON_CHECKS"})
#: The issuer's verdict on a statement and a certificate, read as attributes.
VERDICTS = frozenset({"verified", "verified_predicates"})

DISTRIBUTION = "the support of a probability distribution, not evidence for a requirement"
DERIVATION = "a derived fact's derivation (its support), not evidence for a requirement"
REFUTATION_TEST = "DoWhy's refutation tests of a causal estimate (refute=true), not evidence for a requirement"
PLACES: dict[str, tuple[str, str]] = {
    "evidence/certificate.py": ("issuer", "the one rule that decides a requirement's status"),
    "evidence/__init__.py": ("through the issuer", "re-exports the issuer"),
    "evidence/mapping.py": ("through the issuer", "the mapping table: promotes records into the issuer's inputs"),
    "harness/__init__.py": ("through the issuer", "the harness adjudicates with the issuer"),
    "harness/patch_review.py": ("through the issuer", "its run reading per requirement rides beside the issuer's "
                                                      "statement (assessment.requirement_evidence)"),
    "harness/requirement_evidence.py": ("through the issuer", "the patch review's adapter: builds the issuer's inputs and "
                                                          "copies its statement for each requirement"),
    "reasoning/vote.py": ("other meaning", "a candidate answer's checked flag its caller sets, counted in a vote; "
                                           "not a requirement's status"),
    "harness/verification_search.py": ("reader", "reads the review's run reading (passed_on_stated_checks) to list "
                                                 "what was demonstrated; it decides no status of its own"),
    "harness/patch_report.py": ("reader", "renders the issuer's statement from assessment.requirement_evidence"),
    "action_gating/scope.py": ("through the issuer", "a review record read from the issuer"),
    "workforce/coding_certificate.py": ("through the issuer", "the coding certificate, issued by the issuer"),
    "research/mission_bridge/csir/certificate.py": ("through the issuer", "CSIR's adapter, which delegates"),
    "formal/four_valued.py": ("reader", "Belnap's connectives over a statement it is handed"),
    "core/component_certificate.py": ("reader", "reads an issued certificate's body"),
    "research/echa/view.py": ("view", "ECHA's audit view: each status is the issuer's, copied"),
    "research/echa/claims.py": ("analysis", "ECHA's claim rule: research analysis, never a requirement's status"),
    "research/echa/trace.py": ("analysis", "the trace of an ECHA claim"),
    "research/echa/example.py": ("analysis", "a synthetic worked example"),
    "research/echa/__init__.py": ("analysis", "ECHA's exports"),
    "research/mission_bridge/bundle.py": ("analysis", "research behind the mission bridge's own flag"),
    "causal/causal_block.py": ("other meaning", REFUTATION_TEST),
    "causal/envelope.py": ("other meaning", REFUTATION_TEST),
    "causal/estimation.py": ("other meaning", REFUTATION_TEST),
    "datalog/checks.py": ("other meaning", DERIVATION),
    "datalog/ops.py": ("other meaning", DERIVATION),
    "datalog/seminaive.py": ("other meaning", DERIVATION),
    "graph_reasoning/checks.py": ("other meaning", DERIVATION),
    "ml/adapt_bayesian/block.py": ("other meaning", DISTRIBUTION),
    "ml/adapt_bayesian/certificate/adapter.py": ("other meaning", DISTRIBUTION),
    "workforce/readiness.py": ("open", "the cognitive state's per-check observations carry a support and refutation "
                                       "pair of their own; routing them through the issuer changes what that state's "
                                       "fields mean (a surface field), so it is listed for the owner, not changed"),
}
ROLES = frozenset({"issuer", "through the issuer", "reader", "view", "analysis", "other meaning", "open"})


def _module(rel: str) -> str:
    parts = rel.removesuffix(".py").split("/")
    return ".".join(["g6", *parts[:-1]] if parts[-1] == "__init__" else ["g6", *parts])


def _trees() -> dict[str, ast.Module]:
    return {path.relative_to(SRC).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
            for path in sorted(SRC.rglob("*.py")) if "vendor" not in path.parts}


def _status_word() -> str:
    return patch_review.PASSED_ON_CHECKS


def hits(tree: ast.Module, word: str) -> set[str]:
    """What makes a module a place: the scan's rules (the module's docstring)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in BELNAP | VERDICTS:
            found.add(f"attribute {node.attr}")
        elif isinstance(node, ast.keyword) and node.arg in BELNAP:
            found.add(f"keyword {node.arg}")
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in BELNAP:
            found.add(f"field {node.target.id}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in BELNAP | {word}:
            found.add(f"text {node.value}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = {alias.name for alias in node.names}
            if node.module.startswith("g6.evidence") and names & ISSUER_API:
                found.add("issuer api")
            if names & CLAIM_RULE:
                found.add("claim rule")
            if node.module == "g6.harness.patch_review" and names & STATUS_NAMES:
                found.add("status name")
        elif isinstance(node, (ast.FunctionDef, ast.Name)) and getattr(node, "name", getattr(node, "id", "")) in CLAIM_RULE:
            found.add("claim rule")
    return found


def _imports(rel: str, tree: ast.Module) -> set[str]:
    """Every g6 module ``rel`` imports, anywhere in it (inside functions too), relative imports resolved."""
    package = _module(rel).split(".") if rel.endswith("__init__.py") else _module(rel).split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(package[:len(package) - node.level + 1] + ([base] if base else []))
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return {name for name in found if name == "g6" or name.startswith("g6.")}


def _graph(trees: dict[str, ast.Module]) -> dict[str, set[str]]:
    modules = {_module(rel): rel for rel in trees}
    graph = {}
    for rel, tree in trees.items():
        graph[_module(rel)] = {name for name in _imports(rel, tree) if name in modules}
    return graph


#: The issuer's own modules, and the adapters whose one job is to build its inputs (each imports the issuer itself).
ISSUER_MODULES = frozenset({"g6.evidence", ISSUER, "g6.evidence.mapping"})
ADAPTERS = frozenset({"g6.harness.requirement_evidence", "g6.research.mission_bridge.csir.certificate"})


def _through(graph: dict[str, set[str]], module: str) -> bool:
    """Whether ``module``'s own imports name the issuer, or an adapter that does. Not the whole import closure: on the
    base commit, the patch review's closure reached the issuer through unrelated modules without its code using it."""
    return bool(graph.get(module, set()) & (ISSUER_MODULES | ADAPTERS))


def _sets_the_pair(tree: ast.Module) -> list[str]:
    """Where a module sets the pair: a keyword, a field, a dictionary key or an assignment of it."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in BELNAP:
            found.append(f"keyword {node.arg}")
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in BELNAP:
            found.append(f"field {node.target.id}")
        elif isinstance(node, ast.Dict):
            found += [f"key {k.value}" for k in node.keys if isinstance(k, ast.Constant) and k.value in BELNAP]
        elif isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            name = node.attr if isinstance(node, ast.Attribute) else getattr(node.slice, "value", None)
            if name in BELNAP:
                found.append(f"assignment {name}")
    return found


TREES = _trees()
GRAPH = _graph(TREES)
FOUND = {rel: hits(tree, _status_word()) for rel, tree in TREES.items()}
FOUND = {rel: found for rel, found in FOUND.items() if found}


def test_every_place_the_scan_finds_is_listed_and_every_listed_place_is_still_one():
    assert sorted(set(FOUND) - set(PLACES)) == [], "unlisted places: classify each, or route it through the issuer"
    assert sorted(set(PLACES) - set(FOUND)) == [], "listed places the scan no longer finds: remove them"
    assert {role for role, _ in PLACES.values()} <= ROLES


def test_the_issuer_is_the_one_place_with_the_issuer_role():
    assert [rel for rel, (role, _) in PLACES.items() if role == "issuer"] == ["evidence/certificate.py"]
    assert _module("evidence/certificate.py") == ISSUER


@pytest.mark.parametrize("rel", [rel for rel, (role, _) in PLACES.items() if role == "through the issuer"])
def test_a_place_through_the_issuer_imports_it_or_an_adapter_of_it(rel):
    assert _through(GRAPH, _module(rel)), rel


def test_each_adapter_imports_the_issuer_itself():
    for adapter in sorted(ADAPTERS):
        assert GRAPH[adapter] & ISSUER_MODULES, adapter


@pytest.mark.parametrize("rel", [rel for rel, (role, _) in PLACES.items() if role in ("reader", "view")])
def test_a_reader_or_a_view_never_sets_the_pair(rel):
    assert _sets_the_pair(TREES[rel]) == [], rel


@pytest.mark.parametrize("rel", [rel for rel, (role, _) in PLACES.items() if role == "analysis"])
def test_research_analysis_is_imported_by_no_product_module(rel):
    target = _module(rel)
    package = target.rsplit(".", 1)[0] if not rel.endswith("__init__.py") else target
    importers = sorted(mod for mod, names in GRAPH.items() if not mod.startswith("g6.research")
                       and any(name == target or name.startswith(package + ".") or name == package for name in names))
    assert importers == [], (rel, importers)


def test_the_open_places_are_named_with_why_and_only_shrink():
    # The list was this on 2026-11-06 (slice 2). A place leaves it when it goes through the issuer; none joins it.
    assert {rel for rel, (role, _) in PLACES.items() if role == "open"} <= {"workforce/readiness.py"}
    assert all(why.strip() for _role, why in PLACES.values())


def test_the_scan_finds_a_planted_place_and_the_role_checks_can_fail():
    # survival controls: the scan sees each rule, the import check sees a module that does not reach the issuer, and the
    # reader check sees a module that sets the pair
    planted = ast.parse("from g6.evidence import issue\nx.support\nf(refute=True)\nrecord = {'supported_by': []}\n"
                        "from g6.research.echa import evaluate_claim\nstatus = 'passed_on_stated_checks'\n")
    assert hits(planted, _status_word()) == {"issuer api", "attribute support", "keyword refute", "text supported_by",
                                              "claim rule", "text passed_on_stated_checks"}
    assert not _through(GRAPH, "g6.datalog.ops") and not _through(GRAPH, "g6.workforce.readiness")
    assert sorted(_sets_the_pair(planted)) == ["key supported_by", "keyword refute"]
    assert _sets_the_pair(TREES["workforce/readiness.py"])
