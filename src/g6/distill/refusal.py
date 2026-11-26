# SPDX-License-Identifier: Apache-2.0
"""Shared direct and job rendering of distillation write refusals."""


def refusal(exc):
    from g6.core.door import blocked_reply
    from g6.distill.ledger import CommittedStepsRefusal, PartialAuthorityWrite
    if isinstance(exc, (PartialAuthorityWrite, CommittedStepsRefusal)):
        earlier = exc.earlier_publication_committed and not exc.committed_steps
        steps = '; '.join(f'{kind} {ident}' for kind, ident in exc.committed_steps)
        recovered = isinstance(exc, CommittedStepsRefusal) or getattr(exc, 'recovery_complete', False)
        detail = ('request interrupted; recovery completed' if recovered else
                  'intent cleanup pending' if exc.committed else 'undo recovery incomplete')
        if getattr(exc, 'reservation_close_pending', False):
            detail += '; reservation close recorded durably for recovery'
        elif getattr(exc, 'reservation_closed', False):
            detail += '; reservation closed'
        state = ('This request recorded ' + steps + '; ' + detail if steps else
                 'Earlier publication committed; its cleanup is incomplete; this request made no change' if earlier
                 else 'Publication committed; intent cleanup pending' if exc.publication_committed
                 else 'Recovery incomplete')
        return {**blocked_reply(f'[{exc.code}] {state}; durable state: ' + '; '.join(exc.durable_state)),
                'durable_state': exc.durable_state, 'publication_committed': exc.publication_committed,
                **({'earlier_publication_committed': True} if earlier else {}),
                **({'committed_steps': [{'kind': kind, 'id': ident} for kind, ident in exc.committed_steps]}
                   if exc.committed_steps else {}),
                'rollback_or_containment': (state + ('.' if exc.publication_committed else
                    '; no activation committed; the method is unchanged.' if exc.committed or recovered else
                    '; no activation committed; inspect the named durable state before retrying.')
                    if steps else state + '.' if earlier else
                    'The evaluation reservation committed; no admission decision was made; the method is unchanged.'
                    if exc.reservation else
                    'The publication committed; its intent still needs cleanup.' if exc.publication_committed else
                    'Undo is incomplete; inspect the named durable state before retrying.')}
    code = getattr(exc, 'code', '')
    if isinstance(code, str) and code.startswith('G6_E_'):
        return blocked_reply(f'[{code}] Backend refused the operation; read the current durable state.')
    return blocked_reply("[G6_E_DISTILL_INPUT] Invalid input, missing entry or unreadable distillation record. "
                         "The operation could not complete; read the current durable state.")

