# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The person's confirmed expectations, enforced on the patch under review (G6 0.3 design, section 3.2 step 4 and
section 4 step 5; slice 3, item 5).

Each confirmed expectation of the task's standard (``g6.harness.confirmed_expectation.ExpectationStandard``) is its
one ``example.v1`` check, compiled from the question's bytes and the option the person chose
(``ConfirmedExpectation.instance``, provenance ``person_confirmed``). The review's existing machinery runs it:
``verification_templates.prepare`` resolves its names on the tree and compiles it from the template's fixed text, and
verification search's executor runs it confined, with no network, in a copy of the patched tree, then in a copy at
the base commit's code, then, when it passed on the patch and failed at the base commit, once more on the patch, so
the issuer has a pass its control (the run at the base commit) came before (``requirement_evidence``'s rule for a
stated check). A copy's run has no test receipt from the receipt store (only a run in place has one); the issuer's
receipt binds the review's artefact digest.

Its result is the review's like a stated check's, read from every run on the patch: ``failed`` when any of them
fails there (a defect the review reproduces: what the person confirmed does not hold), the first such run its
reproduction; ``passed`` when it passes there and no run on the patch failed; and ``not_run`` with why otherwise.
Nothing here decides the review's outcome; ``patch_review`` reads these results.

Each run on the patch is bound to the bytes the issuer is told about: given the review's snapshot of the tree
(``before``, whose digest is ``artifact``), the executor refuses a copy that is not that tree
(``verification_search._run_check``'s ``artefact``), and such a refusal is recorded (``tree_changed``), never run.
Without ``before`` nothing here is bound to the artefact (``bound`` false) and the issuer counts none of it.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

from g6.core import component_process
from g6.harness import verification_search as vs
from g6.harness import verification_templates
from g6.harness.confirmed_expectation import ExpectationStandard
from g6.workforce import repo_tests

PASSED, FAILED, NOT_RUN = "passed", "failed", "not_run"
#: The label every result and reproduction carries: who wrote what the check expects, and who compiled it.
COMPILED = ("G6 compiled it from the fixed text of example.v1 and the outcome the person confirmed: the person, not "
            "the patch's author, fixed what it expects")
CAUTION = ("G6 generated the check from the fixed text of example.v1, with the case and the outcome the person "
           "confirmed written in it as one literal; it imports and runs the repository's code, so G6 ran it only "
           "inside its operating-system boundary, with no network. Read it before you run it, or run it in a "
           "container.")


def _result(on_patch: dict) -> tuple[str, str]:
    if vs._to_a_result(on_patch):
        return (FAILED if on_patch["result"] == "failed" else PASSED), on_patch.get("detail", "")
    return NOT_RUN, f"on the patch it did not run to a result: {on_patch.get('detail', '')}"


def _failed(on_patch: dict) -> bool:
    """Whether a run on the patch observed the confirmed case fail (a failure G6 can read as the code's)."""
    return vs._to_a_result(on_patch) and on_patch["result"] == "failed"


def patch_result(readings: Sequence[object]) -> tuple[str, str]:
    """Read every patch run: an observed failure decides; anything short of all passes prevents support."""
    failed = next((one for one in readings if isinstance(one, Mapping) and one.get("result") == FAILED
                   and vs._to_a_result(one)), None)
    if failed is not None:
        return FAILED, str(failed.get("detail", ""))
    for one in readings:
        if not isinstance(one, Mapping) or one.get("result") != PASSED or not vs._to_a_result(one):
            detail = one.get("detail", "an unavailable or unrecognised run") if isinstance(one, Mapping) else "a partial run"
            return NOT_RUN, f"on the patch it did not run to a result: {detail}"
    return (PASSED, str(readings[0].get("detail", ""))) if readings else (NOT_RUN, "no run on the patch")


def normal_reading(found: dict) -> dict:
    """One reading decides enforcement and every issuer summary, including unavailability."""
    return found if vs._to_a_result(found) else {**found, "result": "error"}


def run(root: Path, change: repo_tests.Change, standard: ExpectationStandard, *, deadline: float, artifact: str,
        roots: Sequence[str] = (), skip: str = "", before: dict[str, str] | None = None) -> list[dict]:
    """One result per expectation of ``standard``, in its order (the module's docstring). ``skip``: why none runs (an
    integrity problem, say), each then ``not_run`` with that reason. ``before``: the review's snapshot of the tree,
    whose digest is ``artifact``, to which each run on the patch is bound."""
    rooted = {"roots": tuple(roots)} if roots else {}
    binding = {"artefact": before} if before is not None else {}
    found: list[dict] = []
    prepared = None
    if not skip:
        prepared = verification_templates.prepare(root, change, [one.instance for one in standard.expectations],
                                                  deadline=deadline, roots=tuple(roots))
    for one in standard.expectations:
        ident = one.expectation_id
        result = {"expectation_id": ident, "question_sha256": one.question_sha256, "answer_sha256": one.answer_sha256,
                  "option": one.option, "answered_by": one.answered_by, "requirement_id": one.requirement_id,
                  "scope": one.scope, "template": "example.v1", "provenance": one.provenance,
                  "check_sha256": one.check_sha256, "compiled": COMPILED, "runs": {}, "killed": False,
                  "bound": before is not None}
        found.append(result)
        if skip:
            result.update(result=NOT_RUN, detail=skip)
            continue
        check = next((c for c in prepared.checks if c.id == ident), None)
        if check is None:
            why = next((a.get("reason", "") for a in prepared.activities if a.get("check") == ident),
                       "G6 could not compile it")
            result.update(result=NOT_RUN, detail=f"it could not be applied to this tree: {why}")
            continue
        placed = prepared.placed[ident]
        node = placed
        result.update(file=placed, node=node, source_sha256=hashlib.sha256(check.source.encode("utf-8")).hexdigest())

        def read(at_base: bool) -> dict:
            ran = vs._run_check(change, placed, check.source, node, at_base=at_base,
                                timeout_s=vs._bound(deadline - vs._clock()), **rooted, **({} if at_base else binding))
            if ran.get(vs.NOT_THE_ARTEFACT_KEY):   # the executor refused a copy that is not the tree under review
                result["tree_changed"] = ran.get("said", vs.NOT_THE_ARTEFACT)
            return normal_reading(vs._guarded(change, ran, vs.reading(ran, node)))

        try:
            if deadline - vs._clock() < vs.MIN_RUN_S:
                result.update(result=NOT_RUN, detail=vs.OUT_OF_TIME)
                continue
            on_patch = read(at_base=False)
            on_the_patch = [on_patch]   # every run on the patch, in the order it ran: each one's result is read
            result["runs"]["patch"] = vs._summary(on_patch)
            result["result"], result["detail"] = _result(on_patch)
            if deadline - vs._clock() >= vs.MIN_RUN_S:
                at_base = read(at_base=True)
                result["runs"]["base"] = vs._summary(at_base)
                # the one rule for the control: the check ran to a failure at the base commit (a collection that
                # failed outside the repository's own code is no failure of it); the issuer's adapter reads this
                killed = result["killed"] = vs._to_a_result(at_base) and at_base["result"] == "failed"
                if result["result"] == PASSED and killed and deadline - vs._clock() >= vs.MIN_RUN_S:
                    again = read(at_base=False)
                    on_the_patch.append(again)
                    result["runs"]["again"] = vs._summary(again)
            result["result"], result["detail"] = patch_result(on_the_patch)
            failing = next((found for found in on_the_patch if _failed(found)), None)
            if failing is not None:   # any run on the patch that observed the case fail: a defect
                result["result"], result["detail"] = FAILED, failing.get("detail", "")
                result["reproduction"] = {"check": ident, "file": placed, "text": check.source,
                                          "sha256": result["source_sha256"], "node": node,
                                          **vs.reproduce(node, failing), "how": vs.HOW, "caution": CAUTION,
                                          "patched_tree_digest": failing.get("tree_digest", ""),
                                          "artifact_digest": artifact, "base_commit": change.base_commit,
                                          "template": "example.v1", "compiled": COMPILED,
                                          "on_patch": f"{failing['result']}: {failing.get('detail', '')}"}
        except (component_process.ExecutionInterrupted, component_process.ContainmentFailure, repo_tests.RepoRefused):
            raise   # an operator stop, unconfirmed cleanup or a refusal (no boundary) still reaches the door
        except Exception as exc:   # G6's own work: this expectation is not enforced, and says why
            result.update(result=NOT_RUN, detail=vs.stopped(exc))
    return found


__all__ = ["CAUTION", "COMPILED", "FAILED", "NOT_RUN", "PASSED", "patch_result", "run"]
