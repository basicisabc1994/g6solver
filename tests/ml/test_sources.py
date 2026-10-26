# SPDX-License-Identifier: Apache-2.0
"""The vetting protocol's offline checks for the ml family's rules: every rule resolves to a committed register entry
with every field filled, stale entries raise a finding, and the components resting on them declare their review
state, which waits for an independent pass (g6.core.vetting)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from g6.core import component_certificate as records
from g6.core import vetting
from g6.ml import _sources
from g6.workforce import component_certificate as certificates
from g6.workforce._certificate_runner import observe

SKLEARN = "g6.ml.adapt_sklearn"


def _observed(rule: str, inputs: list[dict], source: str | None = None) -> tuple[list, str, str]:
    """The shared certificate adapter's observations of the rule's own function, run in the certificate's bounded
    child; with ``source``, that function's module is replaced by it (a seeded defect). Also the module's text and
    the function's name."""
    root, folder, declaration = records.layout(SKLEARN, allow_missing_review=True)
    files = records.input_files(root, folder, declaration, include_review=False)
    adapter = records.read_json(records.certificate_dir(root, folder)[0] / "checks.json")["adapter"]
    file, function = records.implementation(records.rules_from(records.rule_path(root, declaration))[rule])
    selected = {k: v for k, v in files.items() if "/certificate/" not in k}
    selected[adapter] = files[adapter]
    if source is not None:
        selected[file] = source.encode("utf-8")
    found = observe(selected, "candidate", file=file, function=function, adapter=adapter, rule=rule, inputs=inputs,
                    refusals=["MLError"])
    return [v.get("value", v.get("error")) for v in found["values"]], (root / file).read_text(encoding="utf-8"), function


def test_every_rule_resolves_to_a_complete_register_entry():
    assert _sources.register_problems() == []
    found = _sources.entries()
    for name, rule in _sources.RULES.items():
        if rule.kind == "register":
            entry = found[rule.source]
            assert all(entry[f] not in (None, "") for f in _sources.FIELDS), name
            assert entry["url"].startswith("https://scikit-learn.org/1.9/"), name
        elif rule.kind == "own":
            # A guard of G6's own can only refuse, escalate, lower a label or add a note (certificates v2a).
            assert rule.effect in ("refuse", "escalate", "lower_label", "note"), name
            assert rule.source == "g6" or rule.source in found, name
        else:
            assert rule.source and not rule.effect, name


def test_thresholds_g6_chose_cite_g6_or_say_they_are_its_own_choice():
    # Certificates v2a fix I3: citing pitfalls-12.2 or cv-3.1 for these guards, or dropping headline_imbalance's
    # own_choice flag, presents a threshold of G6's own as sourced.
    rules = _sources.RULES
    assert {n for n in ("leakage_probes", "too_good", "held_out_from_rows") if rules[n].source == "g6"} == {
        "leakage_probes", "too_good", "held_out_from_rows"}
    assert rules["headline_imbalance"].own_choice is True
    assert [n for n, r in rules.items() if r.own_choice] == ["preprocessing_in_pipeline", "headline_imbalance"]


def test_the_category_cap_is_g6s_own_choice_and_scaling_is_the_estimators():
    # Certificates v2a fix 2, ML1: removing preprocessing_in_pipeline's own_choice leaves G6's 50-category cap
    # unflagged in the Markdown.
    rule = _sources.RULES["preprocessing_in_pipeline"]
    assert rule.own_choice is True and "flagged scale" in rule.rule
    body = {"rules": records.rules_from(Path(_sources.__file__)), "issuer": {"gates": []},
            "attested": [{"rule": "preprocessing_in_pipeline", "tier": "property", "reason": "no printed value"}]}
    assert "(G6's own choice: no source sets it)" in certificates._tier_note("preprocessing_in_pipeline", body)


