# SPDX-License-Identifier: Apache-2.0
"""A component in a vetted area is listed as in the library only once its G6 certificate is current (handoff §4): the
declaration is read from source, and a missing or malformed one where the area needs it fails closed. No kind waits
for a qualified person's signed review (owner, 2026-10-23, DECISIONS #39)."""
from __future__ import annotations

import sys
import textwrap

import pytest

from g6.core import vetting


def _package(tmp_path, monkeypatch, name: str, body: str, files: tuple[str, ...] = ()) -> str:
    root = tmp_path / "src"
    (root / name).mkdir(parents=True)
    (root / name / "__init__.py").write_text(textwrap.dedent(body), encoding="utf-8")
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("{}", encoding="utf-8")
    monkeypatch.syspath_prepend(str(root))
    sys.modules.pop(name, None)
    return name


def _declaring(kinds: str = '("physical_safety",)', independent: str = '"pkg/review.json"', signed: str = "None") -> str:
    return f"""
        __g6_vetting__ = {{"kinds": {kinds}, "register": "pkg/register.json",
                          "independent_pass": {independent}, "signed_review": {signed}}}
        """


FILES = ("pkg/register.json", "pkg/review.json")


def test_a_package_outside_a_vetted_area_has_no_status(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, "plainpkg", '"""Nothing vetted."""\n')
    assert vetting.declared(pkg) is None
    assert vetting.problems(pkg) == []
    assert vetting.status(pkg) is None and vetting.awaiting_review(pkg) is False


def test_a_vetted_area_with_no_declaration_waits_for_review(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, "nodeclpkg", '"""Clinical, but says nothing."""\n')
    assert vetting.awaiting_review(pkg, required=("clinical",))
    assert any("declares no __g6_vetting__" in p for p in vetting.problems(pkg, required=("clinical",)))


def test_a_vetted_kind_waits_for_its_certificate(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, "pkg", _declaring(), FILES)
    assert vetting.problems(pkg, required=("physical_safety",)) == []
    assert vetting.status(pkg) == vetting.AWAITING == "built, awaiting review"


@pytest.mark.parametrize("kind", sorted(vetting.KINDS))
def test_a_current_certificate_lists_every_kind_without_a_signature(tmp_path, monkeypatch, kind):
    # owner, 2026-10-23 (DECISIONS #39): the certificate is the gate; no qualified person's signature is required
    # Fails for the clinical, mental-health, physical-safety, finance and political kinds while vetting.status keeps
    # its signed-review gate (``if SIGNED_KINDS.intersection(value["kinds"])``).
    from g6.core import component_certificate as records

    pkg = _package(tmp_path, monkeypatch, "pkg", _declaring(kinds=f"({kind!r},)"), FILES)
    current = {"path": "pkg/certificate/certificate.json", "state": "qualified-draft", "sha256": "0" * 64,
               "current": True, "not_checked": []}
    monkeypatch.setattr(records, "inspect", lambda package, today=None: (current, {"issuer": {}}, []))
    assert vetting.status(pkg, required=(kind,)) == vetting.REVIEWED
    assert not vetting.awaiting_review(pkg, required=(kind,))


def test_a_signed_review_without_a_certificate_still_waits(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, "pkg", _declaring(signed='"pkg/signed.md"'), (*FILES, "pkg/signed.md"))
    assert vetting.problems(pkg, required=("physical_safety",)) == []
    # Removing the certificate gate would admit an unbound signed review.
    assert vetting.status(pkg) == vetting.AWAITING


def test_a_kind_without_a_signed_review_also_needs_a_certificate(tmp_path, monkeypatch):
    done = _package(tmp_path, monkeypatch, "pkg", _declaring(kinds='("legal_regulatory",)'), FILES)
    # Removing the certificate gate would admit the old independent-pass-only record.
    assert vetting.status(done) == vetting.AWAITING
    sys.modules.pop("pkg", None)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text(
        textwrap.dedent(_declaring(kinds='("legal_regulatory",)', independent="None")), encoding="utf-8")
    assert vetting.status("pkg") == vetting.AWAITING


