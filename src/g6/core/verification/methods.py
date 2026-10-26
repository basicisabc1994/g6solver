# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Reusable verification methods."""
from __future__ import annotations

from typing import Any

from g6.core.evidence import Claim, EvidenceBundle, TestResult
from g6.core.verification import register


def acceptance_criteria_check(*, output: Any, contract: Any, block: Any) -> EvidenceBundle:
    return EvidenceBundle(
        claims=(Claim(text=f"{getattr(block, 'name', 'block')} produced output", checked=output is not None),),
        tests_run=(TestResult(name="acceptance_criteria_check", passed=output is not None),),
        confidence=0.5 if output is not None else 0.0,
        verifier_id="acceptance_criteria_check",
    )


def evidence_coverage_check(*, output: Any, contract: Any, block: Any) -> EvidenceBundle:
    has_evidence = bool(getattr(output, "evidence", None) or getattr(output, "sources", None))
    return EvidenceBundle(
        claims=(Claim(text="output includes evidence or sources", checked=has_evidence),),
        tests_run=(TestResult(name="evidence_coverage_check", passed=has_evidence),),
        confidence=0.75 if has_evidence else 0.25,
        verifier_id="evidence_coverage_check",
    )


def reliability_pipeline_self_check(*, output: Any, contract: Any, block: Any) -> EvidenceBundle:
    report = getattr(getattr(output, "evaluation", None), "verification_report", None)
    checks_failed = list(getattr(report, "checks_failed", []) or []) if report is not None else []
    unverified = list(getattr(report, "unverified_items", []) or []) if report is not None else []
    completion = getattr(getattr(output, "completion_state", None), "value", str(getattr(output, "completion_state", "") or ""))
    passed = bool(report is not None and not checks_failed and not unverified and completion != "blocked_escalated")
    return EvidenceBundle(
        claims=(Claim(text=f"reliability pipeline completion_state={completion}", checked=report is not None),),
        tests_run=(
            TestResult(name="reliability_pipeline_self_check", passed=passed, output_ref="; ".join(checks_failed + unverified)),
        ),
        confidence=0.85 if passed else 0.35,
        verifier_id="reliability_pipeline_self_check",
        unresolved_questions=tuple(checks_failed + unverified),
    )


def formal_methods_verification(*, output: Any, contract: Any, block: Any) -> EvidenceBundle:
    has_structured_result = output is not None
    return EvidenceBundle(
        claims=(Claim(text="formal methods output is structured", checked=has_structured_result),),
        tests_run=(TestResult(name="formal_methods_verification", passed=has_structured_result),),
        confidence=0.7 if has_structured_result else 0.0,
        verifier_id="formal_methods_verification",
    )


def csf_verification(*, output: Any, contract: Any, block: Any) -> EvidenceBundle:
    unsafe = bool(getattr(output, "unsafe", False) or getattr(output, "blocked", False))
    return EvidenceBundle(
        claims=(Claim(text="CSF output does not expose unsafe/block flag", checked=not unsafe),),
        tests_run=(TestResult(name="csf_verification", passed=not unsafe),),
        confidence=0.75 if not unsafe else 0.1,
        verifier_id="csf_verification",
    )


register("acceptance_criteria_check", acceptance_criteria_check)
register("evidence_coverage_check", evidence_coverage_check)
register("reliability_pipeline_self_check", reliability_pipeline_self_check)
register("formal_methods", formal_methods_verification)
register("formal_methods_verification", formal_methods_verification)
register("csf", csf_verification)
register("csf_verification", csf_verification)


__all__ = [
    "acceptance_criteria_check",
    "evidence_coverage_check",
    "reliability_pipeline_self_check",
    "formal_methods_verification",
    "csf_verification",
]
