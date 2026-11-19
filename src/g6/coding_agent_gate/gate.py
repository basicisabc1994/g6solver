# SPDX-License-Identifier: Apache-2.0
"""The two floors, ``preflight`` (before acting) and ``verify_answer`` (before reporting done), and their envelope.

Every rule emits a coded ``Finding``; ``SEVERITY`` maps each code to the verdict it forces, and the verdict is the
strictest of them, decided in one place (audit 019 PY-006). With no finding a caller report is ``report_consistent``;
``pass`` requires every test to have a stored receipt for the host-bound artifact. A call that checked nothing
never passes (PY-002). Explicit repository scope enables reference checks. Nothing here writes a file (PY-010)
or calls a model (CAG-008: the reviewer is the opt-in ``review``
module). A verdict maps down-only to ``qualified-draft`` or ``blocked-escalated``, never ``verified``.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from g6.coding_agent_gate.protected_paths import ProtectedPathsPolicy

from g6.coding_agent_gate.detectors import NEW_TEST_MARKER, acknowledges_unverified, overclaims, risk_hits, scan_diff, shortcut_hits
from g6.coding_agent_gate.evidence import Evidence, VerifierResult, is_placeholder
from g6.core.reliability_envelope import envelope_fields, warning_card

VERDICT_RANK: dict[str, dict[str, int]] = {
    "preflight": {"proceed_with_caution": 0, "needs_evidence": 1, "needs_review": 2, "block": 3},
    "verify_answer": {"pass": 0, "report_consistent": 1, "revise": 2, "needs_review": 3, "block": 4},
}

#: Finding code -> the verdict it forces. The only place a rule's weight is set.
SEVERITY: dict[str, str] = {
    "protected_policy_missing": "block",
    "protected_policy_failed": "block",
    "protected_review_missing": "needs_review",
    "shortcut_proposed": "block",
    "shortcut_in_answer": "block",
    "shortcut_in_diff": "block",
    "marker_on_new_test": "block",          # this gate has no other check on such a diff; patch review, which runs the tests, reads it as evidence
    "no_task_contract": "block",            # require_task_contract=True (the default)
    "no_task_contract_advisory": "revise",  # require_task_contract=False
    "review_missing": "needs_review",
    "evidence_needed": "needs_evidence",
    "empty_answer": "revise",
    "no_evidence": "revise",
    "test_failed": "revise",
    "verifier_failed": "revise",
    "claims_without_files": "revise",
    "overclaim_without_run": "revise",
    "overclaim_on_declared_run": "revise",
    "EVIDENCE_REF_MISSING": "revise",
}
_MISSING: dict[str, str] = {
    "protected_policy_missing": "a protected-paths policy supplied by the person",
    "protected_policy_failed": "valid repository-relative paths and a working protected-paths policy",
    "protected_review_missing": "host-observed review for each protected path, by someone other than the author",
    "evidence_needed": "an inspected file path, a reproduction log, or a test run with its exit code",
    "no_evidence": "a counted item: a file path, a test command with its exit code, a log, a diff or a named review",
    "review_missing": "a review naming a reviewer other than the author",
    "no_task_contract": "a task contract: what done means for this task",
    "no_task_contract_advisory": "a task contract: what done means for this task",
    "claims_without_files": "the inspected file behind each repo-specific claim",
    "overclaim_without_run": "a passing test run (exit code 0) behind the readiness claim",
    "overclaim_on_declared_run": "a G6-observed passing run, or an answer that says it is unverified",
    "empty_answer": "the answer to check",
}
_TIER_RANK = {"low": 0, "medium": 1, "high": 2}
_TIER_WORDS = {"low": "low", "medium": "medium", "high": "high", "critical": "high",
               "r0": "low", "r1": "medium", "r2": "high", "r3": "high"}
_DRAFT_VERDICTS = frozenset({"pass", "report_consistent", "proceed_with_caution", "revise", "needs_evidence"})
_CODES = {
    "pass": "G6_E_CODING_AGENT_GATE_PASS",
    "report_consistent": "G6_E_CODING_AGENT_GATE_REPORT_CONSISTENT",
    "proceed_with_caution": "G6_E_CODING_AGENT_GATE_PROCEED_WITH_CAUTION",
    "revise": "G6_E_CODING_AGENT_GATE_REVISE",
    "needs_evidence": "G6_E_CODING_AGENT_GATE_NEEDS_EVIDENCE",
    "block": "G6_E_CODING_AGENT_GATE_BLOCK",
    "needs_review": "G6_E_CODING_AGENT_GATE_NEEDS_REVIEW",
}
_NEXT = {"block": "Do not proceed: remove the shortcut and repair the behaviour with every check still active.",
         "needs_review": "Stop: get the named human or domain-expert review before acting or reporting done.",
         "revise": "Fix the findings, attach the missing evidence, and call the gate again.",
         "needs_evidence": "Collect the missing evidence before acting, then call the gate again."}


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str


def _finding(code: str, message: str) -> Finding:
    return Finding(code, SEVERITY[code], message)


def stricter_of(a: str, b: str, *, vocabulary: str) -> str:
    """The stricter of ``a`` (the trusted floor) and ``b``. ``b`` wins only as a known, strictly stricter verdict;
    an unknown ``a`` stands (and reads ``blocked-escalated``)."""
    ranks = VERDICT_RANK[vocabulary]
    a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
    return b if b in ranks and ranks[b] > ranks.get(a, len(ranks)) else a


def strictest(op: str, findings: Iterable[Finding]) -> str:
    """The verdict the findings force: the strictest severity, or the vocabulary's lowest word when there are none."""
    verdict = min(VERDICT_RANK[op], key=VERDICT_RANK[op].__getitem__)
    for f in findings:
        verdict = stricter_of(verdict, f.severity, vocabulary=op)
    return verdict


