# SPDX-License-Identifier: Apache-2.0
"""Component certificates: real offline executions and deliberately broken inputs."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import conformance_kit as kit
import pytest

from g6.core import confine, vetting
from g6.core import component_certificate as records
from g6.workforce import component_certificate as certificates

TODAY = dt.date(2026, 10, 22)
HEADER = "# SPDX-License-Identifier: Apache-2.0\n"
PACKAGE = "g6.certfixture.inches"
PREFIX = "g6/certfixture/inches"
IMPL = PREFIX + "/implementation.py"
CLAUSE = "inch; metre; 2.54 E-02; so 12 inches are 0.3048 metre, and 0.5 foot (6 inches) is 0.1524 metre"
QUOTE = "so 12 inches are 0.3048 metre"
RULE_CLASS = '''
from dataclasses import dataclass
@dataclass(frozen=True)
class Rule:
    rule: str
    kind: str
    source: str
    effect: str = ""
    tier: str = ""
    implements: str = ""
    own_choice: bool = False
'''
INCH = f'Rule("inch to metre", "register", "nist-inch", tier="computes", implements="{IMPL}:convert")'
ADAPTER = PREFIX + "/certificate/adapter.py"


@pytest.fixture(autouse=True)
def _lf_text(monkeypatch):
    """Every fixture file is written as an LF checkout holds it, on every machine: Windows' ``write_text`` would turn
    each newline into CRLF, which the issuer refuses (``records.lf_only``). The issuer itself writes bytes."""
    write = Path.write_text

    def lf(self, data, encoding=None, errors=None, newline="\n"):
        return write(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", lf)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"SPDX-License-Identifier": "Apache-2.0", **value}, sort_keys=True, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))


def rules(path, table):
    """Write a rule table: name -> the Rule(...) source."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + RULE_CLASS + "RULES = {" + ", ".join(f'"{k}": {v}' for k, v in table.items()) + "}\n",
                    encoding="utf-8")


def finding(rule, **extra):
    return {"rule": rule, "code_matches": True, "clause_fair": True,
            "source_sha256": hashlib.sha256(CLAUSE.encode()).hexdigest(), "finding": "The code matches the clause.",
            **extra}


@pytest.fixture
def component(tmp_path, monkeypatch, registry_reset):
    import g6
    from g6.core import registry

    root = tmp_path / "src"
    folder = root / PREFIX
    folder.mkdir(parents=True)
    (folder.parent / "__init__.py").write_text(HEADER + '__g6_family__ = "certfixture"\n', encoding="utf-8")
    declaration = {"kinds": ("scientific_statistical",), "register": PREFIX + "/data/source_register.json",
                   "independent_pass": PREFIX + "/data/rule_review.json", "signed_review": None}
    (folder / "__init__.py").write_text(HEADER + "__g6_vetting__ = " + repr(declaration) + "\n", encoding="utf-8")
    (folder / "implementation.py").write_text(
        HEADER + "def convert(x):\n    return float(x) * 0.0254\n", encoding="utf-8")
    rules(folder / "sources.py", {"inch": INCH})
    put(folder / "data/source_register.json", {"entries": [{
        "id": "nist-inch", "issuing_body": "NIST", "title": "Guide for the Use of the International System of Units",
        "edition": "SP 811, 2008", "published": "2008", "jurisdiction": "unit conversion",
        "url": "https://www.nist.gov/pml/special-publication-811", "licence": "US government public domain",
        "retrieved": "2026-10-22", "review_months": 12, "section": "Appendix B.8, inch row",
        "clause": CLAUSE, "hash_scope": "clause_utf8", "sha256": hashlib.sha256(CLAUSE.encode()).hexdigest()}]})
    put(folder / "data/rule_review.json", {"reviewer": "independent source reader", "rules": [finding("inch")]})
    put(folder / "certificate/cases.json", {"cases": [{"rule": "inch", "source": "nist-inch",
        "location": "Appendix B.8, inch row", "input": {"x": "12"}, "expected": "0.3048", "precision": 4,
        "printed": True, "quote": QUOTE}]})
    put(folder / "certificate/checks.json", {
        "producer": "component author", "known_author": "table reader", "route_author": "second route author",
        "falsification": {"seeds": [17, 31], "count": 4, "precision": 8,
                         "bounds": "0 to 100 inches", "boundaries": {"inch": [{"x": "0"}, {"x": "100"}]}}})
    put(folder / "certificate/scope.json", {"not_checked": ["Other units and non-finite inputs."]})
    (folder / "certificate/second_route.py").write_text(HEADER + '''
from decimal import Decimal
def evaluate(rule, inputs):
    return Decimal(inputs["x"]) * Decimal("254") / Decimal("10000")
''', encoding="utf-8")
    (folder / "certificate/falsify.py").write_text(HEADER + '''
import random
def generate(rule, seed, count):
    rng = random.Random(seed)
    return [{"x": str(rng.randrange(0, 101))} for _ in range(count)]
def invariant(rule, inputs, value):
    return value >= 0
''', encoding="utf-8")
    monkeypatch.setattr(g6, "__path__", [*g6.__path__, str(root / "g6")])
    monkeypatch.setattr(registry, "FAMILIES", registry.FAMILIES | {"certfixture"})
    importlib.invalidate_caches()
    refresh_review(folder)
    monkeypatch.setattr(kit, "_certificate_history_positions", positions, raising=False)
    yield folder
    for name in list(sys.modules):
        if name == "g6.certfixture" or name.startswith(("g6.certfixture.", "g6.certhelp")):
            del sys.modules[name]


def refresh_review(component, package=PACKAGE):
    """Record a new fixture review only when a test deliberately changes reviewed inputs: the reader copies the
    issuing tool's --reviewed hashes and records the source hash it computed from the bytes it read."""
    reviewed = records.reviewed(package)
    root, folder, declaration = records.layout(package, allow_missing_review=True)
    table = records.rules_from(records.rule_path(root, declaration))
    entries = {e["id"]: e for e in records.read_json(root / declaration["register"])["entries"]}

    def update(body):
        body.update(reviewed=reviewed)
        for row in body["rules"]:
            if table[row["rule"]]["source"] in entries:
                row["source_sha256"] = entries[table[row["rule"]]["source"]]["sha256"]
    edit_json(root, declaration["independent_pass"], update)


def positions(bundle, retrofit=False, shift=0):
    return certificates.Positions(artifact=(1 if retrofit else 2) + shift,
        frozen={name: (2 if retrofit else 1) + shift for name in certificates.check_contents(bundle)},
        controls=3 + shift, positive=4 + shift, receipts=5 + shift)


def issue(component, *, today=TODAY, retrofit=False, package=PACKAGE):
    bundle = certificates.load(package)
    body = certificates.issue(bundle, positions(bundle, retrofit), today=today)
    certificates.write(bundle, body)
    return body


def target(component):
    return kit.Target("inches", PACKAGE, component, tuple(component.rglob("*.py")), "certfixture", "builtin")


def edit_json(component, relative, change):
    path = component / relative
    body = json.loads(path.read_text(encoding="utf-8"))
    change(body)
    put(path, body)


@pytest.fixture
def confined(confined_python, monkeypatch):
    """The certificate's children started with ``confined_python`` (``tests/conftest.py``), so they run confined by the
    operating system (``g6.core.confine``): only then does a check count as independent. A skip that says why where
    no backend can run."""
    name, why = confine.backend(confined_python)
    if name is None:
        pytest.skip(f"the Windows backend cannot run here: {why}")
    monkeypatch.setenv("G6_PYTHON", confined_python)
    return confined_python


def test_a_check_whose_runs_were_not_confined_never_supports_verified(component, monkeypatch):
    # Removing issue's confinement condition on independence would verify a component whose checks ran unconfined.
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead
    body = issue(component)
    assert body["issuer"]["state"] == "qualified-draft"
    assert {c["kind"] for c in body["issuer"]["checks"]} == {"partial_falsifier"}
    assert body["isolation"] == {"backend": None, "why": "unavailable: switched off by this test", "network": False}


def test_an_unconfined_issuance_records_no_folder_of_the_issuing_machine(component, monkeypatch):
    # Removing issue's folder scrub writes the issuing machine's folders, and its user's name, into a committed
    # certificate (confine's reason names the folders and the grant commands for them).
    folder = "C:" + "\\Users\\someone\\code\\oss\\src"  # built at run time: the export scan refuses a user path
    why = (f'unavailable: the Windows AppContainer cannot read {folder}, so untrusted code would run with your '
           f'permissions; run once: icacls "{folder}" /grant "*S-1-15-2-1:(OI)(CI)(RX)", then /home/someone/code/oss/src')
    monkeypatch.setattr(confine, "_select", lambda: (None, why))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead
    body = issue(component)
    assert "someone" not in records.canonical(body).decode("utf-8")
    assert body["isolation"]["why"].startswith("unavailable: the Windows AppContainer cannot read <folder>, so ")


def test_a_certificate_verifies_on_a_machine_whose_confinement_differs_from_the_issuers(component, monkeypatch):
    # Removing the rerun's use of the record's isolation fails every certificate on any machine but the issuer's kind
    # (an export issued unconfined on Windows, checked on Linux, whose reason differs).
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: the issuer's reason"))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead
    body = issue(component)
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: another machine's reason"))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead
    assert certificates.verify(certificates.load(PACKAGE), body, today=TODAY) == []
    # The record's isolation is the issuer's statement, so one of another shape is not taken from it
    forged = {**body, "isolation": "confined"}
    forged["digest"] = records.digest(forged)
    assert "component certificate differs from offline reissue" in certificates.verify(
        certificates.load(PACKAGE), forged, today=TODAY)


def test_a_confined_certificate_verifies_where_nothing_can_be_confined(component, confined, monkeypatch):
    # Removing the rerun's use of the record's isolation makes a reissue with no confinement mark every check a partial
    # falsifier, so a certificate issued confined fails on Linux and macOS.
    body = issue(component)
    assert body["isolation"]["backend"] and body["issuer"]["state"] == "verified"
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead
    assert certificates.verify(certificates.load(PACKAGE), body, today=TODAY) == []


def test_fixture_passes_receipts_controls_listing_and_c1(component, confined):
    # Removing issue's per-check run or vetting's certificate gate breaks these observed receipts and listing.
    body = issue(component)
    assert body["issuer"]["state"] == "verified"
    assert [r["result"] for r in body["issuer"]["receipts"]] == ["pass", "pass"]
    assert all(any(c["killed"] for c in check["controls"]) for check in body["issuer"]["checks"])
    assert body["falsification"]["inch"]["count"] == 10
    assert body["falsification"]["inch"]["seeds"] == [17, 31]
    assert body["printed_values"] == [{"rule": "inch", "location": "Appendix B.8, inch row", "source": "nist-inch",
                                       "values": ["0.3048"], "quote": QUOTE, "found": True}]
    assert certificates.verify(certificates.load(PACKAGE), body, today=TODAY) == []
    assert vetting.status(PACKAGE, today=TODAY) == vetting.REVIEWED
    assert kit.c1_certificate(target(component), today=TODAY) == []


# --- withheld quotes (owner, 2026-10-25): a source whose text is not published keeps its quotes out of the record ------
BOOK = ("Appendix B.8 lists the inch. Then the worked line: so 12 inches are 0.3048 metre, which the table "
        "rounds no further.").encode("utf-8")
OWN_WORDS = "Appendix B.8 gives the inch as 2.54 hundredths of a metre (the register's own words, not a quote)."


def sha(text):
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def withhold(component, *, case_hash=None):
    """The fixture's source as an all-rights-reserved book: its bytes held, its clause in the register's own words,
    its printed case giving only its quote's hash."""
    def register(body):
        body["entries"][0].update(hash_scope="corpus_file", sha256=sha(BOOK), clause=OWN_WORDS,
                                  clause_sha256=sha(OWN_WORDS), withheld=True,
                                  licence="All rights reserved (a stand-in): citation and hashes only")
    edit_json(component, "data/source_register.json", register)

    def cases(body):
        case = body["cases"][0]
        case["quote_sha256"] = case_hash or sha(case.pop("quote"))
        case.pop("quote", None)
    edit_json(component, "certificate/cases.json", cases)
    refresh_review(component)


def issue_withheld(quotes=(QUOTE,), *, today=TODAY):
    bundle = certificates.load(PACKAGE, source_files={"nist-inch": BOOK}, quotes=list(quotes))
    body = certificates.issue(bundle, positions(bundle), today=today)
    certificates.write(bundle, body)
    return bundle, body


def test_a_withheld_quote_is_checked_in_the_held_source_and_only_its_hash_is_published(component):
    withhold(component)
    bundle, body = issue_withheld()
    # fails if the issuer publishes a withheld quote, or stops checking it against the source's held bytes
    assert body["printed_values"] == [{"rule": "inch", "location": "Appendix B.8, inch row", "source": "nist-inch",
                                       "values": ["0.3048"], "quote_sha256": sha(QUOTE), "found": True}]
    for name in ("certificate.json", "CERTIFICATE.md"):
        assert QUOTE not in (component / "certificate" / name).read_text(encoding="utf-8")
    assert "withheld" in (component / "certificate" / "CERTIFICATE.md").read_text(encoding="utf-8")


def test_a_withheld_quote_the_source_does_not_hold_is_not_found(component):
    absent = "so 12 inches are 0.3049 metre"
    withhold(component, case_hash=sha(absent))
    _, body = issue_withheld([absent])
    # fails if a withheld quote is taken on its hash alone, without being found in the held bytes
    assert body["printed_values"][0]["found"] is False
    assert ("inch:known", "printed_value_not_in_source") in [(o[0], o[1]) for o in body["issuer"]["obligations"]]
    assert body["issuer"]["state"] == "blocked-escalated"


def test_a_supplied_text_must_have_the_cases_hash(component):
    withhold(component)
    # fails if any supplied text stands in for the quote the case author hashed
    with pytest.raises(ValueError, match="quote_sha256"):
        certificates.load(PACKAGE, source_files={"nist-inch": BOOK}, quotes=["so 12 inches are about 0.3 metre"])


