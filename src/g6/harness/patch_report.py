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
6. the standing note: the specification may be incomplete.

A list cut at its room ends with "and N more" and names the field that holds the rest. Text the repository or the
caller wrote (node ids, paths, requirement text, messages, commands) is shown only inside code spans fenced against
the backticks it holds, on one line, so it can neither break the Markdown nor read as G6's own words."""
from __future__ import annotations

import re

MAX_LINES = 40
#: The third part the reports of V9's arm 3 must keep (the registered protocol's amendment A5).
STANDING_NOTE = ("The task's specification may be incomplete: G6 checked the requirements as they were stated and "
                 "cannot rule out something they leave out.")
OUTCOMES = {"defect_detected": "A defect was found", "supported_within_scope": "Supported within its stated scope",
            "insufficient_evidence": "Not enough evidence", "test_integrity_compromised": "Test integrity is in doubt"}
RULES = {"test_integrity_compromised": "a sign that the tests, their configuration or a run cannot be trusted comes "
                                       "before every other finding",
         "defect_detected": "a stated check, or a test of the changed code that passes at the base commit, failed; or "
                            "the patch adds an import a stated contract forbids",
         "insufficient_evidence": "something needed to support the patch is missing",
         "supported_within_scope": "every requirement passed a stated check the patch did not write, and no "
                                   "other problem was found"}
INTEGRITY = {"test_weakened": "a test or a test helper was weakened",
             "runner_configuration_changed": "the test runner's configuration changed",
             "ignored_file_can_change_tests": "a file git ignores could change the tests",
             "changed_file_can_change_tests": "a file the patch adds or changes could change what the tests load",
             "run_not_trusted": "a test run's result cannot be believed",
             "repository_changed_during_review": "the working tree changed during the review",
             "gate_shortcut": "the diff of the tests takes a shortcut"}
FAILED = {"check_failed": "A stated check failed", "derived_test_regressed": "A test of the changed code fails",
          "forbidden_dependency_added": "The patch adds an import a stated contract forbids"}
UNCONFIRMED = {"counterexample": "A proposed check found a counterexample, not confirmed",
               "unconfirmed_failure": "A proposed check fails on the patch, not confirmed"}
BUDGET = {"findings": 10, "missing": 5, "shown": 3, "decide": 5}
#: The review's runs and skips by plain names (no internal tier names in the report).
RUNS = {"stated checks and extra tests": "the stated checks",
        "tests that touch the changed modules": "tests that touch the changed code",
        "stated checks at the base commit": "the stated checks at the base commit",
        "mutation and old-against-new probes": "deeper checks"}
#: Where the exact command G6 ran is, beside the repository's plain one.
EXACT = ("G6 ran it with its own isolating options; the exact command is in "
         "`assessment.completion.defects_reproduced` (`reproduction.command`)")
#: A proposed check's or template instance's result, in the Tested step (the owner's pilot, 2026-10-30).
#: A structural instance's results (forbidden_dependency.v1) are words too; one against a stated contract can decide
#: the outcome, so the step says only that none of these passes a requirement (the fresh review of these fixes).
PROPOSED = {"not_refuted": "not refuted", "counterexample": "found a counterexample",
            "unconfirmed_failure": "fails on the patch", "fails_on_base_too": "fails at the base commit too",
            "behaviour_changed": "behaves differently from the base commit (a question, not a finding)",
            "check_error": "could not be judged", "refused": "refused", "failed": "failed", "passed": "passed",
            "vacuous": "vacuous", "not_established": "not established"}
#: Reviewer B's B6: the caution a proposed or compiled check's reproduction carries, said on its line too.
CAUTION = "Read it before you run it, or run it in a container."
#: What the person does about git-ignored files the tests could load (worked examples, W3b), under that finding.
IGNORED_TO_DO = ("  - What to do: these files are untracked and git-ignored, yet the tests could load them (one named "
                 "like a module in the repository could replace it), so G6 cannot rule that out. If they belong to the "
                 "project, commit them before the change under review; otherwise remove them or move them out of the "
                 "repository; then review again. Each is named once in `assessment.next_actions`.")