def surface_state(verdict: str) -> str:
    """Verdict -> completion state, down-only: ``qualified-draft`` or ``blocked-escalated``, never ``verified``."""
    return "qualified-draft" if str(verdict or "").strip().lower() in _DRAFT_VERDICTS else "blocked-escalated"


def tier_of(declared: str, fields: str, said: str = "") -> tuple[str, tuple[str, ...]]:
    """The tier and the risk terms behind it. A declared tier may raise the computed one, never lower it; an
    unrecognised word reads ``high``. No risk term computes ``medium``: an absent keyword is not evidence of low risk."""
    terms = tuple(risk_hits(fields, said))
    raw = str(declared or "").strip().lower()
    stated = _TIER_WORDS.get(raw, "high") if raw else "low"
    return max("high" if terms else "medium", stated, key=_TIER_RANK.__getitem__), terms


@dataclass(frozen=True)
class GateDecision:
    op: str
    verdict: str
    risk_tier: str
    risk_terms: tuple[str, ...]
    findings: tuple[Finding, ...]
    counted: tuple[Evidence, ...] = ()
    rejected: tuple[tuple[Evidence, str], ...] = ()
    verifier: VerifierResult | None = None
    verifier_counted: bool = False
    detected: dict[str, Any] = field(default_factory=dict)
    checks_run: tuple[str, ...] = ()
    reviewer: str = "off"
    references_checked: bool = False
    next_actions: tuple[dict[str, Any], ...] = ()

    @property
    def evidence_status(self) -> str:
        items = [e.evidence_status for e in self.counted]
        items += ["asserted"] * len(self.rejected)
        if self.verifier is not None:
            items.append("asserted")
        return "observed_by_g6" if items and all(s == "observed_by_g6" for s in items) else "asserted"

    @property
    def completion_state(self) -> str:
        return surface_state(self.verdict)

    @property
    def review_satisfied(self) -> bool:
        return (not any(f.code.startswith("protected_") for f in self.findings)
                and any(e.kind == "review" for e in self.counted))

    @property
    def requires_human_review(self) -> bool:
        return self.risk_tier == "high" or self.verdict in ("needs_review", "block")

    @property
    def evidence_basis(self) -> str:
        sources = {"g6" if e.evidence_status == "observed_by_g6" else "caller" for e in self.counted}
        if self.verifier is not None and self.verifier_counted:
            sources.add("caller")
        if not sources:
            return "none"
        return {frozenset({"g6"}): "g6-observed", frozenset({"caller"}): "caller-declared"}.get(frozenset(sources), "mixed")

    @property
    def missing_evidence(self) -> list[str]:
        return sorted({_MISSING[f.code] for f in self.findings if f.code in _MISSING})

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op, "verdict": self.verdict, "completion_state": self.completion_state,
            "risk_tier": self.risk_tier, "risk_terms": list(self.risk_terms),
            "requires_human_review": self.requires_human_review, "review_satisfied": self.review_satisfied,
            "findings": [{"code": f.code, "severity": f.severity, "message": f.message} for f in self.findings],
            "missing_evidence": self.missing_evidence, "evidence_basis": self.evidence_basis,
            "evidence_status": self.evidence_status, "references_checked": self.references_checked,
            "reference_check": "checked within supplied scope" if self.references_checked else "not checked: no repository root or path list supplied",
            "next_actions": list(self.next_actions),
            "evidence_counted": [e.to_dict() for e in self.counted],
            "evidence_rejected": [{**e.to_dict(), "reason": why} for e, why in self.rejected],
            "verifier": None if self.verifier is None else {**self.verifier.to_dict(), "counted": self.verifier_counted},
            "detected": dict(self.detected), "checks_run": list(self.checks_run), "reviewer": self.reviewer,
            "shortcut_scan": "tripwire+diff" if "shortcut_scan(diff)" in self.checks_run else "tripwire",
        }

    def _message(self) -> str:
        if not self.findings:
            scan = "a text tripwire plus a diff scan" if "shortcut_scan(diff)" in self.checks_run else "a text tripwire only"
            return (f"No rule of the coding-agent gate objected ({len(self.checks_run)} checks; evidence "
                    f"{self.evidence_basis}; shortcut scan: {scan}). Verdict: {self.verdict}. "
                    "This is a qualified draft; test reports are attributed per evidence item.")
        rank = VERDICT_RANK[self.op]
        lead = max(self.findings, key=lambda f: rank.get(f.severity, len(rank)))
        return f"Coding-agent gate returned {self.verdict!r}: {lead.message} ({len(self.findings)} finding(s))."

    def to_envelope(self, *, request_id: str = "", run_id: str = "") -> dict[str, Any]:
        """The decision as the library envelope (``g6.core.reliability_envelope.envelope_fields``)."""
        code = _CODES.get(self.verdict, "G6_E_CODING_AGENT_GATE_UNKNOWN_VERDICT")
        message = self._message()
        card = warning_card(code, message, _NEXT.get(self.verdict, "Report this as a qualified draft with its "
                                                    "evidence; do not call it verified."))
        evidence = [{"source": f"coding_agent_gate.{self.op}", "summary": message, "verdict": self.verdict,
                     "checks_run": list(self.checks_run), "finding_codes": [f.code for f in self.findings],
                     "evidence_basis": self.evidence_basis, "evidence_status": self.evidence_status}]
        evidence += [{"source": e.to_dict()["observed_by"], "summary": f"{e.kind}: {e.ref}"[:200],
                      **e.to_dict()} for e in self.counted]
        return envelope_fields(reliability_label=self.completion_state, warning_card=card, evidence=evidence,  # type: ignore[arg-type]
                               request_id=request_id or uuid.uuid4().hex[:12], run_id=run_id,
                               degradation_reason=message)


