# SPDX-License-Identifier: Apache-2.0
"""The readable report of a patch review (``assessment.report``): Markdown a coding agent shows the person verbatim.

``render`` is a pure function of an assessment's other fields: it decides nothing, and no decided field depends on it.
At most ``MAX_LINES`` lines, in this order:

1. one headline: the outcome in plain words, its code, "qualified draft", and "merging needs your separate approval"
   (no score, certificate or count of checks);
2. the reproduced findings, integrity first: what weakened the tests or their configuration, then each failed check
   with its requirement, the repository's plain command that reproduces it (G6's exact, isolating one is named by its
   field), and whether a base run shows it passes there; then the unconfirmed findings of proposed or compiled checks;
3. what is not established, each with its reason and what would settle it; then what was demonstrated on the stated
   checks, with "fails at the base commit, passes on the patch" where the base run shows a requirement's checks
   exercise the change (with the findings and what is not established, the review contract and its evidence); a
   check that could not run at the base commit is never said to fail there;
4. what the person decides: at most 3 questions, the unknowns G6 counts neither for nor against the patch, and
   merging;
5. how this was decided, one line per step: proposed, tested (with the proposed checks and template instances, each
   with its result; none of them passes a requirement), gate, approve;
6. the standing note: the specification may be incomplete; then what was not checked (``assessment.not_checked``).

Where the assessment carries the evidence issuer's statements (``assessment.requirement_evidence``), each requirement's
line in parts 2 and 3 also says the evidence kind of its checks and how the issuer, the one rule that decides a
requirement's status, reads it: checked, refuted, conflicting, or not counted yet and why. Model results and proposed
checks are never among them: they stay in the Tested step, where none passes a requirement.

Where the review enforced cases the person confirmed (``assessment.confirmed_expectations``, G6 0.3 slice 3), a part
of its own follows what was demonstrated: each case, the option the person chose and its outcome, the scope, who
answered (recorded as given, not authenticated), its check's result on the patch and the issuer's statement, at most
``CONFIRMED_SHOWN`` of them and a header (so at most ``CONFIRMED_SHOWN`` + 2 lines beyond ``MAX_LINES``). No model
result is among them. A case that fails is a reproduced finding too. Without that field the report is as it was.

A list cut at its room ends with "and N more" and names the field that holds the rest. Text the repository or the
caller wrote (node ids, paths, requirement text, messages, commands) is shown only inside code spans fenced against
the backticks it holds, on one line, so it can neither break the Markdown nor read as G6's own words."""

from __future__ import annotations

import re

from g6.harness.report_input import Assessment, Loop
from g6.harness.report_text import G6Text, InertCode, caller_values, join_report as _join_report, text as _text

from g6.core.git_name import shown


class _RenderedReport(G6Text):
    """A completed report, distinct from text a caller supplies to completion_report."""


MAX_LINES = 41
#: The third part the reports of V9's arm 3 must keep (the registered protocol's amendment A5).
STANDING_NOTE = G6Text(
    "The task's specification may be incomplete: G6 checked the requirements as they were stated and "
    "cannot rule out something they leave out."
)
OUTCOMES = {
    "defect_detected": "A defect was found",
    "supported_within_scope": "Supported within its stated scope",
    "insufficient_evidence": "Not enough evidence",
    "test_integrity_compromised": "Test integrity is in doubt",
}
RULES = {
    "test_integrity_compromised": "a sign that the tests, their configuration or a run cannot be trusted comes "
    "before every other finding",
    "defect_detected": "a stated check, or a test of the changed code that passes at the base commit, failed; or "
    "the patch adds an import a stated contract forbids",
    "insufficient_evidence": "something needed to support the patch is missing",
    "supported_within_scope": "every requirement passed a stated check the patch did not write, and no "
    "other problem was found",
}
INTEGRITY = {
    "test_weakened": "a test or a test helper was weakened",
    "runner_configuration_changed": "the test runner's configuration changed",
    "ignored_file_can_change_tests": "a file git ignores could change the tests",
    "changed_file_can_change_tests": "a file the patch adds or changes could change what the tests load",
    "run_not_trusted": "a test run's result cannot be believed",
    "repository_changed_during_review": "the working tree changed during the review",
    "gate_shortcut": "the diff of the tests takes a shortcut",
}
FAILED = {
    "check_failed": "A stated check failed",
    "derived_test_regressed": "A test of the changed code fails",
    "forbidden_dependency_added": "The patch adds an import a stated contract forbids",
    "confirmed_expectation_failed": "A case the person confirmed fails",
}
#: G6 0.3, slice 3: what the person confirmed, shown in its own part when the review enforced any, at most this many.
CONFIRMED_SHOWN = 3
CONFIRMED_RESULT = {
    "passed": "its check passes on the patch",
    "failed": "its check fails on the patch",
    "not_run": "its check was not run on the patch",
}
UNCONFIRMED = {
    "counterexample": "A proposed check found a counterexample, not confirmed",
    "unconfirmed_failure": "A proposed check fails on the patch, not confirmed",
}
BUDGET = {"findings": 10, "missing": 5, "shown": 3, "decide": 5}
#: The review's runs and skips by plain names (no internal tier names in the report).
RUNS = {
    "stated checks and extra tests": "the stated checks",
    "tests that touch the changed modules": "tests that touch the changed code",
    "stated checks at the base commit": "the stated checks at the base commit",
    "mutation and old-against-new probes": "deeper checks",
}
#: Where the exact command G6 ran is, beside the repository's plain one.
EXACT = G6Text(
    "G6 ran it with its own isolating options; the exact command is in "
    "`assessment.completion.defects_reproduced` (`reproduction.command`)"
)
#: A proposed check's or template instance's result, in the Tested step (the owner's pilot, 2026-10-30).
#: A structural instance's results (forbidden_dependency.v1) are words too; one against a stated contract can decide
#: the outcome, so the step says only that none of these passes a requirement (the fresh review of these fixes).
PROPOSED = {
    "not_refuted": "not refuted",
    "counterexample": "found a counterexample",
    "unconfirmed_failure": "fails on the patch",
    "fails_on_base_too": "fails at the base commit too",
    "behaviour_changed": "behaves differently from the base commit (a question, not a finding)",
    "check_error": "could not be judged",
    "refused": "refused",
    "failed": "failed",
    "passed": "passed",
    "vacuous": "vacuous",
    "not_established": "not established",
}
#: Reviewer B's B6: the caution a proposed or compiled check's reproduction carries, said on its line too.
CAUTION = G6Text("Read it before you run it, or run it in a container.")
#: What the person does about git-ignored files the tests could load (worked examples, W3b), under that finding.
IGNORED_TO_DO = G6Text(
    "  - What to do: these files are untracked and git-ignored, yet the tests could load them (one named "
    "like a module in the repository could replace it), so G6 cannot rule that out. If they belong to the "
    "project, commit them before the change under review; otherwise remove them or move them out of the "
    "repository; then review again. Each is named once in `assessment.next_actions`."
)
TO_DO = frozenset({"ignored_file_can_change_tests"})
#: A file of a reviewer's test that ran, and nothing failed, but a case that failed before has not passed by its own id.
NOT_PASSED = "ran, but a case that failed has not passed"
#: A file of a reviewer's test with a case the operating system refused: unknown, so the file neither passes nor fails.
NOT_JUDGED = "ran, but a case could not be judged"
#: How a run of the review-until-clean loop ended, in the reply and at the head of the report, and what each label says.
ENDINGS = {
    "clean": "a review of this patch reproduced nothing, and no reviewer's test failed on it",
    "fixed": "each reviewer's test that failed now passes, and a fresh review of this patch reproduced nothing",
    "contested": "a reviewer's test fails, and the agent left the code as it was",
    "open": "a reviewer's test that failed has not been shown to pass",
    "untested": "nothing here tested this patch to a result",
}
#: What the review-until-clean loop's report says of itself, every time (the worked examples' arm K).
LOOP_LIMITS = (
    "- This loop has been measured on seven training bugs only.",
    "- A review that reproduces nothing is not evidence the work is right, only that this reviewer found no failure "
    "it could show.",
    "- An open finding is a reviewer's test that still fails; on the training bugs that did not always mean a wrong "
    "patch.",
    "- It differs from what was measured: the reviewer here reads a text pack and has no tools, and each step "
    "replays the rounds before it.",
    "- A model wrote these tests, so none of them passes a requirement.",
)