@pytest.mark.parametrize("body, problem", [
    ("KINDS = ('clinical',)\n__g6_vetting__ = {'kinds': KINDS}\n", "not a literal"),
    ('__g6_vetting__ = {"kinds": ("clinical",)}\n', "keys"),
    (_declaring(kinds='("astrology",)'), "unknown kind"),
    (_declaring(kinds="()"), "no kinds"),
    (_declaring(independent='"pkg/missing.json"'), "does not exist"),
    (_declaring(signed='"../../outside.md"'), "outside"),
])
def test_a_malformed_declaration_is_named_and_waits_for_review(tmp_path, monkeypatch, body, problem):
    pkg = _package(tmp_path, monkeypatch, "pkg", body, FILES)
    found = vetting.problems(pkg)
    assert any(problem in p for p in found), found
    assert vetting.awaiting_review(pkg)


def test_every_kind_the_area_needs_must_be_declared(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, "pkg", _declaring(kinds='("finance",)'), FILES)
    assert any("does not declare" in p and "clinical" in p
               for p in vetting.problems(pkg, required=("finance", "clinical")))
    assert vetting.awaiting_review(pkg, required=("finance", "clinical"))


def test_it_is_read_without_importing_the_package(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, "pkg", "raise RuntimeError('imported')\n" + textwrap.dedent(_declaring()),
                   FILES)
    assert vetting.declared(pkg)["kinds"] == ("physical_safety",)
    assert "pkg" not in sys.modules


def test_the_summary_says_what_is_missing(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, "pkg", _declaring(), FILES)
    assert vetting.summary(pkg) == {"kinds": ["physical_safety"], "status": "built, awaiting review",
                                    "independent_pass": False, "signed_review": False, "problems": []}
    assert vetting.summary("g6.core") is None


VETTED_MEMBER = '''"""The vetted test member."""
__g6_vetting__ = {"kinds": ("clinical",), "register": "g6/vettest/vetted/register.json",
                  "independent_pass": "g6/vettest/vetted/review.json", "signed_review": None}
'''


@pytest.fixture
def vetted_family(tmp_path, monkeypatch, registry_reset):
    import importlib

    import g6
    from g6.core import registry

    fam = tmp_path / "g6" / "vettest"
    for name, text in (("vetted", VETTED_MEMBER), ("unvetted", '"""The unvetted test member."""\n')):
        (fam / name).mkdir(parents=True)
        (fam / name / "__init__.py").write_text(text, encoding="utf-8")
    for rel in ("register.json", "review.json"):
        (fam / "vetted" / rel).write_text("{}", encoding="utf-8")
    (fam / "__init__.py").write_text('__g6_family__ = "vettest"\n', encoding="utf-8")
    monkeypatch.setattr(g6, "__path__", [*g6.__path__, str(tmp_path / "g6")])
    monkeypatch.setattr(registry, "FAMILIES", registry.FAMILIES | {"vettest"})
    importlib.invalidate_caches()
    yield fam
    for name in [m for m in sys.modules if m == "g6.vettest" or m.startswith("g6.vettest.")]:
        del sys.modules[name]


def test_describe_says_a_vetted_component_is_awaiting_review(vetted_family):
    from g6.core import discovery
    from g6.mcp import describe_reply

    assert discovery.describe("vetted")["vetting"] == {"kinds": ["clinical"], "status": "built, awaiting review",
                                                       "independent_pass": False, "signed_review": False,
                                                       "problems": []}
    assert describe_reply("vetted")["vetting"]["status"] == "built, awaiting review"
    assert discovery.describe("unvetted")["vetting"] is None


def test_the_kinds_are_the_trackers():
    assert vetting.KINDS == {"clinical", "mental_health", "physical_safety", "finance", "political",
                             "legal_regulatory", "scientific_statistical"}
    # no kind needs a signature to be listed any more (DECISIONS #39): fails while vetting keeps SIGNED_KINDS
    assert not hasattr(vetting, "SIGNED_KINDS")


def test_every_vetted_component_says_plainly_that_no_qualified_person_has_reviewed_it():
    # owner, 2026-10-23: the review is dropped, the honesty stays; each vetted component's skill says so in one plain
    # sentence (fails until every skill carries vetting.NO_QUALIFIED_REVIEW)
    from pathlib import Path

    from g6.core.registry import get_registry

    reg = get_registry()
    vetted = sorted(n for n in reg.names() if vetting.declared(reg.refs[n].module) is not None)
    assert {"adapt_sklearn", "adapt_automl", "adapt_bayesian", "adapt_physical_ai"} <= set(vetted)
    for name in vetted:
        skill = Path(reg.get_module(name).__file__).parent / "skill" / "SKILL.md"
        text = " ".join(skill.read_text(encoding="utf-8").split())
        assert vetting.NO_QUALIFIED_REVIEW in text, name
