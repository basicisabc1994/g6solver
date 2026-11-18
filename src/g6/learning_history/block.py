# SPDX-License-Identifier: Apache-2.0
"""Closed, read-only registry surface; deletion is mediated by MCP's existing dialog transport."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.result import Result
from g6.harness import outcome_history
from g6.workforce import repo_tests


class HistoryInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    op: Literal['ops', 'help', 'list', 'forget'] = 'ops'
    repo: str = ''

    @model_validator(mode='after')
    def explicit_repository(self):
        if self.op in {'list', 'forget'} and not self.repo.strip():
            raise ValueError('list and forget require an explicit repository')
        return self


@block_contract(purpose="inspect G6's local repository check history", mitigates_failure_modes=('opaque_learning',),
                verification_method='receipt-backed local counts; no correctness or promotion claim')
class LearningHistoryBlock(AIBlock):
    def __init__(self):
        super().__init__(name='learning_history')

    def infer(self, data: HistoryInput) -> Result:
        if data.op in {'ops', 'help'}:
            return Result.ok({'ops': ['list', 'forget'], 'help':
                'list(repo): retained checks, admitted trials, rejected rows and local store path. '
                'forget(repo): erase one repository history after agreement in the client dialog; sensitive. '
                'Recording is on by default and retains only G6 check results and already retained check source. '
                'Same-user local state; execution receipts remain separate.'})
        if data.op == 'forget':
            return Result.fail('[G6_E_LEARNING_HISTORY_DIALOG_REQUIRED] Use g6_invoke learning_history.forget; '
                               'only agreement in the client dialog authorizes removal.')
        try:
            root = repo_tests.checked_repo(data.repo)
            history = outcome_history.read(root)
            checks = [{key: row.get('check', {}).get(key, '') for key in ('file', 'test', 'sha256')}
                      | {key: row.get(key, '') for key in ('node', 'modules', 'finding')}
                      for row in history.retained]
            trials = [{key: row.get(key, '') for key in
                       ('classification', 'method', 'engine', 'op', 'origin', 'receipt', 'artifact_digest')}
                      for row in history.rows]
            return Result.ok({**history.learned(), 'repository_id': history.repository,
                              'checks': checks, 'trials': trials,
                              'store_path': str(outcome_history.store_path(history.repository))
                              if history.repository else '', 'recording': 'on by default',
                              'scope': 'G6 own local check results; same-user state'})
        except (OSError, ValueError) as exc:
            return Result.fail(f'[G6_E_INPUT_VALIDATION] history unavailable ({type(exc).__name__})')
