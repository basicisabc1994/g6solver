# SPDX-License-Identifier: Apache-2.0
"""Host integration of installed slices; no ambient execution capabilities.

The host injects its measured self-model context, model caller and runner. Public
requests cannot supply those capabilities, independence or admission receipts.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
import time

from g6.core.guidance_builders import blocked_reply
from g6.core.ledger import digest
from g6.core.storage import state_path
from g6.distill.ledger import Stores, failure_writer
from g6.distill.records import CapabilityExtension, Concept, Evidence, FailureRecord

#: How long a measured reading of G6's own import graph and protected set stays fresh in this process.
LOCAL_TTL_S = 3600.
CASE_FIELDS = ('unit_id', 'provenance', 'files', 'target', 'changed_lines', 'before_tests', 'after_tests', 'known_wrong')
NEEDS_CASE = ('Distillation learns only from independent training failures. Record the failure with '
              'g6_failure_record and a case_json object holding unit_id, provenance, files, target, changed_lines, '
              'before_tests, after_tests and known_wrong; g6_distill_start then labels it by its own confined '
              'oracle run, and only a case the oracle labels weakened becomes a training failure.')


def refused(code, reason):
    return blocked_reply(f'[{code}] {reason}')


def _oracle_case(failure):
    """The executable case a self-observed record carries, or None. The oracle, not the caller, labels it."""
    from g6.distill.held_out.case_gate import CaseRecord
    case = failure.provenance.get('case')
    if failure.observed_by != 'self' or not isinstance(case, dict) or not set(CASE_FIELDS) <= case.keys():
        return None
    try:
        return CaseRecord.model_validate({key: case[key] for key in CASE_FIELDS})
    except ValueError:
        return None


def _unseen(case):
    """An explicitly unseen case (the selection cohort, per case_gate.unit_split) never becomes training data."""
    from g6.distill.held_out.case_gate import unit_split
    return unit_split(case.unit_id, case.provenance.cohort, 'intake') == 'unseen'


def _unseen_units(records):
    """Custody is per unit across all families and history, never just the latest header."""
    from g6.distill.intake import unit_cohort
    units = set()
    for record in records:
        unit, cohort = unit_cohort(record)
        if unit and cohort in {'selection', 'held_out', 'unseen'}:
            units.add(unit)
    return units


def _record_unit(record, records):
    from g6.distill.intake import unit_cohort
    indexed = {row.record_id: row for row in records}
    seen = set()
    while record.record_id not in seen:
        seen.add(record.record_id)
        unit, _ = unit_cohort(record)
        if unit:
            return unit
        source = indexed.get(record.provenance.get('labelled_from'))
        if source is None:
            return None
        record = source
    return None


def _labelled(failure, case, result):
    """An independent training record whose label is G6's own executed oracle result, never the caller's word.

    Its identity depends only on the source record, the case and the oracle policy: no clock, no run detail."""
    from g6.distill.held_out.oracle import TEST_PATH
    if result.label != 'positive' or result.case_id != case.case_id or _unseen(case):
        raise ValueError('only a positive oracle label of a seen case becomes training data')
    label = {'source_record': failure.record_id, 'case_id': case.case_id, 'label': result.label,
             'policy_sha256': result.policy_sha256}
    return FailureRecord(kind=failure.kind, family=failure.family, source='experiment', observed_by='independent',
        case_ref=case.case_id, split='training',
        evidence=[Evidence(kind='oracle', digest=digest(label), receipt=f'oracle:{result.label}')],
        finding_class=failure.finding_class, met_before=[], g6_commit=failure.g6_commit,
        observed_at=failure.observed_at,
        provenance={'case': {'before': {TEST_PATH: case.before_tests}, 'after': {TEST_PATH: case.after_tests}},
                    'labelled_from': failure.record_id, 'cohort': case.provenance.cohort, 'unit_id': case.unit_id,
                    'oracle_policy_sha256': result.policy_sha256, 'intake': 'oracle_label',
                    'scope': 'client_request', **({'other': failure.provenance['other']}
                                                  if 'other' in failure.provenance else {})})


def _derived(family, source_id):
    """The independent training record already labelled from ``source_id``, if any (read at write time)."""
    return next((f for f in Stores().for_model(FailureRecord).list()
                 if f.family == family and f.observed_by == 'independent' and f.split == 'training'
                 and f.provenance.get('labelled_from') == source_id), None)


def _put_once(record):
    store = Stores().for_model(type(record))
    try:
        old = store.get(record.content_id)
    except KeyError:
        store.put(record)
    else:
        if old != record:
            raise ValueError('persisted record differs from its identity')


class Service:
    def __init__(self, *, jobs=None, synthesizer=None, runners=None, snapshot=None,
                 simulation_inputs=None, registration='', now=time.time, config=None):
        self.jobs, self.synthesizer = jobs, synthesizer
        self.runners = {} if runners is None else dict(runners)
        self.snapshot, self.simulation_inputs = snapshot, simulation_inputs
        self.registration, self.now = registration, now
        self._graph = None
        self.config = config if config is not None else Path(__file__).parents[1] / 'self_training/autonomy.json'
        for folder, runner in self.runners.items():
            original = runner.stop
            runner.stop = lambda prior=original, key=folder: prior() or self._stop_path(key).exists()

    def _local_snapshot(self, now):
        from g6.core import stop
        from g6.self_model.model import Field, NamedField, SelfModel
        from g6.self_training.autonomy_gate import authority
        def field(name, value):
            return NamedField(name=name, field=Field.measured(value, source='g6.distill.service.local_state',
                observed_at=now, ttl_s=0.))
        return SelfModel(state=(field('stop', stop.engaged() is not None), field('budget_room', 'unknown'),
                                field('isolation', 'unknown')),
                         authority=(field('level', authority(self.config).level),))

    def _relationships(self, now):
        """G6's own import graph between its packages (g6.<package>), read from the installed source."""
        from g6.self_model.simulate import Relationships
        if self._graph is None or not 0 <= now - self._graph.provenance.observed_at <= LOCAL_TTL_S:
            import g6
            from g6.code.design_governance.graph import build, module_of
            root = Path(g6.__file__).resolve().parent
            files = {}
            for path in sorted(root.rglob('*.py')):
                rel = path.relative_to(root.parent).as_posix()
                if module_of(rel) is not None:
                    files[rel] = path.read_text(encoding='utf-8', errors='replace')
            grouped = build(files, 'g6').grouped(2)
            imports = tuple(sorted((importer, dependency) for importer, targets in grouped.items()
                                   for dependency in targets))
            self._graph = Relationships.measured(imports=imports, corpus=(),
                source='g6.code.design_governance.graph.build', observed_at=now, ttl_s=LOCAL_TTL_S)
        return self._graph

    def _protected_manifest(self, now):
        """The merge gate's complete protected union; an unreadable certificate leaves it unknown (all protected)."""
        from g6.distill.merge_evidence_gate import protected_set
        from g6.self_model.model import Field
        from g6.self_model.risk_gate import ProtectedManifest
        try:
            found = protected_set()
        except ValueError:
            return None
        meta = dict(source='g6.distill.merge_evidence_gate.protected_set', observed_at=now, ttl_s=LOCAL_TTL_S)
        return ProtectedManifest(patterns=Field.measured(tuple(sorted({*found.patterns, *found.bound})), **meta),
                                 complete=Field.measured(found.complete, **meta))

    def _simulation_inputs(self, now):
        # No earlier action outcomes are recorded outside a host runner, so forecasts stay unknown, never invented.
        return {'reference_actions': (), 'relationships': self._relationships(now),
                'protected_manifest': self._protected_manifest(now)}

    def self_model(self, section='', detail='compact'):
        if detail not in ('compact', 'full'):
            raise ValueError('detail must be compact or full')
        now = self.now()
        model = self.snapshot(now) if self.snapshot is not None else self._local_snapshot(now)
        reading = model.read(now=now)
        if section:
            if section not in reading:
                raise ValueError('unknown self-model section')
            reading = {section: reading[section]}
        return reading if detail == 'compact' else {'reading': reading, 'fields': model.model_dump(mode='json'),
                                                    'snapshot_sha256': model.sha256(now=now)}

    def simulate(self, action_json):
        from g6.self_model.simulate import Action, simulate
        action = Action.model_validate_json(action_json)
        now = self.now()
        snapshot = self.snapshot(now) if self.snapshot is not None else self._local_snapshot(now)
        inputs = self.simulation_inputs(now) if self.simulation_inputs is not None else self._simulation_inputs(now)
        return simulate(action, snapshot, now=now, **inputs).model_dump(mode='json')

    def distill_start(self, family, failure_ids=''):
        from g6.distill.synthesis import SynthesisCase, Synthesizer
        # A configured admission anchor is checked before even training intake.
        # Hosts without one may still perform training-only work, never admission.
        self.validate_registration()
        if not isinstance(family, str) or not family.strip() or len(family) > 200 or not isinstance(failure_ids, str):
            raise ValueError('invalid training request')
        ids = failure_ids.replace(',', ' ').split()
        if any(not re.fullmatch(r'[0-9a-f]{64}', ident) for ident in ids):
            raise ValueError('invalid failure record identity')
        everything = Stores().for_model(FailureRecord).list()
        selected = [f for f in everything if f.family == family and (not ids or f.record_id in ids)]
        if not selected or (ids and set(ids) != {f.record_id for f in selected}):
            return refused('G6_E_SYNTH_TRAINING_ONLY', 'No failure record of this family matches. ' + NEEDS_CASE)
        derived = {f.provenance.get('labelled_from'): f for f in everything
                   if f.family == family and f.observed_by == 'independent' and f.split == 'training'}
        ready, pending, skipped = {}, [], []
        for failure in selected:
            if failure.observed_by == 'independent' and failure.split == 'training':
                ready[failure.record_id] = failure
            elif failure.record_id in derived:
                ready[derived[failure.record_id].record_id] = derived[failure.record_id]
            elif (case := _oracle_case(failure)) is not None:
                pending.append((failure, case))
            else:
                skipped.append({'record_id': failure.record_id, 'reason': 'no runnable case'})
        for failure in tuple(ready.values()):
            from g6.distill.facts import source_mapping
            case = failure.provenance.get('case', {})
            try:
                if failure.provenance.get('cohort') in {'selection', 'held_out', 'unseen'}:
                    raise ValueError('held-out or unseen case is never training data')
                if not isinstance(case, dict) or not {'before', 'after'} <= case.keys():
                    raise ValueError('no before/after source maps')
                source_mapping(case['before'])
                source_mapping(case['after'])
            except ValueError as exc:
                skipped.append({'record_id': failure.record_id, 'reason': str(exc)})
                del ready[failure.record_id]
        if not ready and not pending:
            return {**refused('G6_E_SYNTH_TRAINING_ONLY', NEEDS_CASE), 'skipped': skipped}
        if self.synthesizer is None:
            self.synthesizer = Synthesizer()
        synth = self.synthesizer
        if self.jobs is None:
            return refused('G6_E_JOB_HOST_REQUIRED', 'Host job store required; no work started.')
        def run():
            from g6.distill.held_out import oracle
            from g6.distill.synthesis import propose_concept
            from g6.workforce.repo_tests import RepoRefused
            failures, labels = list(ready.values()), []
            for failure, case in pending:
                if _unseen(case) or case.unit_id in _unseen_units(Stores().for_model(FailureRecord).list()):
                    labels.append({'record_id': failure.record_id, 'label': 'unseen',
                                   'reason': 'selection cohort: never training data'})
                    continue
                # A start queued beside this one may have labelled the record already: read again before running.
                with failure_writer():
                    existing = _derived(family, failure.record_id)
                if existing is not None:
                    labels.append({'record_id': failure.record_id, 'label': 'positive', 'reason': 'already_labelled',
                                   'training_record_id': existing.record_id})
                    failures.append(existing)
                    continue
                workspace = state_path('distill', 'oracle')
                workspace.mkdir(parents=True, exist_ok=True)
                try:
                    result = oracle.label(case, workspace=workspace)
                except RepoRefused as exc:
                    return {**refused(exc.code, 'G6 labels a case only inside an operating-system boundary, and '
                                                'none is available here; no record was labelled.'), 'labels': labels}
                row = {'record_id': failure.record_id, 'label': result.label, 'reason': result.reason}
                if result.label == 'positive':
                    with failure_writer():
                        if case.unit_id in _unseen_units(Stores().for_model(FailureRecord).list()):
                            labels.append({'record_id': failure.record_id, 'label': 'unseen',
                                           'reason': 'unit held out: never training data'})
                            continue
                        training = _derived(family, failure.record_id) or _labelled(failure, case, result)
                        _put_once(training)
                    row['training_record_id'] = training.record_id
                    failures.append(training)
                labels.append(row)
            # Recheck already-derived rows against all recorded unit custody before use.
            history = Stores().for_model(FailureRecord).list()
            unseen = _unseen_units(history)
            eligible = []
            for failure in failures:
                unit = _record_unit(failure, history)
                if unit in unseen or (failure.provenance.get('labelled_from') and unit is None):
                    skipped.append({'record_id': failure.record_id, 'reason': 'unit custody unavailable or held out'})
                else:
                    eligible.append(failure)
            failures = eligible
            # A pair of source maps counts once, whatever identity the caller supplied.
            unique, duplicates = {}, []
            for failure in failures:
                key = digest([failure.provenance['case']['before'], failure.provenance['case']['after']])
                if key in unique:
                    duplicates.append(failure.record_id)
                else:
                    unique[key] = failure
            failures = list(unique.values())
            if not failures:
                return {**refused('G6_E_SYNTH_TRAINING_ONLY', "G6's oracle labelled no supplied case weakened, "
                                                              'so there is no training failure to learn from.'),
                        'labels': labels, 'skipped': skipped}
            cases = [SynthesisCase(f, f.provenance['case']['before'], f.provenance['case']['after'])
                     for f in failures]
            concepts = [c for c in Stores().for_model(Concept).list()
                        if c.family == family and set(c.from_failures) == {f.record_id for f in failures}
                        and c.status == 'proposed']
            concept = concepts[-1] if concepts else propose_concept(cases, synthesizer=synth)
            if isinstance(concept, dict):
                return {**concept, 'labels': labels, 'duplicates': duplicates, 'skipped': skipped}
            _put_once(concept)
            result = synth.synthesise(concept, cases)
            if result.reuses:
                from g6.core.ledger import ChainLedger
                with failure_writer():
                    journal = ChainLedger(state_path('distill', 'reuse_ledger.jsonl'))
                    for reuse in result.reuses:
                        body = reuse.model_dump(mode='json')
                        if not any(row['id'] == digest(body) for row in journal.entries()):
                            journal.append('reuse', digest(body), body)
            for candidate in result.candidates:
                _put_once(candidate.contract)
                _put_once(candidate)
            # Admission is a distinct host operation; never invent a registration.
            from g6.distill.acceptance_gate import AcceptanceDecision, decide_batch
            from g6.distill.library import Library
            decisions = decide_batch(result.candidates, Library(), self.registration) if result.candidates else ()
            if not result.candidates and not result.reuses:
                # Runtime refusal produces no proposal or admission look. Keep
                # its explicit blocked outcome visible to the requesting dialog.
                inactive = {'G6_E_DISTILL_METHOD_HELD', 'G6_E_DISTILL_METHOD_WITHDRAWN',
                            'G6_E_DISTILL_IDENTITY_UNKNOWN', 'G6_E_DISTILL_METHOD_HELD_DURING_RUN',
                            'G6_E_DISTILL_METHOD_WITHDRAWN_DURING_RUN', 'G6_E_DISTILL_STATUS_CHANGED_DURING_RUN'}
                reasons = tuple(dict.fromkeys(refusal.code for refusal in result.refusals if refusal.code in inactive))
                if reasons:
                    decisions = (AcceptanceDecision('block', reasons),)
            return {'concept_id': concept.concept_id, **result.model_dump(mode='json'),
                    'decisions': [asdict(d) for d in decisions], 'labels': labels, 'duplicates': duplicates,
                    'skipped': skipped}
        return {**self.jobs.start(run), 'skipped': skipped}

    def distill_job(self, job_id, detail='compact'):
        if self.jobs is None:
            return refused('G6_E_JOB_UNKNOWN', 'No retained job store in this process.')
        return self.jobs.status(job_id, detail=detail)

    def validate_registration(self):
        if self.registration != '':
            from g6.distill.acceptance_gate import _registration
            _registration(self.registration)

    def restore(self, entry_id):
        from g6.distill.acceptance_gate import restore
        from g6.distill.library import Library
        # Explicit bad anchors retain the service input-refusal envelope before
        # gate.restore turns admission refusals into decision dictionaries.
        self.validate_registration()
        return asdict(restore(entry_id, Library(), self.registration))

    def extend(self, request, repo=''):
        return self.jobs.start(lambda: self._extend(request, repo)) if self.jobs is not None else self._extend(request, repo)

    def _extend(self, request, repo):
        from g6.distill.ledger import PartialAuthorityWrite
        from g6.distill.surface import failure_record
        from g6.distill.rectify_gate import Rectifier
        recorded = None
        try:
            with failure_writer():
                result = failure_record('user_request', 'capability_extension', json.dumps({'repo': repo}), request)
                if 'record_id' not in result:
                    return result
                extension = CapabilityExtension(kind='user_requested', requested_by='person', gap_record=result['record_id'],
                    summary=request, artefact_ref='', evidence_ref=digest({'request': request}), certificate_ref='0' * 64,
                    status='requested', scope='local')
                # The gap and its request publish together and grant no authority.
                recorded = Rectifier().extend(extension)
        except PartialAuthorityWrite as error:
            if error.publication_committed and recorded is not None:
                error.committed_steps = [('gap record', result['record_id']),
                                         ('capability request', recorded.content_id)]
            raise
        return recorded.model_dump(mode='json')

    def _runner(self, folder):
        return self.runners.get(folder)

    def research_admission(self, folder=''):
        from g6.self_training.autonomy_gate import authority
        runner = self._runner(folder)
        config = runner.config if runner is not None else self.config
        if not authority(config).permits('research'):
            return refused('G6_E_RESEARCH_AUTHORITY', 'Owner research authority is off. The owner grants it in '
                           'self_training/autonomy.json (level and research), a protected file that changes only '
                           'through owner issuance; no MCP call grants it. g6_research_status reads it.')
        if runner is None:
            return refused('G6_E_RESEARCH_HOST_REQUIRED', 'No host research runner is attached to this server. The '
                           "owner's host service attaches one with its engineer missions, budget meter and "
                           'repository port (docs/research-runner.md); no MCP call creates it.')
        return None

    def research_start(self, folder=''):
        denied = self.research_admission(folder)
        if denied:
            return denied
        if self.jobs is None:
            return refused('G6_E_JOB_HOST_REQUIRED', 'Host job store required; no work started.')
        runner = self._runner(folder)
        if self._stop_path(folder).exists():
            return refused('G6_E_RESEARCH_STOPPED', 'Owner stop is set; only the owner clears it.')
        return self.jobs.start(lambda: asdict(runner.tick(now=self.now())))

    def _stop_path(self, folder):
        return state_path('distill', 'research', digest(folder), 'STOP')

    def research_stop(self, folder='', reason=''):
        if self._runner(folder) is None:
            return refused('G6_E_RESEARCH_RUNNER_UNKNOWN',
                f'No runner attached under key {folder!r}. Attached keys: {sorted(self.runners)!r}.')
        path = self._stop_path(folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('owner stop\n', encoding='utf-8', newline='\n')
        return {'status': 'stopped', 'reason': reason}

    def research_status(self, folder=''):
        from g6.self_training.autonomy_gate import authority
        runner = self._runner(folder)
        level = authority(runner.config if runner is not None else self.config).level
        return {'authority': level, 'host_configured': runner is not None,
                'status': 'stopped' if runner is not None and (self._stop_path(folder).exists() or runner.stopped())
                          else 'off' if level == 'off' else 'ready' if runner is not None else 'host_required'}

    def research_digest(self, folder=''):
        runner = self._runner(folder)
        if runner is None:
            return {'ticks': 0, 'status': self.research_status(folder), 'body': '', 'pr': ''}
        # Reading this tool must not publish the digest. Publication belongs to the host tick.
        return runner.digest_readout(now=self.now())
