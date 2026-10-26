# SPDX-License-Identifier: Apache-2.0
"""The bundles shipped with this version. A bundle is listed only once it has an offline end-to-end test."""
from __future__ import annotations

from g6.bundles.agent_change import BUNDLE as AGENT_CHANGE_CHECK
from g6.bundles.bookkeeping_check import BUNDLE as BOOKKEEPING_CHECK
from g6.bundles.check_claims import BUNDLE as CHECK_CLAIMS
from g6.bundles.code_review import BUNDLE as CODE_REVIEW
from g6.bundles.compliance_gap import BUNDLE as COMPLIANCE_GAP
from g6.bundles.contract_review import BUNDLE as CONTRACT_REVIEW
from g6.bundles.grounded_answer import BUNDLE as GROUNDED_ANSWER
from g6.bundles.ml_check import BUNDLE as ML_CHECK
from g6.bundles.premortem import BUNDLE as PREMORTEM
from g6.bundles.runbook import BUNDLE as RUNBOOK
from g6.bundles.safe_actions import BUNDLE as SAFE_ACTIONS

BUNDLES = (AGENT_CHANGE_CHECK, SAFE_ACTIONS, CHECK_CLAIMS, GROUNDED_ANSWER, PREMORTEM, RUNBOOK, CODE_REVIEW, ML_CHECK,
           BOOKKEEPING_CHECK, COMPLIANCE_GAP, CONTRACT_REVIEW)
