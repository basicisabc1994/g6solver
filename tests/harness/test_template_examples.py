# SPDX-License-Identifier: Apache-2.0
"""Agents could not write valid template instances (worked examples, W4).

In the worked examples four reviews were refused because the agent's template instances lacked or misnamed bindings
(``bindings.relation``, ``bindings.function``, ``bindings.mode``): the refusal named only the first problem, and nothing
said what the bindings were. The agent then dropped the templates, and a regression it could have caught went
unchecked. Now the refusal names each binding missing or misnamed, its warning card lists the template's bindings and
gives one valid instance, and the using-g6 skill gives one valid instance of each template it names. Every example is
checked here against the real schema (``verification_templates.template_instances``), so none can drift. The schemas are
unchanged. Offline: refused before anything runs.
"""
from __future__ import annotations

import json
import re

import pytest

from g6.core import skills
from g6.harness import verification_templates as templates
from g6.harness.patch_review import review_patch

GUIDE = skills.guide_dir()


def test_a_binding_refusal_says_what_to_do_when_a_template_cannot_express_the_check():
    # worked examples, round 3: a function needing an object JSON cannot write was never checked, and nothing named the
    # free-form check that could have built it
    assert "proposed_checks" in templates.bindings_help("differential.v1")


SKILL = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
EXAMPLES_FILE = GUIDE / "references" / "templates.md"
REQUIREMENTS = [{"id": "R1", "text": "the change", "checks": ["tests/test_x.py::test_x"]}]
#: The instances the worked examples' agent wrote, as it wrote them (runs 1 and 5).
WRITTEN = [
    ({"id": "T1", "template": "relation.v1", "requirement_id": "R1", "intent": "preserve", "bindings": {}},
     ["relation", "function", "inputs"], []),
    ({"id": "T1", "template": "differential.v1", "requirement_id": "R1", "intent": "preserve", "bindings": {}},
     ["function", "mode", "calls"], []),
    ({"id": "T1", "template": "differential.v1", "requirement_id": "R1", "intent": "preserve",
      "bindings": {"function": "starlette.middleware.wsgi.build_environ", "calls": [{"args": [{"path": "/app"}, ""]}]}},
     ["mode"], []),
    ({"id": "T1", "template": "example.v1", "requirement_id": "R1", "intent": "change",
      "bindings": {"func": "shop.prices.total", "cases": [{"args": [[2, 3]], "returns": 5}]}},
     ["function"], [("func", "function")]),
]


def _valid(instance: dict) -> None:
    """``instance`` passes the real schema, as a review checks it before anything runs."""
    found, = templates.template_instances([instance], {instance["requirement_id"]})
    assert found.template == instance["template"]


def _instances_in(text: str) -> list[dict]:
    """Every JSON object in ``text`` that is a template instance (it names a template)."""
    found, decoder = [], json.JSONDecoder()
    for start in (m.start() for m in re.finditer(r"\{\"id\"", text)):
        try:
            value, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "template" in value:
            found.append(value)
    return found


def test_each_template_has_one_valid_example():
    assert set(templates.EXAMPLES) == set(templates.REGISTRY)
    for ident, example in templates.EXAMPLES.items():
        assert example["template"] == ident
        _valid(example)


@pytest.mark.parametrize("instance, missing, misnamed", WRITTEN)
def test_a_refusal_names_each_binding_missing_or_misnamed_lists_the_bindings_and_gives_a_valid_instance(
        tmp_path, instance, missing, misnamed):
    got = review_patch(tmp_path, task="t", requirements=REQUIREMENTS, template_instances=[instance])
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    message, card = got["warning_card"]["message"], got["warning_card"]
    for name in missing:
        assert f"bindings.{name}" in message, (name, message)
    for wrong, meant in misnamed:
        assert f"bindings.{wrong} is not one of {instance['template']}'s bindings (did you mean {meant}?)" in message
    form = templates.REGISTRY[instance["template"]].bindings
    required = [name for name, field in form.model_fields.items() if field.is_required()]
    help_text = card["suggested_action"]
    assert f"{instance['template']} needs bindings {', '.join(required)}" in help_text, help_text
    examples = _instances_in(help_text)
    assert [example["template"] for example in examples] == [instance["template"]]
    _valid(examples[0])


def test_the_refusal_still_starts_with_the_first_problem_as_before(tmp_path):
    instance = WRITTEN[0][0]
    got = review_patch(tmp_path, task="t", requirements=REQUIREMENTS, template_instances=[instance])
    assert got["warning_card"]["message"].startswith("template_instances.0 (T1): bindings.relation: Field required")


def test_an_access_rule_refusal_lists_the_form_it_was_given(tmp_path):
    steps = {"id": "A1", "template": "access_rule.v1", "requirement_id": "R1", "intent": "change",
             "bindings": {"steps": {"grant": {"call": "shop.notes.grant", "effect": "allow"}}}}
    got = review_patch(tmp_path, task="t", requirements=REQUIREMENTS, template_instances=[steps])
    assert "bindings.constructor" in got["warning_card"]["message"]
    assert "with steps, access_rule.v1 needs bindings constructor" in got["warning_card"]["suggested_action"]


def test_the_skill_gives_one_valid_instance_of_each_template_it_names():
    named = set(re.findall(r"`([a-z_]+\.v\d+)`", SKILL))
    assert named == set(templates.REGISTRY)
    assert "references/templates.md" in SKILL
    examples = _instances_in(EXAMPLES_FILE.read_text(encoding="utf-8"))
    assert sorted(example["template"] for example in examples) == sorted(named)
    for example in examples:
        _valid(example)
    assert {example["template"]: example for example in examples} == templates.EXAMPLES