#: The one table from the evidence issuer's codes (``assessment.requirement_evidence.requirements[].codes``, which
#: ``g6.harness.requirement_evidence.codes`` reads from the issuer's structured fields) to G6's words for why it does
#: not count a requirement yet. The first row whose code the requirement has is said: a failure or a result that is
#: none comes before why a pass did not count.
NOT_COUNTED = (
    (
        "advisory_check_failed",
        "a run of its check failed, and nothing showed the check passes on code "
        "known to be right, so the failure is a caveat, not a refutation",
    ),
    ("check_result_unknown", "a run of its check gave no result"),
    ("self_authored", "the patch's author wrote the check"),
    ("frozen_after_artifact", "the check is new with the patch"),
    ("evidence_kind", "no check of a kind that can count ran"),
    ("formal_without_certificate", "a formal check names no digest of what it decided"),
    ("no_negative_control", "no run showed the check can fail"),
    ("control_not_generated_by_g6", "its negative control is not one G6 made"),
    ("control_not_killed", "its negative control was not caught by the check"),
    ("control_for_other_code", "its negative control was run against other code of the check"),
    ("control_before_freeze", "its negative control came before the check was fixed"),
    ("not_in_ledger", "a record is not in G6's ledger"),
)


_ISSUER_FORM = G6Text("; {}")


def _issuer(assessment: dict, requirement: object, form: G6Text = _ISSUER_FORM) -> str:
    """The evidence issuer's reading of ``requirement``, with its checks' evidence kinds, as a clause in ``form`` (by
    default one that ends a line); "" when the assessment carries none."""
    evidence = assessment.get("requirement_evidence") or {}
    if evidence.get("refused_by_issuer"):  # never silence: the issuer counted nothing, and says why in the field
        return form.format(
            _text(
                "the evidence issuer refused the review's inputs, so it counts nothing ({})",
                code("assessment.requirement_evidence"),
            )
        )
    found = next((s for s in evidence.get("requirements") or [] if s.get("requirement") == requirement), None)
    if found is None:
        return G6Text("")
    kinds = sorted({str(kind) for kind in (found.get("kinds") or {}).values() if kind})
    shown = G6Text(", ").join(code(kind, 40) for kind in kinds) or "none it can count"
    if found.get("checked"):
        said = "checked"
    elif found.get("support") and found.get("refute"):
        said = "conflicting evidence, one check for it and one against"
    elif found.get("refute"):
        said = G6Text("refuted by ") + G6Text(", ").join(code(c, 40) for c in found.get("refuted_by") or [])
    elif found.get("not_counted"):  # G6's own words for what the review ran, from requirement_evidence
        said = G6Text("not counted: ") + str(found["not_counted"])
    elif not kinds and (found.get("kinds") or {}):  # each check was refused before the issuer saw it
        said = _text(
            "not counted: the evidence mapping refused each of its checks for a part it lacks ({})",
            code("assessment.requirement_evidence.refused"),
        )
    else:  # the issuer's own codes, never the text of its gap or of a check's name
        given = found.get("codes")
        held = frozenset(str(one) for one in given) if isinstance(given, list) else frozenset()
        said = G6Text("not counted yet: ") + next(
            (why for key, why in NOT_COUNTED if key in held), "no check G6 can count ran"
        )
    return form.format(_text("evidence {}, and the evidence issuer: {}", shown, said))


