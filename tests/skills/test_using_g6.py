# SPDX-License-Identifier: Apache-2.0
"""The using-g6 skill G6 ships stays true to the library: every component, op, field and Python name it gives an agent
exists, every superpowers skill it pairs a stage with is vendored, and it stays short. The skill was tested on agents
before it shipped (docs/skills/using-g6/TESTING.md); re-run those tests whenever it changes."""
from __future__ import annotations

import importlib
import re
import typing
from pathlib import Path

from g6.core import discovery, skills
from g6.core.registry import get_registry

GUIDE = skills.guide_dir()
TEXT = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
LIFECYCLE = (GUIDE / "references" / "lifecycle.md").read_text(encoding="utf-8")
BOTH = TEXT + "\n" + LIFECYCLE
#: The ceiling for SKILL.md's body: the cap every skill has (0.3 design, 14.1: the 1,800-word cap holds). The version
#: tested on agents was about 900 words (TESTING.md, "Limits of this test"); the longer one is not yet measured (the
#: routing measurement under validation/routing is built and has not been run).
MAX_WORDS = skills.MAX_WORDS


def _resolve(dotted: str) -> object:
    """The module, or the attribute of the longest importable module prefix, that ``dotted`` names."""
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        for attr in parts[i:]:
            obj = getattr(obj, attr)
        return obj
    raise ImportError(dotted)


def _offers(component: str, name: str) -> bool:
    """``name`` is one of the component's ops, or (for a ``*`` pattern) a prefix of one, or part of its Python API."""
    ops = discovery.ops(component)
    if name.endswith("*"):
        return any(op.startswith(name[:-1]) for op in ops)
    return name in ops or hasattr(importlib.import_module(get_registry().refs[component].module), name)


def test_it_names_itself_and_says_when_to_use_it() -> None:
    meta = skills.front_matter(TEXT)
    assert meta["name"] == "using-g6" and meta["description"].startswith("Use when")


def test_every_g6_invoke_names_a_real_op_and_only_fields_its_input_takes() -> None:
    calls = re.findall(r'g6_invoke\("(\w+)", "(\w+)"(?:, \{([^}]*)\})?', TEXT)
    assert len(calls) >= 3
    reg = get_registry()
    for component, op, fields in calls:
        assert op in discovery.ops(component), (component, op)
        block = reg.get_class(component)()
        model = next(iter(typing.get_type_hints(block.infer).values()))
        named = set(re.findall(r'"(\w+)"', fields))
        assert named <= set(model.model_fields), (component, op, named - set(model.model_fields))


def test_every_component_and_op_it_names_exists() -> None:
    names = set(get_registry().names())
    pairs = [(c, o) for c, o in re.findall(r"`([a-z_]+)\.([a-z_]+)`", BOTH)]
    for component, listed in re.findall(r"`([a-z_]+)` \(([^)]*)\)", BOTH):
        pairs += [(component, o) for o in re.findall(r"`([a-z_*]+)`", listed)]
    # Mapping placeholders and the host permission namespace are not components.
    pairs = [(c, o) for c, o in pairs if c not in {"caller", "patch_review"}]
    assert pairs
    for component, op in pairs:
        assert component in names, component
        assert _offers(component, op), (component, op)
    # lists of components, such as "`formal`, `algebra`, ... or `lean`" (ops in parentheses and tool names aside)
    unbracketed = re.sub(r"\([^)]*\)", "", BOTH)
    runs = re.findall(r"`[a-z_]+`(?:\s*(?:,|or|and)\s*`[a-z_]+`)+", unbracketed)
    listed = {t for run in runs for t in re.findall(r"`([a-z_]+)`", run) if not t.startswith(("g6_", "metaharness_"))}
    assert listed and listed <= names, listed - names


def test_every_python_name_it_gives_exists() -> None:
    dotted = re.findall(r"`(g6\.[a-z_.]+?)(?:\(|`)", BOTH)
    dotted += [f"g6.{m}.{n.strip()}" for m, ns in re.findall(r"from g6\.([a-z_]+) import ([a-z_, ]+)", BOTH)
               for n in ns.split(",")]
    dotted += [f"g6.metaharness.{n}" for n in ("Workflow", "Suite", "adapt")]
    dotted += [f"g6.metaharness.api.MetaHarness.{n}" for n in ("activate", "baseline")]
    assert len(dotted) >= 10
    for name in dotted:
        assert _resolve(name) is not None, name


def test_every_tool_and_code_it_names_is_real() -> None:
    from g6.mcp import registrations

    registered = set(registrations.recorded()["g6"])   # what the server registers, wherever the tool is defined
    named = set(re.findall(r"`(g6_[a-z_]+|metaharness_[a-z_]+)`", BOTH))
    assert named and named <= registered, named - registered
    assert "Client-mediated consent may occur in the proposing session" in TEXT
    assert "recorded name is not authenticated" in TEXT
    assert "host-configured safety/injection evaluator" in TEXT
    # Actual approval behavior is exercised through MCP in test_phase6_mcp,
    # including proposing-session consent, decline and changed digest rejection.