def _lines(*parts: object) -> str:
    return "\n".join(str(p) for p in parts if p).lower()


def _sort(evidence: Iterable[Evidence], author: str) -> tuple[tuple[Evidence, ...], tuple[tuple[Evidence, str], ...]]:
    counted: list[Evidence] = []
    rejected: list[tuple[Evidence, str]] = []
    for item in evidence:
        if not isinstance(item, Evidence):
            raise TypeError(f"evidence items must be Evidence, not {type(item).__name__}")
        why = item.problem(author=author)
        if why:
            rejected.append((item, why))
        else:
            counted.append(item)
    return tuple(counted), tuple(rejected)


def _diffs(evidence: Iterable[Evidence]) -> str:
    return "\n".join(e.output for e in evidence if e.kind == "diff" and e.output)


def _protected(paths, policy, evidence: tuple[Evidence, ...], author: str) -> tuple[list[Finding], dict]:
    if paths is None and policy is None:
        return [], {}  # existing text-only calls are unchanged; hosts opt in with policy or a path list
    if policy is None:
        return [_finding("protected_policy_missing", "G6_E_PROTECTED_POLICY_MISSING: no path policy supplied.")], {}
    from g6.coding_agent_gate.protected_paths import _normal_path, classify_paths

    try:
        classified = classify_paths(paths, policy)
        missing = []
        for path, status in classified.items():
            if status != "protected":
                continue
            reviewed = False
            for item in evidence:
                if (item.kind != "review" or item.observed_by != "g6" or is_placeholder(author)
                        or item.problem(author=author) or is_placeholder(item.output)):
                    continue
                try:
                    same_path = _normal_path(item.ref) == path
                except ValueError:
                    same_path = False
                if same_path:
                    reviewed = True
                    break
            if not reviewed:
                missing.append(path)
        findings = ([_finding("protected_review_missing", "G6_E_PROTECTED_REVIEW_REQUIRED: "
                              "protected changes need observed independent review for: " + ", ".join(missing))]
                    if missing else [])
        return findings, classified
    except Exception:
        return [_finding("protected_policy_failed", "G6_E_PROTECTED_POLICY_FAILED: paths or policy could not be checked.")], {}