def _plain(node: object) -> str:
    """The repository's plain command for ``node``: its own pytest, with none of G6's isolating options."""
    import shlex

    return code(f"python -m pytest {shlex.quote(str(node))}", 200)


def _words(text: str) -> str:
    """G6's own wording of a run or a skip, with its internal tier names put plainly."""
    for old, new in (
        ("tier 1 found", "the stated checks found"),
        ("tier 2 found", "the tests of the changed code found"),
        ("tier 1", "the stated checks"),
        ("tier 2", "the tests of the changed code"),
        ("tier 3 not run: ", ""),
        ("tier 3", "deeper checks"),
    ):
        text = text.replace(old, new)
    return text


def _at_base(assessment: dict, runs: list[dict] | None = None) -> dict[str, str]:
    """Each stated check's own observation at the base commit, from the review's run there (empty when none ran), read
    by the review's one rule (``patch_review.at_base_observations``; review 4's finding 1): a set-up or teardown error
    there is no failure of the check, so it never shows the check exercises the change. ``runs`` narrows the scope's
    runs; the assessment itself is always passed whole, so its attribution is never dropped."""
    from g6.harness.patch_review import at_base_observations  # the one rule; patch_review imports this module lazily

    runs = ((assessment.get("scope") or {}).get("checks_run") or []) if runs is None else runs
    return at_base_observations([run for run in runs if _observed(assessment, run)])


def _observed(assessment: dict, item: dict | None = None) -> bool:
    """Only schema positions establish report attribution, never a nested data key.

    A leaf can state its own provenance in a mixed report. Missing/unknown statuses
    are assertions; a verified label alone is not a statement that G6 ran a test.
    """
    inherited = (assessment.get("scope") or {}).get("evidence_status", assessment.get("evidence_status"))
    if item is not None:
        return item.get("evidence_status", inherited) == "observed_by_g6"
    completion = assessment.get("completion") or {}
    leaves = [
        *((assessment.get("scope") or {}).get("checks_run") or []),
        *(completion.get("demonstrated") or []),
        *(completion.get("activities") or []),
        *(one.get("check") or {} for one in (assessment.get("confirmed_expectations") or {}).get("expectations") or []),
        *(assessment.get("reviews") or []),
        *(assessment.get("sets") or []),
    ]
    return inherited == "observed_by_g6" and all(
        one.get("evidence_status", inherited) == "observed_by_g6" for one in leaves
    )


#: Text Markdown and HTML read nothing in: letters, digits, spaces, plain punctuation, an underscore only inside a word.
_INERT = re.compile(r"(?:[A-Za-z0-9 ,.;:'()/?%-]|(?<=[A-Za-z0-9])_(?=[A-Za-z0-9]))+\Z")


def _inert(value: object) -> str:
    """A value from the report's input, in prose: an exact int or a plain G6 word as it is (the report's historical
    text), anything else as one inert code span."""
    if isinstance(value, InertCode):
        return str(value)
    if type(value) is int:
        return str(value)
    if isinstance(value, str) and value == value.strip() and _INERT.match(value):
        return value
    return code(value)


def _assertion(item: dict) -> str:
    verdict = item.get("verdict", item.get("result"))
    if verdict in ("failed", "fail", "crashed", "timed_out", "run_inconsistent"):
        return _text("the agent reports {}; G6 did not run it", code(verdict))
    passed = item.get("passed")
    if passed is not None or verdict in ("pass", "passed"):  # a count is an exact int; anything else is no count
        count = str(passed) if type(passed) is int else "an unspecified number"
        return _text("the agent reports {} passed; G6 did not run it", count)
    return _text("the agent reports {}; G6 did not run it", code(item.get("verdict", item.get("result", "a result"))))


def code(value: object, limit: int | None = 140, *, literal: bool = False, confirmed: bool = False) -> str:
    """``value`` as one inert code span: on one line, cut at ``limit`` characters, fenced by more backticks than any
    run inside it (padded where it begins or ends with one), so nothing in it is read as Markdown or HTML."""
    from g6.harness.behaviour_question import caller_text

    if literal or confirmed:
        text = caller_text(value, literal=literal) or "(empty)"
    else:
        text = " ".join(
            (shown(value) if isinstance(value, str) else str(value)).split()
        )  # a name git gave, by its rule
        text = "".join(ch if ch.isprintable() else "?" for ch in text) or "(empty)"
    if limit is not None and len(text) > limit:
        text = text[: limit - 3] + "..."
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text[0] == "`" or text[-1] == "`" else ""
    return InertCode(f"{fence}{pad}{text}{pad}{fence}")


def _cut(items: list[list[str]], room: int, field: str) -> list[str]:
    """Each item's lines while they fit in ``room`` lines, and "and N more" naming ``field`` for the rest."""
    lines: list[str] = []
    for index, item in enumerate(items):
        left = len(items) - index
        if len(lines) + len(item) > room - (1 if left > 1 else 0):
            lines.append(_text("- and {} more ({})", left, code(field)))
            break
        lines += item
    return lines


def _headline(assessment: dict) -> str:
    outcome = assessment.get("outcome") or "unknown"
    if outcome == "test_integrity_compromised":
        first = next((r["code"] for r in assessment.get("reasons", []) if r.get("stage") == "integrity"), G6Text(""))
        plain = (
            "Tests or their configuration were weakened"
            if first in ("test_weakened", "runner_configuration_changed", "gate_shortcut")
            else "Test integrity is in doubt"
        )
    else:
        plain = OUTCOMES.get(outcome, "The review gave no outcome")
    if outcome == "supported_within_scope" and not _observed(assessment):
        plain = "Agent-reported support; G6 did not run it"
    return _text("**{}** ({}). Qualified draft: merging needs your separate approval.", plain, outcome)


