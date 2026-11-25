# SPDX-License-Identifier: Apache-2.0
"""Authority-bounded library observations; detector silence is never a pass.

Uses the same confined executor as acceptance, with no admission or field labels.
Only current in-scope entries run. A historical ID cannot revive old authority.
"""
from __future__ import annotations

from types import SimpleNamespace
import threading
import time

from g6.core.ledger import ChainLedger, LedgerError, digest
from g6.core.storage import state_path
from g6.distill.ledger import PartialAuthorityWrite, Stores, append_retained, failure_writer, retain_before_write
from g6.distill.library import Library
from g6.distill.records import LibraryEntry, VerifierCandidate
from g6.distill.runtime_state import StatusChanged, require_current, status_snapshot

_FIRE_MUTEX = threading.RLock()


def observation(entry, verdict: str) -> dict:
    if verdict not in {"concern", "no_concern", "abstain"}:
        verdict = "abstain"
    defect = verdict == "concern" and entry.authority == "A2" and entry.status == "admitted"
    precision = entry.evidence.precision.lo95
    count = entry.evidence.n_units
    sample = str(count) if count is not None else 'unknown'
    return {"entry_id": entry.entry_id, "candidate_id": entry.candidate_id, "provenance": "library",
            "authority": entry.authority, "status": entry.status, "verdict": verdict,
            "precision": {"lo95": precision}, "held_out_n": count, "passes_requirement": False,
            "outcome": "defect_detected" if defect else "insufficient_evidence" if verdict == "concern" else "not_a_pass",
            "reason": f"library:{entry.entry_id}, precision >= {precision:g} on {sample} held-out units; "
                      f"{entry.authority} {verdict}; not a pass"}


def record_fire(found: dict, before: dict, after: dict, snapshot) -> None:
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
    recorded = False
    try:
        with _FIRE_MUTEX, failure_writer():
            require_current(snapshot)
            path.parent.mkdir(parents=True, exist_ok=True)
            lock = path.with_suffix(".lock")
            # The sentinel is also undone if the process dies before its finally.
            # An existing sentinel remains a refusal and is never taken over.
            if not lock.exists():
                retain_before_write(lock, after=None)
            try:
                stream = lock.open("x", encoding="utf-8", newline="\n")
            except FileExistsError:
                raise LedgerError("field writer busy or recovery required") from None
            try:
                with stream:
                    ledger = ChainLedger(path)
                    if not any(row["id"] == ident for row in ledger.entries()):
                        append_retained(ledger, "field_fire", ident, body)
            finally:
                lock.unlink()
            # Capture only this fire, after its status recheck and write, before
            # transaction cleanup. Entry recovery cannot supply this result.
            recorded = True
    except PartialAuthorityWrite as error:
        if not error.publication_committed or not recorded:
            raise
        found.update(field_recorded=True, publication_committed=True, code=error.code,
                     durable_state=error.durable_state,
                     field_note='Field fire committed; intent cleanup pending at publication.')


def _unavailable(error: PartialAuthorityWrite) -> dict:
    return {'library_unavailable': True, 'code': error.code, 'durable_state': error.durable_state,
            'publication_committed': error.publication_committed,
            **({'earlier_publication_committed': True} if error.earlier_publication_committed else {}),
            'authority': 'A0', 'verdict': 'abstain', 'outcome': 'insufficient_evidence',
            'field_recorded': False,
            'reason': 'Library unavailable; pending undo or intent cleanup in authority_intent.json; ' +
                      ('earlier publication committed; its cleanup is incomplete; this request made no change; '
                       if error.earlier_publication_committed else '') +
                      '; '.join(error.durable_state)}


def check(before: dict[str, str], after: dict[str, str], *, entry_id: str = "", tests=()) -> list[dict]:
    findings = []
    try:
        return _check(before, after, entry_id=entry_id, tests=tests, findings=findings)
    except PartialAuthorityWrite as error:
        # Unavailable reads grant no authority. They cannot disown a fire this
        # review already committed, even when cleanup still fails on that read.
        committed = [found for found in findings if found.get('field_recorded')
                     and found.get('publication_committed')]
        if committed:
            for found in committed:
                found.update(authority='A0', verdict='abstain', outcome='insufficient_evidence',
                             library_unavailable_for_further_application=True, code=error.code,
                             durable_state=error.durable_state,
                             field_note='Field fire committed; intent cleanup pending; library unavailable '
                                        'for further application.',
                             reason='Library unavailable for further application; field fire committed; '
                                    'intent cleanup pending in authority_intent.json; ' +
                                    '; '.join(error.durable_state))
            return committed
        return [_unavailable(error)]


