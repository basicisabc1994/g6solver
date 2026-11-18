# SPDX-License-Identifier: Apache-2.0
"""Host integration of installed slices; no ambient execution capabilities.

The host injects its measured self-model context, model caller and runner. Public
requests cannot supply those capabilities, independence or admission receipts.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import time

from g6.core.door import blocked_reply
from g6.core.ledger import digest
from g6.core.storage import state_path
from g6.distill.ledger import Stores
from g6.distill.records import CapabilityExtension, Concept, FailureRecord, VerifierCandidate


def refused(code, reason):
    return blocked_reply(f'[{code}] {reason}')


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
        self.config = config if config is not None else Path(__file__).parents[1] / 'self_training/autonomy.json'
        for folder, runner in self.runners.items():
            original = runner.stop
            runner.stop = lambda prior=original, key=folder: prior() or self._stop_path(key).exists()

    def self_model(self, section='', detail='compact'):
        from g6.core import stop
        from g6.self_model.model import Field, NamedField, SelfModel
        from g6.self_training.autonomy_gate import authority
        if detail not in ('compact', 'full'):
            raise ValueError('detail must be compact or full')
        now = self.now()
        def field(name, value):
            return NamedField(name=name, field=Field.measured(value, source='g6.distill.service.local_state',
                observed_at=now, ttl_s=0.))
        model = self.snapshot(now) if self.snapshot is not None else SelfModel(
            state=(field('stop', stop.engaged() is not None), field('budget_room', 'unknown'),
                   field('isolation', 'unknown')),
            authority=(field('level', authority(self.config).level),))
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
        if self.snapshot is None or self.simulation_inputs is None:
            return refused('G6_E_SELF_MODEL_INPUTS', 'Trusted measured snapshot and simulation context required.')
        now = self.now()
        return simulate(action, self.snapshot(now), now=now,
                        **self.simulation_inputs(now)).model_dump(mode='json')

    def distill_start(self, family, failure_ids=''):
        from g6.distill.synthesis import SynthesisCase, Synthesizer
        ids = failure_ids.replace(',', ' ').split()
        failures = Stores().for_model(FailureRecord).list()
        failures = [f for f in failures if f.family == family and (not ids or f.record_id in ids)]
        if (not failures or (ids and set(ids) != {f.record_id for f in failures})
            or any(f.split != 'training' or f.observed_by != 'independent' for f in failures)):
            return refused('G6_E_SYNTH_TRAINING_ONLY', 'Exactly independent training failures are required.')
        cases = []
        for failure in failures:
            case = failure.provenance.get('case', {})
            if not isinstance(case, dict) or not {'before', 'after'} <= case.keys():
                return refused('G6_E_SYNTH_TRAINING_ONLY', 'Training failures need supplied before/after source maps.')
            cases.append(SynthesisCase(failure, case['before'], case['after']))
        if self.synthesizer is None:
            self.synthesizer = Synthesizer()
        synth = self.synthesizer
        concepts = [c for c in Stores().for_model(Concept).list()
                    if c.family == family and set(c.from_failures) == {f.record_id for f in failures}
                    and c.status == 'proposed']
        if self.jobs is None:
            return refused('G6_E_JOB_HOST_REQUIRED', 'Host job store required; no work started.')
        def run():
            from g6.distill.synthesis import propose_concept
            concept = concepts[-1] if concepts else propose_concept(cases, synthesizer=synth)
            if isinstance(concept, dict):
                return concept
            _put_once(concept)
            result = synth.synthesise(concept, cases)
            for candidate in result.candidates:
                _put_once(candidate.contract)
                _put_once(candidate)
            # Admission is a distinct host operation; never invent a registration.
            from g6.distill.acceptance_gate import decide_batch
            from g6.distill.library import Library
            decisions = decide_batch(result.candidates, Library(), self.registration) if result.candidates else ()
            return {'concept_id': concept.concept_id, **result.model_dump(mode='json'),
                    'decisions': [asdict(d) for d in decisions]}
        return self.jobs.start(run)

    def distill_job(self, job_id, detail='compact'):
        if self.jobs is None:
            return refused('G6_E_JOB_UNKNOWN', 'No retained job store in this process.')
        return self.jobs.status(job_id, detail=detail)

    def restore(self, entry_id):
        from g6.distill.acceptance_gate import decide
        from g6.distill.library import Library
        library = Library()
        entry = library.get(entry_id)
        if entry is None:
            raise ValueError('stale library entry')
        return asdict(decide(Stores().for_model(VerifierCandidate).get(entry.candidate_id), library, self.registration))

    def extend(self, request, repo=''):
        return self.jobs.start(lambda: self._extend(request, repo)) if self.jobs is not None else self._extend(request, repo)

    def _extend(self, request, repo):
        from g6.distill.surface import failure_record
        from g6.distill.rectify_gate import Rectifier
        result = failure_record('user_request', 'capability_extension', json.dumps({'repo': repo}), request)
        extension = CapabilityExtension(kind='user_requested', requested_by='person', gap_record=result['record_id'],
            summary=request, artefact_ref='', evidence_ref=digest({'request': request}), certificate_ref='0' * 64,
            status='requested', scope='local')
        # Requested records grant no authority and never claim completed adaptation.
        return Rectifier().extend(extension).model_dump(mode='json')

    def _runner(self, folder):
        return self.runners.get(folder)

    def research_admission(self, folder=''):
        from g6.self_training.autonomy_gate import authority
        runner = self._runner(folder)
        config = runner.config if runner is not None else self.config
        if not authority(config).permits('research'):
            return refused('G6_E_RESEARCH_AUTHORITY', 'Owner research authority is off.')
        if runner is None:
            return refused('G6_E_RESEARCH_HOST_REQUIRED', 'Trusted host runner capabilities are required.')
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
        path = self._stop_path(folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('owner stop\n', encoding='utf-8', newline='\n')
        return {'status': 'stopped', 'reason': reason}

    def research_status(self, folder=''):
        from g6.self_training.autonomy_gate import authority
        runner = self._runner(folder)
        level = authority(runner.config if runner is not None else self.config).level
        return {'authority': level, 'host_configured': runner is not None,
                'status': 'stopped' if self._stop_path(folder).exists() or runner is not None and runner.stopped()
                          else 'off' if level == 'off' else 'ready' if runner is not None else 'host_required'}

    def research_digest(self, folder=''):
        runner = self._runner(folder)
        if runner is None:
            return {'ticks': 0, 'status': self.research_status(folder), 'body': '', 'pr': ''}
        # Reading this tool must not publish the digest. Publication belongs to the host tick.
        return runner.digest_readout(now=self.now())