def _findings(assessment: dict) -> list[list[str]]:
    from functools import partial

    completion = assessment.get("completion") or {}
    defects = completion.get("defects_reproduced") or {}
    items = [
        [
            _text(
                "- **Test integrity: {}** ({}): {}",
                INTEGRITY.get(r["code"], "the tests cannot be trusted"),
                _inert(r["code"]),
                code(_words(str(r.get("detail", "")))),
            ),
            *([IGNORED_TO_DO] if r["code"] in TO_DO else []),
        ]
        for r in assessment.get("reasons", [])
        if r.get("stage") == "integrity"
    ]
    for found in defects.get("stated_and_repository_checks", []):
        show = partial(code, confirmed=found.get("code") == "confirmed_expectation_failed")
        requirements = (
            G6Text(", ").join(show(r) + _issuer(assessment, r, G6Text(" ({})")) for r in found.get("requirements", []))
            or "no stated requirement"
        )
        reproduction = found.get("reproduction") or {}
        head = _text(
            "- **{}** ({}) for {}: {}",
            FAILED.get(found.get("code"), "A check failed"),
            _inert(found.get("code")),
            requirements,
            show(found.get("check", "")),
        )
        if not _observed(assessment, found):
            items.append(
                [
                    head,
                    _text(
                        "  - {}; stated reproduction: {}.",
                        _assertion(found),
                        _plain(reproduction.get("node", found.get("check", ""))),
                    ),
                ]
            )
            continue
        if reproduction.get("kind") == "static":
            imports = reproduction.get("imports") or [{}]
            first = imports[0]
            how = _text(
                "  - Read from the files, no code runs: {} line {} holds {}; the base commit's files have no such import",
                code(first.get("file", "")),
                _inert(first.get("line", "?")),
                code(first.get("text", "")),
            ) + (
                _text("; the repository's own linter: {}", code(reproduction["command"]))
                if reproduction.get("command")
                else G6Text("")
            )
        elif found.get("code") == "confirmed_expectation_failed":
            how = _text(
                "  - Reproduce: write its text (in {}) to {}, then {}. G6 compiled it from example.v1 and the person's answer. {}. {}",
                show("assessment.completion.defects_reproduced"),
                show(reproduction.get("file", ""), 80),
                _plain(reproduction.get("node", "")),
                EXACT,
                CAUTION,
            )
        else:
            base = (
                "passes at the base commit, fails on the patch"
                if found.get("code") == "derived_test_regressed"
                else "not run at the base commit"
            )
            how = _text(
                "  - Reproduce: {}; {}. {}.", _plain(reproduction.get("node", found.get("check", ""))), base, EXACT
            )
        items.append([head, how])
    for found in defects.get("proposed_checks", []):
        reproduction = found.get("reproduction") or {}
        head = _text(
            "- **{}** ({}) for {}: {}",
            UNCONFIRMED.get(found.get("result"), "A proposed check found a difference, not confirmed"),
            _inert(found.get("code")),
            code(found.get("requirement", "")),
            code(found.get("check", "")),
        )
        if not _observed(assessment, found):
            items.append(
                [
                    head,
                    _text("  - {}; stated reproduction: {}.", _assertion(found), _plain(reproduction.get("node", ""))),
                ]
            )
            continue
        if found.get("result") == "counterexample":
            base = "passes at the base commit, fails on the patch"
        elif str(reproduction.get("at_base", "not run")).startswith("not run"):
            base = "not run at the base commit"
        else:
            base = _text("at the base commit: {}", code(reproduction.get("at_base", ""), 80))
        how = _text(
            "  - Reproduce: write its text (in {}) to {}, then {}; {}. {}. The check is code someone other than G6 wrote: {}",
            code("assessment.completion.defects_reproduced.proposed_checks"),
            code(reproduction.get("file", ""), 80),
            _plain(reproduction.get("node", "")),
            base,
            EXACT,
            CAUTION,
        )
        items.append([head, how])
    return items


def _missing(assessment: dict) -> list[list[str]]:
    return [
        [
            _text(
                "- {} {}: {}; to settle it: {}",
                code(item.get("requirement", ""), 40),
                code(item.get("text", ""), 80),
                code(_words(str(item.get("reason", ""))), 120),
                code(item.get("settle_by", ""), 140),
            )
            + _issuer(assessment, item.get("requirement"))
        ]
        for item in (assessment.get("completion") or {}).get("not_established", [])
    ]


def _shown(assessment: dict) -> list[list[str]]:
    """Each requirement passed on its stated checks; one whose every check fails at the base commit and passes on the
    patch says so, as it shows the checks exercise the change (for a requirement that keeps behaviour, a failure at
    the base keeps its own meaning and is not shown as that)."""
    base = _at_base(assessment)
    items = []
    for item in (assessment.get("completion") or {}).get("demonstrated", []):
        checks = item.get("checks", [])
        line = _text("- {} on ", code(item.get("requirement", ""), 40)) + G6Text(", ").join(code(c, 80) for c in checks)
        if not _observed(assessment, item):
            line += G6Text(": ") + _assertion({"result": "passed", **item})
        elif item.get("kind") != "preserve" and checks and all(base.get(c) in ("failed", "timed_out") for c in checks):
            line += ": fails at the base commit, passes on the patch"
        items.append([line + _issuer(assessment, item.get("requirement"))])
    return items


