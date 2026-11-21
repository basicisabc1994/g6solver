# SPDX-License-Identifier: Apache-2.0
"""Class-removal decisions and append-only logs over the records slice's Stores.

This records an independent gate's receipts; it never executes a test, edits code,
simulates an action, or grants library authority. The runner must perform simulation,
review and admission before submitting its decision here. One serial writer across
these ledgers is required. Reconcile/replay heals interrupted derived-log writes.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from g6.core.ledger import LedgerError
from .intake import class_key, earlier
from .ledger import Stores
from .records import CapabilityExtension, FailureMode, FailureRecord, LibraryEntry, Rectification


def _key(record: FailureRecord) -> str | None:
    """The record's class, or None for a malformed record (an unnamed other) that no class can hold."""
    try:
        return class_key(record)
    except LedgerError:
        return None


class Rectifier:
    def __init__(self, stores: Stores | None = None):
        self.stores = stores or Stores()

    def _logs(self):
        # Audit all logs before any write, even if this operation only needs one.
        return {cls: self.stores.for_model(cls).list() for cls in
                (FailureRecord, FailureMode, Rectification, CapabilityExtension, LibraryEntry)}

    def _append_once(self, value):
        store = self.stores.for_model(type(value))
        if not any(old.content_id == value.content_id for old in store.list()):
            store.put(value)
        return value

    @staticmethod
    def _replace(rect: Rectification, **updates) -> Rectification:
        return Rectification(**(rect.model_dump(mode="json", exclude={"rect_id"}) | updates))

    def _pinning(self, rect: Rectification, *, require_current: bool = True):
        if rect.pinning is None or not rect.removal or not rect.removal.strip():
            raise LedgerError("a rectification requires a removal and a pinning test")
        ref = rect.pinning.test_ref
        if ref.startswith("library:"):
            try:
                entry = self.stores.for_model(LibraryEntry).get(ref.removeprefix("library:"))
            except KeyError:
                raise LedgerError("pinning library check reference is missing") from None
            history = self.stores.for_model(LibraryEntry).list()
            latest = [row for row in history if row.candidate_id == entry.candidate_id][-1]
            if ((require_current and entry != latest) or entry.family != "g6_self"
                    or entry.status != "admitted" or entry.authority != "A2"):
                raise LedgerError("pinning library check must be a current admitted g6_self method")
        else:
            path = PurePosixPath(ref.split("::", 1)[0])
            if ("\\" in ref or path.is_absolute() or ".." in path.parts or not path.parts
                    or path.parts[0] != "tests" or path.suffix != ".py"):
                raise LedgerError("pinning test must name a repository test or an admitted g6_self library check")

    def submit(self, failure_id: str, rect: Rectification) -> Rectification:
        """Refuse instance repairs of known classes; incomplete replays remain open.

        All known instances of the class, across loops, must fail the same check at
        base. Merely naming a test never establishes removal. Evidence is declared
        by the gate caller; this layer does not authenticate the caller's statements.
        """
        rect = Rectification.model_validate_json(rect.model_dump_json())
        logs = self._logs()
        failures = logs[FailureRecord]
        failure = next((row for row in failures if row.record_id == failure_id), None)
        if failure is None:
            raise LedgerError("unknown failure reference")
        key = class_key(failure)
        if rect.failure_mode != key:
            raise LedgerError("rectification names a different failure mode/class")
        same = [row for row in failures if _key(row) == key]
        self._pinning(rect, require_current=rect.outcome != "failed")
        known = {row.record_id for row in same}
        replayed = set(rect.pinning.replayed)
        if not replayed <= known:
            raise LedgerError("replay references an unknown failure or a different class")
        # The store owns the class/instance rule, including attempts targeting an
        # older record after a later recurrence. Both entry points share one floor.
        if rect.class_removed and (replayed != known or not rect.pinning.fixed_passed):
            rect = self._replace(rect, class_removed=False, outcome="open")
        elif rect.outcome == "held" and not rect.pinning.fixed_passed:
            rect = self._replace(rect, class_removed=False, outcome="open")
        self._append_once(rect)
        self.reconcile()
        return rect

    def _state(self, failures, rects):
        failures = [row for row in failures if _key(row) is not None]
        grouped = defaultdict(list)
        for row in failures:
            grouped[class_key(row)].append(row)
        states = {}
        for key, rows in grouped.items():
            mine = [rect for rect in rects if rect.failure_mode == key]
            removed = [rect for rect in mine if rect.class_removed and rect.outcome == "held" and rect.pinning]
            last_removal = removed[-1] if removed else None
            recurrence = ([row for row in rows if row.record_id not in last_removal.pinning.replayed]
                          if last_removal else [])
            active = last_removal is not None and not recurrence
            # Count recurrences since the first removal cumulatively, even after
            # another removal succeeds. Re-closing must not erase this history.
            total = sum(row.record_id not in removed[0].pinning.replayed for row in rows) if removed else 0
            states[key] = FailureMode(mode_code=key, first_seen=rows[0].observed_at, occurrences=len(rows),
                met_before=any(row.met_before or earlier([f for f in failures if f != row], row) for row in rows),
                removing_check=last_removal.rect_id if active else None, recurrences_after_removal=total)
        return states

    def _demote(self, rect: Rectification, failures: list[FailureRecord]) -> None:
        ref = rect.pinning.test_ref
        if not ref.startswith("library:"):
            return
        store = self.stores.for_model(LibraryEntry)
        pinned = store.get(ref.removeprefix("library:"))
        current = [row for row in store.list() if row.candidate_id == pinned.candidate_id][-1]
        if current.authority == "A0":
            return
        at = failures[-1].observed_at
        body = current.model_dump(mode="json", exclude={"entry_id"})
        # Down only. Retain the prior certificate reference as provenance; this
        # does not issue a certificate or grant a fresh authority level.
        self._append_once(LibraryEntry(**(body | dict(version=current.version + 1, status="demoted", authority="A0",
            changed_at=at, reason=f"removing check recurred: {rect.rect_id}",
            decided_by=dict(gate_version="distill.findings.recurrence.v1", person=None)))))

    def reconcile(self) -> dict:
        """Append derived snapshots; never rewrite a prior decision or consume an event twice.

        A malformed old record (an unnamed other class) is skipped and reported, never fatal to the whole log."""
        logs = self._logs()
        malformed = [row.record_id for row in logs[FailureRecord] if _key(row) is None]
        states = self._state(logs[FailureRecord], logs[Rectification])
        for key, state in states.items():
            rects = [row for row in logs[Rectification] if row.failure_mode == key]
            removed = [row for row in rects if row.class_removed and row.outcome == "held" and row.pinning]
            if removed:
                last = removed[-1]
                recurrence = [row for row in logs[FailureRecord] if _key(row) == key
                              and row.record_id not in last.pinning.replayed]
                if recurrence:
                    # Demote before writing the failed decision; retry is safe if
                    # either append is interrupted.
                    self._demote(last, recurrence)
                    self._append_once(self._replace(last, class_removed=False, outcome="failed"))
            old = [row for row in logs[FailureMode] if row.mode_code == key]
            if not old or old[-1] != state:
                self._append_once(state)
        return {"malformed": malformed}

    def mode(self, code: str) -> FailureMode | None:
        """Fresh derived state; read-only, including after an interrupted reconcile."""
        logs = self._logs()
        return self._state(logs[FailureRecord], logs[Rectification]).get(code)

    def extend(self, extension: CapabilityExtension) -> CapabilityExtension:
        extension = CapabilityExtension.model_validate_json(extension.model_dump_json())
        logs = self._logs()
        gap = next((row for row in logs[FailureRecord] if row.record_id == extension.gap_record), None)
        if gap is None or gap.kind not in {"capability_gap", "user_request"}:
            raise LedgerError("extension must reference a capability gap or user request")
        return self._append_once(extension)