def test_a_withheld_source_never_publishes_a_quote(component):
    withhold(component)
    edit_json(component, "certificate/cases.json",
              lambda body: body["cases"][0].update(quote=QUOTE) or body["cases"][0].pop("quote_sha256"))
    # fails if a case may quote a source the register says is withheld
    with pytest.raises(ValueError, match="withheld"):
        certificates.load(PACKAGE, source_files={"nist-inch": BOOK}, quotes=[QUOTE])


def test_a_quote_hash_is_only_for_a_withheld_source(component):
    edit_json(component, "certificate/cases.json",
              lambda body: body["cases"][0].update(quote_sha256=sha(QUOTE)) or body["cases"][0].pop("quote"))
    # fails if an open source's quote may be hidden: what can be published is published
    with pytest.raises(ValueError, match="withheld"):
        certificates.load(PACKAGE)


def test_a_withheld_source_is_held_as_bytes_never_hashed_by_its_clause(component):
    withhold(component)
    edit_json(component, "data/source_register.json",
              lambda body: body["entries"][0].update(hash_scope="clause_utf8", sha256=sha(OWN_WORDS)))
    # fails if a withheld quote could be "found" in the register's own words instead of the source
    with pytest.raises(ValueError, match="withheld"):
        certificates.load(PACKAGE)


def test_offline_a_withheld_quote_rests_on_the_issuance_record(component):
    withhold(component)
    _, body = issue_withheld()
    # fails if a later check without the quote or the bytes cannot replay the record, or replays a changed one
    assert certificates.verify(certificates.load(PACKAGE), body, today=TODAY) == []
    edit_json(component, "certificate/cases.json",
              lambda b: b["cases"][0].update(quote_sha256=sha("so 12 inches are 0.30480 metre")))
    refresh_review(component)
    with pytest.raises(ValueError, match="printed value"):
        certificates.load(PACKAGE)


def test_issuing_with_the_bytes_but_not_the_quote_is_refused(component):
    withhold(component)
    # fails if the issuer falls back on an old record while it holds the source and could check the quote itself
    with pytest.raises(ValueError, match="--quotes"):
        certificates.load(PACKAGE, source_files={"nist-inch": BOOK})


def test_the_issue_tool_reads_withheld_quotes_from_a_file(component, tmp_path, monkeypatch):
    withhold(component)
    quotes = tmp_path / "quotes.json"
    quotes.write_text(json.dumps({"quotes": [QUOTE]}), encoding="utf-8")
    seen = {}
    real = certificates.load

    def load(package, **kwargs):
        seen.update(kwargs)
        return real(package, **kwargs)
    monkeypatch.setattr(certificates, "load", load)
    book = tmp_path / "book.md"
    book.write_bytes(BOOK)
    tool().main(["--precheck", PACKAGE, "--source-file", f"nist-inch={book}", "--quotes", str(quotes)])
    # fails if the tool drops --quotes, so a withheld source's printed case could never be checked at issuance
    assert seen.get("quotes") == [QUOTE]


def test_evidence_01_records_the_issuance_did_not_write_to_its_ledger_never_verify(component, confined, monkeypatch):
    # Removing g6.evidence's `elif missing := _unresolved(c, p, r, resolves):` branch makes this fail (verified).
    monkeypatch.setattr(certificates.issuer, "record", lambda ledger, *found: None)  # typed into issue() by hand only
    body = issue(component)
    assert body["issuer"]["state"] == "qualified-draft" and body["issuer"]["unresolved"]
    assert any("not_in_ledger" in gap for _, gap in body["issuer"]["gaps"])


@pytest.mark.parametrize("field,value", [("component", "elsewhere"), ("not_checked", ["rewritten"])])
def test_hand_edited_outer_field_fails(component, field, value):
    # Removing verify's full reissue comparison or record digest check lets rewritten metadata through.
    body = issue(component)
    body[field] = value
    (component / "certificate/certificate.json").write_bytes(records.canonical(body))
    assert certificates.verify(certificates.load(PACKAGE), body, today=TODAY)
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING
    assert kit.c1_certificate(target(component), today=TODAY)


def test_changed_code_bytes_fail(component):
    # Removing artifact_bytes comparison would keep a certificate current after source changes.
    issue(component)
    path = component / "implementation.py"
    path.write_bytes(path.read_bytes() + b"\n# changed bytes\n")
    with pytest.raises(ValueError, match="independent pass.*hash"):
        certificates.load(PACKAGE)
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


@pytest.mark.parametrize("variant,needle", [
    ("missing_case", "case"), ("other_source", "source"), ("no_location", "location"),
    ("computed_expected", "expected"), ("empty_scope", "scope"),
    ("route_import", "independent"), ("dynamic_import", "independent"),
])
def test_bad_inputs_are_refused_and_await_review(component, variant, needle):
    # Removing load's corresponding source/case/scope/AST validation permits this variant to issue.
    issue(component)
    if variant == "missing_case":
        edit_json(component, "certificate/cases.json", lambda b: b.update(cases=[]))
    elif variant == "other_source":
        edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(source="some-other-source"))
    elif variant == "no_location":
        edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(location=""))
    elif variant == "computed_expected":
        edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(expected={"call": "convert"}))
    elif variant == "empty_scope":
        edit_json(component, "certificate/scope.json", lambda b: b.update(not_checked=[]))
    else:
        code = "from g6.certfixture.inches import implementation\n" if variant == "route_import" else (
            'import importlib as loader\nx = loader.import_module("g6.certfixture.inches")\n')
        path = component / "certificate/second_route.py"
        path.write_bytes(path.read_bytes() + code.encode())
    with pytest.raises(ValueError, match=needle):
        issue(component)
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING
    assert kit.c1_certificate(target(component), today=TODAY)


def test_check_that_catches_no_defect_is_a_named_gap(component, monkeypatch):
    # Removing issue's no_seeded_defect gate would list passing but untested checks.
    monkeypatch.setattr(certificates.mutation_check, "mutants", lambda *a, **k: [])
    body = issue(component)
    assert any("no_seeded_defect" in gap for _, gap in body["issuer"]["gaps"])
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


def test_review_window_is_live_and_injectable(component):
    # Removing source review_due comparison would list a stale certificate.
    body = issue(component)
    later = dt.date(2027, 10, 23)
    assert certificates.verify(certificates.load(PACKAGE), body, today=later)
    assert vetting.status(PACKAGE, today=later) == vetting.AWAITING
    stale = issue(component, today=later)
    assert stale["stale_sources"] == ["nist-inch"]
    assert vetting.status(PACKAGE, today=later) == vetting.AWAITING


def test_disagreement_on_falsification_refutes(component):
    # Removing comparison on generated inputs misses a route that agrees only on the source example.
    path = component / "certificate/second_route.py"
    path.write_text(HEADER + '''
from decimal import Decimal
def evaluate(rule, inputs):
    return Decimal("0.3048") if inputs["x"] == "12" else Decimal("1")
''', encoding="utf-8")
    body = issue(component)
    assert body["issuer"]["state"] == "blocked-escalated"
    assert body["issuer"]["predicates"][0]["refute"]
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


def _signed(package=PACKAGE) -> bool:
    """Whether the package's detached signed review verifies over its current certificate."""
    root, folder, declaration = records.layout(package)
    return records.signed_review(root, declaration, certificate_path=folder / "certificate/certificate.json")


def test_an_old_signed_review_is_never_verified_and_listing_needs_none(component):
    # Removing signed_review's SSH verification admits an arbitrary signature with a retargeted content digest.
    # Owner, 2026-10-23 (DECISIONS #39): a physical-safety component lists on its certificate alone, so the record says
    # no signature is required (fails while component_certificate records SIGNED_KINDS as required, and while
    # vetting.status waits for a verified signature).
    init = component / "__init__.py"
    init.write_text(HEADER + "__g6_vetting__ = " + repr({
        "kinds": ("physical_safety",), "register": PREFIX + "/data/source_register.json",
        "independent_pass": PREFIX + "/data/rule_review.json", "signed_review": PREFIX + "/signed.json"}) + "\n",
        encoding="utf-8")
    put(component / "signed.json", {"reviewer": "qualified reviewer", "qualification": "relevant qualification",
        "signature": "Owner-held signed review reference", "content_sha256": "0" * 64})
    refresh_review(component)
    body = issue(component)
    assert body["review"]["required"] is False and body["review"]["present"] is True
    assert not _signed()
    assert vetting.status(PACKAGE, today=TODAY) == vetting.REVIEWED
    edit_json(component, "signed.json", lambda b: b.update(content_sha256=records.content_sha256(body)))
    assert not _signed()
    assert vetting.summary(PACKAGE)["signed_review"] is False
    assert certificates.verify(certificates.load(PACKAGE), body, today=TODAY) == []


def test_issuer_redecides_even_when_digests_are_recomputed(component):
    # Removing the issuer.verify call lets a self-consistent hand-written state bypass its decision rules.
    body = issue(component, retrofit=True)
    body["issuer"]["state"] = "verified"
    from g6.evidence.certificate import digest_of
    body["issuer"]["digest"] = digest_of(body["issuer"])
    body["digest"] = records.digest(body)
    problems = certificates.verify(certificates.load(PACKAGE), body, today=TODAY)
    assert any("state" in p or "issuing rules" in p for p in problems)


def test_retrofit_never_earns_early_freeze_support(component):
    # Removing the positions mapping would erase the issuer's frozen_after_artifact reason.
    body = issue(component, retrofit=True)
    assert body["issuer"]["state"] == "qualified-draft"
    assert any("frozen_after_artifact" in reason for _, reason in body["issuer"]["gaps"])


def test_deterministic_bytes_and_restrained_words(component, confined):
    # Removing canonical JSON/LF writes, or adding time/raw floats, breaks byte equality and the published wording.
    issue(component)
    paths = [component / "certificate/certificate.json", component / "certificate/CERTIFICATE.md"]
    before = [p.read_bytes() for p in paths]
    issue(component)
    assert [p.read_bytes() for p in paths] == before
    for data in before:
        assert b"\r" not in data
        assert not re.search(rb"\b(certified|proved|correct|safe)\b", data, re.I)
    body = json.loads(before[0])
    assert body["observations"]["inch"]["known"][0]["actual"] == "0.3048"
    assert body["observations"]["inch"]["second_route"][-1]["actual"].count(".") == 1
    assert before[0].decode().count('"verified"') == 1


def test_evidence_helper_does_not_raise_a_result_label(component):
    # Removing add_evidence's append or current-certificate check loses attribution or cites stale evidence.
    body = issue(component)
    result = {"completion_state": "qualified-draft", "evidence": [{"kind": "calculation"}]}
    records.add_evidence(result, PACKAGE, ["inch"], today=TODAY)
    assert result["completion_state"] == "qualified-draft"
    assert result["evidence"][-1] == {"kind": "certificate", "component": PACKAGE,
        "certificate_sha256": hashlib.sha256((component / "certificate/certificate.json").read_bytes()).hexdigest(),
        "rules_applied": ["inch"],
        "not_checked": [records.state_line(body["issuer"], {"inch": body["rules"]["inch"]}),
                        "Other units and non-finite inputs."]}
    with pytest.raises(ValueError):
        records.add_evidence(result, PACKAGE, ["undeclared"], today=TODAY)


def test_describe_exposes_current_certificate(component, confined):
    # Removing discovery's certificate field hides the exact bytes and scope from callers.
    from g6.core.discovery import describe
    issue(component)
    view = describe("inches")["certificate"]
    assert view["path"].endswith("/certificate/certificate.json")
    assert view["state"] == "verified" and view["current"] is True
    assert view["not_checked"][1:] == ["Other units and non-finite inputs."]


def test_missing_certificate_is_c1_not_d8(component):
    # Removing C1's declared-package requirement allows a new vetted port without a certificate.
    found = kit.c1_certificate(target(component), today=TODAY)
    assert len(found) == 1 and found[0].gate == "C1" and "missing certificate" in found[0].message


def tool():
    path = Path(__file__).resolve().parents[2] / "tools/issue_certificate.py"
    spec = importlib.util.spec_from_file_location("issue_component_certificate_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_check_reruns_and_compares_both_files(component, monkeypatch):
    # Removing --check's regenerated byte comparison would accept an edited Markdown file.
    cli = tool()
    monkeypatch.setattr(cli, "history_positions", positions)
    monkeypatch.setattr(cli, "today", lambda: TODAY)
    assert cli.main([PACKAGE]) == 0
    assert cli.main(["--check", PACKAGE]) == 0
    path = component / "certificate/CERTIFICATE.md"
    path.write_bytes(path.read_bytes() + b"\nchanged\n")
    assert cli.main(["--check", PACKAGE]) == 1


CRLF_CODE = "G6_E_CERTIFICATE_CRLF"


@pytest.mark.parametrize("argv", [["--reviewed", PACKAGE], ["--precheck", PACKAGE], ["--check", PACKAGE], [PACKAGE]],
                         ids=["reviewed", "precheck", "check", "issue"])
def test_every_issuer_mode_refuses_an_artifact_file_with_crlf_bytes(component, monkeypatch, capsys, argv):
    issue(component)                                   # an LF certificate first, so --check has one to compare
    path = component / "implementation.py"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))   # a checkout that kept CRLF
    cli = tool()
    monkeypatch.setattr(cli, "history_positions", positions)
    monkeypatch.setattr(cli, "today", lambda: TODAY)
    capsys.readouterr()
    # fails if records.reviewed stops refusing CRLF artifact bytes (records.lf_only): --reviewed then prints hashes no
    # LF checkout reproduces, and the other modes fail, if at all, with no word of why
    assert cli.main(argv) == 1
    said = capsys.readouterr().err
    assert f"[{CRLF_CODE}] {IMPL} holds CRLF line endings" in said and "LF line endings" in said, said
    assert path.read_bytes().count(b"\r\n") > 0                     # nothing was normalised


