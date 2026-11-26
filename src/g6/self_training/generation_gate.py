# SPDX-License-Identifier: Apache-2.0
"""Host-signed generation chain; keys and git objects stay behind injected ports.

The host signer implements sign(canonical_body) and verify(canonical_body, signature).
For production it must use the owner's protected allowed-signers root and SSH signing
service, outside mission custody. No key discovery or in-process production key.
"""
from pathlib import Path

from g6.core.ledger import ChainLedger, LedgerCorrupt, canonical, digest


class Generations:
    def __init__(self, path, host, *, expect_head=None):
        self.path = Path(path) if path is not None else None
        self.host = host
        self.chain = ChainLedger(self.path, expect_head=expect_head, validate=self._validate)
        self.chain.expect_head = None

    def _validate(self, kind, ident, record, seen):
        try:
            if kind != 'generation' or set(record) != {'body', 'signature'} or ident != digest(record):
                raise ValueError('invalid generation envelope')
            body = record['body']
            if body['schema'] != 'g6.generation_certificate/1' or not self.host.verify(canonical(body), record['signature']):
                raise ValueError('generation signature does not verify')
            previous = list(seen.values())
            if body['generation'] != len(previous) + 1:
                raise ValueError('generation sequence differs')
            parent = digest(previous[-1]) if previous else ''
            if body['parent_sha256'] != parent:
                raise ValueError('generation parent differs')
            if previous and body['parent_commit'] != previous[-1]['body']['merge_commit']:
                raise ValueError('generation parent commit differs')
            if body['kind'] not in ('improvement', 'no_behaviour_change', 'rollback'):
                raise ValueError('generation kind differs')
            if not body['scope'] or not body['does_not_show']:
                raise ValueError('certificate must scope its claim')
        except (ValueError, KeyError, TypeError) as exc:
            return [str(exc)]
        return []

    def read(self):
        self.chain.reload()
        return [row['body'] for row in self.chain.entries()]

    def prepare(self, *, change, evidence, kind=None, trigger=None):
        history = self.read()
        if history and change.parent_commit != history[-1]['body']['merge_commit']:
            raise LedgerCorrupt('candidate parent is not the certified generation tip')
        ev = evidence.evaluation
        body = dict(schema='g6.generation_certificate/1', kind=kind or change.claim,
            generation=len(history) + 1, parent_sha256=digest(history[-1]) if history else '',
            parent_commit=change.parent_commit, merge_commit=change.merge_commit, tree_sha256=change.tree_sha256,
            change=change.model_dump(mode='json') | {'diff_sha256': change.diff_sha256},
            protected_check={'manifest_sha256': evidence.manifest_sha256, 'result': 'untouched'},
            ci=[row.model_dump(mode='json') for row in evidence.ci], review=evidence.review.model_dump(mode='json'),
            evaluation=ev.model_dump(mode='json'), evidence_sha256=digest(evidence.model_dump(mode='json')),
            predictions=list(evidence.prediction_refs), trigger=trigger,
            scope=f'measured on {ev.split} split {ev.split_sha256} ({ev.n} cases) at commit '
                  f'{change.merge_commit}; no claim beyond these cases',
            does_not_show=['other families', 'other repositories', 'long-run drift', 'consciousness or awareness'])
        if kind == 'rollback':
            body['scope'] = (f'rollback of generation {trigger["generation"]} after measured '
                f'{trigger["metric"]} regression, receipt {trigger["receipt"]}, at commit {change.merge_commit}; '
                'no claim beyond these cases; no new held-out improvement claimed')
        record = {'body': body, 'signature': self.host.sign(canonical(body))}
        if not self.host.verify(canonical(body), record['signature']):
            raise LedgerCorrupt('host signer returned an unverifiable generation')
        return record

    def append(self, record):
        self.read()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        return self.chain.append('generation', digest(record), record)