def _decide(assessment: dict) -> list[str]:
    """The questions, then the unknowns themselves while they fit (only counted when they cannot), then merging,
    which always ends the part."""
    questions = (assessment.get("completion") or {}).get("open_questions", [])[:3]
    lines = [_text("- {}", code(q.get("question", ""), 300)) for q in questions]
    unknowns = assessment.get("unknowns") or []
    room = BUDGET["decide"] - len(lines) - 1
    if unknowns and room >= 2:
        lines.append(G6Text("- What G6 counts neither for nor against the patch:"))
        lines += [
            G6Text("  ") + line if line.startswith(G6Text("- and ")) else line
            for line in _cut(
                [[_text("  - {}", code(_words(str(u)), 200))] for u in unknowns], room - 1, "assessment.unknowns"
            )
        ]
    elif unknowns:
        lines.append(
            _text(
                "- {} unknown(s) G6 counts neither for nor against the patch ({}).",
                len(unknowns),
                code("assessment.unknowns"),
            )
        )
    lines.append(G6Text("- Merging: it needs your separate approval; G6 never merges."))
    return lines


def _base_words(outcomes: dict) -> str:
    """What the stated checks' run against the base commit's code shows: one that fails there exercises the change,
    one that passes there too may not, and one that could not run there shows neither."""
    total = len(outcomes)
    if not total:  # no outcome at the base: nothing failed there, so nothing shows the checks exercise the change
        return "no stated check has an outcome at the base commit, so G6 cannot say whether they exercise the change"
    fail = sum(verdict in ("failed", "timed_out") for verdict in outcomes.values())
    passing = sum(verdict == "passed" for verdict in outcomes.values())
    unrun = total - fail - passing
    if fail == total:
        return "the stated checks fail at the base commit, so they exercise the change"
    if passing == total:
        return "the stated checks pass at the base commit too, so they may not exercise the change"
    if unrun == total:
        return "the stated checks could not run at the base commit, so G6 cannot say whether they exercise the change"
    one = {"fail": fail == 1, "pass": passing == 1}
    parts = []
    if fail:
        parts.append(
            _text(
                "{} of the {} stated checks {} at the base commit ({} the change)",
                fail,
                total,
                "fails" if one["fail"] else "fail",
                "it exercises" if one["fail"] else "they exercise",
            )
        )
    if passing:
        verb = "passes" if one["pass"] else "pass"
        pronoun = "it" if one["pass"] else "they"
        parts.append(
            _text("{} {} there too ({} may not exercise the change)", passing, verb, pronoun)
            if fail
            else _text(
                "{} of the {} stated checks {} at the base commit too ({} may not exercise the change)",
                passing,
                total,
                verb,
                pronoun,
            )
        )
    if unrun:
        parts.append(_text("{} could not run there", unrun))
    return G6Text(", ").join(parts)


def _proposed(assessment: dict) -> str:
    """The proposed checks and template instances that ran, each with its result; none of them passes a
    requirement."""
    own = [
        a
        for a in (assessment.get("completion") or {}).get("activities", [])
        if a.get("activity") in ("proposed check", "template instance") and a.get("check")
    ]
    if not own:
        return G6Text("")
    shown = [
        _text("{}", code(a["check"], 40))
        + (_text(" for {}", code(a["requirement"], 40)) if a.get("requirement") else G6Text(""))
        + G6Text(": ")
        + (
            PROPOSED.get(str(a.get("result")), code(a.get("result", G6Text("")), 30))
            if _observed(assessment, a)
            else _assertion(a)
        )
        for a in own[:3]
    ]
    more = (
        _text(", and {} more ({})", len(own) - 3, code("assessment.completion.activities"))
        if len(own) > 3
        else G6Text("")
    )
    return (
        G6Text("the proposed checks and template instances, which never pass a requirement: ")
        + G6Text(", ").join(shown)
        + more
    )


def _steps(assessment: dict) -> list[str]:
    scope = assessment.get("scope") or {}
    search = scope.get("search") or {}
    requirements = assessment.get("requirements") or []
    who = ["the checks stated in the request"]  # reviewer B's B3: the agent states them, not the person
    if search.get("template_instances") or search.get("proposed_checks"):
        who.append("template instances and proposed checks")
    if search.get("model_calls"):
        who.append("G6's proposer, a model whose proposals decide nothing")
    runs = scope.get("checks_run") or []
    shown = []
    for run in runs[:3]:
        name = RUNS.get(run.get("check"), str(run.get("check", G6Text(""))))
        if not _observed(assessment, run):
            shown.append(_text("{}: {}", name, _assertion(run)))
        elif run.get("check") == "stated checks at the base commit" and run.get("outcomes"):
            shown.append(_base_words(_at_base(assessment, [run])))
        elif run.get("verdict") == "run_inconsistent":  # the owner's ruling (2026-10-29): said plainly, no accusation
            shown.append(
                _text(
                    "{}: passed by pytest's own count, but the process exited with an error afterwards ({}), so they are not counted as passed",
                    name,
                    code("run_inconsistent"),
                )
            )
        elif run.get("verdict") == "crashed":  # the same: a crash is no accusation
            shown.append(
                _text("{}: the test process crashed ({}), so this run counts neither way", name, code("crashed"))
            )
        else:
            shown.append(_text("{}: {}", name, code(run.get("verdict", ""), 30)))
    if _proposed(assessment):
        shown.append(_proposed(assessment))
    ran = G6Text("; ").join(shown) or "no test ran"
    skipped = scope.get("checks_skipped") or []
    passed_over = G6Text("; ").join(
        "deeper checks (not run)"
        if s.get("tier") == 3 and "not run" in str(s.get("reason", ""))
        else _text(
            "{} (not run: {})",
            RUNS.get(s.get("check"), str(s.get("check", ""))),
            code(_words(str(s.get("reason", ""))), 70),
        )
        for s in skipped[:2]
    )
    backend = ((scope.get("environment") or {}).get("isolation") or {}).get("backend")
    outcome = assessment.get("outcome") or "unknown"
    stage = {"test_integrity_compromised": "integrity", "defect_detected": "tier", "insufficient_evidence": "evidence"}
    codes = list(
        dict.fromkeys(
            r["code"]
            for r in assessment.get("reasons", [])
            if str(r.get("stage", "")).startswith(stage.get(outcome, "-"))
        )
    )
    rule = RULES.get(outcome, G6Text("")) + (
        _text(" ({}", ", ".join(codes[:4])) + (", ..." if len(codes) > 4 else G6Text("")) + G6Text(")")
        if codes
        else G6Text("")
    )
    if outcome == "supported_within_scope" and not _observed(assessment):
        rule = "the agent reports support within the stated scope; G6 did not run it"
    return [
        _text(
            "- **Proposed:** the patch {} on base {}, {} requirement(s); checks from {}.",
            code(str(scope.get("artifact_digest", ""))[:12]),
            code(str(scope.get("base_commit", ""))[:12]),
            len(requirements),
            ", ".join(who),
        ),
        _text(
            "- **Tested:** {}; isolation {}; {} seconds",
            ran,
            code(backend or "none"),
            _seconds(scope.get("seconds", 0)),
        )
        + (
            _text("; {}", passed_over)
            + (_text(" and {} more not run", len(skipped) - 2) if len(skipped) > 2 else G6Text(""))
            if skipped
            else G6Text("")
        )
        + G6Text("."),
        _text(
            "- **Gate:** {} ({}): {}{}.",
            OUTCOMES.get(outcome, outcome.replace("_", " ")),
            outcome,
            rule,
            _gate_note(assessment),
        ),
        G6Text("- **Approve:** waiting for you: merging needs your separate approval."),
    ]


