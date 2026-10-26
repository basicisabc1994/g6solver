# SPDX-License-Identifier: Apache-2.0
"""Skills: the ``SKILL.md`` an agent loads to use G6 well, shipped as package data (FW-01).

A component's skill is ``<its package>/skill/SKILL.md``, installed for an agent as ``g6-<component>`` (hyphens for
underscores, as skill names allow). G6's own guide, ``using-g6``, and the vendored superpowers skills
(``vendor/superpowers/``) live in the ``g6.skills`` package. Each is found from its package's folder, so a skill is
read without importing its component, from a wheel or a source checkout.

A skill's ``## Ops`` section lists one op per line, ``- `op`: what it does``, matching the block's own ``ops`` answer;
each worked example is a fenced block::

    ```g6-example op=<op>
    {"the": "op's params, as one JSON object"}
    ```

which the tests send through ``g6_invoke``.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from g6.core.registry import get_registry

GUIDE = "using-g6"
#: The sections every component's skill has, in this order (D7, FW-01); ``tests/core/test_component_skills.py`` holds
#: every registered skill to them.
SECTIONS = ("When to use it", "When not to use it", "Alone and composed", "Failure modes it catches", "Blind spots",
            "What it needs", "Examples", "The four open problems")
#: What the last section says something about, each by name (framework design section 5).
OPEN_PROBLEMS = ("Learning signals", "Evaluators the model cannot influence", "Transfer by failure mode",
                 "Enough held-out cases")
#: The most words a skill has.
MAX_WORDS = 1800
_OPS =re.compile(r"^## Ops[ \t]*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
_OP_LINE = re.compile(r"^- `([a-z_][a-z0-9_]*)`", re.MULTILINE)
_EXAMPLE = re.compile(r"^```g6-example op=([a-z_][a-z0-9_]*)[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_API_LINE = re.compile(r"^- `([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)`", re.MULTILINE)
_PYTHON = re.compile(r"^```python[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


def skill_name(component: str) -> str:
    """The name a component's skill is installed under: ``g6-`` and the component's name, hyphens for underscores."""
    return "g6-" + component.replace("_", "-")


def _package_dir(module: str) -> Path | None:
    spec = importlib.util.find_spec(module)
    locations = list(spec.submodule_search_locations or ()) if spec else []
    return Path(locations[0]) if locations else None


def skill_dir(component: str) -> Path | None:
    """The folder holding a component's ``SKILL.md`` (and any references), or ``None`` when it has no skill. Raises
    ``KeyError`` for an unknown component."""
    root = _package_dir(get_registry().refs[component].module.split(":", 1)[0])
    found = root / "skill" if root else None
    return found if found is not None and (found / "SKILL.md").is_file() else None


def skill_text(component: str) -> str | None:
    """A component's ``SKILL.md``, or ``None`` when it has no skill."""
    found = skill_dir(component)
    return (found / "SKILL.md").read_text(encoding="utf-8") if found else None


def components_with_skills() -> list[str]:
    """Every registered component that ships a skill, by name."""
    return [name for name in get_registry().names() if skill_dir(name)]


def _skills_root() -> Path:
    root = _package_dir("g6.skills")
    if root is None:
        raise FileNotFoundError("the g6.skills package is missing from this installation")
    return root


def guide_dir() -> Path:
    """The ``using-g6`` skill's folder."""
    return _skills_root() / GUIDE


def vendor_dir() -> Path:
    """The vendored superpowers skills' folder (with their ``LICENSE`` and ``VENDORED.md``)."""
    return _skills_root() / "vendor" / "superpowers"


def vendored() -> dict[str, Path]:
    """Each vendored superpowers skill's folder, by skill name."""
    return {d.name: d for d in sorted(vendor_dir().iterdir()) if (d / "SKILL.md").is_file()}


def front_matter(text: str) -> dict[str, str]:
    """The ``key: value`` lines between a skill's opening ``---`` lines; ``{}`` when it has none."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not line[:1].isspace():
            value = value.strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            out[key.strip()] = value
    return out


def skill_ops(text: str) -> list[str]:
    """The ops a skill's ``## Ops`` section lists, in order; ``[]`` when it has none."""
    found = _OPS.search(text)
    return _OP_LINE.findall(found.group(1)) if found else []


def section(text: str, heading: str) -> str:
    """The body of a skill's ``## <heading>`` section, up to the next ``## `` heading; ``""`` when it has none."""
    found = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return found.group(1).strip("\n") + "\n" if found and found.group(1).strip() else ""


def skill_api(text: str) -> list[str]:
    """The names a blockless component's ``## Python API`` section lists (``- `name```, dotted for a submodule's),
    in order; ``[]`` when it has none."""
    return _API_LINE.findall(section(text, "Python API"))


def skill_python_examples(text: str) -> list[str]:
    """Each fenced ``python`` block in a skill's ``## Examples`` section, which the tests run."""
    return _PYTHON.findall(section(text, "Examples"))


def skill_examples(text: str) -> list[tuple[str, dict]]:
    """Each ``g6-example`` block as ``(op, params)``. Raises ``ValueError``, naming the op, for one whose body is not a
    JSON object."""
    out = []
    for op, body in _EXAMPLE.findall(text):
        try:
            params = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"the g6-example for {op} is not JSON: {exc}") from exc
        if not isinstance(params, dict):
            raise ValueError(f"the g6-example for {op} is not a JSON object")
        out.append((op, params))
    return out
