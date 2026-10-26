# SPDX-License-Identifier: Apache-2.0
"""Protected changes use the coding gate's evidence and verdicts."""
from datetime import UTC, datetime, timedelta

import pytest

from g6.coding_agent_gate import (CodingAgentGateBlock, CodingAgentGateInput, Evidence, PathExemption,
                                  ProtectedPathsPolicy, preflight, verify_answer)
from g6.mcp import invoke_reply

PATH = "rules/release.toml"
POLICY = ProtectedPathsPolicy(protected=("rules/**",))
FILE = Evidence("file", PATH)


def check(*, policy=POLICY, paths=(PATH,), reviews=(), author="Author", **kwargs):
    return preflight("Adjust the release notes", evidence=(FILE, *reviews), author=author,
                     touched_paths=paths, protected_paths_policy=policy, **kwargs)


def review(*, reviewer="Reviewer", observed_by="g6", ref=PATH):
    return Evidence("review", ref=ref, reviewer=reviewer, observed_by=observed_by,
                    output="Inspected the proposed change against the release rules.")


def test_governance_01_d1_claimed_review_cannot_release_protected_change():
    # Removal oracle: gate._protected requires review observed_by == 'g6'.
    result = check(reviews=(review(observed_by="caller"),))
    assert result.verdict == "needs_review" and result.completion_state == "blocked-escalated"
    assert "protected_review_missing" in {f.code for f in result.findings}
    assert not result.review_satisfied and result.missing_evidence
    assert check(reviews=(review(),)).verdict == "proceed_with_caution"
    assert check(reviews=(review(reviewer="Author"),)).verdict == "needs_review"
    assert check(reviews=(review(ref="unrelated.toml"),)).verdict == "needs_review"
    assert check(reviews=(review(),), author="").verdict == "needs_review"


def test_governance_01_mcp_approval_and_forged_provenance_refuse():
    # Removal oracle: CodingAgentGateInput and EvidenceIn forbid approval and observed_by='g6'.
    for extra in ('"approval":true', '"evidence":[{"kind":"review","ref":"rules/x","reviewer":"R","observed_by":"g6"}]'):
        reply = invoke_reply("coding_agent_gate", "preflight", '{"task":"Change rules","touched_paths":["rules/x"],'+extra+'}')
        assert not reply["ok"] and reply["completion_state"] == "blocked-escalated"


def test_governance_02_missing_policy_is_refused():
    # Removal oracle: gate._protected's policy is None refusal.
    result = check(policy=None)
    assert result.verdict == "block"
    assert "protected_policy_missing" in {f.code for f in result.findings}
    reply = invoke_reply("coding_agent_gate", "preflight", '{"task":"Edit notes","touched_paths":["notes.txt"]}')
    assert reply["result"]["verdict"] == "block"


def test_governance_03_globs_and_unknown_paths_are_classified_honestly():
    # Removal oracle: gate._protected evaluates the supplied globs, not fixed repository paths.
    custom = ProtectedPathsPolicy(protected=("assets/*/**",))
    result = check(policy=custom, paths=("assets/private/weights.dat",))
    assert result.verdict == "needs_review"
    unknown = check(paths=("rules-copy/release.toml",))
    assert unknown.detected["protected_paths"]["rules-copy/release.toml"] == "unclassified"
    assert unknown.completion_state == "qualified-draft"


def test_expired_exemption_no_longer_exempts():
    # Removal oracle: classify_paths compares e.expires_at > now.
    now = datetime.now(UTC)
    expired = ProtectedPathsPolicy(protected=("rules/**",), exemptions=(PathExemption(pattern="rules/**", expires_at=now-timedelta(days=1)),))
    future = ProtectedPathsPolicy(protected=("rules/**",), exemptions=(PathExemption(pattern="rules/**", expires_at=now+timedelta(days=1)),))
    assert check(policy=expired).verdict == "needs_review"
    assert check(policy=future).detected["protected_paths"][PATH] == "exempt"
    with pytest.raises(ValueError):
        PathExemption(pattern="rules/**", expires_at=datetime(2099, 1, 1))


@pytest.mark.parametrize("path", ["rules/../rules/release.toml", "../rules/release.toml", "C:/rules/release.toml", "//host/rules"])
def test_ambiguous_paths_fail_closed(path):
    # Removal oracle: _normal_path rejects traversal, absolute and drive paths before matching.
    assert check(paths=(path,)).verdict == "block"


def test_missing_paths_cannot_skip_a_configured_policy():
    # Removal oracle: gate._protected refuses missing or empty touched_paths.
    for paths in (None, (), iter(())):
        assert check(paths=paths).verdict == "block"
    block = CodingAgentGateBlock(protected_paths_policy=POLICY)
    assert block.infer(CodingAgentGateInput(op="preflight", task="Adjust notes")).unwrap()["verdict"] == "block"


def test_policy_evaluation_failure_blocks(monkeypatch):
    # Removal oracle: gate._protected's exception produces protected_policy_failed.
    import g6.coding_agent_gate.protected_paths as policies
    def broken(*args, **kwargs):
        raise OSError("policy unavailable")
    monkeypatch.setattr(policies, "classify_paths", broken)
    result = check()
    assert result.verdict == "block" and "protected_policy_failed" in {f.code for f in result.findings}


def test_verify_answer_keeps_same_protected_path_floor():
    # Removal oracle: verify_answer extends findings with _protected(...).
    result = verify_answer("Adjust notes", "Adjusted the notes.", task_contract="Keep rules intact", evidence=[FILE],
                           author="Author", touched_paths=[PATH], protected_paths_policy=POLICY)
    assert result.verdict == "needs_review" and result.completion_state == "blocked-escalated"


def test_policy_objects_forbid_unknown_fields():
    # Removal oracle: ProtectedPathsPolicy's ConfigDict(extra='forbid').
    with pytest.raises(ValueError):
        ProtectedPathsPolicy(protected=("rules/**",), approval=True)