def test_crlf_in_a_bound_module_outside_the_package_is_refused(component):
    helper = component.parents[1] / "certhelp.py"      # a g6 module the implementation imports: bound by its bytes
    helper.write_bytes(HEADER.encode() + b"FACTOR = 0.0254\r\n")
    impl = component / "implementation.py"
    impl.write_text(HEADER + "from g6.certhelp import FACTOR\n\n\ndef convert(x):\n    return float(x) * FACTOR\n",
                    encoding="utf-8")
    # fails if records.lf_only is given only the package's own files, not every module the artifact binds
    with pytest.raises(ValueError, match=re.escape(f"[{CRLF_CODE}] g6/certhelp.py holds CRLF")):
        records.reviewed(PACKAGE)


def test_a_binary_data_file_holding_crlf_bytes_is_not_refused(component):
    (component / "data" / "table.bin").write_bytes(b"\x00\x01\r\n\x02")   # binary: git converts no line ending in it
    # fails if records.lf_only refuses a file git itself treats as binary (a NUL byte in its first 8000 bytes)
    assert records.reviewed(PACKAGE)["artifact_sha256"]


def test_fail_closed_on_child_refusal(component, monkeypatch):
    # Removing runner failure handling would turn a sandbox refusal into a pass or a killed control.
    from g6.core import sandbox
    monkeypatch.setattr(sandbox, "run_script", lambda *a, **k: sandbox.ScriptRun("refused", None, "", "refused"))
    body = issue(component)
    assert body["issuer"]["state"] != "verified"
    assert all(r["result"] == "unknown" for r in body["issuer"]["receipts"])
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


def test_positive_reference_failure_cannot_refute(component):
    # Removing positive_seq's observed-reference condition turns an always-failing route into a refutation.
    (component / "certificate/second_route.py").write_text(
        HEADER + 'def evaluate(rule, inputs):\n    return -1\n', encoding="utf-8")
    body = issue(component)
    assert not body["issuer"]["predicates"][0]["refute"]
    assert all(c["positive_seq"] is None for c in body["issuer"]["checks"])


def test_source_hash_and_independent_coverage_are_not_claims(component):
    # Removing clause rehash/independent rule coverage validation admits changed clauses and missing reviews.
    issue(component)
    edit_json(component, "data/source_register.json", lambda b: b["entries"][0].update(clause="rewritten"))
    with pytest.raises(ValueError, match="hash"):
        issue(component)
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING

def test_independent_pass_must_cover_every_rule(component):
    # Removing load's review coverage check would accept a review with no findings.
    issue(component)
    edit_json(component, "data/rule_review.json", lambda b: b.update(rules=[]))
    with pytest.raises(ValueError, match="coverage"):
        issue(component)
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


def test_no_file_or_clause_hash_is_accepted_without_bytes(component):
    # Removing the offline hash recomputation would accept an owner-file digest on a caller's word.
    edit_json(component, "data/source_register.json", lambda b: b["entries"][0].update(hash_scope="corpus_file", clause_sha256=records.sha(CLAUSE.encode())))
    with pytest.raises(ValueError, match="offline"):
        certificates.load(PACKAGE)
    refresh_review(component)
    bundle = certificates.load(PACKAGE, source_files={"nist-inch": CLAUSE.encode()})
    assert bundle.register[0]["sha256"] == hashlib.sha256(CLAUSE.encode()).hexdigest()


def test_out_of_order_receipts_are_refused(component):
    # Removing issue's chronology check would fabricate a run before the controls existed.
    bundle = certificates.load(PACKAGE)
    bad = certificates.Positions(2, {k: 1 for k in certificates.check_contents(bundle)}, 5, 4, 3)
    with pytest.raises(ValueError, match="order"):
        certificates.issue(bundle, bad, today=TODAY)


def test_case_precision_controls_the_comparison_not_binary_rounding(component):
    # Removing the unrounded Decimal comparison admits an error that rounds onto a tolerance boundary.
    path = component / "implementation.py"
    path.write_text(HEADER + 'def convert(x):\n    return float(x) * 0.0254 + 0.00005000001\n', encoding="utf-8")
    refresh_review(component)
    body = issue(component)
    assert body["issuer"]["receipts"][0]["result"] == "fail"
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


def fake_folder_ids(stdin):
    """A different folder id in every fake commit, so every one of them is compared."""
    lines = stdin.decode("utf-8").splitlines()
    return b"".join(hashlib.sha1(line.partition(":")[0].encode()).hexdigest().encode() + b"\n" for line in lines)


def test_history_freeze_cannot_move_existing_code_after_later_checks(component, monkeypatch):
    # Removing code_files from history resolution lets later check files move a retrofit's artifact position.
    cli = tool()
    bundle = certificates.load(PACKAGE)
    root = bundle.root
    early = records.code_files(bundle.files, bundle.declaration)
    late = dict(early)
    for content in certificates.check_contents(bundle).values():
        late.update(content)
    history = {"early": early, "late": late}

    def git(where, *args, stdin=None):
        if args == ("rev-parse", "--is-shallow-repository"):
            return b"false\n"
        if args == ("rev-parse", "--show-toplevel"):
            return str(root).encode()
        if args == ("rev-list", "--first-parent", "--reverse", "HEAD"):
            return b"early\nlate\n"
        if args[0] == "cat-file":
            return fake_folder_ids(stdin)
        if args[:3] == ("ls-tree", "-r", "--name-only"):
            return ("\n".join(history[args[3]]) + "\n").encode()
        if args[0] == "show":
            commit, name = args[1].split(":", 1)
            return history[commit][name]
        raise AssertionError(args)

    monkeypatch.setattr(cli, "_git", git)
    found = cli.history_positions(bundle)
    assert found.artifact == 1 and set(found.frozen.values()) == {2}
    body = certificates.issue(bundle, found, today=TODAY)
    assert body["issuer"]["state"] == "qualified-draft"


def test_history_refuses_uncommitted_check_bytes(component, monkeypatch):
    # Removing exact-byte comparison would silently assign freeze positions to changed checks.
    cli = tool()
    bundle = certificates.load(PACKAGE)

    def git(where, *args, stdin=None):
        if args == ("rev-parse", "--is-shallow-repository"):
            return b"false\n"
        if args == ("rev-parse", "--show-toplevel"):
            return str(bundle.root).encode()
        if args[0] == "rev-list":
            return b"only\n"
        if args[0] == "cat-file":
            return fake_folder_ids(stdin)
        return b""

    monkeypatch.setattr(cli, "_git", git)
    with pytest.raises(ValueError, match="absent from history"):
        cli.history_positions(bundle)


def test_formal_check_is_bound_to_actual_code_not_a_claimed_digest():
    # Removing expression_of's syntax restriction would prove a different program than the one run.
    from g6.workforce import _certificate_formal as formal
    assert formal.expression_of("def f(x):\n    return x * 254\n", "f", ["x"]) == "(* x 254)"
    for source in ("def f(x):\n    return helper(x)\n", "def f(x):\n    return x * 0.0254\n",
                   "def f(x):\n    x = 1\n    return x * 254\n"):
        with pytest.raises(ValueError):
            formal.expression_of(source, "f", ["x"])


def test_formal_solver_rechecks_equivalence_and_catches_a_mutant():
    # Removing the actual g6.formal equivalence query would trust the proof's expression as a pass.
    pytest.importorskip("z3")
    from g6.workforce import _certificate_formal as formal
    proof = {"parameters": ["x"], "expression": "(* x 254)", "author": "proof author"}
    assert formal.check("def f(x):\n    return x * 254\n", "f", proof) == "pass"
    assert formal.check("def f(x):\n    return x - 254\n", "f", proof) == "fail"


def test_certificate_data_is_in_the_built_wheel(component, tmp_path):
    # Removing the wheel package/data selection would omit certificate inputs and generated records.
    import tomllib
    import zipfile
    builder_module = pytest.importorskip("hatchling.builders.wheel")
    issue(component)
    project = component.parents[3]
    original = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = original["tool"]["hatch"]["build"]["targets"]["wheel"]
    config = {"project": {"name": "certificate-fixture", "version": "1.0", "requires-python": ">=3.12"},
              "tool": {"hatch": {"build": {"targets": {"wheel": wheel}}}}}
    destination = tmp_path / "dist"
    destination.mkdir()
    builder = builder_module.WheelBuilder(str(project), config=config)
    built = list(builder.build(directory=str(destination)))
    with zipfile.ZipFile(built[0]) as archive:
        names = set(archive.namelist())
    assert {PREFIX + "/certificate/" + n for n in ("certificate.json", "CERTIFICATE.md", "cases.json",
                                                  "checks.json", "scope.json")} <= names

def test_formal_digest_is_named_but_does_not_bless_unsupported_float_code(component):
    # Removing the formal receipt or its actual-code check would bless this unsupported encoding.
    (component / "certificate/formal_reference.py").write_text(
        HEADER + "def reference(x):\n    return x * 254\n", encoding="utf-8")
    put(component / "certificate/proof.json",
        {"parameters": ["x"], "expression": "(* x 254)", "author": "proof author",
         "reference": {"file": PREFIX + "/certificate/formal_reference.py", "function": "reference"}})
    edit_json(component, "certificate/checks.json",
              lambda b: b.update(proofs={"inch": PREFIX + "/certificate/proof.json"}))
    body = issue(component)
    formal = next(c for c in body["issuer"]["checks"] if c["check_id"].endswith(":formal"))
    # an independent formal check, unless its children ran unconfined (then every check is a partial falsifier)
    assert formal["kind"] == ("formal_checked" if body["isolation"]["backend"] else "partial_falsifier")
    assert formal["certificate"] == hashlib.sha256((component / "certificate/proof.json").read_bytes()).hexdigest()
    receipt = next(r for r in body["issuer"]["receipts"] if r["check_id"] == formal["check_id"])
    assert receipt["result"] == "unknown"
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING

def test_receipts_run_after_controls_and_positive_reference(component, monkeypatch):
    # Moving the original target run before controls would record a chronology that never happened.
    seen = []
    original = (component / "implementation.py").read_bytes()
    real = certificates.observe

    def observed(files, mode, **request):
        if mode == "candidate":
            seen.append(("candidate", files[request["file"]] == original))
        elif mode == "reference":
            seen.append(("reference", True))
        return real(files, mode, **request)

    monkeypatch.setattr(certificates, "observe", observed)
    issue(component)
    target_at = [i for i, row in enumerate(seen) if row == ("candidate", True)]
    mutants_at = [i for i, row in enumerate(seen) if row == ("candidate", False)]
    reference_at = [i for i, row in enumerate(seen) if row[0] == "reference"]
    assert len(target_at) == 1 and mutants_at
    assert max(mutants_at) < reference_at[-1] < target_at[0]


@pytest.mark.parametrize("result", ["pass", "fail"])
def test_candidate_marker_and_early_exit_are_unknown(component, result):
    # Removing observe's nonce equality check accepts a candidate's forged completion marker.
    from g6.workforce._certificate_runner import observe
    source = (HEADER + "import json\nfrom pathlib import Path\n"
              + "request = json.loads(Path('request.json').read_text(encoding='utf-8'))\n"
              + "values = [{'value': "
              + ("str(float(item['x']) * 0.0254)" if result == "pass" else "'-1'")
              + "} for item in request['inputs']]\n"
              + "print('G6-CERTIFICATE ' + json.dumps({'nonce': 'a' * 64, 'values': values}))\n"
              + "raise SystemExit(0)\ndef convert(x):\n    return float(x) * 3\n")
    found = observe({PREFIX + "/implementation.py": source.encode()}, "candidate",
                    file=PREFIX + "/implementation.py", function="convert", inputs=[{"x": "12"}])
    assert certificates._measure(certificates._values(found, 1), ["0.3048"], [4])[0] == "unknown"
    (component / "implementation.py").write_bytes(source.encode())
    refresh_review(component)
    body = issue(component)
    assert all(r["result"] == "unknown" for r in body["issuer"]["receipts"])
    assert all(check["controls"] for check in body["issuer"]["checks"])
    assert all(not c["killed"] for check in body["issuer"]["checks"] for c in check["controls"])
    assert not body["issuer"]["predicates"][0]["refute"]


@pytest.mark.parametrize("variant", ["wrong_nonce", "duplicate", "not_last", "exit_nonzero", "no_output"])
def test_child_protocol_requires_one_final_nonce_and_zero_exit(monkeypatch, variant):
    # Removing observe's final-line/count/returncode/nonce guard accepts this incomplete protocol.
    from g6.core import sandbox
    from g6.workforce import _certificate_runner as runner

    def run(source, files, **kwargs):
        payload = json.loads(files["request.json"])
        marker = "G6-CERTIFICATE " + json.dumps(
            {"nonce": payload.get("nonce", "a" * 64), "values": [{"value": "1"}]})
        stdout = marker + "\n"
        if variant == "wrong_nonce":
            stdout = 'G6-CERTIFICATE {"nonce":"wrong","values":[{"value":"1"}]}\n'
        elif variant == "duplicate":
            stdout += marker + "\n"
        elif variant == "not_last":
            stdout += "after harness\n"
        elif variant == "no_output":
            stdout = ""
        return sandbox.ScriptRun("ok", 1 if variant == "exit_nonzero" else 0, stdout, "")

    monkeypatch.setattr(sandbox, "run_script", run)
    assert runner.observe({}, "candidate") is None


def test_refused_mutant_cannot_satisfy_seeded_defect(component, monkeypatch):
    # Removing _measure's unknown-on-error line awards killed-control credit for a guard refusal.
    from types import SimpleNamespace
    mutant = HEADER + "import socket\ndef convert(x):\n    socket.socket()\n    return -1\n"
    monkeypatch.setattr(certificates.mutation_check, "mutants",
                        lambda *a, **k: [SimpleNamespace(source=mutant)])
    body = issue(component)
    assert all(not c["killed"] for check in body["issuer"]["checks"] for c in check["controls"])
    assert any("no_seeded_defect" in reason for _, reason in body["issuer"]["gaps"])
    assert not body["issuer"]["predicates"][0]["refute"]
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


