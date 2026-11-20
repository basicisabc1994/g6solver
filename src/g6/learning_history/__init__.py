# SPDX-License-Identifier: Apache-2.0
"""Inspect and erase G6's own local repository check history through g6_invoke.

List returns counts and the store path. Forget is sensitive and asks the person in the MCP
client dialog; Python, CLI, composition and caller-supplied approval never authorize it.
This is same-user local state, not protection against code running as that user.
"""
from g6.core.outside import OutsideProgram
from g6.learning_history.block import LearningHistoryBlock

__g6_component__ = True
__g6_block__ = 'LearningHistoryBlock'
__g6_requires__: tuple[str, ...] = ('harness', 'workforce')
__g6_mitigates__: tuple[str, ...] = ('opaque_learning',)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (OutsideProgram(
    name='git', purpose='checks the repository root and reads its committed identity for local history',
    install='Install git from git-scm.com and put it on PATH.', licence='GPL-2.0', command='git'),)
__g6_sensitive_ops__ = frozenset({'forget'})
__g6_refusals__ = {'G6_E_LEARNING_HISTORY_DIALOG_REQUIRED': 'person'}

__all__ = ['LearningHistoryBlock']
