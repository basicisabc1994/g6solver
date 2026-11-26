# SPDX-License-Identifier: Apache-2.0
"""Authority-bounded library observations; detector silence is never a pass.

Uses the same confined executor as acceptance, with no admission or field labels.
Only current in-scope entries run. A historical ID cannot revive old authority.
"""
from __future__ import annotations

from types import SimpleNamespace
import threading
import time

from g6.core.component_process import ContainmentFailure, ExecutionInterrupted
from g6.core.ledger import ChainLedger, LedgerError, digest
from g6.core.storage import state_path
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.distill.records import LibraryEntry, VerifierCandidate

_FIRE_MUTEX = threading.RLock()


def observation(entry, verdict: str) -> dict:
    if verdict not in {"concern", "no_concern", "abstain"}:
        verdict = "abstain"
    defect = verdict == "concern" and entry.authority == "A2" and entry.status == "admitted"
    precision = entry.evidence.precision.lo95
    count = entry.evidence.n_pos + entry.evidence.n_neg
    return {"entry_id": entry.entry_id, "candidate_id": entry.candidate_id, "provenance": "library",
            "authority": entry.authority, "status": entry.status, "verdict": verdict,
            "precision": {"lo95": precision}, "held_out_n": count, "passes_requirement": False,
            "outcome": "defect_detected" if defect else "insufficient_evidence" if verdict == "concern" else "not_a_pass",
            "reason": f"library:{entry.entry_id}, precision >= {precision:g} on {count} held-out; "
                      f"{entry.authority} {verdict}; not a pass"}


def record_fire(found: dict, before: dict, after: dict) -> None:
    """Unlabelled host observations, outside independent evaluator custody.

    These rows can inform a later investigation; they cannot satisfy the gate's
    independently labelled field bar. Never write the gate's held_out/field path.
    """
    if found["verdict"] != "concern":
        return
    body = {"candidate_id": found["candidate_id"], "entry_id": found["entry_id"],
            "before_sha256": digest(before), "after_sha256": digest(after),
            "authority": found["authority"], "label": "unlabelled", "source": "review"}
    ident = digest(body)
    path = state_path("distill", "field_fires_ledger.jsonl")
    with _FIRE_MUTEX:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(".lock")
        try:
            stream = lock.open("x", encoding="utf-8", newline="\n")
        except FileExistsError:
            raise LedgerError("field writer busy or recovery required") from None
        try:
            with stream:
                ledger = ChainLedger(path)
                if not any(row["id"] == ident for row in ledger.entries()):
                    ledger.append("field_fire", ident, body)
        finally:
            lock.unlink()


def check(before: dict[str, str], after: dict[str, str], *, entry_id: str = "", tests=()) -> list[dict]:
    from g6.distill.acceptance_gate import _run
    from g6.workforce.repo_tests import is_test_related

    entries = Library().list()
    selected = entries
    if entry_id:
        historical = Stores().for_model(LibraryEntry).get(entry_id)
        selected = [e for e in entries if e.candidate_id == historical.candidate_id]
    findings = []
    for entry in selected:
        if entry.status not in {"probation", "admitted", "demoted"} or entry.authority not in {"A1", "A2"}:
            continue
        candidate = Stores().for_model(VerifierCandidate).get(entry.candidate_id)
        contract = candidate.contract
        if entry.family != "test_weakening" or "python" not in contract.languages \
                or (contract.frameworks and "pytest" not in contract.frameworks):
            continue
        verdicts = []
        began = time.monotonic()
        for path in sorted(before.keys() | after.keys()):
            if not path.endswith(".py") or not (is_test_related(path) or path in tests) \
                    or before.get(path) == after.get(path):
                continue
            case = SimpleNamespace(before_tests=before.get(path, ""), after_tests=after.get(path, ""))
            try:
                verdicts.append(_run(candidate, case, entries))
            except (ValueError, KeyError, LedgerError):
                verdicts.append("abstain")
        if verdicts:
            verdict = "concern" if "concern" in verdicts else "abstain" if "abstain" in verdicts else "no_concern"
            found = observation(entry, verdict)
            found["seconds"] = time.monotonic() - began
            if verdict == "concern":
                try:
                    record_fire(found, before, after)
                    found["field_recorded"] = True
                except (ExecutionInterrupted, ContainmentFailure):
                    raise
                except (LedgerError, OSError):
                    found["field_recorded"] = False
                    found["field_note"] = "The unlabelled field fire could not be retained; no field evidence was added."
            findings.append(found)
    return findings


def apply_review(before: dict, after: dict, reasons: list, runs: list) -> None:
    try:
        # The review's related_texts already established these are test inputs,
        # including nonstandard filenames explicitly named by the repository's tests.
        findings = check(before, after, tests=tuple(before.keys() | after.keys()))
    except (ValueError, KeyError, LedgerError):
        reasons.append({"stage": "evidence", "code": "library_unavailable", "detail": "Library records could not be checked.",
                        "provenance": "library"})
        return
    for found in findings:
        runs.append({"tier": 2, "check": "library method", "tests": [], "seconds": found.get("seconds", 0.0),
                     "passed": 0, "failed": 0, "skipped": 0, "said": found["reason"], "receipt": "",
                     "command": "library:" + found["entry_id"], "verdict": found["verdict"],
                     "provenance": "library", "library": found})
        if found["verdict"] == "concern":
            defect = found["outcome"] == "defect_detected"
            reasons.append({"stage": "tier_2" if defect else "evidence",
                            "code": "library_defect_detected" if defect else "library_concern",
                            "detail": found["reason"], "provenance": "library", "library": found})


def check_learned(params: dict, *, venture: str | None = None) -> dict:
    from g6.workforce import bench

    scope = params["regression"]["learned"]
    entry_id = scope.get("entry_id", scope.get("guard_id", "")) if isinstance(scope, dict) else ""
    findings = []
    try:
        if entry_id:
            # Reuse the bench's repository boundary and file validation. Read only named paths,
            # checking each ancestor for links; no walk and no arbitrary caller source maps.
            source = bench._repo(params.get("repo"), venture)
            after = bench._files(params.get("files") or {})
            before = {}
            if source is None or bench._linked(source):
                raise ValueError("no repository baseline")
            for name in after:
                path = source
                for part in bench._inside(name).parts:
                    path = path / part
                    if bench._linked(path):
                        raise ValueError("linked baseline")
                try:
                    before[name] = path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    before[name] = ""
            findings = check(before, after, entry_id=entry_id)
    except (ValueError, KeyError, LedgerError, UnicodeError):
        findings = []
    defects = [f for f in findings if f["outcome"] == "defect_detected"]
    return {"status": "fail" if defects else "not_run", "authority": "A2" if defects else "A0",
            "reason": "; ".join(f["reason"] for f in findings) or "No in-scope library method ran; not a pass.",
            "library": findings, "counterexamples": defects, "functions": [], "guard_id": entry_id}