@pytest.mark.parametrize("changed", ["code", "clause", "rules", "cases"])
def test_old_independent_pass_refuses_changed_reviewed_bytes(component, changed):
    # Removing load's reviewed-hash equality requirement reuses findings for bytes the reader never reviewed.
    if changed == "code":
        path = component / "implementation.py"
        path.write_bytes(path.read_bytes() + b"\n# unreviewed change\n")
    elif changed == "clause":
        edit_json(component, "data/source_register.json", lambda b: b["entries"][0].update(
            clause=CLAUSE + ".", sha256=hashlib.sha256((CLAUSE + ".").encode()).hexdigest()))
    elif changed == "rules":
        path = component / "sources.py"
        path.write_bytes(path.read_bytes().replace(b"inch to metre", b"inches to metres"))
    else:
        edit_json(component, "certificate/cases.json",
                  lambda b: b["cases"][0].update(location="another unreviewed table"))
    with pytest.raises(ValueError, match="independent pass.*hash"):
        issue(component)


def test_always_false_invariant_is_calibration_gap(component):
    # Removing the reference invariant run from per-check positives lets an always-false check refute.
    path = component / "certificate/falsify.py"
    path.write_bytes(path.read_bytes().replace(b"return value >= 0", b"return False"))
    body = issue(component)
    checks = {c["check_id"]: c for c in body["issuer"]["checks"]}
    assert checks["inch:known"]["positive_seq"] is not None
    assert checks["inch:second_route"]["positive_seq"] is None
    assert ("inch:second_route", "positive_reference_not_passed") in [
        (v[0], v[1]) for v in body["issuer"]["obligations"]]
    assert not body["issuer"]["predicates"][0]["refute"]


def test_changed_harness_bytes_make_listing_stale(component, tmp_path, monkeypatch):
    # Removing inspect's harness manifest comparison keeps a certificate current after a harness edit.
    issue(component)
    library = Path(records.__file__).parents[2]
    names = ("g6/core/component_certificate.py", "g6/workforce/component_certificate.py",
             "g6/workforce/_certificate_runner.py", "g6/workforce/_certificate_formal.py")
    shadow = tmp_path / "harness"
    for name in names:
        path = shadow / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((library / name).read_bytes())
    monkeypatch.setattr(records, "HARNESS_ROOT", shadow, raising=False)
    assert records.inspect(PACKAGE, today=TODAY)[0]["current"]
    changed = shadow / "g6/workforce/_certificate_runner.py"
    changed.write_bytes(changed.read_bytes() + b"\n# altered harness\n")
    view, _, problems = records.inspect(PACKAGE, today=TODAY)
    assert not view["current"] and any("harness" in p for p in problems)
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


def git_repo(repo, bundle):
    """A repository over the fixture, with the harness files its checks freeze written beside it."""
    import subprocess

    for content in certificates.check_contents(bundle).values():
        for name, data in content.items():
            path = bundle.root / name
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=Codex",
                               "-c", "user.email=codex@example.invalid", "-c", "core.autocrlf=false",
                               "-c", "commit.gpgsign=false",
                               *args], check=True, capture_output=True)
    git("init")
    return git


@pytest.mark.parametrize("variant", ["reversed", "unreachable"])
def test_c1_rejects_reversed_committed_history(component, monkeypatch, variant):
    # Removing C1's authoritative history comparison accepts code-before-checks with invented early freezes.
    from dataclasses import asdict

    cli = tool()
    bundle = certificates.load(PACKAGE)
    git = git_repo(bundle.root.parent, bundle)
    git("add", *["src/" + name for name in records.code_files(bundle.files, bundle.declaration)])
    git("commit", "-m", "Code before checks")
    git("add", "src")
    git("commit", "-m", "Freeze checks")
    actual = cli.history_positions(bundle)
    assert actual.artifact < min(actual.frozen.values())
    body = certificates.issue(bundle, actual, today=TODAY)
    certificates.write(bundle, body)
    monkeypatch.setattr(kit, "_certificate_history_positions", cli.history_positions)
    assert kit.c1_certificate(target(component), today=TODAY) == []
    invented = asdict(actual)
    if variant == "reversed":
        invented.update(artifact=2, frozen={name: 1 for name in actual.frozen})
    else:
        invented["commits"]["artifact"] = "0" * 40
    body = certificates.issue(bundle, certificates.Positions(**invented), today=TODAY)
    certificates.write(bundle, body)
    assert any("history" in p.message for p in kit.c1_certificate(target(component), today=TODAY))


@pytest.mark.parametrize("reason", ["history unavailable", "recorded commit unreachable"])
def test_c1_fails_closed_without_history(component, monkeypatch, reason):
    # Removing C1's history-resolution failure path accepts unverifiable chronology.
    issue(component)

    def unavailable(bundle):
        raise ValueError(reason)

    monkeypatch.setattr(kit, "_certificate_history_positions", unavailable)
    assert any(reason in p.message for p in kit.c1_certificate(target(component), today=TODAY))