def _gate_note(assessment: dict) -> str:
    """What the gate's sentence must not hide: the proposed checks that never gave a result (worked examples, round 3)."""
    count = sum(
        a.get("result") == "check_error" and a.get("activity") in ("proposed check", "template instance")
        for a in (assessment.get("completion") or {}).get("activities") or []
    )
    return (
        _text("; {} proposed check{} could not be judged", count, G6Text("s") if count != 1 else G6Text(""))
        if count
        else G6Text("")
    )


def _seconds(value: object) -> str:
    """Only numeric elapsed times are formatted as measurements."""
    return format(value, ".1f") if type(value) in (int, float) else code(value)


def _question_sha256(question: object) -> str:
    """The digest of a question's record as the assessment holds it (its canonical JSON), which is its identity."""
    from g6.core.ledger import digest

    try:
        return digest(question)
    except (TypeError, ValueError, RecursionError):
        return "(a question G6 cannot write)"


def _confirmed(assessment: dict) -> list[list[str]]:
    """G6 0.3, slice 3: each expectation the person confirmed (``assessment.confirmed_expectations``): the case asked
    (its positional and keyword arguments) and the outcome chosen, shown as the behaviour form shows them, in the one
    encoding of an outcome (``behaviour_question.shown_call``, ``shown_outcome``), the question's digest, so two cases
    that differ never read alike even where a long one is cut, the option chosen, the scope, who answered (recorded as
    given, not authenticated), its check's result on the patch and the issuer's statement. No model result is among
    them."""
    from g6.harness import behaviour_question as bq
    from functools import partial

    # Only confirmed-evidence lines use the form's caller-text encoding.
    show = partial(code, confirmed=True)

    held = assessment.get("confirmed_expectations") or {}
    statements = {s.get("expectation"): s for s in (held.get("evidence") or {}).get("expectations", [])}
    items = []
    for one in held.get("expectations", []):
        question, answer, check = one.get("question") or {}, one.get("answer") or {}, one.get("check") or {}
        scope = str(question.get("scope", G6Text("")))
        option = answer.get("option", G6Text(""))
        statement = statements.get(one.get("expectation_id")) or {}
        issuer = (
            "checked"
            if statement.get("checked")
            else "refuted"
            if statement.get("refute")
            else _text("not counted yet ({})", _words(statement.get("not_counted", "") or statement.get("gap", "")))
        )
        outcomes = question.get("outcomes") if isinstance(question.get("outcomes"), dict) else {}
        result = (
            CONFIRMED_RESULT.get(check.get("result"), "its check gave no result")
            if _observed(assessment, check)
            else _assertion(check)
        )
        items.append(
            [
                _text(
                    "- {}: the person chose {}, {} (question SHA-256 {}), scope shown in full below, answered by {} (recorded as given, not authenticated); {}; the evidence issuer: {}; Scope (exact text): {}. ",
                    show(bq.shown_call(question), 200),
                    show(option, 20),
                    show(bq.shown_outcome(outcomes.get(option)), 120),
                    show(_question_sha256(question), 70),
                    show(answer.get("answered_by", ""), 60),
                    result,
                    show(issuer, 120),
                    show(scope, None, literal=True),
                )
                + _execution_limit()
            ]
        )
    return items


def _execution_limit() -> str:
    from g6.harness.behaviour_runner import LIMIT

    return G6Text(LIMIT)


def render(assessment: Assessment) -> G6Text:
    """The report of ``assessment`` (the module's order and rules), from its decided fields alone."""
    assessment = caller_values(assessment)
    lines = [_headline(assessment), G6Text(""), G6Text("## Reproduced findings")]
    lines += _cut(_findings(assessment), BUDGET["findings"], "assessment.completion.defects_reproduced") or [
        G6Text("- None.")
    ]
    lines += [G6Text(""), G6Text("## Not established")]
    lines += _cut(_missing(assessment), BUDGET["missing"], "assessment.completion.not_established") or [
        G6Text("- None.")
    ]
    lines += [G6Text(""), G6Text("## Demonstrated on the stated checks")]
    lines += _cut(_shown(assessment), BUDGET["shown"], "assessment.completion.demonstrated") or [G6Text("- None.")]
    if assessment.get("confirmed_expectations"):  # only when the review enforced any: otherwise the report is as it was
        lines += [G6Text(""), G6Text("## What the person confirmed")]
        lines += _cut(_confirmed(assessment), CONFIRMED_SHOWN, "assessment.confirmed_expectations") or [
            G6Text("- None.")
        ]
    lines += [G6Text(""), G6Text("## For you to decide"), *_decide(assessment)[: BUDGET["decide"]]]
    lines += [G6Text(""), G6Text("## How this was decided"), *_steps(assessment), G6Text(""), STANDING_NOTE]
    if assessment.get("not_checked"):
        lines.append(
            G6Text("Not checked: ") + G6Text("; ").join(str(item) for item in assessment["not_checked"]) + G6Text(".")
        )
    return _RenderedReport(_join_report([part for line in lines for part in (line, G6Text("\n"))][:-1]))