def preflight(task: str, *, proposed_action: str = "", evidence: Iterable[Evidence] = (), risk_tier: str = "",
              constraints: Iterable[str] = (), author: str = "", touched_paths: Iterable[str] | None = None,
              protected_paths_policy: ProtectedPathsPolicy | None = None) -> GateDecision:
    """Check a coding task before the agent acts.

    ``proposed_action`` is scanned for shortcuts and read for risk, but it is a plan, never evidence (CAG-002): only
    typed ``Evidence`` counts. The task is the user's request and is not scanned for shortcuts, so "remove the
    skipped test markers" is not blocked.
    """
    evidence = tuple(evidence)
    counted, rejected = _sort(evidence, author)
    diff = _diffs(evidence)
    tier, terms = tier_of(risk_tier, _lines(task, *constraints, proposed_action, diff))
    text_hits, diff_hits = shortcut_hits(proposed_action), scan_diff(diff)
    findings, path_status = _protected(touched_paths, protected_paths_policy, evidence, author)
    if text_hits or diff_hits:
        findings.append(_finding("shortcut_proposed", "The proposed action removes a check or control: "
                                 + ", ".join(text_hits + diff_hits) + "."))
    if tier == "high" and not any(e.kind == "review" for e in counted):
        findings.append(_finding("review_missing", f"High-risk work ({', '.join(terms) or 'declared high'}) needs a "
                                 "named human or domain-expert review before acting."))
    if not counted:
        findings.append(_finding("evidence_needed", "No evidence counted yet; a proposed action is a plan, not evidence."))
    checks = ("evidence_structure", "risk_tier", "review_required", "shortcut_tripwire(proposed_action)")
    return GateDecision("preflight", strictest("preflight", findings), tier, terms, tuple(findings), counted, rejected,
                        detected={"shortcuts_text": text_hits, "shortcuts_diff": diff_hits, "protected_paths": path_status},
                        checks_run=checks + (("protected_paths",) if touched_paths is not None or protected_paths_policy is not None else ())
                        + (("shortcut_scan(diff)",) if diff else ()))