@pytest.fixture
def ssh_reviewer(component, tmp_path, monkeypatch):
    import subprocess
    from g6.core import outside

    executable = outside.find_program("ssh-keygen")
    if executable is None:
        pytest.skip("ssh-keygen is absent")
    key = tmp_path / "reviewer"
    subprocess.run([executable, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                   check=True, capture_output=True, env=outside.clean_environment())
    allowed = tmp_path / "allowed_signers"
    allowed.write_text("qualified-reviewer " + key.with_suffix(".pub").read_text(encoding="utf-8"),
                       encoding="utf-8")
    monkeypatch.setattr(records, "ALLOWED_SIGNERS", allowed)
    declaration = {"kinds": ("physical_safety",), "register": PREFIX + "/data/source_register.json",
                   "independent_pass": PREFIX + "/data/rule_review.json", "signed_review": PREFIX + "/signed.json"}
    (component / "__init__.py").write_text(HEADER + "__g6_vetting__ = " + repr(declaration) + "\n",
                                         encoding="utf-8")
    put(component / "signed.json", {"reviewer": "qualified-reviewer", "qualification": "relevant qualification",
                                   "signature": "untrusted text is not an SSH signature", "content_sha256": ""})
    refresh_review(component)
    issue(component)

    def sign(namespace="g6-certificate"):
        # The reviewer signs the content the issuing tool writes (--content), not the record's own bytes.
        path = component / "certificate/certificate.json"
        signature = path.with_suffix(".json.sig")
        signature.unlink(missing_ok=True)
        message = tmp_path / "content.json"
        message.write_bytes(records.canonical(records.content(records.read_json(path))))
        message.with_suffix(".json.sig").unlink(missing_ok=True)
        subprocess.run([executable, "-Y", "sign", "-n", namespace, "-f", str(key), str(message)],
                       check=True, capture_output=True, env=outside.clean_environment())
        signature.write_bytes(message.with_suffix(".json.sig").read_bytes())
        edit_json(component, "signed.json",
                  lambda b: b.update(content_sha256=records.content_sha256(records.read_json(path))))
        return signature

    return sign


def test_ssh_signature_over_exact_bytes_verifies(component, ssh_reviewer):
    # Removing the successful ssh-keygen verification path prevents a qualified signature from verifying. A signature
    # stays possible (DECISIONS #39) and is reported by describe; listing does not need it.
    ssh_reviewer()
    assert _signed() and vetting.summary(PACKAGE)["signed_review"] is True
    assert vetting.status(PACKAGE, today=TODAY) == vetting.REVIEWED
    body = records.read_json(component / "certificate/certificate.json")
    assert certificates.verify(certificates.load(PACKAGE), body, today=TODAY) == []


@pytest.mark.parametrize("variant", ["arbitrary", "retargeted", "unknown_signer", "namespace", "missing_program"])
def test_invalid_ssh_attestation_is_never_verified(component, ssh_reviewer, monkeypatch, variant):
    # Removing signed_review's ssh-keygen verify/require gate admits fabricated or retargeted attestations. The
    # component still lists on its current certificate: no signature is required (DECISIONS #39).
    from g6.core import outside
    from g6.core.result import Result

    signature = ssh_reviewer("different-namespace" if variant == "namespace" else "g6-certificate")
    if variant == "arbitrary":
        signature.write_text("any nonempty string", encoding="utf-8")
        edit_json(component, "signed.json", lambda b: b.update(signature="a fabricated signature"))
    elif variant == "retargeted":
        path = component / "implementation.py"
        path.write_bytes(path.read_bytes() + b"\n# new reviewed bytes\n")
        refresh_review(component)
        body = issue(component)
        edit_json(component, "signed.json", lambda b: b.update(content_sha256=records.content_sha256(body)))
    elif variant == "unknown_signer":
        records.ALLOWED_SIGNERS.write_text("# no qualified reviewers\n", encoding="utf-8")
    elif variant == "missing_program":
        monkeypatch.setattr(outside, "require", lambda *a, **k: Result.fail("ssh-keygen missing"))
    assert not _signed()
    assert vetting.status(PACKAGE, today=TODAY) == vetting.REVIEWED


def test_a_signature_survives_a_reissue_that_only_renumbers_positions(component, ssh_reviewer):
    # Removing content()'s removal of the position fields voids every signature at an export's reissue.
    ssh_reviewer()
    assert _signed()
    signed = records.read_json(component / "certificate/certificate.json")
    bundle = certificates.load(PACKAGE)
    moved = certificates.issue(bundle, positions(bundle, shift=40), today=TODAY)
    certificates.write(bundle, moved)
    assert moved["positions"]["receipts"] == 45 and moved["digest"] != signed["digest"]
    assert records.content_sha256(moved) == records.content_sha256(signed)
    assert _signed()
    # Any other change voids it: a new scope line is new content. The reissued certificate still lists the component.
    edit_json(component, "certificate/scope.json", lambda b: b["not_checked"].append("A further limit."))
    issue(component)
    assert not _signed()
    assert vetting.status(PACKAGE, today=TODAY) == vetting.REVIEWED


@pytest.mark.parametrize("reference_expression,calibrated", [("x * 254", True), ("x - 254", False)])
def test_formal_check_has_its_own_positive_control(component, reference_expression, calibrated):
    # Removing the formal reference check lends known-value calibration to an uncalibrated formal check.
    pytest.importorskip("z3")
    (component / "certificate/formal_reference.py").write_text(
        HEADER + f"def reference(x):\n    return {reference_expression}\n", encoding="utf-8")
    put(component / "certificate/proof.json",
        {"parameters": ["x"], "expression": "(* x 254)", "author": "proof author",
         "reference": {"file": PREFIX + "/certificate/formal_reference.py", "function": "reference"}})
    edit_json(component, "certificate/checks.json",
              lambda b: b.update(proofs={"inch": PREFIX + "/certificate/proof.json"}))
    body = issue(component)
    checks = {c["check_id"]: c for c in body["issuer"]["checks"]}
    assert checks["inch:known"]["positive_seq"] is not None
    assert (checks["inch:formal"]["positive_seq"] is not None) is calibrated
    if not calibrated:
        assert any(o[:2] == ["inch:formal", "positive_reference_not_passed"]
                   for o in body["issuer"]["obligations"])
    assert not body["issuer"]["predicates"][0]["refute"]


def page_source(component, raw=b"A whole source page containing the inch table: so 12 inches are 0.3048 metre."):
    edit_json(component, "data/source_register.json", lambda b: b["entries"][0].update(
        hash_scope="retrieved_bytes", sha256=records.sha(raw), clause_sha256=records.sha(CLAUSE.encode())))
    refresh_review(component)
    return raw


def issue_page(component, raw=None):
    raw = page_source(component, *([raw] if raw else []))
    bundle = certificates.load(PACKAGE, source_files={"nist-inch": raw})
    body = certificates.issue(bundle, positions(bundle), today=TODAY)
    certificates.write(bundle, body)
    return body, raw


def test_page_source_c1_rechecks_offline_with_issuance_record(component):
    # Removing source_checks' issuance record lookup makes every page-sourced certificate fail offline.
    body, raw = issue_page(component)
    assert body["source_bytes_required_at_issuance"] == [{"id": "nist-inch", "sha256": records.sha(raw)}]
    assert kit.c1_certificate(target(component), today=TODAY) == []
    assert certificates.verify(certificates.load(PACKAGE), body, today=TODAY) == []


def test_page_source_edited_clause_fails_even_with_old_issuance_record(component):
    # Removing source_checks' clause rehash keeps an edited clause behind an old issuance record.
    issue_page(component)
    edit_json(component, "data/source_register.json", lambda b: b["entries"][0].update(clause="changed"))
    refresh_review(component)
    with pytest.raises(ValueError, match="clause hash"):
        certificates.load(PACKAGE)
    assert kit.c1_certificate(target(component), today=TODAY)


def test_page_source_without_issuance_observation_fails_c1(component):
    # Removing load's requirement of the issuance record accepts a page hash nobody recomputed.
    body, _ = issue_page(component)
    body.pop("source_bytes_required_at_issuance")
    body["digest"] = records.digest(body)
    (component / "certificate/certificate.json").write_bytes(records.canonical(body))
    with pytest.raises(ValueError, match="issuance"):
        certificates.load(PACKAGE)
    assert kit.c1_certificate(target(component), today=TODAY)


def test_page_source_requires_bytes_for_fresh_issuance(component):
    # Removing _issue's fresh-issuance bytes requirement issues a page source nobody held.
    page_source(component)
    with pytest.raises(ValueError, match="offline|issuance"):
        certificates.load(PACKAGE)
    issue_page(component)
    bundle = certificates.load(PACKAGE)
    with pytest.raises(ValueError, match="issuance.*bytes"):
        certificates.issue(bundle, positions(bundle), today=TODAY)


def test_page_source_supplied_bytes_are_always_rechecked(component):
    # Removing source_checks' held-bytes rehash accepts any bytes for a page source.
    issue_page(component)
    with pytest.raises(ValueError, match="source hash differs"):
        certificates.load(PACKAGE, source_files={"nist-inch": b"wrong page"})


def test_reviewed_does_not_require_review_or_source_bytes(component, monkeypatch, capsys):
    # Removing --reviewed's tolerance of a missing review makes the reader's first pass impossible.
    page_source(component)
    expected = records.read_json(component / "data/rule_review.json")["reviewed"]
    (component / "data/rule_review.json").unlink()
    spec = importlib.util.spec_from_file_location("reviewed_tool", Path(__file__).parents[2] / "tools/issue_certificate.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    monkeypatch.setattr(tool, "history_positions", lambda _: pytest.fail("reviewed must not read history"))
    assert tool.main(["--reviewed", PACKAGE]) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_candidate_adapter_projects_actual_mutated_function(component):
    # Removing the adapter's call of the watched target would observe nothing the mutants changed.
    path = component / "certificate/adapter.py"
    path.write_text(HEADER + 'def evaluate(function, rule, inputs):\n    return function(float(inputs["x"]))\n', encoding="utf-8")
    edit_json(component, "certificate/checks.json", lambda b: b.update(adapter=ADAPTER))
    refresh_review(component)  # the adapter is a reviewed input
    body = issue(component)
    assert all(any(c["killed"] for c in check["controls"]) for check in body["issuer"]["checks"])
    assert certificates.verify(certificates.load(PACKAGE), body, today=TODAY) == []


def test_page_source_check_command_replays_offline(component, monkeypatch):
    # Removing --check's offline replay of the issuance record fails every page-sourced certificate.
    body, _ = issue_page(component)
    cli = tool()
    monkeypatch.setattr(cli, "today", lambda: TODAY)
    monkeypatch.setattr(cli, "history_positions", positions)
    assert cli.main(["--check", PACKAGE]) == 0
    edit_json(component, "data/source_register.json", lambda b: b["entries"][0].update(clause="edited"))
    assert cli.main(["--check", PACKAGE]) == 1
    assert body["source_bytes_required_at_issuance"]


def test_family_register_includes_shared_data_but_not_sibling_certificates(component):
    # Removing input_bases' shared-only filter would bind a sibling member's certificate inputs.
    root, folder, declaration = records.layout(PACKAGE)
    shared = folder.parent
    put(shared / "_data/source_register.json", records.read_json(folder / "data/source_register.json"))
    put(shared / "_data/other.json", {"bound": 1})
    (shared / "_sources.py").write_bytes((folder / "sources.py").read_bytes())
    put(shared / "sibling/certificate/cases.json", {"unrelated": True})
    put(shared / "sibling/data/rule_review.json", {"unrelated": True})
    declaration = {**declaration, "register": "g6/certfixture/_data/source_register.json"}
    files = records.input_files(root, folder, declaration)
    assert "g6/certfixture/_data/other.json" in files
    assert "g6/certfixture/_sources.py" in files
    assert not any("/sibling/" in name for name in files)
    assert records.rule_path(root, declaration) == shared / "_sources.py"


@pytest.mark.parametrize("module", [
    "g6.ml._sources", "g6.ml.adapt_bayesian.sources", "g6.embodied._motion.sources",
])
def test_component_register_checks_reject_changed_external_clause(module, monkeypatch):
    # Removing register_problems' clause rehash keeps an edited external clause.
    sources = importlib.import_module(module)
    assert sources.register_problems() == []
    register = sources.register()
    entry = next(e for e in register["entries"] if e.get("hash_scope") != "clause_utf8")
    entry["clause"] += " changed"
    monkeypatch.setattr(sources, "register", lambda: register)
    assert any("clause hash" in p for p in sources.register_problems())


def test_write_refuses_a_record_of_bytes_it_never_held(component):
    # Removing write's held-bytes requirement lets an offline reissue be written as if its bytes were checked.
    issue_page(component)
    bundle = certificates.load(PACKAGE)  # offline: the issuance record stands in for the page bytes
    body = certificates._reissue(bundle, positions(bundle), today=TODAY)
    with pytest.raises(ValueError, match="bytes"):
        certificates.write(bundle, body)


def test_markdown_says_what_offline_checks_cannot_establish(component):
    # Removing markdown's issuance-bytes paragraph hides that offline checks trust an unsigned record.
    sentence = ("their full bytes were hashed by the issuing tool at issue; checks without the bytes recompute only "
                "the clause hash and trust this unsigned record")
    assert sentence not in certificates.markdown(issue(component)).decode("utf-8")
    body, raw = issue_page(component)
    assert body["source_bytes_required_at_issuance"] == [{"id": "nist-inch", "sha256": records.sha(raw)}]
    assert "nist-inch: " + sentence in (component / "certificate/CERTIFICATE.md").read_text(encoding="utf-8")


def test_edited_adapter_refuses_the_old_review(component):
    # Removing adapter_sha256 from the reviewed hashes reuses a review for an adapter nobody read.
    path = component / "certificate/adapter.py"
    path.write_text(HEADER + 'def evaluate(function, rule, inputs):\n    return function(inputs["x"])\n',
                    encoding="utf-8")
    edit_json(component, "certificate/checks.json", lambda b: b.update(adapter=ADAPTER))
    refresh_review(component)
    certificates.load(PACKAGE)
    path.write_text(HEADER + 'def evaluate(function, rule, inputs):\n    return 0.0254 * float(inputs["x"])\n',
                    encoding="utf-8")
    with pytest.raises(ValueError, match="independent pass.*hash"):
        certificates.load(PACKAGE)


@pytest.mark.parametrize("route", ["adapter", "component"])
def test_a_raising_target_is_an_error_row_even_when_caught(route):
    # Removing CHILD's watched target lets an adapter, or component code it calls, answer for a target that raised.
    from g6.workforce._certificate_runner import observe
    implementation = (HEADER + "def convert(x):\n    raise ValueError('refused')\n\n\n"
                      "def guarded(x):\n    try:\n        return convert(x)\n    except ValueError:\n"
                      "        return x * 0.0254\n")
    call = ("    try:\n        return function(float(inputs['x']))\n    except ValueError:\n"
            "        return float(inputs['x']) * 0.0254\n" if route == "adapter" else
            "    from g6.certfixture.inches import implementation\n"
            "    return implementation.guarded(float(inputs['x']))\n")
    adapter = HEADER + "def evaluate(function, rule, inputs):\n" + call
    found = observe({PREFIX + "/implementation.py": implementation.encode(),
                     ADAPTER: adapter.encode()}, "candidate",
                    file=PREFIX + "/implementation.py", function="convert", adapter=ADAPTER,
                    rule="inch", inputs=[{"x": "1"}])
    assert found == {"values": [{"error": "ValueError"}]}


def test_family_shared_code_binds_a_member_whose_register_is_its_own(component):
    # Removing input_bases' family-root entry leaves the shared code a member imports outside its artifact.
    shared = component.parent / "_shared.py"
    shared.write_text(HEADER + "FACTOR = 0.0254\n", encoding="utf-8")
    (component / "implementation.py").write_text(
        HEADER + "from g6.certfixture._shared import FACTOR\n\n\ndef convert(x):\n    return float(x) * FACTOR\n",
        encoding="utf-8")
    refresh_review(component)
    issue(component)
    assert "certificate artifact bytes changed" not in records.inspect(PACKAGE, today=TODAY)[2]
    shared.write_text(HEADER + "FACTOR = 0.0255\n", encoding="utf-8")
    assert "certificate artifact bytes changed" in records.inspect(PACKAGE, today=TODAY)[2]


def test_consistent_clause_edit_is_caught_only_by_reissue_comparison(component, monkeypatch):
    # Removing verify's reissue comparison (or inspect's cited-register comparison) keeps this edit current.
    body, _ = issue_page(component)
    edited = CLAUSE + "; edited"
    edit_json(component, "data/source_register.json", lambda b: b["entries"][0].update(
        clause=edited, clause_sha256=records.sha(edited.encode())))
    refresh_review(component)
    bundle = certificates.load(PACKAGE)  # the clause hash and the issuance record still agree offline
    assert "component certificate differs from offline reissue" in certificates.verify(bundle, body, today=TODAY)
    assert "certificate sources are stale or changed" in records.inspect(PACKAGE, today=TODAY)[2]
    cli = tool()
    monkeypatch.setattr(cli, "today", lambda: TODAY)
    monkeypatch.setattr(cli, "history_positions", positions)
    assert cli.main(["--check", PACKAGE]) == 1
    assert cli.main([PACKAGE]) == 1  # a fresh issue needs the page bytes, which this run does not hold


@pytest.mark.parametrize("recorded", [None, "0" * 64])
def test_independent_pass_must_record_the_source_hash_it_computed(component, recorded):
    # Removing load's source_sha256 comparison accepts a review that never recorded holding the source bytes.
    def change(body):
        if recorded is None:
            body["rules"][0].pop("source_sha256")
        else:
            body["rules"][0]["source_sha256"] = recorded
    edit_json(component, "data/rule_review.json", change)
    with pytest.raises(ValueError, match="source.?(hash|sha256)"):
        certificates.load(PACKAGE)


def test_load_uses_the_one_reviewed_computation(component, monkeypatch):
    # Removing load's call to records.reviewed lets two computations of the review targets drift apart.
    monkeypatch.setattr(records, "reviewed", lambda package: {"drifted": package})
    with pytest.raises(ValueError, match="independent pass.*hash"):
        certificates.load(PACKAGE)


def test_unknown_source_file_id_is_refused(component, tmp_path, monkeypatch):
    # Removing load's source-id check silently ignores bytes meant for a source the register does not have.
    with pytest.raises(ValueError, match="unknown source"):
        certificates.load(PACKAGE, source_files={"nist-inch-typo": b"page"})
    page = tmp_path / "page.bin"
    page.write_bytes(b"page")
    cli = tool()
    monkeypatch.setattr(cli, "today", lambda: TODAY)
    monkeypatch.setattr(cli, "history_positions", positions)
    assert cli.main([PACKAGE, "--source-file", f"nist-inch-typo={page}"]) == 1
    assert not (component / "certificate/certificate.json").exists()


# --- version 2a: tiers, generated inputs, mechanical checks, binding, signatures -------------------------------------


def with_rules(component, table, findings):
    """Replace the rule table and the reader's findings, and record the reader's new targets."""
    rules(component / "sources.py", table)
    edit_json(component, "data/rule_review.json", lambda b: b.update(rules=findings))
    refresh_review(component)


@pytest.mark.parametrize("effect,needle", [
    ("changes which metric heads the result", "not a guard: it changes the result"), ("", "not a guard")])
def test_an_own_rule_that_changes_the_result_is_not_a_guard(component, effect, needle):
    # Removing _tiered's GUARD_EFFECTS check loads an own rule whose effect is free text.
    rules(component / "sources.py", {"inch": INCH, "headline": (
        f'Rule("pick the headline metric", "own", "g6", "{effect}", tier="guard", implements="{IMPL}:convert")')})
    with pytest.raises(ValueError, match=needle):
        records.rules_from(component / "sources.py")
    with pytest.raises(ValueError, match=needle):
        certificates.load(PACKAGE)


@pytest.mark.parametrize("rule,needle", [
    (f'Rule("inch to metre", "register", "nist-inch", implements="{IMPL}:convert")', "computes or property"),
    ('Rule("inch to metre", "register", "nist-inch", tier="computes")', "path:function"),
    (f'Rule("inch to metre", "register", "nist-inch", tier="computes", implements="{IMPL}:missing")',
     "top-level function"),
    ('Rule("caller sizes", "caller", ("x",), tier="guard")', "caller rule"),
])
def test_a_rule_names_its_tier_and_implementation(component, rule, needle):
    # Removing _tiered's tier/implements checks (or load's function check) certifies an unnamed implementation.
    table = {"inch": rule} if "caller" not in rule else {"inch": INCH, "sizes": rule}
    rules(component / "sources.py", table)
    with pytest.raises(ValueError, match=needle):
        certificates.load(PACKAGE)


def test_the_implementation_map_comes_from_the_rule_and_is_reviewed(component):
    # Removing load's refusal of checks.json implementations keeps a map the reader's rule hash does not cover.
    edit_json(component, "certificate/checks.json", lambda b: b.update(
        implementations={"inch": {"file": IMPL, "function": "convert"}}))
    with pytest.raises(ValueError, match="implements, not checks.json"):
        certificates.load(PACKAGE)
    edit_json(component, "certificate/checks.json", lambda b: b.pop("implementations"))
    (component / "implementation.py").write_text(
        HEADER + "def convert(x):\n    return float(x) * 0.0254\n\n\ndef other(x):\n    return float(x) * 0.0254\n",
        encoding="utf-8")
    refresh_review(component)
    certificates.load(PACKAGE)
    rules(component / "sources.py", {"inch": INCH.replace(":convert", ":other")})
    with pytest.raises(ValueError, match="independent pass.*hash"):  # implements is inside rules_sha256
        certificates.load(PACKAGE)
    refresh_review(component)
    body = issue(component)
    assert body["rules"]["inch"]["implements"] == IMPL + ":other"


@pytest.mark.parametrize("variant,needle", [
    ("unprinted", "printed in its source"), ("degenerate", "degenerate"), ("unsaid", r"says whether its source"),
])
def test_a_computes_rule_needs_a_printed_case_at_a_non_degenerate_point(component, variant, needle):
    # Removing load's printed/degenerate requirement certifies a computed rule on an identity point alone.
    def change(body):
        case = body["cases"][0]
        if variant == "unprinted":
            case["printed"] = False
            case.pop("quote")
        elif variant == "degenerate":
            case.update(input={"x": "1"}, expected="0.0254")
        else:
            case.pop("printed")
    edit_json(component, "certificate/cases.json", change)
    refresh_review(component)
    with pytest.raises(ValueError, match=needle):
        certificates.load(PACKAGE)


def test_a_printed_value_missing_from_the_source_is_a_named_gap(component):
    # Removing _issue's printed_value_not_in_source gap lets a transcribed number nobody can find in the source through.
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(
        input={"x": "6"}, expected="0.1524", quote="0.5 foot (6 inches) is 0.1524 metre"))
    refresh_review(component)
    assert issue(component)["printed_values"][0]["found"] is True  # half a foot is printed in the clause
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(
        input={"x": "24"}, expected="0.6096", quote="so 24 inches are 0.6096 metre"))
    refresh_review(component)
    body = issue(component)
    assert body["printed_values"][0]["found"] is False
    assert ("inch:known", "printed_value_not_in_source") in [(o[0], o[1]) for o in body["issuer"]["obligations"]]
    assert body["issuer"]["state"] == "blocked-escalated"
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


