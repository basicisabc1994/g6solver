# SPDX-License-Identifier: Apache-2.0
"""The generated catalogue and failure map never list a component built in a vetted area as guarding anything until
its certificate is current (handoff §4, ``g6.core.vetting``): it is shown as built, awaiting its certificate. No
qualified person's review is awaited (owner, 2026-10-23, DECISIONS #39)."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[2] / "tools"
CODE = "unchosen_model"
DECLARES = f'__g6_mitigates__ = ("{CODE}",)\n__g6_introduces__ = ()\n'
VETTED = f'''"""The vetted listing member."""
{DECLARES}__g6_vetting__ = {{"kinds": ("finance",), "register": "g6/listtest/vetted/register.json",
                  "independent_pass": "g6/listtest/vetted/review.json", "signed_review": None}}
'''


def _tool(name: str):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def listed(tmp_path, monkeypatch, registry_reset):
    import g6
    from g6.core import registry

    fam = tmp_path / "g6" / "listtest"
    for name, text in (("vetted", VETTED), ("plain", f'"""The plain listing member."""\n{DECLARES}')):
        (fam / name).mkdir(parents=True)
        (fam / name / "__init__.py").write_text(text, encoding="utf-8")
    for rel in ("register.json", "review.json"):
        (fam / "vetted" / rel).write_text("{}", encoding="utf-8")
    (fam / "__init__.py").write_text('__g6_family__ = "listtest"\n', encoding="utf-8")
    monkeypatch.setattr(g6, "__path__", [*g6.__path__, str(tmp_path / "g6")])
    monkeypatch.setattr(registry, "FAMILIES", registry.FAMILIES | {"listtest"})
    importlib.invalidate_caches()
    yield fam
    for name in [m for m in sys.modules if m == "g6.listtest" or m.startswith("g6.listtest.")]:
        del sys.modules[name]


@pytest.mark.parametrize("show_planned", [False, True], ids=["live only", "with planned"])
def test_the_failure_map_shows_it_as_awaiting_its_certificate_never_as_guarding(listed, show_planned):
    # the marker names the certificate, not a person's review (fails while render_md says "awaiting review")
    gen = _tool("gen_failure_map")
    mode = next(r for r in gen.collect() if r["code"] == CODE)
    assert "plain" in mode["guarded_by"] and "vetted" not in mode["guarded_by"]
    assert mode["awaiting_review"] == ["vetted"]
    md = gen.render_md(gen.collect(), show=show_planned)
    row = next(line for line in md.splitlines() if f"`{CODE}`" in line)
    assert "`vetted` (built, awaiting its certificate)" in row and "`plain`" in row and "awaiting review" not in md


def test_the_generated_failure_map_names_planned_packages_only_with_the_switch_on():
    # the site's one switch (web/pages/extensions.py SHOW_PLANNED) governs the generated page too (fails while
    # render_md always writes the "Planned in" column)
    gen = _tool("gen_failure_map")
    rows = gen.collect()
    planned = sorted({p for r in rows if not r["guarded_by"] for p in r["planned"]})
    assert planned and gen.show_planned() is False
    live, everything = gen.render_md(rows, show=False), gen.render_md(rows, show=True)
    assert "Planned in" in everything and all(f"`{p}`" in everything for p in planned)
    assert "planned" not in live.lower() and not [p for p in planned if f"`{p}`" in live]
    assert gen.render_md(rows) == live                                 # the default follows the switch


def test_the_catalogue_marks_it_as_awaiting_its_certificate(listed):
    gen = _tool("gen_catalog")
    rows = {r["name"]: r for r in gen.collect()}
    assert rows["vetted"]["vetting"] == "built, awaiting review" and rows["plain"]["vetting"] is None
    md = gen.render_md(list(rows.values()))
    assert "| `vetted` (built, awaiting its certificate) |" in md and "| `plain` |" in md
