# SPDX-License-Identifier: Apache-2.0
"""Skills ship as package data and are served (FW-01): a component's ``skill/SKILL.md`` is read without importing the
component, ``g6_describe`` returns it, and its ``## Ops`` list and ``g6-example`` blocks parse."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

import g6
from g6.core import discovery, registry, skills
from g6.mcp import describe_reply

TEXT = '''---
name: g6-stripe-counter
description: Use when you need to count zebra stripes
---

# Stripe counter

## Ops

- `count`: how often the word stripe appears in a text
- `ops`: this list

## Examples

```g6-example op=count
{"text": "stripe stripe"}
```
'''


@pytest.fixture
def member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry_reset) -> Path:
    """``g6.skilltest.stripe_counter`` (with a skill, and failing if imported) and ``g6.skilltest.bare`` (without a
    skill), in a temporary family."""
    fam = tmp_path / "g6" / "skilltest"
    for name, tail in (("stripe_counter", 'raise ImportError("never imported")\n'), ("bare", "")):
        (fam / name).mkdir(parents=True)
        (fam / name / "__init__.py").write_text(f'"""The {name} test member."""\n{tail}', encoding="utf-8")
    (fam / "__init__.py").write_text('__g6_family__ = "skilltest"\n', encoding="utf-8")
    (fam / "stripe_counter" / "skill").mkdir()
    (fam / "stripe_counter" / "skill" / "SKILL.md").write_text(TEXT, encoding="utf-8")
    monkeypatch.setattr(g6, "__path__", [*g6.__path__, str(tmp_path / "g6")])
    monkeypatch.setattr(registry, "FAMILIES", registry.FAMILIES | {"skilltest"})
    importlib.invalidate_caches()
    yield fam
    for name in [m for m in sys.modules if m == "g6.skilltest" or m.startswith("g6.skilltest.")]:
        del sys.modules[name]


def test_a_skill_name_is_the_component_name_with_hyphens() -> None:
    assert skills.skill_name("coding_agent_gate") == "g6-coding-agent-gate"
    assert skills.skill_name("lean") == "g6-lean"


def test_a_components_skill_is_read_without_importing_it(member: Path) -> None:
    assert skills.skill_text("stripe_counter") == TEXT
    assert skills.skill_text("bare") is None
    assert skills.skill_dir("stripe_counter") == member / "stripe_counter" / "skill"
    assert "g6.skilltest.stripe_counter" not in sys.modules


def test_an_unknown_component_has_no_skill() -> None:
    with pytest.raises(KeyError):
        skills.skill_text("definitely_not_a_component")


def test_front_matter_ops_and_examples_parse() -> None:
    assert skills.front_matter(TEXT) == {"name": "g6-stripe-counter", "description": "Use when you need to count zebra "
                                                                                     "stripes"}
    assert skills.skill_ops(TEXT) == ["count", "ops"]
    assert skills.skill_examples(TEXT) == [("count", {"text": "stripe stripe"})]


def test_an_example_that_is_not_a_json_object_names_its_op() -> None:
    with pytest.raises(ValueError, match="count"):
        skills.skill_examples('```g6-example op=count\n["not", "an", "object"]\n```\n')
    with pytest.raises(ValueError, match="count"):
        skills.skill_examples('```g6-example op=count\n{not json\n```\n')


def test_text_without_front_matter_or_ops_parses_to_nothing() -> None:
    assert skills.front_matter("# just a heading\n") == {}
    assert skills.skill_ops("# just a heading\n") == []
    assert skills.skill_examples("# just a heading\n") == []
    assert skills.skill_api("# just a heading\n") == []
    assert skills.skill_python_examples("# just a heading\n") == []
    assert skills.section("# just a heading\n", "Ops") == ""


API_TEXT = '''# Counter

## Python API

- `count`: how often a word appears
- `report.render`: the report as text

## Examples

```python
from g6.example import count
assert count("a a", "a") == 2
```

```text
not run
```

## Blind spots

- It counts words, not meanings.
'''


def test_a_python_api_list_and_python_examples_parse() -> None:
    assert skills.skill_api(API_TEXT) == ["count", "report.render"]
    assert skills.skill_python_examples(API_TEXT) == ['from g6.example import count\nassert count("a a", "a") == 2\n']
    assert skills.section(API_TEXT, "Blind spots") == "- It counts words, not meanings.\n"
    assert skills.section(API_TEXT, "Python API").startswith("- `count`")


def test_describe_returns_the_skill() -> None:
    info = describe_reply("coding_agent_gate")
    assert info["skill"] == skills.skill_text("coding_agent_gate") and info["skill"].startswith("---")
    assert skills.front_matter(info["skill"])["name"] == "g6-coding-agent-gate"


def test_describe_returns_none_for_a_component_without_a_skill(member: Path) -> None:
    assert discovery.describe("bare")["skill"] is None


def test_find_ranks_a_component_by_its_skill_description_without_importing_it(member: Path) -> None:
    assert discovery.find("zebra")[0]["component"] == "stripe_counter"
    assert "g6.skilltest.stripe_counter" not in sys.modules


def test_help_points_an_agent_to_the_using_g6_skill() -> None:
    assert discovery.HELP.rstrip().endswith("load the using-g6 skill first; `g6 skills install` copies it into your "
                                            "agent's skills folder.")


def test_the_guide_and_the_vendored_skills_are_found_as_package_data() -> None:
    assert (skills.guide_dir() / "SKILL.md").is_file()
    assert {"brainstorming", "test-driven-development"} <= set(skills.vendored())
    assert all((d / "SKILL.md").is_file() for d in skills.vendored().values())
