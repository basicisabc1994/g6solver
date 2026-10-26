# SPDX-License-Identifier: Apache-2.0
"""Every registered component ships a current skill (D7, FW-01), and the skill agrees with the code: its ops are the
block's own ``ops`` answer (or, with no block, its Python API names exist), it names every failure mode the component
declares it guards against or can cause and everything it needs from outside G6, it says what it does about the four
open problems (framework design section 5), and its examples run: each ``g6-example`` through ``g6_invoke``, each
Python example offline, with no model, in a scratch folder."""
from __future__ import annotations

import importlib
import json
import re
import socket
from pathlib import Path

import pytest

from g6.core import discovery, skills
from g6.core.registry import get_registry
from g6.mcp import describe_reply, invoke_reply

NAMES = get_registry().names()
SECTIONS, OPEN_PROBLEMS, MAX_WORDS = skills.SECTIONS, skills.OPEN_PROBLEMS, skills.MAX_WORDS
#: Names from the earlier hosted G6; a skill teaches the library's own surface (audit method R10).
OLD_NAMES = ("mvp.", "invoke_component", "list_components", "describe_component", "guide_find_tool", "nav_discover",
             "g6_first_call_help_tool")
#: A run of base64 this long in a skill's prose is data pasted where words belong (a 4-pixel PNG is 96 characters).
PASTED = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def _text(name: str) -> str:
    text = skills.skill_text(name)
    assert text is not None, f"{name} ships no skill/SKILL.md"
    return text


def _package(name: str) -> str:
    return get_registry().refs[name].module.split(":", 1)[0]


def _resolve(package: str, dotted: str) -> object:
    found: object = importlib.import_module(package)
    for part in dotted.split("."):
        if hasattr(found, part):
            found = getattr(found, part)
        else:
            found = importlib.import_module(f"{found.__name__}.{part}")  # type: ignore[attr-defined]
    return found


@pytest.mark.parametrize("name", NAMES)
def test_the_front_matter_names_the_skill_and_says_when_to_use_it(name: str) -> None:
    meta = skills.front_matter(_text(name))
    assert meta.get("name") == skills.skill_name(name)
    assert meta.get("description", "").startswith("Use when") and len(meta["description"]) <= 1024


@pytest.mark.parametrize("name", NAMES)
def test_the_sections_are_all_there_in_order(name: str) -> None:
    text = _text(name)
    at = [m.start() if (m := re.search(rf"^## {re.escape(s)}[ \t]*$", text, re.MULTILINE)) else -1 for s in SECTIONS]
    assert -1 not in at, [s for s, a in zip(SECTIONS, at) if a == -1]
    assert at == sorted(at), "the sections are out of order"
    assert all(skills.section(text, s).strip() for s in SECTIONS)


@pytest.mark.parametrize("name", NAMES)
def test_the_ops_are_the_blocks_own_or_the_python_api_exists(name: str) -> None:
    text, ops = _text(name), discovery.ops(name)
    listed = skills.skill_ops(text)
    if ops:
        assert sorted(listed) == sorted(ops) and len(listed) == len(set(listed)), (listed, ops)
    else:
        assert not listed and skills.skill_api(text), f"{name} has no block, so its skill lists its Python API"
    for dotted in skills.skill_api(text):
        _resolve(_package(name), dotted)


@pytest.mark.parametrize("name", NAMES)
def test_it_names_the_failure_modes_it_catches_and_can_cause(name: str) -> None:
    text, info = _text(name), describe_reply(name)
    catches, blind = skills.section(text, "Failure modes it catches"), skills.section(text, "Blind spots")
    assert [m["code"] for m in info["mitigates"] if f"`{m['code']}`" not in catches] == []
    assert [m["code"] for m in info["introduces"] if f"`{m['code']}`" not in blind] == []


@pytest.mark.parametrize("name", NAMES)
def test_it_names_everything_it_needs_from_outside(name: str) -> None:
    needs, info = skills.section(_text(name), "What it needs"), describe_reply(name)
    assert [p["name"] for p in info["outside"] if p["name"] not in needs] == []
    assert [e["extra"] for e in info["extras"] if f"g6[{e['extra']}]" not in needs] == []


@pytest.mark.parametrize("name", NAMES)
def test_it_says_what_it_does_about_the_four_open_problems(name: str) -> None:
    body = skills.section(_text(name), "The four open problems")
    assert [p for p in OPEN_PROBLEMS if p not in body] == []


@pytest.mark.parametrize("name", NAMES)
def test_it_teaches_the_librarys_own_surface_briefly(name: str) -> None:
    text = _text(name)
    assert [old for old in OLD_NAMES if old in text] == []
    assert len(text.split()) <= MAX_WORDS


@pytest.mark.parametrize("name", NAMES)
def test_its_prose_has_no_data_pasted_into_it(name: str) -> None:
    # outside the fenced examples; a hash in one case only (hex) is a name, mixed-case runs are pasted data
    prose = re.sub(r"^```.*?^```", "", _text(name), flags=re.MULTILINE | re.DOTALL)
    pasted = [m.group(0)[:24] for m in PASTED.finditer(prose) if re.search("[A-Z]", m.group(0))
              and re.search("[a-z]", m.group(0))]
    assert pasted == []


@pytest.mark.parametrize("name", NAMES)
def test_its_examples_run_through_g6_invoke(name: str) -> None:
    text, ops = _text(name), discovery.ops(name)
    examples = skills.skill_examples(text)
    if not ops:
        assert not examples, f"{name} has no block, so its examples are Python"
        return
    assert examples, f"{name} has a block, so its skill shows at least one g6-example"
    sensitive = set(describe_reply(name).get("sensitive_ops") or ())
    for op, params in examples:
        assert op in ops, f"{name}: the example's op {op!r} is not one of the block's ops"
        if op in sensitive:
            continue  # it runs a program on this machine; it runs over MCP only when the server's owner allows it
        reply = invoke_reply(name, op, json.dumps(params))
        assert reply["ok"], (op, reply.get("warning_card"))


@pytest.mark.parametrize("name", NAMES)
def test_its_python_examples_run_offline(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code = skills.skill_python_examples(_text(name))
    if not discovery.ops(name):
        assert code, f"{name} has no block, so its skill shows at least one Python example"
    missing = [e["extra"] for e in describe_reply(name)["extras"] if not e["installed"]]

    def offline(*args: object, **kwargs: object) -> None:
        raise OSError("skill examples run offline")

    monkeypatch.setattr(socket.socket, "connect", offline)
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for index, block in enumerate(code):
        try:
            exec(compile(block, f"<{name} skill example {index + 1}>", "exec"), {"__name__": "__skill_example__"})
        except Exception as exc:
            if not missing:
                raise
            # the conformance job installs every extra and runs it there
            pytest.skip(f"{name}'s example {index + 1} fails where g6[{','.join(missing)}] is not installed: "
                        f"{type(exc).__name__}")