def test_a_printed_value_is_found_in_held_page_bytes_and_replayed_offline(component):
    # Removing _printed's issuance-record replay fails every offline recheck of a page-sourced printed case.
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(quote="12 in 0.3048 m"))
    body, _ = issue_page(component, b"<td>12 in</td> <td>see the table</td>")
    assert body["printed_values"][0]["found"] is False
    body, _ = issue_page(component, b"<td>12 in</td>\n <td><span class='mf'>0.3048</span> m</td>")
    assert body["printed_values"][0]["found"] is True
    assert certificates.verify(certificates.load(PACKAGE), body, today=TODAY) == []


def test_printed_as_reads_a_power_of_ten_and_must_equal_the_expected_value(component):
    # Removing _case's printed_as comparison lets a case name a printed number other than its expected value.
    assert certificates._as_number("9.4 × 10−21") == certificates._number("0.0000000000000000000094")
    assert certificates._as_number("2.54 E-02".replace(" E", "e")) == certificates._number("0.0254")
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(printed_as="0.3049"))
    refresh_review(component)
    with pytest.raises(ValueError, match="printed_as"):
        certificates.load(PACKAGE)


def test_a_printed_case_quotes_its_source_with_every_number_it_rests_on(component):
    # Removing _printed's quote checks (the quote in the held text, its length, each printed, input and expected
    # number in it) lets a case rest on numbers the page prints only far apart.
    far = b"<p>Twelve: 12 inches." + b" Some other text." * 120 + b" They are <b>0.3048</b> metre.</p>"
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(quote="They are 0.3048 metre."))
    body, raw = issue_page(component, far)
    assert body["printed_values"][0]["found"] is False  # the input 12 is not in the quote
    assert ("inch:known", "printed_value_not_in_source") in [(o[0], o[1]) for o in body["issuer"]["obligations"]]
    whole = " ".join(certificates.html.unescape(re.sub(r"<[^>]*>", "", raw.decode())).split())
    assert whole.startswith("Twelve: 12 inches.") and len(whole) > certificates.QUOTE_MAX
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(quote=whole))
    assert issue_page(component, far)[0]["printed_values"][0]["found"] is False  # verbatim, but too long
    near = b"<p>Twelve: 12 inches.\n They are <b>0.3048</b> metre.</p>"
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(
        quote="Twelve: 12 inches. They are 0.3048 metre."))
    assert issue_page(component, near)[0]["printed_values"][0]["found"] is True
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].pop("quote"))
    refresh_review(component)
    with pytest.raises(ValueError, match="quote"):
        certificates.load(PACKAGE)


def test_a_quote_reads_numbers_grouped_by_thousands(component):
    # B7. Removing _quoted_numbers' grouped reading never finds a printed input of 22000 the source prints as 22,000.
    from decimal import Decimal
    numbers = certificates._quoted_numbers("costs 22,000 and 1,234,567.5 dollars; 2,07 and (1,2)")
    assert {Decimal(22000), Decimal("1234567.5")} <= numbers
    assert {Decimal(22), Decimal(0), Decimal(2), Decimal(7), Decimal(1)} <= numbers  # the plain readings stay
    assert not {Decimal(207), Decimal(12), Decimal("2.07")} & numbers  # a comma before other than 3 digits groups nothing
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(
        input={"x": "22000"}, expected="558.8000", printed_as=["558.8"], quote="so 22,000 inches are 558.8 metres"))
    body, _ = issue_page(component, b"<p>A long table: so 22,000 inches are 558.8 metres.</p>")
    assert body["printed_values"][0]["found"] is True


def test_printed_as_covers_every_expected_value(component):
    # Removing _case's equality of printed_as with the expected values lets a case name only some of them.
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(
        expected=["0.3048", "0.1524"], printed_as=["0.3048"], input={"x": ["12", "6"]}))
    refresh_review(component)
    with pytest.raises(ValueError, match="printed_as"):
        certificates.load(PACKAGE)


def test_a_source_that_prints_by_truncation_is_compared_by_cutting_digits(component):
    # Removing _close's truncate branch fails a value the source printed by cutting digits (0.94861 as 0.948).
    assert certificates._close("0.94861", "0.948", 3, "truncate")
    assert not certificates._close("0.94861", "0.948", 3)
    assert not certificates._close("0.9479", "0.948", 3, "truncate")
    assert certificates._close("-0.94861", "-0.948", 3, "truncate")
    (component / "implementation.py").write_text(
        HEADER + "def convert(x):\n    return float(x) * 0.0254 + 0.00008\n", encoding="utf-8")
    refresh_review(component)
    assert issue(component)["issuer"]["receipts"][0]["result"] == "fail"  # 0.30488 without a declared truncation
    edit_json(component, "data/source_register.json", lambda b: b["entries"][0].update(rounding="truncate"))
    refresh_review(component)
    body = issue(component)
    assert body["issuer"]["receipts"][0]["result"] == "pass"  # 0.30488 printed as 0.3048


def test_rounding_is_the_register_entrys_and_a_case_inherits_it(component):
    # M1. Removing load's refusal of a case's own rounding makes truncation each case's choice, which the reader
    # never reviews with the source.
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(rounding="truncate"))
    refresh_review(component)
    with pytest.raises(ValueError, match="register entry"):
        certificates.load(PACKAGE)
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].pop("rounding"))
    edit_json(component, "data/source_register.json", lambda b: b["entries"][0].update(rounding="up"))
    refresh_review(component)
    with pytest.raises(ValueError, match="rounding"):
        certificates.load(PACKAGE)


ORDER = "a conversion factor is a positive constant, so a longer length converts to a longer length"
PROPERTY = (f'Rule("a conversion keeps the order of lengths", "register", "nist-order", tier="property", '
            f'implements="{IMPL}:convert")')
TIER_REASON = "the clause states an order and prints no value G6's functions can be run on"


def unprinted_source(component):
    """A second register entry whose clause prints no number at all."""
    edit_json(component, "data/source_register.json", lambda b: b["entries"].append({
        **b["entries"][0], "id": "nist-order", "clause": ORDER, "sha256": hashlib.sha256(ORDER.encode()).hexdigest(),
        "section": "Appendix B, the conversion factors"}))


def test_a_property_rule_needs_the_readers_attestation_and_prints_as_such(component, confined):
    # Removing load's tier_reason requirement certifies a property rule on a bare boolean; removing the attestation
    # from markdown or state_line hides which rules no printed value checks.
    unprinted_source(component)
    with_rules(component, {"inch": INCH, "order": PROPERTY}, [finding("inch"), finding("order")])
    with pytest.raises(ValueError, match="tier_justified"):
        certificates.load(PACKAGE)
    with_rules(component, {"inch": INCH, "order": PROPERTY},
               [finding("inch"), finding("order", tier_justified=True)])
    with pytest.raises(ValueError, match="tier_reason"):
        certificates.load(PACKAGE)
    with_rules(component, {"inch": INCH, "order": PROPERTY},
               [finding("inch"), finding("order", tier_justified=True, tier_reason=TIER_REASON)])
    body = issue(component)
    checks = {c["check_id"] for c in body["issuer"]["checks"]}
    assert checks == {"inch:known", "inch:second_route", "order:second_route"}
    assert body["issuer"]["state"] == "verified" and body["falsification"]["order"]["count"] == 8
    assert body["attested"] == [{"rule": "order", "tier": "property", "reason": TIER_REASON}]
    text = (component / "certificate/CERTIFICATE.md").read_text(encoding="utf-8")
    assert f"- order: property (no printed value is checked; the independent reader's attestation: {TIER_REASON})" in text
    assert "prints no worked value" not in text
    assert "property-checked, no printed value: order" in body["not_checked"][0]
    evidence = records.add_evidence({}, PACKAGE, ["inch"], today=TODAY)["evidence"][0]
    assert "property-checked, no printed value: order" in evidence["not_checked"][0]
    assert vetting.status(PACKAGE, today=TODAY) == vetting.REVIEWED


def test_a_threshold_of_g6s_own_choice_caps_the_state_and_is_flagged(component):
    # Removing _issue's own_choice gates (or the markdown flag, or state_line's naming) presents G6's own threshold
    # in a register rule as if its source set it.
    unprinted_source(component)
    chosen = PROPERTY.replace('tier="property"', 'tier="property", own_choice=True')
    with_rules(component, {"inch": INCH, "order": chosen},
               [finding("inch"), finding("order", tier_justified=True, tier_reason=TIER_REASON)])
    assert records.rules_from(component / "sources.py")["order"]["own_choice"] is True
    body = issue(component)
    assert body["issuer"]["state"] == "qualified-draft" and body["issuer"]["violations"] == []
    assert ["gate:own_choice:order", "human_decision_missing"] in body["issuer"]["gaps"]
    assert "G6's own choice, which no source sets: order" in body["not_checked"][0]
    text = (component / "certificate/CERTIFICATE.md").read_text(encoding="utf-8")
    assert "(G6's own choice: no source sets it)" in text.split("- order: property", 1)[1].split("\n", 1)[0]
    guarded(component)
    text = (component / "certificate/CERTIFICATE.md").read_text(encoding="utf-8") if issue(component) else ""
    assert "- too_long: guard (G6's own threshold: no source sets it)" in text
    rules(component / "sources.py", {"inch": INCH.replace('tier="computes"', 'tier="computes", own_choice=1')})
    with pytest.raises(ValueError, match="own_choice"):
        records.rules_from(component / "sources.py")


def test_a_property_rule_may_have_unprinted_known_cases_and_never_a_printed_one(component, confined):
    # Removing _case's property branch refuses the identity known values version 1 had (or admits a printed one).
    unprinted_source(component)
    case = {"rule": "order", "source": "nist-order", "location": "the case author's value: 10 inches, 0.254 metre",
            "input": {"x": "10"}, "expected": "0.2540", "precision": 4, "printed": False}
    edit_json(component, "certificate/cases.json", lambda b: b["cases"].append(case))
    with_rules(component, {"inch": INCH, "order": PROPERTY},
               [finding("inch"), finding("order", tier_justified=True, tier_reason=TIER_REASON)])
    body = issue(component)
    assert {c["check_id"] for c in body["issuer"]["checks"]} >= {"order:known", "order:second_route"}
    assert body["issuer"]["state"] == "verified"
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][-1].update(printed=True))
    refresh_review(component)
    with pytest.raises(ValueError, match="printed: false"):
        certificates.load(PACKAGE)


GUARD_CODE = '''

def too_long(x):
    if float(x) > 100:
        raise ValueError("refused: over 100 inches")
    return float(x) * 0.0254
'''
GUARD_ADAPTER = '''
def evaluate(function, rule, inputs):
    if rule != "too_long":
        return function(inputs["x"])
    try:
        function(inputs["x"])
    except ValueError:
        return 1
    return 0
'''


def guarded(component, source="g6", roles=("fires", "holds", "recall"), adapter=GUARD_ADAPTER):
    path = component / "implementation.py"
    path.write_text(path.read_text(encoding="utf-8") + GUARD_CODE, encoding="utf-8")
    (component / "certificate/adapter.py").write_text(HEADER + adapter, encoding="utf-8")
    edit_json(component, "certificate/checks.json", lambda b: b.update(adapter=ADAPTER, refusals=["ValueError"]))
    inputs = {"fires": "100.000001", "holds": "100", "recall": "5000"}
    edit_json(component, "certificate/cases.json", lambda b: b["cases"].extend(
        {"rule": "too_long", "source": source, "role": role, "location": "G6's own threshold: 100 inches",
         "input": {"x": inputs[role]}} for role in roles))
    guard = (f'Rule("a length over 100 inches is refused", "own", "{source}", "refuse", tier="guard", '
             f'implements="{IMPL}:too_long")')
    own = {"source_sha256": None, "clause_fair": None} if source == "g6" else {}
    with_rules(component, {"inch": INCH, "too_long": guard}, [finding("inch"), finding("too_long", **own, **EFFECT)])


#: The reader's word that a guard's code does only what its effect says.
EFFECT = {"effect_only": True, "effect_reason": "the code only raises past the threshold; the value is unchanged"}


def test_a_guard_needs_its_firing_holding_and_recall_cases(component):
    # Removing load's guard role requirement issues a guard with no recall set.
    guarded(component, roles=("fires", "holds"))
    with pytest.raises(ValueError, match="recall set"):
        certificates.load(PACKAGE)


def test_a_guard_passes_with_its_cases_and_its_removal_is_caught(component, confined):
    # Removing the removal defect (or its firing-case requirement) lists a guard whose removal nobody noticed.
    guarded(component)
    body = issue(component)
    assert body["issuer"]["state"] == "verified"
    guard = next(c for c in body["issuer"]["checks"] if c["check_id"] == "too_long:known")
    assert ":removal:0:" in guard["controls"][0]["control_id"] and guard["controls"][0]["killed"]
    assert [r["passed"] for r in body["observations"]["too_long"]["known"]] == [True, True, True]
    assert "- too_long: guard (G6's own threshold: no source sets it)" in (
        component / "certificate/CERTIFICATE.md").read_text(encoding="utf-8")
    assert vetting.status(PACKAGE, today=TODAY) == vetting.REVIEWED


def test_a_guard_whose_removal_breaks_no_firing_case_is_a_named_gap(component):
    # Removing _issue's guard_removal_not_caught gap lists a guard whose cases fire whatever the code does.
    guarded(component, adapter=GUARD_ADAPTER.replace("    return 0\n", "    return int(float(inputs['x']) != 100)\n"))
    body = issue(component)
    assert ("too_long:known", "guard_removal_not_caught") in [(o[0], o[1]) for o in body["issuer"]["obligations"]]
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