def _loop_lines(loop: dict) -> list[str]:
    """What the completion loop did. Its probes are a model's, so they speak only to what they tried: nothing here says
    the patch is right, and what they never touched stays in the review's list of what is not established."""
    sets, failing = loop.get("sets") or [], loop.get("failing") or []
    if not _observed(loop) or any(type(one.get("ran")) is not int for one in sets):
        return [
            G6Text("## What the completion loop reported"),
            G6Text("- The agent reports probe results; G6 did not run it."),
            _text("- Reported reason: {}.", code(loop.get("reason"))),
            *_cut(
                [
                    [
                        _text(
                            "- Reported set {}: {} probes; left out: {}.",
                            code(one.get("label")),
                            code(one.get("ran", 0)),
                            code(one.get("left_out", "")),
                        )
                    ]
                    for one in sets
                ],
                BUDGET["findings"],
                "loop.sets",
            ),
            *[_text("- Reported failing probe: {}.", code(case, 200)) for case in failing[: BUDGET["findings"]]],
        ]
    ran = sum(one["ran"] for one in sets)
    result = (
        _text("{} still fail, of {} probes that ran", len(failing), ran)
        if failing
        else _text("no counterexample found in {} probes; that does not show the patch is right", ran)
        if ran
        else "no probe ran, so the loop says nothing for or against the patch"
    )
    lines = [
        G6Text("## What the completion loop tested"),
        _text(
            "- **Stopped:** {}, after {} of at most {} repairs.",
            _inert(loop.get("reason")),
            _inert(loop.get("repairs")),
            _inert(loop.get("max_repairs")),
        ),
        _text("- **Result:** {}.", result),
    ]
    if ran and loop.get("boundary"):  # the review below ran no test of its own, so its isolation says nothing of these
        lines.append(
            _text(
                "- **Boundary:** the probes ran inside {}, in scratch copies with no network.", code(loop["boundary"])
            )
        )
    late = [code(one["label"]) for one in sets if one.get("late")]
    if late:
        lines.append(
            _text(
                "- **Not the loop the worked examples measured:** {} ran after the agent stopped, not before it started: {}.",
                ", ".join(late),
                code(loop.get("late_because"), 200),
            )
        )
    for one in sets:
        kind = "hidden from the agent" if one.get("hidden") else "the agent could read it"
        lines.append(
            _text("- Left out {} ({}): {}", code(one["label"]), kind, code(one["left_out"], 200))
            if one.get("left_out")
            else _text("- Ran {} ({}): {} probes.", code(one["label"]), kind, _inert(one["ran"]))
        )
        lines += [_text("  - Refused: {}", code(why, 200)) for why in (one.get("refused") or [])[:2]]
    critic = loop.get("critic")
    lines.append(
        G6Text("- No reviewer from another model family read the patch.")
        if critic is None
        else _text("- The reviewer from another model family did not finish: {}", code(critic["error"], 200))
        if critic.get("error")
        else _text("- A reviewer from another model family read the patch once: {}.", code(critic.get("verdict")))
    )
    if failing:
        lines += [G6Text(""), G6Text("## Probes that still fail")]
        lines += _cut(
            [[_text("- {}", code(case, 200))] for case in failing], BUDGET["findings"], "report.md's folder, state.json"
        )
    return [*lines, G6Text(""), "A model wrote these probes, so none of them passes a requirement."]


def _times(count: int) -> str:
    return {1: "once", 2: "twice"}.get(count, _text("{} times", count))


def findings(reviews: list[dict]) -> tuple[list[dict], list[dict]]:
    """The reviews whose test failed on the patch in any run, as a repair can break a test that passed when it was written:
    fixed (it passed on the last run) and open (anything else: a test that did not run again says nothing, so it fixes
    nothing)."""
    last = reviews[-1]["results"] if reviews else {}
    found = [one for one in reviews if any(run["results"].get(one["file"]) == "failed" for run in reviews)]
    return [one for one in found if last.get(one["file"]) == "passed"], [
        one for one in found if last.get(one["file"]) != "passed"
    ]


def _latest(reviews: list[dict], file: str) -> str | None:
    """What the latest run that reached ``file`` said of it, None if none did. A review's own ``results`` are its round's, and
    a later repair can change what its test does, so nothing here may read them to say what a test does now."""
    return next((run["results"][file] for run in reversed(reviews) if file in run["results"]), None)