TO_DO = frozenset({"ignored_file_can_change_tests"})


def _plain(node: object) -> str:
    """The repository's plain command for ``node``: its own pytest, with none of G6's isolating options."""
    import shlex

    return code(f"python -m pytest {shlex.quote(str(node))}", 200)


def _words(text: str) -> str:
    """G6's own wording of a run or a skip, with its internal tier names put plainly."""
    for old, new in (("tier 1 found", "the stated checks found"), ("tier 2 found", "the tests of the changed code "
                                                                                    "found"),
                     ("tier 1", "the stated checks"), ("tier 2", "the tests of the changed code"),
                     ("tier 3 not run: ", ""), ("tier 3", "deeper checks")):
        text = text.replace(old, new)
    return text


def _at_base(assessment: dict) -> dict[str, str]:
    """Each stated check's own verdict at the base commit, from the review's run there (empty when none ran)."""
    runs = (assessment.get("scope") or {}).get("checks_run") or []
    found = next((run for run in runs if run.get("check") == "stated checks at the base commit"), None)
    return dict((found or {}).get("outcomes") or {})


def code(value: object, limit: int = 140) -> str:
    """``value`` as one inert code span: on one line, cut at ``limit`` characters, fenced by more backticks than any
    run inside it (padded where it begins or ends with one), so nothing in it is read as Markdown or HTML."""
    text = " ".join(str(value).split())
    text = "".join(ch if ch.isprintable() else "?" for ch in text) or "(empty)"
    if len(text) > limit:
        text = text[:limit - 3] + "..."
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text[0] == "`" or text[-1] == "`" else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _cut(items: list[list[str]], room: int, field: str) -> list[str]:
    """Each item's lines while they fit in ``room`` lines, and "and N more" naming ``field`` for the rest."""
    lines: list[str] = []
    for index, item in enumerate(items):
        left = len(items) - index
        if len(lines) + len(item) > room - (1 if left > 1 else 0):
            lines.append(f"- and {left} more ({code(field)})")
            break
        lines += item
    return lines


def _headline(assessment: dict) -> str:
    outcome = assessment.get("outcome") or "unknown"
    if outcome == "test_integrity_compromised":
        first = next((r["code"] for r in assessment.get("reasons", []) if r.get("stage") == "integrity"), "")
        plain = ("Tests or their configuration were weakened" if first in (
            "test_weakened", "runner_configuration_changed", "gate_shortcut") else "Test integrity is in doubt")
    else:
        plain = OUTCOMES.get(outcome, "The review gave no outcome")
    return f"**{plain}** ({outcome}). Qualified draft: merging needs your separate approval."


def _findings(assessment: dict) -> list[list[str]]:
    completion = assessment.get("completion") or {}
    defects = completion.get("defects_reproduced") or {}
    items = [[f"- **Test integrity: {INTEGRITY.get(r['code'], 'the tests cannot be trusted')}** ({r['code']}): "
              f"{code(_words(str(r.get('detail', ''))))}", *([IGNORED_TO_DO] if r["code"] in TO_DO else [])]
             for r in assessment.get("reasons", []) if r.get("stage") == "integrity"]
    for found in defects.get("stated_and_repository_checks", []):
        requirements = ", ".join(code(r) for r in found.get("requirements", [])) or "no stated requirement"
        reproduction = found.get("reproduction") or {}
        head = (f"- **{FAILED.get(found.get('code'), 'A check failed')}** ({found.get('code')}) for {requirements}: "
                f"{code(found.get('check', ''))}")
        if reproduction.get("kind") == "static":
            imports = reproduction.get("imports") or [{}]
            first = imports[0]
            how = (f"  - Read from the files, no code runs: {code(first.get('file', ''))} line {first.get('line', '?')} "
                   f"holds {code(first.get('text', ''))}; the base commit's files have no such import"
                   + (f"; the repository's own linter: {code(reproduction['command'])}" if reproduction.get("command")
                      else ""))
        else:
            base = ("passes at the base commit, fails on the patch" if found.get("code") == "derived_test_regressed"
                    else "not run at the base commit")
            how = f"  - Reproduce: {_plain(reproduction.get('node', found.get('check', '')))}; {base}. {EXACT}."
        items.append([head, how])
    for found in defects.get("proposed_checks", []):
        reproduction = found.get("reproduction") or {}
        head = (f"- **{UNCONFIRMED.get(found.get('result'), 'A proposed check found a difference, not confirmed')}** "
                f"({found.get('code')}) for {code(found.get('requirement', ''))}: {code(found.get('check', ''))}")
        if found.get("result") == "counterexample":
            base = "passes at the base commit, fails on the patch"
        elif str(reproduction.get("at_base", "not run")).startswith("not run"):
            base = "not run at the base commit"
        else:
            base = f"at the base commit: {code(reproduction.get('at_base', ''), 80)}"
        how = (f"  - Reproduce: write its text (in {code('assessment.completion.defects_reproduced.proposed_checks')}) "
               f"to {code(reproduction.get('file', ''), 80)}, then {_plain(reproduction.get('node', ''))}; {base}. "
               f"{EXACT}. The check is code someone other than G6 wrote: {CAUTION}")
        items.append([head, how])
    return items