def test_a_guard_needs_the_readers_word_that_its_code_only_has_its_effect(component):
    # Removing load's effect_only requirement certifies a guard on its declared effect word alone.
    guarded(component)
    edit_json(component, "data/rule_review.json", lambda b: b["rules"][1].pop("effect_reason"))
    with pytest.raises(ValueError, match="effect_only"):
        certificates.load(PACKAGE)
    edit_json(component, "data/rule_review.json", lambda b: b["rules"][1].update(effect_only=False, effect_reason="x"))
    with pytest.raises(ValueError, match="effect_only"):
        certificates.load(PACKAGE)


DOUBLED = '''

def doubled(x):
    value = float(x) * 0.0254
    if float(x) > 100:
        return value * 2
    return value
'''


def test_a_guard_whose_check_catches_only_its_removal_is_a_named_gap(component):
    # The reviewer's probe P2: a rule that doubles the result, declared a note guard, whose cases are far from its
    # threshold. Removing _issue's boundary-control requirement for guards lists it on its removal alone.
    path = component / "implementation.py"
    path.write_text(path.read_text(encoding="utf-8") + DOUBLED, encoding="utf-8")
    (component / "certificate/adapter.py").write_text(HEADER + '''
def evaluate(function, rule, inputs):
    if rule == "inch":
        return function(inputs["x"])
    out = function(inputs["x"])
    return 0 if out is None else int(out != float(inputs["x"]) * 0.0254)
''', encoding="utf-8")
    edit_json(component, "certificate/checks.json", lambda b: b.update(adapter=ADAPTER))
    inputs = {"fires": "5000", "holds": "0", "recall": "6000"}
    edit_json(component, "certificate/cases.json", lambda b: b["cases"].extend(
        {"rule": "doubled", "source": "g6", "role": role, "location": "G6's own threshold: 100 inches",
         "input": {"x": inputs[role]}} for role in ("fires", "holds", "recall")))
    guard = f'Rule("over 100 inches the reply adds a note", "own", "g6", "note", tier="guard", implements="{IMPL}:doubled")'
    with_rules(component, {"inch": INCH, "doubled": guard},
               [finding("inch"), finding("doubled", source_sha256=None, clause_fair=None, **EFFECT)])
    body = issue(component)
    check = next(c for c in body["issuer"]["checks"] if c["check_id"] == "doubled:known")
    assert check["controls"][0]["killed"]  # its removal is caught
    assert ("doubled:known", "no_seeded_defect") in [(o[0], o[1]) for o in body["issuer"]["obligations"]]
    assert body["issuer"]["state"] == "blocked-escalated"
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


def test_a_guard_catches_its_threshold_shifted_and_its_comparison_flipped(component):
    # Removing removal()'s sibling boundary() seeds no threshold or comparison change for a guard to catch.
    guarded(component)
    body = issue(component)
    check = next(c for c in body["issuer"]["checks"] if c["check_id"] == "too_long:known")
    boundary = [c for c in check["controls"] if ":boundary:" in c["control_id"]]
    assert len(boundary) == 3 and all(c["killed"] for c in boundary)  # > to >=, 100 to 200 and to 50
    assert certificates.boundary(GUARD_CODE, "too_long")[0].count(">=") == 1


PHRASE = '''

def forbidden(x):
    if "forbidden" in x:
        raise ValueError("refused: a forbidden word")
    return x
'''


def phrase_guarded(component, adapter):
    """A guard on a phrase: no comparison or number for G6 to move."""
    path = component / "implementation.py"
    path.write_text(path.read_text(encoding="utf-8") + PHRASE, encoding="utf-8")
    (component / "certificate/adapter.py").write_text(HEADER + adapter, encoding="utf-8")
    edit_json(component, "certificate/checks.json", lambda b: b.update(adapter=ADAPTER, refusals=["ValueError"]))
    inputs = {"fires": "forbidden", "holds": "forbid", "recall": "a forbidden word"}
    edit_json(component, "certificate/cases.json", lambda b: b["cases"].extend(
        {"rule": "forbidden", "source": "g6", "role": role, "location": "G6's own phrase: forbidden",
         "input": {"x": inputs[role]}} for role in ("fires", "holds", "recall")))
    guard = f'Rule("the word forbidden is refused", "own", "g6", "refuse", tier="guard", implements="{IMPL}:forbidden")'
    with_rules(component, {"inch": INCH, "forbidden": guard},
               [finding("inch"), finding("forbidden", source_sha256=None, clause_fair=None, **EFFECT)])


def test_a_guard_with_no_threshold_to_move_rests_on_its_caught_removal_as_a_named_gap(component, confined):
    # Owner, 2026-10-24 (duty_of_care's phrase guards). Removing _issue's removal fallback blocks a guard whatever its
    # cases catch; removing its no_threshold gate (or state_line's or the tier note's naming of it) lists it as
    # verified on its removal alone.
    phrase_guarded(component, GUARD_ADAPTER.replace("too_long", "forbidden"))
    assert certificates.boundary(PHRASE, "forbidden") == []
    body = issue(component)
    assert body["issuer"]["state"] == "qualified-draft" and body["issuer"]["violations"] == []
    assert ["gate:no_threshold:forbidden", "human_decision_missing"] in body["issuer"]["gaps"]
    assert ("these guards have no threshold G6 can move (no comparison or number in their code), so only removing "
            "each one tests its cases: forbidden.") in body["not_checked"][0]
    assert ("- forbidden: guard (G6's own threshold: no source sets it) (no threshold G6 can move: only its removal "
            "tests its cases)") in (component / "certificate/CERTIFICATE.md").read_text(encoding="utf-8")
    assert vetting.status(PACKAGE, today=TODAY) == vetting.REVIEWED  # it does not by itself block listing


def test_a_guard_with_no_threshold_whose_removal_is_not_caught_stays_blocked(component):
    # Removing _issue's caught-removal requirement for a guard with no threshold lists one whose cases fire whatever
    # its code does.
    phrase_guarded(component, GUARD_ADAPTER.replace("too_long", "forbidden").replace(
        "    return 0\n", "    return int('forbidden' in inputs['x'])\n"))
    body = issue(component)
    obligations = [(o[0], o[1]) for o in body["issuer"]["obligations"]]
    assert ("forbidden:known", "no_seeded_defect") in obligations
    assert ("forbidden:known", "guard_removal_not_caught") in obligations
    assert vetting.status(PACKAGE, today=TODAY) == vetting.AWAITING


def test_a_refusal_not_declared_is_still_an_error_row(component):
    # Removing _target_values' guard-only refusals lets any rule's adapter swallow the target's exception.
    guarded(component)
    edit_json(component, "certificate/checks.json", lambda b: b.update(refusals=[]))
    refresh_review(component)
    body = issue(component)
    assert next(r for r in body["issuer"]["receipts"] if r["check_id"] == "too_long:known")["result"] == "unknown"


def test_a_guard_citing_a_source_needs_the_readers_clause_finding(component, confined):
    # Removing load's own-threshold branch would let a cited guard skip the reader's source hash.
    guarded(component, source="nist-inch")
    assert issue(component)["issuer"]["state"] == "verified"
    edit_json(component, "data/rule_review.json", lambda b: b["rules"][1].update(source_sha256=None))
    with pytest.raises(ValueError, match="source hash"):
        certificates.load(PACKAGE)


def test_a_clause_read_by_context_only_is_an_attested_named_gap(component):
    # Removing load's context_attested requirement accepts a context-only reading without the reader's word.
    edit_json(component, "data/rule_review.json", lambda b: b["rules"][0].update(clause_fair="by context only"))
    with pytest.raises(ValueError, match="attestation and reason"):
        certificates.load(PACKAGE)
    edit_json(component, "data/rule_review.json", lambda b: b["rules"][0].update(
        context_attested=True, reason="the table gives the factor; the worked foot is the guide's context"))
    body = issue(component)
    assert body["context_only"] == [{"rule": "inch", "context_attested": True,
                                     "reason": "the table gives the factor; the worked foot is the guide's context"}]
    text = (component / "certificate/CERTIFICATE.md").read_text(encoding="utf-8")
    assert "## Read by context only" in text and "- inch: the table gives the factor" in text


def test_a_context_only_reading_caps_the_state_and_is_named_in_every_results_evidence(component):
    # The reviewer's probe P1. Removing _issue's context_only gates (or state_line's naming of them, or markdown's
    # line beside the signature) lists a context-only reading as verified with nothing in a result's evidence.
    edit_json(component, "data/rule_review.json", lambda b: b["rules"][0].update(
        clause_fair="by context only", context_attested=True, reason="the clause does not state the rule"))
    body = issue(component)
    assert body["issuer"]["state"] == "qualified-draft" and body["issuer"]["violations"] == []
    assert ["gate:context_only:inch", "human_decision_missing"] in body["issuer"]["gaps"]
    assert "read by context only: inch" in body["not_checked"][0]
    evidence = records.add_evidence({}, PACKAGE, ["inch"], today=TODAY)["evidence"][0]
    assert evidence["not_checked"][0] == body["not_checked"][0]
    text = (component / "certificate/CERTIFICATE.md").read_text(encoding="utf-8")
    signature = text.index("A detached SSH signature")
    assert text.index("Read by context only (a named gap") == text.index("\n", signature) + 1
    assert vetting.status(PACKAGE, today=TODAY) == vetting.REVIEWED  # it does not by itself block listing


def test_the_reader_no_longer_records_what_issuance_recomputes(component):
    # Removing load's finding-key check accepts an old record whose hash_matches nobody recomputes.
    edit_json(component, "data/rule_review.json", lambda b: b["rules"][0].update(hash_matches=True))
    with pytest.raises(ValueError, match="recomputed at issuance"):
        certificates.load(PACKAGE)


def test_the_scopes_first_line_comes_from_the_issuer_record(component, confined):
    # Removing state_line from the body (or inspect's comparison of it) lets a hand-written state line stand.
    body = issue(component, retrofit=True)
    assert body["not_checked"][0].startswith("Issuer state qualified-draft: these checks were frozen after the code")
    assert body["not_checked"][1:] == ["Other units and non-finite inputs."]
    assert issue(component)["not_checked"][0].startswith("Issuer state verified, for the recorded bytes")
    edit_json(component, "certificate/scope.json", lambda b: b.update(
        not_checked=["Issuer state verified everywhere.", "Other units and non-finite inputs."]))
    assert "certificate scope is empty or changed" in records.inspect(PACKAGE, today=TODAY)[2]


def test_boundaries_default_to_the_case_inputs(component):
    # Removing the cases from the second route's inputs compares the route only on generated values.
    edit_json(component, "certificate/checks.json", lambda b: b["falsification"].pop("boundaries"))
    refresh_review(component)
    body = issue(component)
    assert body["falsification"]["inch"]["count"] == 8
    assert body["observations"]["inch"]["second_route"][0]["expected"] == "0.3048"
    assert len(body["observations"]["inch"]["second_route"]) == 9


HELPER = "g6/certhelp/factor.py"


def test_a_module_the_implementation_imports_from_outside_the_component_is_bound(component, confined):
    # Removing input_files' import closure leaves a helper outside the component folder out of the artifact.
    helper = component.parents[2] / HELPER
    helper.parent.mkdir(parents=True)
    helper.write_text(HEADER + "FACTOR = 0.0254\n", encoding="utf-8")
    (component / "implementation.py").write_text(
        HEADER + "from g6.certhelp.factor import FACTOR\n\n\ndef convert(x):\n    return float(x) * FACTOR\n",
        encoding="utf-8")
    refresh_review(component)
    body = issue(component)
    assert body["imported_code"] == [HELPER] and body["issuer"]["state"] == "verified"
    assert "certificate artifact bytes changed" not in records.inspect(PACKAGE, today=TODAY)[2]
    helper.write_text(HEADER + "FACTOR = 0.0255\n", encoding="utf-8")
    assert "certificate artifact bytes changed" in records.inspect(PACKAGE, today=TODAY)[2]


def test_the_initialiser_of_every_followed_package_is_bound(component, confined):
    # M2. Removing imported()'s initialisers leaves the package code Python runs on import out of the artifact.
    helper = component.parents[2] / HELPER
    helper.parent.mkdir(parents=True)
    helper.write_text(HEADER + "FACTOR = 0.0254\n", encoding="utf-8")
    (helper.parent / "__init__.py").write_text(HEADER + "from g6.certhelp.extra import NAME\n", encoding="utf-8")
    (helper.parent / "extra.py").write_text(HEADER + 'NAME = "extra"\n', encoding="utf-8")
    (component / "implementation.py").write_text(
        HEADER + "from g6.certhelp.factor import FACTOR\n\n\ndef convert(x):\n    return float(x) * FACTOR\n",
        encoding="utf-8")
    refresh_review(component)
    body = issue(component)
    assert body["imported_code"] == ["g6/certhelp/__init__.py", "g6/certhelp/extra.py", HELPER]
    assert body["issuer"]["state"] == "verified"
    (helper.parent / "__init__.py").write_text(HEADER + "from g6.certhelp.extra import NAME  # edited\n",
                                               encoding="utf-8")
    assert "certificate artifact bytes changed" in records.inspect(PACKAGE, today=TODAY)[2]


def test_the_child_imports_no_g6_module_from_outside_its_snapshot(component):
    # M2. Removing the snapshot finder's refusal loads a g6 module the artifact does not bind from the installed tree.
    (component / "implementation.py").write_text(HEADER + '''
def convert(x):
    import importlib
    importlib.import_module("g6.core." + "env")
    return float(x) * 0.0254
''', encoding="utf-8")
    refresh_review(component)
    body = issue(component)
    known = next(r for r in body["issuer"]["receipts"] if r["check_id"] == "inch:known")
    assert known["result"] == "unknown" and body["issuer"]["state"] != "verified"


def test_a_module_name_matches_its_file_exactly(component, tmp_path):
    # Removing _module_path's exact-name test binds grasp.py again as Grasp.py on a case-insensitive file system.
    (tmp_path / "g6/pkg").mkdir(parents=True)
    (tmp_path / "g6/pkg/grasp.py").write_text(HEADER, encoding="utf-8")
    assert records._module_path(tmp_path, "g6.pkg.grasp") == "g6/pkg/grasp.py"
    assert records._module_path(tmp_path, "g6.pkg.Grasp") is None


