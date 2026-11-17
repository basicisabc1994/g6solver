# SPDX-License-Identifier: Apache-2.0
"""The wiring-int root's words survive in a short router and directly linked detail.

The fixture is the root before this split, not a second source of runtime guidance.
Check sentences and code examples, not just keywords that could hide lost constraints.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from g6.skills import routing

GUIDE = Path(__file__).resolve().parents[2] / "src/g6/skills/using-g6"
BASELINE = Path(__file__).with_name("fixtures") / "using_g6_wiring_int.md"
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _normal(text: str) -> str:
    # Navigation is checked by the link census, separately from instructions.
    text = re.sub(r"^#{1,6} .*?$", "", text, flags=re.M)
    text = re.sub(r"\*\*[a-z]+\.\*\*\s*", "", text)
    text = re.sub(r"\s*\(`(?:references/)?[^`]+\.md`\)", "", text)
    text = re.sub(r"Other tools: `[^`]+\.md`\.", "", text)
    text = re.sub(r"`references/([^`]+\.md)`", r"`\1`", text)
    text = " ".join(text.split())
    # This signpost described the old monolithic headings, not agent instructions.
    return re.sub(r"The sections below are routes too: [^.]+\.", "", text).strip()


def _units(text: str) -> list[str]:
    """Keep each prose sentence, list lead and fenced example, with only whitespace folded."""
    body = text.split("---", 2)[2] if text.startswith("---") else text
    units = []
    for block in re.split(r"(```.*?```)", body, flags=re.S):
        if block.startswith("```"):
            units.append(_normal(block))
            continue
        for paragraph in re.split(r"\n\s*\n", block):
            lines = [re.sub(r"^\s*(?:[-*+] |\d+\. )", "", line)
                     for line in paragraph.splitlines()]
            prose = _normal("\n".join(lines))
            units.extend(s for s in re.split(r"(?<=[.!?])\s+", prose) if s)
    return units


def _linked(root: str) -> dict[Path, str]:
    linked = {}
    for target in LINK.findall(root):
        path = (GUIDE / target.split("#", 1)[0]).resolve()
        assert path.is_relative_to(GUIDE.parents[1]), target
        assert path.is_file(), f"root link does not resolve: {target}"
        linked[path] = path.read_text(encoding="utf-8")
    return linked


def test_root_is_a_router_with_headroom():
    root = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
    assert routing.root_words(root) <= 800, "root router exceeds the 800-word target"
    assert routing.ABSENT_TOOL_RULE in _normal(root)
    assert "Never call a patch `verified`, and never merge for the person." in _normal(root)
    assert "Only the person answers" in root and "Never grant for the person." in root
    assert "never change permissions yourself" in root


def test_root_keeps_every_original_tool_name():
    baseline = BASELINE.read_text(encoding="utf-8")
    root = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
    assert routing.forms(baseline).prefixed <= routing.forms(root).prefixed


def test_every_original_sentence_and_example_is_kept_in_reachable_text():
    root = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
    available = [_normal(root), *map(_normal, _linked(root).values())]
    missing = [unit for unit in _units(BASELINE.read_text(encoding="utf-8"))
               if not any(unit in text for text in available)]
    assert missing == [], "lost original text:\n" + "\n".join(missing)


def test_every_reference_is_directly_reachable_and_each_route_links_its_detail():
    root = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
    linked = _linked(root)
    references = {path.resolve() for path in (GUIDE / "references").rglob("*.md")}
    assert references <= linked.keys(), f"unreachable references: {references - linked.keys()}"
    for route in routing.load().routes.values():
        section = routing.section(root, route.anchor)
        assert section is not None, route.id
        assert len(section.splitlines()) == 1, f"route is not one line: {route.id}"
        assert LINK.search(section), f"route has no detail link: {route.id}"
        for detail in route.detail:
            if not detail.startswith(routing.REFERENCES):
                continue  # Existing component skills are package APIs, not files shipped in the plugin copy.
            path = GUIDE.parents[1] / detail
            assert path.resolve() in linked, f"route detail is not linked from root: {detail}"


@pytest.mark.parametrize("path", sorted((GUIDE / "references").glob("*.md")), ids=lambda p: p.name)
def test_links_in_detail_also_resolve(path):
    text = path.read_text(encoding="utf-8")
    targets = LINK.findall(text) + re.findall(r"`([^`\n]+\.md(?:#[^`\n]+)?)`", text)
    for target in targets:
        assert (path.parent / target.split("#", 1)[0]).is_file(), (path.name, target)