def _missing(assessment: dict) -> list[list[str]]:
    return [[f"- {code(item.get('requirement', ''), 40)} {code(item.get('text', ''), 80)}: "
             f"{code(_words(str(item.get('reason', ''))), 120)}; to settle it: {code(item.get('settle_by', ''), 140)}"]
            for item in (assessment.get("completion") or {}).get("not_established", [])]


def _shown(assessment: dict) -> list[list[str]]:
    """Each requirement passed on its stated checks; one whose every check fails at the base commit and passes on the
    patch says so, as it shows the checks exercise the change (for a requirement that keeps behaviour, a failure at
    the base keeps its own meaning and is not shown as that)."""
    base = _at_base(assessment)
    items = []
    for item in (assessment.get("completion") or {}).get("demonstrated", []):
        checks = item.get("checks", [])
        line = f"- {code(item.get('requirement', ''), 40)} on " + ", ".join(code(c, 80) for c in checks)
        if item.get("kind") != "preserve" and checks and all(base.get(c) in ("failed", "timed_out") for c in checks):
            line += ": fails at the base commit, passes on the patch"
        items.append([line])
    return items


def _decide(assessment: dict) -> list[str]:
    """The questions, then the unknowns themselves while they fit (only counted when they cannot), then merging,
    which always ends the part."""
    questions = (assessment.get("completion") or {}).get("open_questions", [])[:3]
    lines = [f"- {code(q.get('question', ''), 300)}" for q in questions]
    unknowns = assessment.get("unknowns") or []
    room = BUDGET["decide"] - len(lines) - 1
    if unknowns and room >= 2:
        lines.append("- What G6 counts neither for nor against the patch:")
        lines += ["  " + line if line.startswith("- and ") else line
                  for line in _cut([[f"  - {code(_words(str(u)), 200)}"] for u in unknowns], room - 1,
                                   "assessment.unknowns")]
    elif unknowns:
        lines.append(f"- {len(unknowns)} unknown(s) G6 counts neither for nor against the patch "
                     f"({code('assessment.unknowns')}).")
    lines.append("- Merging: it needs your separate approval; G6 never merges.")
    return lines


def _base_words(outcomes: dict) -> str:
    """What the stated checks' run against the base commit's code shows: one that fails there exercises the change,
    one that passes there too may not, and one that could not run there shows neither."""
    total = len(outcomes)
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
        parts.append(f"{fail} of the {total} stated checks {'fails' if one['fail'] else 'fail'} at the base commit "
                     f"({'it exercises' if one['fail'] else 'they exercise'} the change)")
    if passing:
        verb = "passes" if one["pass"] else "pass"
        pronoun = "it" if one["pass"] else "they"
        parts.append(f"{passing} {verb} there too ({pronoun} may not exercise the change)" if fail else
                     f"{passing} of the {total} stated checks {verb} at the base commit too ({pronoun} may not "
                     "exercise the change)")
    if unrun:
        parts.append(f"{unrun} could not run there")
    return ", ".join(parts)


