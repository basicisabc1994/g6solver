# SPDX-License-Identifier: Apache-2.0
"""Regressions for route-local authority and selection after progressive disclosure."""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from g6.skills import corpus, routing

GUIDE = Path(__file__).resolve().parents[2] / "src/g6/skills/using-g6"
BASELINE = Path(__file__).with_name("fixtures") / "using_g6_wiring_int.md"
EXCEPTIONS = BASELINE.with_name("using_g6_sentence_exceptions.tsv")
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
TASKS = [
    ("write numbered requirements, acceptance tests and template instances for a hidden constraint", "verify",
     {"route:review"}),
    ("the reply carries repair and by is person", "stuck", {"route:select", "route:review"}),
    ("evidence kinds must match between data-only stages field mapping", "plan", {"route:compose"}),
    ("held-out cases exploration safety injection evaluator approval prompt", "plan", {"route:fit"}),
]


def _normal(text: str) -> str:
    return " ".join(text.split())


def _instructions(text: str) -> str:
    # Relative navigation changes location, not the authority of the sentence.
    # The separate pointer census resolves every such file from its own folder.
    text = re.sub(r"\s*\(`(?:references/)?[^`]+\.md`\)", "", text)
    return re.sub(r"`references/([^`]+\.md)`", r"`\1`", text)


def _sentences(text: str) -> list[str]:
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    text = re.sub(r"^#{1,6} .*?$", "", text, flags=re.M)
    text = re.sub(r"^```[^\n]*$", "", text, flags=re.M)
    text = re.sub(r"^\s*(?:[-*+] |\d+\. )", "", text, flags=re.M)
    text = re.sub(r"\*\*[\w ]+\.\*\*\s*", "", text)
    text = _instructions(text).replace("`", "").replace("**", "")
    sentences = []
    for paragraph in re.split(r"\n\s*\n", text):
        sentences.extend(s for s in re.split(r"(?<=[.!?])\s+", _normal(paragraph)) if s)
    return sentences


def _held_sentences(original, route_id):
    """All sentences, without a keyword filter; shared text binds every route."""
    route = routing.load().routes[route_id]
    headings = {"review": "## Deliver a coding task with G6", "discover": "## Find",
                "compose": "## Compose", "fit": "## Fit"}
    section = routing.section(original, headings.get(route_id, route.anchor))
    assert section, route_id
    tools = {key.split("/", 1)[1] for key, entry in routing.load().entries.items()
             if entry.route == route_id and key.startswith("mcp:g6/")}
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n|\n(?=- \*\*|\d+\. )", original):
        first_tool = re.search(r"\b(?:g6_|metaharness_)[a-z_]+", paragraph)
        if first_tool and first_tool.group() in tools:
            paragraphs.append(paragraph)
    shared = [original.split("- **select.**", 1)[0], routing.section(original, "## Read and ask")]
    assert all(shared)
    return {s for text in [*shared, section, *paragraphs] for s in _sentences(text)}


def _exceptions(original):
    rows = list(csv.DictReader(EXCEPTIONS.read_text(encoding="utf-8").splitlines(), delimiter="\t"))
    exceptions = {}
    for row in rows:
        assert set(row) == {"sentence", "reason", "replacement"}, row
        sentence, reason = row["sentence"], row["reason"]
        replacement = "" if row["replacement"] == "-" else row["replacement"]
        assert sentence in _sentences(original), f"stale sentence exception: {sentence}"
        assert sentence not in exceptions, f"duplicate sentence exception: {sentence}"
        assert reason.startswith(("reworded-to: ", "dropped-because: ")) and reason.split(": ", 1)[1], row
        if reason.startswith("reworded-to:"):
            assert replacement and _sentences(replacement) == [replacement], row
        else:
            assert not replacement, row
        exceptions[sentence] = replacement
    return exceptions


def _reachable(root, route):
    """Follow local reference links from this route only, including onward links."""
    line = routing.section(root, route.anchor)
    assert line, route.id
    texts, seen, pending = [root], set(), [GUIDE / target for target in LINK.findall(line)]
    while pending:
        path = pending.pop().resolve()
        assert path.is_relative_to(GUIDE.resolve()), path.name
        if path in seen:
            continue
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        texts.append(text)
        pending.extend(path.parent / target for target in LINK.findall(text))
    return texts


def test_every_fixture_sentence_has_an_original_route_owner():
    original = BASELINE.read_text(encoding="utf-8")
    held = set().union(*(_held_sentences(original, route_id) for route_id in routing.load().routes))
    assert set(_sentences(original)) <= held
    _exceptions(original)


@pytest.mark.parametrize("route_id", sorted(routing.load().routes))
def test_each_route_reaches_its_original_safety_and_authority_sentences(route_id):
    """Every fixture sentence survives on every route that held it, or has a reviewed exception.

    Whole sentences are compared after whitespace and markup normalization. An
    exception that rewords a sentence must name a reachable replacement sentence.
    """
    original = BASELINE.read_text(encoding="utf-8")
    exceptions = _exceptions(original)
    required = {exceptions.get(s, s) for s in _held_sentences(original, route_id)} - {""}
    root = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
    reachable = _reachable(root, routing.load().routes[route_id])
    available = {s for text in reachable for s in _sentences(text)}
    missing = sorted(required - available)
    assert not missing, f"{route_id} cannot reach original sentences: {missing}"
    qualified = sorted((s, candidate) for s in required for candidate in available
                       if candidate != s and re.match(re.escape(s.rstrip('.!?')) + r"\b", candidate))
    # An intact duplicate cannot hide a newly appended conditional exception,
    # even across a paragraph break or with a different verb. Original following
    # sentences are allowed exactly as written; no exception wording is hand-kept.
    original_sentences = set(_sentences(original))
    for text in reachable:
        sentences = _sentences(text)
        qualified.extend((rule, following) for rule, following in zip(sentences, sentences[1:])
                         if rule in required
                         and re.match(r"(?:If|Unless|Except|But|However|Otherwise|When|Although)\b", following)
                         and following not in original_sentences)
    assert not qualified, f"{route_id} qualifies original authority rules: {qualified}"