def verify_answer(task: str, answer: str, *, evidence: Iterable[Evidence] = (),
                  verifier: VerifierResult | str | None = None, claims: Iterable[str] = (), task_contract: str = "",
                  risk_tier: str = "", author: str = "", require_task_contract: bool = True,
                  touched_paths: Iterable[str] | None = None,
                  protected_paths_policy: ProtectedPathsPolicy | None = None, scanned_diff: str = "",
                  repo_root: str | None = None) -> GateDecision:
    """Check a drafted answer before it is reported as done.

    ``report_consistent`` means a consistent assertion; ``pass`` needs observed tests and no finding.
    Readiness words ("verified", "all tests pass") need a
    passing run; on caller-declared evidence alone they also need the answer to say it is unverified. A negative
    signal (a failing exit code, a verifier ``fail``) is believed as given; a positive one needs structure.

    ``scanned_diff``: a unified diff the host read itself (``g6_review_patch`` passes the working tree's diff against
    its base). It is scanned for shortcuts and read for risk terms as a diff item's text is, and never counted as
    evidence: it shows what changed, not that it works, so it leaves ``evidence_basis`` as the other items make it.
    """
    evidence = tuple(evidence)
    claims = [str(c) for c in claims if str(c).strip()]
    counted, rejected = _sort(evidence, author)
    v = VerifierResult.parse(verifier)
    status = v.effective_status
    v_counted = status in ("pass", "fail") and (v.observed_by == "g6" or v.exit_code is not None)
    said, diff = _lines(answer, *claims), "\n".join(part for part in (_diffs(evidence), scanned_diff) if part)
    tier, terms = tier_of(risk_tier, _lines(task, task_contract, diff), said)
    text_hits, diff_hits, claimed = shortcut_hits(said), scan_diff(diff), overclaims(said)
    passing = [e for e in counted if e.kind == "test" and e.exit_code == 0]
    failing = [e for e in counted if e.kind == "test" and e.exit_code != 0]
    any_pass = bool(passing) or (v_counted and status == "pass")
    observed_pass = bool(passing) and all(e.evidence_status == "observed_by_g6" for e in passing)
    f, path_status = _protected(touched_paths, protected_paths_policy, evidence, author)
    from g6.coding_agent_gate.references import check_references, review_call

    missing_refs = check_references(evidence, repo_root=repo_root, paths=touched_paths)
    for ref in missing_refs:
        f.append(_finding("EVIDENCE_REF_MISSING", f"EVIDENCE_REF_MISSING: reference does not resolve within the supplied scope: {ref}"))
    if not str(answer or "").strip():
        f.append(_finding("empty_answer", "No answer text was supplied, so there is nothing to check."))
    if text_hits:
        f.append(_finding("shortcut_in_answer", "The answer describes removing a check or control (text tripwire): "
                          + ", ".join(text_hits) + "."))
    if diff_hits == [NEW_TEST_MARKER]:
        f.append(_finding("marker_on_new_test", "The diff adds a skip or xfail marker to a test that looks new. A diff cannot show that "
                          "the test is new, so read it: it may override or replace one that already existed."))
    elif diff_hits:
        f.append(_finding("shortcut_in_diff", "The diff removes or neutralises a check: " + ", ".join(diff_hits) + "."))
    if is_placeholder(task_contract):
        f.append(_finding("no_task_contract" if require_task_contract else "no_task_contract_advisory",
                          "No task contract was stated, so fitness for purpose cannot be judged."))
    if failing:
        f.append(_finding("test_failed", f"{len(failing)} test run(s) exited non-zero: "
                          + ", ".join(f"{e.ref} (exit {e.exit_code})" for e in failing[:3]) + "."))
    if status == "fail":
        f.append(_finding("verifier_failed", "The verifier reported failure"
                          + (f" (exit {v.exit_code})." if v.exit_code is not None else ".")))
    if not counted and not v_counted:
        f.append(_finding("no_evidence", "No evidence counted" + (f"; {len(rejected)} item(s) were placeholders or "
                                                                  "malformed." if rejected else ".")))
    if claims and not any(e.kind == "file" for e in counted):
        f.append(_finding("claims_without_files", f"{len(claims)} repo-specific claim(s) without an inspected file."))
    if claimed and not any_pass:
        f.append(_finding("overclaim_without_run", f"The answer claims {claimed[0]!r} without a passing test run."))
    elif claimed and not observed_pass and not acknowledges_unverified(said):
        f.append(_finding("overclaim_on_declared_run", f"The answer claims {claimed[0]!r}, but G6 did not observe the "
                          "run; say the result is unverified, or let G6 run the check."))
    if tier == "high" and not any(e.kind == "review" for e in counted):
        f.append(_finding("review_missing", f"High-risk work ({', '.join(terms) or 'declared high'}) has no named "
                          "review by someone other than the author."))
    checks = ("answer_present", "task_contract", "evidence_structure", "test_exit_codes", "verifier_status",
              "claims_have_files", "readiness_claims", "risk_tier", "review_required", "shortcut_tripwire(text)")
    verdict = strictest("verify_answer", f)
    tests = [e for e in evidence if e.kind == "test"]
    if verdict == "pass" and not (tests and all(e.evidence_status == "observed_by_g6" for e in tests)):
        verdict = "report_consistent"
    return GateDecision("verify_answer", verdict, tier, terms, tuple(f), counted, rejected,
                        verifier=None if verifier is None else v, verifier_counted=v_counted,
                        detected={"shortcuts_text": text_hits, "shortcuts_diff": diff_hits, "readiness_claims": claimed,
                                  "acknowledges_unverified": acknowledges_unverified(said), "protected_paths": path_status},
                        checks_run=checks + (("protected_paths",) if touched_paths is not None or protected_paths_policy is not None else ())
                        + (("shortcut_scan(diff)",) if diff else ()),
                        references_checked=repo_root is not None or touched_paths is not None,
                        next_actions=(review_call(task, task_contract, evidence, repo_root),))


__all__ = ["SEVERITY", "VERDICT_RANK", "Finding", "GateDecision", "preflight", "stricter_of",
           "strictest", "surface_state", "tier_of", "verify_answer"]
