# SPDX-License-Identifier: Apache-2.0
"""G6's execution-owned outcomes, one hash chain per root-commit repository identity.

Same-user local operational state, like test receipts: protects against caller JSON, not code
running as that user. No public tool writes this store. A missing receipt, changed digest or
unreadable chain admits nothing. Retention is evidence reuse, never strategy adoption.
"""
from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

from g6.core.ledger import ChainLedger, digest
from g6.core.storage import state_path
from g6.workforce import receipt_store

KIND = 'coding-outcome'
SCHEMA = 'g6.outcome/1'
CAP = 5
CLASSIFICATIONS = frozenset({'counterexample', 'unconfirmed_failure', 'defect_detected', 'not_refuted',
                             'fails_on_base_too', 'check_error'})
REPRODUCED = frozenset({'counterexample', 'defect_detected'})
repository_id = receipt_store.repository_id


def store_path(repository: str) -> Path:
    if len(repository) != 64 or any(c not in '0123456789abcdef' for c in repository):
        raise ValueError('a repository identity is a SHA-256 digest')
    return state_path('evidence', 'outcomes') / (repository + '.jsonl')


def _receipt(row: dict, *, base=False) -> dict | None:
    prefix = 'base_' if base else ''
    body = receipt_store.lookup_learning(row.get(prefix + 'receipt', ''),
                                artifact_digest=row.get(prefix + 'artifact_digest', ''), _observations=True)
    if not body or body.get('repository_id') != row.get('repository_id'):
        return None
    if body.get('source_after') != row.get(prefix + 'source_after'):
        return None
    check = row.get('check') or {}
    if body.get('check_digests', {}).get(row.get('node', '').partition('::')[0]) != check.get('sha256'):
        return None
    if check.get('observation') is not None and body.get('observation_digest') != digest(check['observation']):
        return None
    if not any(row.get('node') == req or row.get('node', '').startswith(req + '::')
               for req in body.get('tests', {}).get('requested', [])):
        return None
    return body


def _admitted(row: dict, repository: str) -> bool:
    if row.get('schema') != SCHEMA or row.get('repository_id') != repository:
        return False
    classification = row.get('classification')
    if classification not in CLASSIFICATIONS:
        return False
    check = row.get('check')
    if (not isinstance(check, dict) or not isinstance(check.get('source'), str)
            or not isinstance(check.get('file'), str) or not check['file']
            or not isinstance(row.get('node'), str) or not row['node']
            or not isinstance(row.get('modules'), list) or not all(isinstance(m, str) for m in row['modules'])
            or not isinstance(row.get('finding'), str) or not isinstance(row.get('method'), str)
            or not isinstance(check.get('sha256'), str) or len(check['sha256']) != 64
            or any(c not in '0123456789abcdef' for c in check['sha256'])):
        return False
    if check['source'] and hashlib.sha256(check['source'].encode('utf-8')).hexdigest() != check.get('sha256'):
        return False
    body = _receipt(row)
    if body is None:
        return False
    passed, failed = _status(body, row['node'])
    if classification == 'not_refuted':
        return passed
    if classification in {'defect_detected', 'unconfirmed_failure'}:
        return failed
    if classification in {'counterexample', 'fails_on_base_too'}:
        base = _receipt(row, base=True)
        if check.get('refuted_by_difference') and base is not None:
            patch_observed, base_observed = body.get('result', {}).get('observations'), base.get('result', {}).get('observations')
            return (classification == 'counterexample' and passed and _status(base, row['node'])[0]
                    and isinstance(patch_observed, list) and bool(patch_observed)
                    and isinstance(base_observed, list) and bool(base_observed) and patch_observed != base_observed)
        if not failed or base is None:
            return False
        return _status(base, row['node'])[0] if classification == 'counterexample' else _status(base, row['node'])[1]
    return not passed and not failed


