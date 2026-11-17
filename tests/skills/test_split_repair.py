# SPDX-License-Identifier: Apache-2.0
"""Regressions for route-local authority and selection after progressive disclosure."""
from __future__ import annotations

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
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
AUTHORITY = re.compile(
    r"never|do not|only the person|only where|person.*(?:decid|registered|confirm|agrees)|"
    r"consent|grant|dialog|approv|accept", re.I)
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
    text = re.sub(r"^\s*(?:#{1,6} |[-*+] |\d+\. )", "", text, flags=re.M)
    text = re.sub(r"\*\*[\w ]+\.\*\*\s*", "", text)
    return [s for s in re.split(r"(?<=[.!?])\s+", _normal(_instructions(text))) if s]


@pytest.mark.parametrize("route_id", sorted(routing.load().routes))
def test_each_route_reaches_its_original_safety_and_authority_sentences(route_id):
    """Moving a tool's authority rules into an unrelated route must fail this check.

    Derive obligations from the original section and paragraphs led by this route's
    owned tools. The latter includes the question tool's hidden-constraints paragraph
    in Deliver; no list of safety sentences is maintained by hand.
    """
    original = BASELINE.read_text(encoding="utf-8")
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
    required = {s for text in [section, *paragraphs] for s in _sentences(text) if AUTHORITY.search(s)}
    root = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
    line = routing.section(root, route.anchor)
    assert line, route_id
    reachable = [root, *((GUIDE / target).read_text(encoding="utf-8") for target in LINK.findall(line))]
    available = [_normal(_instructions(re.sub(r"\*\*[\w ]+\.\*\*\s*", "", text))) for text in reachable]
    missing = sorted(s for s in required if not any(s in text for text in available))
    assert not missing, f"{route_id} cannot reach original authority rules: {missing}"


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
    rules = [sentence for sentence in _sentences(root.split("## Routes", 1)[0]) if "`repair`" in sentence]
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
