# SPDX-License-Identifier: Apache-2.0
"""Recurrence receipts with no evaluator or held-out custody dependencies."""
from g6.core.ledger import ChainLedger, LedgerError, digest
from g6.core.storage import state_path

from .ledger import append_retained, failure_writer
from .records import LibraryEntry


@failure_writer()
def record_hold_lift(entry: LibraryEntry) -> None:
    """Journal a written hold lift inside its enclosing publication transaction."""
    try:
        journal = ChainLedger(state_path('distill', 'acceptance_gate.jsonl'))
        count = len(journal.entries())
    except (RecursionError, MemoryError) as exc:   # a home ledger line nested past the decoder: G6's refusal
        raise LedgerError("a ledger line exceeds the JSON decoder's limits") from exc
    # Preserve the existing decision-row format without importing its evaluator.
    body = {'verdict': 'lift_hold', 'reasons': (entry.reason,),
            'candidate_id': entry.candidate_id, 'report_sha256': '', 'entry_id': entry.entry_id,
            'p_value': 1.0, 'minimum_detectable_gain': 1.0, 'p_value_upper_bound': None,
            'minimum_detectable_precision_difference': None,
            'reservation': '', 'gate_version': 'distill-acceptance-v1'}
    ident = digest(['decision', count, body])
    append_retained(journal, 'decision', ident, body)