def _status(body: dict, node: str) -> tuple[bool, bool]:
    result = body.get('result', {})
    if body.get('source_before') != body.get('source_after') or result.get('verdict') not in {'passed', 'failed'}:
        return False, False
    if body.get('schema') == receipt_store.OBSERVATION_SCHEMA:
        complete = result.get('completed') is True and not result.get('degraded')
        return complete and result['verdict'] == 'passed', complete and result['verdict'] == 'failed'
    phases = body.get('test_outcomes', result.get('test_outcomes'))
    if isinstance(phases, dict) and result.get('completed') is True and not result.get('degraded'):
        mine = [p for n, p in phases.items() if n == node or n.startswith(node + '::')]
        whole = bool(mine) and all(p.get('setup') == 'passed' and p.get('teardown') == 'passed' for p in mine)
        return (whole and all(p.get('call') == 'passed' for p in mine),
                whole and any(p.get('call') == 'failed' for p in mine))
    return (receipt_store.counts_as_pass(body),
            result.get('verdict') == 'failed' and result.get('completed') is True
            and not result.get('degraded') and any(n == node or n.startswith(node + '::')
                                                  for n in result.get('failed_ids', [])))


@dataclass(frozen=True)
class History:
    repository: str = ''
    rows: tuple[dict, ...] = ()
    rejected: int = 0
    unavailable: str = ''
    root: Path | None = None

    @property
    def note(self) -> str:
        return (f'history({len(self.rows)} admitted, {self.rejected} rejected)' if self.rows else
                'cold-start (no admitted history)')

    @property
    def retained(self) -> tuple[dict, ...]:
        unique = {}
        for row in self.rows:
            if row['classification'] in REPRODUCED and row.get('check', {}).get('sha256'):
                # Repeated executions refresh recency but never create duplicate regression checks.
                key = (row['check']['sha256'], row['check']['file'], row['check'].get('test', ''))
                unique.pop(key, None)
                unique[key] = row
        return tuple(unique.values())

    def learned(self, activities=()) -> dict:
        retained = [a for a in activities if a.get('origin') == 'retained']
        missing = sum(any(not (self.root / rel).is_file() for rel in row.get('modules', []))
                      for row in self.retained) if self.root is not None else 0
        return {'retained_checks': len(self.retained),
                'stale_checks': max(missing, sum(a.get('result') == 'stale' for a in retained)),
                'admitted_trials': len(self.rows), 'rejected_trials': self.rejected,
                'ran': [a['check'] for a in retained if any(r.get('on') == 'patch' for r in a.get('runs', []))],
                **({'history_unavailable': self.unavailable} if self.unavailable else {})}

    def selector(self, context, variants):
        """Evaluator-owned translation; no caller-supplied trial, success flag or free text is read."""
        from g6.cognitive_bridge import EvaluatedTrial, MetacognitiveMethodSelector

        trials = []
        admitted = set()
        for row in self.rows:
            for variant in variants:
                if row.get('method') != variant.method:
                    continue
                if row.get('engine') and (row['engine'], row.get('op')) != (variant.engine, variant.op):
                    continue
                ident = digest([row, variant.id, variant.definition_digest])
                trials.append(EvaluatedTrial(ident, row['artifact_digest'], self.repository,
                    'software_engineering', 'bug_fix', 'patch', variant.id,
                    row['classification'] in REPRODUCED, 1, 'local:test-receipt:' + row['receipt'],
                    row['artifact_digest'], split='shadow', variant_digest=variant.definition_digest))
                admitted.add(ident)
        return MetacognitiveMethodSelector(trials, admit_trial=lambda trial: trial.trial_id in admitted)

    def candidate_scores(self, entries: dict, phase: str) -> dict[str, float]:
        from g6.cognitive_bridge import MethodVariant, ProblemSignature

        if not self.rows:
            return {}
        variants = []
        for key, entry in entries.items():
            call = entry.get('call') or {}
            if call.get('tool') in {'g6_review_patch', 'g6_review_until_clean'}:
                variants.append(MethodVariant(key, 'review', 'pytest', call['tool'], 'review', 1, kind='reviewer'))
        if not variants:
            return {}
        context = ProblemSignature('software_engineering', 'bug_fix', 'patch', 1, 100)
        decision = self.selector(context, variants).select(context, variants, required_methods=['review'],
                                                           max_optional=len(variants) - 1)
        return {r['variant_id']: 2 * (r['smoothed_success_proxy'] - .5)
                for r in decision.ordered_variants if r['training_cases']}


def read(root: Path) -> History:
    repository = repository_id(root)
    if not repository:
        return History()
    try:
        entries = ChainLedger(store_path(repository)).entries()
    except (OSError, ValueError):
        return History(repository, rejected=1, unavailable='outcome chain unreadable; no history admitted', root=root)
    rows, rejected = [], 0
    for entry in entries:
        row = entry['body']
        try:
            valid = entry['kind'] == KIND and entry['id'] == digest(row) and _admitted(row, repository)
        except (ValueError, TypeError, KeyError, AttributeError):
            valid = False
        if valid:
            rows.append(row)
        else:
            rejected += 1
    return History(repository, tuple(rows), rejected, root=root)