def test_the_bounds_guard_holds_rows_to_predict_and_its_removal_is_caught():
    # ML2: putting _bounds' check of the rows to predict back in prepare() leaves it intact when the guard is removed.
    cases = [{"predict_cell": 10001}, {"predict_cell": 10000}]
    values, source, function = _observed("bounds", cases)
    assert values == ["1", "0"]
    assert _observed("bounds", cases, certificates.removal(source, function))[0] == ["0", "0"]


def test_the_overfit_gap_guard_is_the_gap_note_alone_and_its_boundary_is_caught():
    # ML3: pointing overfit_gap at said() again certifies a function that also refuses and picks the card.
    assert _sources.RULES["overfit_gap"].implements == "g6/ml/_report.py:_gap_note"
    cases = [{"gap": "0.1", "held_out": "0.8"}, {"gap": "0.099999", "held_out": "0.8"}]
    values, source, function = _observed("overfit_gap", cases)
    assert values == ["1", "0"]
    edges = certificates.boundary(source, function)
    assert edges and any(_observed("overfit_gap", cases, mutant)[0] != values for mutant in edges)


def test_the_hold_out_half_of_grouping_is_its_own_rule_on_make_split():
    # ML5: dropping grouped_hold_out leaves make_split's keeping of identical rows on one side unexamined.
    rule = _sources.RULES["grouped_hold_out"]
    assert (rule.kind, rule.tier, rule.implements) == ("register", "property", "g6/ml/_types.py:make_split")
    rows = {"keys": [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9], "fraction": "0.25"}
    assert _observed("grouped_hold_out", [rows])[0] == ["0"]


def test_rule_texts_and_case_locations_say_what_the_code_applies():
    # ML4 and ML6-ML9: the readers found these said nowhere; removing any one of them fails this.
    rules = _sources.RULES
    assert "two-class numeric" in rules["leakage_probes"].rule and "at least 5 rows (10" in rules["leakage_probes"].rule
    assert "rounded to 6 places" in rules["too_good"].rule
    assert "first label in text order" in rules["baseline"].rule
    assert "ELLIPSIS" not in json.dumps(_sources.entries()["sklearn-1.9-metrics-3.4.6.1"])
    cases = records.read_json(Path(_sources.__file__).parent / "adapt_sklearn/certificate/cases.json")["cases"]
    rmse = next(c for c in cases if c["rule"] == "rmse")
    assert "printed MSE; RMSE checked as its square root" in rmse["location"]


def test_a_problem_in_the_register_is_named(monkeypatch):
    broken = _sources.register()
    broken["entries"][0] = dict(broken["entries"][0], sha256="not a hash", clause="")
    monkeypatch.setattr(_sources, "register", lambda: broken)
    problems = _sources.register_problems()
    assert any("sha256 is not" in p for p in problems) and any("clause not filled in" in p for p in problems)


def test_an_entry_past_its_review_window_raises_a_stale_source_finding():
    assert _sources.stale_sources(dt.date(2026, 10, 22)) == []
    stale = _sources.stale_sources(dt.date(2027, 11, 1))
    assert stale and all(s["finding"] == "stale_source" for s in stale)


def test_cited_rules_carry_their_source_and_say_no_qualified_person_reviewed_them():
    cited = _sources.cited(("held_out", "interval"))
    held, interval = cited["rules"]
    assert held["kind"] == "register" and held["url"] and held["clause"]
    assert interval["kind"] == "own" and interval["effect"] == "note" and interval["source"] == "g6"
    # owner, 2026-10-23: no qualified person's review before listing; each rule has an independent reader's pass
    assert cited["review"] == vetting.NO_QUALIFIED_REVIEW


def test_the_fitting_members_are_listed_on_their_certificates():
    for package in ("g6.ml.adapt_sklearn", "g6.ml.adapt_automl"):
        assert vetting.problems(package, ["scientific_statistical"]) == []
        assert vetting.status(package, ["scientific_statistical"]) == vetting.REVIEWED