@pytest.mark.parametrize("route_id", sorted(routing.load().routes))
def test_route_corpus_includes_its_root_entry_and_every_declared_detail(route_id):
    route = routing.load().routes[route_id]
    entry = next(e for e in corpus._routes([]) if e["id"] == f"route:{route_id}")
    root = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
    assert _normal(routing.section(root, route.anchor)) in entry["text"]
    for detail in route.detail:
        assert _normal((GUIDE.parents[1] / detail).read_text(encoding="utf-8")) in entry["text"], detail


@pytest.mark.parametrize("detail", [path for path in sorted((GUIDE / "references").glob("*.md"))
                                    if "**Repair.**" in path.read_text(encoding="utf-8")], ids=lambda p: p.name)
def test_every_explicit_repair_step_keeps_the_roots_repair_owner_rule(detail):
    """A repair step must retain the root's complete rule, including the person-owned stop."""
    root = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
    rules = [sentence for sentence in _sentences(root.split("## Routes", 1)[0]) if "reply carries repair" in sentence]
    assert rules
    step = re.search(r"\d+\. \*\*Repair\.\*\*\s*(.*?)(?=\n\d+\. |\Z)",
                     detail.read_text(encoding="utf-8"), re.S)
    assert step
    section = _normal(step.group(1))
    assert all(rule.replace("`", "") in section.replace("`", "") for rule in rules), detail.name


def test_validating_any_skill_route_reads_guidance_without_importing_components():
    """Every shipped route validates in a fresh process; a missing section still refuses."""
    probe = r'''
import copy, json, sys
from g6.core import selector
from g6.core.registry import get_registry
from g6.skills import corpus

data = corpus.load()
registry = get_registry()
packages = {registry.refs[c].module.split(":", 1)[0]: c
            for c in data["components"] if c in registry.refs}
before = set(sys.modules)
results = []
for entry in data["entries"]:
    if entry["kind"] != "route":
        continue
    isolated = {**data, "entries": [entry]}
    candidate = selector.select("using g6", corpus=isolated)["candidates"][0]
    results.append((candidate["id"], candidate["validated"], candidate["issues"]))
missing = copy.deepcopy(isolated)
missing["entries"][0]["call"]["section"] = "## A section that does not exist"
refused = selector.select("using g6", corpus=missing)["candidates"][0]
print(json.dumps({"results": results, "refused": refused,
                  "imports": sorted(c for p, c in packages.items() if p in sys.modules and p not in before)}))
'''
    env = {**os.environ, "PYTHONPATH": str(GUIDE.parents[2])}
    done = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], capture_output=True,
                          text=True, env=env, timeout=120, check=True)
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["imports"] == []
    assert {row[0] for row in result["results"]} == {f"route:{r}" for r in routing.load().routes}
    assert all(valid and not issues for _, valid, issues in result["results"])
    assert result["refused"]["validated"] is False
    assert result["refused"]["issues"] == ["the skill has no section ## A section that does not exist"]


@pytest.mark.parametrize("task,phase,baseline_routes", TASKS)
def test_selection_loses_no_route_offered_at_the_integration_baseline(task, phase, baseline_routes):
    """These literal route sets were read from c5782df1's corpus with the unchanged selector."""
    from g6.core import selector

    offered = {c["id"] for c in selector.select(task, phase)["candidates"]}
    assert baseline_routes <= offered, sorted(offered)


@pytest.mark.parametrize("task,phase,baseline_routes", TASKS)
def test_live_route_details_preserve_the_baseline_choices(task, phase, baseline_routes):
    """Exercise the route builder and public selector together, without building or writing a corpus."""
    from g6.core import selector

    data = corpus.load()
    tools = [e for e in data["entries"] if e["kind"] == "tool"]
    live = {**data, "entries": [e for e in data["entries"] if e["kind"] != "route"] + corpus._routes(tools)}
    offered = {c["id"] for c in selector.select(task, phase, corpus=live)["candidates"]}
    assert baseline_routes <= offered, sorted(offered)


def test_root_keeps_repair_owner_and_workflow_registration_at_the_moment_of_need():
    root = _normal((GUIDE / "SKILL.md").read_text(encoding="utf-8"))
    assert "When a G6 reply carries `repair`, follow its step; if `by` is person, tell the person and stop." in root
    line = routing.section((GUIDE / "SKILL.md").read_text(encoding="utf-8"), "**workflow.**")
    assert "where the server is registered" in _normal(line)


def test_reference_guidance_has_no_empty_signpost_or_self_pointer():
    for path in (GUIDE / "references").glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "Route signpost" not in text, path.name
        assert "sections below are routes too" not in _normal(text), path.name
        targets = LINK.findall(text) + re.findall(r"`([^`\n]+\.md)`", text)
        assert all((path.parent / target).resolve() != path.resolve() for target in targets), path.name