def _record(root: Path, row: dict) -> None:
    """Only execution call sites use this, after reading G6's own runner reports."""
    repository = repository_id(root)
    if not repository or not _admitted(row, repository):
        return
    path = store_path(repository)
    with receipt_store._locked(path):
        ledger = ChainLedger(path)
        ident = digest(row)
        if not any(e['id'] == ident for e in ledger.entries()):
            ledger.append(KIND, ident, row)


NOTICE = ('G6 keeps its own check results for this repository locally, including check source it already retains. '
          'Use g6_invoke("learning_history", "list", {"repo": <repository>}) to inspect counts and the store path, '
          'or g6_invoke("learning_history", "forget", {"repo": <repository>}) to ask to erase this history in '
          'the client dialog.')


def fingerprint(repository: str) -> str:
    path = store_path(repository)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ''


def take_notice(root: Path) -> str:
    """Once after the first persisted result; forget resets it. No check result is invented."""
    repository = repository_id(root)
    if not repository:
        return ''
    path = store_path(repository)
    with receipt_store._locked(path):
        marker = path.with_suffix('.notice')
        if not path.is_file() or not path.stat().st_size or marker.exists():
            return ''
        marker.write_text('local learning disclosed\n', encoding='utf-8')
    return NOTICE


def forget(root: Path, repository: str, *, expected: str | None = None) -> None:
    """Internal deletion, called only after the MCP client dialog. Receipts remain execution evidence."""
    if not repository or repository_id(root) != repository:
        raise ValueError('repository identity changed; nothing removed')
    path = store_path(repository)
    with receipt_store._locked(path):
        if expected is not None and fingerprint(repository) != expected:
            raise ValueError('history changed while the dialog was open; nothing removed')
        path.unlink(missing_ok=True)
        path.with_suffix('.notice').unlink(missing_ok=True)


def disclose(reply: dict, root: Path) -> dict:
    """Add the first-record disclosure to the existing warning card, without changing evidence authority."""
    try:
        notice = take_notice(root)
    except (OSError, ValueError):
        notice = ''
        if read(root).rows:
            # An unwritable marker never silently suppresses disclosure.
            notice = NOTICE
    if notice:
        card = reply.setdefault('warning_card', {})
        card['message'] = (card.get('message', '') + ' ' + notice).strip()
    return reply