def _review_lines(loop: dict) -> list[str]:
    """What the review-until-clean loop did, reproduced findings first: what was found, what was fixed, what stayed open.
    A finding is a reviewer's test that failed on the patch. It stays open at the cap, or when the agent rejects it, and an
    open finding is no ruling on the patch: nothing here says it is right or wrong."""
    reviews = loop.get("reviews") or []
    if not _observed(loop) or any(type(one.get("masked", 0)) is not int for one in reviews):
        return [
            G6Text("## What the reviews reported"),
            G6Text("- The agent reports review results; G6 did not run it."),
            _text("- Reported reason: {}.", code(loop.get("reason"))),
            *_cut(
                [
                    [
                        _text(
                            "- Reported review {}: {}; {}; result {}.",
                            code(one.get("n")),
                            code(one.get("rule")),
                            code(one.get("file")),
                            code(one.get("verdict")),
                        ),
                        *[
                            _text("  - {}: {}.", code(test), _assertion({"result": result}))
                            for test, result in (one.get("results") or {}).items()
                        ],
                    ]
                    for one in reviews
                ],
                BUDGET["findings"],
                "loop.reviews",
            ),
        ]
    fixed, still = findings(reviews)
    last, count = reviews[-1]["results"] if reviews else {}, len(fixed) + len(still)
    lines = [G6Text("## What the reviews found")]
    if loop.get("ending"):
        lines.append(
            _text("- **Ending:** {}: {}.", code(loop["ending"]), ENDINGS.get(loop["ending"], "unknown ending"))
        )
    lines.append(
        _text("- **Found:** {} reproduced, each a reviewer's test that failed on the patch.", count)
        if count
        else G6Text("- **Found:** no reviewer's test failed on the patch.")
    )
    lines += _cut(
        [
            [_text("- **Fixed:** review {}: {}; its test passes now.", one["n"], code(one["rule"], 200))]
            for one in fixed
        ],
        BUDGET["findings"],
        "report.md's folder, state.json",
    )
    lines += _cut(
        [
            [
                _text("- **Open:** review {}: {}; its test {} ", one["n"], code(one["rule"], 200), code(one["file"]))
                + {
                    "failed": "fails.",
                    NOT_PASSED: "ran, but the case that failed did not pass, so nothing shows it is fixed.",
                    NOT_JUDGED: "ran, but a case could not be judged, so nothing shows it is fixed.",
                }.get(last.get(one["file"]), "did not run again, so nothing shows it is fixed."),
                _text("  - The reviewer's reasons: {}", code(one["why"], 300)),
            ]
            for one in still
        ],
        BUDGET["findings"],
        "report.md's folder, state.json",
    )
    if loop.get("left"):
        lines.append(_text("- **The agent left the code as it was.** Its reason: {}.", code(loop["left"], 300)))
    if loop.get("no_clean"):
        error = reviews[-1].get("error") if reviews else G6Text("")
        lines.append(
            _text("- **No clean review:** {}", _inert(loop.get("reason")))
            + (_text(": {}.", code(error, 200)) if error else ".")
        )
    lines += [
        G6Text(""),
        G6Text("## How the loop ran"),
        _text(
            "- **Stopped:** {}, after {} review{} and {} of at most {} repairs.",
            _inert(loop.get("reason")),
            len(reviews),
            G6Text("s") if len(reviews) != 1 else G6Text(""),
            _inert(loop.get("repairs")),
            _inert(loop.get("max_repairs")),
        ),
    ]
    if reviews:
        lines.append(
            _text("- A reviewer from another model family read the patch {}: ", _times(len(reviews)))
            + G6Text(", ").join(code(one.get("verdict")) for one in reviews)
            + G6Text(".")
        )
    for one in reviews:
        if one.get("told"):
            lines.append(
                _text(
                    "- Review {} first named a defect, {}, but its test would not run: {}. The reviewer was told once, and answered again.",
                    one["n"],
                    code(one["claimed"], 200),
                    code(one["told"], 200),
                )
            )
        if one.get("verdict") == "defect" and one not in (*fixed, *still):
            said = _latest(reviews, one["file"])
            why = (
                _text("a case of its test could not be judged: {}", code(one.get("unrun", ""), 200))
                if said == NOT_JUDGED
                else _text("its test would not run: {}", code(one["unrun"], 200))
                if said == "did not run" and one.get("unrun")
                else "its test passes on the patch"
                if said == "passed"
                else "it wrote no test"
                if said is None
                else "its test showed nothing"
            )
            lines.append(
                _text(
                    "- Review {} named a defect but showed no failure: {}; {}.", one["n"], code(one["rule"], 200), why
                )
            )
    masked = sum(one.get("masked", 0) for one in reviews)
    if masked:
        lines.append(
            _text(
                "- **Masked:** G6 put {} in place of {} value{} that look like secrets before the reviewer read the patch, so none was sent, and none was reviewed.",
                code("[REDACTED]"),
                masked,
                G6Text("s") if masked != 1 else G6Text(""),
            )
        )
    if loop.get("boundary"):  # known only from a test that ran
        lines.append(
            _text(
                "- **Boundary:** the reviewers' tests ran inside {}, in scratch copies with no network.",
                code(loop["boundary"]),
            )
        )
    return [*lines, G6Text(""), G6Text("## What this loop does not show"), *LOOP_LIMITS]


def completion_report(loop: Loop, review: str) -> G6Text:
    """The completion loop's report: what it tested and what still fails, above the ``review`` of the patch it stopped
    on, which says what is not established."""
    loop = caller_values(loop)
    # Only a completed report retains its formatting across this public boundary.
    # A caller cannot mark arbitrary input as a G6 template to opt out of quoting.
    review = review if isinstance(review, _RenderedReport) else str(review)
    return _RenderedReport(
        G6Text("\n").join(
            [*(_loop_lines(loop) if loop.get("reviews") is None else _review_lines(loop)), G6Text(""), review]
        )
    )


def safe_render(assessment: dict) -> str:
    """``render``, or one line that says where the review's fields are when it could not be rendered."""
    try:
        return render(assessment)
    except Exception as exc:  # the report decides nothing, so it never fails the review
        return _RenderedReport(
            _text(
                "G6 could not render this review's report ({}); every finding is in the assessment's fields. Qualified draft: merging needs your separate approval.",
                type(exc).__name__,
            )
        )


__all__ = [
    "ENDINGS",
    "MAX_LINES",
    "NOT_JUDGED",
    "NOT_PASSED",
    "STANDING_NOTE",
    "code",
    "completion_report",
    "findings",
    "render",
    "safe_render",
]
