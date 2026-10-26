# SPDX-License-Identifier: Apache-2.0
"""The using-g6 skill G6 ships stays true to the library: every component, op, field and Python name it gives an agent
exists, every superpowers skill it pairs a stage with is vendored, and it stays short. The skill was tested on agents
before it shipped (docs/skills/using-g6/TESTING.md); re-run those tests whenever it changes."""
from __future__ import annotations

import importlib
import re
import typing

from g6.core import discovery, skills
from g6.core.registry import get_registry

GUIDE = skills.guide_dir()
TEXT = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
LIFECYCLE = (GUIDE / "references" / "lifecycle.md").read_text(encoding="utf-8")
BOTH = TEXT + "\n" + LIFECYCLE
#: The ceiling for SKILL.md's body. The tested version is about 900 words (TESTING.md, "Limits of this test").
MAX_WORDS = 1000


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
    assert len(pairs) >= 10
    for component, op in pairs:
        assert component in names, component
        assert _offers(component, op), (component, op)
    # lists of components, such as "`formal`, `algebra`, ... or `lean`" (ops in parentheses and tool names aside)
    unbracketed = re.sub(r"\([^)]*\)", "", BOTH)
    runs = re.findall(r"`[a-z_]+`(?:\s*(?:,|or|and)\s*`[a-z_]+`)+", unbracketed)
    listed = {t for run in runs for t in re.findall(r"`([a-z_]+)`", run) if not t.startswith(("g6_", "metaharness_"))}
    assert len(listed) >= 10 and listed <= names, listed - names


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
    import g6.mcp

    source = open(g6.mcp.__file__, encoding="utf-8").read()
    for tool in set(re.findall(r"`(g6_[a-z]+|metaharness_[a-z]+)`", BOTH)):
        assert f"def {tool}(" in source, tool
    from g6.metaharness import schema, surface

    assert "G6_E_META_APPROVER_IS_PROPOSER" in TEXT and schema.APPROVER_IS_PROPOSER == "G6_E_META_APPROVER_IS_PROPOSER"
    assert "_blocked(APPROVER_IS_PROPOSER" in open(surface.__file__, encoding="utf-8").read()  # the MCP door refuses


def test_every_superpowers_skill_it_pairs_a_stage_with_is_vendored() -> None:
    vendored = set(skills.vendored())
    named = set(re.findall(r"`([a-z]+(?:-[a-z]+)+)`", LIFECYCLE)) - {"qualified-draft"}
    named |= {n for n in ("brainstorming",) if f"`{n}`" in LIFECYCLE}
    assert len(named) >= 12 and named <= vendored, named - vendored
    assert "superpowers:using-superpowers" in TEXT and "using-superpowers" in vendored


def test_it_stays_short() -> None:
    body = TEXT.split("---", 2)[2]
    assert len(body.split()) <= MAX_WORDS
