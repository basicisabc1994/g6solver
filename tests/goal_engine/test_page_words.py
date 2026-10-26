# SPDX-License-Identifier: Apache-2.0
"""GOAL_ENGINE-01 (audit 2026-10-23): the components page said the package "Breaks a goal into a tree of subtasks"; it
generates no subtask. Its own words (the docstring's first line, which the site reads, and its skill) and the page's
plain words now say what it does."""
from __future__ import annotations

import ast
from pathlib import Path

import g6.goal_engine

WORDS = "Holds the subtasks you declare as a tree and checks them against what your executor reports"
CATALOGUE = Path(__file__).resolve().parents[2] / "web" / "pages" / "catalogue.py"


def plain(name: str) -> tuple[str, str, str]:
    """The site's plain words for ``name`` (``pages.catalogue.PLAIN``), read from its source without Django."""
    for node in ast.parse(CATALOGUE.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "PLAIN":
            return ast.literal_eval(node.value)[name]
    raise AssertionError("pages/catalogue.py has no PLAIN")


def test_the_package_says_it_holds_declared_subtasks_and_breaks_no_goal_down() -> None:
    # fails if goal_engine/__init__.py's first docstring line, its skill's opening or PLAIN["goal_engine"] loses these
    # words, or any of them says again that it breaks a goal into subtasks
    first = (g6.goal_engine.__doc__ or "").strip().splitlines()[0]
    summary = first.split("—", 1)[-1].strip().rstrip(".")
    skill = " ".join((Path(g6.goal_engine.__file__).parent / "skill" / "SKILL.md").read_text(encoding="utf-8").split())
    title, does, _ = plain("goal_engine")
    assert summary.lower() == WORDS.lower() and does == WORDS and WORDS.lower() in skill.lower()
    for text in (first, does, skill):
        assert "breaks a goal" not in text.lower(), text[:120]