def _check(before: dict[str, str], after: dict[str, str], *, entry_id: str = "", tests=(), findings) -> list[dict]:
    from g6.distill.acceptance_gate import _InactiveMethod, _run
    from g6.distill.facts import source_mapping
    from g6.workforce.repo_tests import is_test_related
    from g6.distill.records import Sha256
    from pydantic import TypeAdapter

    before, after = source_mapping(before), source_mapping(after)
    if not isinstance(entry_id, str) or not isinstance(tests, (list, tuple)) or any(
            not isinstance(path, str) or not path for path in tests):
        raise ValueError('invalid library application input')
    if entry_id:
        TypeAdapter(Sha256).validate_python(entry_id)
    entries = status_snapshot()
    selected = entries
    if entry_id:
        historical = Stores().for_model(LibraryEntry).get(entry_id)
        selected = [e for e in entries if e.candidate_id == historical.candidate_id]
    retention_failed = []
    for entry in selected:
        if entry.status not in {"probation", "admitted", "demoted"} or entry.authority not in {"A1", "A2"}:
            continue
        candidate = Stores().for_model(VerifierCandidate).get(entry.candidate_id)
        contract = candidate.contract
        if entry.family != "test_weakening" or "python" not in contract.languages \
                or (contract.frameworks and "pytest" not in contract.frameworks):
            continue
        verdicts = []
        changed = None
        began = time.monotonic()
        for path in sorted(before.keys() | after.keys()):
            if not path.endswith(".py") or not (is_test_related(path) or path in tests) \
                    or before.get(path) == after.get(path):
                continue
            case = SimpleNamespace(before_tests=before.get(path, ""), after_tests=after.get(path, ""))
            try:
                verdicts.append(_run(candidate, case, entries))
            except StatusChanged as error:
                changed = error
                break
            except _InactiveMethod:
                # An inactive alias has no application observation or authority.
                verdicts.clear()
                break
            except (ValueError, KeyError, LedgerError):
                verdicts.append("abstain")
        if verdicts or changed:
            # Authority is the library's at reporting time, never the snapshot taken before the run: a demotion
            # or withdrawal that completed meanwhile applies, and a method no longer in the library reports nothing.
            with failure_writer(reading=True):
                try:
                    current = require_current(entries)
                except StatusChanged as error:
                    changed = error
                    current = Library().list()
                entry = next((e for e in current if e.candidate_id == entry.candidate_id), None)
                if entry is None:
                    continue
                verdict = ('abstain' if changed else 'concern' if 'concern' in verdicts else
                           'abstain' if 'abstain' in verdicts else 'no_concern')
                found = observation(entry, verdict)
                if changed:
                    found.update(authority='A0', verdict='abstain', outcome='insufficient_evidence',
                                 code=changed.code, reason=str(changed))
            found["seconds"] = time.monotonic() - began
            if verdict == "concern":
                try:
                    record_fire(found, before, after, entries)
                    found["field_recorded"] = True
                    found['publication_committed'] = True
                except StatusChanged as error:
                    found.update(authority='A0', verdict='abstain', outcome='insufficient_evidence',
                                 code=error.code, reason=str(error), field_recorded=False)
                except PartialAuthorityWrite as error:
                    if error.publication_committed or error.recovering:
                        raise
                    found.update(field_recorded=False, code=error.code,
                                 durable_state=error.durable_state,
                                 field_note='Field retention recovery incomplete; inspect the named durable state.')
                    retention_failed.append(found)
                except (LedgerError, OSError):
                    found["field_recorded"] = False
                    found["field_note"] = "The unlabelled field fire could not be retained; no field evidence was added."
            findings.append(found)
    with failure_writer(reading=True):
        try:
            require_current(entries)
        except StatusChanged as error:
            for found in findings:
                found.update(authority='A0', verdict='abstain', outcome='insufficient_evidence',
                             code=error.code, reason=str(error))
    for found in retention_failed:
        if found.get('code') == PartialAuthorityWrite.code:
            found.pop('code', None)
        found.pop('durable_state', None)
        found['field_note'] = 'The unlabelled field fire could not be retained; no field evidence was added.'
    return findings


def apply_review(before: dict, after: dict, reasons: list, runs: list) -> None:
    try:
        # The review's related_texts already established these are test inputs,
        # including nonstandard filenames explicitly named by the repository's tests.
        findings = check(before, after, tests=tuple(before.keys() | after.keys()))
    except PartialAuthorityWrite as error:
        findings = [_unavailable(error)]
    except (ValueError, KeyError, LedgerError):
        reasons.append({"stage": "evidence", "code": "library_unavailable", "detail": "Library records could not be checked.",
                        "provenance": "library"})
        return
    unavailable = next((found for found in findings if found.get('library_unavailable')), None)
    if unavailable:
        reasons.append({'stage': 'evidence', 'code': 'library_unavailable', 'detail': unavailable['reason'],
                        'provenance': 'library'})
        return
    unavailable_after_fire = next((found for found in findings
                                   if found.get('library_unavailable_for_further_application')), None)
    if unavailable_after_fire:
        reasons.append({'stage': 'evidence', 'code': 'library_unavailable',
                        'detail': unavailable_after_fire['reason'], 'provenance': 'library'})
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
    unavailable = None
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
            unavailable = next((found for found in findings if found.get('library_unavailable')), None)
            if unavailable:
                findings = []
    except PartialAuthorityWrite as error:
        unavailable = _unavailable(error)
        findings = []
    except (ValueError, KeyError, LedgerError, UnicodeError):
        findings = []
    if not findings and unavailable is None:
        # Validation can prevent check() from reaching any library read. An
        # unavailable library must still be named by the learned probe.
        try:
            status_snapshot()
        except PartialAuthorityWrite as error:
            unavailable = _unavailable(error)
        except (ValueError, KeyError, LedgerError, UnicodeError):
            pass
    defects = [f for f in findings if f["outcome"] == "defect_detected"]
    return {"status": "fail" if defects else "not_run", "authority": "A2" if defects else "A0",
            "reason": unavailable['reason'] if unavailable else (
                "; ".join(f["reason"] for f in findings) or "No in-scope library method ran; not a pass."),
            "library": findings, "counterexamples": defects, "functions": [], "guard_id": entry_id,
            **({'code': 'library_unavailable'} if unavailable else {})}