def _proposed(assessment: dict) -> str:
    """The proposed checks and template instances that ran, each with its result; none of them passes a
    requirement."""
    own = [a for a in (assessment.get("completion") or {}).get("activities", [])
           if a.get("activity") in ("proposed check", "template instance") and a.get("check")]
    if not own:
        return ""
    shown = [f"{code(a['check'], 40)}" + (f" for {code(a['requirement'], 40)}" if a.get("requirement") else "")
             + f": {PROPOSED.get(str(a.get('result')), code(a.get('result', ''), 30))}" for a in own[:3]]
    more = f", and {len(own) - 3} more ({code('assessment.completion.activities')})" if len(own) > 3 else ""
    return "the proposed checks and template instances, which never pass a requirement: " + ", ".join(shown) + more


def _steps(assessment: dict) -> list[str]:
    scope = assessment.get("scope") or {}
    search = scope.get("search") or {}
    requirements = assessment.get("requirements") or []
    who = ["the checks stated in the request"]   # reviewer B's B3: the agent states them, not the person
    if search.get("template_instances") or search.get("proposed_checks"):
        who.append("template instances and proposed checks")
    if search.get("model_calls"):
        who.append("G6's proposer, a model whose proposals decide nothing")
    runs = scope.get("checks_run") or []
    shown = []
    for run in runs[:3]:
        name = RUNS.get(run.get("check"), str(run.get("check", "")))
        outcomes = run.get("outcomes") or {}
        if run.get("check") == "stated checks at the base commit" and outcomes:
            shown.append(_base_words(outcomes))
        elif run.get("verdict") == "run_inconsistent":   # the owner's ruling (2026-10-29): said plainly, no accusation
            shown.append(f"{name}: passed by pytest's own count, but the process exited with an error afterwards "
                         f"({code('run_inconsistent')}), so they are not counted as passed")
        elif run.get("verdict") == "crashed":   # the same: a crash is no accusation
            shown.append(f"{name}: the test process crashed ({code('crashed')}), so this run counts neither way")
        else:
            shown.append(f"{name}: {code(run.get('verdict', ''), 30)}")
    if _proposed(assessment):
        shown.append(_proposed(assessment))
    ran = "; ".join(shown) or "no test ran"
    skipped = scope.get("checks_skipped") or []
    passed_over = "; ".join(
        "deeper checks (not run)" if s.get("tier") == 3 and "not run" in str(s.get("reason", "")) else
        f"{RUNS.get(s.get('check'), str(s.get('check', '')))} (not run: {code(_words(str(s.get('reason', ''))), 70)})"
        for s in skipped[:2])
    backend = ((scope.get("environment") or {}).get("isolation") or {}).get("backend")
    outcome = assessment.get("outcome") or "unknown"
    stage = {"test_integrity_compromised": "integrity", "defect_detected": "tier", "insufficient_evidence": "evidence"}
    codes = list(dict.fromkeys(r["code"] for r in assessment.get("reasons", [])
                               if str(r.get("stage", "")).startswith(stage.get(outcome, "-"))))
    rule = RULES.get(outcome, "") + (f" ({', '.join(codes[:4])}" + (", ..." if len(codes) > 4 else "") + ")"
                                     if codes else "")
    return [f"- **Proposed:** the patch {code(str(scope.get('artifact_digest', ''))[:12])} on base "
            f"{code(str(scope.get('base_commit', ''))[:12])}, {len(requirements)} requirement(s); checks from "
            f"{', '.join(who)}.",
            f"- **Tested:** {ran}; isolation {code(backend or 'none')}; {float(scope.get('seconds') or 0):.1f} "
            f"seconds" + (f"; {passed_over}" + (f" and {len(skipped) - 2} more not run" if len(skipped) > 2 else "")
                          if skipped else "") + ".",
            f"- **Gate:** {OUTCOMES.get(outcome, outcome.replace('_', ' '))} ({outcome}): {rule}{_gate_note(assessment)}.",
            "- **Approve:** waiting for you: merging needs your separate approval."]