def exercised(source: str, changed=()) -> list[str]:
    """Import names from check bytes; changed modules are used only when no import is named."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names = {alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    names |= {node.module.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    found = [rel for rel in changed if rel.endswith('.py') and any(
        rel == name + '.py' or rel.endswith('/' + name + '.py') or rel.startswith(name + '/')
        or '/' + name + '/' in rel for name in names)]
    return sorted(found or [rel for rel in changed if rel.endswith('.py')])


def record_check(change, check, placed: str, patch: dict, base: dict | None, classification: str, *,
                 origin='proposed', engine='g6_review_patch', refuted_by_difference=False) -> None:
    if classification not in CLASSIFICATIONS:
        return
    body = receipt_store.lookup_learning(patch.get('receipt', ''), artifact_digest=patch.get('source_before', ''),
                                _observations=True)
    if body is None:
        return
    row = {'schema': SCHEMA, 'repository_id': repository_id(change.repo), 'receipt': patch['receipt'],
           'artifact_digest': body['source_before'], 'source_after': body['source_after'],
           'classification': classification, 'origin': origin, 'node': placed,
           'method': 'review', 'engine': engine, 'op': 'review',
           'modules': exercised(check.source, change.files), 'finding': check.requirement_id,
           'check': {'file': check.file, 'source': check.source,
                     'sha256': hashlib.sha256(check.source.encode()).hexdigest() if check.source else
                               body.get('check_digests', {}).get(placed.partition('::')[0], ''),
                     'test': check.test, 'intent': check.intent}}
    from g6.harness.behaviour_runner import ExecutableSource

    if type(check.source) is ExecutableSource:
        row['check'].update(observation=check.source.observation, refuted_by_difference=refuted_by_difference)
    if base is not None:
        other = receipt_store.lookup_learning(base.get('receipt', ''), artifact_digest=base.get('source_before', ''),
                                     _observations=True)
        if other is not None:
            row.update(base_receipt=base['receipt'], base_artifact_digest=other['source_before'],
                       base_source_after=other['source_after'])
    _record(change.repo, row)


def check_sources(root: Path, nodes) -> dict[str, str]:
    """Capture bytes before execution; never follow an outside link or retain an oversized test."""
    from g6.workforce import repo_tests

    found = {}
    for node in nodes:
        relative = node.partition('::')[0]
        path = repo_tests._disk(root, relative)
        if (path is None or path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                or not path.is_file()):
            continue
        if path.stat().st_size > 64 * 1024:
            found[node] = ''   # retain its node and receipt-owned byte digest without raising the replay bound
            continue
        try:
            found[node] = path.read_bytes().decode('utf-8')
        except (OSError, UnicodeError):
            continue
    return found


def record_stated(change, sources: dict[str, str], runs, *, artifact: str) -> None:
    """One row per executed stated/derived check, using only receipt-owned verdicts."""
    from types import SimpleNamespace

    for run in runs:
        receipt = run.get('receipt', '')
        body = receipt_store.lookup_learning(receipt, artifact_digest=artifact)
        if body is None:
            continue
        for node, source in sources.items():
            requested = body.get('tests', {}).get('requested', [])
            if not any(node == req or node.startswith(req + '::') for req in requested):
                continue
            passed, failed = _status(body, node)
            classification = 'defect_detected' if failed else 'not_refuted' if passed else 'check_error'
            relative, _, test = node.partition('::')
            check = SimpleNamespace(file=relative, source=source, test=test, intent='preserve',
                                    requirement_id='stated check ' + node)
            record_check(change, check, node, {'receipt': receipt, 'source_before': body['source_before']},
                         None, classification, origin=run.get('provenance', 'stated'))


def retained_checks(change, requirement: str):
    """Overlap, then recency. No execution at preflight; missing exercised files stay stale."""
    from g6.harness import verification_search as vs

    history = read(change.repo)
    rows = sorted(enumerate(history.retained),
                  key=lambda pair: (-len(set(pair[1].get('modules', [])) & set(change.files)), -pair[0]))
    checks, origins, skipped = [], {}, []
    for _, row in rows:
        check = row['check']
        ident = 'retained-' + digest([check['sha256'], check['file'], check.get('test', '')])[:20]
        activity = {'activity': 'retained check', 'check': ident, 'requirement': requirement,
                    'origin': 'retained', 'intent': 'preserve', 'provenance': 'proposed', 'model_calls': 0,
                    'seconds': 0.0, 'finding': row['finding']}
        missing = [rel for rel in row.get('modules', []) if not (change.repo / rel).is_file()]
        if missing:
            skipped.append({**activity, 'result': 'stale', 'reason': 'exercised file gone: ' + ', '.join(missing)})
            continue
        if not check['source']:
            skipped.append({**activity, 'result': 'skipped',
                            'reason': 'retained node has no source bytes within the 64 KiB replay limit'})
            continue
        if len(checks) >= CAP:
            skipped.append({**activity, 'result': 'skipped', 'reason': 'retained check limit of 5; overlap then recency'})
            continue
        try:
            # Apply all of the existing proposed-check byte and placement restrictions again.
            one = vs.proposed_checks([{'id': ident, 'requirement_id': requirement, 'intent': 'preserve',
                                      'file': check['file'], 'source': check['source'], 'test': check.get('test', '')}],
                                     {requirement})[0]
            vs.placements(change.repo, [one])
        except ValueError:
            skipped.append({**activity, 'result': 'stale', 'reason': 'retained bytes or placement no longer apply'})
            continue
        checks.append(one)
        observed = check.get('observation')
        if observed is not None:
            from g6.harness.behaviour_runner import ExecutableSource

            checks[-1] = one.model_copy(update={'source': ExecutableSource(one.source, observed)})
        origins[ident] = vs.Origin(activity='retained check', label='retained', about={'origin': 'retained'},
                                  proposed_by='G6 retained a previously reproduced check',
                                  unconfirmed='a retained proposed check decides no requirement',
                                  template='differential.v1' if check.get('refuted_by_difference') else '',
                                  refuted_by_difference=check.get('refuted_by_difference', False))
    return checks, origins, skipped
