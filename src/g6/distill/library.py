# SPDX-License-Identifier: Apache-2.0
"""Read-only views of the latest gate-supplied library snapshots."""
from __future__ import annotations

from .ledger import Stores
from .records import Concept, FailureRecord, LibraryEntry, MethodContract, Status, VerifierCandidate

_PRIVATE_DECISION = '<private decision>'


def public_decision(decision: dict) -> dict:
    """Owner decisions disclose outcomes and measurements, never dialog text.

    An allowlist also keeps future private fields from becoming public by default.
    Local acceptance journal and library rows keep the original decision.
    """
    fields = {'verdict', 'candidate_id', 'report_sha256', 'entry_id', 'p_value',
              'minimum_detectable_gain', 'p_value_upper_bound', 'minimum_detectable_precision_difference'}
    return {**{key: value for key, value in decision.items() if key in fields},
            'reasons': (_PRIVATE_DECISION,)}


def public_entry(entry: LibraryEntry) -> dict:
    """One outward view; local custody retains the full decision record."""
    data = entry.model_dump(mode='json')
    data.pop('inactive_cases', None)
    data['decided_by']['person'] = None
    data['reason'] = _PRIVATE_DECISION
    return data


class Library:
    def export(self, entry_id: str) -> dict:
        """Repository export: every training ancestor must be G6 public archive.

        User and unknown origins remain local, including through reuse/composition.
        Raw failure sources and evaluator custody are never part of this bundle.
        """
        stores, entries, candidates, concepts, contracts = Stores(), {}, {}, {}, {}
        visiting = set()
        current = {e.candidate_id: e for e in self.list()}
        def visit(ident):
            if ident in visiting:
                raise ValueError('cyclic export provenance')
            if ident in entries:
                return
            visiting.add(ident)
            entry = stores.for_model(LibraryEntry).get(ident)
            latest = current.get(entry.candidate_id)
            if entry.status != 'admitted' or latest is None or latest.status != 'admitted':
                raise ValueError('only admitted methods may leave local custody')
            candidate = stores.for_model(VerifierCandidate).get(entry.candidate_id)
            concept = stores.for_model(Concept).get(candidate.concept_id)
            failures = [stores.for_model(FailureRecord).get(i) for i in concept.from_failures]
            training = {f.case_ref for f in failures}
            # Candidate training can name additional failures beyond the concept.
            for ref in set(candidate.training_case_ids) - training:
                matching = [f for f in stores.for_model(FailureRecord).list() if f.case_ref == ref]
                if not matching:
                    raise ValueError('unknown training provenance stays local')
                failures.extend(matching)
            if not failures or any(f.provenance.get('scope') != 'g6_public_archive'
                or f.observed_by != 'independent' or f.split != 'training' for f in failures):
                raise ValueError('user or unknown provenance stays local')
            parents = set(entry.parents) | set(candidate.lineage)
            if candidate.form == 'reuse':
                parents.add(candidate.body)
            elif candidate.form == 'composition':
                parents.update(candidate.body.entries)
            for parent in sorted(parents):
                visit(parent)
            entries[ident], candidates[candidate.candidate_id] = entry, candidate
            concepts[concept.concept_id], contracts[candidate.contract.content_id] = concept, candidate.contract
            visiting.remove(ident)
        visit(entry_id)
        return {'scope': 'g6_public_archive', **{name: [public_entry(value) if isinstance(value, LibraryEntry)
            else value.model_dump(mode='json') for value in values.values()]
            for name, values in (('entries', entries), ('candidates', candidates), ('concepts', concepts),
                                 ('contracts', contracts))}}

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
        if status not in {"probation", "admitted", "held", "demoted", "withdrawn", "superseded"}:
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
