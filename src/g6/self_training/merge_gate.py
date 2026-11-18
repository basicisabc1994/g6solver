# SPDX-License-Identifier: Apache-2.0
"""PROTECTED host merge authority. Never import simulations or predictions here.

All attestations are authenticated by an injected host verifier. Container agents
cannot supply that verifier, mutate this service, its config, or its ledgers.
GitHub rules (host-only status, no bypass, protected CODEOWNERS) enforce custody;
this module alone is not a boundary against arbitrary same-user shell access.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import hashlib
from pathlib import Path
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictBool, StrictInt, model_validator

from g6._agentic.protected import DOOR_WORDS, TRUST_DOORS, certificate_bound
from g6.core.adaptation_gate import PROTECTED_FRAGMENTS, protected_hits
from g6.core.ledger import LedgerCorrupt, canonical, digest
from g6.core import stop as global_stop
from g6.safety_engine.weakening import detect_test_weakening
from g6.self_training.patch_promotion_gate import PROTECTED_PATH_FRAGMENTS
from .autonomy_gate import Autonomy as Autonomy, RateState, authority, clock
from .generation_gate import Generations
from .merge_measurement_gate import check_evaluation

Sha = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Commit = Annotated[str, Field(pattern=r'^[0-9a-f]{40}([0-9a-f]{24})?$')]
Count = Annotated[StrictInt, Field(ge=0, le=100000)]
REQUIRED_CI = tuple(f'test ({os}, {python}, {shard})' for os in
                    ('ubuntu-latest', 'macos-latest', 'windows-latest')
                    for python in ('3.12', '3.13') for shard in (1, 2, 3, 4)) + tuple(
                    f'build ({os})' for os in ('ubuntu-latest', 'macos-latest', 'windows-latest'))


class Data(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class FileChange(Data):
    path: str
    before: str | None
    after: str | None
    old_path: str | None = None
    mode: Literal['regular', 'symlink', 'submodule', 'binary'] = 'regular'


class Change(Data):
    pr: Annotated[StrictInt, Field(gt=0)]
    branch: str
    parent_commit: Commit
    head_commit: Commit
    merge_commit: Commit
    tree_sha256: Sha
    claim: Literal['improvement', 'no_behaviour_change', 'rollback']
    author_family: Literal['claude', 'codex']
    author_model: str
    files: tuple[FileChange, ...]

    @model_validator(mode='after')
    def valid(self):
        if not re.fullmatch(r'claude/g6-auto/[a-z0-9/-]+', self.branch) or not self.author_model.strip():
            raise ValueError('public auto branch and author model required')
        if not self.files or len({f.path.casefold() for f in self.files}) != len(self.files):
            raise ValueError('nonempty distinct diff paths required')
        if self.parent_commit == self.merge_commit:
            raise ValueError('merge must create a new commit')
        return self

    @property
    def diff_sha256(self):
        return digest([f.model_dump(mode='json') for f in self.files])

    @property
    def sha256(self):
        return digest(self.model_dump(mode='json'))


def normal(path):
    path = path.replace('\\', '/').casefold()
    if (not path or path.startswith('/') or ':' in path or '\x00' in path
            or any(p in ('', '.', '..') or p.rstrip(' .') != p for p in path.split('/'))):
        raise ValueError('ambiguous repository path')
    return path


@dataclass(frozen=True)
class ProtectedSet:
    patterns: tuple[str, ...]
    bound: tuple[str, ...]
    complete: bool = True

    @classmethod
    def load(cls, *, judging_patterns=()):
        files, folders = certificate_bound()
        patterns = (*PROTECTED_FRAGMENTS, *PROTECTED_PATH_FRAGMENTS, *DOOR_WORDS, *judging_patterns,
            *(door.replace('.', '/') + '/' for door in TRUST_DOORS), *folders,
            '.git/', '.github/', 'codeowners', 'validation/distill/', 'self_model/risk_gate.py',
            'self_training/autonomy.json', 'self_training/merge_gate.py', 'distill/runner.py',
            'distill/repository_adapter.py', 'distill/missions.py', 'certificates/')
        return cls(tuple(sorted(set(patterns))), tuple(sorted(files)), complete=bool(judging_patterns))

    @property
    def sha256(self):
        return digest({'patterns': self.patterns, 'bound': self.bound, 'complete': self.complete})

    def hits(self, files):
        if not self.complete or not self.patterns or not self.bound:
            return tuple(f.path for f in files)
        hits = []
        for file in files:
            try:
                paths = (normal(file.path),) + ((normal(file.old_path),) if file.old_path else ())
                if file.mode != 'regular' or file.before == file.after:
                    raise ValueError('unreadable, indirect or empty change')
                for path in paths:
                    aliases = (path, path.removeprefix('src/'))
                    is_bound = any(alias == b.casefold() for alias in aliases for b in self.bound)
                    # Exempt only a new regular Python test under tests/. Other
                    # protection (trust door, held-out, fixture, etc.) still applies.
                    new_test = (file.before is None and file.after is not None and not file.old_path
                        and path.startswith('tests/') and path.endswith('.py') and 'conftest' not in path)
                    candidate = path.removeprefix('tests/') if new_test else path
                    patterns = tuple(p for p in self.patterns if not new_test or p not in ('tests/',))
                    protected = (any(fragment in candidate for fragment in PROTECTED_FRAGMENTS)
                                 if new_test else bool(protected_hits((candidate,))))
                    protected |= any(p.casefold() in alias or fnmatchcase(alias, p.casefold())
                        for p in patterns for alias in (candidate, candidate.removeprefix('src/')))
                    if is_bound or protected:
                        hits.append(file.path)
                        break
            except ValueError:
                hits.append(file.path)
        return tuple(hits)


class CIJob(Data):
    job: str
    run_id: str
    conclusion: str
    commit: Commit


class Finding(Data):
    id: str
    disposition: Literal['fixed', 'closed', 'open']
    reason: str
    reviewer_accepted: StrictBool
    reproduction_digest: str
    test_digest: str
    test_passed: StrictBool


class Review(Data):
    family: Literal['claude', 'codex']
    model: str
    commit: Commit
    clean: StrictBool
    rounds: Annotated[StrictInt, Field(ge=1, le=4)]
    repairs: Annotated[StrictInt, Field(ge=0, le=3)]
    k_loop_receipt: Sha
    search_receipt: Sha
    findings: tuple[Finding, ...]


class Measure(Data):
    before: FiniteFloat
    after: FiniteFloat
    before_interval: tuple[FiniteFloat, FiniteFloat]
    after_interval: tuple[FiniteFloat, FiniteFloat]
    delta_upper95: FiniteFloat
    delta_lower95: FiniteFloat | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.delta_lower95 is not None and self.delta_lower95 > self.delta_upper95:
            raise ValueError('paired change interval is reversed')
        for point, (lo, hi) in ((self.before, self.before_interval), (self.after, self.after_interval)):
            if not 0 <= lo <= point <= hi:
                raise ValueError('nonnegative ordered 95% interval containing measurement required')
        return self


class Evaluation(Data):
    split: Literal['held_out', 'monitor']
    policy_sha256: Sha
    split_sha256: Sha
    registration_sha256: Sha
    reuse_index: Annotated[StrictInt, Field(ge=1)]
    n: Count
    wins: Count
    losses: Count
    ties: Count
    p_value: Annotated[FiniteFloat, Field(ge=0, le=1)]
    gate: str
    gate_receipt: Sha
    independent: StrictBool
    measures: dict[str, Measure]
    receipts: tuple[Sha, ...]
    full_suite: StrictBool


class Trust(Data):
    bot: str
    bot_is_owner_provisioned: StrictBool
    workflows_write: StrictBool
    ruleset_main: StrictBool
    required_checks: tuple[str, ...]
    no_bypass: StrictBool
    pr_required: StrictBool
    codeowners_complete: StrictBool
    owner_review_required: StrictBool
    status_host_only: StrictBool
    isolated_mission: StrictBool


class HostEvidence(Data):
    commit: Commit
    change_sha256: Sha
    measured_at: FiniteFloat
    expires_at: FiniteFloat
    origin: str
    manifest_sha256: Sha
    ci: tuple[CIJob, ...]
    review: Review
    evaluation: Evaluation
    trust: Trust
    prediction_refs: tuple[Sha, ...] = ()  # recorded premortem references, never gate evidence


class MergeRequest(Data):
    change: Change
    evidence: HostEvidence
    signature: str


class Decision(Data):
    status: str
    reason: str
    labels: tuple[str, ...] = ()
    certificate: str = ''
    pr: str = ''


class Regression(Data):
    generation: Annotated[StrictInt, Field(gt=0)]
    commit: Commit
    measured_at: FiniteFloat
    expires_at: FiniteFloat
    metric: Literal['false_acceptance', 'false_rejection', 'cost', 'success', 'ci', 'crash_rate', 'refusal_rate']
    delta_lower95: FiniteFloat
    baseline: Annotated[FiniteFloat, Field(ge=0)]
    receipt: Sha
    origin: Literal['host_measurement']


class MergeGate:
    def __init__(self, *, home, config, host, repository, manifest=None, stop=None,
                 state_head=None, certificate_head=None, policy=None):
        self.config, self.host, self.repository = config, host, repository
        self.manifest = manifest if manifest is not None else ProtectedSet.load()
        self.stop = stop or (lambda: False)
        self.policy = policy
        self.anchored = home is None or (state_head is not None and certificate_head is not None)
        self.state = RateState(None if home is None else Path(home) / 'distill/merge_state.jsonl', expect_head=state_head)
        self.certificates = Generations(None if home is None else Path(home) / 'certificates/generations.jsonl',
                                        host, expect_head=certificate_head)

    def stopped(self):
        return bool(global_stop.engaged() or self.stop() or self.repository.stopped())

    def assess(self, request, *, now, rollback=False):
        clock(now)
        if self.stopped():
            return Decision(status='stopped', reason='owner stop or kill switch')
        if not authority(self.config).permits('merge'):
            return Decision(status='draft_only', reason='autonomous merge authority is off')
        try:
            req = MergeRequest.model_validate_json(request.model_dump_json())
            change, e = req.change, req.evidence
            if not self.anchored:
                raise ValueError('independent state and certificate history anchors required on the host')
            if e.origin != 'host_measurement':
                raise ValueError('predictions are not merge evidence')
            if self.manifest.hits(change.files):
                return Decision(status='draft_only', reason='protected or bound diff', labels=('protected-change',))
            before = {f.path: f.before for f in change.files if f.before is not None}
            after = {f.path: f.after for f in change.files if f.after is not None}
            if detect_test_weakening(before, after).weakened:
                return Decision(status='draft_only', reason='test weakening', labels=('protected-change',))
            if not self.host.verify(canonical(e.model_dump(mode='json')), req.signature):
                raise ValueError('host evidence signature failed')
            if not e.measured_at <= now <= e.expires_at or e.expires_at - e.measured_at > 3600:
                raise ValueError('host evidence is stale or has an excessive validity window')
            if e.commit != change.merge_commit or e.change_sha256 != change.sha256:
                raise ValueError('host evidence does not bind the exact change and merge commit')
            if e.manifest_sha256 != self.manifest.sha256:
                raise ValueError('protected manifest changed')
            trust = e.trust
            if (not trust.bot or not trust.bot_is_owner_provisioned or trust.workflows_write
                or not all((trust.ruleset_main, trust.no_bypass, trust.pr_required, trust.codeowners_complete,
                            trust.owner_review_required, trust.status_host_only, trust.isolated_mission))
                or not set((*REQUIRED_CI, 'g6-merge-gate')).issubset(trust.required_checks)):
                raise ValueError('owner bot, ruleset, CODEOWNERS or host status custody missing')
            jobs = {j.job: j for j in e.ci}
            if len(jobs) != len(e.ci) or not set(REQUIRED_CI).issubset(jobs):
                raise ValueError('missing or duplicate required CI job')
            if any(j.conclusion != 'success' or j.commit != change.merge_commit or not j.run_id for j in e.ci):
                raise ValueError('every CI job must be green on the exact merge commit')
            if rollback:
                if change.claim != 'rollback':
                    raise ValueError('rollback claim required')
            else:
                if change.claim == 'rollback':
                    raise ValueError('rollback requires measured triggering evidence')
                review = e.review
                if not review.clean or review.family == change.author_family or review.commit != change.merge_commit:
                    raise ValueError('clean exact-commit review by other model family required')
                for finding in review.findings:
                    if not finding.reviewer_accepted:
                        raise ValueError('reviewer did not accept finding disposition')
                    if finding.disposition == 'fixed':
                        if not (re.fullmatch('[0-9a-f]{64}', finding.reproduction_digest)
                            and re.fullmatch('[0-9a-f]{64}', finding.test_digest) and finding.test_passed):
                            raise ValueError('fixed finding requires reproduced and passing retained test')
                        added_tests = {hashlib.sha256(f.after.encode('utf-8')).hexdigest()
                            for f in change.files if f.before is None and f.after is not None
                            and normal(f.path).startswith('tests/') and f.path.endswith('.py')}
                        if finding.test_digest not in added_tests:
                            raise ValueError('review reproduction test must be retained in the exact diff')
                    elif finding.disposition != 'closed' or not finding.reason.strip():
                        raise ValueError('unresolved finding')
                why = check_evaluation(e.evaluation, change.claim, self.policy)
                if why:
                    raise ValueError(why)
            self.certificates.read()
            if not self.state.room(now=now, rollback=rollback):
                raise ValueError('rate limit, pending reservation, demotion or rollback cooldown')
            return Decision(status='ready', reason='host-authenticated exact-commit checks pass')
        except LedgerCorrupt as exc:
            return Decision(status='anomaly', reason=str(exc))
        except (ValueError, KeyError, TypeError) as exc:
            return Decision(status='blocked', reason=str(exc))

    def merge(self, request, *, now):
        if self.stopped():
            return Decision(status='stopped', reason='owner stop or kill switch')
        if not authority(self.config).permits('merge'):
            return Decision(status='draft_only', reason='autonomous merge authority is off')
        request = MergeRequest.model_validate_json(request.model_dump_json())
        with self.state.exclusive():
            if not self._attempt(now=now):
                return Decision(status='blocked', reason='merge attempt anomaly or backwards clock')
            decision = self.assess(request, now=now)
            if decision.status == 'anomaly':
                self.state.append('demote', now=now, reason='certificate chain failed verification')
                return Decision(status='blocked', reason=decision.reason)
            if decision.status != 'ready':
                return decision
            change, e = request.change, request.evidence
            prepared = self.certificates.prepare(change=change, evidence=e)
            reservation = digest(prepared)
            self.state.append('reserve', now=now, reservation=reservation, prepared=prepared)
            if self.stopped() or not authority(self.config).permits('merge'):
                return Decision(status='stopped', reason='authority withdrawn before merge; reservation retained')
            actual = self.repository.merge_exact(change)
            if actual != change.merge_commit:
                self.state.append('demote', now=now, reason='merge returned unexpected commit')
                return Decision(status='blocked', reason='exact merge commit mismatch; owner reconciliation required')
            certificate = self.certificates.append(prepared)
            self.state.append('merged', now=now, reservation=reservation, certificate=certificate)
            return Decision(status='merged', reason='exact merge confirmed', certificate=certificate)

    def regression(self, signal, *, signature, now):
        clock(now)
        if self.stopped():
            return Decision(status='stopped', reason='owner stop')
        grants = authority(self.config)
        if not grants.permits('push') or not grants.permits('draft_pr'):
            return Decision(status='draft_only', reason='revert proposal authority off')
        try:
            measured = Regression.model_validate(signal)
            signal = measured.model_dump(mode='json')
            if (not self.host.verify(canonical(measured.model_dump(mode='json')), signature)
                    or not measured.measured_at <= now <= measured.expires_at):
                raise ValueError('fresh authenticated regression measurement required')
            if self.policy is None:
                raise ValueError('frozen owner policy required')
            margin = self.policy.cost_margin * measured.baseline if measured.metric == 'cost' else (
                0 if measured.metric == 'ci' else self.policy.false_accept_margin)
            if measured.delta_lower95 <= margin:
                return Decision(status='no_regression', reason='measured regression does not exceed frozen margin')
            with self.state.exclusive():
                history = self.certificates.read()
                generation = history[measured.generation - 1]
                if generation['body']['merge_commit'] != measured.commit:
                    raise ValueError('regression does not bind a certified generation')
                key = digest({'revert_generation': measured.generation})
                prior = [r for r in self.state.rows() if r['kind'] == 'revert_pr' and r['body']['key'] == key]
                if prior:
                    return Decision(status='revert_proposed', reason='already proposed', pr=prior[-1]['body']['pr'])
                if any(r['kind'] == 'revert_pending' and r['body']['key'] == key for r in self.state.rows()):
                    return Decision(status='blocked', reason='uncertain revert proposal; owner reconciliation required')
                self.state.append('revert_pending', now=now, key=key, signal=signal, signature=signature)
                if self.stopped() or not authority(self.config).permits('push'):
                    return Decision(status='stopped', reason='stopped before revert proposal')
                change = {k: v for k, v in generation['body']['change'].items() if k != 'diff_sha256'}
                change = Change.model_validate(change)
                pr = self.repository.revert_pr(change, evidence=signal, key=key)
                self.state.append('revert_pr', now=now, key=key, pr=pr)
                return Decision(status='revert_proposed', reason='measured regression exceeds margin', pr=pr)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            return Decision(status='blocked', reason=str(exc))

    def note_attempt(self, *, now):
        """Host records every attempted merge; a burst only lowers authority."""
        with self.state.exclusive():
            return self._attempt(now=now)

    def _attempt(self, *, now):
        rows = self.state.rows()
        if rows and now < max(r['body']['at'] for r in rows):
            return False
        count = sum(r['kind'] == 'attempt' and 0 <= now - r['body']['at'] < 3600 for r in rows)
        self.state.append('attempt', now=now, number=count + 1)
        if count >= 4:
            if not self.state.demoted():
                self.state.append('demote', now=now, reason='burst of merge attempts')
            return False
        return not self.state.demoted()

    def report_anomaly(self, reason, *, now):
        """A discrepancy or invalid chain cannot grant authority, only remove it."""
        with self.state.exclusive():
            if not self.state.demoted():
                self.state.append('demote', now=now, reason=str(reason))

    def merge_rollback(self, request, *, signal, signature, now):
        """Exact inverse of a certified generation, protected check + CI fast path."""
        if self.stopped():
            return Decision(status='stopped', reason='owner stop')
        if not authority(self.config).permits('merge'):
            return Decision(status='draft_only', reason='rollback merge authority off')
        request = MergeRequest.model_validate_json(request.model_dump_json())
        signal = Regression.model_validate(signal).model_dump(mode='json')
        with self.state.exclusive():
            try:
                if not self._attempt(now=now):
                    return Decision(status='blocked', reason='rollback attempt anomaly or backwards clock')
                measured = Regression.model_validate(signal)
                if not self.host.verify(canonical(measured.model_dump(mode='json')), signature):
                    raise ValueError('rollback trigger signature failed')
                if not measured.measured_at <= now <= measured.expires_at:
                    raise ValueError('rollback trigger is stale')
                key = digest({'revert_generation': measured.generation})
                proposed = [r for r in self.state.rows() if r['kind'] == 'revert_pending' and r['body']['key'] == key
                            and r['body']['signal'] == signal and r['body']['signature'] == signature]
                if not proposed:
                    raise ValueError('measured regression and revert proposal required')
                original = self.certificates.read()[measured.generation - 1]['body']
                expected = tuple(FileChange(**{**f, 'before': f['after'], 'after': f['before']})
                                 for f in original['change']['files'])
                if (request.change.files != expected or request.change.parent_commit != measured.commit
                        or original['merge_commit'] != measured.commit):
                    raise ValueError('rollback must be the exact inverse at the certified tip')
                decision = self.assess(request, now=now, rollback=True)
                if decision.status == 'anomaly':
                    self.state.append('demote', now=now, reason='certificate chain failed verification')
                    return Decision(status='blocked', reason=decision.reason)
                if decision.status != 'ready':
                    return decision
                prepared = self.certificates.prepare(change=request.change, evidence=request.evidence,
                    kind='rollback', trigger=signal)
                reservation = digest(prepared)
                self.state.append('reserve', now=now, reservation=reservation, prepared=prepared)
                if self.stopped() or not authority(self.config).permits('merge'):
                    return Decision(status='stopped', reason='rollback stopped; reservation retained')
                actual = self.repository.merge_exact(request.change)
                if actual != request.change.merge_commit:
                    self.state.append('demote', now=now, reason='rollback returned unexpected commit')
                    return Decision(status='blocked', reason='rollback commit mismatch')
                certificate = self.certificates.append(prepared)
                self.state.append('merged', now=now, reservation=reservation, certificate=certificate)
                self.state.append('rollback', now=now, generation=prepared['body']['generation'], reverted=measured.generation)
                recent = sum(r['kind'] == 'rollback' and now - r['body']['at'] < 14 * 86400 for r in self.state.rows())
                if recent >= 2 and not self.state.demoted():
                    self.state.append('demote', now=now, reason='two rollbacks within fourteen days')
                return Decision(status='merged', reason='rollback confirmed', certificate=certificate)
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                return Decision(status='blocked', reason=str(exc))
