# SPDX-License-Identifier: Apache-2.0
"""Package checks for audit 016 (navigator -> g6.navigator): what ships, its licence header, its dependencies, and
the archive modules that were dropped with their findings."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

import g6.navigator

PACKAGE = Path(g6.navigator.__file__).parent
_HEADER = "# SPDX-License-Identifier: Apache-2.0"
_BANNED = ("Proprie" + "tary", "All rights " + "reserved", "Pty " + "Ltd")
_HOST_PATH = re.compile(r"[A-Za-z]:\\\\?Users|/home/[a-z]|/Users/[A-Za-z]")


def shipped_problems(directory: Path) -> list[str]:
    """What is wrong with a component tree as shipped: a data file (the archive's census and purposes snapshots), a
    file without the Apache-2.0 header, a closed-licence notice, or an absolute host path."""
    problems = []
    for path in sorted(p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        if path.suffix != ".py":
            if path.suffix == ".md" and "skill" in path.relative_to(directory).parts[:-1]:
                continue  # the component's skill ships as package data (FW-01)
            problems.append(f"{path.name}: ships a data file")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.startswith(_HEADER):
            problems.append(f"{path.name}: no Apache-2.0 SPDX header on line 1")
        problems += [f"{path.name}: carries {word!r}" for word in _BANNED if word in text]
        if _HOST_PATH.search(text):
            problems.append(f"{path.name}: carries an absolute host path")
    return problems


def test_nav016_nav011_the_package_ships_open_source_code_and_no_census_snapshot() -> None:
    # Archive: every file began with a closed-licence notice and license_status was unknown (NAV-016), and the package
    # shipped census_levels.json (5,142 lines, 99% unqualified, with Windows home paths) and op_purposes.json (NAV-011).
    assert shipped_problems(PACKAGE) == []
    assert shipped_problems(Path(__file__).parent) == []


def test_the_package_declares_exactly_what_it_imports() -> None:
    assert g6.navigator.__g6_requires__ == ("workforce",)
    assert g6.navigator.__g6_maturity__ == "beta"


def test_the_docstring_states_the_label_and_what_was_not_ported() -> None:
    doc = g6.navigator.__doc__ or ""
    assert "(audit 016 §11)" in doc and "Capability label: ``implemented``" in doc
    offered, _, dropped = doc.partition("Not ported from the archive")
    assert dropped and "verified" in offered and "nav_self_heal" in dropped


@pytest.mark.parametrize("module", ["op_catalog", "door_authority", "composer", "effect_checker", "navigator_mcp",
                                    "navigator_block", "registry", "catalog_data", "routing_engine", "fallback",
                                    "state_machine", "agency_tracker", "mcp_lifecycle", "healing_handlers",
                                    "meta_handlers", "monitoring_handlers", "introspection_handlers", "patterns",
                                    "skills"])
def test_dropped_archive_modules_are_not_ported(module: str) -> None:
    # NAV-002 (self-heal), NAV-004 (clusters), NAV-005/006 (recommend, search), NAV-008/015 (lifecycle), NAV-009
    # (headless CLI), NAV-010 (graph, stats), NAV-012 (agency), NAV-013 (routing) and NAV-014 (introspection) went
    # with these files; op_catalog, door_authority, composer and effect_checker were rewritten under other names.
    assert importlib.util.find_spec(f"g6.navigator.{module}") is None


def test_the_archive_skill_module_is_not_ported_only_the_components_skill_ships() -> None:
    # The archive's skill module was dropped; skill/ holds only the component's SKILL.md (package data, FW-01).
    folder = Path(g6.navigator.__file__).parent / "skill"
    assert sorted(p.name for p in folder.iterdir() if p.name != "__pycache__") == ["SKILL.md"]
