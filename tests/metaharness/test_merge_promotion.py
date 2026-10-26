# SPDX-License-Identifier: Apache-2.0
"""Optional staged promotion consumes only this harness's recorded held-out measurements."""
from dataclasses import replace

import pytest
from pydantic import ValidationError

from g6.metaharness import EvidencePolicy, PromotionPolicy, PromotionRequest, adapt
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("metaharness_merge_rig", Path(__file__).with_name("_merge_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "harness"):
    _spec.loader.exec_module(rig)
harness = rig.harness


def configured(tmp_path):
    old, _ = harness(tmp_path / "old", cost=0.5)
    wf = replace(old.workflow, promotion_policy=PromotionPolicy(draft=0.4, assisted=0.5, autopilot=0.9),
                 policy=EvidencePolicy(max_evaluations=10))
    mh = adapt(wf, workspace=tmp_path / "policy", clock=lambda: 0.0)
    mh.confirm("Ada")
    return mh


def measured(mh):
    mh.baseline()
    return mh.ledger.rows("evaluation")[-1]["report_digest"]


def test_llm_extension_01_claims_never_earn_a_label_or_mode(tmp_path):
    mh = configured(tmp_path)
    result = mh.promotion(PromotionRequest(requested="autopilot", accepted_by="Ada",
                                          security_claims={"least_privilege": True}))
    assert result["mode"] == "shadow" and result["completion_state"] == "blocked-escalated"
    assert result["claims"]["accepted_by"] == "Ada"
    with pytest.raises(ValidationError):
        PromotionRequest(requested="autopliot")
    with pytest.raises(ValidationError):
        PromotionRequest(requested="draft", pass_rate=1.0)
    # RED: removing PromotionRequest `requested: Literal["shadow", "draft", "assisted", "autopilot"]` loses typed modes.


def test_llm_extension_02_each_step_needs_a_fresh_measurement_and_person(tmp_path):
    mh = configured(tmp_path)
    report = measured(mh)
    assert mh.promotion(PromotionRequest(requested="draft", report_digest=report))["mode"] == "shadow"
    draft = mh.promotion(PromotionRequest(requested="draft", report_digest=report, accepted_by="Ada"))
    assert draft["mode"] == "draft" and draft["completion_state"] == "qualified-draft"
    assert mh.promotion(PromotionRequest(requested="assisted", report_digest=report, accepted_by="Ada"))["mode"] == "draft"
    report2 = measured(mh)
    assert mh.promotion(PromotionRequest(requested="assisted", report_digest=report2, accepted_by="Ada"))["mode"] == "assisted"
    report3 = measured(mh)
    assert mh.promotion(PromotionRequest(requested="autopilot", report_digest=report3, accepted_by="Ada"))["mode"] == "assisted"
    assert mh.ledger.rows("ticket")[-1]["claims"]["accepted_by"] == "Ada"
    # RED: removing `ident not in used` from the promotion condition lets one report promote two stages.


def test_llm_extension_d1_it_can_only_move_down_on_its_own(tmp_path):
    mh = configured(tmp_path)
    mh.promotion(PromotionRequest(requested="draft", report_digest=measured(mh), accepted_by="Ada"))
    result = mh.promotion(PromotionRequest(requested="assisted", report_digest=measured(mh)))
    assert result["mode"] == "draft"
    result = mh.promotion(PromotionRequest(requested="shadow", security_claims={"boundary": False}))
    assert result["mode"] == "shadow" and result["completion_state"] != "verified"
    reopened = adapt(mh.workflow, workspace=mh.workspace, clock=lambda: 0.0)
    assert reopened.promotion()["mode"] == "shadow"
    # RED: removing `who and not is_automated(who)` from the promotion condition allows an automatic upward move.


def test_promotion_rejects_older_reports_and_changed_judging_code(tmp_path, monkeypatch):
    from g6.metaharness import api
    mh = configured(tmp_path)
    older = measured(mh)
    latest = measured(mh)
    assert mh.promotion(PromotionRequest(requested="draft", report_digest=older, accepted_by="Ada"))["mode"] == "shadow"
    monkeypatch.setattr(api, "judging_sha256", lambda: "changed")
    refused = mh.promotion(PromotionRequest(requested="draft", report_digest=latest, accepted_by="Ada"))
    assert refused["completion_state"] == "blocked-escalated"
    # RED: removing `ident == evaluations[-1].get("report_digest")` admits the older report.
