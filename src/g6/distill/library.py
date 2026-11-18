# SPDX-License-Identifier: Apache-2.0
"""Read-only views of the latest gate-supplied library snapshots."""
from __future__ import annotations

from .ledger import Stores
from .records import LibraryEntry, MethodContract, Status


class Library:
    def list(self) -> list[LibraryEntry]:
        current = {}
        for entry in Stores().for_model(LibraryEntry).list():
            old = current.get(entry.candidate_id)
            if old is not None and entry.version <= old.version:
                from g6.core.ledger import LedgerCorrupt
                raise LedgerCorrupt("library versions must increase")
            current[entry.candidate_id] = entry
        return list(current.values())

    def get(self, entry_id: str) -> LibraryEntry | None:
        return next((entry for entry in self.list() if entry.entry_id == entry_id), None)

    def by_status(self, status: Status) -> list[LibraryEntry]:
        if status not in {"probation", "admitted", "demoted", "withdrawn", "superseded"}:
            raise ValueError("unknown library status")
        return [entry for entry in self.list() if entry.status == status]

    def by_scope(self, *, family: str, language: str | None = None,
                 framework: str | None = None) -> list[LibraryEntry]:
        result = []
        for entry in self.list():
            contract = Stores().for_model(MethodContract).get(entry.contract_sha256)
            if (entry.family == family and (language is None or language in contract.languages)
                    and (framework is None or framework in contract.frameworks)):
                result.append(entry)
        return result