def _gate_note(assessment: dict) -> str:
    """What the gate's sentence must not hide: the proposed checks that never gave a result (worked examples, round 3)."""
    count = sum(a.get("result") == "check_error" and a.get("activity") in ("proposed check", "template instance")
                for a in (assessment.get("completion") or {}).get("activities") or [])
    return f"; {count} proposed check{'s' if count != 1 else ''} could not be judged" if count else ""


def render(assessment: dict) -> str:
    """The report of ``assessment`` (the module's order and rules), from its decided fields alone."""
    lines = [_headline(assessment), "", "## Reproduced findings"]
    lines += _cut(_findings(assessment), BUDGET["findings"], "assessment.completion.defects_reproduced") or ["- None."]
    lines += ["", "## Not established"]
    lines += _cut(_missing(assessment), BUDGET["missing"], "assessment.completion.not_established") or ["- None."]
    lines += ["", "## Demonstrated on the stated checks"]
    lines += _cut(_shown(assessment), BUDGET["shown"], "assessment.completion.demonstrated") or ["- None."]
    lines += ["", "## For you to decide", *_decide(assessment)[:BUDGET["decide"]]]
    lines += ["", "## How this was decided", *_steps(assessment), "", STANDING_NOTE]
    return "\n".join(lines)


def _loop_lines(loop: dict) -> list[str]:
    """What the completion loop did. Its probes are a model's, so they speak only to what they tried: nothing here says
    the patch is right, and what they never touched stays in the review's list of what is not established."""
    sets, failing = loop.get("sets") or [], loop.get("failing") or []
    ran = sum(one["ran"] for one in sets)
    result = (f"{len(failing)} still fail, of {ran} probes that ran" if failing else
              f"no counterexample found in {ran} probes; that does not show the patch is right" if ran else
              "no probe ran, so the loop says nothing for or against the patch")
    lines = ["## What the completion loop tested",
             f"- **Stopped:** {loop.get('reason')}, after {loop.get('repairs')} of at most {loop.get('max_repairs')} "
             "repairs.", f"- **Result:** {result}."]
    if ran and loop.get("boundary"):   # the review below ran no test of its own, so its isolation says nothing of these
        lines.append(f"- **Boundary:** the probes ran inside {code(loop['boundary'])}, in scratch copies with no network.")
    late = [code(one["label"]) for one in sets if one.get("late")]
    if late:
        lines.append(f"- **Not the loop the worked examples measured:** {', '.join(late)} ran after the agent stopped, "
                     f"not before it started: {code(loop.get('late_because'), 200)}.")
    for one in sets:
        kind = "hidden from the agent" if one.get("hidden") else "the agent could read it"
        lines.append(f"- Left out {code(one['label'])} ({kind}): {code(one['left_out'], 200)}" if one.get("left_out") else
                     f"- Ran {code(one['label'])} ({kind}): {one['ran']} probes.")
        lines += [f"  - Refused: {code(why, 200)}" for why in (one.get("refused") or [])[:2]]
    critic = loop.get("critic")
    lines.append("- No reviewer from another model family read the patch." if critic is None else
                 f"- The reviewer from another model family did not finish: {code(critic['error'], 200)}"
                 if critic.get("error") else
                 f"- A reviewer from another model family read the patch once: {code(critic.get('verdict'))}.")
    if failing:
        lines += ["", "## Probes that still fail"]
        lines += _cut([[f"- {code(case, 200)}"] for case in failing], BUDGET["findings"], "report.md's folder, state.json")
    return [*lines, "", "A model wrote these probes, so none of them passes a requirement."]


def completion_report(loop: dict, review: str) -> str:
    """The completion loop's report: what it tested and what still fails, above the ``review`` of the patch it stopped
    on, which says what is not established."""
    return "\n".join([*_loop_lines(loop), "", review])


def safe_render(assessment: dict) -> str:
    """``render``, or one line that says where the review's fields are when it could not be rendered."""
    try:
        return render(assessment)
    except Exception as exc:   # the report decides nothing, so it never fails the review
        return (f"G6 could not render this review's report ({type(exc).__name__}); every finding is in the "
                "assessment's fields. Qualified draft: merging needs your separate approval.")


__all__ = ["MAX_LINES", "STANDING_NOTE", "code", "completion_report", "render", "safe_render"]
