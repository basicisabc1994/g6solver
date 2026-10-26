# SPDX-License-Identifier: Apache-2.0
"""g6.safety_engine — trusted runtime ceilings for one run: the policy, the counters, the pre-action check.

Capability label: ``implemented``.

Public pieces: ``RuntimePolicy`` / ``ActionRequest`` / ``AuthorizationDecision`` (``schema``), ``RunState`` (``state``),
``exceeds_ceiling`` (``ceilings``) and ``PolicyEngine.authorize_action`` (``policy_engine``), which checks the kill
switch, the allow/deny lists and every ceiling before an action runs. The workforce's single call door uses it for
each call. ``detect_test_weakening`` (``weakening``) compares the test files, every ``conftest.py`` and the pytest and
CI configuration before and after a change by reading them: it runs nothing, and a clean report names what it compared
and what it could not see.

    from g6.safety_engine import detect_test_weakening
    report = detect_test_weakening({"tests/test_a.py": before_text},
                                   {"tests/test_a.py": after_text, "tests/conftest.py": conftest_text})
    report.weakened, report.reasons, report.completion_state   # never "verified"

Limits: the decision is not a token; ceilings bound only what passes through a door that asks and obeys. They are not
an OS sandbox and do not observe an action's real effects. The quorum counts declared validator votes and proves
nothing about their independence. Only the modules the workforce needs are ported (audit 007 §11), plus the hardened
test-weakening check; the permit ledger and engine loop are not, and the pattern guard, the prompt-extraction check and
the static source checks moved to ``g6.guards`` as advisory scanners. Applied from the audit: SE-001/002/004 (no
permit), SE-005 (test weakening: conftest.py in any directory, new files, skip markers, pytest and CI configuration),
SE-008 (normalised forbidden paths), SE-009 (quorum claims relabelled), SE-011 (guards moved out, no fixed risk
score). Not applied: SE-006 (the door leaves the shell, files, lines and privilege axes inert, and nothing records a
retry).
"""
from g6.safety_engine.weakening import TestWeakeningReport, detect_test_weakening, read_snapshot

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only (the docstring's capability label)
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("runaway_run", "test_weakening")
__g6_introduces__: tuple[str, ...] = ()

__all__ = ["TestWeakeningReport", "detect_test_weakening", "read_snapshot"]