def test_every_superpowers_skill_it_pairs_a_stage_with_is_vendored() -> None:
    vendored = set(skills.vendored())
    named = set(re.findall(r"`([a-z]+(?:-[a-z]+)+)`", LIFECYCLE)) - {"qualified-draft"}
    named |= {n for n in ("brainstorming",) if f"`{n}`" in LIFECYCLE}
    assert len(named) >= 12 and named <= vendored, named - vendored
    assert "optional companions, not prerequisites" in TEXT and "using-superpowers" in vendored


def test_it_names_only_templates_review_parameters_and_bindings_that_exist() -> None:
    # owner, 2026-10-29: the delivery process names G6's templates; each must be one G6 has, and each parameter or
    # binding it names one the review takes, never an option that does not exist
    import g6.mcp
    from g6.harness import verification_templates as templates

    known = set(templates.TEMPLATES) | set(templates.FORMAL_TEMPLATES)
    named = set(re.findall(r"`([a-z_]+\.v\d+)`", TEXT))
    assert known <= named and named <= known, (known - named, named - known)
    # the tools live in more than one module of g6.mcp (the Context Engine's are in g6/mcp/knowledge.py)
    source = "\n".join(p.read_text(encoding="utf-8") for p in sorted(Path(g6.mcp.__file__).parent.glob("*.py")))
    signature = re.search(r"def g6_review_patch\((.*?)\) -> dict", source, re.S).group(1)
    parameters = set(re.findall(r"(\w+):", signature))
    forms = [definition.bindings for definition in {**templates.TEMPLATES, **templates.FORMAL_TEMPLATES}.values()]
    forms += [templates.AccessBindings, templates.AccessStepsBindings]
    fields = {name for form in forms if hasattr(form, "model_fields") for name in form.model_fields}
    section = TEXT.split("## Deliver a coding task with G6", 1)[1].split("\n## ", 1)[0]
    words = set(re.findall(r"`([a-z_]+)`", section)) - {"verified"}
    tools = {word for word in words if word.startswith(("g6_", "metaharness_"))}
    assert {"intent", "requirements", "template_instances", "proposed_checks"} <= words
    assert words - tools <= parameters | fields, (words - tools) - (parameters | fields)


def test_a_review_starts_from_its_three_inputs_and_the_optional_ones_wait_for_their_shapes() -> None:
    """The owner's pilot (2026-10-30): both agents' first review calls were refused, Codex's for an intent in the wrong
    shape, Claude Code's for a template instance's bindings; each fixed its call only after the refusal."""
    section = " ".join(TEXT.split("## Deliver a coding task with G6", 1)[1].split("\n## ", 1)[0].split())
    assert ("Call `g6_review_patch` with the folder, the task and the requirements, then poll `metaharness_job`."
            in section), section
    assert ("Add optional inputs (`intent`, `template_instances`, `proposed_checks`) only in the shapes the tool's "
            "description gives; a refused call names the field to fix.") in section
    assert "Pass them as `intent`" not in section


def test_after_a_no_in_the_form_the_agent_asks_and_offers_no_other_way() -> None:
    assert "After a decline or cancel, ask the person; offer no other way to confirm." in " ".join(TEXT.split())


def test_it_stays_short() -> None:
    body = TEXT.split("---", 2)[2]
    assert len(body.split()) <= MAX_WORDS


def _guidance() -> dict[str, str]:
    from g6.mcp import registrations

    return registrations.guidance()


def _route_section(route: str) -> str:
    from g6.skills import routing

    found = routing.section(TEXT, routing.load().routes[route].anchor)
    assert found is not None, route
    return " ".join(found.split())


def test_one_question_is_the_default_and_three_is_gone() -> None:
    """0.3 design 14.1: the guidance carries A's persistent one-question default in the commit that routes the
    question tool, and no guidance text still tells an agent to ask three."""
    for surface, text in _guidance().items():
        assert not re.search(r"(at most|up to) (3|three) questions", " ".join(text.split())), surface
    deliver = _route_section("review")
    assert "One question per task by default, kept across calls." in deliver
    assert "`g6_confirm_behaviour`" in deliver
    assert "may allow one more question" in deliver and "set it to none" in deliver
    assert "an unanswered question stays open in the report" in deliver
    question = _route_section("question")
    assert "one question per task by default" in question and "may allow more or set none" in question
    assert "one question per task by default" in " ".join(LIFECYCLE.split())


def test_the_question_has_no_fallback() -> None:
    """A has no terminal fallback and no agent-supplied answer: its route offers no other way to an answer."""
    question = _route_section("question")
    for offered in ("python -P -m", "terminal", "relay the", "command"):
        assert offered not in question, offered
    assert "Only the person answers; you never answer for them" in question
    deliver = _route_section("review")
    assert "Only the person answers: no argument answers and you never answer for them." in deliver
    step = deliver.split("**Hidden constraints.**", 1)[1].split("**Acceptance first", 1)[0]
    assert "terminal" not in step and "relay" not in step


def test_fallback_text_is_not_shared_across_routes() -> None:
    """No other component's approval fallback is copied: the one route whose section relays a terminal command is the
    meta-harness's own. The routes are read from the manifest, so a new route that copies the text fails here."""
    from g6.skills import routing

    fallback = re.compile(r"terminal command|relay the", re.IGNORECASE)
    relays = {route for route in routing.load().routes if fallback.search(_route_section(route))}
    assert relays == {"fit"}, relays
