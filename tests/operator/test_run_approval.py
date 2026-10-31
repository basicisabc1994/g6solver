# SPDX-License-Identifier: Apache-2.0
"""A run approval (P1, Review Focus 2): an internal queue item a person other than the requester decides, bound to
exactly one mission or ticket; the approver's name is what the run records, and it is never sent anywhere."""
from __future__ import annotations

import pytest

from g6.operator.queue import APPROVAL_MISMATCH, APPROVAL_REQUIRED, RUN_APPROVAL, QueueError


def test_a_run_approval_is_a_persons_decision_bound_to_one_binding(queue, venture):
    item = queue.request_run(venture, binding="b1", summary="run tests for mission m", requested_by="g6:algorithm_design")
    assert (item["status"], item["action_type"], item["meta"]["binding"]) == ("proposed", RUN_APPROVAL, "b1")
    with pytest.raises(QueueError, match=r"^\[G6_E_OPERATOR_APPROVAL_REQUIRED\]"):
        queue.run_approver(venture, item["action_id"], binding="b1")
    with pytest.raises(QueueError):
        queue.approve(venture, item["action_id"], reviewer="g6:algorithm_design")   # the requester
    with pytest.raises(QueueError):
        queue.approve(venture, item["action_id"], reviewer="auto:loop")
    queue.approve(venture, item["action_id"], reviewer="ana")
    assert queue.run_approver(venture, item["action_id"], binding="b1") == "ana"
    with pytest.raises(QueueError, match=r"^\[G6_E_OPERATOR_APPROVAL_MISMATCH\]"):
        queue.run_approver(venture, item["action_id"], binding="b2")
    with pytest.raises(QueueError):
        queue.mark_sent(venture, item["action_id"], reviewer="ana", channel="x")   # internal, never outward


def test_a_rejected_unknown_or_other_kind_of_item_approves_no_run(queue, venture):
    rejected = queue.request_run(venture, binding="b1", summary="run", requested_by="g6:algorithm_design")
    queue.reject(venture, rejected["action_id"], reviewer="ana", reason="not this one")
    with pytest.raises(QueueError, match=r"^\[G6_E_OPERATOR_APPROVAL_REQUIRED\]"):
        queue.run_approver(venture, rejected["action_id"], binding="b1")
    with pytest.raises(QueueError, match=r"^\[G6_E_OPERATOR_APPROVAL_REQUIRED\]"):
        queue.run_approver(venture, "no-such-item", binding="b1")
    post = queue.enqueue(venture, task_id="t1", action={"type": "post", "body": "A post. More text."},
                         drafted_by="agent-1", review={"state": "qualified-draft"})
    queue.approve(venture, post["action_id"], reviewer="ana")
    with pytest.raises(QueueError, match=r"^\[G6_E_OPERATOR_APPROVAL_REQUIRED\]"):
        queue.run_approver(venture, post["action_id"], binding="b1")


def test_a_run_approval_needs_a_binding_and_a_requester(queue, venture):
    with pytest.raises(QueueError):
        queue.request_run(venture, binding="", summary="run", requested_by="g6:algorithm_design")
    with pytest.raises(QueueError):
        queue.request_run(venture, binding="b1", summary="run", requested_by=" ")
    assert (APPROVAL_REQUIRED, APPROVAL_MISMATCH) == ("G6_E_OPERATOR_APPROVAL_REQUIRED",
                                                      "G6_E_OPERATOR_APPROVAL_MISMATCH")