def test_an_imported_module_is_bound_by_bytes_but_does_not_move_the_artifact(component):
    # Removing history_positions' imported-module split moves a retrofit's code after its checks on a helper edit.
    helper = component.parents[2] / HELPER
    helper.parent.mkdir(parents=True)
    helper.write_text(HEADER + "FACTOR = 0.0254\n", encoding="utf-8")
    (component / "implementation.py").write_text(
        HEADER + "from g6.certhelp.factor import FACTOR\n\n\ndef convert(x):\n    return float(x) * FACTOR\n",
        encoding="utf-8")
    refresh_review(component)
    cli = tool()
    bundle = certificates.load(PACKAGE)
    git = git_repo(component.parents[3], bundle)
    git("add", *["src/" + n for n in records.code_files(bundle.files, bundle.declaration)])
    git("commit", "-m", "code")
    git("add", "src")
    git("commit", "-m", "checks")
    before = cli.history_positions(bundle)
    helper.write_text(HEADER + "FACTOR = 0.0254  # same value, new bytes\n", encoding="utf-8")
    refresh_review(component)
    bundle = certificates.load(PACKAGE)
    with pytest.raises(ValueError, match="imported module"):
        cli.history_positions(bundle)
    git("add", "src")
    git("commit", "-m", "helper edited after the checks")
    after = cli.history_positions(bundle)
    assert after.artifact == before.artifact == 1 and after.frozen == before.frozen
    assert certificates.issue(bundle, after, today=TODAY)["issuer"]["state"] == "qualified-draft"


def test_a_file_removed_after_the_checks_does_not_move_the_code_after_them(component):
    # Removing history_positions' superset match moves a retrofit's code after its checks when a later commit only
    # removes a file the code does not need (2026-10-25: jobs/_calc left the jobs family, and duty_of_care's checks,
    # written after its unchanged code, came out as if frozen before it).
    extra = component / "data" / "unused.json"
    extra.write_text("{}\n", encoding="utf-8")
    refresh_review(component)
    cli = tool()
    bundle = certificates.load(PACKAGE)
    git = git_repo(component.parents[3], bundle)
    git("add", *["src/" + n for n in records.code_files(bundle.files, bundle.declaration)])
    git("commit", "-m", "code")
    git("add", "src")
    git("commit", "-m", "checks")
    extra.unlink()
    refresh_review(component)
    bundle = certificates.load(PACKAGE)
    git("add", "-A", "src")
    git("commit", "-m", "an unused file removed after the checks")
    found = cli.history_positions(bundle)
    assert found.artifact == 1 and set(found.frozen.values()) == {2}


def test_code_edited_after_its_checks_does_not_make_them_look_written_first(component):
    # Placing the code at the first commit with its current bytes turns every retrofit into a check frozen before the
    # code once the code is edited after it (2026-10-25: the cited-only rewording of duty_of_care's crisis.py and of
    # adapt_bayesian's register came after the commit that froze their checks, and 55 retrofits came out as
    # independent checks).
    cli = tool()
    bundle = certificates.load(PACKAGE)
    git = git_repo(component.parents[3], bundle)
    git("add", *["src/" + n for n in records.code_files(bundle.files, bundle.declaration)])
    git("commit", "-m", "code")
    git("add", "src")
    git("commit", "-m", "checks")
    code = component / "implementation.py"
    code.write_text(code.read_text(encoding="utf-8") + "# reworded after the checks\n", encoding="utf-8")
    refresh_review(component)
    bundle = certificates.load(PACKAGE)
    git("add", "-A", "src")
    git("commit", "-m", "the code reworded after the checks")
    found = cli.history_positions(bundle)
    assert found.artifact == 1 and set(found.frozen.values()) == {2}
    body = certificates.issue(bundle, found, today=TODAY)
    assert any("frozen_after_artifact" in reason for _, reason in body["issuer"]["gaps"])


def test_history_lists_only_the_commits_that_change_what_the_certificate_reads(component, monkeypatch):
    # Removing history_positions' unchanged-commit skip lists every commit's whole tree again: on a long history C1
    # then takes about half an hour per certificate.
    cli = tool()
    bundle = certificates.load(PACKAGE)
    git = git_repo(bundle.root.parent, bundle)
    unrelated = bundle.root.parent / "unrelated.txt"
    for n in range(5):
        unrelated.write_text(str(n), encoding="utf-8")
        git("add", "unrelated.txt")
        git("commit", "-m", f"unrelated {n}")
    git("add", *["src/" + name for name in records.code_files(bundle.files, bundle.declaration)])
    git("commit", "-m", "code")
    unrelated.write_text("after the code", encoding="utf-8")
    git("add", "unrelated.txt")
    git("commit", "-m", "unrelated after the code")
    git("add", "src")
    git("commit", "-m", "checks")
    real, listed = cli._git, []

    def counting(where, *args, **kwargs):
        if args[:1] == ("ls-tree",):
            listed.append(args)
        return real(where, *args, **kwargs)

    monkeypatch.setattr(cli, "_git", counting)
    found = cli.history_positions(bundle)
    assert found.artifact == 6 and set(found.frozen.values()) == {8}
    assert len(listed) == 3


FEET = "g6.certfixture.feet"


@pytest.fixture
def shared_set(component):
    """Two members on one family register: inches holds the shared input set, feet inherits it, bound by hash."""
    family = component.parent
    put(family / "_data/source_register.json", records.read_json(component / "data/source_register.json"))
    (family / "_conv.py").write_text(HEADER + "def convert(x):\n    return float(x) * 0.0254\n", encoding="utf-8")
    rules(family / "_sources.py", {
        "inch": 'Rule("inch to metre", "register", "nist-inch", tier="computes", '
                'implements="g6/certfixture/_conv.py:convert")',
        "foot": 'Rule("foot to metre", "register", "nist-inch", tier="computes", '
                'implements="g6/certfixture/feet/own.py:convert_feet")'})
    for name, folder in (("inches", component), ("feet", family / "feet")):
        folder.mkdir(exist_ok=True)
        declaration = {"kinds": ("scientific_statistical",), "register": "g6/certfixture/_data/source_register.json",
                       "independent_pass": f"g6/certfixture/{name}/data/rule_review.json", "signed_review": None}
        (folder / "__init__.py").write_text(HEADER + "__g6_vetting__ = " + repr(declaration) + "\n", encoding="utf-8")
    (component / "sources.py").unlink()
    (component / "data/source_register.json").unlink()
    (family / "feet/own.py").write_text(HEADER + "def convert_feet(x):\n    return float(x) * 0.3048\n",
                                        encoding="utf-8")
    edit_json(component, "certificate/cases.json", lambda b: b["cases"].append(
        {"rule": "foot", "source": "nist-inch", "location": "the guide's half foot", "input": {"x": "0.5"},
         "expected": "0.1524", "precision": 4, "printed": True, "quote": "0.5 foot (6 inches) is 0.1524 metre"}))
    (component / "certificate/second_route.py").write_text(HEADER + '''
from decimal import Decimal
def evaluate(rule, inputs):
    factor = Decimal("0.0254") if rule == "inch" else Decimal("0.3048")
    return Decimal(inputs["x"]) * factor
''', encoding="utf-8")
    put(family / "feet/certificate/checks.json", {"inherits": {
        "path": PREFIX + "/certificate", "sha256": records.inherited_sha256(component.parents[2],
                                                                             PREFIX + "/certificate")}})
    put(family / "feet/certificate/scope.json", {"not_checked": ["Feet beyond the guide's half foot."]})
    put(component / "data/rule_review.json", {"reviewer": "independent source reader", "rules": [finding("inch")]})
    put(family / "feet/data/rule_review.json", {"reviewer": "independent source reader",
                                                "rules": [finding("inch"), finding("foot")]})
    importlib.invalidate_caches()
    refresh_review(component)
    refresh_review(family / "feet", FEET)
    return family


def test_an_inherited_input_set_covers_the_shared_rules_and_is_bound_by_hash(component, shared_set, confined):
    # Removing certificate_dir's inherits hash check lets a member reuse a shared set that changed under it.
    inches = issue(component)
    feet = issue(shared_set / "feet", package=FEET)
    assert {p["predicate"] for p in inches["issuer"]["predicates"]} == {"inch"}  # foot is feet's own code
    assert {p["predicate"] for p in feet["issuer"]["predicates"]} == {"foot", "inch"}
    assert inches["issuer"]["state"] == feet["issuer"]["state"] == "verified"
    assert feet["inherits"]["path"] == PREFIX + "/certificate"
    assert vetting.status(FEET, today=TODAY) == vetting.REVIEWED
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(location="Appendix B.8"))
    with pytest.raises(ValueError, match="inherited certificate inputs changed"):
        certificates.load(FEET)
    assert vetting.status(FEET, today=TODAY) == vetting.AWAITING


def test_evidence_cites_only_rules_the_certificate_covers_or_the_callers(component, shared_set):
    # M3. Removing add_evidence's coverage check lets a result cite inches' certificate for foot, which feet's covers.
    issue(component)
    assert records.add_evidence({}, PACKAGE, ["inch"], today=TODAY)["evidence"][0]["rules_applied"] == ["inch"]
    with pytest.raises(ValueError, match="does not cover"):
        records.add_evidence({}, PACKAGE, ["foot"], today=TODAY)


def test_a_register_entry_cannot_be_named_g6(component):
    # M5. Removing load's refusal lets a register entry pass as G6's own threshold, skipping the reader's source hash
    # and the entry's staleness.
    edit_json(component, "data/source_register.json", lambda b: b["entries"][0].update(id="g6"))
    rules(component / "sources.py", {"inch": INCH.replace('"nist-inch"', '"g6"')})
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(source="g6"))
    refresh_review(component)
    with pytest.raises(ValueError, match='register entry named "g6"'):
        certificates.load(PACKAGE)


def test_a_rule_implemented_in_a_member_that_does_not_share_the_register_is_refused(component, shared_set):
    # Removing covered()'s shared-register condition silently drops a rule no certificate covers.
    init = shared_set / "feet/__init__.py"
    init.write_text(init.read_text(encoding="utf-8").replace("_data/source_register.json",
                                                             "feet/data/source_register.json"), encoding="utf-8")
    put(shared_set / "feet/data/source_register.json", records.read_json(shared_set / "_data/source_register.json"))
    with pytest.raises(ValueError, match="does not share this component's register"):
        certificates.load(PACKAGE)


def test_a_wrong_value_is_a_failure_even_when_another_input_crashed(component, monkeypatch):
    # Removing _measure's wrong-value branch hides a seeded defect whenever another input of the check crashed.
    from types import SimpleNamespace
    edit_json(component, "certificate/cases.json", lambda b: b["cases"].append(
        {**b["cases"][0], "input": {"x": "6"}, "expected": "0.1524", "quote": "0.5 foot (6 inches) is 0.1524 metre"}))
    refresh_review(component)
    mutant = HEADER + "def convert(x):\n    if x == '6':\n        raise ValueError('crash')\n    return float(x)\n"
    monkeypatch.setattr(certificates.mutation_check, "mutants", lambda *a, **k: [SimpleNamespace(source=mutant)])
    body = issue(component)
    known = next(c for c in body["issuer"]["checks"] if c["check_id"] == "inch:known")
    assert known["controls"][0]["killed"] is True
    assert certificates._measure([{"error": "ValueError"}], ["1"], [0]) == ("unknown", [])


def test_precheck_runs_every_mechanical_check_without_a_reader_and_writes_nothing(component, monkeypatch, capsys):
    # Removing load's review=False path makes every gap wait for a reader; removing write's guard writes it.
    (component / "data/rule_review.json").unlink()
    edit_json(component, "certificate/cases.json", lambda b: b["cases"][0].update(input={"x": "24"},
                                                                                  expected="0.6096"))
    cli = tool()
    monkeypatch.setattr(cli, "today", lambda: TODAY)
    monkeypatch.setattr(cli, "history_positions", lambda _: pytest.fail("a precheck reads no history"))
    assert cli.main(["--precheck", PACKAGE]) == 1
    out = capsys.readouterr().out
    assert "inch:known: pass, " in out and "gap: inch:known: printed_value_not_in_source" in out
    assert not (component / "certificate/certificate.json").exists()
    bundle = certificates.load(PACKAGE, review=False)
    with pytest.raises(ValueError, match="no independent pass"):
        certificates.write(bundle, certificates.issue(bundle, positions(bundle), today=TODAY))


def test_d9_counts_no_certificate_input_as_the_components_code(component):
    # Removing d9_imports' certificate-folder skip refuses a second route's reference library as a component import.
    (component / "certificate/second_route.py").write_text(HEADER + "import scipy\n", encoding="utf-8")
    (component / "runtime.py").write_text(HEADER + "import scipy\n", encoding="utf-8")
    hits = kit.d9_imports(kit.Target("inches", PACKAGE, component, tuple(component.rglob("*.py")), "certfixture",
                                     "builtin"))
    assert [Path(h.file).name for h in hits] == ["runtime.py"]


def test_d9_skips_only_the_frameworks_certificate_folder_and_c1_holds_its_adapter(component):
    # M4. Removing d9_imports' exact certificate-folder test skips any folder named certificate; removing load's
    # adapter import rule leaves the adapter, which D9 skips, under no import rule at all.
    nested = component / "tools/certificate/helper.py"
    nested.parent.mkdir(parents=True)
    nested.write_text(HEADER + "import scipy\n", encoding="utf-8")
    hits = kit.d9_imports(kit.Target("inches", PACKAGE, component, tuple(component.rglob("*.py")), "certfixture",
                                     "builtin"))
    assert [Path(h.file).name for h in hits] == ["helper.py"]
    nested.unlink()
    (component / "certificate/adapter.py").write_text(
        HEADER + "import pandas\n\n\ndef evaluate(function, rule, inputs):\n    return function(inputs['x'])\n",
        encoding="utf-8")
    edit_json(component, "certificate/checks.json", lambda b: b.update(adapter=ADAPTER))
    refresh_review(component)
    with pytest.raises(ValueError, match="adapter imports only"):
        certificates.load(PACKAGE)
