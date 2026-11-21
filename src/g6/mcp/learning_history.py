# SPDX-License-Identifier: Apache-2.0
"""Client-only consent for learning_history.forget, reached through the existing generic tool."""
import json

from pydantic import ValidationError
from returns.result import Success

from g6.core import component_process
from g6.core.door import blocked_reply, door_reply, is_allowed
from g6.harness import outcome_history
from g6.learning_history.block import HistoryInput
from g6.mcp import consent
from g6.workforce import repo_tests


async def forget_reply(ctx, params_json: str, *, allow: frozenset[str]) -> dict:
    try:
        params = json.loads(params_json)
        if not isinstance(params, dict) or 'op' in params:
            raise ValueError('parameters must be an object without an op override')
        data = HistoryInput.model_validate({**params, 'op': 'forget'})
    except (ValueError, TypeError, ValidationError, RecursionError, MemoryError):
        return blocked_reply('[G6_E_INPUT_VALIDATION] forget takes only {"repo": <repository>}')
    if not is_allowed('learning_history', 'forget', allow):
        return blocked_reply('[G6_E_SENSITIVE_COMPONENT_DENIED] learning_history.forget requires the person to '
                             'allow this sensitive op. Tell the person and stop.')
    try:
        root = repo_tests.checked_repo(data.repo)
        history = outcome_history.read(root)
        if not history.repository:
            return blocked_reply('[G6_E_INPUT_VALIDATION] repository has no committed identity; nothing removed')
        expected = outcome_history.fingerprint(history.repository)
        asked = consent.question('Erase G6 learning history for this repository?',
            {'repository_id': history.repository, 'History digest': expected,
             'Retained checks': len(history.retained), 'Admitted trials': len(history.rows),
             'Rejected rows': history.rejected},
            ['This erases the repository history, including retained check source. It cannot be undone. '
             'Separate test receipts remain. Future G6 executions may record new results.'],
            close='Type your name to erase it, or decline. Identity is recorded as supplied, not authenticated.')
        answer = await consent.request_consent(ctx, asked)
        if answer.status != 'accepted':
            return {'ok': False, **consent.refusal(answer, nothing='No learning history was removed.')}
        outcome_history.forget(root, history.repository, expected=expected)
        return door_reply('learning_history', 'forget', Success({
            'repository_id': history.repository, 'forgotten': True,
            'scope': 'one repository outcome history; separate execution receipts remain'}), caller=params)
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise   # both are OSErrors: an operator stop or unconfirmed cleanup must reach the enclosing door
    except OSError as exc:
        from g6.core.semantic import trusted_stop
        if trusted_stop(exc):
            raise
        return blocked_reply('[G6_E_INPUT_VALIDATION] could not finish removing history; use learning_history.list '
                             'to inspect what remains. Do not retry around a permission refusal.')
    except ValueError:
        return blocked_reply('[G6_E_INPUT_VALIDATION] history unavailable or changed; no deletion authorized')
