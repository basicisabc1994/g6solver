# SPDX-License-Identifier: Apache-2.0
"""Seed templates reuse the existing static detector, including its limitations.

No seed has measured precision here; seeds carry A0. Later acceptance owns L_0's
A1 decisions. Every static seed preserves the existing detector's full report;
its verdict projects an existing reason category, never runs a second detector.
Swallow/self-reference seeds inherit the broad changed-check heuristic and its
false alarms: they do not establish why a check changed. V2 probes require explicit inputs.
"""
from dataclasses import dataclass
from types import MappingProxyType

from g6.safety_engine import weakening
from .facts import check_split, source_mapping


@dataclass(frozen=True)
class SeedTemplate:
    name: str
    supported: bool
    reason_fragments: tuple[str, ...] = ()
    authority: str = "A0"


_CHANGED_CHECK = ("a check was removed, changed or can no longer fail",)
SEED_TEMPLATES = MappingProxyType({
    "weakening.v1": SeedTemplate("weakening.v1", True),
    "assert_strength.v1": SeedTemplate("assert_strength.v1", True,
        (*_CHANGED_CHECK, "test file deleted", "test removed or renamed", "fewer tests", "early return added")),
    "skip_gate.v1": SeedTemplate("skip_gate.v1", True,
        tuple(f"adds {name} (a skip or expected failure" for name in (
            "skip", "skipif", "xfail", "importorskip", "pytestmark", "skipTest", "SkipTest", "skipIf",
            "skipUnless", "expectedFailure"))),
    "swallow.v1": SeedTemplate("swallow.v1", True, _CHANGED_CHECK),
    "self_reference.v1": SeedTemplate("self_reference.v1", True, _CHANGED_CHECK),
    "mock_target.v1": SeedTemplate("mock_target.v1", True, ("a mock or patch", "module-level patch added")),
    "discriminates_at_base.v1": SeedTemplate("discriminates_at_base.v1", False),
    "kill_set.v1": SeedTemplate("kill_set.v1", False),
})
PROBE_TEMPLATES = MappingProxyType({name: SeedTemplate(name, True) for name in (
    "kill_set.v2", "discriminates_at_base.v2")})


@dataclass(frozen=True)
class Detection:
    verdict: str
    reason: str
    report: weakening.TestWeakeningReport | None
    outcome: object | None = None


def detect(before, after, *, split):
    check_split(split)
    return weakening.detect_test_weakening(source_mapping(before), source_mapping(after))


def run_template(template, before, after, *, split, bindings=None, probe_input=None, workspace=None,
                 cost_bound=None, python=None):
    check_split(split)
    if template in PROBE_TEMPLATES:
        from .probes import ProbeTemplateInstance, run_probe
        instance = ProbeTemplateInstance(template=template, bindings={} if bindings is None else bindings)
        if probe_input is None or workspace is None or cost_bound is None:
            return Detection("abstain", "Probe inputs, workspace and cost bound are required.", None)
        outcome = run_probe(instance, before, after, split=split, probe_input=probe_input,
                            workspace=workspace, cost_bound=cost_bound, python=python)
        return Detection(outcome.verdict, outcome.reason, None, outcome)
    seed = SEED_TEMPLATES[template]
    if bindings:
        raise ValueError("seed templates accept no unmeasured bindings")
    if not seed.supported:
        return Detection("abstain", "This template needs an independent implementation or a confined probe.", None)
    report = detect(before, after, split=split)
    reasons = [reason for reason in report.reasons if not seed.reason_fragments
               or any(fragment in reason for fragment in seed.reason_fragments)]
    verdict = "concern" if reasons else "no_concern" if report.checked else "abstain"
    return Detection(verdict, "\n".join(reasons or report.not_checked), report)
